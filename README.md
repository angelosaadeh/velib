# Vélib' data pipeline

## 1. Collect station status

Saves one JSON file per minute in `data_1/` via the [station_status API](https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole/station_status.json).

```bash
python collect_n.py 1
```

## 2. Convert to CSV

Writes `data_1/snapshots.csv` from the JSON files in `data_1/raw/`, skipping repeated data.

Columns: `src_ts`, `station_id`, `bikes`, `docks`, `mechanical`, `ebike`, `last_reported`

If the CSV doesn't exist it creates it from all files. Otherwise, only snapshots newer than its last `src_ts` are appended.

```bash
python to_csv.py data_1/raw                # create, or append new snapshots
python to_csv.py data_1/raw --create-new   # rebuild from scratch
```

Use `--create-new` after adding files older than the CSV's last snapshot (e.g. backfilling a gap from another machine), since appending skips them.

## 3. Build station metadata

```bash
python build_stations.py data_1/snapshots.csv
```

- Downloads `station_information.json` into `data_1/` via the [station_information API](https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole/station_information.json).
- Cross-references it with `data_1/snapshots.csv`.
- Builds `stations.csv` with columns: `station_id`, `station_code`, `name`, `lat`, `lon`, `capacity_official`, `observed_capacity`, `capacity_gap` (official minus observed; negative means more bikes + docks were seen than the official capacity).
- Geocodes each station's coordinates and adds: `house_number`, `street`, `postal_code`, `city`.