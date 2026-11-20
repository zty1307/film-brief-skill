"""Guard the period and integrity checks at the cleaning-to-propagation boundary."""
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))

from report_handoff import digest, read_handoff


class HandoffTests(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.base = Path(temp.name)
        self.word = self.base / 'opinion.docx'
        self.word.write_bytes(b'delivered docx')
        dataset = self.base / 'final_dataset.json'
        self.write(dataset, {'target': '测试剧', 'groups': [{'samples': [{'source': {
            'source_path': '舆论观点-2026.10.24 22_00至2026.10.25 11_27-微博.xlsx'}}]}]})
        self.status = {'stage': 'COMPLETE', 'word': self.word.name,
                       'target': '测试剧', 'review_state': 'AI_PREVIEW',
                       'delivery_manifest': 'delivery_manifest.json'}
        self.write(self.base / 'workflow_status.json', self.status)
        self.write(self.base / 'delivery_manifest.json', {
            'target': '测试剧', 'review_state': 'AI_PREVIEW',
            'artifacts': [{'kind': 'word', 'path': self.word.name, 'sha256': digest(self.word)}],
            'internal_artifacts': [{'path': dataset.name, 'sha256': digest(dataset)}],
            'dataset_sha256': digest(dataset),
        })
        self.spec = {'workspace': '.', 'issue_date': '2026-10-25',
                     'sample_window': {'start': '2026-10-24 22:00:00', 'end': '2026-10-25 11:27:59'}}

    @staticmethod
    def write(path, data):
        path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')

    def test_only_completed_word_for_the_same_period_is_accepted(self):
        self.assertEqual(read_handoff(self.spec, self.base, '测试剧')['review_state'], 'AI_PREVIEW')
        with self.assertRaisesRegex(ValueError, '结束日期'):
            read_handoff(dict(self.spec, issue_date='2026-10-26'), self.base, '测试剧')
        with self.assertRaisesRegex(ValueError, '原表导出窗口'):
            read_handoff(dict(self.spec, sample_window=dict(self.spec['sample_window'],
                         end='2026-10-25 12:27:59')), self.base, '测试剧')
        self.write(self.base / 'workflow_status.json', dict(self.status, stage='awaiting_selection'))
        with self.assertRaisesRegex(ValueError, '尚未交付'):
            read_handoff(self.spec, self.base, '测试剧')

    def test_edited_delivery_is_rejected(self):
        self.word.write_bytes(b'changed after approval')
        with self.assertRaisesRegex(ValueError, '哈希'):
            read_handoff(self.spec, self.base, '测试剧')
