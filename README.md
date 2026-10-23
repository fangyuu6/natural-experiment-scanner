# Natural Experiment Scanner

Code and result tables behind [natural-experiment-scanner.com](https://natural-experiment-scanner.com), a one-person research project on US state policy.
It has three levels:

| Level | What it does | Where |
|---|---|---|
| **L1 Screening** | Reads US federal and state policy text and flags natural-experiment structure (staggered adoption for difference-in-differences, eligibility cutoffs for regression discontinuity), then keeps a shortlist by five rules. | `src/extraction`, `scripts/l1`, `scripts/pipeline`, `scripts/build_*.py` |
| **L2 Estimation** | Turns a policy design into a registered difference-in-differences study and runs it through an automated pipeline (stages S0 to S7: intake, treatment coding, outcomes, support check, specification, registration, execution, export). Every design ends in an estimate or in a named gate where it could not be evaluated. | `scripts/l2_pipeline` |
| **L3 Patterns** | Looks across the L2 estimates for patterns in when, where and how states adopt policies and whether effects hold. All exploratory. | `scripts/l3_meta`, `scripts/l3_pooled`, `scripts/l3_ideas` |

`scripts/viz_site` and `scripts/zh_pages` build the website from the result files. The site is written for economists and admissions readers who skim for a few
minutes, so it favours plain statements and exact numbers over documentation. `docs/FINDINGS.md` is the text of the Explore page.

## Headline numbers (as of 2026-10-23)

| | |
|---|---|
| L1: policy texts screened | 483,414 (315,727 federal regulations, 125,919 federal legislation, 41,496 state legislation, 272 state and local executive) |
| L1: pass all five rules | 7,781 candidates (5,994 of them federal regulations) |
| L2: designs processed | 170, of which 91 ended with an estimate and 79 did not |
| L2: why 79 did not | 55 stopped at the support gate, 7 at randomization-inference feasibility, 7 had no usable data connector, 4 lacked adoption history, 6 other |
| L2: how they ran | 48 by hand (all estimated), 112 by the automated loop (43 estimated), 10 stopped before a run |
| Re-estimates of published papers | 53 papers; 36 within 10% of the published figure, 8 same direction, 8 not comparable, 1 opposite sign |
| L3 lead finding | Where the gap between adopting and not-yet-adopting states was larger the year before adoption, the change after adoption was larger: slope +0.33 against -0.20 from arithmetic alone (3.9 standard deviations apart; 50 studies, 106 outcome series) |

Every number in this table is recomputed from `results/` by `python3 tools/check_headlines.py` (standard library only).
The L2 and L3 numbers come from the refresh of 2026-10-23, run r2 (`results/l2_summary.json`, `results/l3_headline.json`); the pipeline keeps running, so later counts differ.

## What is in the repo

```
results/            our estimates and decisions
  l2_design_decisions.csv        one row per design: status, gate, estimate, SE, interval, randomization-inference p
  replication_reestimates.csv    our estimate for each of 53 published papers (cited by DOI; published figures not reproduced)
  l1_funnel_counts.json          screening funnel counts
  l3/                            result files of the L3 questions
DATA_MANIFEST.md    every dataset: official source, version, licence, how to fetch (no data files are shipped)
data_manifest/      machine-readable dataset list and a download ledger with sizes and SHA-256
scripts/l2_pipeline/            the L2 pipeline: engine, stages, connectors, agent job templates, tests
scripts/l3_meta/                L3 builders, independent re-checks, pre-registration skeleton
scripts/viz_site/, zh_pages/    website builders
src/extraction/, src/config/    L1 extraction prompts, schemas and scoring
tools/check_headlines.py        re-derives the table above
```

## What is not in the repo, and why

- **No downloaded datasets.** Datasets come from official sources and are listed in `DATA_MANIFEST.md`; run the connectors to fetch them. `scripts/viz_site/data` holds only our own coded adoption dates and site inputs.
- **No authors' data or code.** The 53 re-estimates start from each paper's posted data or raw data, but the analysis code is ours; author code is never used or shipped.
- **No policy text corpus** and no model outputs for the 483k texts.
- **No scrapers or deployment tooling.**

## Using the code

The code was written for a larger private working tree, so most entry points expect data directories that are fetched separately.
`scripts/l2_pipeline/README.md` documents the pipeline. In a clean checkout 886 of the 1,500 L2 pipeline tests pass; most of the rest read files from the private data tree. Run them with

```sh
python3 -m unittest discover -s scripts/l2_pipeline/tests -t .
```

The numerical engine needs numpy, pandas and statsmodels (`requirements.txt`).
Hash values inside frozen records, and comments or paths that point at `data/` and `docs/plans/`, refer to the private working tree they were written in.

## Terms used in the code

- **hub**: the coordinating agent in the project's agent workflow. **worker**, **coder**, **auditor**, **reviewer**: agent roles that write or check one piece, each with a written job file (`scripts/l2_pipeline/agents/templates`).
- **owner**: the person who approves rules and releases (the author).
- **gate**: a named check a design must pass (support, history, connector, ri, fresh, v3); failing one ends the design as "not evaluable".

## Scope

The shortlist is a research screen, not a set of validated experiments. State bills are a roughly 10% sample of enacted, non-ceremonial bills in most states (2016 to 2026).
L3 results are exploratory and each states its own uncertainty on the page. L1 and L2 are largely separate: most L2 designs were picked from the literature, not from the shortlist (see block `F_l1_linkage` in `results/l3/funnel_20261023_r3/funnel_table.csv`).

## Licence and citation

Code: MIT (`LICENSE`). Result tables: CC BY 4.0 (`results/LICENSE.md`). Cite with `CITATION.cff`.
Author: Fangyu Liu. Open to collaborations and PhD opportunities; contact details are on the website.
