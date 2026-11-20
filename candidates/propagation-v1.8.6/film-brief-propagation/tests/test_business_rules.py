"""Numerical and evidence invariants, independent of any example report."""
import copy
from decimal import Decimal
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
from propagation import (DRAMA_HEADLINE_HEAT_PHRASES, InputError, compute, compute_sentiment, format_volume,
                         integer, roll_forward, source_check, sha_file)
from trend_template import cardinal_curve


def row(date, heat):
    return {'date': date, 'heat': heat, 'source_cell': '日趋势!B2'}


def fixture():
    series = [dict(row(f'2026-10-{d}', 500000), previous_heat=None, source_role='current_heat', operation='首报采用') for d in range(20, 26)]
    groups = [{'id':str(i), 'title':f'话题{i}', 'boards':['微博热搜榜'], 'rows':[i+2]} for i in range(4)]
    p = {'input_fingerprint':'fixture', 'config':{'work_name':'测试作品', 'work_type':'电视剧', 'sentiment_method':'provided',
         'topic_snapshot_at':'2026-10-25 21:00'}, 'topic_groups':groups,
         'board_rows':[{'topic_id':str(i),'title':f'话题{i}','read_wan':str((i+1)*100), 'row':i+2,
                        'later_than_volume_cutoff':False, 'source_cell':f'L{i+2}', 'latest_on_board':''} for i in range(4)],
         'opinions':[{'id':'p1','text':'肯定制作。期待后续。批评改编。'}],
         'node_samples':[{'id':'s1','date':'2026-10-20','platform':'新浪微博','text':'实际样本',
                          'provenance':{'kind':'local_export','source':'样本表'},
                          'returned_record_count':6,'total_volume':999999999}],
         'trend':series,'cutoff':'2026-10-25 21:00','cutoff_cell':'小时趋势!A7',
         'candidate_node_dates':['2026-10-20'],'sources':[]}
    r = {'topics':[{'id':str(i),'decision':'include','reason':'有明确作品锚点'} for i in range(4)],
         'directions':[{'label':f'方向{i}','topic_ids':[str(i)]} for i in range(3)],
         'nodes':[{'date':'2026-10-20','summary':'官方发布开播物料，相关讨论集中出现','evidence_ids':['s1']}],
         'sentiment':{'method':'provided','percentages':{'positive':70,'neutral':25,'negative':5},
                      'summaries':{'positive':'肯定制作','neutral':'期待后续','negative':'批评改编'},
                      'paragraph_ids':{k:['p1'] for k in ('positive','neutral','negative')},
                      'provided_source':'用户给定'},
         'headline':'一、总体传播特征：剧情讨论持续'}
    return p,r


