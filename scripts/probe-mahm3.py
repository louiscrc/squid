import afterburner
import ctypes
import struct

# Force read path
blob = afterburner._read_mapping_bytes("MAHMSharedMemory")
print("blob", None if blob is None else len(blob), None if blob is None else blob[:4])
if blob:
    print("sig ok", blob[:4] in afterburner._MAHM_SIGS)
    try:
        entries = afterburner._parse_blob(blob)
        print("entries", len(entries))
        for k, v in list(entries.items())[:15]:
            print(" ", k, v.get("value"), v.get("unit"))
        print("canonical", afterburner.to_canonical(entries))
    except Exception as e:
        import traceback
        traceback.print_exc()
