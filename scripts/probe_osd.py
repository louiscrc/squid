import ctypes
import struct
import re

k = ctypes.windll.kernel32
FILE_MAP_READ = 0x0004

h = k.OpenFileMappingW(FILE_MAP_READ, False, ctypes.create_unicode_buffer("RTSSSharedMemoryV2"))
MapViewOfFile = k.MapViewOfFile
MapViewOfFile.restype = ctypes.c_void_p
p = MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0)
hdr = ctypes.string_at(p, 32)
osd_size, osd_off, osd_count = struct.unpack_from("<III", hdr, 20)
print("osd", osd_size, osd_off, osd_count)
for i in range(osd_count):
    off = osd_off + i * osd_size
    entry = ctypes.string_at(p + off, min(osd_size, 8192))
    owner = entry[256:512].split(b"\x00", 1)[0].decode("ascii", "ignore")
    text = entry[0:256].split(b"\x00", 1)[0].decode("ascii", "ignore")
    ex = entry[512:512+4096].split(b"\x00", 1)[0].decode("ascii", "ignore") if len(entry) > 512 else ""
    print(f"--- slot {i} owner={owner!r}")
    print(" text:", repr(text[:200]))
    print(" ex:", repr(ex[:500]))
    for blob in (text, ex):
        for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(FPS|fps|FPS:)", blob):
            print("  fps-like", m.group(0))
        for m in re.finditer(r"(?:FPS|Framerate)[^\d]{0,8}(\d+)", blob, re.I):
            print("  fps-tag", m.group(1))
k.UnmapViewOfFile(ctypes.c_void_p(p))
k.CloseHandle(h)

# fix mem parse
import afterburner

raw = afterburner._read_mapping_bytes("Global\\MAHMSharedMemory")
_sig, _ver, hs, ne, es, _t, ng, gs = struct.unpack_from("<IIIIIiII", raw)
goff = hs + ne * es
mem_kb = struct.unpack_from("<I", raw, goff + 260 * 5)[0]
print("mem_kb", mem_kb, "MB", mem_kb / 1024.0)
# also print family/device strings
family = raw[goff + 260 : goff + 520].split(b"\x00", 1)[0]
device = raw[goff + 520 : goff + 780].split(b"\x00", 1)[0]
print("family", family, "device", device)
