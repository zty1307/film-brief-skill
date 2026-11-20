"""Node prose must retain causes; only the source series owns volume and peak."""
from pathlib import Path
import sys,unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from node_writing import cause_errors,compose_summary,normalize_reply
from node_search_pipeline import writing_errors,node_style,concise_rewrite,one_date
from unittest.mock import patch
from types import SimpleNamespace
import tempfile
from propagation import report_nodes,format_volume


class NodeWritingTests(unittest.TestCase):
    def test_reason_kept_verbatim_in_fixed_sentence(self):
        reason='剧集上线播出后，主创直播宣发持续展开'
        self.assertEqual(compose_summary(reason,'热度攀升'),reason+'，全网讨论热度攀升。')
        self.assertEqual(compose_summary('剧情话题讨论持续展开','热度保持高位运行'),'剧情话题讨论持续展开，助推舆论热度保持高位运行。')

    def test_process_and_empty_background_are_rejected(self):
        for reason in ['Excel确认主创直播','该文涉及改编争议','剧集更新']:
            with self.subTest(reason=reason):self.assertTrue(cause_errors(reason))

    def test_supported_detail_is_not_blanket_forbidden(self):
        text='10时官方公布定档信息并释出“终极预告”，主演宣传带动话题热度攀升。'
        data={'summary':text,'trend_phrase':'热度攀升'}
        self.assertEqual(normalize_reply(data,['热度攀升'])['summary'],text)
        self.assertFalse(writing_errors(data,[],['热度攀升']))

    def test_flexible_connector_preserved_and_opposite_direction_rejected(self):
        data={'summary':'主创宣传叠加剧情讨论，合力推高话题热度。','trend_phrase':'推高话题热度'}
        self.assertFalse(writing_errors(data,[],['推高话题热度']))
        data['summary']='主创宣传持续展开，话题热度回落。'
        self.assertTrue(writing_errors(data,[],['推高话题热度']))

    def test_style_reference_is_loaded_for_actual_rewrite(self):
        factors=[{'label':'主创宣发','role':'driver','mechanism':'主创直播互动为开播宣传造势','basis':'直播互动'}]
        data={'summary':'主创直播互动展开，带动话题热度攀升。','trend_phrase':'带动话题热度攀升',
              'retained_factors':[{'label':'主创宣发','phrase':'主创直播互动展开'}]}
        trend={'work_name':'测试剧','series':[{'date':'2026-10-22','heat':1},{'date':'2026-10-23','heat':2}]}
        with patch('node_search_pipeline.native',return_value=data) as mocked:
            result=concise_rewrite(SimpleNamespace(),Path('unused'),trend,'2026-10-23',factors,max_attempts=1)
        self.assertEqual(result['summary'],data['summary'])
        self.assertIn(node_style(),mocked.call_args.args[2])

    def test_both_model_prompt_paths_load_style_without_importing_human_events(self):
        quote='主创发布预告，主演线上互动。'
        page={'fetch_id':'f1','url':'https://example.org/post','body':'发布时间: 2026-10-23 18:00\n'+quote,
              'body_truncated':False,'queries':['测试剧 2026-10-23']}
        normal={'samples':[{'id':'s1','source_id':'p1','event_date':'2026-10-23','text':quote}],
                'factors':[{'label':'主创宣发','role':'driver','mechanism':'主创发布预告展开宣发','sample_ids':['s1'],'basis':'主创发布预告'}],
                'summary':'主创发布预告持续宣发，带动话题热度攀升。','trend_phrase':'带动话题热度攀升',
                'retained_factors':[{'label':'主创宣发','phrase':'主创发布预告'}]}
        compact={'drivers':[['主创宣发','p1','2026-10-23',quote,'主创发布预告']],
                 'summary':normal['summary'],'trend_phrase':'带动话题热度攀升'}
        trend={'work_name':'测试剧','cutoff':'2026-10-23 21:00',
               'series':[{'date':'2026-10-22','heat':1},{'date':'2026-10-23','heat':2}]}
        for model,reply in [('deepseek-v4.1-flash',normal),('hy4std',compact)]:
            with self.subTest(model=model),tempfile.TemporaryDirectory() as directory:
                args=SimpleNamespace(model=model,output=Path(directory))
                with patch('node_search_pipeline.native',side_effect=[[page],reply]) as mocked:
                    node,samples,errors=one_date(args,trend,'2026-10-23')
                self.assertFalse(errors)
                self.assertTrue(samples)
                self.assertFalse(node['include_daily_volume'])
                prompt=mocked.call_args_list[1].args[2]
                self.assertIn(node_style(),prompt)
                self.assertNotIn('735848',prompt)
                self.assertNotIn('尚公主',prompt)

    def test_cannot_select_an_unoffered_trend(self):
        with self.assertRaises(ValueError):normalize_reply({'reason':'主创直播宣发持续展开','trend_phrase':'热度保持高位运行'},['热度有所回落'])

    def test_numeric_suffix_comes_from_entire_rolled_series(self):
        series=[{'date':'2026-10-23','heat':735848},{'date':'2026-10-24','heat':513893},{'date':'2026-10-25','heat':560777}]
        for include_daily_volume in (False, True):
            with self.subTest(include_daily_volume=include_daily_volume):
                nodes=[{'date':'2026-10-23','summary':'主创宣发持续展开，全网讨论热度攀升。',
                        'include_daily_volume':include_daily_volume},
                       {'date':'2026-10-25','summary':'剧情话题讨论持续展开，带动话题热度回升。'}]
                result=report_nodes({'series':series,'nodes':nodes})
                self.assertIn('23日，主创宣发持续展开，全网讨论热度攀升，达到周期内传播峰值，单日传播量约73.6万条。',result)
                self.assertIn('25日，剧情话题讨论持续展开，带动话题热度回升。',result)
                self.assertNotIn('56.1万',result)
                self.assertEqual(result.count('单日传播量'),1)

    def test_rebound_uses_the_approved_wording(self):
        approved = {'summary':'剧情走向引发讨论，带动话题热度回升。','trend_phrase':'带动话题热度回升'}
        rejected = {**approved,'summary':'剧情走向引发讨论，当日剧集热度有所回升。'}
        self.assertFalse(writing_errors(approved, [], ['带动话题热度回升']))
        self.assertTrue(writing_errors(rejected, [], ['带动话题热度回升']))

    def test_negative_only_search_results_trigger_supplement(self):
        from node_search_pipeline import accepted_evidence
        negative = '观众吐槽剧情硬凑CP，弃剧讨论增多。'
        positive = '观众讨论演员互动和剧中人物关系。'
        stamp = '发布时间：2026-10-25 18:00'
        page = {'fetch_id':'negative','url':'https://example.org/negative',
                'body':f'标题：口碑下滑\n{stamp}\n{negative}',
                'body_truncated':False,'queries':['测试剧']}
        supplement = {**page,'fetch_id':'positive','url':'https://example.org/positive',
                      'body':f'标题：演员互动\n{stamp}\n{positive}'}
        reused = {'samples':[{'id':'s1','fetch_id':'negative','published_at':'2026-10-25 18:00',
                              'published_at_evidence':stamp,'activity_date':'2026-10-25','text':negative}],
                  'factors':[{'label':'剧情讨论','sample_ids':['s1'],'basis':negative}]}
        samples, factors, errors = accepted_evidence(reused,[page],'2026-10-25','2026-10-25 21:00','test')
        self.assertFalse(factors)
        self.assertTrue(errors)
        reply = {
            'samples':[{'id':'s1','source_id':'supplement_p2','activity_date':'2026-10-25','text':positive}],
            'factors':[{'label':'演员互动','role':'driver','mechanism':'观众讨论演员互动与人物关系',
                        'sample_ids':['s1'],'basis':'观众讨论演员互动'}],
            'summary':'观众关注演员互动及剧中人物关系，带动话题热度回升。',
            'retained_factors':[{'label':'演员互动','phrase':'观众关注演员互动'}],
            'trend_phrase':'带动话题热度回升',
        }
        trend = {'work_name':'测试剧','cutoff':'2026-10-25 21:00',
                 'series':[{'date':f'2026-10-{day}','heat':heat} for day,heat in [(23,20),(24,10),(25,15)]]}
        with tempfile.TemporaryDirectory() as directory:
            args = SimpleNamespace(model='test',output=Path(directory))
            with patch('node_search_pipeline.native',side_effect=[[page],[supplement],reply]) as mocked:
                node, samples, errors = one_date(args,trend,'2026-10-25')
            self.assertEqual(mocked.call_args_list[1].args[1].name,'search-supplement')
        self.assertFalse(errors)
        self.assertEqual(node['summary'],reply['summary'])
        self.assertEqual([sample['url'] for sample in samples],[supplement['url']])

    def test_no_cause_cannot_be_filled_by_a_volume_sentence(self):
        self.assertEqual(report_nodes({'series':[{'date':'2026-10-23','heat':12}],
                                      'nodes':[{'date':'2026-10-23','summary':''}]}),'')

    def test_peak_is_not_just_the_max_before_each_node(self):
        result=report_nodes({'series':[{'date':'2026-10-23','heat':100000000},{'date':'2026-10-25','heat':101500000}],
                             'nodes':[{'date':'2026-10-23','summary':'主创直播宣发持续展开，全网讨论热度攀升。','include_daily_volume':True}]})
        self.assertNotIn('传播峰值',result)
        self.assertIn('单日传播量约1.00亿条',result)

    def test_peak_can_be_omitted_and_period_lead_is_not_duplicated(self):
        result=report_nodes({'series':[{'date':'2026-10-23','heat':10}],
                             'nodes':[{'date':'2026-10-23','summary':'周期内，主创宣发带动话题热度攀升。','include_peak':False}]})
        self.assertEqual(result,'周期内，主创宣发带动话题热度攀升。')

    def test_volume_flags_cannot_be_truthy_strings(self):
        for key in ['include_peak','include_daily_volume']:
            with self.subTest(key=key),self.assertRaises(ValueError):
                normalize_reply({'summary':'主创宣发推动热度攀升。',key:'false'},['热度攀升'])


    def test_long_activity_list_is_rejected_without_truncation(self):
        original = '开播前后，孟子义、李昀锐等主创以直播畅聊局分享戏里戏外趣事与角色关系，主演同步以角色口吻发博预热，并预告空降剧集弹幕区、讨论区与观众互动，配套释出终极预告、追剧日历和云包场等物料。相关话题热度攀升。'
        data = {'summary': original}
        with self.assertRaisesRegex(ValueError, '最多60字'):
            normalize_reply(data, ['热度攀升'])
        self.assertEqual(data['summary'], original)
        from propagation import InputError
        with self.assertRaisesRegex(InputError, '完整节点超过90字'):
            report_nodes({'series':[{'date':'2026-10-23','heat':735848}],
                          'nodes':[{'date':'2026-10-23','summary':original}]})

    def test_compact_causes_survive_date_peak_and_volume(self):
        text = '主创发布宣传内容，剧情走向引发网民讨论，带动话题热度攀升。'
        data = normalize_reply({'summary':text}, ['热度攀升'])
        rendered = report_nodes({'series':[{'date':'2026-10-23','heat':735848}],
                                'nodes':[{'date':'2026-10-23',**data,'include_daily_volume':True}]})
        self.assertIn(text.rstrip('。'), rendered)
        self.assertIn('73.6万条', rendered)
        self.assertLessEqual(len(rendered),90)


if __name__=='__main__':unittest.main()
