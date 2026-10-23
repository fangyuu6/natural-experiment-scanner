# Public annual connector library

First-order adapters and observed source coverage are documented in
`data/l2_pipeline/first_order_connectors/REPORT.md`; exact byte inventories are
in `MANIFEST.json` and value-free availability is in `COVERAGE.json` there.
The `FIRST_ORDER_MEASURES` capability map binds outcome IDs to actual parsers.

| First-order connector | Published population and required fields | Source support |
|---|---|---|
| `acs1_health_insurance` | `health_insurance_coverage_pct`; filters `age_group` (B27001 published bin) and `sex` (`all`/`male`/`female`) | ACS1 2008+ except 2020; 2022+ summary files, earlier keyless API plans; actual 2023 file parsed |
| `brfss_state_prevalence` | Exact `questionid`, `responseid`, `breakoutid`, `data_value_type` plus `outcome.response_label`; positive question/response IDs are enforced | Post-2011 published weighted state percent; question-specific waves, no interpolation |
| `ipeds_completions` | `bachelors_completions_count`; filters `award_level:5`, `cipcode:"99"`, `major_number:1`, `sex:"all"`, `institution_control:"public"` | Matched HD/C CSV ZIP; exclude admin units; explicit `institution_ids` roster supported |
| `ipeds_enrollment` | `undergraduate_enrollment_count`; filters `level:"undergraduate"`, `sex:"all"`, `institution_control:"public"` | Matched HD/EF annual fall total EFALEVEL2; missing component institutions invalidate totals |
| `nchs_mortality` | `drug_overdose_deaths_count`/`drug_overdose_age_adjusted_rate` with `sex:"Both Sexes"`, `age:"All Ages"`, `race:"All Races-All Origins"`; general `mortality_*` with exact `cause_name` | Actual final overdose source 1999–2018, leading causes 1999–2017; no homicide or motor vehicle subcause in latter |
| `fbi_ucr_state` | Nine major offense `*_count` IDs; `series:"estimated_state_total"` | Legacy official table5 2010–2019 HTML or optional-xlrd XLS; endpoint currently403, acquisition unavailable |
| `cdc_natality` | `births_count`/`birth_rate`; exact maternal-age filter `age_group:"15-17"`, `"18-19"` or `"15-19"` | Actual final CDC NCHS y268-sna3 state-of-maternal-residence public count/rate cells 1990–2019 |

`first_order_download_plan(request)` returns exact official HTTPS URLs without
network. `fetch_first_order_public(plan, new_destination)` returns URL, byte size,
rights and SHA256; never overwrite or retry an access block. Calls are explicit:
`construct()` only reads local source files. Socrata CSV query caps are checked
before saving. IPEDS ZIP original/revised members are never mixed; the default is
original, changed only with `ipeds_vintage:"revised"`.

The public CRC guideline cell is `_CRCREC`/`RESP192`/`BO1`/`Crude Prevalence`,
response label `Met USPSTF recommendations for testing`, ages50–75, observed
2014/2016/2018. `_COL10YR`/`RESP207` is colonoscopy only, observed2016/2018 and
requires a proxy rationale when the policy targets all screening. Later CRC
definitions `_CRCREC1/2/3` change tests/age eligibility and require separate
predeclared windows. Smoking `_RFSMOK3`/`RESP046`, binge `_RFBING5/6`/`RESP046`,
obesity `_BMI5CAT`/`RESP039`, and drinking-driving `_DRNKDRV`/`RESP201` are supported.
Exact denominator, response label and vintage remain part of registration.

Natality published state teen age cells are supported separately from public
microdata, which omit recent state identifiers. Other maternal ages and years
after 2019 remain unavailable in this adapter. National age rates and county
teen rates cannot substitute for state outcomes. Published count/rate columns
are strictly bound; overlapping age-bin totals are never combined. No restricted
WONDER query or account-based acquisition is automated.

`construct(ConnectorRequest.from_card(card, repo))` reads declared local files and
returns the shared state-year panel, separate availability/linkage metadata,
raw-file URL/size/license/SHA256 inventory, and construction rules. It receives
no treatment table and computes no policy outcome association. `build_panel`
writes these into a new version directory. Population filters must describe
the published cells; these adapters cannot apply unobserved microdata filters.

| Connector | Source bytes | Annual construction |
|---|---|---|
| `qcew` / `qcew_annual` | Official annual CSV or CSV ZIP | Published state totals, or an explicitly declared county roster aggregated to state |
| `acs1` / `acs1_state` | Archived keyless API JSON or public table-based pipe-delimited DAT | Selected detailed-table estimate cells, sum or ratio of declared cells |
| `laus` / `laus_state` | Official `la.data.2.AllStatesU` TSV or archived BLS API JSON | Full-state NSA unemployment rate, official annual-average `M13` only |

QCEW requires `population_filters={"ownership":"private","industry":"23"}`
(ownership also accepts total/federal/state/local). `value_field` accepts
`annual_avg_emplvl`, `annual_avg_estabs_count`, `total_annual_wages`,
`annual_avg_wkly_wage`, and `avg_annual_pay`. Legacy `avg_wkly_wage` maps to the
official annual field. `source_geography="county"` also requires `county_fips`
mapping every requested state to its reviewed complete county roster. Missing
or suppressed counties make the state cell missing; wage aggregation uses
total wages divided by employment (and 52 for weekly wages). County aggregation
covers that roster and does not claim equivalence to official state totals,
which can contain separately allocated jobs. Quarterly rows and duplicate
area/year/industry/ownership cells refuse.

