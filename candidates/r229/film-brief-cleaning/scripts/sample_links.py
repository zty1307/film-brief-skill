"""Append actual in-prose source URLs automatically after prose review passes.

No screenshots, browser access, model call or additional human confirmation.
The approved prose remains unchanged in its original file.
"""
from pathlib import Path
import argparse
import hashlib
import ipaddress
import json
from urllib.parse import urlsplit

PROFILE='ACTUAL_QUOTED_SAMPLE_LINKS_V1'
def read(p):return json.loads(Path(p).read_text(encoding='utf-8-sig'))
def sha(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def save(p,v):Path(p).write_text(json.dumps(v,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

def safe_url(url):
    try:
        p=urlsplit(url);host=(p.hostname or '').lower()
        if p.scheme not in ('http','https') or not host or p.username or p.password:return False
        if any(ord(c)<32 for c in url) or host in ('localhost','woa.com') or host.endswith(('.local','.localhost','.internal','.woa.com')):return False
        try:return ipaddress.ip_address(host).is_global
        except ValueError:return '.' in host
    except ValueError:return False

def inventory(dataset,audit):
    from rapid_film_workbench import URL_FIELDS
    lookup={s['candidate_id']:s for g in dataset['groups'] for s in g['samples']}
    points=[];seen=set()
    for number,point in enumerate(audit['points'],1):
        samples=[]
        for row in point['samples']:
            cid=row['candidate_id']
            if cid in seen or cid not in lookup:raise ValueError('引用样本重复或不在本次已审工作台中')
            seen.add(cid);source=lookup[cid]
            if row['source_id']!=source['source']['id']:raise ValueError('引用样本出处与工作台不一致')
            data=source['source']['data'];url=next((str(data[k]).strip() for k in URL_FIELDS if str(data.get(k) or '').strip() not in ('','-','—')),'')
            samples.append({'candidate_id':cid,'attribution':row['attribution'],'url':url,'usable_url':safe_url(url)})
        points.append({'number':number,'claim':point['claim'],'samples':samples})
    return points

def add_link(paragraph,url):
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    node=OxmlElement('w:hyperlink');node.set(qn('r:id'),paragraph.part.relate_to(url,RT.HYPERLINK,is_external=True))
    run=OxmlElement('w:r');props=OxmlElement('w:rPr')
    fonts=OxmlElement('w:rFonts')
    for script in ('ascii', 'hAnsi', 'eastAsia'):
        fonts.set(qn('w:'+script), '仿宋_GB2312')
    props.append(fonts)
    for tag,val in [('color','467886'),('sz','32'),('szCs','32'),('u','single')]:
        x=OxmlElement('w:'+tag);x.set(qn('w:val'),val);props.append(x)
    run.append(props);text=OxmlElement('w:t');text.text=url;run.append(text);node.append(run);paragraph._p.append(node)

def append_to(document, points):
    """Use the report's appendix layout and include every in-prose source in order."""
    from docx.shared import Pt
    from report_writing import grid_paragraph, set_font

    heading = document.add_paragraph()
    grid_paragraph(heading)
    heading.paragraph_format.first_line_indent = Pt(32)
    heading.paragraph_format.space_before = Pt(12)
    heading.paragraph_format.keep_with_next = True
    set_font(heading.add_run('附典型样本：'), '黑体', 16)
    missing = []
    for point in points:
        title = document.add_paragraph()
        grid_paragraph(title)
        title.paragraph_format.first_line_indent = Pt(32)
        title.paragraph_format.keep_with_next = True
        set_font(title.add_run(f"{point['number']}.{point['claim'].rstrip('。')}"), '仿宋_GB2312', 16, True)
        for sample in point['samples']:
            paragraph = document.add_paragraph()
            grid_paragraph(paragraph)
            paragraph.paragraph_format.first_line_indent = Pt(32)
            if sample['usable_url']:
                add_link(paragraph, sample['url'])
            else:
                missing.append(sample['candidate_id'])
                set_font(paragraph.add_run('原表未提供可用原文链接'), '仿宋_GB2312', 16)
    return missing


def build(dataset_path,plan_path,writing):
    from docx import Document
    from report_writing import check_dataset
    from prose_review import require_pass
    dataset_path,plan_path,writing=Path(dataset_path),Path(plan_path),Path(writing)
    dataset=read(dataset_path);check_dataset(dataset,dataset_path);require_pass(dataset_path,plan_path,writing)
    audit_path=writing/'source_audit.json';audit=read(audit_path)
    if audit['dataset_sha256']!=sha(dataset_path):raise ValueError('引用审计不属于本次工作台')
    points=inventory(dataset,audit);document=Document(writing/'opinion_draft.docx')
    missing=append_to(document,points)
    output=writing/'opinion_with_links.docx';document.save(output)
    receipt={'profile':PROFILE,'dataset_sha256':sha(dataset_path),'plan_sha256':sha(plan_path),
             'prose_word_sha256':sha(writing/'opinion_draft.docx'),'terminal_review_sha256':sha(writing/'final_prose_review.json'),
             'audit_sha256':sha(audit_path),'output':str(output),'output_sha256':sha(output),
             'point_count':len(points),'sample_count':sum(len(p['samples']) for p in points),
             'link_count':sum(s['usable_url'] for p in points for s in p['samples']),
             'missing_link_ids':missing,'points':points,'screenshots_required':False,
             'link_access_checked':False}
    save(writing/'sample_links_receipt.json',receipt)
    return receipt

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--dataset',required=True);p.add_argument('--plan',required=True);p.add_argument('--writing',required=True);a=p.parse_args()
    try:result=build(a.dataset,a.plan,a.writing)
    except (OSError,ValueError,KeyError) as exc:p.error(str(exc))
    print(json.dumps(result,ensure_ascii=False,indent=2))
if __name__=='__main__':main()
