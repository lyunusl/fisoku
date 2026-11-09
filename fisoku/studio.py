"""AugeLab Studio bridge over Studio's local MCP endpoint.

Fisoku does not ship any Studio code. It talks to a Studio that the user installed and
started, using the public MCP tools Studio exposes (node.custom.create, graph.program,
scene.run.start, ...). On first use it registers its own blocks (see studio_block.py)
and creates a small scene in a new Studio document:

    Fisoku Image ------> Load Image From Path -+-------------------------+
                                               +-> Contrast Optimization -+-> Multi Port   -> Fisoku OCR
                                               +-> Auto Contrast ---------+   Switch           (RapidOCR)
                                               +-> Fisoku Light Flatten --+     ^
    Fisoku Variant (0..3) ------------------------------------------------------+

For every photo it sets the path and the variant and runs that scene once; only the
selected branch reaches OCR. The scene is remembered, so Studio keeps one Fisoku
document while it stays open.
"""
from __future__ import annotations

import asyncio
import json
import re
import secrets
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from . import studio_block
from .engine import ensure_engine
from .layout import lines_from_words

DEFAULT_URL = "http://127.0.0.1:8765/mcp"

# Block op codes in Studio's public block registry.
OP_STRING_INPUT = "119"
OP_NUMBER_INPUT = "101"
OP_LOAD_IMAGE_FROM_PATH = "213"
OP_AUTO_CONTRAST = "379"
OP_CONTRAST_OPTIMIZATION = "380"  # CLAHE
OP_MULTI_PORT_SWITCH = "1131"

PATH_TITLE = "Fisoku Image"  # renamed from "Fisoku Image Path": older scenes stored an engine path
VARIANT_TITLE = "Fisoku Variant"
OCR_TITLE = "Fisoku OCR"

# Image variants the switch can send to OCR. 0 is used for every photo; the others are
# re-reads for receipts whose first read left gaps (receipt.merge decides what to keep).
VARIANTS = ("ham", "CLAHE", "Auto Contrast", "Light Flatten")


def _scene_source() -> str:
    return f"""
path = node('{OP_STRING_INPUT}', title='{PATH_TITLE}')
variant = node('{OP_NUMBER_INPUT}', title='{VARIANT_TITLE}')
load = node('{OP_LOAD_IMAGE_FROM_PATH}')
clahe = node('{OP_CONTRAST_OPTIMIZATION}')
auto = node('{OP_AUTO_CONTRAST}')
flat = node('{studio_block.FLATTEN_OP_CODE}', title='{studio_block.FLATTEN_TITLE}')
switch = node('{OP_MULTI_PORT_SWITCH}')
ocr = node('{studio_block.OP_CODE}', title='{OCR_TITLE}')
switch.params['cmbbox'] = '{len(VARIANTS)}'
clahe.params['s0'] = 8
clahe.params['s1'] = 3
flat.params['sigma'] = 61
connect(path.out[0], load.in_[0])
connect(load.out[0], clahe.in_[0])
connect(load.out[0], auto.in_[0])
connect(load.out[0], flat.in_[0])
connect(variant.out[0], switch.in_[0])
connect(load.out[0], switch.in_[1])
connect(clahe.out[0], switch.in_[2])
connect(auto.out[0], switch.in_[3])
connect(flat.out[0], switch.in_[4])
connect(switch.out[0], ocr.in_[0])
"""


FIRST_RUN_TIMEOUT_S = 10 * 60  # first run loads the ONNX models
RUN_TIMEOUT_S = 5 * 60


class StudioError(RuntimeError):
    """Studio refused a request or the OCR run failed."""


class StudioUnavailable(StudioError):
    """Studio is not running or its MCP server is off: worth waiting for, not a failure."""


@dataclass
class OcrResult:
    text: str  # receipt lines rebuilt from word boxes, one per line
    duration_ms: int | None = None
    words: list | None = None
    boxes: list | None = None


def _key() -> str:
    return "fisoku-" + secrets.token_hex(12)


def _quote(value: str) -> str:
    return "'" + value.replace("\\", "/").replace("'", "\\'") + "'"


