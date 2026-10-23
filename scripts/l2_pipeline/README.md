# L2 reusable pipeline

The package serves all suitable L1 designs, preserving parked and stopped designs for L3. Batches 1–3 keep their frozen code. The numerical engine is a standalone copy-and-generalize implementation; the only real fit performed by its tests replays an already-frozen batch2 primary result in memory.

Run with the existing numerical environment; JSON intake/validation also works with ordinary Python:

```sh
python -m scripts.l2_pipeline validate path/to/card.json
python -m scripts.l2_pipeline intake --queue data/pipeline/queue_v2 --defaults path/to/defaults.json --output /private/tmp/l2/cards
python -m scripts.l2_pipeline run --stage S0 --batch /private/tmp/l2/cards --work-dir /private/tmp/l2/work
python -m scripts.l2_pipeline run --stage S2 --design path/to/card.json --work-dir /private/tmp/l2/work
python -m scripts.l2_pipeline connectors
.venv_did/bin/python -B -m unittest discover -s scripts/l2_pipeline/tests -v
```

`--design` accepts one card; `--batch` reads every top-level JSON/YAML card in sorted order and reports invalid cards alongside valid ones. YAML needs optional PyYAML, which this task does not install. Default output is `scripts/l2_pipeline/.work`; `--work-dir` also accepts `data/l2_pipeline` and scratch directories. Other repository output paths are refused. Exit codes: 0 all ready, 2 any parked/stopped, 1 validation/operational failure. Prior READY receipts and artifact hashes are checked before each selected stage; selecting S2 alone cannot certify treatment readiness.

Agent preparation now writes versioned, self-contained Markdown packets and hash-bound sidecars under `data/l2_pipeline/jobs/<stage>/<design>/<role>`. Stages never launch a subprocess. S1 coders use separate output directories; the auditor job becomes available after originals and the ten-unit sample are locked. S5 emits five blind persona jobs that write new `*.filled.json` ledgers; the collector preserves those originals and records any normalization. S6 emits one complete reviewer sweep, using the MUST definition in `PRE_RUN_REVIEW_5.md`. Role blindness is an instruction and attestation contract: the required repository working directory does not create an OS read barrier.

S1 preparation first fetches and archives dated complete-list sources without an agent. `source-packs --batch <cards> --candidates <reviewed-config> --work-dir <fresh-work> --jobs-root <jobs> --attempt-id attempt3 --output <report>` runs S0 and prepares S1, parking cards whose actual source bytes do not support the declared list/date/scope. The config maps design IDs to source declarations with URL, license, as-of/coverage dates, predicate, universe, history type, and archive-verifiable text anchors. Existing public bytes can be reused with `path` and `sha256`. Use a Python environment with an installed PDF reader or local Poppler; unavailable extraction parks the source, and `--design-id` can prepare a repaired subset in a fresh workspace without changing old packets. Each raw archive and extracted text has URL/size/rights/SHA provenance and an initial injection-check record; ML/OCR limitations are explicit.

Attempt3 repairs inadequate attempt2 collection rather than retrying a model call: original jobs/submissions remain hash-bound and preserved, while a fresh work directory receives new A/B outputs. Coders must use the pack and web search for official starts; never-treated status remains bounded by complete-list history. The collector rejects known-status coverage below 50% as `INSUFFICIENT_WORK`, with no promotion, unless every unknown has a distinct unit-specific official-source blocker and the pack has a documented history/universe gap. New pack jobs require specific blockers for every unknown at any coverage level. Host dispatch records wall time; old artifact-time estimates are labeled. Source availability can release coding jobs while historical completeness still blocks S1 readiness and downstream estimation.

The host dispatcher must run outside the pipeline sandbox. It limits concurrency to four, writes exclusive per-job attempt markers/logs and performs zero retries. Previewing commands makes no Codex call:

