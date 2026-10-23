# Prospective research-panel intake

Reusable code lives here; data directories hold source metadata, cards and jobs.
`build_intake` accepts an explicit attempt directory, seeds, design prefix and
catalog/evidence paths. It reads treatment histories and outcome catalogs only.
`v8b_calendar` prepares disjoint calibration and fresh audit samples under C11.
`fresh_gate` checks calibration confirmation, output hashes, V6.2 acceptance and
the same card hash before any fresh launch. Unaudited variables also require an
independently bound direction ruling. Preflight checks grant no acceptance.

Read-only checks against the existing C18 attempt:

```sh
.venv_did/bin/python -B -m scripts.l2_pipeline.intake.verify_intake --intake-root data/l2_pipeline/sources/research_panel/c18_20261010
.venv_did/bin/python -B -m scripts.l2_pipeline.intake.dispatch --intake-root data/l2_pipeline/sources/research_panel/c18_20261010 dispatch S0 --jobs-root data/l2_pipeline/sources/research_panel/c18_20261010/s0_jobs --preflight-only --hub-outside-sandbox --max-concurrency 4
.venv_did/bin/python -B -m scripts.l2_pipeline.intake.dispatch --intake-root data/l2_pipeline/sources/research_panel/c18_20261010 dispatch S1 --jobs-root data/l2_pipeline/sources/research_panel/c18_20261010/calibration_jobs --preflight-only --hub-outside-sandbox --max-concurrency 4
.venv_did/bin/python -B -m scripts.l2_pipeline.intake.dispatch --intake-root data/l2_pipeline/sources/research_panel/c18_20261010 dispatch S1 --jobs-root data/l2_pipeline/sources/research_panel/c18_20261010/fresh_audit_jobs --preflight-only --hub-outside-sandbox --max-concurrency 4
```

All three old/new preflight stdout hashes match:
`b2c2c2435673985e36735c951558a4388321888454c0c7dc38fd252225b1bb83`.
Both validator stdout hashes match:
`b3ccf030c634d7d87d6d74e711930def917089960920f1753d269613be09ff2d`.
The C19 migration report in the scratchpad binds 90 unchanged C18 artifacts,
including every original executable, cards, imports, packets and sidecars.
No model dispatch or outcome read was used for this comparison.

## Deferred cleanup

Do not edit the running C18 originals. Once its S0/calibration processes have
finished and their receipts have been collected, retire these original active
entry points: `build_intake.py`, `verify_intake.py`, `c18_v8b.py`, `dispatch.py`
under `data/l2_pipeline/sources/research_panel/c18_20261010/`.
Its `auditor_template.md` is copied here as well. Old packets still hash-bind
the original code and template: preserve their provenance bytes as archival
evidence before removal, and never silently rewrite those packets or receipts.
Prepare future attempts with the scripts modules. They bind their own scripts
code inventory, including the fresh gate and explicit configuration.

`legacy_data_code_paths.json` lists existing non-agent Python evidence by exact
path. The lint permits this snapshot and agent/reviewer workspaces; all new
Python files elsewhere under `data/l2_pipeline/` fail. Do not expand the snapshot
to accommodate new reusable code.

## Official V9 histories

`official_history` binds an independent V9 A/B reconciliation ledger, its locked
originals, and a completed collector-validated fixed-sample V3 audit. It parks
known binary or operation-year conflicts and retains unknown and interruption
exclusions. The annual engine receives operation-year labels, drops that year,
and shifts once; the audited first-full-year label is preserved separately.
The historical lifetime-certification flag stays unchanged.

Use the explicit entry for S1 so archived source-code hashes remain valid:

```sh
python -B -m scripts.l2_pipeline.intake.official_history run S1 --batch CARDS --work-dir WORK
```

The ordinary hub owns subsequent `advance --only-pass`, `prepare S5`, and
`dispatch S5 --preflight-only`. This input adapter grants no dispatch or fitting
authority. Its temporary route is restored even if the hub raises an exception.
