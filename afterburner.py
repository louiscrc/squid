"""MSI Afterburner MAHMSharedMemory reader (Windows) + VRAM/FPS helpers."""

from __future__ import annotations

import ctypes
import subprocess
import struct
from typing import Any, Dict, Optional, Tuple

_HEADER_FMT = "<IIIIIiII"
_NAME_LEN = 260
_OFF_DATA = _NAME_LEN * 5  # 1300
_FLAG_ACTIVE = 0x00000001
_MAHM_SIGS = (b"MHAM", b"MAHM")  # MSVC FOURCC 'MAHM' stores as MHAM in memory
_FILE_MAP_READ = 0x0004
_CREATE_NO_WINDOW = 0x08000000

_MAPPING_NAMES = (
    "Global\\MAHMSharedMemory",
    "MAHMSharedMemory",
    "Local\\MAHMSharedMemory",
)

_RTSS_NAMES = (
    "RTSSSharedMemoryV2",
    "Global\\RTSSSharedMemoryV2",
    "Local\\RTSSSharedMemoryV2",
)


def _cstr(buf: bytes) -> str:
    end = buf.find(b"\x00")
    if end < 0:
        end = len(buf)
    return buf[:end].decode("ascii", errors="ignore").strip()


def _read_mapping_bytes(name: str) -> Optional[bytes]:
    kernel32 = ctypes.windll.kernel32
    wide = ctypes.create_unicode_buffer(name)
    handle = kernel32.OpenFileMappingW(_FILE_MAP_READ, False, wide)
    if not handle:
        return None
    try:
        MapViewOfFile = kernel32.MapViewOfFile
        MapViewOfFile.restype = ctypes.c_void_p
        ptr = MapViewOfFile(handle, _FILE_MAP_READ, 0, 0, 0)
        if not ptr:
            return None
        try:
            header = ctypes.string_at(ptr, 32)
            if header[:4] not in _MAHM_SIGS:
                return None
            (
                _sig,
                _ver,
                header_size,
                num_entries,
                entry_size,
                _t,
                num_gpu,
                gpu_entry_size,
            ) = struct.unpack_from(_HEADER_FMT, header)
            if header_size < 24 or num_entries > 1024 or entry_size < 1300:
                return None
            total = (
                int(header_size)
                + int(num_entries) * int(entry_size)
                + int(num_gpu) * int(gpu_entry_size)
            )
            total = min(max(total, 32), 8 * 1024 * 1024)
            return ctypes.string_at(ptr, total)
        finally:
            kernel32.UnmapViewOfFile(ctypes.c_void_p(ptr))
    finally:
        kernel32.CloseHandle(handle)


def read_raw_entries() -> Dict[str, Dict[str, Any]]:
    """Return {src_name: {value, unit, min, max, flags, src_id}} or {}."""
    blob = None
    for name in _MAPPING_NAMES:
        try:
            blob = _read_mapping_bytes(name)
            if blob:
                break
        except Exception:
            continue
    if not blob:
        return {}
    try:
        return _parse_blob(blob)
    except Exception:
        return {}


