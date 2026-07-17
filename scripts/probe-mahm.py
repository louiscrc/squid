import ctypes
import afterburner

raw = afterburner.read_raw_entries()
print("raw count", len(raw))
print("keys", list(raw.keys())[:30])
print("canonical", afterburner.to_canonical(raw))

k = ctypes.windll.kernel32
for name in ["Global\\MAHMSharedMemory", "MAHMSharedMemory", "Local\\MAHMSharedMemory"]:
    k.SetLastError(0)
    h = k.OpenFileMappingW(0x0004, False, ctypes.create_unicode_buffer(name))
    err = k.GetLastError()
    print("open", name, "handle", int(h) if h else 0, "err", err)
    if h:
        k.CloseHandle(h)
