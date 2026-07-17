import afterburner
import struct

blob = afterburner._read_mapping_bytes("MAHMSharedMemory")
vals = struct.unpack_from("<IIIIIiII", blob)
hs, n, es = vals[2], vals[3], vals[4]
for i in range(n):
    off = hs + i * es
    elem = blob[off : off + es]
    name = elem[:260].split(b"\x00", 1)[0].decode("ascii", "ignore")
    unit = elem[260:520].split(b"\x00", 1)[0].decode("ascii", "ignore")
    data = struct.unpack_from("<f", elem, 1300)[0]
    print(i, repr(name), repr(unit), data if data < 1e30 else "MAX")
