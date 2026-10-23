"""Pre-adoption and invented-world calibration; no treated-post parsing.

Counts, seeds and estimand operators retain the frozen Protocol v2 defaults.
Orchestration creates preparatory/final freezes and write-once sentinels.
"""
from __future__ import annotations
import copy, csv, hashlib, math
from pathlib import Path
from typing import Sequence
from . import estimator as engine
from .structure import primary_structural_design
from .design import build_design, structural_rows, treatment_rows
from .. import gate_relax
from .ri import support_failure, inventory, require_feasible, predraw_labels, relabeled_design
from .simulation import COUNTS, GRID, OFFSETS, SEED, invented_vectors, project, values, rejected, measured, null_critical, wilson


def blind_anchor(panel_path: str | Path, *, units: Sequence[str],
                 periods: Sequence[int], earliest_operative_period: int,
                 value_column: str, lower_bound: float | None = None,
                 upper_bound: float | None = None) -> dict:
    """Read a complete pooled rectangle strictly before every operative onset.

    Unit/year prefixes are projected before CSV parsing, so unselected numeric
    tails remain opaque. No treatment-status grouping or association is fit.
    """
    units, periods = sorted(set(units)), sorted(set(periods))
    if len(units) < 2 or len(periods) < 2 or max(periods) >= earliest_operative_period:
        raise ValueError('Anchor requires N,T>=2 strictly before operative onset')
    cells = {}
    with Path(panel_path).open(newline='') as handle:
        header = next(csv.reader([handle.readline()]))
        from .panels import key_columns, key_from_line
        ui, yi = key_columns(header)
        if value_column not in header:
            raise ValueError('Anchor needs named unit/year keys and declared outcome column')
        for line in handle:
            unit, period = key_from_line(line, ui, yi)
            if unit not in units or period not in periods:
                continue
            fields = next(csv.reader([line], strict=True))
            if len(fields) != len(header):
                raise ValueError('Malformed anchor row')
            key = unit, period
            if key in cells:
                raise ValueError('Duplicate anchor key')
            value = float(fields[header.index(value_column)])
            if (not math.isfinite(value) or (lower_bound is not None and value < lower_bound)
                    or (upper_bound is not None and value > upper_bound)):
                raise ValueError('Invalid registered anchor value')
            cells[key] = value
    if set(cells) != {(u, t) for u in units for t in periods}:
        raise ValueError('Incomplete anchor rectangle')
    n, t = len(units), len(periods)
    all_mean = sum(cells.values()) / (n * t)
    means_u = {u: sum(cells[u, y] for y in periods) / t for u in units}
    means_t = {y: sum(cells[u, y] for u in units) / n for y in periods}
    sigma = math.sqrt(sum((v - means_u[u] - means_t[y] + all_mean) ** 2
                         for (u, y), v in cells.items()) / ((n - 1) * (t - 1)))
    if not math.isfinite(sigma) or sigma <= 0:
        raise ValueError('Anchor must have positive finite scale')
    return dict(value=sigma, included_units=units, years=periods, N=n, T=t, cells=n*t,
                earliest_operative_year=earliest_operative_period,
                panel_sha256=hashlib.sha256(Path(panel_path).read_bytes()).hexdigest(),
                outcome_values_converted=n*t, treated_post_outcome_values_converted=0,
                actual_data_estimation_calls=0,
                formula='sqrt(sum((y_it-mean_i-mean_t+mean_all)^2)/((N-1)*(T-1)))')


