# I13 preparation only

`python3 -B scripts/l3_ideas/I13/build_frame.py --build` constructs the metadata-only frame and samples once. It refuses an existing frame freeze. Do not delete a frozen manifest to regenerate a study; make a prospective version instead.

`python3 -B scripts/l3_ideas/I13/build_frame.py --verify` checks input/builder/artifact SHA-256 and byte sizes, independently regenerates all metadata artifacts in memory, and compares bytes. No files are written.

`python3 -B scripts/l3_ideas/I13/build_frame.py --check-prereg` runs frame verification, then checks a hub-owned HUB_FREEZE.json. Required fields: `status: "frozen"`, `approved_by: "hub"`, `approved_at_utc` and `sha256` mapping names `PROTOCOL.md`, `PILOT_PLAN.md`, `CODING_SCHEMA.json`, `FRAME_FREEZE.json`, `build_frame.py` to their exact file hashes. This task supplies no approval. A future coding/analysis runner must call this gate and additionally freeze its own code/inputs before computations.

Outputs live in docs/plans/l3-ideas/I13/: immutable descriptive frame, frozen confirmatory and nested pilot samples, answer-free coder lookup packet, marginal metadata count CSVs, and FRAME_FREEZE.json. No coding-result file, analysis association, outcome data or estimator is generated. Frame hints remain untrusted extraction data; the blank CODING_SCHEMA.json is a template for later official-source verification.

Use `prepare_coder_packet.py --verify` to verify the separately frozen pilot_target_packet.jsonl. It adds target variable/boundary/direction and address-field applicability, needed when one quoted passage has several thresholds. It changes no frame/sample bytes. `prepare_coder_packet.py --check-prereg` is the dispatch gate: it first runs the frame/protocol gate, then verifies the target packet and requires hub SHA-256 approval of CODER_PACKET_FREEZE.json and prepare_coder_packet.py as well. Neither gate authorizes analysis without a later frozen executor.

The 600-unit sample is stratified SRS without replacement. Taking the first k_h of each randomized stratum sample gives a nested pilot with unconditional inclusion probability k_h/N_h. The weight columns explicitly distinguish pilot and full-study inference. Domains/years are counted as metadata only, not crossed with future coding statuses.

The source input is read-only; all write locations are task-owned. New downloads: none. No external service or paid call is used by this stdlib-only script.
