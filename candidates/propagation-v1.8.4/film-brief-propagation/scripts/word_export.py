"""Build and fill the editable Word template for the propagation section."""
from copy import deepcopy
from datetime import datetime
from pathlib import Path
import json
import re

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor


ASSETS = Path(__file__).resolve().parents[1] / 'assets'
TEMPLATE = ASSETS / 'propagation-template.docx'
SLOT = re.compile(r'\{\{([^{}]+)\}\}')
SENTIMENT_LEAD = '结合大数据分析舆论情感倾向看，'


def make_template(reference):
    document = Document(reference)
    paragraphs_ref = list(document.paragraphs)
    start = next((i for i,p in enumerate(paragraphs_ref) if p.text.strip().startswith('一、总体传播特征')), None)
    if start is None:
        raise ValueError('参考Word未找到“一、总体传播特征”标题，不能静默套用其他位置')
    end = next((i for i in range(start+1,len(paragraphs_ref)) if re.match(r'^\s*二[、．.]',paragraphs_ref[i].text)),len(paragraphs_ref))
    region = paragraphs_ref[start:end]
    figures = [p for p in region if p._p.xpath('.//wp:extent')]
    extents = [tuple(int(p._p.xpath('.//wp:extent')[0].get(dim)) for dim in ('cx','cy')) for p in figures[:2]]
    texts = [p for p in region[1:] if p.text.strip()]
    title = next((p for p in paragraphs_ref[:start] if '舆情分析' in p.text),None)
    date_p = next((p for p in paragraphs_ref[:start] if re.search(r'\d+月\d+日',p.text)),None)
    roles = {'title':title,'date':date_p,'heading':region[0],
             'body':texts[0] if texts else region[0], 'sentiment':texts[-1] if texts else region[0],
             'trend_figure':figures[0] if figures else None,
             'sentiment_figure':figures[1] if len(figures)>1 else None}
    # Keep the page setup, styles and footer. Replace the report body with slots.
    body=document._element.body
    for child in list(body):
        if child.tag != qn('w:sectPr'):
            body.remove(child)
    for rel_id, rel in list(document.part.rels.items()):
        if rel.reltype.endswith('/image'):
            document.part.drop_rel(rel_id)

    def make_paragraph(role, *parts):
        original=roles.get(role)
        p=document.add_paragraph()
        rpr=None
        if original is not None:
            if original._p.pPr is not None:
                p._p.insert(0,deepcopy(original._p.pPr))
            rpr=next((r._r.rPr for r in original.runs if r.text and r._r.rPr is not None),None)
        for text in parts:
            run=p.add_run(text)
            if rpr is not None:
                run._r.insert(0,deepcopy(rpr))
            else:
                fonts={'title':('华文中宋',22),'heading':('黑体',16),'date':('楷体',16)}
                font, size=fonts.get(role,('仿宋_GB2312',16))
                run.font.name=font
                run._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),font)
                run.font.size=Pt(size)
            run.font.color.rgb=RGBColor(0,0,0)
        if original is None and role in ('body','sentiment'):
            p.paragraph_format.first_line_indent=Pt(32)
        if role in ('title','date'):
            p.alignment=WD_ALIGN_PARAGRAPH.CENTER
        if role=='heading':
            p.paragraph_format.keep_with_next=True
        # Editorial highlights and paragraph-mark highlight do not belong in a
        # new report. Actual source text/other report sections are never reused.
        for node in list(p._p.xpath('.//w:highlight')):
            node.getparent().remove(node)
        return p

    def figure(role, slot):
        p=make_paragraph(role,slot)
        p.alignment=WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.first_line_indent=Pt(0)
        p.paragraph_format.keep_with_next=True
        p.paragraph_format.line_spacing=1

    make_paragraph('title','{{节目类型}}《{{节目名称}}》舆情分析')
    make_paragraph('date','（{{报告日期}}）')
    make_paragraph('heading','一、总体传播特征：{{传播特征标题}}')
    figure('trend_figure','{{传播趋势图}}')
    body=make_paragraph('body',
        '数据显示，截至{{统计截止}}，{{节目类型}}《{{节目名称}}》相关话题传播量约{{传播量}}条。{{节点描述}}',
        '微博平台方面，','网民围绕','{{讨论话题}}','等话题展开讨论，','{{微博热搜及阅读量}}')
    body.runs[3].bold=True
    figure('sentiment_figure','{{情感分布图}}')
    make_paragraph('sentiment',SENTIMENT_LEAD,
        '积极正面态度占比为{{正面占比}}%，{{正面观点}}。'
        '客观中立态度占比{{中立占比}}%，{{中立观点}}。'
        '消极负面态度占比{{负面占比}}%，{{负面观点}}。')
    document.core_properties.title='影视简报总体传播特征模板'
    document.core_properties.author=''
    document.core_properties.last_modified_by=''
    document.core_properties.subject='影视简报第一部分'
    return document, extents