def calibrate_synthetic(case_id: str, op: dict, sigma: float, *,
                        counts: dict | None = None, include_power: bool = True) -> dict:
    """Invented Gaussian paths under registered topology; no source file access.

    Default counts are Protocol v2. Explicit smaller counts support unit tests;
    the stage gate must bind the registered counts before using this record.
    """
    if not math.isfinite(sigma) or sigma <= 0:
        raise ValueError('Positive finite pre-adoption anchor required')
    counts = dict(COUNTS if counts is None else counts)
    if set(counts) != set(COUNTS) or any(type(v) is not int or v < 1 for v in counts.values()):
        raise ValueError('Incomplete or invalid registered calibration counts')
    require_feasible(op['ri_feasibility'])
    if op['ri_feasibility']['requested_draws'] != counts['RI']:
        raise ValueError('Calibration RI operator inventory differs from registered count')
    train = values(project(op, invented_vectors(case_id, op, sigma, 'training', counts['training'])))
    critical = {k: null_critical(v, k, .01) for k, v in train.items()}
    evaluation = invented_vectors(case_id, op, sigma, 'evaluation', counts['heldout'])
    clean_values = values(project(op, evaluation))
    clean = measured(rejected(clean_values, critical))
    clean_pass = 1-clean['full_blocking_set_rejection_rate']
    targets_met = clean_pass >= .8 and all(r['rejection_rate'] <= .05 for r in clean['per_check'].values())
    from .inference import design_binding
    record = dict(schema='l2_pipeline_calibration_v1', case_id=case_id, counts=counts,
                  design_binding=design_binding(op['design']),
                  seeds=dict(base=SEED, offsets=OFFSETS, RI=engine.RI_SEED),
                  noise_scale=sigma, clean_effect=0., critical_values=critical,
                  training_tail_probability=.01, training=measured(rejected(train, critical)),
                  tested_pre_event_times=op['family'], ri_feasibility=op['ri_feasibility'],
                  heldout=dict(clean=clean), clean_pass_rate=clean_pass,
                  targets_met=targets_met, status='ready' if targets_met else 'calibration_blocked',
                  reason=None if targets_met else 'Frozen held-out 80%/5% target missed; no retuning.',
                  actual_data_estimation_calls=0, treated_post_outcome_fields_parsed=0,
                  disclaimer='Model-bound simulation results; no real-data detection-power claim')
    record['per_check'] = {k: dict(false_rejection_rate=r['rejection_rate'],
                                   denominator=counts['heldout'], monte_carlo_ci95=r['monte_carlo_ci95'])
                           for k, r in clean['per_check'].items()}
    covered = 0
    for vector in evaluation[:, 0, :]:
        fitted = engine.estimate_design(op['design'], dict(zip(op['keys'], vector)), B=counts['B'], seed=SEED)
        lo, hi = fitted['overall']['ci95']
        covered += lo <= 0 <= hi
    record['coverage'] = dict(covered=int(covered), repetitions=counts['heldout'],
                              rate=covered/counts['heldout'], monte_carlo_ci95=wilson(covered, counts['heldout']),
                              descriptive=True, method='Gaussian unit multiplier point interval')
    record['synthetic_executor_calls'] = counts['heldout']
    if include_power:
        powers = invented_vectors(case_id, op, sigma, 'power', counts['power'])
        record['power_tables'], record['mdd90'] = {}, {}
        for world, shape in [('linear_pretrend', op['drift']), ('anticipation_jump', op['jump'])]:
            table = []
            for relative in GRID:
                signs = {str(sign): measured(rejected(values(project(op, powers+sign*relative*sigma*shape)), critical))
                         for sign in (-1, 1)}
                table.append(dict(magnitude=relative*sigma, magnitude_over_s=relative, signs=signs,
                                  weaker_sign_power=min(r['full_blocking_set_rejection_rate'] for r in signs.values()),
                                  per_check_weaker_sign_power={k: min(r['per_check'][k]['rejection_rate'] for r in signs.values())
                                                              for k in engine.CHECKS}))
            record['power_tables'][world] = table
            record['mdd90'][world] = {}
            for check in ('full_blocking_set',)+engine.CHECKS:
                detected = next((r['magnitude'] for r in table if
                    (r['weaker_sign_power'] if check == 'full_blocking_set' else r['per_check_weaker_sign_power'][check]) >= .9), None)
                record['mdd90'][world][check] = dict(value=detected,
                    status='grid_detected' if detected is not None else 'range_exceeded', max_examined=10*sigma)
            for sign in (-1, 1):
                shifted = evaluation+sign*sigma*shape
                name = world+('/negative' if sign < 0 else '/positive')
                record['heldout'][name] = measured(rejected(values(project(op, shifted)), critical))
                # Bind held-out world statistics to the actual numerical
                # executor as the frozen batch calibration did (500 calls).
                for vector in shifted[:, 0, :]:
                    engine.estimate_design(op['design'], dict(zip(op['keys'], vector)),
                                           B=counts['B'], seed=SEED)
                record['synthetic_executor_calls'] += counts['heldout']
    return record


