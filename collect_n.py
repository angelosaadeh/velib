#!/usr/bin/env python3
"""Same as collect.py, but the interval (in minutes) is a positional argument and
output goes to data_<minutes>/ (raw/ + fetch_log.csv).

Usage:
    python3 collect_n.py 1                  # every 1 min until Ctrl+C -> data_1/
    python3 collect_n.py 5                  # every 5 min until Ctrl+C -> data_5/
    python3 collect_n.py 60 --duration 86400  # optional time limit
    python3 collect_n.py 1 --analyze
"""
import argparse
import csv
import hashlib
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

URL = "https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole/station_status.json"
FIELDS = ["fetched_at_utc", "file", "lastUpdatedOther", "ttl", "n_stations",
          "max_last_reported", "stations_sha256", "changed_vs_prev"]


def utc(ts):
    return datetime.fromtimestamp(ts, timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def fetch():
    req = urllib.request.Request(URL, headers={"User-Agent": "velib-collector/0.1"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def last_logged_hash(log):
    if not log.exists():
        return None
    with log.open() as f:
        rows = list(csv.DictReader(f))
    return rows[-1]["stations_sha256"] if rows else None


def collect_once(prev_hash, raw, log):
    now = time.time()
    body = fetch()
    doc = json.loads(body)
    stations = doc["data"]["stations"]
    # hash only the station content, so a bumped lastUpdatedOther with identical data is detected
    digest = hashlib.sha256(json.dumps(stations, sort_keys=True).encode()).hexdigest()
    lu = doc.get("lastUpdatedOther")
    # filename: fetch time first (always unique, sorts chronologically), source timestamp second
    name = f"station_status_{utc(now)}_src{utc(lu) if lu else 'NA'}.json"
    raw.mkdir(parents=True, exist_ok=True)
    (raw / name).write_bytes(body)

    row = {
        "fetched_at_utc": datetime.fromtimestamp(now, timezone.utc).isoformat(timespec="seconds"),
        "file": name,
        "lastUpdatedOther": lu,
        "ttl": doc.get("ttl"),
        "n_stations": len(stations),
        "max_last_reported": max((s.get("last_reported") or 0) for s in stations),
        "stations_sha256": digest,
        "changed_vs_prev": "" if prev_hash is None else int(digest != prev_hash),
    }
    new = not log.exists()
    with log.open("a", newline="") as f:
        w = csv.DictWriter(f, FIELDS)
        if new:
            w.writeheader()
        w.writerow(row)
    print(f"{row['fetched_at_utc']}  src={lu}  stations={len(stations)}  changed={row['changed_vs_prev']}  -> {name}")
    return digest


def analyze(log):
    with log.open() as f:
        rows = list(csv.DictReader(f))
    n = len(rows)
    changed = sum(1 for r in rows if r["changed_vs_prev"] == "1")
    srcs = [int(r["lastUpdatedOther"]) for r in rows if r["lastUpdatedOther"]]
    deltas = [b - a for a, b in zip(srcs, srcs[1:]) if b != a]
    print(f"{n} fetches, {changed}/{max(n - 1, 1)} differed from the previous one")
    print(f"distinct lastUpdatedOther values: {len(set(srcs))}")
    if deltas:
        print(f"gap between source timestamps (s): min={min(deltas)} max={max(deltas)} "
              f"mean={sum(deltas) / len(deltas):.0f}")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("minutes", type=float, help="minutes between fetches (e.g. 1, 5, 60)")
    p.add_argument("--duration", type=int, default=None,
                   help="total seconds to run (default: run until stopped with Ctrl+C)")
    p.add_argument("--analyze", action="store_true")
    a = p.parse_args()

    label = f"{a.minutes:g}"
    out = Path(__file__).parent / f"data_{label}"
    raw, log = out / "raw", out / "fetch_log.csv"
    if a.analyze:
        return analyze(log)

    interval = a.minutes * 60
    prev = last_logged_hash(log)
    end = time.time() + a.duration if a.duration else None
    next_t = time.time()
    try:
        while True:
            try:
                prev = collect_once(prev, raw, log)
            except Exception as e:  # keep running through transient network errors
                print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')}  ERROR {e!r}")
            next_t += interval  # fixed schedule: no drift from fetch time
            if end and next_t > end:
                break
            time.sleep(max(0, next_t - time.time()))
    except KeyboardInterrupt:
        print("\nstopped")
    if log.exists():
        analyze(log)


if __name__ == "__main__":
    main()
