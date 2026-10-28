#!/usr/bin/env python3
"""Convert raw Velib station_status snapshots into two compact CSVs.

Usage:
    python3 to_csv.py data_1/raw      # writes data_1/snapshots.csv
    python3 to_csv.py data_1/raw --out other.csv

snapshots.csv: one row per (snapshot, station), dense, ordered by src_ts then station_id.
    src_ts,station_id,bikes,docks,mechanical,ebike,last_reported
Station metadata lives in stations.csv, built by build_stations.py.

Snapshots sharing the same lastUpdatedOther are the same data fetched twice, so only the
first is kept. In the hourly glitch snapshots the feed drops the mechanical/ebike split;
mechanical/ebike are then written empty and bikes/docks are kept.

Stdlib only.
"""
import argparse
import csv
import json
from pathlib import Path


def split_types(types):
    """[{'mechanical': 6}, {'ebike': 2}] -> (6, 2); the glitch form [{}, {}] -> (None, None)."""
    merged = {}
    for t in types or []:
        merged.update(t)
    return merged.get("mechanical"), merged.get("ebike")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", nargs="+", help="directories of station_status_*.json")
    ap.add_argument("--out", help="default: snapshots.csv next to the first raw dir (e.g. data_1/)")
    args = ap.parse_args()
    args.out = args.out or str(Path(args.raw[0]).resolve().parent / "snapshots.csv")

    paths = sorted(p for d in args.raw for p in Path(d).glob("*.json"))
    seen = set()
    n_rows = n_dup = n_glitch = 0

    with open(args.out, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["src_ts", "station_id", "bikes", "docks", "mechanical", "ebike", "last_reported"])
        # order by the feed's own timestamp, not by filename
        docs = sorted((json.loads(p.read_text()) for p in paths), key=lambda d: d["lastUpdatedOther"])
        for doc in docs:
            src = doc["lastUpdatedOther"]
            if src in seen:
                n_dup += 1
                continue
            seen.add(src)
            stations = sorted(doc["data"]["stations"], key=lambda s: s["station_id"])
            glitch = False
            for s in stations:
                sid, bikes, docks = s["station_id"], s["num_bikes_available"], s["num_docks_available"]
                mech, ebike = split_types(s["num_bikes_available_types"])
                glitch |= mech is None and ebike is None
                w.writerow([src, sid, bikes, docks,
                            "" if mech is None else mech, "" if ebike is None else ebike,
                            s["last_reported"]])
                n_rows += 1
            n_glitch += glitch

    print(f"{len(paths)} files read | {len(seen)} unique snapshots ({n_dup} duplicates dropped) "
          f"| {n_glitch} glitch snapshots | {n_rows} rows -> {args.out}")


if __name__ == "__main__":
    main()