def evaluate_intervals(case_id: str, op: dict, sigma: float, *,
                       repetitions: int = 1000, B: int = 4999,
                       coverage_min: float = .93, heldout: int = 100) -> dict:
    """Independent invented worlds; small test runs cannot certify production.

    The first 100 independently seeded evaluation worlds are retained as the
    registered held-out evidence. Coverage/width gates use all 1000 worlds.
    Candidate algebra failures are separate records with the full denominator.
    """
    from .inference import (geometry, draws, outcome_matrix, candidates, METHODS,
        INTERVAL_COUNTS, COVERAGE_MIN, design_binding, object_sha256, interval_summaries)
    if (type(repetitions) is not int or repetitions < 1 or type(B) is not int or B < 2
            or type(heldout) is not int or heldout < 1 or heldout > repetitions):
        raise ValueError('Invalid registered interval simulation count')
    if not math.isfinite(sigma) or sigma <= 0 or not 0 < coverage_min <= 1:
        raise ValueError('Invalid registered interval anchor/coverage target')
    g = geometry(op['design'])
    seed=SEED+4000000
    d = draws(g, B, seed)
    vectors = invented_vectors(case_id, op, sigma, 'evaluation', repetitions)[:, 0, :]
    records = []
    for rep, vector in enumerate(vectors):
        rec = candidates(g, outcome_matrix(g, dict(zip(op['keys'], vector))), d)
        record=dict(repetition=rep,att=rec['att'])
        for name in ('baseline',)+METHODS:
            ci=rec[name]
            record[name]=(dict(status='ok',ci95=ci,covered=bool(ci[0]<=0<=ci[1]),width=ci[1]-ci[0])
                if ci is not None else dict(status='failed',reason=rec['failures'][name]))
        records.append(record)
    heldout_records=records[:heldout]
    counts=dict(repetitions=repetitions,B=B,heldout=heldout)
    return dict(schema='l2_interval_evaluation_v1',case_id=case_id,
        counts=counts,coverage_min=coverage_min,seed=seed,world_stage='evaluation',
        production_ready=counts==INTERVAL_COUNTS and coverage_min==COVERAGE_MIN,
        **design_binding(op['design']),
        summaries=interval_summaries(records,coverage_min),world_records=records,
        heldout_world_records=heldout_records,heldout_summaries=interval_summaries(heldout_records,coverage_min),
        world_sha256=hashlib.sha256(vectors.tobytes()).hexdigest(),
        world_records_sha256=object_sha256(records),heldout_world_records_sha256=object_sha256(heldout_records),
        actual_data_estimation_calls=0,treated_post_outcome_fields_parsed=0)


def certify_intervals(cases: dict, *, expected_designs, ri_floor_designs=()) -> dict:
    """Assemble and verify a full production certificate, including failures.

    R3.1: when any design in `ri_floor_designs` is in the inventory, no certificate may fall back to point+RI p.
    """
    from .inference import METHODS, select_method, validate_certification
    expected=list(expected_designs)
    selected=select_method(cases,expected_designs=expected)
    if selected is None and set(ri_floor_designs)&set(expected):
        raise ValueError('NOT_ASSESSABLE: R3.1 forbids a headline from the randomization p alone')
    evaluation=dict(schema='l2_interval_certification_v1',candidate_order=list(METHODS),
        expected_designs=expected,cases=copy.deepcopy(cases),
        selected_method=selected)
    validate_certification(evaluation,expected_designs=expected)
    return evaluation

