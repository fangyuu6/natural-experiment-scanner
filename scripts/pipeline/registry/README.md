# L1 → L2 registry contract v1

This registry tracks every queued design through verification, data readiness, freezing, execution and disposition. It does not estimate anything. `schema.json` is JSON Schema draft 2020-12; `validate.py` uses only Python's standard library and interprets all keywords used by this schema, then checks references, legal lineage, status history and artifact bytes. The contract is independent of the 13 frozen DiD prototypes.

## Identity and provenance

The normalized collections link `source_text_id → policy_family_id / legal_instrument_id → design_id → outcome_spec_id (outcome_id, spec_version) → run_id`.

- Preserve original L1 source IDs as strings. A source can have several chapter extraction records; preserve `source_record_id`, chapter ID, extraction source, extraction payload SHA-256 and locator. An extraction hash is distinct from a full-source-text hash; the latter stays null until archived source text exists. Archiving can fill a previously null full-text hash; later hash changes require a new source identity. Extraction evidence and domain/instrument relationships can append while prior values remain intact.
- Legal instruments can span several policy families. A family can have several domain labels. Legal IDs imported from queue v2 are provisional parent-policy mappings, with no claim that bill versions have already been collapsed into one enacted law. Official reconciliation promotes a provisional instrument to verified identity with a legal citation; material corrections to verified identity create a new ID.
- Each design points to one family, one domain, source texts and relevant instruments. DiD wave windows within a family are evidence for one screening design, not separate studies. Thresholds and allocation mechanisms receive their own design IDs. Independent verification can split a provisional family into new IDs without deleting its old disposition.
- An outcome/spec version binds the estimand, sample, clock, controls, weights, inference, diagnostics and stop rules. An outcome can have several versions; `supersedes` points to the previous version for the same design/outcome. All run records retain their precise version and frozen hash. The design's active version/run summarize its latest workflow; earlier versions and runs remain exportable.

## Lifecycle

`queued → verifying → data-ready → frozen → run → shown`; any of those stages can move to `stopped`. Each stage requires a reason. `stopped` requires an actionable reopen condition; it returns to `verifying` only with new evidence references. Stops count toward throughput. A run can be executing, completed, stopped or failed; terminal records require completion timestamps, and stopped/failed runs require reopen conditions.

Every design has an append-only event history with actor, UTC timestamp, previous/new state, reason, evidence locators and active outcome/spec/run references. Data-ready events retain immutable readiness attestations. A later source contradiction can stop/reopen a design with failed current checks while retaining its historical attestation. Current data-ready/frozen/run/shown records must still match their latest attestation.

`review.py --previous <snapshot> --reviews <review-list.json> --output <updated.json>`
appends verification reviews and validates against the prior snapshot before writing.
An evidenced `verifying → verifying` review records a new blocker or partial finding
without rewriting history or creating an artificial stop/reopen cycle. Other
same-stage transitions remain prohibited. Output may live with a reconciliation
packet when the task restricts writes to that packet; the imported snapshot is retained.

Common readiness checks certify legal identity, independent coding/reconciliation, free public outcome linkage, population alignment and confounding review. Additional checks are design-specific:

| Track | Required readiness checks |
|---|---|
| DiD | Treatment clock, exposure path, usable controls, clean preperiod, panel support |
| RDD | Official threshold, measured running variable, local support, enforcement, manipulation review, shared-threshold review |
| RCT/lottery | Actual allocation mechanism and assignment records, assignment probabilities, compliance review, attrition review |

DiD screening's state-wave rule is not an RDD/RCT readiness rule. No extracted dataset name or randomization word sets a verified check. Checklist booleans attest to linked human-reviewed evidence; they do not certify substantive legal or econometric validity by themselves. Unknowns remain in `readiness.missing`.

## Freeze and execution gates

A freeze bundle must contain seven artifact roles: treatment, outcome, spec, code, environment, predictions and independent verification. Artifacts carry relative paths and SHA-256 hashes; the bundle also hashes its canonical metadata, timestamp and actor. Paths must stay inside the declared artifact root, including symlinks. Credential/configuration paths are rejected before reading. `validate.py` refuses to validate frozen records without the artifact root and actual bytes.

The JSON spec artifact must equal `spec_payload(spec)`, including design/outcome/version identity and units. The run records the exact frozen-bundle hash and cannot start before freezing. Display requires a completed run and structured original-unit estimate, interval, reference, support and diagnostics. Completed runs have hashed result, diagnostics, reference and support artifacts; the result JSON must equal `result_payload(run)`, binding the summary to original bytes. Static export includes all statuses and runs, including negative estimates and stopped cases.

Use `--previous` against the last validated snapshot for every production update. It rejects deletions, historical rewrites, source/lineage rewrites, frozen-spec changes and edits to terminal runs. Reopening after a freeze requires a new outcome/spec version and a fresh freeze, including when the substantive specification is retained after an infrastructure correction. The prior run remains visible; an infrastructure retry gets a new run ID, with the failure and correction recorded. Outcome-driven sample changes or repeated attempts to obtain a desired result are not an admissible reopening rationale. Byte hashes are an integrity gate, not an external trusted timestamp or a substitute for reviewing stop/reopen reasons.

## Queue bridge and later site export

`import_queue.py` reads all three queue-v2 JSONL files. It preserves existing design/family/legal/source IDs and extracted-payload provenance, creates only queued records, leaves legal identity provisional, and leaves all readiness checks false. Optional repeated `--design-id` arguments select a fixed batch; absent that option, all designs enter the registry. Priority contains only quoted-field completeness, a free-data alias indicator and domain priority. Alias matches remain unverified. No L1 policy-significance score, expected sign or estimated significance enters priority.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/pipeline/registry/import_queue.py \
  --queue-dir data/pipeline/queue_v2 --output data/pipeline/queue_v2/registry.json
PYTHONDONTWRITEBYTECODE=1 python3 scripts/pipeline/registry/validate.py \
  data/pipeline/queue_v2/registry.json
PYTHONDONTWRITEBYTECODE=1 python3 scripts/pipeline/registry/export_static.py \
  data/pipeline/queue_v2/registry.json --output data/pipeline/queue_v2/site_registry.json
# Later updates with frozen records:
PYTHONDONTWRITEBYTECODE=1 python3 scripts/pipeline/registry/validate.py \
  data/pipeline/queue_v2/registry.json --artifact-root data/pipeline/queue_v2/artifacts \
  --previous data/pipeline/queue_v2/registry.previous.json
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/pipeline/registry -p 'test_*.py' -v
```

The exporter sorts by stable ID and reports source-text, family, instrument, design, version and run counts plus domain/track/status counts. It generates static JSON for a later build to inline, escaping `<` and Unicode line separators for safe `application/json` embedding. The site's worker blocks `.json` URLs, so this is build input. No site files, release output or frozen prototype files are changed. Registry JSON and artifact outputs belong under the ignored `data/pipeline/queue_v2/` directory; do not commit them. Tests use entirely synthetic fixtures and perform no estimation or data downloads.
