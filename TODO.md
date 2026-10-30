# TODO

- [x] Make to_csv.py incremental
- [ ] Once the dataset paper is published, add its BibTeX entry to the Hugging Face dataset card (a "Citation" section, which HF renders with a copy button) and update "Suggested credit" to cite the paper
- [ ] 30 Oct 2026: check that `snapshots/2026-10-29.parquet` reached Hugging Face (first upload by cron at 00:05 Paris). On violette2: `tail data/cron.log`, `tail data_1/collect.log`, `crontab -l`
- [ ] 30 Oct 2026: check 29 Oct for gaps longer than ~2 min (`check_parquet.ipynb` section 4). Fill only those gaps from the Mac's backup collector (restarted 29 Oct 18:00 UTC, files in the Mac's `data_1/raw`); never merge the whole Mac folder into violette2 (issue 17). Then stop the Mac collector
- [ ] Upload the fixed dataset card (`~/Desktop/hf_card.md`): the `stations` config breaks the Hub viewer and `load_dataset`
- [ ] Model day types separately (weekday, weekend, public holiday such as 1 Nov, 11 Nov, Christmas): a separate dataset listing each day and its type, then time-of-day models per type or with a day-type effect