def _parse_blob(blob: bytes) -> Dict[str, Dict[str, Any]]:
    (
        _sig,
        _ver,
        header_size,
        num_entries,
        entry_size,
        _t,
        num_gpu,
        gpu_entry_size,
    ) = struct.unpack_from(_HEADER_FMT, blob)

    entries: Dict[str, Dict[str, Any]] = {}
    for i in range(num_entries):
        off = header_size + i * entry_size
        if off + entry_size > len(blob):
            break
        elem = blob[off : off + entry_size]
        name = _cstr(elem[0:_NAME_LEN])
        unit = _cstr(elem[_NAME_LEN : _NAME_LEN * 2])
        if not name:
            continue
        flags = struct.unpack_from("<I", elem, _OFF_DATA + 12)[0]
        data = struct.unpack_from("<f", elem, _OFF_DATA)[0]
        min_lim = struct.unpack_from("<f", elem, _OFF_DATA + 4)[0]
        max_lim = struct.unpack_from("<f", elem, _OFF_DATA + 8)[0]
        src_id = struct.unpack_from("<I", elem, _OFF_DATA + 20)[0]
        if data != data or abs(data) > 1e30:
            continue
        entries[name] = {
            "value": float(data),
            "unit": unit,
            "min": float(min_lim),
            "max": float(max_lim),
            "flags": flags,
            "src_id": src_id,
        }

    for g in range(num_gpu):
        goff = header_size + num_entries * entry_size + g * gpu_entry_size
        # szGpuId/Family/Device/Driver/BIOS = 5 * MAX_PATH, then dwMemAmount (KB)
        mem_off = goff + _NAME_LEN * 5
        if mem_off + 4 > len(blob) or gpu_entry_size < _NAME_LEN * 5 + 4:
            break
        try:
            mem_kb = struct.unpack_from("<I", blob, mem_off)[0]
            if 128 * 1024 <= mem_kb <= 256 * 1024 * 1024:
                entries["__gpu_mem_total_mb"] = {
                    "value": float(mem_kb) / 1024.0,
                    "unit": "MB",
                    "min": 0.0,
                    "max": float(mem_kb) / 1024.0,
                    "flags": _FLAG_ACTIVE,
                    "src_id": 0,
                }
                break
        except Exception:
            pass
    return entries


def _pick(entries: Dict[str, Dict[str, Any]], score_fn) -> Optional[Dict[str, Any]]:
    best = None
    best_score = -1
    for name, ent in entries.items():
        if name.startswith("__"):
            continue
        score = score_fn(name.lower(), ent)
        if score is not None and score > best_score:
            best_score = score
            best = dict(ent)
            best["_name"] = name
    return best


def _score_gpu_temp(n: str, ent: Dict) -> Optional[int]:
    if not n.startswith("gpu"):
        return None
    if "memory" in n or "vram" in n:
        return None
    unit = (ent.get("unit") or "").lower()
    if "c" not in unit and "temp" not in n and "hotspot" not in n:
        return None
    if "hotspot" in n or "hot spot" in n:
        return 100
    if "junction" in n:
        return 95
    if "temperature" in n or "temp" in n:
        return 80
    return None


def _score_cpu_temp(n: str, ent: Dict) -> Optional[int]:
    if not (n.startswith("cpu") or n.startswith("ccd")):
        return None
    if any(x in n for x in ("usage", "clock", "power", "voltage", "fan")):
        return None
    if "motherboard" in n or "vrm" in n:
        return None
    if "package" in n:
        return 100
    if "temperature" in n or "temp" in n:
        return 80
    return None


def _score_gpu_usage(n: str, ent: Dict) -> Optional[int]:
    if not n.startswith("gpu"):
        return None
    if not any(x in n for x in ("usage", "load", "utilization")):
        return None
    if "memory" in n or "vram" in n or "controller" in n:
        return 40
    return 90


def _score_cpu_usage(n: str, ent: Dict) -> Optional[int]:
    if not n.startswith("cpu"):
        return None
    if not any(x in n for x in ("usage", "load", "utilization")):
        return None
    if "thread" in n or ("core" in n and "usage" in n):
        return 50
    return 90


def _score_gpu_power(n: str, ent: Dict) -> Optional[int]:
    if "power" not in n:
        return None
    if "cpu" in n or "limit" in n:
        return None
    if n.startswith("gpu") or n == "power":
        return 90
    return 50


def _score_cpu_power(n: str, ent: Dict) -> Optional[int]:
    if "power" not in n:
        return None
    if "limit" in n:
        return None
    if n.startswith("cpu") or "cpu power" in n:
        return 90
    return None


def _score_fps(n: str, ent: Dict) -> Optional[int]:
    if "framerate" in n or n == "fps" or n.endswith(" fps"):
        return 100
    if "frame rate" in n:
        return 90
    return None


