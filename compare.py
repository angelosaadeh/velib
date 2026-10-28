#!/usr/bin/env python3
"""Compare consecutive Velib snapshots.

Usage: python3 compare.py manual_download/station_status_*.json
       python3 compare.py data/raw/*.json
"""
import json
import sys

paths = sorted(sys.argv[1:])
if len(paths) < 2:
    sys.exit("usage: python compare.py FILE1.json FILE2.json [...]  (need at least 2)")
docs = [json.load(open(p)) for p in paths]
# order by the feed's own timestamp, not by filename ("10" sorts before "2")
paths, docs = map(list, zip(*sorted(zip(paths, docs), key=lambda pd: pd[1]["lastUpdatedOther"])))


def key(s):
    return (s["num_bikes_available"], s["num_docks_available"], json.dumps(s["num_bikes_available_types"]))


for p, d in zip(paths, docs):
    st = d["data"]["stations"]
    print(f"{p}: src={d['lastUpdatedOther']} ttl={d['ttl']} stations={len(st)} "
          f"bikes={sum(s['num_bikes_available'] for s in st)} "
          f"max_last_reported={max(s['last_reported'] for s in st)}")

print()
for (pa, a), (pb, b) in zip(zip(paths, docs), zip(paths[1:], docs[1:])):
    A = {s["station_id"]: s for s in a["data"]["stations"]}
    B = {s["station_id"]: s for s in b["data"]["stations"]}
    common = A.keys() & B.keys()
    avail = sum(1 for k in common if key(A[k]) != key(B[k]))
    lr = sum(1 for k in common if A[k]["last_reported"] != B[k]["last_reported"])
    print(f"{pa.split('/')[-1]} -> {pb.split('/')[-1]}: "
          f"source gap {b['lastUpdatedOther'] - a['lastUpdatedOther']}s | "
          f"availability changed: {avail}/{len(common)} | last_reported changed: {lr} | "
          f"ids in only one file: {len(A.keys() ^ B.keys())}")
