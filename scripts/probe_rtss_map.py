import ctypes
import ctypes.wintypes as wt
import struct

class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wt.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wt.DWORD),
        ("Protect", wt.DWORD),
        ("Type", wt.DWORD),
    ]

k = ctypes.windll.kernel32
h = k.OpenFileMappingW(4, False, ctypes.create_unicode_buffer("RTSSSharedMemoryV2"))
MapViewOfFile = k.MapViewOfFile
MapViewOfFile.restype = ctypes.c_void_p
p = MapViewOfFile(h, 4, 0, 0, 0)
print("map", hex(p or 0))
mbi = MEMORY_BASIC_INFORMATION()
k.VirtualQuery(ctypes.c_void_p(p), ctypes.byref(mbi), ctypes.sizeof(mbi))
print("region size", mbi.RegionSize, "protect", mbi.Protect, "state", mbi.State)

hdr = ctypes.string_at(p, 96)
print("hdr hex", hdr[:48].hex())
fields = struct.unpack_from("<8I", hdr, 0)
print("sig ver appSize appOff appCount osdSize osdOff osdCount", fields)

# try MapViewOfFile with explicit large size
k.UnmapViewOfFile(ctypes.c_void_p(p))
k.CloseHandle(h)

h = k.OpenFileMappingW(0xF001F, False, ctypes.create_unicode_buffer("RTSSSharedMemoryV2"))  # FILE_MAP_ALL_ACCESS
print("all access handle", h)
if not h:
    print("err", ctypes.get_last_error())
MapViewOfFile.restype = ctypes.c_void_p
p = MapViewOfFile(h, 0x0004, 0, 0, 0)  # FILE_MAP_READ
print("map2", hex(p or 0), "err", k.GetLastError())
if p:
    mbi = MEMORY_BASIC_INFORMATION()
    k.VirtualQuery(ctypes.c_void_p(p), ctypes.byref(mbi), ctypes.sizeof(mbi))
    print("region2", mbi.RegionSize)
    # try read past 1MB
    for off in [0, 96, 100000, 500000, 1000000, 2000000, 2396256]:
        try:
            b = ctypes.string_at(p + off, 8)
            print(off, b.hex(), "ok")
        except Exception as e:
            print(off, "FAIL", e)
    k.UnmapViewOfFile(ctypes.c_void_p(p))
k.CloseHandle(h)

# processes
import subprocess
r = subprocess.run(
    ["powershell", "-NoProfile", "-Command",
     "Get-Process Cyberpunk*, MSIAfterburner, RTSS, SignalRGB* -EA SilentlyContinue | Select Name,Id | Format-Table -Auto"],
    capture_output=True, text=True)
print(r.stdout)
