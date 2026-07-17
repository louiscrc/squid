import ctypes
import struct
import subprocess

r = subprocess.run(
    [
        "powershell",
        "-NoProfile",
        "-Command",
        "(Get-Process Cyberpunk2077,MSIAfterburner,RTSS -EA SilentlyContinue | Select-Object -ExpandProperty Id) -join ','",
    ],
    capture_output=True,
    text=True,
)
pids = [int(x) for x in r.stdout.strip().split(",") if x.strip().isdigit()]
print("pids", pids)

k = ctypes.windll.kernel32
h = k.OpenFileMappingW(4, False, ctypes.create_unicode_buffer("RTSSSharedMemoryV2"))
MapViewOfFile = k.MapViewOfFile
MapViewOfFile.restype = ctypes.c_void_p
p = MapViewOfFile(h, 4, 0, 0, 0)
blob = ctypes.string_at(p, 5578752)

for pid in pids:
    needle = struct.pack("<I", pid)
    idx = 0
    hits = 0
    while hits < 8:
        i = blob.find(needle, idx)
        if i < 0:
            break
        print("pid", pid, "at", i, hex(i), "ctx", blob[i : i + 16].hex())
        hits += 1
        idx = i + 4
    print("pid", pid, "hits", hits)

app_off = 2396256
app_size = 12416
nonzero = 0
for i in range(256):
    e = blob[app_off + i * app_size : app_off + i * app_size + 4]
    if e != b"\x00\x00\x00\x00":
        nonzero += 1
        if nonzero <= 5:
            name = blob[app_off + i * app_size + 4 : app_off + i * app_size + 40]
            print("app slot", i, e.hex(), name)
print("nonzero app slots", nonzero)

osd_off = 96
osd_size = 299520
for i in range(8):
    o = osd_off + i * osd_size
    owner = blob[o : o + 64].split(b"\x00")[0]
    text = blob[o + 256 : o + 256 + 64].split(b"\x00")[0]
    ex = blob[o + 512 : o + 512 + 128].split(b"\x00")[0]
    if owner or text or ex:
        print("osd", i, "owner", owner, "text", text[:40], "ex", ex[:40])

k.UnmapViewOfFile(ctypes.c_void_p(p))
k.CloseHandle(h)
