import afterburner
import ctypes
import struct

print("MAHM entries:")
for n, e in sorted(afterburner.read_raw_entries().items()):
    print(repr(n), e.get("value"), e.get("unit"), "max", e.get("max"))

k = ctypes.windll.kernel32
h = k.OpenFileMappingW(4, False, ctypes.create_unicode_buffer("RTSSSharedMemoryV2"))
MapViewOfFile = k.MapViewOfFile
MapViewOfFile.restype = ctypes.c_void_p
p = MapViewOfFile(h, 4, 0, 0, 0)
hdr = ctypes.string_at(p, 32)
ver, a_size, a_off, a_count = struct.unpack_from("<IIII", hdr, 4)
print("\ntry read at app_off", a_off)
for delta in (0, 4, 268, 272, 276):
    try:
        b = ctypes.string_at(p + a_off + delta, 16)
        print(delta, b[:16].hex())
    except Exception as ex:
        print(delta, "ERR", ex)

# scan first 2MB for plausible process names
print("\nscan for .exe names in first 3MB...")
blob = ctypes.string_at(p, min(3 * 1024 * 1024, a_off + 64 * a_size))
needle = b".exe"
idx = 0
hits = 0
while hits < 20:
    i = blob.find(needle, idx)
    if i < 0:
        break
    start = max(0, i - 80)
    chunk = blob[start : i + 4]
    # find printable run
    s = ""
    for c in chunk[::-1]:
        if 32 <= c < 127:
            s = chr(c) + s
        else:
            break
    if s.lower().endswith(".exe") and len(s) > 5:
        print(hex(start), repr(s[-60:]))
        hits += 1
    idx = i + 4
print("hits", hits)
k.UnmapViewOfFile(ctypes.c_void_p(p))
k.CloseHandle(h)
