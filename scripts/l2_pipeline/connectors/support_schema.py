"""Shared value-free sidecar fields; no connector imports or runtime state."""
SUPPORT_FIELDS = {'unit','state','year','period','frequency','available','annual_usable',
    'linkage_certified','suppressed','unreliable','provisional','published_cell_present',
    'denominator_available','missing_reason','series_break','limited_data','source_id_corrected',
    'unknown_age_present','unknown_year_present','source_issue','icd_version','reporting_coverage_unknown','suppression_reason','sampling_error','partial_year'}


# Reviewed C70 value-free quality/provenance fields. These describe reporting
# coverage and labels, never the policy outcome/count/population denominator.
C70_SUPPORT_FIELDS = frozenset({'institution_coverage','institution_coverage_sensitivity_99',
    'institutions_expected','institutions_reporting','unit_term','contributor_exclusion','question_id'})
SUPPORT_FIELDS = SUPPORT_FIELDS | C70_SUPPORT_FIELDS

