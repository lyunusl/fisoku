"""Put the OCR engine where AugeLab Studio's "Fisoku OCR" block finds it.

The engine is RapidOCR (pure Python + PP-OCRv4 ONNX models) plus the two compiled
libraries a fresh Studio install does not have: onnxruntime and shapely. Nothing is
pip-installed into Studio. Fisoku copies these files next to Studio's custom block
folder (<Studio data>/marketplace/fisoku_engine); the block finds that folder relative
to its own file, so no user-specific path is ever stored in a Studio scene.

Sources: inside Fisoku.exe (engine/...), or in development the venv's
rapidocr_onnxruntime (`pip install --no-deps`) and .dev/deps (tools/fetch_engine_deps.py).
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
from pathlib import Path

PACKAGE = "rapidocr_onnxruntime"
REPO = Path(__file__).resolve().parents[1]


def _sources() -> tuple[Path | None, Path | None]:
    if getattr(sys, "frozen", False):
        base = Path(getattr(sys, "_MEIPASS", "")) / "engine"
        return (base / PACKAGE if (base / PACKAGE).is_dir() else None,
                base / "deps" if (base / "deps").is_dir() else None)
    spec = importlib.util.find_spec(PACKAGE)
    pkg = Path(spec.origin).parent if spec and spec.origin else None
    deps = REPO / ".dev" / "deps"
    return pkg, deps if deps.is_dir() else None


def source_dir() -> Path | None:
    """The rapidocr_onnxruntime package folder that gets shipped (used by build.cmd)."""
    return _sources()[0]


def _fingerprint(folder: Path) -> str:
    files = [p for p in folder.rglob("*") if p.is_file() and "__pycache__" not in p.parts]
    return f"{len(files)}:{sum(p.stat().st_size for p in files)}"


def _sync(src: Path, dest: Path, marker: Path) -> None:
    want = _fingerprint(src)
    if dest.is_dir() and marker.exists() and marker.read_text(encoding="ascii") == want:
        return
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(src, dest, ignore=shutil.ignore_patterns("__pycache__"))
    marker.write_text(want, encoding="ascii")


def ensure_engine(root: Path) -> Path:
    """Copy the engine into `root` if missing or outdated. Returns `root`."""
    pkg, deps = _sources()
    if pkg is None:
        if (root / PACKAGE).is_dir():
            return root
        raise RuntimeError(
            f"OCR motoru ({PACKAGE}) bulunamadı. Geliştirme ortamında: pip install --no-deps {PACKAGE}"
        )
    root.mkdir(parents=True, exist_ok=True)
    _sync(pkg, root / PACKAGE, root / "engine.version")
    if deps is not None:
        _sync(deps, root / "deps", root / "deps.version")
    return root