def _score_vram(n: str, ent: Dict) -> Optional[int]:
    unit = (ent.get("unit") or "").lower()
    if "memory usage" in n:
        if "gpu" in n or "dedicated" in n or "fb" in n:
            return 95
        if "mb" in unit or "gb" in unit:
            return 88
        return 70
    if n.startswith("gpu") and ("memory" in n or "vram" in n):
        if "usage" in n or "used" in n:
            return 90
        if "mb" in unit or "gb" in unit:
            return 75
    if "dedicated memory" in n or "vram usage" in n or n == "vram":
        return 85
    return None


def _score_ram(n: str, ent: Dict) -> Optional[int]:
    if n.startswith("ram") and "usage" in n:
        return 90
    if "physical memory" in n and "usage" in n:
        return 85
    if "commit charge" in n or "commitcharge" in n:
        return 75
    if "sysmem" in n or "system memory" in n:
        return 80
    if n == "memory usage":
        return 40
    return None


def _to_mb(value: float, unit: str) -> float:
    u = (unit or "").lower()
    if "gb" in u:
        return value * 1024.0
    return value


def _finite_max(ent: Optional[Dict[str, Any]]) -> Optional[float]:
    if not ent:
        return None
    mx = ent.get("max")
    if mx is None:
        return None
    try:
        mx = float(mx)
    except Exception:
        return None
    if mx != mx or abs(mx) > 1e30 or mx <= 0:
        return None
    return mx


def read_nvidia_vram_mb() -> Tuple[Optional[float], Optional[float]]:
    """Return (used_mb, total_mb) via nvidia-smi, or (None, None)."""
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=2.0,
            creationflags=_CREATE_NO_WINDOW,
        )
        line = out.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            return None, None
        return float(parts[0]), float(parts[1])
    except Exception:
        return None, None


def read_nvidia_power() -> Tuple[Optional[float], Optional[float]]:
    """Return (draw_w, limit_w) via nvidia-smi — limit is the real cap, not AB graph max."""
    try:
        out = subprocess.check_output(
            [
                "nvidia-smi",
                "--query-gpu=power.draw,power.limit,power.max_limit",
                "--format=csv,noheader,nounits",
            ],
            text=True,
            timeout=2.0,
            creationflags=_CREATE_NO_WINDOW,
        )
        line = out.strip().splitlines()[0]
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2:
            return None, None
        draw = float(parts[0]) if parts[0] not in ("", "[N/A]", "N/A") else None
        limit = None
        for p in parts[1:]:
            if p in ("", "[N/A]", "N/A"):
                continue
            try:
                limit = float(p)
                if limit > 0:
                    break
            except Exception:
                continue
        return draw, limit
    except Exception:
        return None, None


def read_rtss_fps() -> Optional[float]:
    """Best-effort FPS from RTSS hooked 3D apps (must run in same session as RTSS)."""
    kernel32 = ctypes.windll.kernel32
    for name in _RTSS_NAMES:
        handle = kernel32.OpenFileMappingW(
            _FILE_MAP_READ, False, ctypes.create_unicode_buffer(name)
        )
        if not handle:
            continue
        try:
            MapViewOfFile = kernel32.MapViewOfFile
            MapViewOfFile.restype = ctypes.c_void_p
            ptr = MapViewOfFile(handle, _FILE_MAP_READ, 0, 0, 0)
            if not ptr:
                continue
            try:
                hdr = ctypes.string_at(ptr, 72)
                if hdr[:4] not in (b"SSTR", b"RTSS"):
                    continue
                ver, app_size, app_off, app_count = struct.unpack_from("<IIII", hdr, 4)
                if ver < 0x00020000 or app_count > 512 or app_size < 280:
                    continue
                fg_pid = 0
                if ver >= 0x00020015:
                    fg_pid = struct.unpack_from("<I", hdr, 68)[0]

                best = None
                fg_fps = None
                for i in range(app_count):
                    off = app_off + i * app_size
                    try:
                        entry = ctypes.string_at(ptr + off, min(app_size, 288))
                    except Exception:
                        break
                    pid = struct.unpack_from("<I", entry, 0)[0]
                    if not pid:
                        continue
                    fps = None
                    t0, t1, frames = struct.unpack_from("<III", entry, 268)
                    if t1 > t0 and frames > 0:
                        cand = frames * 1000.0 / (t1 - t0)
                        if 0 < cand <= 1000:
                            fps = cand
                    if fps is None and len(entry) >= 284:
                        # dwFrameTime is microseconds per frame
                        frame_us = struct.unpack_from("<I", entry, 280)[0]
                        if 0 < frame_us < 1_000_000:
                            cand = 1_000_000.0 / frame_us
                            if 0 < cand <= 1000:
                                fps = cand
                    if fps is None:
                        continue
                    app = _cstr(entry[4 : 4 + _NAME_LEN]).lower()
                    score = fps
                    if fg_pid and pid == fg_pid:
                        fg_fps = fps
                    if "signalrgb" in app or "signal rgb" in app:
                        score += 1000
                    if best is None or score > best[0]:
                        best = (score, fps)
                if fg_fps is not None:
                    return fg_fps
                if best is not None:
                    return best[1]
            finally:
                kernel32.UnmapViewOfFile(ctypes.c_void_p(ptr))
        finally:
            kernel32.CloseHandle(handle)
    return None