class StudioOCR:
    """Async client. Use one instance from one event loop (the worker thread)."""

    def __init__(self, url: str = DEFAULT_URL, state_file: Path | None = None) -> None:
        self.url = url
        self.state_file = state_file  # remembers Fisoku's Studio document across restarts
        self.scene_handle: str | None = None
        self.scene_raw: str | None = None
        self.revision: str | None = None
        self.path_node: str | None = None
        self.variant_node: str | None = None
        self.ocr_raw: str | None = None
        self.block_ready = False
        self.warmed_up = False

    # ---------------------------------------------------------------- MCP plumbing
    async def _call(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        # Studio occasionally drops a response under load. Every mutating call carries an
        # idempotencyKey, so sending the same arguments again replays, never repeats.
        last: Exception | None = None
        for attempt in range(5):  # waits 1+2+3+4 s: rides out short stalls while Studio is busy
            try:
                async with streamable_http_client(self.url) as (read, write, *_):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        result = await session.call_tool(tool, args)
                break
            except Exception as exc:  # connection refused, timeouts, protocol errors
                last = exc
                if attempt < 4:
                    await asyncio.sleep(1 + attempt)
        else:
            raise StudioUnavailable(
                f"AugeLab Studio'ya bağlanılamadı ({self.url}). Studio açık mı, MCP etkin mi?"
            ) from last
        payload = result.structured_content or {}
        if result.is_error or payload.get("error"):
            err = payload.get("error") or {}
            raise StudioError(f"{tool}: {err.get('code', 'ERROR')} {err.get('message', '')}".strip())
        data = dict(payload.get("data") or {})
        # Long-running tools answer "accepted" with the operation next to data, not in it.
        if payload.get("operation") and "operation" not in data:
            data["operation"] = payload["operation"]
        return data

    async def _wait(self, data: dict[str, Any], limit_s: float) -> dict[str, Any]:
        """Follow an accepted operation to its end with short server-side waits."""
        op = data.get("operation") or data
        start = time.monotonic()
        while op.get("state") in ("queued", "running") and time.monotonic() - start < limit_s:
            more = await self._call("operation.get", {"operationId": op["id"], "waitTimeoutMs": 15000, "updateLimit": 1})
            op = more.get("operation") or more
        return op

    async def status(self) -> dict[str, Any]:
        data = await self._call("studio.status.get", {})
        return {"version": data.get("studioVersion"), "mcp": (data.get("mcp") or {}).get("state")}

    # ---------------------------------------------------------------- block and scene
    async def ensure_block(self) -> None:
        """Register (or refresh) the Fisoku OCR custom block once per Fisoku run."""
        if self.block_ready:
            return
        data = await self._call(
            "node.custom.create",
            {"specifications": studio_block.SPECS, "replaceExisting": True, "idempotencyKey": _key()},
        )
        op = await self._wait(data, 120)
        if op.get("state") != "succeeded":
            err = op.get("error") or {}
            raise StudioError(f"Fisoku blokları Studio'ya eklenemedi: {err.get('message') or op.get('state')}")
        # The engine goes next to Studio's custom block folder, where the block looks for it.
        files = [n.get("file_path") for n in (op.get("result") or {}).get("nodes", []) if n.get("file_path")]
        if not files:
            raise StudioError("Studio, Fisoku bloklarının nereye kaydedildiğini bildirmedi.")
        try:
            ensure_engine(Path(files[0]).parent.parent / studio_block.ENGINE_DIR_NAME)
        except (OSError, RuntimeError) as exc:
            raise StudioError(f"OCR motoru Studio'nun klasörüne kopyalanamadı: {exc}") from exc
        self.block_ready = True

    async def ensure_scene(self) -> None:
        """Reuse Fisoku's document while Studio stays open; create it otherwise."""
        if self.scene_handle:
            return
        await self.ensure_block()
        saved = self._load_state()
        if saved and await self._adopt(saved):
            return
        data = await self._call(
            "graph.program",
            {"target": "create_new", "expectedRevision": "none", "idempotencyKey": _key(),
             "source": _scene_source()},
        )
        if not await self._adopt(data["scene"]):
            raise StudioError("Fisoku sahnesi Studio'da kurulamadı.")
        self._save_state(data["scene"])

    async def _adopt(self, scene: str) -> bool:
        """Map Fisoku's blocks in `scene` by title. False if it is gone or not ours."""
        try:
            graph = await self._call("graph.read", {"sceneId": scene, "view": "graph"})
        except StudioError:
            return False
        # Observations need raw node ids; graph.read lists handles in the same order as ids.
        rows = re.findall(r'^ (n\w+):"[^"]*" @(\S+)(?: title="([^"]*)")?', graph.get("graph", ""), flags=re.M)
        targets = graph.get("navigationTargets") or [{}]
        ids = dict(zip((h for h, _, _ in rows), targets[0].get("nodeIds", [])))
        by_title = {title: h for h, _, title in rows if title}
        if any(t not in by_title for t in (PATH_TITLE, VARIANT_TITLE, OCR_TITLE)) or len(ids) != len(rows):
            return False
        self.scene_handle = scene
        self.scene_raw = targets[0]["sceneId"]
        self.path_node = by_title[PATH_TITLE]
        self.variant_node = by_title[VARIANT_TITLE]
        self.ocr_raw = ids[by_title[OCR_TITLE]]
        self.revision = graph["revision"]
        return True

    def _load_state(self) -> str | None:
        try:
            return json.loads(self.state_file.read_text(encoding="utf-8")).get("scene") if self.state_file else None
        except (OSError, ValueError):
            return None

    def _save_state(self, scene: str) -> None:
        if self.state_file:
            self.state_file.write_text(json.dumps({"scene": scene}), encoding="utf-8")

    async def _set_path(self, image_path: str, variant: int = 0) -> None:
        src = (f"ref('{self.path_node}').params['threshold'] = {_quote(image_path)}\n"
               f"ref('{self.variant_node}').params['threshold'] = '{int(variant)}'")
        try:
            data = await self._call(
                "graph.program",
                {"sceneId": self.scene_handle, "expectedRevision": self.revision, "idempotencyKey": _key(), "source": src},
            )
        except StudioError as exc:
            if "REVISION" not in str(exc).upper() and "CONFLICT" not in str(exc).upper():
                raise
            graph = await self._call("graph.read", {"sceneId": self.scene_handle, "view": "overview"})
            self.revision = graph["revision"]
            data = await self._call(
                "graph.program",
                {"sceneId": self.scene_handle, "expectedRevision": self.revision, "idempotencyKey": _key(), "source": src},
            )
        self.revision = data["revision"]

    # ---------------------------------------------------------------- OCR
    async def read_image(self, image_path: str, variant: int = 0) -> OcrResult:
        """OCR one photo; `variant` picks the image filter (see VARIANTS)."""
        for attempt in (1, 2):
            try:
                await self.ensure_scene()
                await self._set_path(image_path, variant)
                try:
                    return await self._run()
                finally:
                    await self._clear_path()
            except StudioError as exc:
                # The user may have closed Fisoku's Studio document: rebuild once.
                if attempt == 2 or "NOT_FOUND" not in str(exc):
                    raise
                self.scene_handle = None
        raise AssertionError("unreachable")

    async def _clear_path(self) -> None:
        """Leave no photo path (user name, folders) in the Studio scene after a read."""
        try:
            data = await self._call(
                "graph.program",
                {"sceneId": self.scene_handle, "expectedRevision": self.revision, "idempotencyKey": _key(),
                 "source": f"ref('{self.path_node}').params['threshold'] = ''"},
            )
            self.revision = data["revision"]
        except StudioError:
            pass  # best effort; the next read overwrites it anyway

    async def _run(self) -> OcrResult:
        data = await self._call(
            "scene.run.start",
            {
                "sceneId": self.scene_raw,
                "mode": "one_time",
                "idempotencyKey": _key(),
                "observations": [
                    {"key": "words", "view": "node_output", "nodeId": self.ocr_raw, "outputIndex": 0},
                    {"key": "status", "view": "node_output", "nodeId": self.ocr_raw, "outputIndex": 1},
                ],
                # Short server-side waits keep each HTTP request well under client timeouts.
                "waitTimeoutMs": 15000,
            },
        )
        op = await self._wait(data, RUN_TIMEOUT_S if self.warmed_up else FIRST_RUN_TIMEOUT_S)
        if op.get("state") != "succeeded":
            err = op.get("error") or {}
            raise StudioError(f"OCR çalışmadı: {err.get('message') or op.get('state')}")
        result = op.get("result") or {}
        out: dict[str, str] = {}
        for obs in result.get("observations", []):
            if obs.get("success"):
                value = ((obs.get("data") or {}).get("output") or {}).get("value") or {}
                out[obs.get("key")] = value.get("value") or ""
        status = out.get("status", "")
        if not status.startswith("ok"):
            raise StudioError(
                f"Fisoku OCR bloğu çalışmadı ({status or 'çıktı yok'}). Studio'da Fisoku sahnesini "
                "açıp günlükteki hata mesajına bakın."
            )
        items = json.loads(out.get("words") or "[]")
        words = [i["t"] for i in items]
        boxes = [i["b"] for i in items]
        if not words:
            raise StudioError("Fotoğrafta yazı bulunamadı. Fişi düz, aydınlık ve yakından çekin.")
        self.warmed_up = True
        return OcrResult(text="\n".join(lines_from_words(words, boxes)), duration_ms=result.get("durationMs"),
                         words=words, boxes=boxes)


def read_image_sync(image_path: str, url: str = DEFAULT_URL) -> OcrResult:
    """Convenience for scripts and tests."""
    return asyncio.run(StudioOCR(url).read_image(image_path))
