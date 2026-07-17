"""Overlay layout persistence, defaults, and value formatting."""

from __future__ import annotations

import json
import os
import uuid
from copy import deepcopy
from typing import Any, Dict, List, Optional, Tuple

LAYOUT_VERSION = 10

# Clear air between label bottom and value top (both centered on their y).
LABEL_VALUE_GAP_PX = 12
# Rubik Bold: half of glyph height from middle baseline ≈ this * fontSize
GLYPH_HALF_FACTOR = 0.36

# (text, role) — "num" main; "total" grey max suffix; "unit"/"tiny" grey units
MetricPart = Tuple[str, str]

METRIC_META = [
    {"key": "liquid_c", "label": "LIQUID", "format": "temp"},
    {"key": "fps", "label": "FPS", "format": "int"},
    {"key": "cpu_power_w", "label": "CPU PWR", "format": "power"},
    {"key": "gpu_power_w", "label": "GPU PWR", "format": "power"},
    {"key": "gpu_temp_c", "label": "GPU °", "format": "temp"},
    {"key": "cpu_temp_c", "label": "CPU °", "format": "temp"},
    {"key": "gpu_usage_pct", "label": "GPU %", "format": "pct"},
    {"key": "cpu_usage_pct", "label": "CPU %", "format": "pct"},
    {"key": "vram", "label": "VRAM", "format": "mem"},
    {"key": "ram", "label": "RAM", "format": "mem"},
]

# Metrics that can drive a rim arc (need a 0..1 fill amount)
ARC_METRIC_KEYS = (
    "cpu_temp_c",
    "gpu_temp_c",
    "liquid_c",
    "cpu_usage_pct",
    "gpu_usage_pct",
    "cpu_power_w",
    "gpu_power_w",
    "fps",
    "ram",
    "vram",
)

DEFAULT_ARC_COLORS = ["#3278ff", "#ffffff", "#ff3232"]

# Default arc side readout placement (value center).
DEFAULT_ARC_SIDE = {
    "left": {
        "metric": "cpu_temp_c",
        "min": 20,
        "max": 100,
        "x": 70,
        "y": 320,
        "fontSize": 30,
        "labelFontSize": 16,
        "label": "",
    },
    "right": {
        "metric": "gpu_temp_c",
        "min": 20,
        "max": 90,
        "x": 570,
        "y": 320,
        "fontSize": 30,
        "labelFontSize": 16,
        "label": "",
    },
}


def default_arcs() -> Dict[str, Any]:
    return {
        "enabled": True,
        "colors": list(DEFAULT_ARC_COLORS),
        "left": deepcopy(DEFAULT_ARC_SIDE["left"]),
        "right": deepcopy(DEFAULT_ARC_SIDE["right"]),
    }


def default_layout() -> Dict[str, Any]:
    """Aligned from user's tuned placement (symmetric columns, shared row ys)."""
    widgets = [
        _w("liquid_c", "LIQUID", "temp", 320, 94, 82, 26),
        _w("fps", "FPS", "int", 320, 282, 140, 30),
        _w("cpu_usage_pct", "CPU", "pct", 166, 177, 72, 24),
        _w("gpu_usage_pct", "GPU", "pct", 474, 177, 72, 24),
        _w("ram", "RAM", "mem", 185, 378, 60, 22),
        _w("vram", "VRAM", "mem", 455, 378, 60, 22),
        _w("cpu_power_w", "CPU PWR", "power", 230, 491, 56, 22),
        _w("gpu_power_w", "GPU PWR", "power", 410, 491, 56, 22),
    ]
    return {
        "version": LAYOUT_VERSION,
        "arcs": default_arcs(),
        "backgroundColor": "#000000",
        "textColor": "#ffffff",
        "labelColor": "#9a9a9a",
        "widgets": widgets,
    }


def default_unit_font(font_size: int) -> int:
    return max(10, int(round(font_size * 0.55)))


def default_total_font(font_size: int) -> int:
    return max(9, int(round(font_size * 0.42)))


