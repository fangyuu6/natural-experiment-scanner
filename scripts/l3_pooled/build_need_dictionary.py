#!/usr/bin/env python3
"""Build topic/description-only need candidates; no adoption or covariate values read.

python3 scripts/l3_pooled/build_need_dictionary.py [--check]
Outputs remain metadata candidates. This does not certify historical release vintages.
"""
import argparse
import csv
import io
import re
from collections import Counter
from pathlib import Path

from dta_reader import rows

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/l3_pooled'
SRC = OUT / 'downloads'


def read(path, delimiter=','):
    with path.open(newline='') as stream:
        return list(csv.DictReader(stream, delimiter=delimiter))


def render(records, fields):
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=fields)
    writer.writeheader()
    writer.writerows(records)
    return stream.getvalue().encode('utf-8')


def build():
    rules = read(ROOT / 'scripts/l3_pooled/need_rules.csv')
    assert len({r['rule_id'] for r in rules}) == len(rules)
    assert len({int(r['priority']) for r in rules}) == len(rules)
    rules.sort(key=lambda rule: int(rule['priority']))
    for rule in rules:
        re.compile(rule['match_regex'])
        int(rule['count_lag_years'])
        assert rule['defensibility'] in {'candidate_objective_need', 'no_defensible_need_indicator'}
    fields = ['policy', 'topic', 'description'] + [f for f in rules[0] if f != 'topic']

    def classify(policy, topic, description):
        matched = [r for r in rules if r['topic'] in {topic, '*'}
                   and re.search(r['match_regex'], description, re.IGNORECASE)]
        assert matched, (policy, topic)
        rule = matched[0]
        return dict(policy=policy, topic=topic, description=description,
                    **{k: v for k, v in rule.items() if k != 'topic'})

    native = read(SRC / 'SPID_v1.2_policies.tab', '\t')
    assert len(native) == 724 and len({p['policy'] for p in native}) == 724
    native_ids = {p['policy'] for p in native}
    native_dictionary = [classify(p['policy'], p['majortopic_str'], p['description']) for p in native]
    author_dictionary = []
    for bundle in ['SPID', 'NBER']:
        definitions = {}
        for record in rows(SRC / f'DVKim_{bundle}_iqt.dta',
                           ['policy_num', 'policy', 'policy_description', 'policyarea']):
            number = record['policy_num']
            assert number not in definitions or definitions[number] == record
            definitions[number] = record
        for number, record in sorted(definitions.items()):
            if bundle == 'SPID' and record['policy'] in native_ids:
                continue  # matched IDs inherit native dictionary in readiness crosswalk
            author_dictionary.append(dict(author_id=f'dv_{bundle.lower()}:{number}', bundle=bundle,
                                          **classify(record['policy'], record['policyarea'],
                                                     record['policy_description'])))
    assert len(author_dictionary) == 59
    return {
        OUT / 'need_dictionary_v0.csv': render(native_dictionary, fields),
        OUT / 'author_need_dictionary_v0.csv': render(author_dictionary, ['author_id', 'bundle'] + fields),
    }, native_dictionary, author_dictionary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='verify byte-for-byte without writing')
    args = parser.parse_args()
    outputs, native, author = build()
    for path, body in outputs.items():
        if args.check:
            assert path.read_bytes() == body, f'Stale dictionary: {path}'
        else:
            path.write_bytes(body)
    print({'mode': 'check' if args.check else 'build', 'native_rows': len(native),
           'author_one_sided_rows': len(author),
           'native_classes': dict(Counter(r['defensibility'] for r in native)),
           'author_classes': dict(Counter(r['defensibility'] for r in author)),
           'released_vintage_state_years_certified': 0})


if __name__ == '__main__':
    main()
