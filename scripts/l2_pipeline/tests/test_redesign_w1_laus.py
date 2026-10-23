import unittest
from pathlib import Path
from types import SimpleNamespace

from scripts.l2_pipeline.connectors import c46_core, outcome_v17


class RelativePathTests(unittest.TestCase):
    def test_repo_relative_when_base_dir_is_elsewhere(self):
        request = SimpleNamespace(base_dir=Path('/nonexistent/elsewhere'))
        path = c46_core.REPO / 'data' / 'x' / 'file.csv'
        self.assertEqual(outcome_v17._relative(request, path), 'data/x/file.csv')

    def test_base_dir_wins_when_it_contains_the_path(self):
        request = SimpleNamespace(base_dir=c46_core.REPO / 'data')
        self.assertEqual(outcome_v17._relative(request, c46_core.REPO / 'data' / 'a.csv'), 'a.csv')

    def test_outside_both_roots_stays_absolute(self):
        request = SimpleNamespace(base_dir=Path('/nonexistent/elsewhere'))
        self.assertEqual(outcome_v17._relative(request, Path('/other/a.csv')), '/other/a.csv')


if __name__ == '__main__':
    unittest.main()