def _w(metric, label, fmt, x, y, font, label_font):
    return {
        "id": "w_" + metric,
        "metric": metric,
        "label": label,
        "x": x,
        "y": y,
        "fontSize": font,
        "labelFontSize": label_font,
        "unitFontSize": default_unit_font(font),
        "totalFontSize": default_total_font(font),
        "showTotal": True,
        "color": "#ffffff",
        "align": "center",
        "format": fmt,
    }


def widget_stack_ys(
    y: float,
    font_size: int,
    label_size: int,
    has_label: bool,
) -> Tuple[Optional[float], float]:
    """Return (label_cy, value_cy) with a fixed pixel gap between glyphs.

    Widget ``y`` is the value center. Label sits above so the clear space
    between label bottom and value top is LABEL_VALUE_GAP_PX regardless of
    font size (FPS no longer kisses its title).
    """
    value_cy = float(y)
    if not has_label:
        return None, value_cy
    value_half = float(font_size) * GLYPH_HALF_FACTOR
    label_half = float(label_size) * GLYPH_HALF_FACTOR
    label_cy = value_cy - value_half - LABEL_VALUE_GAP_PX - label_half
    return label_cy, value_cy


def layout_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    path = os.path.join(base, "KrakenLCDBridge")
    os.makedirs(path, exist_ok=True)
    return path


def layout_path() -> str:
    return os.path.join(layout_dir(), "overlay_layout.json")


def load_layout() -> Dict[str, Any]:
    path = layout_path()
    if not os.path.isfile(path):
        layout = default_layout()
        save_layout(layout)
        return layout
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "widgets" not in data:
            return default_layout()
        # Soft-migrate: normalize fills new fields; do not wipe user layouts.
        cleaned = normalize_layout(data)
        if int(data.get("version") or 1) < LAYOUT_VERSION:
            cleaned["version"] = LAYOUT_VERSION
            save_layout(cleaned)
        return cleaned
    except Exception:
        return default_layout()


def save_layout(layout: Dict[str, Any]) -> Dict[str, Any]:
    cleaned = normalize_layout(layout)
    with open(layout_path(), "w", encoding="utf-8") as f:
        json.dump(cleaned, f, indent=2)
    return cleaned


def _normalize_arcs(raw: Any, legacy_temp_arcs: Any = None) -> Dict[str, Any]:
    base = default_arcs()
    if isinstance(legacy_temp_arcs, bool) and not isinstance(raw, dict):
        base["enabled"] = legacy_temp_arcs
    if not isinstance(raw, dict):
        return base
    base["enabled"] = bool(raw.get("enabled", True))
    colors = raw.get("colors")
    if isinstance(colors, list) and len(colors) >= 3:
        base["colors"] = [str(colors[i]) for i in range(3)]
    for side in ("left", "right"):
        src = raw.get(side) if isinstance(raw.get(side), dict) else {}
        dflt = DEFAULT_ARC_SIDE[side]
        metric = str(src.get("metric") or dflt["metric"])
        if metric not in ARC_METRIC_KEYS:
            metric = dflt["metric"]
        try:
            amin = float(src.get("min", dflt["min"]))
        except Exception:
            amin = dflt["min"]
        try:
            amax = float(src.get("max", dflt["max"]))
        except Exception:
            amax = dflt["max"]
        if amax <= amin:
            amax = amin + 1
        try:
            x = int(src.get("x", dflt["x"]))
        except Exception:
            x = dflt["x"]
        try:
            y = int(src.get("y", dflt["y"]))
        except Exception:
            y = dflt["y"]
        try:
            font_size = max(8, min(200, int(src.get("fontSize", dflt["fontSize"]))))
        except Exception:
            font_size = dflt["fontSize"]
        try:
            label_font = max(
                8, min(80, int(src.get("labelFontSize", dflt["labelFontSize"])))
            )
        except Exception:
            label_font = dflt["labelFontSize"]
        label = str(src.get("label") if src.get("label") is not None else "")
        base[side] = {
            "metric": metric,
            "min": amin,
            "max": amax,
            "x": max(20, min(620, x)),
            "y": max(20, min(620, y)),
            "fontSize": font_size,
            "labelFontSize": label_font,
            "label": label,
        }
    # Left/right can't share the same metric
    if base["left"]["metric"] == base["right"]["metric"]:
        base["right"]["metric"] = (
            "gpu_temp_c"
            if base["left"]["metric"] != "gpu_temp_c"
            else "cpu_temp_c"
        )
    return base


