import ctypes
import struct

k = ctypes.windll.kernel32
FILE_MAP_READ = 0x0004
MAX_PATH = 260

for name in ("RTSSSharedMemoryV2", "Global\\RTSSSharedMemoryV2"):
    h = k.OpenFileMappingW(FILE_MAP_READ, False, ctypes.create_unicode_buffer(name))
    print("open", name, bool(h), "err", k.GetLastError())
    if not h:
        continue
    MapViewOfFile = k.MapViewOfFile
    MapViewOfFile.restype = ctypes.c_void_p
    p = MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0)
    print(" map", bool(p), "err", k.GetLastError())
    if not p:
        k.CloseHandle(h)
        continue
    hdr = ctypes.string_at(p, 72)
    ver, app_size, app_off, app_count = struct.unpack_from("<IIII", hdr, 4)
    osd_size, osd_off, osd_count = struct.unpack_from("<III", hdr, 20)
    print(" ver", hex(ver), "app", app_size, app_off, app_count, "osd", osd_size, osd_off, osd_count)
    fg_idx = fg_pid = None
    if ver >= 0x00020015:
        fg_idx, fg_pid = struct.unpack_from("<II", hdr, 64)
        print(" fg", fg_idx, fg_pid)

    # Try reading one app entry via pointer arithmetic in ctypes
    found = 0
    for i in range(min(app_count, 256)):
        off = app_off + i * app_size
        try:
            pid = struct.unpack_from("<I", ctypes.string_at(p + off, 4))[0]
        except Exception as e:
            print(" read fail at", i, off, e)
            break
        if not pid:
            continue
        entry = ctypes.string_at(p + off, 280)
        name_b = entry[4 : 4 + MAX_PATH]
        end = name_b.find(b"\x00")
        app = name_b[: end if end >= 0 else MAX_PATH].decode("ascii", "ignore")
        t0, t1, frames = struct.unpack_from("<III", entry, 268)
        fps = frames * 1000.0 / (t1 - t0) if t1 > t0 and frames else None
        print(f" [{i}] pid={pid} fps={fps} name={app!r}")
        found += 1
        if found >= 15:
            break
    print(" found", found)
    k.UnmapViewOfFile(ctypes.c_void_p(p))
    k.CloseHandle(h)

# MAHM mem
import afterburner

raw = afterburner._read_mapping_bytes("Global\\MAHMSharedMemory")
hs, ne, es, ng, gs = struct.unpack_from("<IIIIIiII", raw)[2:]
goff = hs + ne * es
mem_kb = struct.unpack_from("<I", raw, goff + MAX_PATH * 5)[0]
print("mem_kb", mem_kb, "MB", mem_kb / 1024.0)
