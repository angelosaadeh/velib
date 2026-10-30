import re, sys
from datetime import datetime
from pathlib import Path

# Defaut path:
d = Path(sys.argv[1] if len(sys.argv) > 1 else "data_1/raw")

ts = sorted(datetime.strptime(m[1], "%Y%m%dT%H%M%SZ") for f in d.glob("*.json")
            if (m := re.match(r"station_status_(\w{16})_src", f.name)))
gaps = [(a, int((b - a).total_seconds())) for a, b in zip(ts, ts[1:]) if int((b - a).total_seconds()) > 65]
for a, s in gaps:
    print(f"GAP {s}s after {a}")
print(f"{len(ts)} files, {len(gaps)} gaps")