from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from node_selection import select_nodes
from node_search_pipeline import trend_phrases

def rows(values):
    return [{'date':f'2026-11-{i+1:02d}','heat':v} for i,v in enumerate(values)]

class NodeSelection(unittest.TestCase):
    def test_local_peak_selected_instead_of_low_last_day(self):
        selected=select_nodes(rows([10,100,20,80,10]))
        self.assertEqual([n['date'] for n in selected],['2026-11-02','2026-11-04'])
    def test_small_ripple_does_not_displace_major_node(self):
        selected=select_nodes(rows([100,94,96,92,20,110,60]))
        self.assertEqual([n['date'] for n in selected],['2026-11-01','2026-11-06'])
    def test_latest_high_level_does_not_have_to_be_peak(self):
        series=rows([10,100,95]);result=select_nodes(series)
        self.assertEqual([n['date'] for n in result],['2026-11-02','2026-11-03'])
        choices=trend_phrases({'series':series},'2026-11-03')
        self.assertNotIn('热度有所回落',choices)
        self.assertIn('推动话题热度保持高位运行',choices)
    def test_flat_series_does_not_invent_peak(self):
        self.assertEqual(select_nodes(rows([10,10,10])),[])
    def test_rebound_at_end_is_not_confirmed_local_peak(self):
        series=rows([80,10,70])
        result=select_nodes(series)
        last=result[-1]
        self.assertNotIn('显著局部峰值',last['curve_roles'])
        phrases=trend_phrases({'series':series},last['date'])
        self.assertIn('带动话题热度回升',phrases)
        self.assertNotIn('热度有所回升',phrases)
        self.assertNotIn('带动话题热度回升',trend_phrases({'series':rows([10,20,30])},'2026-11-03'))
    def test_starting_high_and_later_period_peak_are_both_selected(self):
        series=[{'date':f'2026-10-{i+17:02d}','heat':heat}
                for i,heat in enumerate([664215,623740,637251,657955,538633,318533,735848,416786])]
        result=select_nodes(series)
        self.assertEqual([n['date'] for n in result],['2026-10-17','2026-10-23'])
        choices=trend_phrases({'series':series},'2026-10-17')
        self.assertGreater(len(choices),1)
        self.assertIn('相关话题热度保持高位运行',choices)
        self.assertNotIn('致舆情出现首轮高峰',choices)

    def test_first_interior_peak_keeps_the_report_phrase(self):
        series=rows([10,80,20,100,10])
        self.assertIn('致舆情出现首轮高峰',trend_phrases({'series':series},'2026-11-02'))

if __name__=='__main__':unittest.main()
