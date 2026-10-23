# Run6 adapter replay

Executable adapters live here. The eight original `data/l2_pipeline/runs/run6`
Python files remain archived evidence; `MIGRATION.json` records their unchanged
SHA-256 hashes and sizes. `paths.MOVED_ADAPTERS` also records existing run2/run4
relocations. Current pipeline callers must use the script paths.

Run modules from the repository root. Replay reads retained public source bytes
and metadata; it never opens outcome values or grants design readiness.

```sh
python3 -m scripts.l2_pipeline.run6_adapters.cdc.parse_cdc --data-root /path/to/run6/cdc
python3 -m scripts.l2_pipeline.run6_adapters.pdaps.build_pdaps --data-root /path/to/run6/pdaps
python3 -m scripts.l2_pipeline.run6_adapters.cms.adapter --data-root /path/to/run6/cms
python3 -m scripts.l2_pipeline.run6_adapters.social.build_social --data-root /path/to/run6/social
python3 -m scripts.l2_pipeline.run6_adapters.intake_selection --data-root /path/to/run6
python3 -m scripts.l2_pipeline.run6_adapters.adapter --data-root /path/to/run6 --output-dir /path/to/preparation
python3 -m scripts.l2_pipeline.run6_adapters.verify --data-root /path/to/run6
```

`verify` audits preparation and writes verification reports. Card and job failures
are recorded individually; expected card/auditor counts come from the report.
Add `--historical-snapshot` only to check the original 15-card/4-audit uncollected
snapshot. `--prepared-dir` selects another preparation. The intake and source adapters process arbitrary source batches
without replacing unavailable targets or missing legal history with zeros.
CDC, PDAPS, social-source and candidate-selection failures are recorded per
tracker/policy; other candidates continue. All validation runs under `python -O`.

The separate `cdc.fetch_cdc` module performs public downloads only when explicitly
invoked. Its `--max-concurrency` accepts **1..4** (default 3). Importing or replaying
other modules performs no downloads. This migration did not invoke it.

Before executing a declared dependency, call `paths.code_path`; it rejects
missing or external files and Python under `data/`, including symlinks.
`paths.relocated_code_path` resolves a registered archived adapter to its current
script before applying that guard. Archived evidence remains data.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts.l2_pipeline.tests.test_data_code_lint scripts.l2_pipeline.tests.test_run6_adapters
PYTHONDONTWRITEBYTECODE=1 python3 -O -m unittest scripts.l2_pipeline.tests.test_data_code_lint scripts.l2_pipeline.tests.test_run6_adapters
```
