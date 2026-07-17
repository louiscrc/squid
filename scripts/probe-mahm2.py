import ctypes
import struct

FILE_MAP_READ = 0x0004
k = ctypes.windll.kernel32
k.MapViewOfFile.restype = ctypes.c_void_p

name = "MAHMSharedMemory"
h = k.OpenFileMappingW(FILE_MAP_READ, False, ctypes.create_unicode_buffer(name))
print("handle", int(h) if h else 0)
if not h:
    raise SystemExit(1)

ptr = k.MapViewOfFile(h, FILE_MAP_READ, 0, 0, 0)
print("ptr", ptr, hex(ptr) if ptr else None)
k.SetLastError(0)
if not ptr:
    # try with explicit size
    ptr = k.MapViewOfFile(h, FILE_MAP_READ, 0, 0, 65536)
    print("ptr65536", ptr, "err", k.GetLastError())

if ptr:
    header = ctypes.string_at(ptr, 64)
    print("sig", header[:4], list(header[:4]))
    print("hex", header[:32].hex())
    if header[:4] == b"MAHM":
        vals = struct.unpack_from("<IIIIIiII", header)
        print("header", vals)
        header_size, num_entries, entry_size = vals[2], vals[3], vals[4]
        print("num_entries", num_entries, "entry_size", entry_size)
        if num_entries > 0:
            off = header_size
            elem = ctypes.string_at(ptr + off, min(entry_size, 1400))
            name0 = elem[:260].split(b"\x00", 1)[0]
            print("first entry name", name0)
            data = struct.unpack_from("<f", elem, 1300)[0]
            flags = struct.unpack_from("<I", elem, 1312)[0]
            print("data", data, "flags", hex(flags))
    k.UnmapViewOfFile(ctypes.c_void_p(ptr))
k.CloseHandle(h)