def arc_side_title(side: Dict[str, Any]) -> str:
    custom = (side.get("label") or "").strip()
    if custom:
        return custom
    return metric_label(side.get("metric") or "")


def arc_metrics(arcs: Dict[str, Any]) -> List[str]:
    if not arcs or not arcs.get("enabled"):
        return []
    out = []
    for side in ("left", "right"):
        m = (arcs.get(side) or {}).get("metric")
        if m:
            out.append(m)
    return out


def normalize_layout(layout: Dict[str, Any]) -> Dict[str, Any]:
    arcs = _normalize_arcs(
        layout.get("arcs") if isinstance(layout, dict) else None,
        layout.get("tempArcs") if isinstance(layout, dict) else None,
    )
    used = set(arc_metrics(arcs))

    widgets_in = layout.get("widgets") if isinstance(layout, dict) else None
    if not isinstance(widgets_in, list):
        widgets_in = []
    widgets: List[Dict[str, Any]] = []
    for raw in widgets_in:
        if not isinstance(raw, dict):
            continue
        metric = str(raw.get("metric") or "")
        if not metric or metric in used:
            continue
        used.add(metric)
        wid = str(raw.get("id") or ("w_" + uuid.uuid4().hex[:8]))
        label_default = metric.upper()
        for meta in METRIC_META:
            if meta["key"] == metric:
                label_default = meta["label"]
                break
        # Prefer explicit power titles
        if metric == "cpu_power_w":
            label_default = "CPU PWR"
        elif metric == "gpu_power_w":
            label_default = "GPU PWR"
        font_size = max(8, min(200, int(raw.get("fontSize", 28))))
        unit_raw = raw.get("unitFontSize")
        total_raw = raw.get("totalFontSize")
        unit_fs = (
            default_unit_font(font_size)
            if unit_raw is None
            else max(8, min(200, int(unit_raw)))
        )
        total_fs = (
            default_total_font(font_size)
            if total_raw is None
            else max(8, min(200, int(total_raw)))
        )
        show_total = raw.get("showTotal", True)
        if isinstance(show_total, str):
            show_total = show_total.strip().lower() not in ("0", "false", "no", "")
        else:
            show_total = bool(show_total)
        widgets.append(
            {
                "id": wid,
                "metric": metric,
                "label": str(raw.get("label") or label_default),
                "x": int(raw.get("x", 320)),
                "y": int(raw.get("y", 320)),
                "fontSize": font_size,
                "labelFontSize": max(8, min(80, int(raw.get("labelFontSize", 12)))),
                "unitFontSize": unit_fs,
                "totalFontSize": total_fs,
                "showTotal": show_total,
                "color": str(raw.get("color") or "#ffffff"),
                "align": str(raw.get("align") or "center"),
                "format": str(raw.get("format") or "auto"),
            }
        )

    bg = "#000000"
    fg = "#ffffff"
    label = "#9a9a9a"
    if isinstance(layout, dict):
        raw_bg = layout.get("backgroundColor")
        raw_fg = layout.get("textColor")
        bg = "transparent" if is_transparent(raw_bg) else str(raw_bg or bg)
        fg = "transparent" if is_transparent(raw_fg) else str(raw_fg or fg)
        label = str(layout.get("labelColor") or label)
    return {
        "version": LAYOUT_VERSION,
        "arcs": arcs,
        "tempArcs": bool(arcs.get("enabled")),
        "backgroundColor": bg,
        "textColor": fg,
        "labelColor": label,
        "widgets": widgets,
    }


