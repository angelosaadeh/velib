#!/usr/bin/env python3
"""Convert raw Velib station_status snapshots into one Parquet file per day, rebuild the station
metadata, and optionally upload both to a Hugging Face dataset. Replaces to_csv.py +
build_stations.py for the daily pipeline (both stay usable on their own).

Usage:
    python3 to_parquet.py data_1/raw                    # update data/snapshots/ and data/stations.csv
    python3 to_parquet.py data_1/raw --create-new       # rewrite every day from scratch
    python3 to_parquet.py data_1/raw --push angelosaadeh/velib
    python3 to_parquet.py data_1/raw --no-download --no-geocode   # offline

Output (default: data/ next to the raw dir's parent, e.g. data_1/raw -> data/):
    snapshots/YYYY-MM-DD.parquet  one Paris-time day (--tz) of src_ts, station_id, bikes, docks,
                                  mechanical, ebike, last_reported, is_installed, is_renting,
                                  is_returning; ordered by station_id then src_ts
    stations.csv                  same columns as build_stations.py, but observed_capacity is
                                  max(bikes + docks) over the finished days in snapshots/, plus
                                  geocoded_at (UTC date the address was geocoded, empty if none)
    errors.txt                    one tab-separated line per problem, appended, never pushed

Each run writes the days that have no file yet and rewrites the newest existing file, which may
have been written while that day was still being collected. An older day is rebuilt only if the
raw files hold a snapshot (src_ts) that its file lacks: a backfill, or a file that was unreadable
before. If its file holds a snapshot no longer in the raw files (moved or deleted), it is kept as
is and the mismatch logged, so a rebuild never loses a snapshot. --create-new, though, rewrites
every day from whatever raw files exist now: after raw files are moved or deleted it WILL shrink
those days, and a push would then replace them on the Hub. DST days hold 23 or 25 hours; pick
--tz once, since files written with different --tz don't line up.

A JSON that does not parse, lacks a field or holds a bad value is skipped, logged once in
errors.txt, and the rest of the day is still written. Exception: a file younger than FRESH seconds
is skipped without logging, since the collector is probably still writing it; the next run reads
it. Failing steps (download, geocoding, push) are logged on every run. Errors also go to stderr.

stations.csv: station_information.json is downloaded next to the raw dir (falls back to the local
copy with a warning) and geocoded with build_stations.py's cache (postal_codes_cache.csv, same
folder), so only new or moved stations hit the geocoder.

--push uploads the finished days (every day but the newest) and stations.csv in one commit;
huggingface_hub skips the files whose content already matches the Hub, and makes no commit if
nothing changed. So a day rebuilt or edited by hand is re-uploaded on the next push, and nothing
else on the Hub (e.g. the README edited on the website) is touched. The dataset must already exist (create it on
huggingface.co, which also sets its visibility); needs `pip install huggingface_hub` and
`hf auth login` with a write token.

Duplicates, glitch snapshots and _srcNA files are handled as in to_csv.py. Needs pyarrow.
"""
import argparse
import csv
import json
import os
import sys
import time
from array import array
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

from build_stations import download_info, geocode, load_cache, save_cache
from to_csv import split_types, src_from_name

FRESH = 300  # seconds: an unreadable file younger than this is probably still being written
META_KEY = b"to_parquet"  # Parquet key-value metadata holding day_state
FLAGS = ["is_installed", "is_renting", "is_returning"]
SCHEMA = pa.schema([("src_ts", pa.int64()), ("station_id", pa.int64()), ("bikes", pa.int16()),
                    ("docks", pa.int16()), ("mechanical", pa.int16()), ("ebike", pa.int16()),
                    ("last_reported", pa.int64())] + [(f, pa.int8()) for f in FLAGS])
STATION_FIELDS = ["station_id", "station_code", "name", "lat", "lon", "house_number", "street",
                  "postal_code", "city", "capacity_official", "observed_capacity", "capacity_gap",
                  "geocoded_at"]


