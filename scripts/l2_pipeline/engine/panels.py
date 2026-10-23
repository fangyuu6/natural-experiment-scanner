"""Exact metadata mask and shared primary/sensitivity replacement path.

Structural audits project only unit/year prefixes; values load separately.
"""
from __future__ import annotations
import csv, hashlib, math
from pathlib import Path

from dataclasses import dataclass
from .types import PanelRow


def transition_year_drop_enabled(card):
    """Absent archival flags remain false; only literal booleans are valid."""
    value = card.get('transition_year_drop', False)
    if type(value) is not bool:
        raise ValueError('transition_year_drop must be a boolean')
    return value


def transition_year_keys(effective_years, enabled=False):
    """Metadata-only per-state exclusions; never-treated states have no entry."""
    if type(enabled) is not bool:
        raise ValueError('transition_year_drop must be a boolean')
    if not enabled:
        return set()
    if any(type(year) is not int for year in effective_years.values()):
        raise ValueError('Transition effective calendar years must be integers')
    return {(unit, year) for unit, year in effective_years.items()}


@dataclass(frozen=True)
class PanelBinding:
    """Source + complete registered replacement bound to an exact metadata mask."""
    source_path: Path
    panel_path: Path
    metadata_path: Path
    source_sha256: str
    panel_sha256: str
    metadata_sha256: str
    required_keys: frozenset[tuple[str, int]] | None = None


def audit_binding(binding: PanelBinding) -> dict:
    """Hash bytes and project prefixes; never convert numeric outcome fields."""
    for path, expected in ((binding.source_path, binding.source_sha256),
                           (binding.panel_path, binding.panel_sha256),
                           (binding.metadata_path, binding.metadata_sha256)):
        if sha(path) != expected:
            raise RuntimeError('Registered panel binding hash mismatch: ' + str(path))
    construction = metadata_keys(binding.metadata_path)
    _merge(dict.fromkeys(projected_keys(binding.source_path)),
           dict.fromkeys(projected_keys(binding.panel_path)), construction)
    required = construction if binding.required_keys is None else set(binding.required_keys)
    if not required <= construction:
        raise RuntimeError("Registered analysis mask exceeds construction inventory")
    return dict(passed=True, cells=len(required), construction_cells=len(construction), outcome_values_parsed=0,
                treated_post_outcome_fields_parsed=0,
                panel_sha256=binding.panel_sha256, metadata_sha256=binding.metadata_sha256,
                merge_path='declared_source + complete_registered_variant -> exact_metadata_mask')


def load_bound_panel(binding: PanelBinding) -> tuple[list[PanelRow], dict]:
    """Decode for approved execution or the mandatory no-fit DRY-LOAD check.

    DRY-LOAD validates values without associations, output values or reservation.
    """
    identity = audit_binding(binding)
    required = (metadata_keys(binding.metadata_path) if binding.required_keys is None
                else set(binding.required_keys))
    merged = _merge(_raw_records(binding.source_path, required),
                    _raw_records(binding.panel_path, required), required)
    rows = []
    for (unit, year), record in sorted(merged.items()):
        value = _value(record)
        if value is None:
            from ..execution import DeclaredStop
            raise DeclaredStop('Sensitivity outcome/support inventory mismatch: missing outcome')
        rows.append((unit, year, value, None))
    require_inventory({(u, t) for u, t, _, _ in rows}, required)
    identity['outcome_values_parsed'] = len(rows)
    identity.pop('treated_post_outcome_fields_parsed')
    return rows, identity
def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def projected_keys(path):
    """Do not CSV-parse or convert any outcome field, including treated-post tails."""
    keys = set()
    with Path(path).open(newline='', encoding='utf-8') as handle:
        header = next(handle, '').rstrip('\r\n').split(',')
        ui, yi = key_columns(header)
        for line in handle:
            key = key_from_line(line, ui, yi)
            if key in keys:
                raise RuntimeError('Duplicate outcome key')
            keys.add(key)
    return keys


def key_columns(header):
    """Named keys shared by calibration and loading; numeric tails stay opaque."""
    if len(header) != len(set(header)) or not {'unit', 'year'} <= set(header):
        raise RuntimeError('Panel key columns missing or duplicated')
    return header.index('unit'), header.index('year')


def key_from_line(line, ui, yi):
    """Locate named keys lexically, respecting CSV quotes and stopping at keys.

    Fields before a key are traversed as opaque text. Fields after the last key
    are never parsed, so an unselected treated-post numeric tail stays opaque.
    """
    selected={};column=0;start=0;quoted=False;i=0
    while i <= len(line):
        char=line[i] if i < len(line) else '\n'
        if char=='"':
            if quoted and i+1 < len(line) and line[i+1]=='"':i+=2;continue
            quoted=not quoted
        if not quoted and char in ',\r\n':
            if column in (ui,yi):
                selected[column]=next(csv.reader([line[start:i]],strict=True))[0]
            if column >= max(ui,yi):
                return selected[ui],int(selected[yi])
            if char!=',':break
            column+=1;start=i+1
        i+=1
    raise RuntimeError('Invalid panel key prefix')

