"""T190 (2026-10-20): the LAUS ratio connector must read its numerator/denominator files from a card that lives in a
subdirectory of the repository (base_dir = card directory, paths bound absolute by bind_outcome), and keep checking
their hashes."""
import unittest
from unittest.mock import patch
from pathlib import Path

from scripts.l2_pipeline.connectors.common import ConnectorError, ConnectorRequest, sha
from scripts.l2_pipeline.connectors import c46_core
from scripts.l2_pipeline.tests.test_named_panel_20261016 import RatioFixture, api_file


class RatioFromCardDirectoryTests(RatioFixture):
    def layout(self):
        self.repo = self.base
        self.card_dir = self.repo / 'cards' / 'x'
        (self.repo / 'data').mkdir()
        self.card_dir.mkdir(parents=True)
        api_file(self.repo / 'data/emp.json', '05', self.emp)
        api_file(self.repo / 'data/pop.json', '09', self.pop)
        spec = dict(series_id=self.v17.RATIO_ID, years=list(self.YEARS), numerator_paths=['data/emp.json'],
                    denominator_paths=['data/pop.json'], fallback_paths=[])
        data = dict(series={self.v17.RATIO_ID: spec})
        absolute = [str(self.repo / n) for n in ('data/emp.json', 'data/pop.json')]
        return data, absolute, {p: sha(Path(p)) for p in absolute}

    def request_in_card_dir(self, names, hashes):
        opts = dict(connector='outcome_v17_' + self.v17.RATIO_ID, series_id=self.v17.RATIO_ID, measure_id=self.v17.RATIO_ID,
                    paths=names, source_sha256=hashes, population_filters={}, frequency='annual', license='public')
        return ConnectorRequest(opts, {}, tuple(self.YEARS), self.UNITS, 'annual', self.card_dir)

    def test_absolute_bound_paths_with_card_directory_base(self):
        data, names, hashes = self.layout()
        with patch.object(c46_core, 'REPO', self.repo):
            panel = self.construct(data, self.request_in_card_dir(names, hashes))
        rows = self.values(panel)
        self.assertEqual(len(rows), 4)
        self.assertAlmostEqual(rows[('AL', 2000)]['outcome_value'], 100 * 1000 / 2000)

    def test_hash_of_the_subrequest_files_is_still_checked(self):
        data, names, hashes = self.layout()
        bad = dict(hashes, **{names[0]: '0' * 64})
        with patch.object(c46_core, 'REPO', self.repo):
            with self.assertRaisesRegex(ConnectorError, 'hash mismatch'):
                self.construct(data, self.request_in_card_dir(names, bad))


if __name__ == '__main__':
    unittest.main()
