"""Local web server: phone upload page (paired by QR), PC dashboard, OCR worker."""
from __future__ import annotations

import asyncio
import queue
import secrets
import socket
import threading
import time
import uuid
from datetime import date
from io import BytesIO
from importlib import resources
from pathlib import Path

import segno
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from PIL import Image, ImageOps

from . import __version__
from .export import to_xlsx
from .receipt import UNVERIFIED_NOTE, Receipt, fill_bases, merge, parse, validate
from .store import Store
from .studio import DEFAULT_URL, VARIANTS, StudioError, StudioOCR, StudioUnavailable
from .studio_setup import StudioSetup

MAX_SIDE = 2000  # px; bigger photos only slow OCR down
LOCAL_HOSTS = {"127.0.0.1", "::1", "localhost"}


def lan_ip() -> str:
    """The address phones on the same Wi-Fi can reach. No packet is sent."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def same_receipt(a: dict, b: dict) -> bool:
    """Same fiş no and total, and no field that both sides read disagrees.

    People photograph a faded receipt twice to get a better read, so one copy may lack
    the date or VKN; those gaps must not hide the duplicate.
    """
    if not (a.get("receipt_no") and a.get("total")):
        return False
    if (a.get("receipt_no"), a.get("total")) != (b.get("receipt_no"), b.get("total")):
        return False
    return all(not (a.get(k) and b.get(k)) or a.get(k) == b.get(k) for k in ("vkn", "date"))


def duplicates(store: Store, rid: int, r: Receipt) -> list[str]:
    """The same receipt photographed twice must not be booked twice."""
    me = r.to_dict()
    same = [
        o["id"] for o in store.all()
        if o["id"] != rid and o["state"] == "done" and same_receipt(me, o["data"])
    ]
    return [f"Bu fiş daha önce eklenmiş (#{', #'.join(map(str, same))}). Çift kayıt olmasın diye kontrol edin."] if same else []


class Worker(threading.Thread):
    """Runs every queued photo through AugeLab Studio, one at a time."""

    def __init__(self, store: Store, studio_url: str) -> None:
        super().__init__(daemon=True, name="fisoku-ocr")
        self.store = store
        self.studio = StudioOCR(studio_url, state_file=store.dir / "studio_scene.json")
        self.jobs: queue.Queue[int] = queue.Queue()
        self.status = {"connected": False, "version": None, "message": "Studio'ya bağlanılıyor…", "busy": False}

    def run(self) -> None:
        asyncio.run(self._main())

    async def _main(self) -> None:
        for rid in self.store.pending_ids():
            self.jobs.put(rid)
        last_check = 0.0
        while True:
            # Photos wait in the queue while Studio is closed and are read once it is back.
            idle_every = 10 if self.status["connected"] else 4
            if time.monotonic() - last_check > idle_every or not self.status["connected"]:
                await self._check()
                last_check = time.monotonic()
                if not self.status["connected"]:
                    await asyncio.sleep(4)
                    continue
            try:
                rid = self.jobs.get(timeout=1)
            except queue.Empty:
                continue
            await self._process(rid)
            last_check = time.monotonic()

    async def _check(self) -> None:
        try:
            st = await self.studio.status()
            self.status.update(connected=True, version=st["version"])
            if not self.status["busy"]:
                self.status["message"] = (
                    "Hazır." if self.studio.warmed_up
                    else "Hazır. İlk fişte OCR modeli Studio'ya yüklenir; biraz uzun sürebilir."
                )
        except StudioError as exc:
            self.status.update(connected=False, message=str(exc))

    async def _process(self, rid: int) -> None:
        rec = self.store.get(rid)
        if not rec:
            return
        self.store.update(rid, state="processing")
        self.status.update(busy=True, message=(
            f"Fiş #{rid} okunuyor…" if self.studio.warmed_up
            else f"Fiş #{rid} okunuyor. İlk okumada OCR modeli Studio'ya yükleniyor…"
        ))
        try:
            photo = str(self.store.photos / rec["photo"])
            res = await self.studio.read_image(photo)
            r = parse(res.text)
            texts = [f"[{VARIANTS[0]}]\n{res.text}"]
            notes: list[str] = []
            if validate(r):
                # Gaps after the plain read: re-read the same photo through the other
                # filters in Studio and keep only what receipt.merge can trust.
                others = []
                for v in range(1, len(VARIANTS)):
                    self.status["message"] = f"Fiş #{rid}: eksik alanlar için {VARIANTS[v]} filtresiyle tekrar okunuyor…"
                    alt = await self.studio.read_image(photo, variant=v)
                    others.append(parse(alt.text))
                    texts.append(f"[{VARIANTS[v]}]\n{alt.text}")
                r, filled, unverified = merge(r, others)
                notes = [UNVERIFIED_NOTE[k] for k in unverified]
            problems = validate(r) + notes + duplicates(self.store, rid, r)
            self.store.update(
                rid, state="done", status="review" if problems else "ok",
                data=r.to_dict(), problems=problems, ocr_text="\n\n".join(texts), error="",
            )
            self.status.update(connected=True, message="Hazır.")
        except StudioUnavailable as exc:
            self.store.update(rid, state="queued", error="")
            self.jobs.put(rid)
            self.status.update(connected=False, message=str(exc))
        except StudioError as exc:
            self.store.update(rid, state="error", error=str(exc))
            self.status.update(message=str(exc))
        except Exception as exc:  # parsing bugs must not stop the queue
            self.store.update(rid, state="error", error=f"Beklenmeyen hata: {exc}")
        finally:
            self.status["busy"] = False


def _web(name: str) -> str:
    return resources.files("fisoku").joinpath("web", name).read_text(encoding="utf-8")


def create_app(store: Store, worker: Worker, port: int, setup: StudioSetup | None = None) -> FastAPI:
    setup = setup or StudioSetup()
    app = FastAPI(title="Fisoku", version=__version__, docs_url=None, redoc_url=None)
    # Kept across restarts so a phone that scanned once can bookmark the page.
    key_file = store.dir / "pairing.key"
    if not key_file.exists():
        key_file.write_text(secrets.token_urlsafe(12), encoding="ascii")
    token = key_file.read_text(encoding="ascii").strip()

    def is_local(req: Request) -> bool:
        return (req.client.host if req.client else "") in LOCAL_HOSTS

    def require(req: Request) -> None:
        sent = req.headers.get("x-fisoku-token") or req.query_params.get("t")
        if not is_local(req) and not (sent and secrets.compare_digest(sent, token)):
            raise HTTPException(403, "Eşleşme gerekli: bilgisayardaki QR kodu okutun.")

    def phone_url() -> str:
        return f"http://{lan_ip()}:{port}/m/{token}"

    @app.get("/", response_class=HTMLResponse)
    def desk(req: Request):
        if not is_local(req):
            raise HTTPException(403, "Bu sayfa sadece bilgisayardan açılır.")
        return _web("desk.html")

    @app.get("/m/{t}", response_class=HTMLResponse)
    def phone(t: str):
        if not secrets.compare_digest(t, token):
            return HTMLResponse(_web("expired.html"), status_code=403)
        return _web("phone.html").replace("__TOKEN__", token)

    @app.get("/api/pairing")
    def pairing(req: Request):
        if not is_local(req):
            raise HTTPException(403)
        url = phone_url()
        qr = segno.make(url, error="m").svg_inline(scale=6, dark="#111", light="#fff")
        return {"url": url, "qr_svg": qr, "version": __version__}

    @app.get("/api/status")
    def status(req: Request):
        require(req)
        return {**worker.status, "queue": worker.jobs.qsize()}

    def require_local(req: Request) -> None:
        if not is_local(req):
            raise HTTPException(403, "Kurulum sadece bilgisayardan yapılır.")

    @app.get("/api/setup")
    def setup_state(req: Request):
        require_local(req)
        return {**setup.state(), "connected": worker.status["connected"], "message": worker.status["message"]}

    @app.post("/api/setup/install")
    def setup_install(req: Request):
        require_local(req)
        return {"started": setup.start_install()}

    @app.post("/api/setup/reopen")
    def setup_reopen(req: Request):
        require_local(req)
        try:
            return {"ok": setup.open_installer()}
        except OSError as exc:
            raise HTTPException(500, f"Kurulum açılamadı: {exc}")

    @app.post("/api/setup/launch")
    def setup_launch(req: Request):
        require_local(req)
        if not setup.launch():
            raise HTTPException(404, "AugeLab Studio bu bilgisayarda bulunamadı.")
        return {"ok": True}

    @app.post("/api/upload")
    async def upload(req: Request, files: list[UploadFile] = File(...)):
        require(req)
        ids = []
        for f in files:
            raw = await f.read()
            try:
                src = Image.open(BytesIO(raw))
                upright = src.format == "JPEG" and src.getexif().get(0x0112, 1) == 1
                small = max(src.size) <= MAX_SIDE
                name = f"{date.today():%Y%m%d}_{uuid.uuid4().hex[:10]}.jpg"
                if upright and small:
                    # Already a right-way-up JPEG of a sane size (the phone page sends
                    # these): keep the bytes, a second JPEG pass costs OCR accuracy.
                    (store.photos / name).write_bytes(raw)
                else:
                    img = ImageOps.exif_transpose(src).convert("RGB")
                    img.thumbnail((MAX_SIDE, MAX_SIDE))
                    img.save(store.photos / name, quality=95)
            except Exception:
                raise HTTPException(400, f"{f.filename}: görüntü okunamadı.")
            rid = store.add(name)
            worker.jobs.put(rid)
            ids.append(rid)
        return {"ids": ids}

    @app.get("/api/receipts")
    def receipts(req: Request):
        require(req)
        return store.all()

    @app.get("/api/receipts/{rid}/photo")
    def photo(rid: int, req: Request):
        require(req)
        rec = store.get(rid) or {}
        path = store.photos / rec.get("photo", "-")
        if not path.is_file():
            raise HTTPException(404)
        return FileResponse(path, media_type="image/jpeg")

    @app.put("/api/receipts/{rid}")
    async def edit(rid: int, req: Request):
        require(req)
        rec = store.get(rid)
        if not rec:
            raise HTTPException(404)
        body = await req.json()
        r = Receipt.from_dict({**rec["data"], **body})
        # The person confirmed the header values; unreadable item lines no longer block it.
        r.lines = []
        fill_bases(r)
        problems = validate(r) + duplicates(store, rid, r)
        store.update(rid, data=r.to_dict(), problems=problems, status="review" if problems else "ok", edited=1)
        return store.get(rid)

    @app.post("/api/receipts/{rid}/retry")
    def retry(rid: int, req: Request):
        require(req)
        if not store.get(rid):
            raise HTTPException(404)
        store.update(rid, state="queued", error="")
        worker.jobs.put(rid)
        return {"ok": True}

    @app.delete("/api/receipts/{rid}")
    def remove(rid: int, req: Request):
        require(req)
        store.delete(rid)
        return {"ok": True}

    @app.get("/api/export.xlsx")
    def export(req: Request):
        require(req)
        data = to_xlsx(store.all())
        return Response(
            data,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="fisler_{date.today():%Y-%m-%d}.xlsx"'},
        )

    @app.exception_handler(HTTPException)
    async def http_error(_req: Request, exc: HTTPException):
        return JSONResponse({"error": exc.detail}, status_code=exc.status_code)

    return app


def build(data_dir: Path, port: int, studio_url: str = DEFAULT_URL) -> FastAPI:
    store = Store(data_dir)
    worker = Worker(store, studio_url)
    worker.start()
    return create_app(store, worker, port)
