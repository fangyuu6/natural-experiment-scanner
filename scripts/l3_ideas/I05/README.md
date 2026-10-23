# I05 phase-1 metadata tools

`python3 -B scripts/l3_ideas/I05/build_frame.py` creates the frame/draw once. It refuses to overwrite an existing FRAME_MANIFEST.json. No beneficiary/study/outcome fields are exported; only IDs, source metadata and queue membership enter selection.

`python3 -B scripts/l3_ideas/I05/build_frame.py --verify` validates input/artifact hashes, frame membership, uniqueness, seeded main/pilot reproduction and main sampling probabilities.

`python3 -B scripts/l3_ideas/I05/freeze_gate.py` verifies a separately supplied hub approval. Before approval, exit2 and BLOCKED are expected. This tool never writes an approval or runs coding/analysis.

For approval, hub creates `docs/plans/l3-ideas/I05/HUB_FREEZE.json` with:

```json
{
  "status": "APPROVED_FOR_CODING",
  "approved_by": "actual hub reviewer",
  "approved_utc": "actual approval timestamp with UTC timezone",
  "no_headline_associations_examined": true,
  "sha256": {"every path shown by freeze_gate.py": "its exact displayed hash"}
}
```

The draft label inside the protocol is superseded by the matching approval, without editing approved bytes. Frame inputs remain locked; verify them again immediately before coding. Full association analysis requires the additional inventory/audit/support gates in PROTOCOL.md; no estimator is provided in phase1.

Sampling uses one metadata-parent record per candidate instrument, source/domain/queue strata, minimum allocation plus sqrt(N) largest remainders, then independent hash-derived seeded SRS streams. Core pilot is nested; unconditional pilot probabilities concern the original frame. Coverage-only checks use a separate stream and have no primary-analysis role.

If metadata identity proves multiple parent IDs are one operative law, inference stops for this version. Hub must freeze a complete canonical frame and a fresh draw before comparisons. The process pilot is preserved; unknowns, difficult laws and unevaluated laws are never replaced.

Phase1 downloads: none. No paid calls, credential access, estimates, site builds, releases or git mutations.