def fill_template(metrics, node_text, reference=None):
    document, extents = make_template(reference) if reference else (Document(TEMPLATE), [])
    _, _, _, _, body, sentiment_figure, sentiment_body = document.paragraphs
    stamp=datetime.fromisoformat(metrics['cutoff'])
    report_date=f'{stamp.month}月{stamp.day}日'
    labels='、'.join(x['label'] for x in metrics['directions'])
    topics=''
    if metrics['topic_count']:
        names=''.join(f'#{x["title"]}#' for x in metrics['top3'])
        qualifier='暂计' if metrics['pending_topic_ids'] else '约'
        # The number covers all included topics, including those outside the examples.
        topics=f'{names}等话题累计阅读量{qualifier}{metrics["topic_read_display"]}次。'
    _, weibo, before_labels, _, after_labels, _ = body.runs
    if not labels:
        before_labels.text=after_labels.text=''
    elif not topics:
        after_labels.text='等话题展开讨论。'
    if not labels and not topics:
        weibo.text=''
    values={
        '节目类型':metrics['work_type'], '节目名称':metrics['work_name'], '报告日期':report_date,
        '传播特征标题':metrics['headline'].split('：',1)[1], '统计截止':f'{report_date}{stamp.hour}时',
        '传播量':metrics['volume_display'], '节点描述':node_text, '讨论话题':labels,
        '微博热搜及阅读量':topics, '传播趋势图':'', '情感分布图':'',
    }
    sentiment=metrics['sentiment']
    if sentiment:
        estimated=sentiment['method']=='editorial_estimate'
        if estimated:
            sentiment_body.runs[0].text=SENTIMENT_LEAD
            wording=sentiment_body.runs[1]
            wording.text=wording.text.replace('占比为{{','占比约{{').replace('占比{{','占比约{{')
        elif sentiment['method']=='measured' and sentiment.get('scope'):
            sentiment_body.runs[0].text=f'{SENTIMENT_LEAD}在{sentiment["scope"]}中，'
        for label, key in (('正面','positive'),('中立','neutral'),('负面','negative')):
            values[label+'占比']=sentiment['percentages'][key]
            values[label+'观点']=sentiment['summaries'][key].rstrip('。； ')
    else:
        for p in (sentiment_figure, sentiment_body):
            p._p.getparent().remove(p._p)
    for p in document.paragraphs:
        for run in p.runs:
            if SLOT.search(run.text):
                run.text=SLOT.sub(lambda match:values[match[1]],run.text)
    paragraphs=[body.text]+([sentiment_body.text] if sentiment else [])
    return document, paragraphs, extents


def write_word(document, metrics, output, extents):
    figures=[(document.paragraphs[3],'trend','每日传播趋势')]
    if metrics['sentiment']:
        figures.append((document.paragraphs[5],'sentiment','舆论情感态度分布'))
    for i, (p, name, description) in enumerate(figures):
        style=json.loads((ASSETS/f'{name}-style.json').read_text(encoding='utf-8'))
        width,height=extents[i] if i<len(extents) else style['word_extent_emu']
        section=document.sections[0]
        width=min(width,section.page_width-section.left_margin-section.right_margin)
        shape=p.runs[0].add_picture(str(output/f'{name}.png'),width=width,height=height)
        shape._inline.docPr.set('descr',description)
    document.core_properties.title=f'{metrics["work_name"]}总体传播特征'
    destination=output/'report.docx'
    document.save(destination)
    return destination
