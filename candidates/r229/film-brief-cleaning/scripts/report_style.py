"""Deterministic report presentation distilled from multiple manual briefs.

No baseline report is loaded at runtime. Workbench groups/evidence are read-only.
"""
from __future__ import annotations
import copy
import re

STYLE = 'MANUAL_BRIEF_V1'
MIN_BODY_PARAGRAPHS = 6
MAX_BODY_PARAGRAPHS = 7
REPORT_LEAD_VERBS = ('肯定', '认可', '称赞', '点赞', '赞扬', '认为', '讨论', '探讨', '分析', '关注', '热议', '期待', '看好')
CRITICISM_LEAD_VERBS = ('吐槽', '质疑', '认为', '担忧', '看衰')
STANCE_ORDER = {'正向词倾向':0,'positive':0,'客观词倾向':1,'中性或混合':1,'objective':1,'负向词倾向':2,'negative':2}
LIMITS = {
    'style':STYLE,'max_sources_per_point':None,'preferred_sources_per_point':[3,4],'usual_sources_per_point':[3,4],
    'paragraph_target_characters':[200,250],'paragraph_normal_band':[180,300],
    'criticism_target_characters':[150,250],'criticism_normal_band':[100,300],
    'length_basis':'去空白后的可见字符，含编号、小标题、出处与标点',
    'max_quote_characters':140,'min_quote_characters':8,'max_claim_characters':48,'maximum_criticism_claim_characters':48,
    'short_complete_quote_needs_extra_length_reason':False,
    'body_paragraph_count':[MIN_BODY_PARAGRAPHS,MAX_BODY_PARAGRAPHS],'maximum_criticism_paragraphs':1,
    'rule':'正文至少6段，证据充足可写7段，含唯一批评末段。先肯定认可，再讨论，主要批评最后。通常3—4条互补声音，充分时2条，短评有新增信息时可选5—6条。普通段目标200—250字。已审工作台不够支撑6个独立观点时，报告证据缺口。图表从上到下与最终正文段落逐项同序，“其他”固定最后。',
}


def point_rank(dataset:dict, point:dict) -> int:
    indices=point.get('group_indices',[])
    if not isinstance(indices,list) or not indices or any(type(i) is not int or not 0<=i<len(dataset['groups']) for i in indices):
        raise ValueError('正文点须绑定当前工作台的有效group_indices，不改变聚类归属')
    ranks={STANCE_ORDER.get(dataset['groups'][i].get('stance'),1) for i in indices}
    if len(ranks)!=1:
        raise ValueError('一个成文点不能将工作台正面、讨论与负面簇混为同一立场')
    return ranks.pop()


def compile_plan(dataset:dict, plan:dict) -> dict:
    """Stable editorial ordering; remap all point indices together, never quotes."""
    result=copy.deepcopy(plan)
    if result.get('report_style')!=STYLE:return result
    points=result.get('points',[])
    if not isinstance(points,list) or not points:raise ValueError('先填写有本期证据的正文点，不提交空计划')
    from human_selection import POLICY, validate_points
    validate_points(dataset, points)
    if dataset.get("quantity_contract") != POLICY and not MIN_BODY_PARAGRAPHS <= len(points) <= MAX_BODY_PARAGRAPHS:
        raise ValueError(
            f'正文当前{len(points)}段，须为{MIN_BODY_PARAGRAPHS}或{MAX_BODY_PARAGRAPHS}段，含唯一批评末段。'
            '从已审工作台选取独立、有证据的观点。材料不足时如实报告缺口，保留工作台，勿拆近义点或补原表。'
        )
    ranks=[point_rank(dataset,p) for p in points]
    negative=[i for i,r in enumerate(ranks) if r==2]
    if len(negative)>1:
        raise ValueError(f'人工简报体只保留一个末尾批评汇总段；当前负面点={negative}。在计划中合并/选取主要批评，保留各条引文与分句支持，通常3—4条独立互补来源，充分时2条，短评有新增信息时可选5—6条，不为数量凑样本；不机械拼接现有标题，不改工作台聚类或立场。')
    order=sorted(range(len(points)),key=lambda i:(ranks[i],i))
    mapping={old:new for new,old in enumerate(order)}
    result['points']=[points[i] for i in order]
    for p,old in zip(result['points'],order):
        if ranks[old] == 2:
            opening = p.get('criticism_opening', 'aside' if p.get('format') == 'aside' else 'numbered')
            if opening not in ('numbered', 'aside'):
                raise ValueError('criticism_opening只能numbered或aside，选择同一末段的起句形式')
            p['criticism_opening'] = opening
            p['format'] = 'criticism_summary'
        else:
            if p.get('format') in ('aside', 'criticism_summary') or p.get('criticism_opening'):
                raise ValueError('此外与批评汇总仅用于唯一末尾批评段，普通观点使用编号')
            p['format'] = 'numbered'
    outline=result.get('outline_review',{})
    boundaries=outline.get('point_boundaries',[])
    if len(boundaries)==len(points):
        by_id={v.get('point_index'):v for v in boundaries}
        if set(by_id)==set(range(len(points))):
            outline['point_boundaries']=[dict(by_id[old],point_index=new) for new,old in enumerate(order)]
    for pair in outline.get('pairwise_review',[]):
        if pair.get('left') in mapping and pair.get('right') in mapping:
            pair['left'],pair['right']=sorted([mapping[pair['left']],mapping[pair['right']]])
    result['presentation_order']={'policy':STYLE,'original_point_indices':order,'negative_summary_last':True}
    return result


def criticism_lead(claim:str) -> str:
    return '部分声音' + claim


def paragraph_measure(text:str, point:dict, stance:str) -> dict:
    count=len(re.sub(r'\s+','',text))
    negative=stance=='negative'
    band=LIMITS['criticism_normal_band' if negative else 'paragraph_normal_band']
    target=LIMITS['criticism_target_characters' if negative else 'paragraph_target_characters']
    note=str(point.get('length_exception_reason') or '').strip()
    return {'characters':count,'chinese_characters':len(re.findall(r'[\u3400-\u9fff]',text)),
            'target':target,'normal_band':band,'inside_target':target[0]<=count<=target[1],
            'inside_normal_band':band[0]<=count<=band[1],'exception_reason':note,
            'needs_length_repair':not band[0]<=count<=band[1] and len(note)<12}


def validate_added_sources(point:dict, selected:list[dict]) -> None:
    """Extra voices must bind new text; the AI final review checks semantic value."""
    for i in range(4,len(selected)):
        new=point['samples'][i].get('added_information_quote','')
        if (not isinstance(new,str) or len(new.strip())<8
                or new not in selected[i]['verbatim_quote']
                or any(new in prior['verbatim_quote'] for prior in selected[:i])):
            raise ValueError(f'第{i+1}条须在自己的samples项内填写added_information_quote：本条引文内连续至少8字的新信息，不能重复此前引文；无实际增量则删去该样本')
