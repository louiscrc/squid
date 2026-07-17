"""Minimal production-safety tests (Mac-friendly, no USB/ctypes)."""

from overlay_layout import (
    LAYOUT_VERSION,
    format_metric_parts,
    normalize_layout,
)
from afterburner import (
    _FLT_MAX,
    _mahm_finite,
    _rtss_sample_live,
    _rtss_sample_seen,
    pick_display_fps,
    sanitize_fps,
    to_canonical,
)


def test_mahm_finite_rejects_flt_max():
    assert _mahm_finite(_FLT_MAX) is None
    assert _mahm_finite(float("nan")) is None
    assert _mahm_finite(75.0) == 75.0


def test_sanitize_fps():
    assert sanitize_fps(_FLT_MAX) is None
    assert sanitize_fps(0.5) is None
    assert sanitize_fps(144.0) == 144.0


def test_pick_display_fps_clears_sticky_mahm_when_rtss_idle():
    assert pick_display_fps(227.0, None, True) is None
    assert pick_display_fps(227.0, 120.0, True) == 120.0
    assert pick_display_fps(227.0, None, False) == 227.0
    assert pick_display_fps(None, None, True) is None


def test_rtss_sample_live_goes_stale():
    _rtss_sample_seen.clear()
    assert _rtss_sample_live(42, 1, 2, 10) is True
    assert _rtss_sample_live(42, 1, 2, 10) is True
    # Pretend the same counters have been frozen for >1s.
    key = (1, 2, 10)
    _rtss_sample_seen[42] = (key, _rtss_sample_seen[42][1] - 2.0)
    assert _rtss_sample_live(42, 1, 2, 10) is False
    # New counters become live again.
    assert _rtss_sample_live(42, 1, 3, 20) is True
    _rtss_sample_seen.clear()


def test_format_temp_degree_is_unit_role():
    parts = format_metric_parts("liquid_c", {"liquid_c": 42.0}, "temp")
    assert parts == [("42", "num"), ("\u00b0", "unit")]


def test_format_power_show_total_false():
    parts = format_metric_parts(
        "gpu_power_w",
        {"gpu_power_w": 100.0, "gpu_power_max_w": 300.0},
        "power",
        show_total=False,
    )
    assert parts == [("100", "num"), (" W", "unit")]


def test_format_power_with_total():
    parts = format_metric_parts(
        "gpu_power_w",
        {"gpu_power_w": 100.0, "gpu_power_max_w": 300.0},
        "power",
        show_total=True,
    )
    assert ("/300", "total") in parts


def test_to_canonical_framerate_none_when_missing_value():
    out = to_canonical(
        {
            "Framerate": {
                "value": None,
                "unit": "FPS",
                "min": 0.0,
                "max": 200.0,
                "flags": 0,
                "src_id": 0,
            }
        }
    )
    assert out["fps"] is None


def test_to_canonical_framerate_value():
    out = to_canonical(
        {
            "Framerate": {
                "value": 120.0,
                "unit": "FPS",
                "min": 0.0,
                "max": 200.0,
                "flags": 0,
                "src_id": 0,
            }
        }
    )
    assert out["fps"] == 120.0


def test_normalize_layout_version():
    layout = normalize_layout({"version": 1, "widgets": [], "arcs": {"enabled": False}})
    assert layout["version"] == LAYOUT_VERSION