class ErrorLog:
    """errors.txt: when, what (a raw file name or a step), detail. A raw file is logged only once,
    a failing step (download, geocoding, push) on every run."""

    def __init__(self, path):
        self.path = path
        self.files = set()
        if path.exists():
            with open(path, encoding="utf-8") as fh:
                self.files = {line.split("\t")[1] for line in fh if line.count("\t") >= 2}
        self.new = 0

    def file(self, name, detail):
        if name not in self.files:
            self.files.add(name)
            self.step(name, detail)

    def step(self, what, detail):
        detail = " ".join(str(detail).split())  # no tabs or newlines: the file is split on tabs
        when = datetime.now(timezone.utc).isoformat(timespec="seconds")
        with open(self.path, "a", encoding="utf-8") as fh:
            fh.write(f"{when}\t{what}\t{detail}\n")
        self.new += 1
        print(f"ERROR {what}: {detail}", file=sys.stderr)


def count(v):
    """A bike/dock count that fits int16, or None; raises otherwise."""
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, int) or not 0 <= v <= 32767:
        raise ValueError(f"bad count {v!r}")
    return v


def flag(v):
    """GBFS specifies booleans, Vélib sends 0/1: both become 0/1, a missing flag None."""
    if v is None or v in (0, 1):  # True == 1 and False == 0, so bools pass too
        return None if v is None else int(v)
    raise ValueError(f"bad flag {v!r}")


def read_snapshot(p):
    """(src, rows) of one JSON file; raises on anything malformed, so the caller can skip the file."""
    doc = json.loads(p.read_text())
    src = int(doc["lastUpdatedOther"])
    rows = []
    for s in doc["data"]["stations"]:
        mech, ebike = split_types(s["num_bikes_available_types"])
        rows.append((int(s["station_id"]), count(s["num_bikes_available"]), count(s["num_docks_available"]),
                     count(mech), count(ebike), int(s["last_reported"]), *(flag(s.get(f)) for f in FLAGS)))
    return src, rows


def day_state(path):
    """What a day file was built from ({"n_raw": .., "skipped": [..]}), or None for an unknown file."""
    meta = pq.read_schema(path).metadata or {}
    return json.loads(meta[META_KEY]) if META_KEY in meta else None


def write_day(paths, out, log):
    """Read one day's sorted JSON files into out. Returns (snapshots, duplicates, glitches, skipped)."""
    names = ["station_id", "bikes", "docks", "mechanical", "ebike", "last_reported", *FLAGS]
    cols = {"src_ts": array("q"), **{n: [] for n in names}}
    seen = set()
    skipped = []
    n_dup = n_glitch = 0
    for _, _, p in paths:
        try:
            src, rows = read_snapshot(p)
        except Exception as e:
            skipped.append(p.name)
            if time.time() - p.stat().st_mtime >= FRESH:
                log.file(p.name, f"{type(e).__name__}: {e}")
            continue
        if src in seen:
            n_dup += 1
            continue
        seen.add(src)
        n_glitch += any(r[3] is None and r[4] is None for r in rows)
        cols["src_ts"].extend([src] * len(rows))
        for i, n in enumerate(names):
            cols[n].extend(r[i] for r in rows)
    table = pa.table({f.name: pa.array(cols[f.name], type=f.type) for f in SCHEMA}, schema=SCHEMA)
    table = table.sort_by([("station_id", "ascending"), ("src_ts", "ascending")])
    # remember what the day was built from, so a later run can tell it needs rebuilding
    state = json.dumps({"n_raw": len(paths), "skipped": skipped})
    table = table.replace_schema_metadata({META_KEY: state})
    # write next to the target and rename, so a crash never leaves a half-written file behind
    tmp = out.with_suffix(".parquet.tmp")
    pq.write_table(table, tmp, compression="zstd")
    os.replace(tmp, out)
    return len(seen), n_dup, n_glitch, len(skipped)


