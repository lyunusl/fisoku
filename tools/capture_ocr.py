"""Run every samples/*.jpg through AugeLab Studio and store the OCR result next to it.

The stored samples/*.ocr.txt (rebuilt lines) and *.words.json (raw words and boxes) let
the tests run without Studio.
Usage (Studio open, MCP enabled): python tools/capture_ocr.py [images ...]
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fisoku.studio import StudioOCR  # noqa: E402


async def main() -> None:
    studio = StudioOCR(state_file=ROOT / ".studio_scene.json")
    images = [Path(a) for a in sys.argv[1:]] or sorted((ROOT / "samples").glob("*.jpg"))
    for img in images:
        res = await studio.read_image(str(img.resolve()))
        img.with_suffix(".ocr.txt").write_text(res.text, encoding="utf-8")
        img.with_suffix(".words.json").write_text(
            json.dumps({"words": res.words, "boxes": res.boxes}, ensure_ascii=False), encoding="utf-8"
        )
        print(f"{img.name}: {len(res.text)} chars, {res.duration_ms} ms")


if __name__ == "__main__":
    asyncio.run(main())
