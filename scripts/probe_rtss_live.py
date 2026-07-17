import afterburner
import ctypes
import json
import struct

print("canonical", json.dumps(afterburner.to_canonical(afterburner.read_raw_entries()), indent=2))
print("rtss_fps", afterburner.read_rtss_fps())

k = ctypes.windll.kernel32
FILE_MAP_READ = 4
MAX_PATH = 260
h = k.OpenFileMappingW(FILE_MAP_READ, False, ctypes.create_unicode_buffer("RTSSSharedMemoryV2"))
print("open", bool(h), "err", k.GetLastError())
if not h:
    raise SystemExit
MapViewOfFile = k.MapViewOfFile
MapViewOfFile.restype = ctypes.c_void_p
p = MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0)
hdr = ctypes.string_at(p, 72)
print("sig", hdr[:4])
ver, a_size, a_off, a_count = struct.unpack_from("<IIII", hdr, 4)
o_size, o_off, o_count = struct.unpack_from("<III", hdr, 20)
print("app", hex(ver), a_size, a_off, a_count)
print("osd", o_size, o_off, o_count)
fg = struct.unpack_from("<II", hdr, 64) if ver >= 0x00020015 else (0, 0)
print("fg", fg)

# Try both interpretations: named-app fields vs named-osd fields
for label, size, off, count in (
    ("as_app", a_size, a_off, a_count),
    ("as_osd_fields", o_size, o_off, o_count),
):
    print("---", label, "---")
    found = 0
    for i in range(min(int(count), 64)):
        base = off + i * size
        try:
            pid = struct.unpack_from("<I", ctypes.string_at(p + base, 4))[0]
        except Exception as e:
            print("read fail", i, e)
            break
        if not pid:
            continue
        entry = ctypes.string_at(p + base, min(280, size))
        name = entry[4 : 4 + MAX_PATH].split(b"\x00", 1)[0].decode("ascii", "ignore")
        if size >= 280:
            t0, t1, frames = struct.unpack_from("<III", entry, 268)
            fps = frames * 1000.0 / (t1 - t0) if t1 > t0 and frames else None
        else:
            t0 = t1 = frames = fps = None
        print(f"[{i}] pid={pid} fps={fps} frames={frames} {name!r}")
        found += 1
        if found >= 12:
            break
    print("found", found)

# OSD text slots (Afterburner often writes here)
print("--- osd text ---")
for i in range(min(o_count, 8)):
    base = o_off + i * o_size
    entry = ctypes.string_at(p + base, min(4608, o_size))
    owner = entry[256:512].split(b"\x00", 1)[0].decode("ascii", "ignore")
    text = entry[0:256].split(b"\x00", 1)[0].decode("ascii", "ignore")
    ex = entry[512:512 + 4096].split(b"\x00", 1)[0].decode("ascii", "ignore") if len(entry) > 512 else ""
    if owner or text or ex:
        print(i, "owner", owner)
        print(" text", repr(text[:120]))
        print(" ex", repr(ex[:300]))

k.UnmapViewOfFile(ctypes.c_void_p(p))
k.CloseHandle(h)
