# Host workflow and mechanical checks

Use `python -B -m scripts.l2_pipeline.hub` from the repository root. Preparation
and collection never launch models. Only `dispatch` launches an external wave;
it requires the existing host assertion and refuses sandbox execution.
`--max-concurrency` accepts **1..4**, default 4, on every wave.

```sh
python -B -m scripts.l2_pipeline.hub status --batch CARDS --work-dir WORK
python -B -m scripts.l2_pipeline.hub prepare S1 --batch CARDS --work-dir WORK --jobs-root JOBS
python -B -m scripts.l2_pipeline.hub dispatch S1 --jobs-root JOBS --preflight-only --max-concurrency 4
python -B -m scripts.l2_pipeline.hub dispatch S1 --jobs-root JOBS --hub-outside-sandbox --max-concurrency 4
python -B -m scripts.l2_pipeline.hub collect --jobs-root JOBS --stage S1
python -B -m scripts.l2_pipeline.hub approve-run --only-pass --batch CARDS --work-dir WORK --signer HUB --signed-at UTC_TIMESTAMP
python -B -m scripts.l2_pipeline.hub run S6 --batch CARDS --work-dir WORK
python -B -m scripts.l2_pipeline.hub run S7 --batch CARDS --work-dir WORK
```

`approve-run` signs final approvals only. It does not execute estimates. A sealed
candidate review must say `PASS`, contain an empty `must` list, and satisfy the
existing candidate hash, independent reviewer, sweep, fix-cycle, SHOULD-closure,
signer, UTC timestamp, and freeze checks. STOP, draft, absent, or stale reviews
retain their own dispositions. A bad card, output, reserved job, or execution
does not cancel another design. Existing once-only execution and retry rules
still apply. The hub's `run` calls each design separately; use it for batch
execution instead of the legacy CLI batch-abort path.

Contract fixtures live in `contract_tests/fixtures.json`. `packets.py` creates
invented packets with the production emitters; the harness checks each current
template's machine-readable schema and invokes the actual collector on golden
and invalid outputs in separate temporary directories. No outcome associations,
downloads or model calls are involved. Wave preflight runs the relevant job types
before any dispatch reservation and additionally checks the exact selected
template. Contract failures block the entire wave, while individual input or
reservation failures remain isolated.

CI and local checks (both modes are required):

```sh
.venv_did/bin/python -B -m scripts.l2_pipeline.agents.contract_tests
.venv_did/bin/python -B -O -m scripts.l2_pipeline.agents.contract_tests
.venv_did/bin/python -B -m scripts.l2_pipeline.regression --no-archival-fits
.venv_did/bin/python -B -O -m scripts.l2_pipeline.regression --no-archival-fits
```

The regression runner discovers the contract, hub, migration and data-code lint
tests automatically. `--no-archival-fits` retains invented statistical tests and
excludes the existing real archival fit. The task permits edits only within the
agent package, hub, tests and moved adapters, so no CI-provider configuration or
legacy stage/registration/CLI implementation is changed here.

Reusable run6 code is under `scripts/l2_pipeline/run6_adapters/`; originals in
`data/` remain byte-identical evidence. `MIGRATION.json` binds those originals;
`paths.MOVED_ADAPTERS` also records earlier run2/run4 relocations. The lint rejects
pipeline imports and executable loads from `data/`, including symlinks. The
existing freeze dependency closure binds the moved Python modules, the
contract fixture JSON and all 11 current templates. Each template version's
`dependencies.py` declares adjacent prompt resources in the static loader form
that the existing freeze scanner understands. Its guard remains fail-closed on
missing dependencies.