def metadata_keys(path):
    """Metadata has named columns; outcome prefixes keep their strict contract."""
    keys=set()
    with Path(path).open(newline='',encoding='utf-8') as handle:
        reader=csv.DictReader(handle)
        if not reader.fieldnames or len(reader.fieldnames)!=len(set(reader.fieldnames)) or not {'unit','year'}<=set(reader.fieldnames):
            raise RuntimeError('Metadata key columns missing or duplicated')
        for row in reader:
            key=row['unit'],int(row['year'])
            if not key[0] or key in keys:raise RuntimeError('Invalid or duplicate metadata key')
            keys.add(key)
    return keys


def metadata_eligible_keys(path):
    """Select declared availability flags from metadata; never open outcomes."""
    keys = set(); seen = set()
    with Path(path).open(newline='', encoding='utf-8') as handle:
        for row in csv.DictReader(handle):
            key = row['unit'], int(row['year'])
            if key in seen:
                raise RuntimeError('Duplicate outcome key')
            seen.add(key)
            if (all(row.get(flag, '1') == '1' for flag in
                    ('available', 'annual_usable', 'linkage_certified'))
                    and all(str(row.get(flag, '0')).strip().lower() in {'0', 'false', ''}
                            for flag in ('suppression_flag', 'suppressed', 'unreliable'))):
                keys.add(key)
    return keys


def _raw_records(path, required=None):
    """Select keys before parsing their tails; return raw strings, not numbers."""
    rows = {}
    seen = set()
    with Path(path).open(newline='', encoding='utf-8') as handle:
        header = next(handle, '').rstrip('\r\n').split(',')
        ui, yi = key_columns(header)
        for line in handle:
            key = key_from_line(line, ui, yi)
            if key in seen:
                raise RuntimeError('Duplicate outcome key')
            seen.add(key)
            if required is not None and key not in required:
                continue
            values = next(csv.reader([line]))
            if len(values) != len(header):
                raise RuntimeError('Malformed outcome record')
            rows[key] = dict(zip(header, values))
    return rows

def _number(value, convert=float):
    try:
        return convert(value)
    except (ValueError, TypeError):
        raise ValueError('Outcome value is not numeric') from None


def _value(record):
    field = ('outcome_value' if 'outcome_value' in record else
             'employment_percent' if 'employment_percent' in record else 'turnout_percent')
    if record[field] == '':
        return None
    value = _number(record[field])
    if not math.isfinite(value) or (field != 'outcome_value' and not 0 <= value <= 100):
        raise RuntimeError('Outcome construction range failure')
    if field == 'employment_percent':
        share = _number(record['employment_share'])
        if not math.isfinite(share) or not 0 <= share <= 1 or abs(value - 100 * share) > 1e-8:
            raise RuntimeError('Outcome construction identity failure')
        weight = _number(record['weight_sum'])
        if not math.isfinite(weight) or weight <= 0 or _number(record['n_person_months'], int) <= 0:
            raise RuntimeError('Invalid target denominator')
    return value

def read_panel(path):
    return {key: record for key, record in _raw_records(path).items()
            if _value(record) is not None}

def require_inventory(actual, required):
    if set(actual) != set(required):
        raise RuntimeError('Sensitivity outcome/support inventory mismatch')

def _merge(base, replacement, required):
    """Shared merge on the complete declared mask, for keys and actual records."""
    if not set(required) <= set(base):
        raise RuntimeError('Historical/source inventory incomplete')
    require_inventory(replacement, required)
    merged = {key: value for key, value in base.items() if key in required}
    # A complete registered variant replaces the mask, including historical rows.
    merged.update(replacement)
    require_inventory(merged, required)
    return merged

def load_variant(primary, overlay=None, replacement_years=(), exclude_units=(), exclude_years=()):
    historical = read_panel(primary)
    if overlay is not None:
        if not replacement_years:
            raise RuntimeError('Overlay replacement periods undeclared')
        added = read_panel(overlay)
        if any(year not in replacement_years for _, year in added):
            raise RuntimeError('Unexpected overlay period')
        expected = {key for key in historical if key[1] in replacement_years}
        if set(added) != expected:
            raise RuntimeError('Overlay replacement inventory incomplete')
        replacement = {key: record for key, record in historical.items()
                       if key[1] not in replacement_years}
        replacement.update(added)
        historical = _merge(historical, replacement, set(historical))
    return {key: record for key, record in historical.items()
            if key[0] not in exclude_units and key[1] not in exclude_years}