def _parts_missing(
    max_v: Optional[float] = None,
    as_int: bool = False,
    unit: str = "",
    show_total: bool = True,
) -> List[MetricPart]:
    if max_v is None or not show_total:
        parts: List[MetricPart] = [("--", "num")]
        if unit:
            parts.append((unit, "unit"))
        return parts
    if as_int:
        mid = "/{:.0f}".format(max_v)
    else:
        mid = "/{:.1f}".format(max_v)
    parts = [("--", "num"), (mid, "total")]
    if unit:
        parts.append((unit, "unit"))
    return parts


def format_metric_parts(
    metric: str,
    metrics: Dict[str, Any],
    fmt: str = "auto",
    show_total: bool = True,
) -> List[MetricPart]:
    """Split value into number + smaller unit/suffix (unit role = grey unit color)."""
    if metric == "vram":
        return _parts_mem(
            metrics.get("vram_used"),
            metrics.get("vram_total"),
            show_total=show_total,
        )
    if metric == "ram":
        return _parts_mem(
            metrics.get("ram_used"),
            metrics.get("ram_total"),
            show_total=show_total,
        )

    v = metrics.get(metric)
    if fmt == "auto":
        if metric.endswith("_c") or metric == "liquid_c":
            fmt = "temp"
        elif metric.endswith("_pct"):
            fmt = "pct"
        elif metric == "fps":
            fmt = "int"
        elif metric.endswith("_w"):
            fmt = "power"
        else:
            fmt = "int"

    if fmt == "temp":
        if v is None:
            return _parts_missing()
        return [("{:.0f}".format(v), "num"), ("\u00b0", "unit")]
    if fmt == "pct":
        if v is None:
            return _parts_missing()
        return [("{:.0f}".format(v), "num"), ("%", "unit")]
    if fmt == "power":
        max_key = metric.replace("_w", "_max_w") if metric.endswith("_w") else None
        mx = metrics.get(max_key) if max_key else None
        try:
            mx = float(mx) if mx is not None else None
        except Exception:
            mx = None
        if mx is not None and mx <= 0:
            mx = None
        if v is None:
            return _parts_missing(mx, as_int=True, unit=" W", show_total=show_total)
        parts: List[MetricPart] = [("{:.0f}".format(v), "num")]
        if show_total and mx is not None:
            parts.append(("/{:.0f}".format(mx), "total"))
        parts.append((" W", "unit"))
        return parts
    if fmt == "mem":
        return _parts_mem(v, None, show_total=show_total)
    if v is None:
        return _parts_missing()
    return [("{:.0f}".format(v), "num")]


def _parts_mem(
    used: Optional[float],
    total: Optional[float],
    show_total: bool = True,
) -> List[MetricPart]:
    total_gb = None
    if total is not None and total > 0:
        total_gb = total / 1024.0
    if used is None:
        return (
            _parts_missing(total_gb, as_int=False, unit=" GB", show_total=show_total)
            if total_gb is not None
            else _parts_missing()
        )
    u = used / 1024.0
    if total is None or total <= 0:
        if used <= 100:
            return [("{:.0f}".format(used), "num"), ("%", "unit")]
        return [("{:.1f}".format(u), "num"), (" GB", "unit")]
    parts: List[MetricPart] = [("{:.1f}".format(u), "num")]
    if show_total:
        parts.append(("/{:.1f}".format(total_gb), "total"))
    parts.append((" GB", "unit"))
    return parts


def format_metric(
    metric: str,
    metrics: Dict[str, Any],
    fmt: str = "auto",
    show_total: bool = True,
) -> str:
    return "".join(
        t for t, _ in format_metric_parts(metric, metrics, fmt, show_total=show_total)
    )


def is_transparent(color: Any) -> bool:
    if color is None:
        return True
    s = str(color).strip().lower()
    return s in ("", "transparent", "none", "null")


def parse_color(color: str, alpha: int = 255) -> tuple:
    if is_transparent(color):
        return (0, 0, 0, 0)
    c = (color or "#ffffff").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        r = int(c[0:2], 16)
        g = int(c[2:4], 16)
        b = int(c[4:6], 16)
    except Exception:
        r, g, b = 255, 255, 255
    return (r, g, b, alpha)