class BusinessRules(unittest.TestCase):
    def test_only_latest_two_days_are_replaced(self):
        old=[row('2026-10-22',100),row('2026-10-23',200),row('2026-10-24',300)]
        current=[row('2026-10-22',999),row('2026-10-23',888),row('2026-10-24',400),row('2026-10-25',500)]
        result=roll_forward(old,current,'2026-10-25')
        self.assertEqual([x['heat'] for x in result],[100,200,400,500])
        self.assertEqual(sum(x['heat'] for x in result),1200)

    def test_first_report_does_not_invent_history(self):
        result=roll_forward([], [row('2026-10-24',0),row('2026-10-25',12)], '2026-10-25')
        self.assertEqual([x['date'] for x in result],['2026-10-24','2026-10-25'])
        self.assertEqual(sum(x['heat'] for x in result),12)

    def test_missing_update_is_not_zero(self):
        with self.assertRaises(InputError):
            roll_forward([row('2026-10-24',20)],[row('2026-10-25',30)],'2026-10-25')

    def test_missing_numeric_and_fractional_counts_rejected(self):
        for invalid in [None,'',-1,'NaN',1.5,True]:
            with self.subTest(invalid=invalid), self.assertRaises(InputError):
                integer(invalid,'测试单元格')
        self.assertEqual(integer('1,234','测试单元格'),1234)

    def test_decimal_half_up_and_unit_boundary(self):
        self.assertEqual(format_volume(1234500),'123.5万')
        self.assertEqual(format_volume(100000000),'1.00亿')
        self.assertEqual(format_volume(101500000),'1.02亿')

    def test_record_count_cannot_replace_volume(self):
        p,r=fixture()
        m=compute(p,r)
        self.assertEqual(len(p['trend']),6)
        self.assertEqual(m['volume'],3000000)
        self.assertEqual(m['volume_display'],'300.0万')

    def test_total_uses_all_retained_board_topics_not_top3(self):
        p,r=fixture()
        m=compute(p,r)
        self.assertEqual([x['topic_id'] for x in m['top3']],['3','2','1'])
        self.assertEqual(Decimal(m['topic_read_wan']),1000)

    def test_exclusion_changes_ranking_and_total(self):
        p,r=fixture()
        r['topics'][3]['decision']='exclude'
        m=compute(p,r)
        self.assertEqual([x['topic_id'] for x in m['top3']],['2','1','0'])
        self.assertEqual(Decimal(m['topic_read_wan']),600)

    def test_other_board_topic_can_support_direction_not_sum(self):
        p,r=fixture()
        p['topic_groups'].append({'id':'other','title':'其他榜话题','boards':['微博文娱热搜榜'],'rows':[12]})
        r['topics'].append({'id':'other','decision':'include','reason':'明确相关'})
        r['directions'][0]['topic_ids']=['other']
        self.assertEqual(Decimal(compute(p,r)['topic_read_wan']),1000)

    def test_unconfirmed_topic_keeps_draft(self):
        p,r=fixture()
        r['topics'][3]['decision']='uncertain'
        m=compute(p,r)
        self.assertEqual(m['stage'],'draft')
        self.assertEqual(Decimal(m['topic_read_wan']),600)

    def test_unsupported_node_claim_is_rejected(self):
        p,r=fixture()
        r['nodes'][0]['evidence_ids']=[]
        with self.assertRaises(InputError):compute(p,r)

    def test_evidence_date_and_platform_must_match(self):
        for change in [{'date':'2026-10-21'},{'platform':'抖音'}]:
            p,r=fixture();p['node_samples'][0].update(change)
            with self.assertRaises(InputError):compute(p,r)

    def test_absent_node_evidence_can_produce_a_draft(self):
        p,r=fixture();r['nodes'][0].update(summary='',evidence_ids=[])
        self.assertEqual(compute(p,r)['stage'],'draft')

    def test_legacy_review_date_cannot_suppress_a_node(self):
        p,r=fixture()
        r['previous_nodes_through']='2026-10-24'
        m=compute(p,r)
        self.assertEqual([n['date'] for n in m['nodes']],['2026-10-20'])
        self.assertNotIn('previous_nodes_through',m)

    def test_unaddressed_candidate_is_flagged_even_with_another_node(self):
        p,r=fixture()
        p['candidate_node_dates'].append('2026-10-25')
        m=compute(p,r)
        self.assertEqual(m['stage'],'draft')
        self.assertIn('2026-10-25节点分析待补，正文暂不写入该节点',m['issues'])

    def test_negative_threshold_never_clips_the_input(self):
        p,r=fixture();r['sentiment']['percentages']={'positive':60,'neutral':20,'negative':20}
        m=compute(p,r)
        self.assertIsNone(m['sentiment'])
        self.assertEqual(m['held_sentiment']['percentages']['negative'],'20')
        self.assertEqual(m['stage'],'draft')

    def test_editorial_negative_range_includes_five_and_ten(self):
        p,r=fixture()
        p['config']['sentiment_method']='editorial_estimate'
        sentiment=r['sentiment']
        sentiment.update(method='editorial_estimate',rationale='本期观点研判')
        for negative in (4,5,10,11):
            with self.subTest(negative=negative):
                sentiment['percentages']={'positive':70,'neutral':30-negative,'negative':negative}
                if negative in (5,10):
                    self.assertEqual(compute(p,r)['sentiment']['percentages']['negative'],str(negative))
                else:
                    with self.assertRaisesRegex(InputError,'5%–10%'):
                        compute(p,r)

    def test_measured_negative_range_checks_unrounded_share(self):
        p,r=fixture()
        r['sentiment'].update(method='measured', counts={'positive':699,'neutral':200,'negative':101},
                              denominator=1000,count_source='测试样本',percentages={})
        result=compute_sentiment(r['sentiment'],p['opinions'],[])
        self.assertEqual(result['percentages']['negative'],'10')
        self.assertTrue(result['outside_report_range'])

    def test_invalid_percentage_sum_and_missing_basis_rejected(self):
        p,r=fixture();r['sentiment']['percentages']['positive']=69
        with self.assertRaises(InputError):compute(p,r)
        p,r=fixture();p['config']['sentiment_method']='editorial_estimate';r['sentiment']['method']='editorial_estimate'
        with self.assertRaises(InputError):compute(p,r)

    def test_full_counts_control_measured_percentages(self):
        p,r=fixture();s=r['sentiment'];s.update(method='measured',counts={'positive':2,'neutral':1,'negative':0},denominator=3,
                                               count_source='完整3条分类样本',percentages={})
        issues=[];result=compute_sentiment(s,p['opinions'],issues)
        self.assertEqual(result['percentages'],{'positive':'67','neutral':'33','negative':'0'})
        s['denominator']=4
        with self.assertRaises(InputError):compute_sentiment(s,p['opinions'],[])

    def test_unrelated_or_missing_source_change_is_detected(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'source.txt';path.write_text('原始输入',encoding='utf-8')
            p={'sources':[{'path':str(path),'sha256':sha_file(path),'role':'opinions'}]}
            source_check(p);path.write_text('输入变化',encoding='utf-8')
            with self.assertRaises(InputError):source_check(p)

    def test_headline_limit_and_empty_tail(self):
        p,r=fixture()
        for title in ['一、总体传播特征：','一、总体传播特征：'+'长'*51]:
            r['headline']=title
            with self.assertRaises(InputError):compute(p,r)

    def test_drama_headline_matches_the_documented_heat_phrases(self):
        self.assertEqual(len(DRAMA_HEADLINE_HEAT_PHRASES),7)
        style=(Path(__file__).resolve().parents[1]/'references/review-rules.md').read_text(encoding='utf-8')
        self.assertTrue(all(phrase in style for phrase in DRAMA_HEADLINE_HEAT_PHRASES))
        p,r=fixture()
        p['trend'][-2]['heat']=735848
        p['trend'][-1]['heat']=416786
        r['headline']='一、总体传播特征：热度较首播日回落，积极正面声音占据主流'
        with self.assertRaisesRegex(InputError,'剧集标题'):
            compute(p,r)
        r['headline']='一、总体传播特征：剧集开播、主创宣发'+DRAMA_HEADLINE_HEAT_PHRASES[1]+'，积极正面声音占据主流'
        self.assertEqual(compute(p,r)['headline'],r['headline'])

    def test_reference_smooth_curve_passes_through_daily_values(self):
        points=[(0,100),(1,20),(2,80),(3,80),(4,30)]
        curve=cardinal_curve(points)
        for x,y in points:self.assertTrue(any(abs(xx-x)<1e-8 and abs(yy-y)<1e-8 for xx,yy in curve))


if __name__=='__main__':unittest.main(verbosity=2)
