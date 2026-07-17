import afterburner
import json

raw = afterburner.read_raw_entries()
print("entries", len(raw))
for name, e in sorted(raw.items(), key=lambda x: x[0].lower()):
    print(
        repr(name),
        "unit=",
        repr(e.get("unit")),
        "data=",
        e.get("value"),
        "min=",
        e.get("min"),
        "max=",
        e.get("max"),
    )
print("---")
print(json.dumps(afterburner.to_canonical(raw), indent=2))
