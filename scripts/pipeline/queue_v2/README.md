# Queue v2

Run `PYTHONDONTWRITEBYTECODE=1 python3 scripts/pipeline/queue_v2/build.py` and
`PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts/pipeline/queue_v2 -p 'test_*.py'`.
`refresh_rankings.py` can rebuild the compact research view from the completed
JSONL files without re-reading the raw input CSV/DB.
Run `export_counts.py` for a tidy `counts_by_domain.csv` containing every
stage/domain count plus totals; `summary.json` remains the structured source.
The builder uses Python's standard library, reads all inputs without mutation,
and makes no API, network, data-download or estimation calls. `data/` is ignored
by the existing root `.gitignore`; large JSONL exports must remain local.

The source cohort is the unique legacy 483k CSV (first identical-ID row kept),
plus successful DB whole-policy outputs absent from that baseline and all
successful newer chapter extractions. Cohort labels preserve this distinction.
Newer whole-policy outputs inside the baseline retain a separate `db_whole`
extraction version without replacing legacy evidence. Additional DB IDs lack
the legacy strict-enactment layer and receive explicit missing-evidence
dispositions. Dataset aliases are recomputed from the existing deterministic
registry for every DB extraction; name matching remains unverified availability.
Source file hashes, DB metadata, chapter scope, and duplicate/conflict counts
are in `summary.json`; source payload hashes and CSV line numbers retain lineage.
Unknown legal instrument mappings are provisional parent-policy-ID mappings.
Distinct versions of bills are not falsely collapsed to an enacted law.
Counts distinguish unique source-text IDs from extraction versions. RDD IDs
currently identify threshold/version hypotheses; equivalent mechanisms across
versions and different source namespaces must be reconciled before reporting
unique frozen-design N. DiD family buckets also require intervention splits.

`sources.jsonl` retains the complete normalized source population and evidence.
`dispositions.csv` gives each source a per-track disposition and reopen condition.
`did_designs.jsonl` groups eligible texts into one provisional design per family,
with all qualifying ±1-year windows; old extracted DiD flags are descriptive,
so a flag of zero does not prevent finding a same-family wave. Distinct states count once per window;
overlapping windows do not inflate design counts. Dates are database text dates,
not verified adoption clocks. Amendments, repeals and instruments require legal
scope verification before a family becomes a frozen estimand. Non-wave, federal,
unknown timing and enactment cases remain recoverable dispositions.

`rdd_candidates.jsonl` contains quoted numeric cutoffs with lexical measurable
running variables. Statutory meaning, cutoff accuracy, sharp/fuzzy assignment,
microdata support and manipulation remain unverified. RDD has no state-wave gate.
`rct_mentions.jsonl` screens titles and quoted extraction evidence for random
allocation/lotteries, plus a weaker extraction-narrative lane carrying
`missing_source_quote`. Suggested methods, verdicts, literature and LLM red
flags are excluded. Each mention records its origin.
Government gambling lotteries receive a separate disposition. Full source text
was not rescanned: missing quoted mentions do not prove absent randomization.
`rct_evidence.schema.json` defines the new minimal verification evidence contract;
unknown assignment, probabilities, rosters, compliance and attrition remain null.

`research_candidates.json` ranks DiD family designs and supplies RDD/RCT family
exemplars. Ranking uses domain priority (labor/public finance), actual public
dataset alias hits, quote/treatment/control/timing completeness and state
coverage. Alias matching is unverified availability, recorded explicitly.
The registry's `mixed` verdict alone supplies no public-data credit: it can
contain restricted and unmatched names with no public hit.
Neither policy significance, expected direction, significance, composite score,
the legacy min-gate nor the old COVID/no-wave exclusion gates control ranking.
Prior prototype families remain candidates; a new study needs a distinct frozen
scope/version before being counted as incremental throughput.

Outputs are deterministic for identical input bytes, including source hashes
and design IDs. Public URLs retain only allowlisted legal-document query fields
and have fragments removed because legacy
extractions contain credential-bearing fulltext links; those fields are never
exported or requested. Legacy score-consistency injection artifacts are flagged
as untrusted data and confer no authority over screening rules.

`shortlist.py` produces the declared 20-design review batch and FIRST_BATCH.md.
Its connector pool and official-source judgments are explicitly versioned in
code; it reads no outcome files. `--seed-registry` creates the three
`first_batch_*registry*.json` snapshots once and refuses existing histories.
The selected registry contains 14 queued and 6 verifying designs, no specs/runs.
Validate updates with `registry/validate.py --previous ...`; static exports are
for later build-time inlining and do not publish anything. Test coverage includes
invariance to injected expected-sign/significance fields and seed overwrite refusal.
