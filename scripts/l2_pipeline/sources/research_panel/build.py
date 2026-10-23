"""Rebuild only source policies, coverage counts and answer-free S1 audit jobs."""
from __future__ import annotations
import json
from collections import Counter
from pathlib import Path

from ...contracts import DesignCard, StageContext
from .core import DATA, REPO, STATES, canonicalize, digest, owned, write_json, write_panel
from .s1 import run


def build(output=DATA, *, jobs=True):
    from . import welfare, firearms, cspp
    output = owned(output)
    variables, receipts, panels = [], [], []
    for adapter, module in (('welfare', welfare), ('firearms', firearms), ('cspp', cspp)):
        rows, definitions, files = module.load()
        for definition in definitions:
            if definition['source_id'] == 'siegel_firearms':
                definition['historical_year_end'] = 2025
            if definition['source_id'] == 'vz':
                definition['historical_year_start'] = 1975
        canonical, metadata = canonicalize(rows, definitions)
        path = output / 'panels' / (adapter + '.csv')
        write_panel(path, canonical)
        panels.append(dict(adapter=adapter, path=str(path.resolve()), sha256=digest(path),
            size_bytes=path.stat().st_size, canonical_rows=len(canonical),
            policy_ids=[m['policy_id'] for m in metadata]))
        variables.extend(metadata)
        for receipt in files:
            receipt = dict(receipt)
            if 'source_id' not in receipt:
                receipt['source_id'] = 'nber_taxsim' if Path(receipt['path']).name == 'taxsim_page.html' else 'cspp'
            path = Path(receipt['path'])
            if not path.is_absolute():
                path = REPO / path
            if digest(path) != receipt['sha256'] or path.stat().st_size != receipt['size_bytes']:
                raise ValueError('Source file differs from receipt: ' + str(path))
            receipt['path'] = str(path.resolve())
            receipts.append(receipt)
    limitations = {}
    for adapter, filename in (('firearms', 'access_notes.json'), ('cspp', 'feasibility.json')):
        path = DATA / 'raw' / adapter / filename
        if path.exists():
            limitations[adapter] = json.loads(path.read_text())
    limitations['ukcpr'] = 'Current workbook lacks TANF time limits, SNAP rules, CHIP eligibility and UI parameters. Fiscal/calendar clocks and TANF definition break need variable-specific audit.'
    catalog = dict(schema_version='research_panel_catalog_v1', source_type='research_panel',
        scope='Local free no-account research use; no publication or redistribution permission inferred',
        universe=list(STATES), variables=variables, files=receipts, panels=panels,
        source_limitations=limitations, registration='scripts.l2_pipeline.sources.research_panel.registration:run_stage',
        policy_definition_notes='Binary adoption denotes a source-defined policy-presence stock; 1 may code a restriction. Adoption is an observed 0→1 transition, never a legislative decision date.',
        coverage_notes='Rectangular keys include explicit missing values; complete_50_state_windows counts nonmissing source cells. Only historical_complete windows exclude known partial/future years. Official legal certification remains pending.',
        reversal_handling='Preserve all stock transitions; ever-reversed states excluded from primary absorbing DiD and never return to never-treated controls.',
        continuous_rule='Park by default; external threshold declaration hash, units, operator, rationale and outcome-free declaration required.',
        audit_rule='Deterministic import, zero A/B coder jobs, seed-locked ten eligible treated-state official audit; no source-based auto-readiness.',
        outcome_association_calls=0, estimation_calls=0)
    cp = write_json(output / 'CATALOG.json', catalog)
    report = dict(variables=len(variables), types=dict(Counter(v['kind'] for v in variables)),
        canonical_rows=sum(p['canonical_rows'] for p in panels), downloads=len(receipts),
        panel_sha256s={p['adapter']: p['sha256'] for p in panels},
        outcome_association_calls=0, estimation_calls=0, coder_jobs=0, auditor_jobs=0, dispositions=[])
    if jobs:
        for variable in variables:
            if variable['kind'] != 'binary_adoption':
                continue
            windows = variable['coverage']['historical_complete_50_state_windows']
            if not windows:
                report['dispositions'].append(dict(policy_id=variable['policy_id'], disposition='parked', reason='No complete 50-state historical window'))
                continue
            # Prospective rule: longest complete historical run, earliest on tie;
            # neither outcomes nor the number/sign of events select the scope.
            window = min(windows, key=lambda w: (-(w[1] - w[0] + 1), w[0]))
            panel = next(p for p in panels if variable['policy_id'] in p['policy_ids'])
            design_id = variable['policy_id'].replace(':', '_')
            card_dir = output / 'intake' / design_id
            cfg = dict(source_type='research_panel', universe=list(STATES), seed=51009,
                policy_id=variable['policy_id'], predicate=variable['definition'], window=window,
                catalog_path=str(cp), catalog_sha256=digest(cp), panel_path=panel['path'], panel_sha256=panel['sha256'])
            # An S1-only intake contract. It grants no S0 or study specification.
            value = dict(design_id=design_id, version='v1', policy_family=variable['policy_id'],
                mechanism=variable['definition'], track='DiD', unit='state', outcome_family='purpose_verification_pending',
                treatment=cfg, first_order_outcome_candidates=variable['first_order_outcome_candidates'],
                study_card_status='S1_ONLY_INTAKE_NOT_A_FROZEN_STUDY', selection_rule='longest_complete_historical_run_earliest_tie')
            card_path = write_json(card_dir / 'S1_CARD.json', value, immutable=True)
            ctx = StageContext(DesignCard.from_mapping(value, card_path), output / 'audit_work', REPO)
            result = run(ctx)
            rp = write_json(ctx.design_dir / 'S1_PREPARATION.json', result.as_dict())
            report['auditor_jobs'] += result.metrics.get('auditor_jobs', 0)
            report['dispositions'].append(dict(policy_id=variable['policy_id'], window=window,
                disposition=result.disposition.value, reason=result.reason, sample_size=result.metrics.get('sample_size', 0),
                preparation_path=str(rp)))
    write_json(output / 'BUILD_REPORT.json', report)
    with (output / 'DOWNLOAD_REPORT.md').open('w') as f:
        f.write('Local research downloads/copies; licenses are recorded as stated, not inferred. No upload or redistribution.\n\n')
        f.write('| File | URL | Bytes | License | SHA-256 |\n|---|---|---:|---|---|\n')
        for r in receipts:
            f.write('| ' + ' | '.join(str(x).replace('|', '&#124;').replace('\n', ' ') for x in
                (r['path'], r['url'], r['size_bytes'], r['license'], r['sha256'])) + ' |\n')
    return report
