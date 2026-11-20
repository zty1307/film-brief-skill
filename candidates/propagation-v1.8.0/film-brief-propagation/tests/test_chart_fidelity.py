import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from trend_template import render_trend,render_sentiment,ASSETS

class ChartFidelity(unittest.TestCase):
    def fixture(self):
        return {'work_name':'测试剧','work_type':'电视剧','volume':3000,'volume_display':'0.3万',
                'series':[{'date':'2026-11-01','heat':1000},{'date':'2026-11-02','heat':2000}],
                'sentiment':{'percentages':{'positive':'70','neutral':'22','negative':'8'}}}

    def test_generation_does_not_read_report_images(self):
        original=Path.read_bytes
        def guarded(path):
            if path.name.endswith('-reference.png'):
                raise AssertionError('Production must not read a completed report image')
            return original(path)
        with tempfile.TemporaryDirectory() as tmp, patch.object(Path,'read_bytes',guarded):
            m=self.fixture()
            a=render_trend(m,tmp);b=render_sentiment(m,tmp)
            self.assertEqual(a['trend_fidelity']['mode'],'generated_from_input')
            self.assertEqual(b['sentiment_fidelity']['mode'],'generated_from_input')
            with Image.open(Path(tmp)/'trend.png') as im: self.assertEqual(im.size,(856,532))
            with Image.open(Path(tmp)/'sentiment.png') as im: self.assertEqual(im.size,(865,520))

    def test_changed_input_changes_curve_and_date_but_keeps_legend(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=self.fixture();render_trend(m,tmp)
            a=Image.open(Path(tmp)/'trend.png').copy()
            m['series'][-1]={'date':'2026-11-03','heat':800};m['volume']=1800;m['volume_display']='0.2万'
            render_trend(m,tmp);b=Image.open(Path(tmp)/'trend.png').copy()
            self.assertNotEqual(a.crop((110,110,840,450)).tobytes(),b.crop((110,110,840,450)).tobytes())
            self.assertNotEqual(a.crop((0,456,856,494)).tobytes(),b.crop((0,456,856,494)).tobytes())
            self.assertEqual(a.crop((370,496,500,532)).tobytes(),b.crop((370,496,500,532)).tobytes())

    def test_sentiment_changes_with_current_percentages(self):
        with tempfile.TemporaryDirectory() as tmp:
            m=self.fixture();render_sentiment(m,tmp);a=(Path(tmp)/'sentiment.png').read_bytes()
            m['sentiment']['percentages']={'positive':'60','neutral':'32','negative':'8'}
            render_sentiment(m,tmp)
            self.assertNotEqual(a,(Path(tmp)/'sentiment.png').read_bytes())

if __name__=='__main__':unittest.main()
