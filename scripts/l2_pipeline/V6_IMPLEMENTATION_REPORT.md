# V6 implementation and run3 intake

Implemented generic outcome-first ranking and target-to-measure catalog mappings, coverage-bounded primary/sensitivity windows, S0 domain-lens purpose jobs, citation/field collection and explicit hub acceptance. S0/S2/S4 prevent declared legal coverage overruns; full V6 intake also bounds connector vintages and marks unknown/empty intersections. V3 ten-treated audits, V5 table imports, V4 stock/dates and fresh freeze/forecast rules are retained. No expected signs enter selection.

FARS now reads immutable official national CSV ZIPs (1982–2022): full traffic deaths/crashes and exact child/teen age/role counts, including bicyclist PER_TYP=6. Missing ages block certified subgroup linkage; fatal person records must reproduce crash totals. California DOJ is a public no-key crime alternative (CA only, 1985–2025); source rights and complete annual agency rosters remain required. FBI unavailable access is excluded from available mappings.

NCES public download coverage starts at 2002 after historical HD probes failed; bounds are not inferred from modern outcome years. DK supplied coverage is an audit scope pending S1 certification.

## Final intake

30 distinct SPID IDs: 21 provisional available-connector target mappings, 19 with nonempty coverage intersections; 0 ready / 30 parked pending purpose and other evidence. Minimum-wage presence and above-federal-wage indicators share a family and must be reconciled before independent design counts. Removed explicit DUI, zero-tolerance and PBF aliases from candidate selection.
19 prior `_fo` cards are preserved and independently reassessed as v2: 2 provisional target mappings (colorectal screening and dependent insurance), with only dependent insurance having an intersection (2008 only). Screening has no legal/outcome overlap. CCB has a 2002–2006 window but cannot isolate authorized colleges, so remains unmapped. Other targets retain explicit connector/population gaps. No prior results or forecasts were read.
Emitted 49 S0 purpose jobs and 23 S1 V5 audit packets for all mapped cards. Purpose outputs are not substantive acceptance; the hub must accept/park, and any changed target/population requires a new card/version and rebound preparation. One-year/short windows do not confer cohort, pre-period or control support.
Only `data/l2_pipeline/jobs/run3/v6_final_verified20261008/` is dispatchable. Earlier task attempts remain preserved and disabled: schema/path preparation failures and code-bound superseded packets. See [RUN3_PLAN](../../docs/plans/l2-pipeline/RUN3_PLAN.md) for S0-first dispatch commands; [PLAN](../../data/l2_pipeline/cards/run3/_meta/v6_final_verified20261008/PLAN.json) and [DRY_RUN](../../data/l2_pipeline/cards/run3/_meta/v6_final_verified20261008/DRY_RUN.json) contain card/job bindings.

## Verification

Normal and `-O`: 339 tests passed, exit 0 in both modes, with one real archival-fit equivalence test deliberately excluded (`regression --no-archival-fits`); invented tests still execute. This avoids new real-data estimates. Final normal/optimized S0 plan dry-runs pass; hub dispatcher S0/S1 dry-runs check installed CLI flags with help only. No agent/model invocation, forecast, new study freeze, outcome association, commit, push, deployment, upload or release occurred.

## Public downloads

All 7 public research downloads are stored in the approved scratchpad; total 48,207,112 bytes. Schema/header/coverage inspection only; no policy-outcome associations. Every downloaded file is listed below.

| File | Source URL | Bytes | License / rights |
|---|---|---:|---|
| ca_dataset_metadata.json | [official source](https://data-openjustice.doj.ca.gov/jsonapi/node/dataset?include=field_source) | 286629 | CA DOJ official public portal metadata; no explicit reuse license located |
| ca_crime_1985_2025.csv | [official source](https://data-openjustice.doj.ca.gov/sites/default/files/dataset/2026-07/Crimes_and_Clearances_with_Arson-1985-2025.csv) | 6343304 | CA DOJ publicly downloadable statistics; explicit redistribution license not found, verify before freeze |
| FARS2011NationalCSV.zip | [official source](https://static.nhtsa.gov/nhtsa/downloads/FARS/2011/National/FARS2011NationalCSV.zip) | 14616200 | US federal government public domain; cite NHTSA |
| FARS1982NationalCSV.zip | [official source](https://static.nhtsa.gov/nhtsa/downloads/FARS/1982/National/FARS1982NationalCSV.zip) | 6349935 | US federal government public domain; cite NHTSA |
| FARS1985NationalCSV.zip | [official source](https://static.nhtsa.gov/nhtsa/downloads/FARS/1985/National/FARS1985NationalCSV.zip) | 6522889 | US federal government public domain; cite NHTSA |
| FARS2000NationalCSV.zip | [official source](https://static.nhtsa.gov/nhtsa/downloads/FARS/2000/National/FARS2000NationalCSV.zip) | 7543965 | US federal government public domain; cite NHTSA |
| FARS_1975_2022_manual.pdf | [official source](https://rosap.ntl.bts.gov/view/dot/78035/dot_78035_DS1.pdf) | 6544190 | US federal government public domain; cite NHTSA |

`detecting-indirect-prompt-injection` skill initial checks were applied to locally persisted CA metadata and extracted official manual text. Normalized heuristics found no suspicious patterns; LLM Guard was unavailable, so no ML scan is claimed. Browser/search-only sources were treated as untrusted data without claiming a separate scan. Scan results cannot confer authority or prove safety. The extracted manual TXT is derived from the listed PDF, not another download.

Full download hashes and source check telemetry are in the scratchpad `l2_v6_connector_research/download_inventory.json`. California DOJ reuse rights were not located; its catalog entry remains unavailable until those rights and agency coverage are reviewed.

Freeze verifiers at completion:

- `.venv_did/bin/python -B scripts/did_samples/freeze.py --verify`: exit **0**, `issues=[]`, `actual_data_estimation_calls=0`.
- `.venv_did/bin/python -B scripts/did_samples/cases/freeze_cases.py --verify --code-path scripts/did_samples/amendment5_audit.py --code-path scripts/did_samples/crosscheck/run_crosscheck.py`: exit **0**, `issues=[]`, `actual_data_estimation_calls=0`.

Final dispatcher dry-runs: S0 **49** commands, exit **0**; S1 **23** commands, exit **0**. Final optimized plan dry-run: exit **0**, `passed=true`, `issues=[]`. No dispatch reservation or model output was created.