```sh
python3 scripts/l2_pipeline/agents/dispatch.py --jobs-root data/l2_pipeline/jobs --stage S5 --stage S6 --max-concurrency 4 --dry-run
# Hub only, outside its sandbox:
python3 scripts/l2_pipeline/agents/dispatch.py --jobs-root data/l2_pipeline/jobs --stage S5 --stage S6 --max-concurrency 4 --hub-outside-sandbox
python3 -m scripts.l2_pipeline collect --job data/l2_pipeline/jobs/S5/pay_secrecy/Card.md
```

`python3 -m scripts.l2_pipeline jobs --stage S6 --design <card> --work-dir <workspace> --allow-draft` emits a preparation review when no final seal exists. Its collector produces `REVIEW_DRAFT.json`, which cannot satisfy final execution review. A sealed review requires a separately generated candidate-bound job. Collected forecasts and original/normalization bytes are locked before preparatory approval; approvals and calibration remain external gated steps.

Batch4 archival acceptance is reproducible with `python3 -m scripts.l2_pipeline.acceptance --cards-dir <fresh-cards> --work-dir <fresh-work>`. It validates historical S1 locks/audits, imports their provenance explicitly, and runs S2/S3 without decoding numeric outcome tails. It reproduces all 124 panel/metadata variants and five unsupported variants, then verifies the original hash-bound specification semantics read-only. It generates no new S4 specification and grants no prospective first-order readiness; use fresh directories to preserve older acceptance records. The annual full-year adapter preserves actual clean exposure, never/not-yet controls, anchor geometry and the registered Webb→CT order.

QCEW annual state totals and explicitly declared complete county rosters, ACS1 detailed state tables and LAUS official NSA annual unemployment rates are registered. `connectors.download_plan(request)` returns key-free official download plans; `fetch_public(plan, path)` downloads one file without retries and returns URL, bytes, SHA256 and license. Network never runs as a side effect of panel construction. ACS1 public table files support 2022 onward; keyless API JSON can also be parsed, standard 2020 parks, and pre-2022 public sequence downloads need another adapter. No API key is requested.

Contracts are in `contracts.py`; `design_card.schema.json` and `cards.validate_card` validate the same documented schema subset. Card IDs/version are safe directory components. A card requires policy family, a single mechanism, DiD/RDD/RCT, unit, outcome family, source provenance, L1 lineage, population, outcome/treatment/analysis objects and a sensitivity list. See `examples/design.json`. Intake needs answer-free design defaults; it never invents mechanisms from sampled database dates. Non-state cards remain valid and park at the state-panel adapter boundary.

New S0 intake and S4 specifications pass the hard `first_order_outcome_v1` gate. `first_order` names `target_outcome` from located `statute_refs` and `literature_refs` (each `{source_ref, locator}`), `primary_outcome`, `relation` (`direct` or `closest_proxy`), `availability` (`available` or `unavailable`), and an explicit `secondary_outcomes` list. The primary binds `outcome.measure_id` and the connector's supported measurement; a proxy needs a one-line `proxy_justification`. Employment cannot substitute for a non-labor target. A missing free measurement connector parks with the exact reason `first-order outcome unavailable`. Generic imported panels also need `source_measure_id` and located `measurement_refs`. These are purpose and availability declarations; the gate opens no outcome data. S0/S4 retain hash-bound gate reports; SPEC preserves first-order evidence and registered secondary outcomes.

Archival cards without this field remain schema-readable, and completed/frozen results retain their registered outcomes. They receive no automatic new S0/S4 passage. A new first-order study uses a new `_fo` design ID, discloses the previous second-order registration without its result values, and requires fresh forecasts, freezes and guarded approval. Explicit downstream secondaries belong in `first_order.secondary_outcomes` as `{outcome, role: "secondary", connector}`; specification generation retains that registration.

Implemented stage paths:

