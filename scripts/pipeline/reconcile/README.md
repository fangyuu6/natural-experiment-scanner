# L2 pilot reconciliation

Run with `PYTHONDONTWRITEBYTECODE=1`; no external packages, estimation or outcome
inspection. `reconcile.py` compares immutable UTF-8 submissions. `finalize.py`
binds reviewed component findings, audit receipts and explicit identity crosswalks
to original rows. Inputs and human rulings remain separately hashed and retained.

```sh
PYTHONDONTWRITEBYTECODE=1 python3 scripts/pipeline/reconcile/finalize.py
PYTHONDONTWRITEBYTECODE=1 python3 scripts/pipeline/registry/review.py \
  --previous data/pipeline/coding/reconciled/registry.previous.json \
  --reviews data/pipeline/coding/reconciled/registry_reviews.json \
  --output data/pipeline/coding/reconciled/registry.json
PYTHONDONTWRITEBYTECODE=1 python3 scripts/pipeline/registry/validate.py \
  data/pipeline/coding/reconciled/registry.json \
  --previous data/pipeline/coding/reconciled/registry.previous.json
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover \
  -s scripts/pipeline/reconcile -p 'test_*.py' -v
```

`reviewer_decisions.json` is generated from the reviewed component ledgers; it
does not promote component findings to whole-field acceptance. In this packet
all complete histories remain unresolved. Changing a legal definition requires
a new manual/version and independent recoding, not editing either submission.

The first assigned unit/field occurrence is each template baseline. Additional
records remain one-sided unless an explicit reviewed identity crosswalk joins
them. Source record/version labels are preserved even where administrative
baseline fields align. Missing rows remain in the comparison union; neither
missing nor unknown implies non-treatment. Two Alaska decisions are joined by
official AO182 identity. No answer-based fuzzy matching or synonym reduction is
performed on free-form legal JSON.

Raw agreement uses status plus canonical value and legal coverage/precision
metadata. Object keys, unordered-set order, duplicate set members and finite
equivalent JSON numbers normalize. Quotes/confidence do not weight agreement.
Both unknown matches are reported separately. Known agreement requires matched
scoped values, addressable cited evidence and a human sufficiency attestation.
Source rechecks never increase initial agreement. `accepted_evidence` additionally
requires exact-quote, matched-version and all-material-component attestations for
the protocol's known-versus-not_stated route; other full-field third reads use
`accepted_rechecked`. These attestations are reviewed data, not automated legal
inferences. Component-only findings cannot resolve a whole field.

Free-form whole-field JSON has no repeated shared known categories in this pilot,
so legal-value kappa is not informative. Supplemental unweighted status kappa
uses paired rows only and is suppressed if either coder has a constant margin.
Agreement and common missingness use the full union denominator, with paired-only
rates also retained. Exactly20% unresolved does not pause; >20% does. Unresolved
includes both-unknown and one-sided records. Complete-field comparison is paused
for these six designs; verified components remain available for further coding.

`submission_validation.json` separates blank metadata/shared-minute allocations
from malformed JSON and missing assigned record/version IDs. Original B blanks
are preserved; a positive design-level stopwatch total does not validate every
field's allocation. The updated registry lives under the allowed reconciliation
output path; queue-v2 intake snapshots and fourteen other design dispositions
are retained unchanged.

Some adjudication cells retain all51 original certification rows. Python readers
of that CSV should set `csv.field_size_limit(16 * 1024 * 1024)`; ordinary defaults
can be too small for these intentionally complete original-value ledgers.
