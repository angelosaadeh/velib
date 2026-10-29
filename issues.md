# Data issues

Problems and oddities found in the Vélib' data, with examples, for the descriptive paper. Internal: not
published with the dataset (to_parquet.py only uploads `snapshots/` and `stations.csv`).

**Numbers are permanent IDs** (entries and the paper refer to them): issues are grouped by section, so numbers
can be out of order; never renumber.

**Adding an issue:** give it the next free number, and say when it was found, by whom, and on which data. Include a
real example (rows from the Parquet files), how to detect it, and what it affects. Update the numbers of an
existing issue rather than adding a duplicate.

Unless stated otherwise, everything below was measured on 29 Oct 2026 on the Mac's raw files of
27–29 Oct 2026 (3 days, ~3,100 fetches, 1,520 stations), by velib-c7 and use-velib-d2. Times are Paris time.
Examples are rows of `snapshots/*.parquet` (`time` added from `src_ts`).

Contents: [Collection](#collection) · [Feed](#feed) · [Stations](#stations) · [Addresses](#addresses)

## Collection

### 1. Missing minutes (gaps)

Snapshots come every ~61 s (median spacing of `src_ts`), but some are missing. Missing minutes are absent, with
no placeholder rows. The 3 largest gaps in the first days:

```
2026-10-28 08:27:14 -> 09:02:48   2134 s  (35 min; laptop collector only, not checked against violette2)
2026-10-28 04:45:53 -> 04:47:52    119 s  (one missed minute)
2026-10-29 02:21:55 -> 02:23:54    119 s
```

- Detect: `t = pd.Series(sorted(df.src_ts.unique())); t.diff().nlargest(10)`
- Affects: per-minute rates and anything that assumes 1,440 snapshots a day.

### 2. Two collectors merged

A laptop collector (`collect_n.py 1`, from 27 Oct 2026 10:34) ran alongside the server collector on violette2.
Their raw files are merged in violette2's `data_1/raw` and deduplicated on `lastUpdatedOther`. The first
snapshot is `src_ts` 1793093646 (27 Oct 2026 10:34:06), so the 2026-10-27 file is a partial day.

### 3. Collector errors only go to stdout

`collect_n.py` prints failed fetches to stdout, not to `fetch_log.csv`. Unless its output is captured, a
failed fetch shows up only as a gap (issue 1).

## Feed

### 4. Duplicate snapshots

The feed is fetched slightly more often than it updates, so the same snapshot (same `lastUpdatedOther`) is
sometimes fetched twice: 32 of 3,102 fetches. One copy is kept. Seen in the raw file names, which carry the
fetch time and the source time:

```
station_status_<fetch time>_src<same source time>.json   x2  -> one snapshot
```

### 5. Glitch snapshots (no mechanical / e-bike split)

In some snapshots the feed drops the split for every station at once (`num_bikes_available_types` is
`[{}, {}]`), so `mechanical` and `ebike` are null for that whole snapshot; `bikes` and `docks` are still
given. `stationCode` is null in those snapshots too. They come at most once an hour, about 22–27 minutes past
the hour, and not every hour: 21 in the first 52.5 hours. Station 394404659 (Gare Saint-Lazare - Cour du
Havre):

```
time            bikes docks mechanical ebike
10-27 18:21:10     25    19       16.0   9.0
10-27 18:22:11     26    18        NaN   NaN   <- glitch snapshot
10-27 18:23:12     28    16       20.0   8.0
```

- Detect: `df[df.mechanical.isna() & df.ebike.isna()].src_ts.unique()`
- Affects: mechanical vs e-bike series (interpolate or skip those minutes).

### 6. Redundant, empty or unstable fields

- `numBikesAvailable` / `numDocksAvailable` equal `num_bikes_available` / `num_docks_available` in every row.
- `station_opening_hours` is always null.
- `rental_methods` (in `station_information`) is present for only some stations: 896 of 1,520 have
  `["CREDITCARD"]`, and 624 lack the key (the same in 3 fetches on 29 Oct 2026).
- The `is_*` flags are always the integers 0/1, although GBFS specifies booleans. to_parquet.py accepts both.

### 15. The feed's timestamp once went backwards

Found 29 Oct 2026 by velib-c7, on the Mac's `fetch_log.csv` (3,128 fetches, 27–29 Oct 2026). Once, a fetch
returned a `lastUpdatedOther` 1 s *earlier* than the previous fetch's, with different content: the two
snapshots have the same bikes and docks everywhere, but 18 stations have an older `last_reported` in the one
fetched later. Fetch times in UTC:

```
fetched_at_utc        lastUpdatedOther   change
2026-10-28 02:08:53   1793153325          +61 s
2026-10-28 02:09:53   1793153324           -1 s   <- fetched later, older timestamp
2026-10-28 02:10:53   1793153400          +76 s
```

Both are kept (different `src_ts`), so in the Parquet files, sorted by `src_ts`, the snapshot fetched later
comes first (03:08:44 before 03:08:45 Paris time). Which one reflects the newer state is unknown.

- Detect (raw): `log = pd.read_csv("data_1/fetch_log.csv"); log[log.lastUpdatedOther.diff() < 0]`
- Affects: anything that assumes `src_ts` follows fetch order; spacing between snapshots (a 1 s step next to a
  76 s one).

## Stations

### 7. Stale `last_reported`

`last_reported` doesn't show whether a station is alive. Station 6352 (Saint-Bon - Rivoli, code 4018) has the
same `last_reported`, 1511790036 (27 Nov 2017), in every snapshot, while its counts change and its flags are
all 1:

```
time            bikes docks mechanical ebike last_reported is_installed is_renting is_returning
10-27 10:34:06      1    30        0.0   1.0    1511790036            1          1            1
10-28 12:22:50      2    29        0.0   2.0    1511790036            1          1            1
10-29 14:52:25      1    30        1.0   0.0    1511790036            1          1            1
```

In about 1% of rows (17 stations), `last_reported` is more than 24 h older than `src_ts`. It can also be
*after* `src_ts`, by up to 44 s (station 37420, 27 Oct 21:22:02: `src_ts` 1793132522, `last_reported`
1793132566).

- Detect: `(df.src_ts - df.last_reported) > 86400`, and `df.last_reported > df.src_ts`
- Affects: any "is the station working" rule based on `last_reported`.

### 8. Stations flagged off with frozen counts

Only three flag combinations occur: (1,1,1) 4,520,773 rows, (1,0,0) 51,540, (0,0,0) 9,045 (over 3,015
snapshots). `is_renting`
always equals `is_returning`. 22 of 1,520 stations had a flag at 0 at some point:

- 3 not installed for the whole period: 380891087 Place de la Bastille - Bourdon (0 bikes / 35 docks),
  82571110 Verdun - Place Henri IV, 27414512 Caire - Dussoubs (0 / 39);
- 17 more installed but not renting for the whole period;
- 2 switched to renting: 653080383 Bruneseau - Quai d'Ivry (27 Oct 14:22), and 27414460 Danielle Casanova -
  Place Vendôme (28 Oct 12:23, an hour after it first appeared; issue 11).

Most stations flagged off show frozen counts. Station 21797305137, Invalides - Quai d'Orsay (capacity 410), has
the same counts in every snapshot:

```
time            bikes docks last_reported is_installed is_renting is_returning
10-27 10:34:06      0   400    1790071584            1          0            0
10-29 14:52:25      0   400    1790071584            1          0            0
```

- Detect: `df.groupby("station_id")[["is_renting"]].min()`, and stations with one distinct (bikes, docks).
- Affects: fill ratios and availability; these stations should usually be excluded.

### 9. Stations flagged off whose counts still change

4 stations flagged not renting for the whole period still change counts: 37420 Hauteville - Bonne Nouvelle
(294 changes), 123095125 Alibert - Jemmapes (310), 20702583198 Hôtel de Ville - Adrienne Bolland (63),
20635651817 Réaumur - Montmartre (2). The cause is unknown: operator moves, or flags that lag. Station 37420:

```
time            bikes docks is_renting is_returning
10-27 10:47:18      6    10          0            0
10-27 10:52:25      7     9          0            0
10-27 10:57:29      8     8          0            0
```

- Affects: "flagged off = frozen" can't be assumed; neither the flags nor the counts alone say a station is
  out of service.

### 10. Always 0 bikes and 0 docks

4 stations report 0/0 in every snapshot: 19186120360 Haut du Mont-Mesly, 82571110 Verdun - Place Henri IV,
82396562 Mairie du 10ème, 54000553 Marignan - Champs-Elysées. All are flagged not renting. The "0/0 =
offline" rule finds these 4; the flags find 22 (issue 8). No 0/0 snapshot has all flags at 1. Station
82396562:

```
time            bikes docks last_reported is_installed is_renting is_returning
10-27 10:34:06      0     0    1792649006            1          0            0
10-29 14:52:25      0     0    1792649006            1          0            0
```

- Affects: the fill ratio bikes / (bikes + docks) is undefined.

### 11. Stations come and go

The station count went from 1,519 to 1,520 on 28 Oct 2026 11:23, when 27414460 Danielle Casanova - Place
Vendôme appeared; it started renting an hour later. A station that leaves `station_information` keeps its
past snapshots but gets no row in `stations.csv`.

- Detect: `df.groupby("src_ts").station_id.nunique()` over time.

### 12. Capacity vs bikes + docks

The feed gives no count of disabled bikes or broken docks, so `bikes` + `docks` is below
`capacity_official` in about half of all rows. It can also exceed it: 32 stations have a negative
`capacity_gap`. The largest:

```
station_id  name                                    capacity_official  max(bikes + docks)
1062807847  BNF - Bibliothèque Nationale de France                 42                  82
 653073004  Tremblay - Lac des Minimes                             48                  72
```

For all 22 flagged-off stations, max(bikes + docks) ≤ `capacity_official` (e.g. Invalides - Quai d'Orsay 400
vs 410).

- Affects: using `capacity_official` as the denominator of a fill ratio.

### 16. `last_reported` moves backwards

Found 29 Oct 2026 by velib-c7, on Parquet built from the Mac's raw files (3,086 snapshots, 27–29 Oct 2026). A
station's `last_reported` is not monotonic: from one snapshot to the next it goes back in time in 9,562 of
4.69 million steps (0.20%), at 693 of 1,520 stations. The step back has median 49 s and maximum 604 s. It often
flips between two values: 572 of these steps return to the value of 2 snapshots earlier. 468 of the 3,086
snapshots contain such a step (median 21 stations in each). Station 2515829865 (Basilique):

```
time            src_ts      bikes docks last_reported
10-28 03:07:44  1793153264      9    12    1793150597
10-28 03:08:44  1793153324      9    12    1793150254   <- back 343 s
10-28 03:08:45  1793153325      9    12    1793150597
10-28 03:10:00  1793153400      9    12    1793150254   <- back again
10-28 03:11:00  1793153460      9    12    1793150597
```

See also issue 7 (stale `last_reported`).

- Detect: `d = df.sort_values(["station_id", "src_ts"]).groupby("station_id").last_reported.diff(); (d < 0).sum()`
- Affects: using `last_reported` as "time of the station's latest report", e.g. measuring staleness or
  ordering reports by it.

## Addresses

### 13. Stations without an address

The reverse geocoder finds no address near 4 stations, 3 of them in the Bois de Vincennes / Bois de Boulogne
areas: 82425449 Bois de Vincennes -
Polygone, 129086648 Ceinture du Lac Inferieure - Saint-Cloud, 653076373 Bois de Vincennes - Porte Jaune,
19309244936 Créteil Pompadour. Their address columns are empty, and to_parquet.py retries them every run.

### 14. Addresses drift between geocodings

Re-geocoding all stations on 29 Oct 2026 changed 69 of 1,516 addresses compared with a geocoding done up to a
day earlier, mostly to a neighbouring house number and once to a different postal code. Stored addresses keep
the geocoder's data from their `geocoded_at` date.

The geocoder's batch endpoint uses the `address` (BAN) index by default: tested on 5 stations, the results are
identical with and without `indexes=address`. Re-tested on the 69 changed addresses: without the field, the
geocoder returns the new address for 60 of them and the old one for none, so the drift comes from the
geocoder's data, not from the index.