- S0 queue-v2 adapter, JSON schema/semantic validator, immutable card receipt and first-order target/connector gate.
- S1 versioned host-dispatched coder/auditor job files for isolated A/B directories; original locks, tuple reconciliation, seeded treated-only V3 sampling and official-audit checks. No agent command or fetcher is executed. Supply the declared archived complete-list source, two `agents/{A,B}/submission.json` records, then `OFFICIAL_AUDIT.json`. Audit selection is locked before the latter is consumed. Identical preparation artifacts resume; changed locked answers need a new version. Known conflicts/missing values park for an explicit third-read/completion adapter.
- S2 local public-file connectors and standardized panel+metadata artifacts. Every sensitivity deep-merges its card changes and uses the same builder as primary. Previously completed variants resume only after card, source and output hash checks. Unsupported populations/connectors park. See `connectors.connector_inventory()` for implemented parsers and gaps.
- S3 annual never-treated metadata topology, declared thresholds, availability/linkage and exact panel bindings; variants use their own clock shifts/exclusions and analysis thresholds. The annual full-year adapter additionally certifies actual exposure, not-yet controls and exact masks; nonannual orchestration still parks. No outcome value is decoded.
- S4 repeats the first-order gate before any spec, retains registered secondaries, and checks explicit DiD spec fields and sensitivity support inventory; RDD/RCT are named stubs. The card-to-numerical declaration uses the same effective clock/scope routing as metadata support and retains actual_clean_onset.
- S5 blind persona jobs, original filled-file/normalization forecast locks, two-stage manifests, environment and complete code inventory. Preparatory/final signatures are supplied by the named hub; no function auto-signs. The shared engine exposes pre-adoption anchoring, synthetic calibration, Webb-first registered fallback and fixed feasible RI, but S5 does not launch these studies.
- S6 one-sweep candidate review and exact approval hooks. `execute_once` checks both freezes before loading, checks the freeze-bound canonical study root and reserves it once, writes results exclusively and retains unexpected failures without retry. Final approval also binds the exact candidate-review bytes. CLI S6 invokes the full registered annual DiD adapter after the final gate: primary, standalone companions, state/cohort omissions, endpoint agreement, diagnostics, fixed RI/noise reference and locked forecast scoring. Unsupported variants retain their dispositions; unexpected failures retain partial results/tracebacks and halt the batch with remaining designs NOT_RUN.
- S7 terminal local export fragments for registry, Estimates and L3. `registry.update_snapshot` validates append-only updates with the existing registry validator/exporter. An optional `automation.registry_update_input` envelope supplies previous snapshot, additions and updates; only a new local snapshot is written. Final site inlining/release remains downstream.

Numerical API: `engine.runner.fit_primary(declaration, rows, interval_evaluation_path=...)` returns the primary payload, topology and outcomes. `engine.panels.PanelBinding` binds source, complete replacement and metadata by hashes; overlays retain historical rows via one shared merge. `engine.ri.permutation_reference` predraws and retains infeasible labels without redraw (minimum 90%). `engine.noise.noise_summary` uses the feasible denominator. `engine/calibration.py` anchors only strictly pre-adoption rows and evaluates invented worlds. `engine/README.md` gives the full API and `LINEAGE.json` identifies copied source bytes.

No real-study execution is authorized by a valid card, a ready preparation receipt or an unsigned final manifest. The pipeline does not supply treatment-source fetchers, automatic signatures, dedicated RDD/RCT estimators or a published page.

S6 final inputs require one preparatory-bound `interval_plan` artifact: `{"schema":"l2_interval_plan_v1","expected_designs":["design_id", "other_eligible_design"],"candidate_order":["webb_wcu_t","control_path_ct"],"certification_path":"INTERVAL_CERTIFICATION.json"}`. Its authoritative inventory is fixed before calibration. The final manifest must bind that certification, every variant `panels/<name>/{panel,metadata}.csv`, `PANELS.json`, `SUPPORT.json`, `TREATMENT.json`, `SPEC.json`, and the original forecast/collection ledger bytes. `CALIBRATION_GATES.json` contains literal `passed:true` and `cases` for the same inventory, each a production `calibrate_synthetic` record with design/operator hashes, registered counts/seeds and recomputed held-out targets. A valid failed interval certification is retained and yields point plus RI; a selected-method flag cannot certify an interval. No real run is launched by this build.

