"""leak_scan: finds credentials and stray mailboxes in a built folder and never prints a key. Uses an invented key, not the real table."""
import io
from pathlib import Path
import tempfile
import unittest

from scripts.viz_site.tools import leak_scan
from scripts.viz_site.public_page import credential_leaks, prepare_public_page

FAKE_KEY = 'FAKEKEY0123456789abcdefFAKEKEY0123456789'  # invented; same shape as a govinfo key, matches nothing real


class LeakScanTests(unittest.TestCase):
    def folder(self, files):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for name, content in files.items():
            path = Path(tmp.name, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content if isinstance(content, bytes) else content.encode())
        return Path(tmp.name)

    def run_cli(self, root, *extra, key_file=None):
        out = io.StringIO()
        absent = str(Path(root, 'no-such-key-file'))
        code = leak_scan.main([str(root), '--key-file', str(key_file or absent), *extra], out=out)
        return code, out.getvalue()

    def test_clean_folder_passes(self):
        root = self.folder({'a.html': '<p>Contact fangyu@usc.edu. Authorization: none. logo@2x.png</p>', 'b/c.json': '{"url":"https://example.org/a?Session=2019"}'})
        code, text = self.run_cli(root)
        self.assertEqual(code, 0, text)
        self.assertIn('PASS', text)

    def test_api_key_parameter_in_plain_encoded_and_escaped_forms(self):
        for form in ('?api_key=' + FAKE_KEY, '?API_KEY=' + FAKE_KEY, '?api_key%3D' + FAKE_KEY, '?api-key=' + FAKE_KEY, '\\u003fapi_key\\u003d' + FAKE_KEY,
                     '?apikey&#61;' + FAKE_KEY):
            with self.subTest(form=form):
                root = self.folder({'p.html': '<a href="https://api.example.gov/x' + form + '">t</a>'})
                code, text = self.run_cli(root)
                self.assertEqual(code, 1, text)
                self.assertRegex(text, r'api_key\s+1 hit')
                self.assertNotIn(FAKE_KEY, text)
        self.assertEqual(self.run_cli(self.folder({'p.html': 'api_key=short'}))[0], 0)  # fewer than 8 characters

    def test_exact_key_found_without_the_parameter_name_and_in_binary_files_but_never_printed(self):
        aside = self.folder({'table.csv': 'id,detail\n1,"{""fulltext_url"": ""https://api.example.gov/p?api_key=' + FAKE_KEY + '""}"\n'}) / 'table.csv'
        self.assertEqual(leak_scan.load_keys(aside), {FAKE_KEY.encode()})
        root = self.folder({'data/x.json': '{"note":"' + FAKE_KEY + '"}', 'img/a.png': b'\x89PNG\r\n' + FAKE_KEY.encode() + b'\x00', 'ok.txt': 'nothing'})
        code, text = self.run_cli(root, key_file=aside)
        self.assertEqual(code, 1, text)
        self.assertRegex(text, r'exact_key\s+2 hit')
        self.assertIn('data/x.json', text)
        self.assertIn('img/a.png', text)
        self.assertNotIn('ok.txt', text)
        self.assertNotIn(FAKE_KEY, text)
        self.assertIn('1 key value(s) loaded', text)

    def test_key_straddling_a_read_block_is_still_loaded(self):
        padding = 'x' * ((1 << 24) - 20)
        aside = self.folder({'t.csv': padding + '?api_key=' + FAKE_KEY + '\n'}) / 't.csv'
        self.assertEqual(leak_scan.load_keys(aside), {FAKE_KEY.encode()})

    def test_bearer_url_password_and_other_mailboxes(self):
        cases = {'bearer': 'Authorization: Bearer abc123def456ghi789', 'url_password': 'https://reader:hunter2@example.org/x',
                 'email': 'write to someone@example.org'}
        for pattern, text_in in cases.items():
            with self.subTest(pattern=pattern):
                code, text = self.run_cli(self.folder({'p.html': text_in}))
                self.assertEqual(code, 1, text)
                self.assertRegex(text, pattern + r'\s+1 hit')
                self.assertNotIn('hunter2', text)
                self.assertNotIn('someone@example.org', text)
        code, text = self.run_cli(self.folder({'p.html': 'https://x.org/a?password=hunter2'}))
        self.assertEqual(code, 1)
        for ordinary in ('a bearer of news', 'Bearer bonds mature', 'https://example.org/a?x=1', 'https://example.org:8080/a',
                         '<a href="https://legislature.example.gov/testimony/IDEA20210316@OPLA13262365.pdf">', 'logo@2x.png'):
            self.assertEqual(self.run_cli(self.folder({'p.html': ordinary}))[0], 0, ordinary)
        code, text = self.run_cli(self.folder({'p.html': 'mail a@b.org and FANGYU@usc.edu'}), '--show-emails')
        self.assertIn('a@b.org', text)
        self.assertNotIn('usc.edu', text)

    def test_page_builder_refuses_an_api_key(self):
        page = '<html><head></head><body><a href="https://api.example.gov/p?api_key=' + FAKE_KEY + '">text</a></body></html>'
        self.assertEqual(credential_leaks(page), 1)
        with self.assertRaisesRegex(ValueError, 'API key') as caught:
            prepare_public_page(page)
        self.assertNotIn(FAKE_KEY, str(caught.exception))
        self.assertEqual(credential_leaks('<a href="https://x.org/a?api_key=abc">'), 0)
        self.assertEqual(credential_leaks('<a href="https://x.org/a?docId=15&Session=2019">'), 0)
        json_page = '<html><head></head><body><script type="application/json" id="d">{"u":"https://x.org/a?api_key%3D' + FAKE_KEY + '"}</script></body></html>'
        with self.assertRaisesRegex(ValueError, 'API key'):
            prepare_public_page(json_page)


if __name__ == '__main__':
    unittest.main()
