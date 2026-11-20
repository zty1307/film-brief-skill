"""Synthetic XLSX fixtures exercise the public CLI workflow, not user data."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

from openpyxl import Workbook

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from propagation import InputError, prepare, build, verify, load, dump, sheet_table, daily_series


def workbook(path, tables):
    book=Workbook();book.remove(book.active)
    for name,rows in tables.items():
        sheet=book.create_sheet(name)
        for row in rows:sheet.append(row)
    book.save(path);book.close()


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        workbook(self.root/'heat.xlsx',{
            '日趋势':[['日期','热度'],['2026-10-24',1200000],['2026-10-25',1800000]],
            '小时趋势':[['日期','热度'],['2026-10-24 21',1],['2026-10-25 21',2]]})
        workbook(self.root/'topics.xlsx',{'热搜话题分析':[
            ['标题','榜单','阅读量（万）','最近上榜时间'],
            ['测试作品剧情','微博热搜榜','100.1','2026-10-25 10:00'],
            ['测试作品演技','微博热搜榜','200.2','2026-10-25 11:00'],
            ['测试作品妆造','微博热搜榜','300.3','2026-10-25 12:00'],
            ['测试作品剧情','微博文娱热搜榜','99999','2026-10-25 10:00']]})
        (self.root/'opinions.txt').write_text('肯定剧情。\n\n客观关注后续。\n\n批评改编。',encoding='utf-8')
        samples=[{'id':'fixture_only','date':'2026-10-25','published_at':'2026-10-25 12:00','platform':'新浪微博',
                  'author':'合成测试账户','text':'合成测试正文，仅验证本地样本导入。',
                  'provenance':{'kind':'user_provided','source':'合成测试数据，不是真实舆情'}}]
        dump(self.root/'samples.json',samples)
        self.config={'work_name':'合成测试作品','work_type':'电视剧','current_heat':'heat.xlsx',
                     'weibo_topics':'topics.xlsx','opinions':'opinions.txt','node_samples':'samples.json',
                     'sentiment_method':'provided','topic_snapshot_at':'2026-10-25 21:00'}
        dump(self.root/'config.json',self.config);self.workspace=self.root/'run'

    def tearDown(self):self.temp.cleanup()

    def ready(self):
        prepare(self.root/'config.json',self.workspace)
        r=load(self.workspace/'review.json');p=load(self.workspace/'review_packet.json')
        for topic in r['topics']:topic.update(decision='include',reason='合成测试给定相关')
        r['directions']=[{'label':label,'topic_ids':[p['topics'][i]['id']]} for i,label in enumerate(['剧情','演技','妆造'])]
        r['nodes']=[{'date':'2026-10-25','summary':'合成测试帖子讨论上线话题','evidence_ids':['fixture_only']}]
        ids=[x['id'] for x in p['opinions']]
        r['sentiment'].update(percentages={'positive':70,'neutral':25,'negative':5},
                             summaries={'positive':'肯定剧情','neutral':'关注后续','negative':'批评改编'},
                             paragraph_ids={'positive':[ids[0]],'neutral':[ids[1]],'negative':[ids[2]]},
                             provided_source='合成测试比例')
        r['headline']='一、总体传播特征：合成测试验证'
        dump(self.workspace/'review.json',r)

    def test_public_lifecycle_and_next_report_continuation(self):
        self.ready();result=build(self.workspace)
        self.assertEqual(result['stage'],'complete')
        self.assertEqual(verify(self.workspace)['verification'],'pass')
        metrics=load(self.workspace/'deliverables'/'metrics.json')
        self.assertEqual(metrics['volume'],3000000)
        self.assertEqual(metrics['topic_read_wan'],'600.6')
        self.assertEqual(metrics['topic_count'],3)
        next_data=daily_series(self.workspace/'deliverables'/'trend.json','日趋势')
        self.assertEqual(sum(x['heat'] for x in next_data),3000000)
        self.assertEqual(next_data[-1]['date'],'2026-10-25')

    def test_stale_review_is_not_reported_as_current(self):
        self.ready();build(self.workspace)
        r=load(self.workspace/'review.json');r['headline']='一、总体传播特征：修改后待重新构建'
        dump(self.workspace/'review.json',r)
        with self.assertRaises(InputError):verify(self.workspace)
        build(self.workspace);self.assertEqual(verify(self.workspace)['verification'],'pass')

    def test_tampered_output_and_source_change_are_detected(self):
        self.ready();build(self.workspace)
        (self.workspace/'deliverables'/'report.txt').write_text('输出被改动',encoding='utf-8')
        with self.assertRaises(InputError):verify(self.workspace)
        build(self.workspace)
        (self.root/'opinions.txt').write_text('源文件变化',encoding='utf-8')
        with self.assertRaises(InputError):verify(self.workspace)

    def test_uncached_formula_missing_value_is_not_zero(self):
        workbook(self.root/'formula.xlsx',{'日趋势':[['日期','热度'],['2026-10-25','=SUM(1,2)']]})
        with self.assertRaises(InputError):daily_series(self.root/'formula.xlsx','日趋势')

    def test_duplicate_dates_or_board_topics_are_rejected(self):
        workbook(self.root/'duplicate.xlsx',{'日趋势':[['日期','热度'],['2026-10-25',10],['2026-10-25',20]]})
        with self.assertRaises(InputError):daily_series(self.root/'duplicate.xlsx','日趋势')
        workbook(self.root/'topics.xlsx',{'热搜话题分析':[['标题','榜单','阅读量（万）'],
                 ['同名话题','微博热搜榜',1],['同名话题','微博热搜榜',2]]})
        with self.assertRaises(InputError):prepare(self.root/'config.json',self.workspace)

    def test_hourly_cutoff_uses_last_row_but_rejects_wrong_order(self):
        workbook(self.root/'heat.xlsx',{'日趋势':[['日期','热度'],['2026-10-25',10]],
                 '小时趋势':[['日期'],['2026-10-25 21'],['2026-10-25 20']]})
        with self.assertRaises(InputError):prepare(self.root/'config.json',self.workspace)

    def test_blank_header_prefix_and_normalized_headers_supported(self):
        workbook(self.root/'prefix.xlsx',{'热搜话题分析':[['热搜统计'],[],[' 标题 ','榜单','阅读量 ( 万 )'],['测试','微博热搜榜','1,234.5']]})
        rows=sheet_table(self.root/'prefix.xlsx','热搜话题分析',('标题','榜单','阅读量（万）'))
        self.assertEqual(rows[0]['fields']['标题'],'测试')

    def test_changed_method_requires_new_preparation(self):
        self.ready();r=load(self.workspace/'review.json');r['sentiment']['method']='editorial_estimate'
        dump(self.workspace/'review.json',r)
        with self.assertRaises(InputError):build(self.workspace)


if __name__=='__main__':unittest.main(verbosity=2)