ACS1 requires exact `source_population_filters` matching card filters and
`variables=["B23025_005E"]` for an estimate/sum, or `numerator_variables` and
`denominator_variables` for a ratio. `scale` defaults to 100 for ratios and 1
for sums. `outcome_unit` records original units. File names declare the year
(`acs1_2023.json`, `acsdt1y2023-b23025.dat`), or `source_years` maps paths to
years. Summary columns such as `B23025_E005` map to API `B23025_005E`.
Special estimate codes/nulls remain missing; a zero denominator remains missing;
duplicate state/year/variable inputs refuse. `value_min` defaults to zero and
can be explicitly set to null for variables whose documented range is negative.
Standard 2020 ACS1 is unavailable; experimental estimates are not spliced.

LAUS requires `population_filters={"age_min":16,"civilian_noninstitutional":true}`
and `seasonal_adjustment="not_seasonally_adjusted"` (the default).
SA, county, balance-of-state and monthly rows cannot enter its annual panel.
Missing `M13` stays missing rather than averaging monthly rates. Revision
footnotes and the complete raw-file vintage/hash remain in metadata/inventory.

`download_plan(request)` is network-free. QCEW plans annual singlefile ZIPs;
LAUS plans the unadjusted full-state bulk file; ACS1 plans public table files
(2022+) with a credential-free API alternative. Earlier ACS1 local JSON can be
parsed but sequence-based public download planning is explicitly parked.
`fetch_public(plan, new_path)` is a separate explicit operation: zero retries,
official HTTPS only, no credentials/keys, no overwrite, no bot-check bypass,
and exact URL/final URL/size/rights/SHA256 returned to the caller for its manifest.
Construction never invokes it. No actual data files were downloaded in this build.

`download_strategy="industry_slice"` selects the smaller official QCEW annual
industry CSV endpoint for 2014+. Older years retain the annual ZIP plan.

`ownership="total"` plus `ownership_aggregation="certified_sum"` reconstructs
the total from ownership 1/2/3/5 only with a declared
`ownership_completeness_certificate`. This certificate binds each complete
official industry slice's URL, bytes and SHA and the BLS documentation basis.
For 2004+ files, nonexistent ownership macro cells may be zero because the
official complete slice retains suppressed cells with `N`; any suppressed
component leaves the sum missing. A published ownership-0 total is preferred
when present. Sums of separately published annual averages may differ from a
separately rounded total. This mode does not switch the outcome to private
ownership, infer suppressed values or fill missing files with zero.

`cps` / `cps_monthly` routes the exact hospital `registered_nurses` target to
`cps_nurse_hours`, which requires a declared `dictionary_root` containing the
official local record layouts. `weekly_hours` pools weighted PEHRACTT actual
hours at all jobs among nurses at work (PEMLR=1); `long_hours_percent` pools the
share exceeding a declared threshold (default 40). Missing/invalid hours are
excluded and counted; missing months invalidate annual cells. The first job's
occupation and industry determine membership. Offsets are checked against each
used layout epoch and all dictionary hashes and initial-check telemetry are
retained. No treatment inputs enter this construction.

RN codes are 095 before 2003, 3130 in 2003–2010 and 3255 from January 2011;
hospital codes are 831 before 2003, 8190 in 2003–2019 and 8191/8192 from 2020.
The earlier RN definition includes advanced-practice nurses. Specs should
retain this population break and a 2011+ sensitivity when that window has
support. Actual hours cannot isolate compelled overtime or first-job hours.
Official code sources are recorded in the connector's construction metadata.

Official references inspected through web tools (untrusted data; no local scan claimed):

- [QCEW annual fields](https://www.bls.gov/cew/about-data/downloadable-file-layouts/annual/naics-based-annual-layout.htm)
- [QCEW historical downloads](https://www.bls.gov/cew/downloadable-data-files.htm)
- [ACS1 public summary directory](https://www2.census.gov/programs-surveys/acs/summary_file/2023/table-based-SF/data/1YRData/)
- [ACS1 B23025 public table](https://www2.census.gov/programs-surveys/acs/summary_file/2023/table-based-SF/data/1YRData/acsdt1y2023-b23025.dat)
- [Current Census API guide](https://www.census.gov/data/developers/guidance/api-user-guide.API_Key.html)
- [LAUS bulk inventory](https://download.bls.gov/pub/time.series/la/)
- [LAUS series dictionary](https://download.bls.gov/pub/time.series/la/la.series)
- [BLS public domain rights](https://www.bls.gov/bls/linksite.htm)

Keyless Census access could not be verified through the web tool; no account or
key was requested. The implemented public summary path avoids that dependency.

V6 outcome-first catalog: `catalog.CONNECTOR_CATALOG` lists exact target-to-measure
bindings, public-file year coverage, population/geography and source access.
`target_mapped` refuses unsupported targets and unavailable access/rights; matching
is a purpose-verification lead, with population and source support checked later.
`fars_state` plans original NHTSA national CSV ZIPs1982–2022 and constructs
all-traffic deaths/crashes or age/role-specific child/teen deaths. Exact bicyclist
PER_TYP=6 supports child_bicycle_deaths_count; other cyclists are excluded. Unknown ages
are excluded/counted and prevent certified subgroup linkage. Person fatalities
must reproduce accident FATALS before subgroup selection; no injury/BAC proxy.
`ca_doj_crime` plans no-key CA DOJ agency summary CSV1985–2025. It covers CA
only; reuse rights remain unverified, so default construction parks. Reviewed
rights plus located annual agency-roster certification are required for totals.
