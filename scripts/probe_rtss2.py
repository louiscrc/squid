"""Probe RTSS app entries + MAHM GPU mem amount."""
from __future__ import annotations

import ctypes
import struct

FILE_MAP_READ = 0x0004
MAX_PATH = 260


def map_bytes(name: str, size: int = 0) -> bytes | None:
    k = ctypes.windll.kernel32
    h = k.OpenFileMappingW(FILE_MAP_READ, False, ctypes.create_unicode_buffer(name))
    if not h:
        print("open fail", name, k.GetLastError())
        return None
    try:
        MapViewOfFile = k.MapViewOfFile
        MapViewOfFile.restype = ctypes.c_void_p
        p = MapViewOfFile(h, FILE_MAP_READ, 0, 0, size)
        if not p:
            print("map fail", k.GetLastError())
            return None
        try:
            # peek header to know needed size
            hdr = ctypes.string_at(p, 32)
            ver, app_size, app_off, app_count = struct.unpack_from("<IIII", hdr, 4)
            need = app_off + app_count * app_size
            need = min(max(need, 4096), 16 * 1024 * 1024)
            return ctypes.string_at(p, need)
        finally:
            k.UnmapViewOfFile(ctypes.c_void_p(p))
    finally:
        k.CloseHandle(h)


def main():
    blob = map_bytes("RTSSSharedMemoryV2", 8 * 1024 * 1024)
    if not blob:
        print("no rtss")
        return
    sig = blob[:4]
    print("sig", sig, "len", len(blob))
    ver, app_size, app_off, app_count = struct.unpack_from("<IIII", blob, 4)
    osd_size, osd_off, osd_count = struct.unpack_from("<III", blob, 20)
    print(
        hex(ver),
        "app",
        app_size,
        app_off,
        app_count,
        "osd",
        osd_size,
        osd_off,
        osd_count,
    )
    if ver >= 0x00020015:
        fg_idx, fg_pid = struct.unpack_from("<II", blob, 64)
        print("fg", fg_idx, fg_pid)

    found = 0
    for i in range(app_count):
        off = app_off + i * app_size
        if off + 280 > len(blob):
            print("trunc at", i)
            break
        pid = struct.unpack_from("<I", blob, off)[0]
        if not pid:
            continue
        name = blob[off + 4 : off + 4 + MAX_PATH]
        end = name.find(b"\x00")
        app = name[: end if end >= 0 else MAX_PATH].decode("ascii", "ignore")
        t0, t1, frames = struct.unpack_from("<III", blob, off + 268)
        fps = frames * 1000.0 / (t1 - t0) if t1 > t0 and frames else None
        print(f"[{i}] pid={pid} fps={fps} frames={frames} dt={t1-t0} name={app!r}")
        found += 1
        if found >= 20:
            break
    print("found", found)

    # MAHM GPU mem
    import afterburner

    raw = afterburner._read_mapping_bytes("Global\\MAHMSharedMemory")
    hs, ne, es, ng, gs = struct.unpack_from("<IIIIIiII", raw)[2:]
    goff = hs + ne * es
    mem_kb = struct.unpack_from("<I", raw, goff + MAX_PATH * 5)[0]
    print("dwMemAmount KB", mem_kb, "MB", mem_kb / 1024)


if __name__ == "__main__":
    main()