def observed_capacity(day_files):
    """{station_id: max(bikes + docks)} over the given day files, one at a time to keep memory flat."""
    max_seen = {}
    for f in day_files:
        t = pq.read_table(f, columns=["station_id", "bikes", "docks"])
        t = t.append_column("total", pc.add(t["bikes"].cast(pa.int32()), t["docks"].cast(pa.int32())))
        g = t.group_by("station_id").aggregate([("total", "max")])
        for sid, m in zip(g["station_id"].to_pylist(), g["total_max"].to_pylist()):
            if m is not None and m > max_seen.get(sid, -1):
                max_seen[sid] = m
    return max_seen


def build_stations(day_files, out, info_path, cache_path, download, do_geocode, log):
    """stations.csv as build_stations.py writes it, from the Parquet days instead of snapshots.csv."""
    if download:
        try:
            download_info(info_path)
        except Exception as e:
            if not info_path.exists():
                log.step("station_information download", f"{type(e).__name__}: {e}; no local {info_path}")
                sys.exit(1)
            log.step("station_information download", f"{type(e).__name__}: {e}; used the existing copy")
    stations = json.loads(info_path.read_text(encoding="utf-8"))["data"]["stations"]
    max_seen = observed_capacity(day_files)

    cache = load_cache(cache_path)
    todo = [s for s in stations
            if not (s["station_id"] in cache and cache[s["station_id"]]["postal_code"]
                    and "city" in cache[s["station_id"]]
                    and cache[s["station_id"]].get("geocoded_at")
                    and float(cache[s["station_id"]]["lat"]) == s["lat"]
                    and float(cache[s["station_id"]]["lon"]) == s["lon"])]
    if todo and do_geocode:
        try:
            by_id = {s["station_id"]: s for s in todo}
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for sid, (number, street, code, city) in geocode(todo).items():
                cache[sid] = {"station_id": sid, "lat": by_id[sid]["lat"], "lon": by_id[sid]["lon"],
                              "house_number": number, "street": street, "postal_code": code, "city": city,
                              "geocoded_at": today}
            save_cache(cache_path, cache)
        except Exception as e:  # keep going: the address stays empty for those stations
            log.step("geocoding", f"{type(e).__name__}: {e}; {len(todo)} stations left without address")

    tmp = out.with_suffix(".csv.tmp")
    missing, dates = 0, []  # as written, so the printout matches stations.csv
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(STATION_FIELDS)
        for s in sorted(stations, key=lambda s: s["station_id"]):
            sid, seen = s["station_id"], max_seen.get(s["station_id"])
            c = cache.get(sid, {})
            if c and (float(c["lat"]) != s["lat"] or float(c["lon"]) != s["lon"]):
                c = {}  # station moved since it was geocoded: leave the address empty until the re-geocode succeeds
            missing += not c.get("postal_code")
            if c.get("geocoded_at"):
                dates.append(c["geocoded_at"])
            w.writerow([sid, s["stationCode"], s["name"], s["lat"], s["lon"],
                        c.get("house_number", ""), c.get("street", ""), c.get("postal_code", ""),
                        c.get("city", ""), s["capacity"],
                        "" if seen is None else seen,
                        "" if seen is None else s["capacity"] - seen, c.get("geocoded_at", "")])
    os.replace(tmp, out)
    only_snap = max_seen.keys() - {s["station_id"] for s in stations}
    geocoded = f"{min(dates)} to {max(dates)}" if dates else "-"
    print(f"{len(stations)} stations -> {out} | in snapshots only (no info): {len(only_snap)} "
          f"| without address: {missing} | geocoded {geocoded}")


