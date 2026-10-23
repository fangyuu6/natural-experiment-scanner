"""Outcome-only contracts; adapters receive no treatment tables or estimators."""
from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

STATES = dict(zip(
    '01 02 04 05 06 08 09 10 11 12 13 15 16 17 18 19 20 21 22 23 24 25 26 27 28 29 30 31 32 33 34 35 36 37 38 39 40 41 42 44 45 46 47 48 49 50 51 53 54 55 56'.split(),
    'AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY'.split()))
NAMES = dict(zip(
    'Alabama|Alaska|Arizona|Arkansas|California|Colorado|Connecticut|Delaware|District of Columbia|Florida|Georgia|Hawaii|Idaho|Illinois|Indiana|Iowa|Kansas|Kentucky|Louisiana|Maine|Maryland|Massachusetts|Michigan|Minnesota|Mississippi|Missouri|Montana|Nebraska|Nevada|New Hampshire|New Jersey|New Mexico|New York|North Carolina|North Dakota|Ohio|Oklahoma|Oregon|Pennsylvania|Rhode Island|South Carolina|South Dakota|Tennessee|Texas|Utah|Vermont|Virginia|Washington|West Virginia|Wisconsin|Wyoming'.split('|'),
    STATES.values()))


class ConnectorError(ValueError):
    """Invalid source construction: do not estimate or silently repair."""


class ConnectorNotReady(ConnectorError):
    """Park until the specified public-file adapter or population exists."""


@dataclass(frozen=True)
class PanelData:
    rows: tuple[dict[str, Any], ...]
    metadata: tuple[dict[str, Any], ...]
    inventory: tuple[dict[str, Any], ...] = ()
    limitations: tuple[str, ...] = ()
    details: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ConnectorRequest:
    options: Mapping[str, Any]
    filters: Mapping[str, Any]
    years: tuple[int, ...]
    units: tuple[str, ...]
    frequency: str
    base_dir: Path

    @classmethod
    def from_card(cls, card: Mapping[str, Any], base_dir: Path) -> ConnectorRequest:
        from ..prescreen import bind_outcome
        options = bind_outcome(card['outcome'])
        from .rates import resolved_scale
        scale = resolved_scale(card)
        if scale is not None:
            options['outcome_scale'] = scale
        top = card.get('population', {}).get('filters')
        nested = options.get('population_filters')
        if top is not None and nested is not None and top != nested:
            raise ConnectorError('Card population and outcome population_filters disagree')
        filters = dict(nested if nested is not None else top or {})
        period_years = options.get('period_years')
        if period_years is None:
            window = options.get('years')
            if not isinstance(window, list) or len(window) != 2:
                raise ConnectorError('Declare outcome.years [first,last] or period_years')
            period_years = list(range(int(window[0]), int(window[1]) + 1))
        years = tuple(int(y) for y in period_years)
        if not years or len(set(years)) != len(years) or years != tuple(sorted(years)):
            raise ConnectorError('Period years must be unique and increasing')
        units = tuple(options.get('units', sorted(set(STATES.values()) - {'DC'})))
        if not units or len(set(units)) != len(units) or not set(units) <= set(STATES.values()):
            raise ConnectorError('State units must be unique US abbreviations')
        frequency = options.get('frequency', 'annual')
        if frequency not in ('annual', 'monthly', 'biennial'):
            raise ConnectorError('Supported panel frequencies: annual, monthly, biennial')
        return cls(options, filters, years, units, frequency, Path(base_dir).resolve())

    def paths(self) -> tuple[Path, ...]:
        from ..prescreen import bind_outcome
        options = bind_outcome(self.options)
        names = options.get('paths', [])
        if isinstance(names, str):
            names = [names]
        if not names:
            raise ConnectorNotReady('No local public files declared in outcome.paths')
        paths = tuple(self.base_dir / name for name in names)
        expected = options.get('source_sha256', {})
        for name, path in zip(names, paths):
            resolved = path.resolve()
            if (any(part in ('.codex', '.aws', '.env', '.wrangler') for part in resolved.parts)
                    or resolved.name in ('auth.json', 'credentials', 'credentials.json')
                    or resolved.name.startswith('.env')):
                raise ConnectorError('Outcome sources cannot refer to credential/configuration files')
            if not path.is_file():
                raise ConnectorNotReady('Missing declared public file: ' + str(path))
            if name in expected and sha(path) != expected[name]:
                raise ConnectorError('Source hash mismatch: ' + str(path))
        return paths


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(path: Path, request: ConnectorRequest) -> dict[str, Any]:
    return {'path': str(path), 'size_bytes': path.stat().st_size, 'sha256': sha(path),
            'license': request.options.get('license', 'Existing local file; verify source license before final freeze'),
            'url': request.options.get('source_urls', {}).get(str(path.relative_to(request.base_dir)) if path.is_relative_to(request.base_dir) else str(path), '')}


def reject_filters(request: ConnectorRequest, supported: set[str]) -> None:
    unsupported = set(request.filters) - supported
    if unsupported:
        raise ConnectorNotReady('Unsupported population filters: ' + ', '.join(sorted(unsupported)))


def exact_filters(request: ConnectorRequest, expected: Mapping[str, Any]) -> None:
    if dict(request.filters) != dict(expected):
        raise ConnectorNotReady('Published population unavailable: expected ' + repr(dict(expected)))


def row(unit: str, year: int, value: Any, outcome_unit: str, frequency: str = 'annual',
        month: int | None = None, **extra: Any) -> dict[str, Any]:
    period = f'{year}-{month:02d}' if month is not None else str(year)
    return dict(unit=unit, year=year, period=period, frequency=frequency,
                outcome_value=value, outcome_unit=outcome_unit, **extra)


def metadata(record: Mapping[str, Any], available: bool, linkage_certified: bool = True,
             **counts: Any) -> dict[str, Any]:
    return dict(unit=record['unit'], year=record['year'], state=record['unit'],
                period=record['period'], frequency=record['frequency'], available=int(available),
                annual_usable=int(available), linkage_certified=int(linkage_certified), **counts)


def records(path: Path, delimiter: str = ',') -> list[dict[str, str]]:
    with path.open(newline='', encoding='utf-8-sig') as handle:
        return list(csv.DictReader(handle, delimiter=delimiter))


def flag(value: str) -> bool:
    if value in ('0', 'False', 'false'):
        return False
    if value in ('1', 'True', 'true'):
        return True
    raise ConnectorError('Unknown published boolean flag: ' + repr(value))