`registered.load_registration` reconstructs zero-valued metadata designs and checks exact cells, lead/calibration/interval bindings before its values API. `run_registered` uses B4999 (omissions B999), RI999 with fixed seed/no redraw, and scores each locked forecast against the same original-unit primary. S7 exports completed, stopped and failed terminal records. Attempt-specific S5/S6 jobs retain earlier originals and STOP reviews; use a fresh narrow jobs root to dispatch only a repair batch.

The CLI now owns the non-agent S5 acceptance path. `registration --action
lock|prep|approve-prep|calibrate|seal|approve-final|report` accepts the same
`--design`/`--batch` and explicit `--work-dir`. Run `check --output <path>`
first to retain normal and optimized regression evidence. `prep` requires
`--test-report`, `--review-attempt` (the collected draft PASS), and `--signer`;
its write-once `REGISTRATION.json` records hub authority without changing the
scientific card. Both approval actions require that exact `--signer` and an
explicit UTC `--signed-at`; final approval additionally requires collected
candidate-bound PASS bytes. `seal` uses the preparatory-bound batch inventory,
retains failed interval candidates for the registered point-plus-RI fallback,
produces an unsigned template and writes a READY S5 receipt. Calibration uses
the production counts, the common clean pre-adoption rectangle, and metadata
RI999 feasibility without decoding treated-post outcomes. Each command retains
wall-clock evidence under the workspace's `throughput/`.

Sealed review preparation accepts `jobs --stage S6 --jobs-root <narrow-root>
--attempt-id <fresh-id>`. Packets bind both seals, preparatory approval, complete
calibration/RI/test evidence and the unsigned final template. Interval world
records are hash-bound on disk and permitted for review; the inline packet
contains their summaries rather than duplicating 1000 records per design.
Neither `registration` nor `jobs` dispatches agents or performs a real study.

`hub autopilot` is the standing-authorized host scheduler. `autopilot --dry-run`
plans without launching jobs or running stages; `autopilot tick
--hub-outside-sandbox` chains ready stages until durable agent/local-worker waits.
The default GLOBAL Codex budget is 10 (`--max-workers`); all existing uncompleted
dispatch reservations count, including jobs outside `ACTIVE_DESIGNS.json`.
One repo-wide scheduler lock and a cooperating dispatch reservation cap prevent
multiple scheduler waves. Calibration and the once-only run use at most two
separate Python workers (`--max-local-workers`), so they do not block collection.
Consumed/ambiguous launch and execution slots never retry. The ledger, queue,
action receipts, worker claims and terminal append receipts live in the repo.
Existing finished packages are read-only. A MUST, rejected acceptance, unresolved
SHOULD, failed run or exception stops only that design in `HUB_QUEUE.json`.
`autopilot ingest <repo-cards-dir>` validates the existing card schema and rejects
terminal or prepared/in-flight IDs. The C40 generic V9/FX10 router is the default;
annual year bounds are an answer-free calendar projection. An optional
`automation.autopilot` object can supply `intake_root`, `audit_seed`, `prior_jobs`
or `route: "V8b"`. V8b requires an existing hash-bound accepted direction/purpose
route and the unchanged fresh gate. Autopilot never invents missing scientific
declarations for legacy intake cards. Exact dry-plan, tick, nohup/time-limit and
stop-file commands are in `docs/plans/l2-pipeline/C41_AUTOPILOT.md`.

V6 intake ranks exact connector target mappings before the existing history and
base priority. `coverage.coverage_bounded_card` preserves requested windows and
bounds primary/sensitivity periods by legal audit scope and connector coverage;
unknown/empty intersections stay parked. S0 and S4 check these bounds, and S2
refuses unbounded panels. Existing local-panel cards also cannot exceed their
declared legal coverage. Source scope remains pending S1 legal certification.

