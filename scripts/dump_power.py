import afterburner
import subprocess

e = afterburner.read_raw_entries()
for n, v in sorted(e.items()):
    nl = n.lower()
    if "power" in nl or "limit" in nl or "pwr" in nl:
        print(repr(n), v)

print("--- nvidia-smi ---")
try:
    out = subprocess.check_output(
        [
            "nvidia-smi",
            "--query-gpu=power.draw,power.limit,power.max_limit,power.default_limit",
            "--format=csv,noheader,nounits",
        ],
        text=True,
        timeout=2,
        creationflags=0x08000000,
    )
    print(out.strip())
except Exception as ex:
    print(ex)

print("--- canonical ---")
print(afterburner.to_canonical(e))
