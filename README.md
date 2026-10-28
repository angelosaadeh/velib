# Vélib' data pipeline

## 1. Collect station status

Saves one JSON file per minute in `data_1/` via the [station_status API](https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole/station_status.json).

```bash
python collect_n.py 1
```

## 2. Compare snapshots

Compares changes between consecutive time steps (here 1 minute).

```bash
python compare.py data_1/raw/*.json > data_1/compare_result.txt
```

## 3. Convert to CSV

Creates `data_1/snapshots.csv` from all JSON files in `data_1/raw/`, skipping repeated data.

Columns: `src_ts`, `station_id`, `bikes`, `docks`, `mechanical`, `ebike`, `last_reported`

```bash
python to_csv.py data_1/raw
```

## 4. Build station metadata

```bash
python build_station.py data_1/snapshots.csv
```

- Downloads `station_information.json` into `data_1/` via the [station_information API](https://velib-metropole-opendata.smovengo.cloud/opendata/Velib_Metropole/station_information.json).
- Cross-references it with `data_1/snapshots.csv`.
- Builds `stations.csv` with columns: `station_id`, `station_code`, `name`, `lat`, `lon`, `capacity_official`, `observed_capacity`.
- Geocodes each station's coordinates and adds: `house_number`, `street`, `postal_code`, `city`.