# Agent output contracts

Run the offline CI checks in both interpreter modes:

```sh
python3 -B -m unittest scripts.l2_pipeline.tests.test_agent_contract_harness
python3 -B -O -m unittest scripts.l2_pipeline.tests.test_agent_contract_harness
python3 -B -m scripts.l2_pipeline.agents.contract_tests
python3 -B -O -m scripts.l2_pipeline.agents.contract_tests
```

The harness emits production job packets into fresh temporary directories, checks
that each current template's machine-readable output shape accepts its golden
output, then runs the real collector. Each invalid case uses a new independent
packet; rejection cannot be an immutable promotion collision with an earlier
accepted output. There are no model calls, external downloads, or real outcomes.

`fixtures.json` contains golden outputs and named invalid mutations. `$field`
placeholders bind identities, hashes, rosters, and forecast-ledger metadata from
the emitted job. S0 deliberately omits optional blindness telemetry. S0L keeps
failed search attempts as structured query records with null result URLs.
Published-paper V7 variants keep the distinct project-blindness contract.
Both generic and embedded-ledger forecasts and draft/sealed review scopes are
covered. S1's original and reconciliation-followup auditor templates are checked.

`check_job_types(jobs)` is the fail-closed wave gate. It reruns the selected current
contracts on every wave and validates the exact templates of the actual jobs.
Any failed contract stops the wave before reservation or launch. Job hash/input
integrity remains the production `load_job` check. Machine-readable schemas
specify output shape; the collector additionally checks numeric values, exact
rosters, evidence bindings, scope, and independence. Unsupported schema keywords
fail rather than silently weakening the template check.

Adding a job contract requires a production fixture emitter, a golden output,
at least three independently rejected examples, and an explicit type classifier.
Declare its prompt resource in the corresponding template version's
`dependencies.py` so the existing code inventory also freezes template bytes.

`s1_official_history_v9` checks the official-history evidence contract separately
from generic coding: each state has a source id, high/medium confidence, an
official dated quote for a known start, V2_LIST checks for absence, or a named
binary conflict. Its invalid fixtures include all 50 states unknown without
named conflicts, secondary-only starts and missing per-state searches.
`test_history.py` also exercises absence and NEEDS_RULING; the full pipeline
suite imports these probes. Passing collection does not grant S1 readiness.