`jobs --stage S0 --design <card> --work-dir <work> --jobs-root <jobs>` emits a
purpose-verifier packet with the internal domain lens, target/population leads
and a 2–3-citation output contract. `collect --job <S0-job>` validates fields and
citations. `agents.accept_purpose_verification(ctx, 'accept'|'park', reason)`
records the hub ruling and returns a card patch; changed scientific declarations
require a new card version, remapping and fresh preparation. Collection grants
no substantive acceptance. `run3_plan --action prepare|dry-run` uses a fresh
immutable attempt root for 30 candidates plus the 19 first-order reassessments.
The dispatch order and exact current commands are in `docs/plans/l2-pipeline/RUN3_PLAN.md`.

C28 code snapshots: new prep and final manifests bind `code_snapshot`, a
content-addressed object store plus an exact import tree under
`code_snapshots/<inventory SHA256>/`. Every code/resource byte in the dependency
inventory is copied and verified; final code must match preparatory code. S6
packets permit snapshot paths, including the bound reviewer template. New draft
packets also retain snapshot bytes. Changes to the live tree after a freeze do
not change its verification or execution. Object/tree tampering and extra files
are refused before a worker starts. Verification, approvals, calibration and the
registered S6 path use isolated `-I -B` workers; live callbacks cannot execute a
snapshot-bound study. Legacy manifests retain their original strict live-byte
checks and are never rewritten. Only one task may edit shared pipeline code at
a time. Historical draft collection retains findings only; it does not authorize
carrying an old independent code review forward to changed code. A changed-code
study requires a fresh independent draft packet before new prep approval.

C32 adds a root-local content-addressed `forecast_snapshot` to new manifests.
Absolute collection paths remain provenance; execution resolves originals only
from persona-specific locked paths/hashes and verified snapshot objects. Old
manifests and their terminal failures remain immutable. After a fresh draft
PASS, prep approval, calibration and final seal, run `registration --action
dry-load` with the same card/workspace before `approve-final`. The isolated
worker invokes `load_registration` and every supported variant's production
`load_bound_panel`, including omissions and companions. It never invokes an
estimator, diagnostic, RI/noise generator or forecast scorer, reserves no run
slot and emits no outcome values. `DRY_LOAD.json` binds the candidate and code
snapshot; final signature refuses a missing/stale/failed pass and binds its
exact SHA. New final execution verification also checks that binding. A
geometry repair requires regenerated S1/S3/S4 artifacts and the same new draft
review path. Forecast reuse requires exact `primary_forecast_target` equality;
otherwise supersede the lock and prepare new S5 jobs.

### C55 external executor and election grid

`hub autopilot` defaults to 6 Codex workers and `--agent-backends mixed`. SHA-256 of
`design_id + NUL + role` modulo 3 sends two residues to `sonnet_queue`, one to
`codex`; `--role-backend ROLE=BACKEND` overrides a role. Existing Codex reservations
and launch claims keep their executor. Sonnet packets and worker instructions live
in `data/l2_pipeline/registry/SONNET_QUEUE`; the hub launches external workers.
`hub autopilot sonnet-worker-instructions` prints the same generic worker protocol.
Atomic claims, token-bound heartbeats and 90-minute leases permit one expiry requeue;
a second expiry independently stops that design in HUB_QUEUE. Queue workers consume
no Codex slots. Submission collection and blindness/evidence contracts are shared.

Biennial turnout uses `biennial_even_year_v1`: legal calendar clocks project onto
the even election grid; event time, support, placebo and fixed RI labels use grid
indices. An operation year is dropped only when it is an observed election year.
Definition-changing series breaks bound windows in catalog metadata; opioid
dispensing uses the 2006–2018 pharmacy-location definition under C55.
C55 retains AUTOPILOT_STOP; its restart script is prepared without execution.
