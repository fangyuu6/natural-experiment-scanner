# Data manifest

This repository ships **code and our own derived tables only**. It contains no downloaded datasets, no data or code from the
authors of the papers we re-estimate, and no text of the 483k policy corpus. Every dataset below is fetched from its official source with the
code named in the table. Sizes and SHA-256 values in `data_manifest/download_ledger.csv` let you check that a re-download matches what we used
(providers revise files, so a mismatch after a later release is expected for some series).

How to read the licence column: it is the licence string our download step recorded at retrieval time. "unverified" means the provider page
states no licence; for US federal statistical products we then treat the data as a US government work and cite the agency, but we have not
obtained a legal opinion. Nothing here is legal advice.

## 1. Policy text corpus (L1 screening)

| Source | Official source | How we fetch | Licence / terms | In this repo |
|---|---|---|---|---|
| Federal legislation | congress.gov API, govinfo.gov | `src/ingestion` (private working tree; not shipped, see README) | US government works; public domain | No |
| Federal regulations | federalregister.gov API, ecfr.gov | same | US government works; public domain | No |
| State legislation (about 10% sample of enacted, non-ceremonial bills, 2016 to 2026) | Open States bulk data and official state legislature sites (see `src/config/source_catalog.py`) | same | Open States: public-domain dedication unless noted; state sites: official records, terms vary by state | No |
| State/local executive orders | official state sites | same | official records; terms vary | No |

The corpus (483,414 texts), the database and the LLM outputs stay out of the repo. `results/l1_funnel_counts.json` has the aggregate counts only.
Model-written scores (extraction, literature labels) come from commercial LLM APIs; those outputs are not redistributed.

## 2. Datasets used by the L2 pipeline and the replication check (fetchable, listed by dataset)

Generated from the download ledgers of the pipeline runs (`data_manifest/datasets.csv` has the same table with the example URL, fetch code and byte counts).

