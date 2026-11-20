from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from node_search_pipeline import extraction_pages, validate_sample


class NodeActivityTests(unittest.TestCase):
    def setUp(self):
        self.date = '2026-10-25'
        self.cutoff = '2026-10-25 21:00'
        self.published_at = '2026-10-24 20:35:04'
        self.quote = '直播互动话题仍在榜，下榜时间：2026-10-25 02:05:00'
        self.header = '发布时间：' + self.published_at
        self.page = {
            'fetch_id': 'page1', 'url': 'https://example.org/topic',
            'body': self.header + '\n' + self.quote,
            'body_truncated': False,
        }
        self.sample = {
            'fetch_id': 'page1', 'published_at': self.published_at,
            'published_at_evidence': self.header, 'event_date': '2026-10-24',
            'activity_date': self.date, 'activity_at': '2026-10-25 02:05:00',
            'text': self.quote,
        }

    def test_cross_midnight_topic_is_eligible(self):
        pages, rejected = extraction_pages([self.page], self.date, self.cutoff)
        self.assertFalse(rejected)
        self.assertEqual(validate_sample(self.sample, {'page1': pages[0]},
                                         self.date, self.cutoff), self.page)

    def test_earlier_source_alone_cannot_prove_current_discussion(self):
        self.sample.pop('activity_at')
        with self.assertRaisesRegex(ValueError, '须有当日传播记录'):
            validate_sample(self.sample, {'page1': self.page}, self.date, self.cutoff)
        self.sample['activity_at'] = '2026-10-25 02:05:00'
        self.sample['text'] = '昨日直播互动'
        with self.assertRaisesRegex(ValueError, '找不到所填'):
            validate_sample(self.sample, {'page1': self.page}, self.date, self.cutoff)

    def test_same_day_discussion_can_cite_an_earlier_event(self):
        self.sample.pop('activity_at')
        self.sample['published_at'] = '2026-10-25 10:30'
        self.sample['published_at_evidence'] = '发布时间：2026-10-25 10:30'
        self.sample['text'] = '今早网民继续讨论昨晚直播中的演员互动。'
        self.page['body'] = self.sample['published_at_evidence'] + '\n' + self.sample['text']
        validate_sample(self.sample, {'page1': self.page}, self.date, self.cutoff)

    def test_after_cutoff_activity_cannot_pass(self):
        self.sample['activity_at'] = '2026-10-25 22:05:00'
        self.sample['text'] = self.quote.replace('02:05', '22:05')
        self.page['body'] = self.header + '\n' + self.sample['text']
        with self.assertRaisesRegex(ValueError, '不晚于报告截止'):
            validate_sample(self.sample, {'page1': self.page}, self.date, self.cutoff)
