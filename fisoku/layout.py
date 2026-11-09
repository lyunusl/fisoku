"""Rebuild receipt lines from OCR words and their boxes.

OCR engines return text pieces in detection order, and on a photo the paper is rarely
straight, so a price at the right edge can sit a whole line higher or lower than its
item name. A receipt is a stack of horizontal lines: we find the paper's skew, undo it,
group pieces whose vertical centres are close, and read each group left to right.
"""
from __future__ import annotations

from collections import Counter
from statistics import median

Box = list[list[float]]  # 4 corner points [[x, y], ...]


def _best_slope(items: list[dict], step_px: float) -> float:
    """Projection profile: the slope at which word centres pile up into the fewest rows.

    Tries slopes up to about +/-6 degrees; prefers the flattest one on ties.
    """
    best, best_score = 0.0, -1
    for k in sorted(range(-100, 101, 2), key=abs):
        s = k / 1000
        rows = Counter(round((i["yc"] - s * i["xc"]) / step_px) for i in items)
        score = sum(c * c for c in rows.values())
        if score > best_score:
            best, best_score = s, score
    return best


def lines_from_words(texts: list[str], boxes: list[Box]) -> list[str]:
    items = []
    for t, b in zip(texts, boxes):
        if not t or not t.strip() or len(b) < 4:
            continue
        xs, ys = [p[0] for p in b], [p[1] for p in b]
        items.append({"t": t.strip(), "x": min(xs), "xc": sum(xs) / len(xs),
                      "yc": sum(ys) / len(ys), "h": max(ys) - min(ys)})
    if not items:
        return []
    h = median(i["h"] for i in items)
    slope = _best_slope(items, step_px=max(h / 2, 2.0))
    for i in items:
        i["y"] = i["yc"] - slope * i["xc"]

    items.sort(key=lambda i: i["y"])
    groups: list[list[dict]] = []
    for it in items:
        if groups and abs(it["y"] - sum(g["y"] for g in groups[-1]) / len(groups[-1])) <= 0.5 * h:
            groups[-1].append(it)
        else:
            groups.append([it])
    return [" ".join(i["t"] for i in sorted(g, key=lambda i: i["x"])) for g in groups]