| Dataset | Official source | Files | Retrieved | Licence as recorded |
|---|---|---:|---|---|
| `bls_qcew_industry` | data.bls.gov | 52 | 2026-10-11 | US federal government product (BLS QCEW open data); BLS states data are in the public domain and may be used without permission, cite BLS (bls.gov/opub/copyright-informa… |
| `cdc_brfss_legacy` | data.cdc.gov | 4 | 2026-10-11 | Public Domain per the data.cdc.gov view metadata; attribution: Centers for Disease Control and Prevention National Center for Chronic Disease Prevention and Health Promo… |
| `cdc_yrbs_sadc` | www.cdc.gov | 24 | 2026-10-11 | US federal government product; no licence text on the download page, cite source agency; unverified |
| `census_acs1` | www2.census.gov | 414 | 2026-10-11 | US federal government product (Census Bureau ACS); public summary file, no licence text on the download page, cite U.S. Census Bureau ACS; unverified |
| `census_asfin` | www2.census.gov | 32 | 2026-10-11 | US federal government product; no licence text on the download page, cite source agency; unverified |
| `census_bps` | www2.census.gov | 46 | 2026-10-11 | US federal government product; no licence text on the download page, cite source agency; unverified |
| `census_cps_asec` | www2.census.gov | 29 | 2026-10-11 | US federal government product; no licence text on the download page, cite source agency; unverified |
| `census_cps_dws` | www2.census.gov | 8 | 2026-10-11 | US federal government product; no licence text on the download page, cite source agency; unverified |
| `census_stc` | www2.census.gov | 32 | 2026-10-11 | US federal government product; no licence text on the download page, cite source agency; unverified |
| `cms_sdud_buprenorphine` | data.medicaid.gov | 154 | 2026-10-11 | https://www.usa.gov/government-works (license field of the data.medicaid.gov dataset metadata; US federal government work) |
| `dol_eta_claims` | oui.doleta.gov | 2 | 2026-10-11 | US federal government product; no licence text on the download page, cite source agency; unverified |
| `bls_api_v1` | api.bls.gov | 21 | 2026-10-16 | US federal government public-use data; public domain (17 USC 105); retain attribution |
| `census_acs1_tablebased_b22003` | www2.census.gov | 4 | 2026-10-16 | US federal government public domain; cite US Census Bureau ACS |
| `census_acs1_seq_b22003` | www2.census.gov | 928 | 2026-10-16 | US federal government public domain; cite US Census Bureau ACS |
| `run6_cdc` | data.cdc.gov | 100 | 2026-10-09 | ODC-BY 1.0; https://opendatacommons.org/licenses/by/1.0/ |
| `run6_pdaps` | pdaps.org | 12 | 2026-10-09 | CC BY-NC 3.0; https://creativecommons.org/licenses/by-nc/3.0/ |
| `run6_social` | nces.ed.gov | 6 | 2026-10-09 | US government public domain; credited ECS/CCSSO source rights may differ |
| `run6_cms` | www.medicaid.gov | 3 | 2026-10-09 | US federal government authored material; public domain in US (17 USC105); third-party materials excluded |
| `run6_sources` | www.flsenate.gov | 1 | 2026-10-09 | Florida Senate official legislative summary; no open reuse license verified; retain as research evidence, no publication of full text |
| `acs` | www2.census.gov | 4 | n/a | US federal government public statistical data; public domain; source acknowledgement requested |
| `acs_np` | www2.census.gov | 236 | n/a | US federal government public statistical data; public domain; source acknowledgement requested |
| `qcew` | data.bls.gov | 22 | n/a | US federal government public statistical data; public domain; source acknowledgement requested |
| `qcew_history` | data.bls.gov | 78 | n/a | US BLS public-domain statistical data |
| `cdc` | data.cdc.gov | 1 | n/a | US federal government public statistical data; public domain; source acknowledgement requested |
| `bea_sainc1` | apps.bea.gov | 1 | 2026-10-04 | UNVERIFIED (BEA pages state no licence; US Bureau of Economic Analysis federal-government product; BEA citation guidelines say citations are appreciated) |
| `berry_ideology` | rcfording.files.wordpress.com | 1 | 2026-10-03 | UNVERIFIED (page states no terms of use; page asks that requests for earlier releases go to R. Fording; catalogue 4.4 proposes asking him for terms before any redistribu… |
| `bls_cpi` | api.bls.gov | 8 | 2026-10-04 to 2026-10-15 | UNVERIFIED (BLS terms and copyright pages at www.bls.gov returned 403 Access Denied to automated fetch and were not retrieved; US Bureau of Labor Statistics federal-gove… |
| `census_county_adjacency_2026` | www2.census.gov | 1 | 2026-10-03 | UNVERIFIED (no licence statement on the Census page; US Census Bureau federal-government product, published for public download) |
| `census_popest_2000_2010` | www2.census.gov | 1 | 2026-10-04 | UNVERIFIED (Census page states no licence; US Census Bureau federal-government product) |
| `census_urban_1900_1990` | www2.census.gov | 1 | 2026-10-15 | UNVERIFIED (US federal-government product; the page states no licence) |
| `census_urban_2000_ua` | www2.census.gov | 1 | 2026-10-15 | UNVERIFIED (US federal-government product; the page states no licence) |
| `census_urban_2000_uc` | www2.census.gov | 1 | 2026-10-15 | UNVERIFIED (US federal-government product; the page states no licence) |
| `census_urban_2010_2020` | www2.census.gov | 1 | 2026-10-15 | UNVERIFIED (US federal-government product; the page states no licence) |
| `census_urban_2010_pct` | www2.census.gov | 1 | 2026-10-15 | UNVERIFIED (US federal-government product; the page states no licence) |
| `klarner_main` | dataverse.harvard.edu | 1 | 2026-10-03 | CC0 1.0 (Harvard Dataverse licence field for doi:10.7910/DVN/LZHMG3) |


## 3. Licence notes for other sources used in L3 covariates and coding (carried from the 2026-10-03 public draft, rows about unshipped code removed)

| Source | Licence or usage terms |
|---|---|
| [Shor–McCarty legislator ideology](https://doi.org/10.7910/DVN/SGOQ7G) and [state aggregates](https://doi.org/10.7910/DVN/T53FFK) | CC BY-NC-SA 4.0. |
| [MultiState effective dates](https://www.multistate.us/resources/2026-state-effective-dates) | [Conditions of use](https://www.multistate.us/conditions-of-use): personal use / business relationship only; copying, modification and distribution prohibited. |
| [Kaplan governors](https://doi.org/10.7910/DVN/RYY3OW) | CC BY 4.0. |
| [Klarner partisan balance](https://doi.org/10.7910/DVN/LZHMG3) and [election returns](https://doi.org/10.7910/DVN/FJOGJB) | CC0 1.0. |
| SPID: [CVYSR7](https://doi.org/10.7910/DVN/CVYSR7), [1QJCDJ](https://doi.org/10.7910/DVN/1QJCDJ), [SJTI2W](https://doi.org/10.7910/DVN/SJTI2W), [GMVOI5](https://doi.org/10.7910/DVN/GMVOI5) | CC0 1.0. |
| [Open States / Plural Open data](https://open.pluralpolicy.com/data/) | Public-domain dedication unless otherwise noted. |
| [Squire professionalization](https://doi.org/10.15139/S3/2VORBN), [2017 update](https://doi.org/10.15139/S3/DOS6TZ) and [Parry party monopolies](https://doi.org/10.15139/S3/CTWPFW) | CC0 text with an additional/modified-terms notice; consult each record's terms. |
| CDC STATE System and Tax Burden on Tobacco (`7nwe-3aj9`) | Open Data Commons Attribution License v1.0; cite CDC and Orzechowski and Walker for tobacco sales. |
| CDC BRFSS | `dttw-5yxu`: public domain; `j8jk-5ztv`: no licence stated. |
| BLS QCEW and EIA electricity data | Public domain; provider citation requested. |
| Census ACS, QWI, commuting/migration flows, county adjacency and LODES; IRS migration; CMS-64; FBI UCR; BJS; NHTSA FARS; BEA regional accounts; FHFA HPI | Federal government data products; cite the agency, product and release. Computed Census-derived estimates are included; the frozen Medicaid records retain the earlier, pre-publication redistribution-status note. |
| [Nath/Cullen RPS replication data](https://doi.org/10.7910/DVN/6CR1WU) | CC0 1.0. |
| [Primary seat-belt replication package](https://doi.org/10.15456/jae.2023331.2348092600) | CC BY 4.0. |
| [KFF Medicaid expansion tracker](https://www.kff.org/affordable-care-act/state-indicator/state-activity-around-expanding-medicaid-under-the-affordable-care-act/) | Used for cited implementation dates; page content is excluded. |
| [IIHS seat-belt law table](https://www.iihs.org/research-areas/seat-belts/seat-belt-law-table) | Used for cited law dates; page content is excluded. Cite IIHS. |
| [Mixtape `castle.dta`](https://github.com/scunning1975/mixtape) | Repository licence: MIT. |
| Vaghul–Zipperer historical minimum wages and author-hosted Cheng–Hoekstra replication materials | No accompanying licence; raw packages excluded. |
| [Union Membership and Coverage Database](https://www.unionstats.com/) | Citation required. |
| [NCSL](https://www.ncsl.org/terms-of-use), including ballot measures | Reproduction, republication and redistribution require permission. Cited enactment years and coverage/date cross-checks are included in paid-sick-leave treatment code; complete pages and tables are excluded. |
| CSPP, Berry ideology, NCSBN licence statistics, IMLCC reports and RAND firearm-law database | No redistribution licence established in the source records; provider material excluded. |
| [LegiScan](https://legiscan.com/legiscan) bulk datasets and API | CC BY 4.0. |
| [Parinandi RPS coding](https://doi.org/10.7910/DVN/PMYBBM) | Use information is limited; contact the dataset contacts for terms. |
| IRI direct-democracy data | Contact the provider for usage terms. |
| HeinOnline session laws and NASBO reports | Subscription/purchase terms; source material excluded. |
| [Ballotpedia bulk data](https://developer.ballotpedia.org/) | Proprietary. |
| Crossref and NCBI E-utilities | Used for bibliographic metadata; retrieved content excluded. |
| Official legislative, statutory, court and agency records; research papers; nonprofit and law-firm trackers | Used for cited policy dates and benchmark checks; short quoted evidence fragments from HR Dive, SHRM, DPIC and official records are included in case evidence code. Downloaded originals are excluded. |


## 4. Used locally, not redistributed, access restricted

These sources need an account, a data-use agreement or payment, or forbid redistribution. Nothing from them is in this repo, and no table in `results/` reproduces their records.

| Source | Why it stays out |
|---|---|
| NPDB public use data file | provider terms; obtain from the NPDB site |
| ICPSR studies 35158 and 37986 | ICPSR data-use terms; obtain from ICPSR |
| DEA drug-distribution data | restricted or request-only |
| Vera Institute incarceration data and Social Explorer extracts | provider terms / subscription |
| Replication packages posted by paper authors (Dataverse, Zenodo, journal sites) | the authors' data and code; we use them locally, never redistribute, and rebuild the estimate with our own code. `replication_reestimates.csv` lists papers by citation only |
| CSPP, Berry ideology, NCSBN, IMLCC, RAND firearm-law database, MultiState, NCSL, HeinOnline, NASBO, Ballotpedia, IRI | no redistribution licence (see section 3) |
| UF Election Lab voter turnout (VEP) | the file notes say CC BY 4.0, the archive page says CC BY-NC-ND 4.0; treated as not redistributable |

## 5. Other items

- Paper results quoted for comparison are cited by DOI in `results/replication_reestimates.csv`; published estimates are not reproduced.
- Boundary geometry in the site map comes from the us-atlas package (ISC licence), derived from US Census cartographic boundaries.
- LawAtlas material (CC BY-NC 3.0) is excluded from this repo.
