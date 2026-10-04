import collections
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from audit_campaign import AuditError, audit_rows, frozen_scenarios


class AuditTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = tempfile.TemporaryDirectory()
        folder = Path(cls.snapshot.name)
        source = folder / 'frozen/test/campaign_cases.py'
        source.parent.mkdir(parents=True)
        source.write_text((Path(__file__).resolve().parents[1] / 'test/campaign_cases.py').read_text())
        cls.scenarios = staticmethod(frozen_scenarios(folder))

    @classmethod
    def tearDownClass(cls):
        cls.snapshot.cleanup()

    def fixture(self, start=0, count=3, failed=0):
        manifest = {'seed': 20261003, 'revision': 'revision', 'batch_limit': 10000,
                    'stage_budgets': {'calibration': 100}, 'candidates': {'A': {'rtl_sha256': 'rtl'}}}
        digest = hashlib.sha256()
        coverage = collections.Counter()
        failure = None
        for i in range(start, start + count):
            scenario = self.scenarios(manifest['seed'], 'calibration', i)
            self.assertEqual(scenario['category'], 'uart')
            coverage.update(['uart', 'byte:' + str(scenario['byte']), 'duration:' + str(scenario['duration'])])
            ok = not (failed and i == start + count - 1)
            digest.update(json.dumps([i, scenario, ok], sort_keys=True).encode())
            if not ok:
                failure = {'case_id': i, 'seed': manifest['seed'], 'stage': 'calibration', 'scenario': scenario}
        report = {'final': True, 'checked': count, 'passed': count - failed, 'failed': failed,
                  'stage': 'calibration', 'candidate': 'A', 'start': start,
                  'campaign_revision': 'revision', 'rtl_sha256': 'rtl', 'failure': failure,
                  'digest': digest.hexdigest(), 'coverage': dict(coverage)}
        row = ('calibration', 'A', start, count, count - failed, failed, json.dumps(report))
        return manifest, row, report

    def test_valid_failed_case_is_auditable(self):
        manifest, row, _ = self.fixture(failed=1)
        result = audit_rows(manifest, [row], self.scenarios)
        self.assertEqual((result['checked'], result['passed'], result['failed']), (3, 2, 1))
        self.assertEqual(result['eliminated'], ['A'])

    def test_tampered_reports_are_rejected(self):
        manifest, row, report = self.fixture()
        for field, value in [('digest', 'corrupt'), ('coverage', {}), ('rtl_sha256', 'stale'),
                             ('campaign_revision', 'stale'), ('final', False)]:
            with self.subTest(field=field), self.assertRaises(AuditError):
                changed = dict(report, **{field: value})
                audit_rows(manifest, [row[:-1] + (json.dumps(changed),)], self.scenarios)

    def test_gap_and_duplicate_ranges_are_rejected(self):
        manifest, first, _ = self.fixture()
        _, gap, _ = self.fixture(start=4)
        for rows in ([first, gap], [first, first]):
            with self.assertRaises(AuditError):
                audit_rows(manifest, rows, self.scenarios)

    def test_cases_after_disqualification_are_rejected(self):
        manifest, failed, _ = self.fixture(failed=1)
        _, later, _ = self.fixture(start=3)
        with self.assertRaises(AuditError):
            audit_rows(manifest, [failed, later], self.scenarios)


if __name__ == '__main__':
    unittest.main()
