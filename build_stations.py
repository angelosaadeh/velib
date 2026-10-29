#!/usr/bin/env python3
"""Build stations.csv from station_information.json plus the observed snapshots.

Usage:
    python3 build_stations.py data_1/snapshots.csv
    python3 build_stations.py data_1/snapshots.csv --out stations.csv
    python3 build_stations.py data_1/snapshots.csv --no-download --no-geocode   # offline

station_information.json is downloaded from the Velib open-data feed on every run and saved
next to snapshots.csv (overwriting the previous copy). If the download fails, the existing
local copy is used with a warning. --no-download skips it; --info PATH uses another location.

Columns:
    station_id, station_code, name, lat, lon,
    house_number, street, postal_code, city
                       nearest address, reverse-geocoded from lat/lon (so a station on a
                       postal-code boundary can get the neighbouring code); house_number
                       is empty when the geocoder only matched a street
    capacity_official  capacity from station_information.json
    observed_capacity  max(bikes + docks) seen in snapshots.csv (our own estimate)
    capacity_gap       capacity_official - observed_capacity; negative means we saw more
                       bikes + docks than the official capacity allows

house_number, street, postal_code and city come from the French government geocoder (data.geopf.fr), in one batch request
for the stations not yet in postal_codes_cache.csv (next to stations.csv). Only station ids and
coordinates are sent, with indexes=address so every result comes from the Base Adresse
Nationale. The cache records the UTC date each station was geocoded (geocoded_at). Reruns reuse
the cache, so they make no request unless a station is new, its coordinates changed or its
geocoded_at is empty (rows cached before that column existed are re-geocoded once).
--no-geocode never calls the network; uncached stations are left empty. If the request fails,
those stations are left empty and the rest of the file is still written.

UTF-8, comma-delimited like snapshots.csv; the csv module quotes any name that contains a
comma or quote. The file is rebuilt from scratch on every run. Stdlib only.
"""
import argparse
import csv
import io
import json
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

INFO_URL = "https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole/station_information.json"
GEOCODER_URL = "https://data.geopf.fr/geocodage/reverse/csv/"
CACHE_FIELDS = ["station_id", "lat", "lon", "house_number", "street", "postal_code", "city", "geocoded_at"]


