# I01 phase 1

Python standard library only. Writes only `docs/plans/l3-ideas/I01/`; inputs are read-only. No network, LLM/API calls, credentials, estimation or deployment. The directory holds frame construction and an administrative hub-freeze gate; no outcome-analysis implementation exists yet.

```sh
python3 -B scripts/l3_ideas/I01/build_frame.py
python3 -B scripts/l3_ideas/I01/build_frame.py --verify --check-inputs
python3 -B scripts/l3_ideas/I01/freeze_protocol.py --verify
```

The first command already ran and refuses to overwrite any frame artifact. The second checks byte hashes plus independent reconstruction of allocations and sampled IDs. The third MUST fail before hub approval. Rebuilding a changed frame requires a separately named version, never overwriting a coded cohort.

`FRAME.csv` has one provisional parent ID, not one certified law. Queue memberships take all extraction versions; descriptive metadata come from the first source record (legacy takes precedence). Missing metadata, non-strict-enacted records, nonqueued records and other domains remain eligible for sampling. Metadata date ranges include historical placeholders; primary legal year/scope must be independently verified later.

Sample selection: deterministic SHA256 ranking of seed/stratum/ID represents seeded sampling without replacement. Allocation minima and largest-remainder rules are in code and protocol, with distinct pilot/main selection weights. No outcome/exposure code enters allocation. The main roster is drawn prospectively, not permission to begin main coding. `CODER_INPUT.csv` masks allocation, queue status, LLM family/domain labels and scores.

The hub alone invokes `freeze_protocol.py --freeze --approval-reference '<hub decision reference>'` after its substantive approval. This records approval provenance and exact file hashes, not cryptographic identity. Every future coding/analysis entry point must call `require_hub_freeze()` before reading coded values. Freeze code verifies original input hashes as well as the frame. It does not attest to coders' blindness or automatically validate legal sources.

`FRAME_MANIFEST.json` is the frozen sampling artifact; `HUB_FREEZE.json` is deliberately absent in this phase. Draft protocol edits remain possible before hub approval; the sampled IDs/probabilities remain locked before any coding. A new analytical version requires new approval and handling of previously coded pilot records as specified in PROTOCOL.

No downloaded files; public URL / size / license ledger is empty. Existing sanitized source locations are references, not newly downloaded data. Do not feed full frame/sample files to coders; only the answer-free input and approved manual/schema/source files.
