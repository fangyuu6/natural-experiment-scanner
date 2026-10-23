"""Preview installation preserves all existing and staged files (owner rule)."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import shutil

from scripts.viz_site.build_v5 import install_preview


class PreviewInstallTests(unittest.TestCase):
    def exercise(self, fail_install=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            dest, staged = root / 'preview', root / 'staged'
            dest.mkdir(); staged.mkdir()
            for name, value in [('index.html','old'), ('old.html','old-only'), ('build_v5.json','old receipt')]:
                (dest / name).write_text(value)
            for name, value in [('index.html','new'), ('new.html','new-only'), ('build_v5.json','new receipt')]:
                (staged / name).write_text(value)
            copy = shutil.copy2
            def copying(source, target, **kwargs):
                if fail_install and Path(source) == staged / 'new.html':
                    raise OSError('interrupted installation')
                return copy(source, target, **kwargs)
            with patch('scripts.viz_site.build_v5.shutil.copy2', copying), \
                 patch.object(Path, 'rename', side_effect=AssertionError('move prohibited')), \
                 patch.object(Path, 'unlink', side_effect=AssertionError('delete prohibited')):
                if fail_install:
                    with self.assertRaisesRegex(OSError, 'interrupted'):
                        install_preview(staged, dest)
                else:
                    install_preview(staged, dest)
            backup, = root.glob('preview-previous-*')
            self.assertEqual((backup / 'index.html').read_text(), 'old')
            self.assertEqual((dest / 'index.html').read_text(), 'old' if fail_install else 'new')
            self.assertEqual((dest / 'build_v5.json').read_text(), 'old receipt' if fail_install else 'new receipt')
            self.assertEqual((dest / 'old.html').read_text(), 'old-only')
            self.assertTrue((staged / 'new.html').exists())

    def test_backup_and_copy_without_moves_or_deletion(self):
        self.exercise()

    def test_failed_install_restores_previous_preview(self):
        self.exercise(fail_install=True)
