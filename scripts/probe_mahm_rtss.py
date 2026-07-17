"""Probe MAHM GPU block + RTSS shared memory for FPS/VRAM."""
from __future__ import annotations

import ctypes
import struct

FILE_MAP_READ = 0x0004


def read_map(name: str, size: int = 0) -> bytes | None:
    k = ctypes.windll.kernel32
    h = k.OpenFileMappingW(FILE_MAP_READ, False, ctypes.create_unicode_buffer(name))
    if not h:
        return None
    try:
        MapViewOfFile = k.MapViewOfFile
        MapViewOfFile.restype = ctypes.c_void_p
        p = MapViewOfFile(h, FILE_MAP_READ, 0, 0, size)
        if not p:
            return None
        try:
            if size:
                return ctypes.string_at(p, size)
            # read header first for MAHM
            hdr = ctypes.string_at(p, 32)
            return hdr, p, h  # type: ignore
        finally:
            if size:
                k.UnmapViewOfFile(ctypes.c_void_p(p))
    finally:
        if size:
            k.CloseHandle(h)


def dump_mahm_gpu():
    import afterburner

    blob = None
    for name in afterburner._MAPPING_NAMES:
        blob = afterburner._read_mapping_bytes(name)
        if blob:
            print("map", name, "len", len(blob))
            break
    if not blob:
        print("no mahm")
        return
    (
        sig,
        ver,
        header_size,
        num_entries,
        entry_size,
        _t,
        num_gpu,
        gpu_entry_size,
    ) = struct.unpack_from("<IIIIIiII", blob)
    print("sig", sig, "ver", ver, "gpus", num_gpu, "ges", gpu_entry_size)
    for g in range(num_gpu):
        goff = header_size + num_entries * entry_size + g * gpu_entry_size
        chunk = blob[goff : goff + gpu_entry_size]
        print("gpu", g, "bytes", len(chunk))
        # dump u32 words in first 320 bytes
        for off in range(0, min(320, len(chunk)) - 3, 4):
            v = struct.unpack_from("<I", chunk, off)[0]
            if v != 0:
                print(f"  +{off:3d} u32={v} (0x{v:08x})")
        # try floats
        for off in range(0, min(320, len(chunk)) - 3, 4):
            f = struct.unpack_from("<f", chunk, off)[0]
            if f == f and 1 < abs(f) < 100000 and abs(f) > 0.01:
                print(f"  +{off:3d} f32={f}")


def dump_rtss():
    names = (
        "RTSSSharedMemoryV2",
        "Global\\RTSSSharedMemoryV2",
        "Local\\RTSSSharedMemoryV2",
    )
    k = ctypes.windll.kernel32
    for name in names:
        h = k.OpenFileMappingW(FILE_MAP_READ, False, ctypes.create_unicode_buffer(name))
        print("rtss open", name, bool(h))
        if not h:
            continue
        try:
            MapViewOfFile = k.MapViewOfFile
            MapViewOfFile.restype = ctypes.c_void_p
            p = MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0)
            if not p:
                print("  map fail")
                continue
            try:
                hdr = ctypes.string_at(p, 64)
                print("  sig", hdr[:4], "hex", hdr[:32].hex())
                # RTSS_SHARED_MEMORY header
                # DWORD dwSignature; DWORD dwVersion; DWORD dwAppEntrySize; DWORD dwAppArrOffset; DWORD dwAppArrSize; ...
                sig, ver, app_size, app_off, app_count = struct.unpack_from("<IIIII", hdr)
                print("  ver", hex(ver), "app_size", app_size, "off", app_off, "count", app_count)
                total = min(app_off + app_count * app_size, 2 * 1024 * 1024)
                blob = ctypes.string_at(p, total)
                for i in range(min(app_count, 32)):
                    off = app_off + i * app_size
                    if off + 300 > len(blob):
                        break
                    # dwProcessID at 0, szName at 4 (260 chars?)
                    pid = struct.unpack_from("<I", blob, off)[0]
                    if not pid:
                        continue
                    name_b = blob[off + 4 : off + 4 + 260]
                    end = name_b.find(b"\x00")
                    app = name_b[: end if end >= 0 else 260].decode("ascii", "ignore")
                    # dwFrames, dwFrameTime, etc. — layout varies by version
                    # common: after name+flags ... search for plausible fps
                    print(f"  app[{i}] pid={pid} name={app!r}")
                    # dump some dwords after name region
                    base = off + 4 + 260
                    words = []
                    for j in range(0, 64):
                        if base + j * 4 + 4 > off + app_size:
                            break
                        words.append(struct.unpack_from("<I", blob, base + j * 4)[0])
                    print("   words", words[:40])
            finally:
                k.UnmapViewOfFile(ctypes.c_void_p(p))
        finally:
            k.CloseHandle(h)


if __name__ == "__main__":
    dump_mahm_gpu()
    print("---RTSS---")
    dump_rtss()
