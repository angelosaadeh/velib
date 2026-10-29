#!/usr/bin/env python3
"""Convert raw Velib station_status snapshots into two compact CSVs.

Usage:
    python3 to_csv.py data_1/raw                 # appends new snapshots to data_1/snapshots.csv
    python3 to_csv.py data_1/raw --create-new    # rebuilds data_1/snapshots.csv from scratch
    python3 to_csv.py data_1/raw --out other.csv

snapshots.csv: one row per (snapshot, station), dense, ordered by src_ts then station_id.
    src_ts,station_id,bikes,docks,mechanical,ebike,last_reported
Station metadata lives in stations.csv, built by build_stations.py.

By default only snapshots newer than the last src_ts already in the CSV are appended, so
files backfilled with older timestamps (e.g. copied from another machine) need --create-new.
Files are ordered by the source timestamp in their name and read one at a time, so memory
stays flat however many files there are.

Snapshots sharing the same lastUpdatedOther are the same data fetched twice, so only the
first is kept. In the hourly glitch snapshots the feed drops the mechanical/ebike split;
mechanical/ebike are then written empty and bikes/docks are kept.

Stdlib only.
"""
import argparse
import csv
import io
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

HEADER = ["src_ts", "station_id", "bikes", "docks", "mechanical", "ebike", "last_reported"]
# station_status_<fetch>_src<source>.json, both as YYYYMMDDTHHMMSSZ
NAME_RE = re.compile(r"_src(\d{8}T\d{6}Z)\.json$")


def split_types(types):
    """[{'mechanical': 6}, {'ebike': 2}] -> (6, 2); the glitch form [{}, {}] -> (None, None)."""
    merged = {}
    for t in types or []:
        merged.update(t)
    return merged.get("mechanical"), merged.get("ebike")


def src_from_name(p):
    """Source timestamp (epoch seconds) encoded in the filename, or None for _srcNA files."""
    m = NAME_RE.search(p.name)
    if not m:
        return None
    return int(datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp())


def last_src_ts(path):
    """src_ts of the last row of an existing CSV, read from the end of the file."""
    with open(path, "rb") as f:
        size = f.seek(0, os.SEEK_END)
        if size == 0:
            return None
        f.seek(max(0, size - 4096))
        tail = f.read()
    if not tail.endswith(b"\n"):
        raise SystemExit(f"{path} does not end with a newline (interrupted write?); rerun with --create-new")
    last = tail.rstrip(b"\n").rsplit(b"\n", 1)[-1].decode()
    return None if last.startswith("src_ts") else int(last.split(",", 1)[0])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", nargs="+", help="directories of station_status_*.json")
    ap.add_argument("--out", help="default: snapshots.csv next to the first raw dir (e.g. data_1/)")
    ap.add_argument("--create-new", action="store_true",
                    help="rewrite the CSV from all files instead of appending new snapshots")
    args = ap.parse_args()
    args.out = args.out or str(Path(args.raw[0]).resolve().parent / "snapshots.csv")

    append = not args.create_new and os.path.exists(args.out)
    after = last_src_ts(args.out) if append else None

    paths, n_na = [], 0
    for d in args.raw:
        for p in Path(d).glob("*.json"):
            src = src_from_name(p)
            if src is None:
                n_na += 1
            elif after is None or src > after:
                paths.append((src, p.name, p))
    # order by the feed's own timestamp, then fetch time, so the first fetch of a duplicate wins
    paths.sort()

    seen = set()
    n_read = n_rows = n_dup = n_glitch = 0
    stopped = None
    with open(args.out, "a" if append else "w", newline="") as fh:
        if not append:
            csv.writer(fh).writerow(HEADER)
        for _, _, p in paths:
            try:
                doc = json.loads(p.read_text())
            except json.JSONDecodeError:
                # most likely the file the collector is writing right now; the next run picks it up
                stopped = p.name
                break
            n_read += 1
            src = doc["lastUpdatedOther"]
            if src in seen or (after is not None and src <= after):
                n_dup += 1
                continue
            seen.add(src)
            stations = sorted(doc["data"]["stations"], key=lambda s: s["station_id"])
            buf = io.StringIO()
            w = csv.writer(buf)
            glitch = False
            for s in stations:
                sid, bikes, docks = s["station_id"], s["num_bikes_available"], s["num_docks_available"]
                mech, ebike = split_types(s["num_bikes_available_types"])
                glitch |= mech is None and ebike is None
                w.writerow([src, sid, bikes, docks,
                            "" if mech is None else mech, "" if ebike is None else ebike,
                            s["last_reported"]])
            # one write per snapshot, so a Ctrl+C never leaves half a snapshot behind
            fh.write(buf.getvalue())
            n_rows += len(stations)
            n_glitch += glitch

    mode = f"appended after src_ts={after}" if append else "written from scratch"
    print(f"{n_read} files read | {len(seen)} new snapshots ({n_dup} duplicates dropped) "
          f"| {n_glitch} glitch snapshots | {n_rows} rows -> {args.out} ({mode})")
    if n_na:
        print(f"skipped {n_na} files with no source timestamp in their name")
    if stopped:
        print(f"stopped at unreadable {stopped}; later files left for the next run")


if __name__ == "__main__":
    main()
