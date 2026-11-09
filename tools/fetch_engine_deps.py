"""Download onnxruntime and shapely for Studio's Python (3.12, Windows x64) into .dev/deps.

A fresh AugeLab Studio install has numpy, OpenCV, pyclipper and PyYAML but not these
two; the Fisoku OCR block appends .../fisoku_engine/deps to sys.path *after* Studio's
own packages, so a Studio that already has them keeps using its own copies.
Usage: python tools/fetch_engine_deps.py
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WHEELS = ROOT / ".dev" / "wheels"
DEPS = ROOT / ".dev" / "deps"
PINS = ["onnxruntime==1.31.1", "shapely==2.2.0"]  # both support numpy 2.x (Studio ships 2.2)


def main() -> None:
    WHEELS.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [sys.executable, "-m", "pip", "download", "-q", "--only-binary=:all:", "--platform", "win_amd64",
         "--python-version", "3.12", "--implementation", "cp", "--no-deps", "-d", str(WHEELS), *PINS],
        check=True,
    )
    if DEPS.exists():
        shutil.rmtree(DEPS)
    DEPS.mkdir(parents=True)
    for pin in PINS:
        name = pin.split("==")[0]
        wheel = next(WHEELS.glob(f"{name}-{pin.split('==')[1]}-*.whl"))
        zipfile.ZipFile(wheel).extractall(DEPS)
    print(f"{DEPS}: {', '.join(sorted(p.name for p in DEPS.iterdir()))}")


if __name__ == "__main__":
    main()