def profiles_for(declaration, units, periods):
    if declaration.get('exposure'):
        return declaration['exposure']
    return {u: {t: {'clean': u not in declaration['operative_years'] or t < declaration['operative_years'][u],
                    'full': u in declaration['cohorts'] and t >= declaration['cohorts'][u]}
                for t in periods} for u in units}

def calibration_operators(declaration, structure, treatment, permutations=199):
    """Bind exact supported primary forms and fixed-mask label permutations."""
    import numpy as np
    case=declaration['case']; units=case['sample_mask']['include_units']
    periods=sorted({r[1] for r in structure})
    # Callers supply normalized rows. Recover calendar labels before the shared
    # constructor, which owns comparison groups, exposure and exact masks.
    calendar=declaration.get('period_years')
    rows=[(u,calendar[t] if calendar else t,y,g) for u,t,y,g in structure]
    design=build_design(declaration,rows)
    units=design['units']; periods=design['periods']; keys=sorted(design['mask']); index={k:i for i,k in enumerate(keys)}
    def vector(weights):
        v=np.zeros(len(keys))
        for k,w in weights.items(): v[index[k]]+=w
        return v
    def pre(d):
        ops=engine.event_operators(d); family=sorted(e for e,r in ops.items() if e<0 and r['n_treated']>=5)
        if not family: raise ValueError('NOT_ASSESSABLE: empty supported pre family')
        return family,np.array([vector(ops[e]['weights']) for e in family])
    family,p=pre(design)
    ri=[]; permutations_overall=[]; draw_records=[]
    def overall(d):
        cells=[c for c in d['cells'] if c['post']]; mass=sum(c['n_treated'] for c in cells)
        return sum(c['n_treated']/mass*vector(engine.contrast_weights(c)) for c in cells)
    # Use execution's complete label bundles, including registered never-pool
    # membership. A retained future adopter can have no in-window cohort while
    # still being ineligible as a never control under every relabeling.
    for shuffled in predraw_labels(design, permutations):
        try:
            fake=relabeled_design(declaration,design,shuffled)
            f,m=pre(fake)
            if f!=family: raise ValueError('RI supported family changes under relabeling; no redraw permitted')
            post=overall(fake)
            ri.append(m); permutations_overall.append(post)
        except ValueError as exc:
            if not support_failure(exc): raise
            draw_records.append(dict(infeasible=True,reason=str(exc)))
        else: draw_records.append(dict(infeasible=False))
    feasibility=inventory(draw_records, floor=gate_relax.floor_from_declaration(declaration))
    if permutations: require_feasible(feasibility)
    placebo_record=engine.placebo_operator(design)
    if not placebo_record['mass']: raise ValueError('NOT_ASSESSABLE: empty supported placebo')
    ui={u:i for i,u in enumerate(units)}; ti={t:i for i,t in enumerate(periods)}
    drift=np.array([t-periods[0] if design['adoption'][u] in design['groups'] else 0. for u,t in keys])
    jump=np.array([float(t==design['cohort_bases'].get(u)) if design['adoption'][u] in design['groups'] else 0. for u,t in keys])
    return dict(design=design,keys=keys,pre=p,ri=np.array(ri),placebo=vector(placebo_record['weights']),
        placebo_support=placebo_record,overall=overall(design),ri_overall=np.array(permutations_overall),
        ri_feasibility=feasibility,family=family,units=units,periods=periods,indices=([ui[u] for u,t in keys],[ti[t] for u,t in keys]),drift=drift,jump=jump)