def push(repo, out_dir, files, log):
    """Upload the given files (relative to out_dir) in one commit. create_commit itself drops the
    files whose content already matches the Hub, and makes no commit if none is left."""
    from huggingface_hub import CommitOperationAdd, HfApi
    from huggingface_hub.utils import RepositoryNotFoundError

    api = HfApi()
    try:
        before = api.repo_info(repo, repo_type="dataset").sha
        newest = max((Path(f).stem for f in files if f.startswith("snapshots/")), default="-")
        info = api.create_commit(repo, repo_type="dataset", commit_message=f"update to {newest}",
                                 operations=[CommitOperationAdd(rel, str(out_dir / rel)) for rel in files])
    except RepositoryNotFoundError:
        log.step("push", f"dataset {repo} not found: create it on huggingface.co first (or check the login)")
        sys.exit(1)
    except Exception as e:
        log.step("push", f"{type(e).__name__}: {e}")
        return
    print(f"nothing to push to {repo}" if info.oid == before else f"pushed to {repo}: {info.commit_url}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw", nargs="+", help="directories of station_status_*.json")
    ap.add_argument("--out", help="default: data/ next to the first raw dir's parent (data_1/raw -> data/)")
    ap.add_argument("--tz", default="Europe/Paris", help="timezone that cuts the days, e.g. Europe/Paris or UTC")
    ap.add_argument("--create-new", action="store_true", help="rewrite every day instead of only new ones")
    ap.add_argument("--no-download", action="store_true", help="use the local station_information.json as is")
    ap.add_argument("--no-geocode", action="store_true", help="do not call the geocoder")
    ap.add_argument("--push", metavar="REPO", help="Hugging Face dataset to upload to, e.g. angelosaadeh/velib")
    args = ap.parse_args()
    first_raw = Path(args.raw[0]).resolve()
    out_dir = Path(args.out) if args.out else first_raw.parent.parent / "data"
    snap_dir = out_dir / "snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    log = ErrorLog(out_dir / "errors.txt")
    tz = ZoneInfo(args.tz)

    by_day, n_na = defaultdict(list), 0
    for d in args.raw:
        for p in Path(d).glob("*.json"):
            src = src_from_name(p)
            if src is None:
                n_na += 1
            else:
                by_day[datetime.fromtimestamp(src, tz).strftime("%Y-%m-%d")].append((src, p.name, p))
    # order by the feed's own timestamp, then fetch time, so the first fetch of a duplicate wins
    for paths in by_day.values():
        paths.sort()

    written = sorted(p.stem for p in snap_dir.glob("*.parquet"))

    def stale(day):
        """An older day needs rebuilding if the raw files hold a snapshot its file lacks (backfill, or
        a file unreadable before). A snapshot in the file but no longer in raw (moved or deleted)
        means a rebuild would lose it: that is logged and the file kept."""
        path = snap_dir / f"{day}.parquet"
        try:
            if day_state(path) is None:
                return True
            built = set(pq.read_table(path, columns=["src_ts"])["src_ts"].unique().to_pylist())
        except Exception:  # unreadable day file: rebuild it
            return True
        raw = {src for src, _, _ in by_day[day]}
        if built - raw:
            log.file(f"{day}: {len(built - raw)} snapshot(s) no longer in raw", "kept the existing file")
            return False
        return bool(raw - built)

    todo = sorted(by_day) if args.create_new else sorted(
        d for d in by_day if d not in written or d == written[-1] or stale(d))
    for day in todo:
        n_snap, n_dup, n_glitch, n_skip = write_day(by_day[day], snap_dir / f"{day}.parquet", log)
        print(f"{day}: {n_snap} snapshots ({n_dup} duplicates dropped) | {n_glitch} glitch | {n_skip} unreadable")
    if n_na:
        print(f"skipped {n_na} files with no source timestamp in their name")

    # the newest day may be unfinished: leave it out of stations.csv and the push, so stations.csv
    # changes at most once a day and always matches the published days
    finished = sorted(snap_dir.glob("*.parquet"))[:-1]
    build_stations(finished, out_dir / "stations.csv", first_raw.parent / "station_information.json",
                   first_raw.parent / "postal_codes_cache.csv", not args.no_download, not args.no_geocode, log)

    if args.push:
        push(args.push, out_dir, [f"snapshots/{p.name}" for p in finished] + ["stations.csv"], log)
    if log.new:
        print(f"{log.new} new problem(s) logged in {log.path}")


if __name__ == "__main__":
    main()
