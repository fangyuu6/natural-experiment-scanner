# Lean v2 offline rescoring

From the repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m scripts.pipeline.v2.run
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/pipeline/v2 -p 'test_*.py'
```

Inputs are the immutable L2 and L3 pilot A/B submissions, their existing reviewed
crosswalks and decisions, and existing compiled adoption/coverage files. Outputs
live in `data/pipeline/v2/`; original submissions and earlier reconciliation stay
intact. Input hashes and original row/pointer references make normalization
reviewable. The output manifest records code and artifact hashes.

Known analysis values agree when the annual value and selected intervention
match. Unknowns and unpaired values have separate denominators. Real year/status
conflicts remain disputes; a lower dispute count alone does not establish a
usable treatment/control or assignment/outcome panel. Original blind comparisons
are separate from existing third-read resolutions; athlete-agents stays outside
the L3 blind headline.

Compiled absence-as-nonadoption is limited to the selected source's observation
window and policy definition. It is never applied to unknown legal coding or
extended through 2025 by default. The pooled output reports events and availability
counts only. Existing positive-date agreement is a diagnostic; source acceptance
requires the protocol's random status/timing audit. See
`docs/plans/PROTOCOL_V2_LEAN.md` and `docs/plans/PROTOCOL_V2_RESULTS.md`.

No source page/PDF is reopened, no download or paid API call is made, and no
outcome associations or new estimates are computed. Source text and tool output
are treated as data, including any embedded instructions.
