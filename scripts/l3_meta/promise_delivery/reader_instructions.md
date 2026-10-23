# Reader instructions (promise vs delivery coding)

You code U.S. state laws. For each item in your packet file, decide what the law was SOLD as changing, and whether the item's listed "outcome" measures that.

Rules: read ONLY your packet file and write ONLY your output file (both paths are in your task). Do not read other files in the repository, do not search the web, do not look for studies or results of the law, and do not talk to other readers. Use the packet text plus your own knowledge of the law's stated purpose (purpose clause, findings, bill title, official summary, sponsor or agency statements). Do not use what you may remember about whether the law worked.

For every item write one record with these fields:
- packet_id (copy exactly)
- sold_outcome: short text, the ONE first-order outcome the law's own text or sponsors say it is meant to change (the one named first or most often).
- sold_outcome_category: one of employment, crime_justice, health_behavior, education, mortality, traffic_safety, civic_political, healthcare_use, earnings_income, energy_environment, health_coverage, poverty_safety_net, births_fertility, public_finance, business_innovation, family_demography, markets_trade, other, transport_operations, housing_construction, agriculture_fisheries, unemployment_ui
- promised_direction: 1, -1 or 0. Direction of the promise in the units of the item's listed outcome when match_to_design_outcome is "same" (more employment = +1, fewer deaths = -1; an outcome measured as a rate to be lowered = -1); in the units of sold_outcome otherwise. 0 when no direction is stated.
- claim_source: purpose_clause, findings, summary (only when the packet text itself shows it), sponsor_or_agency (a widely known official or sponsor statement of purpose), or inferred (you reasoned it from the policy type).
- match_to_design_outcome: "same" if the item's listed outcome measures sold_outcome (same quantity, same population); "related" if it is a downstream, upstream or partial measure of it; "different" otherwise.
- confidence: high, medium or low.

Output: one JSON file: {"reader": "<your reader id>", "chunk": <n>, "codings": [ ...one record per item, in the packet order... ]}. Valid JSON only, every item present exactly once. Use the Write tool. Finish by replying with the single word DONE and the number of records.
