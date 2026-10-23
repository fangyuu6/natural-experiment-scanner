# L3 pooled readiness tools

These tools build an external policy/source registry and count-only availability audit. They never fit a model or read politics, need values, author effect/inclusion flags, L2 outputs or author analysis results. All inputs are retained free public originals; no author code is executed and no network request is made by these scripts.

Run from the repository root:

```sh
python3 scripts/l3_pooled/build_need_dictionary.py
python3 scripts/l3_pooled/build_need_dictionary.py --check
python3 scripts/l3_pooled/readiness.py
```

Python stdlib suffices; Stata-118 reader supports little-endian numeric/fixed-string selected columns and fails closed for unsupported releases or selected strL. It validates data block lengths and Stata missing sentinels. Independent pandas 2.3.3 validation matched all 616,380 author rows over eight primitive timing/identifier/stock columns. It used the existing `.venv_did` only as a read-only oracle; runtime scripts have no pandas dependency.

Maintained rules: `need_rules.csv` (exact native topic + description regex, fixed priorities/lag/calendar exclusions); `source_windows.csv` (source bounds with evidence status); `tracker_rules.csv` (family/source metadata matches, primary endpoint first); `cspp_links.csv` (definition-only links/dispositions for all 94 CSPP variables). Every rule change needs a documented version before associations. Per-policy exceptions based on observed outcomes are prohibited.

Outputs under `data/l3_pooled/` are covered by `.gitignore: /data/`. Native and author definition/date crosswalks, unresolved lexical overlap candidates, source/state/policy windows, tracker candidates, both need dictionaries, all domain-era counts, endpoint survival candidates, the fixed first-100 queue, input hashes and summary JSON remain machine-readable. Raw/count scopes overlap; no additive unique-policy total is implied. Author risk rows are supplied-status ceilings with unresolved launch/opportunity/clock rules; all certified scored counts are zero. Candidate calendar support is not actual released state-year indicator coverage.

Human-readable snapshots are in `docs/plans/l3-pooled/{READINESS_REPORT,RECONCILIATION_RULES,OBSERVATION_RULES,CSPP_CROSSWALK,NEED_DICTIONARY_V0,COUNT_AUDIT,BATCH_100}.md`. The report is limited to 50 lines; it lists all reused inputs by URL/bytes/license, including unresolved underlying terms. No new downloads occurred. Snapshot counts must be refreshed after changed rules/input versions; the count runner does not overwrite narrative reports.

The first100 queue requires exact ID, all48 supplied state pairs and a tracker endpoint; round-robin supported domains, metadata definition/history status and fixed-seed SHA-256 decide order. Dates, event counts and indicator eligibility/values do not rank policies. A queued policy is not source-complete. The full benchmark remains in accounting, including unavailable and no-need policies; future L1/L2 links must retain stopped/unestimated designs. Genuine2027–2029 forecasts require independent2026 baseline/annual zeros and timestamped predictions.
