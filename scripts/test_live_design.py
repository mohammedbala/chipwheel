"""Check live source changes and provenance without touching real design files."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from live_design import snapshot, digest

ROOT = Path(__file__).resolve().parents[1]

class LiveDesignTest(unittest.TestCase):
    def test_source_and_result_progress(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for folder in ('src', 'results', 'docs', 'programs', 'simulator'):
                (root / folder).mkdir()
            for file in ('src/project.v','src/config.json','info.yaml','results/final-short.json'):
                shutil.copyfile(ROOT/file, root/file)
            baseline = snapshot(root)
            self.assertEqual(baseline['architecture']['words'],32)
            self.assertTrue(baseline['model_matches'])
            self.assertTrue(baseline['stages'][0]['current'])
            rtl = root/'src/project.v'
            rtl.write_text(rtl.read_text().replace('program_mem [0:31]', 'program_mem [0:15]'))
            changed = snapshot(root)
            self.assertEqual(changed['architecture']['words'],16)
            self.assertNotEqual(changed['revision'],baseline['revision'])
            self.assertFalse(changed['model_matches'])
            self.assertFalse(changed['stages'][0]['current'])
            report = root/'results/final-short.json'
            d = json.loads(report.read_text())
            d['rtl_sha256'] = digest(rtl)
            d['checked_transactions'] = 1234
            report.write_text(json.dumps(d))
            verified = snapshot(root)
            self.assertTrue(verified['stages'][0]['current'])
            self.assertIn('1,234',verified['stages'][0]['detail'])
            self.assertNotEqual(changed['revision'],verified['revision'])

    def test_missing_files_are_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            d = snapshot(Path(directory))
            self.assertIsNone(d['architecture']['words'])
            self.assertFalse(d['model_matches'])
            self.assertTrue(all(s['status']=='pending' for s in d['stages']))

if __name__ == '__main__':
    unittest.main()