def gradient_rgb(u: float, colors: List[str]) -> tuple:
    """Interpolate 3 colors across u in [0,1]."""
    u = 0.0 if u < 0 else (1.0 if u > 1 else u)
    c0 = parse_color(colors[0] if colors else "#3278ff")[:3]
    c1 = parse_color(colors[1] if len(colors) > 1 else "#ffffff")[:3]
    c2 = parse_color(colors[2] if len(colors) > 2 else "#ff3232")[:3]
    if u < 0.5:
        v = u * 2
        a, b = c0, c1
    else:
        v = (u - 0.5) * 2
        a, b = c1, c2
    return (
        int(a[0] + (b[0] - a[0]) * v),
        int(a[1] + (b[1] - a[1]) * v),
        int(a[2] + (b[2] - a[2]) * v),
    )


def temp_to_rgb(temp_c: float, tmin: float = 20.0, tmax: float = 100.0,
                colors: Optional[List[str]] = None) -> tuple:
    u = (temp_c - tmin) / (tmax - tmin) if tmax != tmin else 0.0
    return gradient_rgb(u, colors or DEFAULT_ARC_COLORS)


def arc_fill_unit(metric: str, side: Dict[str, Any], snap: Dict[str, Any]) -> Optional[float]:
    """Return fill amount 0..1 for an arc metric, or None if missing."""
    amin = float(side.get("min", 0))
    amax = float(side.get("max", 100))
    if metric in ("ram", "vram"):
        used = snap.get("ram_used" if metric == "ram" else "vram_used")
        total = snap.get("ram_total" if metric == "ram" else "vram_total")
        if used is None or not total:
            return None
        return max(0.0, min(1.0, float(used) / float(total)))
    if metric.endswith("_w"):
        v = snap.get(metric)
        mx = snap.get(metric.replace("_w", "_max_w"))
        if mx and mx > 0:
            amin, amax = 0.0, float(mx)
        elif amax <= amin:
            amax = amin + 1
    v = snap.get(metric)
    if v is None:
        return None
    if amax <= amin:
        return None
    return max(0.0, min(1.0, (float(v) - amin) / (amax - amin)))


def arc_absolute_for_color(metric: str, side: Dict[str, Any], u: float, snap: Dict[str, Any]) -> float:
    """Map fill fraction u back to a value for gradient coloring."""
    amin = float(side.get("min", 0))
    amax = float(side.get("max", 100))
    if metric.endswith("_w"):
        mx = snap.get(metric.replace("_w", "_max_w"))
        if mx and mx > 0:
            amin, amax = 0.0, float(mx)
    if metric in ("ram", "vram"):
        amin, amax = 0.0, 100.0
        return amin + (amax - amin) * u
    return amin + (amax - amin) * u


def metric_label(metric: str) -> str:
    for meta in METRIC_META:
        if meta["key"] == metric:
            return meta["label"]
    return metric.upper()


def snapshot_for_api(metrics: Dict[str, Any]) -> Dict[str, Any]:
    values = {k: metrics.get(k) for k in (
        "liquid_c", "fps", "gpu_power_w", "gpu_power_max_w",
        "cpu_power_w", "cpu_power_max_w", "gpu_temp_c", "cpu_temp_c",
        "gpu_usage_pct", "cpu_usage_pct", "vram_used", "vram_total",
        "ram_used", "ram_total", "pump", "afterburner",
    )}
    formatted = {}
    parts = {}
    for meta in METRIC_META:
        formatted[meta["key"]] = format_metric(meta["key"], metrics, meta["format"])
        parts[meta["key"]] = [
            {"text": t, "role": r}
            for t, r in format_metric_parts(meta["key"], metrics, meta["format"])
        ]
    return {
        "values": values,
        "formatted": formatted,
        "parts": parts,
        "catalog": deepcopy(METRIC_META),
        "arcMetrics": list(ARC_METRIC_KEYS),
        "afterburner": bool(metrics.get("afterburner")),
    }
