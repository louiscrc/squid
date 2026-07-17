import afterburner
import struct

blob = afterburner._read_mapping_bytes("MAHMSharedMemory")
header_size, num_entries, entry_size = struct.unpack_from("<IIIIIiII", blob)[2:5]
# wait unpack full
vals = struct.unpack_from("<IIIIIiII", blob)
print("vals", vals)
header_size = vals[2]
num_entries = vals[3]
entry_size = vals[4]
print("header_size", header_size, "num", num_entries, "esz", entry_size)

for i in range(min(num_entries, 10)):
    off = header_size + i * entry_size
    elem = blob[off : off + entry_size]
    name = elem[:260].split(b"\x00", 1)[0].decode("ascii", "ignore")
    unit = elem[260:520].split(b"\x00", 1)[0].decode("ascii", "ignore")
    data = struct.unpack_from("<f", elem, 1300)[0]
    mn = struct.unpack_from("<f", elem, 1304)[0]
    mx = struct.unpack_from("<f", elem, 1308)[0]
    flags = struct.unpack_from("<I", elem, 1312)[0]
    src = struct.unpack_from("<I", elem, 1320)[0]
    print(i, repr(name), repr(unit), "data", data, "flags", hex(flags), "src", hex(src), "min", mn, "max", mx)
