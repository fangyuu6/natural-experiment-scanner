# I09 phase 1

`build_frame.py` reads existing queue metadata and membership only, deduplicates parent IDs, records descriptive counts and draws 180 pilot parents. It performs no coding, ancestry construction or association calculation. It refuses to replace an existing frame. The frame and all outputs live in `docs/plans/l3-ideas/I09/`.

```sh
python3 -B scripts/l3_ideas/I09/build_frame.py --verify
python3 -B scripts/l3_ideas/I09/check_gate.py
```

`prepare_audit.py` has already drawn the 72-unit audit, seed 2026100610; it also refuses replacement. `build_frame.py --verify` validates input/output hashes, pilot size, uniqueness, allocations and inclusion probabilities. Hash bindings in `FRAME_MANIFEST.json` cover the builder; preserve its bytes after drawing.

The absent `HUB_FREEZE.json` intentionally makes the gate exit 2. The hub, after approving the concrete protocol, supplies `status: FROZEN`, `approved_by: hub`, `frozen_at_utc`, and a `sha256` mapping for every path in `check_gate.py:REQUIRED`. Frame and audit manifests transitively bind the draw artifacts. No local script creates that approval. A successful gate permits pilot coding only; association execution is not implemented or authorized here.

Future main analysis requires the separate sample/graph-plan/code/manual freeze and locked-input execution bundle in PROTOCOL.md. No output values, effects or model are fabricated as preparation. Amendments preserve old versions, and pilot labels stay outside confirmatory analysis.