def to_canonical(entries: Dict[str, Dict[str, Any]]) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {
        "fps": None,
        "gpu_power_w": None,
        "gpu_power_max_w": None,
        "cpu_power_w": None,
        "cpu_power_max_w": None,
        "gpu_temp_c": None,
        "cpu_temp_c": None,
        "gpu_usage_pct": None,
        "cpu_usage_pct": None,
        "vram_used": None,
        "vram_total": None,
        "ram_used": None,
        "ram_total": None,
    }
    if not entries:
        return out

    def val(picked):
        return None if picked is None else picked["value"]

    out["gpu_temp_c"] = val(_pick(entries, _score_gpu_temp))
    out["cpu_temp_c"] = val(_pick(entries, _score_cpu_temp))
    out["gpu_usage_pct"] = val(_pick(entries, _score_gpu_usage))
    out["cpu_usage_pct"] = val(_pick(entries, _score_cpu_usage))

    gpu_pwr = _pick(entries, _score_gpu_power)
    cpu_pwr = _pick(entries, _score_cpu_power)
    out["gpu_power_w"] = val(gpu_pwr)
    out["cpu_power_w"] = val(cpu_pwr)
    # GPU max: nvidia-smi power.limit (hardware cap). CPU max: Afterburner graph
    # max (what RTSS/AB show) — no portable hardware TDP via MAHM.
    out["gpu_power_max_w"] = None
    out["cpu_power_max_w"] = _finite_max(cpu_pwr)
    nv_draw, nv_limit = read_nvidia_power()
    if nv_limit and nv_limit > 0:
        out["gpu_power_max_w"] = nv_limit
    if out["gpu_power_w"] is None and nv_draw is not None:
        out["gpu_power_w"] = nv_draw

    out["fps"] = val(_pick(entries, _score_fps))

    vram = _pick(entries, _score_vram)
    if vram is not None:
        unit = vram.get("unit") or ""
        out["vram_used"] = _to_mb(vram["value"], unit)
        mx = _finite_max(vram)
        if mx is not None and mx >= (out["vram_used"] or 0):
            out["vram_total"] = _to_mb(mx, unit)
    if out["vram_total"] is None and "__gpu_mem_total_mb" in entries:
        out["vram_total"] = entries["__gpu_mem_total_mb"]["value"]

    ram = _pick(entries, _score_ram)
    if ram is not None:
        unit = ram.get("unit") or ""
        v = ram["value"]
        name = (ram.get("_name") or "").lower()
        mx = ram.get("max") or 0.0
        if "%" in unit.lower():
            if mx > 256:
                out["ram_used"] = (v / 100.0) * mx
                out["ram_total"] = mx
            else:
                out["ram_used"] = v
        else:
            out["ram_used"] = _to_mb(v, unit)
            if "commit" not in name and mx and mx > v:
                out["ram_total"] = _to_mb(mx, unit)

    return out


def read_canonical() -> Dict[str, Optional[float]]:
    return to_canonical(read_raw_entries())