def download_info(path):
    """Fetch station_information.json into path; only replaces it if the download is valid."""
    req = urllib.request.Request(INFO_URL, headers={"User-Agent": "velib-build-stations"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        raw = resp.read()
    if not json.loads(raw)["data"]["stations"]:
        raise ValueError("no stations in downloaded file")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(raw)
    tmp.replace(path)


def load_cache(path):
    if not path.exists():
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {int(r["station_id"]): r for r in csv.DictReader(fh)}


def save_cache(path, cache):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CACHE_FIELDS)
        w.writeheader()
        for sid in sorted(cache):
            w.writerow(cache[sid])


def geocode(todo):
    """todo: list of station dicts. Returns {station_id: (house_number, street, postcode, city)}
    via one batch request."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["station_id", "lat", "lon"])
    for s in todo:
        w.writerow([s["station_id"], s["lat"], s["lon"]])
    boundary = uuid.uuid4().hex
    # indexes=address: Base Adresse Nationale only (already the default, made explicit)
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="indexes"\r\n\r\naddress\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="data"; filename="in.csv"\r\n'
            f"Content-Type: text/csv\r\n\r\n").encode() + buf.getvalue().encode("utf-8") \
        + f"\r\n--{boundary}--\r\n".encode()
    req = urllib.request.Request(GEOCODER_URL, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "velib-build-stations"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        text = resp.read().decode("utf-8")
    found = {}
    for r in csv.DictReader(io.StringIO(text)):
        if r.get("result_status") != "ok":
            continue
        found[int(r["station_id"])] = (r["result_housenumber"], r["result_street"],
                                  r["result_postcode"], r["result_city"])
    return found


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("snapshots", help="snapshots.csv written by to_csv.py")
    ap.add_argument("--info", help="default: station_information.json next to snapshots.csv")
    ap.add_argument("--out", help="default: stations.csv next to snapshots.csv")
    ap.add_argument("--no-download", action="store_true", help="use the local station_information.json as is")
    ap.add_argument("--no-geocode", action="store_true", help="do not call the geocoder")
    args = ap.parse_args()
    folder = Path(args.snapshots).resolve().parent
    out = Path(args.out) if args.out else folder / "stations.csv"
    info_path = Path(args.info) if args.info else folder / "station_information.json"

    if not args.no_download:
        try:
            download_info(info_path)
            print(f"downloaded station_information.json -> {info_path}")
        except Exception as e:
            if not info_path.exists():
                raise SystemExit(f"download failed ({type(e).__name__}: {e}) and no local {info_path}")
            print(f"WARNING: download failed ({type(e).__name__}: {e}); using existing {info_path}")
    cache_path = out.with_name("postal_codes_cache.csv")

    max_seen = {}
    with open(args.snapshots, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            sid, total = int(r["station_id"]), int(r["bikes"]) + int(r["docks"])
            if total > max_seen.get(sid, -1):
                max_seen[sid] = total

    stations = json.loads(info_path.read_text(encoding="utf-8"))["data"]["stations"]
    info_ids = {s["station_id"] for s in stations}
    only_info, only_snap = info_ids - max_seen.keys(), max_seen.keys() - info_ids

    cache = load_cache(cache_path)
    todo = [s for s in stations
            if not (s["station_id"] in cache and cache[s["station_id"]]["postal_code"]
                    and "city" in cache[s["station_id"]]
                    and cache[s["station_id"]].get("geocoded_at")
                    and float(cache[s["station_id"]]["lat"]) == s["lat"]
                    and float(cache[s["station_id"]]["lon"]) == s["lon"])]
    if todo and not args.no_geocode:
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            for sid, (number, street, code, city) in geocode(todo).items():
                s = next(x for x in todo if x["station_id"] == sid)
                cache[sid] = {"station_id": sid, "lat": s["lat"], "lon": s["lon"],
                              "house_number": number, "street": street,
                              "postal_code": code, "city": city, "geocoded_at": today}
            save_cache(cache_path, cache)
            print(f"geocoded {len(todo)} stations -> {cache_path}")
        except Exception as e:  # keep going: postal_code stays empty for those stations
            print(f"WARNING: geocoding failed ({type(e).__name__}: {e}); leaving postal_code empty")

    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["station_id", "station_code", "name", "lat", "lon",
                    "house_number", "street", "postal_code", "city", "capacity_official", "observed_capacity", "capacity_gap"])
        for s in sorted(stations, key=lambda s: s["station_id"]):
            sid, seen = s["station_id"], max_seen.get(s["station_id"])
            c = cache.get(sid, {})
            w.writerow([sid, s["stationCode"], s["name"], s["lat"], s["lon"],
                        c.get("house_number", ""), c.get("street", ""), c.get("postal_code", ""),
                        c.get("city", ""), s["capacity"],
                        "" if seen is None else seen,
                        "" if seen is None else s["capacity"] - seen])

    gaps = [s["capacity"] - max_seen[s["station_id"]] for s in stations if s["station_id"] in max_seen]
    missing = sum(1 for s in stations if not cache.get(s["station_id"], {}).get("postal_code"))
    print(f"{len(stations)} stations -> {out}")
    print(f"in the info file only (no snapshots): {len(only_info)} | in snapshots only (no info): {len(only_snap)}")
    print(f"capacity_gap < 0 (seen more than official): {sum(g < 0 for g in gaps)} | "
          f"= 0: {sum(g == 0 for g in gaps)} | > 0: {sum(g > 0 for g in gaps)}")
    print(f"postal_code missing: {missing}")


if __name__ == "__main__":
    main()
