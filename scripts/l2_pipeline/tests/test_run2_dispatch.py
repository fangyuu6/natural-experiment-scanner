import unittest
from unittest.mock import patch
from scripts.l2_pipeline.run2_dispatch import main


class DispatchAliasTests(unittest.TestCase):
    def test_repeatable_design_alias_keeps_host_and_reservation_guards(self):
        args = ['--jobs-root', 'data/l2_pipeline/jobs/run2/reaudits', '--stage', 'S1',
                '--design-id', 'run2_one', '--design-id=run2_two', '--dry-run']
        with patch('scripts.l2_pipeline.agents.dispatch.main', return_value=0) as dispatch:
            self.assertEqual(main(args), 0)
            dispatch.assert_called_once_with([
                '--jobs-root', 'data/l2_pipeline/jobs/run2/reaudits', '--stage', 'S1',
                '--design', 'run2_one', '--design=run2_two', '--dry-run'])
