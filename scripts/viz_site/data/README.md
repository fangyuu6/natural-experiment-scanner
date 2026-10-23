# scripts/viz_site/data

Data for the homepage figure "what a natural experiment looks like": staggered state adoption of 8 policies.

| File | What it is |
|---|---|
| `curated_adoption.json` | Output. 8 policies in display order, adopting states with year/verification/confidence/<=3 URLs, excluded states, DB evidence rows. As of 2025-12-31. |
| `curated_raw/<id>.final.json` | Research table after the independent second pass (input). `<id>.verify.json` is the verifier report; `*.research.json` is the first pass. |
| `curated_raw/db_evidence_checked.json` | Hand-checked policy-DB rows (input), plus per-policy rejection counts. Lets the generator run without the 1.5 GB CSV. |
| `curated_raw/db_matches.json` | Earlier title-regex candidates (not used by the generator). |

Rebuild and check (stdlib only, deterministic):

    python3 scripts/viz_site/make_curated_adoption.py
    python3 scripts/viz_site/validate_curated.py

## Rules in the generator

- A state is listed only with a non-null final year and verification in {verified, fixed, single_source}. DC counts as one of the 51 units.
- `excluded` = disputed states and states removed in the second pass (reason <= 80 chars): license_recognition NJ, tnc_framework CA, salary_history_ban AL. States that simply had no law by 2025-12-31 (or whose law starts later, e.g. VA sick leave 2027, MO repeal) are not listed anywhere.
- `sources` = up to 3 URLs per state, ranked official statute/legislature pages, then trackers, then law-firm/press, then wikis/press releases.
- Year bases differ by policy and are stated in `year_basis_*`: signing year (licence recognition, both compacts, TNC, $15 wage), first operative date (paid sick leave), first benefits payable (paid family leave), effective date (salary-history bans). Do not compare years across policies without that.

## DB evidence rule (small dots on the map)

A row counts only if it is a State Legislation row (policy DB, dedup on `policy_id`, 41,383 rows with a valid state), a human read of title plus pitch/treatment text confirms it is the enacting law (or substantive enacting amendment) of that policy as defined, its DB year is within +-1 of the state's final year (DB year = last status date), and the state has a non-null final year. Titles are the DB titles, shortened with "..." to <= 90 characters; HI and IL carry the bill number because the DB title is generic. Known DB defect: for AL 1223724, AL 1057601 and IL SB 1 the pitch/treatment text belongs to another bill; title, URL and date are right, so they were kept.

Search: for each policy a sharp keyword regex over title, treatment and pitch (nurse licensure compact, interstate medical licensure, transportation network company, paid sick / earned sick, paid family and medical leave, salary / pay history, minimum wage / $15, universal licensure recognition), then the checks above. Candidates are keyword hits, not all rows (the licence-recognition pattern is broad and matched 674 mostly unrelated rows).

| policy | db_evidence | candidates | rejected | main rejection reasons |
|---|---|---|---|---|
| license_recognition | 2 (ND, NH) | 674 | 672 | no final year 433, blank year/title 126, year gap 87, in window but narrow or other law 26 (MO HB 1511 is military-spouse reciprocity, not HB 2046) |
| nurse_licensure_compact | 5 (AL, FL, RI, TN, UT) | 12 | 7 | other compacts or mismatched text; CO SB18-027 has blank year/title in DB |
| medical_licensure_compact | 4 (AR, CO, DE, MD) | 20 | 16 | year gap 12 (mostly other-profession compacts and later amendments), blank 3 |
| tnc_framework | 3 (AL, DE, NM) | 23 | 20 | NM HB 77 and MD SB 54 minor amendments, VT insurance-only H.143, NY workers' comp, AK (see below) |
| paid_sick_leave | 1 (NJ) | 71 | 70 | sick-leave banks and public-employee bills; NY 2020 A 10153 is temporary COVID leave |
| paid_family_leave | 0 | 58 | 58 | benefits-first-payable years sit 2-4 years after the enacting laws, so none is within +-1; later amendments only |
| salary_history_ban | 0 | 21 | 21 | CA AB 46 (2017) not shown to be the ban (AB 168 is not in the DB); DC rows blank |
| min_wage_15 | 4 (CT, HI, IL, RI) | 97 | 93 | $15 amounts in tax/bond bills, DC contract approval, non-$15 amendments |

Details per rejected row group are in `curated_raw/db_evidence_checked.json` (`rejected_counts`, `rejected_notable`).

## Open flag

- Closed 2026-10-14: tnc_framework AK, year 2017 (HB 132, Chapter 10 SLA 2017, signed 2017-06-15, effective 2017-06-16), checked in `docs/plans/site-restructure/ALASKA_YEAR_CHECK_R21.md`. The DB row for SB 14 (last action 2018-06-27) is the later amending act (Chapter 31 SLA 2018) and stays rejected as evidence. `validate_curated.OPEN_YEAR` is now empty, so the maps and the Adoption sources table draw and list every `single_source` row with its recorded year; the row keeps `single_source`.
- The DB is a ~10% sample of enacted bills for most states, so a missing dot never means the state did not adopt.
