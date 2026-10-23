"""Value-free job summaries rendered from registered inputs, never intake cards."""
from copy import deepcopy
import json
from pathlib import Path


def registered_contract_card(root):
    root = Path(root)
    spec = json.loads((root/'SPEC.json').read_text())
    from .hygiene import inventory_names
    support_name,_=inventory_names(root)
    support = json.loads((root/support_name).read_text()) if (root/support_name).is_file() else {}
    card = {k: deepcopy(spec[k]) for k in
            ('design_id', 'version', 'mechanism', 'track', 'population', 'analysis')
            if k in spec}
    # Screening geometry is pre-S1 evidence, not the registered analysis support.
    card.setdefault('analysis', {})
    card['analysis'].pop('screen_expected_support', None)
    card['analysis'].pop('spec_status', None)
    card['outcome'] = {k: deepcopy(v) for k, v in spec.get('outcome', {}).items()
                       if k in {'years', 'period_years', 'frequency', 'calendar',
                                'outcome_unit', 'outcome_id', 'units', 'population_filters',
                                'series_id'}}
    fields = ('variant', 'comparison_group', 'retained_treated', 'comparison_controls',
              'never_controls', 'cohorts', 'period_years', 'passed', 'support_pass',
              'effective_support_minimums', 'transition_year_drop', 'issues', 'reason')
    card['support'] = {'primary_passed': support.get('primary_passed'),
                       'variants': [{k: deepcopy(v[k]) for k in fields if k in v}
                                    for v in support.get('variants', [])]}
    card['comparison_group'] = spec.get('comparison_group', spec.get('analysis', {}).get('controls'))
    primary = next((v for v in support.get('variants', []) if v['variant'] == 'primary'), {})
    if primary.get('comparison_group', card['comparison_group']) != card['comparison_group']:
        raise ValueError('Registered SPEC/SUPPORT comparison group differs')
    card['registered_metadata_incomplete'] = not bool(spec.get('analysis') and primary)
    return card


def registered_execution_card(root, manifest):
    """One frozen scientific card for S6 and dry-load, including source identity."""
    from .storage import local, sha
    root = Path(root)
    names = [n for n, r in manifest['files'].items() if r['role'] == 'card']
    if len(names) != 1:
        raise ValueError('Exactly one retained card required')
    def bound(name):
        path = local(root, name)
        if name not in manifest['files'] or sha(path) != manifest['files'][name]['sha256']:
            raise ValueError('Registered execution card input changed: '+name)
        return json.loads(path.read_text())
    retained = bound(names[0])
    spec = bound('SPEC.json'); bound('SUPPORT.json')
    registered_contract_card(root)  # Fail closed on SPEC/SUPPORT disagreement.
    # S0 may retain the intake card before audited exclusions. Preparation binds
    # the effective card separately. Compare that independent input with SPEC
    # before rendering; copying SPEC over it would make this check vacuous.
    card = bound('SCIENTIFIC_CARD.json') if 'SCIENTIFIC_CARD.json' in manifest['files'] else retained
    if 'outcome' in card:
        from .connectors.rates import prospective_card
        card = prospective_card(card)
    if spec.get('comparison_group'):
        from .v11_v12 import register_card
        card = register_card(card, spec['comparison_group'])
    fields = ('design_id', 'version', 'track', 'mechanism', 'population', 'analysis',
              'outcome', 'treatment', 'sensitivities', 'transition_year_drop',
              'first_order', 'comparison_group')
    for key in fields:
        if key in spec and card.get(key) != spec[key]:
            raise ValueError('Retained scientific card differs from SPEC: '+key)
    from .variants import secondary_entries
    if secondary_entries(card) != secondary_entries(spec):
        raise ValueError('Retained scientific card differs from SPEC: secondary_outcomes')
    # SPEC's top-level secondary_outcomes is a derived inventory, not a card
    # schema field; the identical declaration lives in first_order.
    return dict(card, _card_path=str(root/names[0]))
