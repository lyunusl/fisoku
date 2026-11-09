"""Entry point for `python -m fisoku`, the `fisoku` command and Fisoku.exe.

Starts the local server on all interfaces (so the phone can reach it on the same Wi-Fi),
then opens the dashboard on this PC. Closing this window stops Fisoku.
"""
from __future__ import annotations

import argparse
import socket
import threading
import time
import webbrowser

import uvicorn

from . import __version__
from .server import build, lan_ip
from .store import default_data_dir
from .studio import DEFAULT_URL

DEFAULT_PORT = 8787


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        return s.connect_ex(("127.0.0.1", port)) == 0


def main() -> None:
    ap = argparse.ArgumentParser(prog="fisoku", description="Fiş okuma: telefonla çek, AugeLab Studio ile oku.")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--studio-url", default=DEFAULT_URL, help="AugeLab Studio MCP adresi")
    ap.add_argument("--data-dir", default=None, help="Fişlerin ve veritabanının klasörü")
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    url = f"http://127.0.0.1:{args.port}/"
    if port_in_use(args.port):
        print(f"Fisoku zaten çalışıyor: {url}")
        if not args.no_browser:
            webbrowser.open(url)
        time.sleep(2)
        return

    from pathlib import Path

    data_dir = Path(args.data_dir) if args.data_dir else default_data_dir()
    app = build(data_dir, args.port, args.studio_url)

    print(f"Fisoku {__version__}")
    print(f"  Bilgisayarda : {url}")
    print(f"  Telefonda    : http://{lan_ip()}:{args.port}  (bilgisayardaki QR kodu okutun)")
    print(f"  Veriler      : {data_dir}")
    print(f"  AugeLab      : {args.studio_url}")
    print("Kapatmak için bu pencereyi kapatın.\n")

    if not args.no_browser:
        threading.Timer(1.2, lambda: webbrowser.open(url)).start()
    # log_config=None: no console log formatting (also required in a windowed exe).
    uvicorn.run(app, host="0.0.0.0", port=args.port, log_level="warning", log_config=None)

