"""Local storage: SQLite database and photo folder in the user's data directory."""
from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path


def documents_dir() -> Path:
    """The user's Documents folder (follows OneDrive/folder redirection on Windows)."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        buf = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 5, None, 0, buf) == 0:  # CSIDL_PERSONAL
            return Path(buf.value)
    return Path.home() / "Documents"


def default_data_dir() -> Path:
    # Not %LOCALAPPDATA%: AugeLab Studio's image loader cannot open files there, and
    # people expect their receipt photos somewhere they can find them. Not plain
    # "Fisoku" either: on Windows that is the same folder as a "fisoku" source checkout.
    return documents_dir() / "Fisoku Fisleri"


class Store:
    """States: queued -> processing -> done | error. Status after done: ok | review."""

    def __init__(self, data_dir: Path) -> None:
        self.dir = data_dir
        self.photos = data_dir / "photos"
        self.photos.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(data_dir / "fisoku.db", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute(
            """CREATE TABLE IF NOT EXISTS receipts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL,
                photo TEXT NOT NULL,
                state TEXT NOT NULL DEFAULT 'queued',
                status TEXT NOT NULL DEFAULT '',
                data TEXT NOT NULL DEFAULT '{}',
                problems TEXT NOT NULL DEFAULT '[]',
                ocr_text TEXT NOT NULL DEFAULT '',
                error TEXT NOT NULL DEFAULT '',
                edited INTEGER NOT NULL DEFAULT 0
            )"""
        )
        self._db.commit()

    def add(self, photo_name: str) -> int:
        with self._lock:
            cur = self._db.execute(
                "INSERT INTO receipts (created_at, photo) VALUES (?, ?)",
                (datetime.now().isoformat(timespec="seconds"), photo_name),
            )
            self._db.commit()
            return int(cur.lastrowid)

    def update(self, rid: int, **fields) -> None:
        for k in ("data", "problems"):
            if k in fields and not isinstance(fields[k], str):
                fields[k] = json.dumps(fields[k], ensure_ascii=False)
        cols = ", ".join(f"{k} = ?" for k in fields)
        with self._lock:
            self._db.execute(f"UPDATE receipts SET {cols} WHERE id = ?", (*fields.values(), rid))
            self._db.commit()

    def get(self, rid: int) -> dict | None:
        with self._lock:
            row = self._db.execute("SELECT * FROM receipts WHERE id = ?", (rid,)).fetchone()
        return _row(row) if row else None

    def all(self) -> list[dict]:
        with self._lock:
            rows = self._db.execute("SELECT * FROM receipts ORDER BY id DESC").fetchall()
        return [_row(r) for r in rows]

    def pending_ids(self) -> list[int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT id FROM receipts WHERE state IN ('queued', 'processing') ORDER BY id"
            ).fetchall()
        return [r["id"] for r in rows]

    def delete(self, rid: int) -> None:
        rec = self.get(rid)
        with self._lock:
            self._db.execute("DELETE FROM receipts WHERE id = ?", (rid,))
            self._db.commit()
        if rec:
            (self.photos / rec["photo"]).unlink(missing_ok=True)


def _row(row: sqlite3.Row) -> dict:
    d = dict(row)
    d["data"] = json.loads(d["data"] or "{}")
    d["problems"] = json.loads(d["problems"] or "[]")
    return d
