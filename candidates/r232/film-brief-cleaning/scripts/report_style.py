"""Deterministic report presentation distilled from multiple manual briefs.

No baseline report is loaded at runtime. Workbench groups/evidence are read-only.
"""
from __future__ import annotations
import copy
import re

STYLE = 'MANUAL_BRIEF_V1'
SECTION_PREFIX = '二、舆论观点：'
SECTION_HEADING_TARGET = (38, 42)
SECTION_HEADING_MAX = 45
SECTION_HEADING_GUIDANCE = (
    '不填固定章节前缀。用“肯定剧集〔正面判断一〕、〔正面判断二〕，少量声音吐槽〔具体问题〕”。'
    '先按最终正文的样本计数顺序选最具代表性的2个正面判断，确有第三个时用顿号追加。'
    '每项写清评价对象和判断，保留正文先后。若批评是质疑、担忧等，可改用准确的批评动词。'
    '通常概括末段的1—2项实际问题，必要时可写3项；无批评段则省略后半句。'
    '人工只选了1个正面点可只写1个；无正面时用“关注剧集”概括实际入稿的客观讨论。'
    f'常规{SECTION_HEADING_TARGET[0]}—{SECTION_HEADING_TARGET[1]}字，最多{SECTION_HEADING_MAX}字。标点、数字和字母计入，空白不计。'
    '较短时可直接用，不为字数添词。不得添加正文没有的判断。'
)
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
    'rule':'正文至少6段，证据充足可写7段，含唯一批评末段。正式成稿将非批评段按本期实际去重样本数降序排列，主要批评最后。通常3—4条互补声音，充分时2条，短评有新增信息时可选5—6条。普通段目标200—250字。已审工作台不够支撑6个独立观点时，报告证据缺口。图表从上到下与最终正文段落逐项同序，“其他”固定最后。',
}


def point_rank(dataset:dict, point:dict) -> int:
    indices=point.get('group_indices',[])
    if not isinstance(indices,list) or not indices or any(type(i) is not int or not 0<=i<len(dataset['groups']) for i in indices):
        raise ValueError('正文点须绑定当前工作台的有效group_indices，不改变聚类归属')
    ranks={STANCE_ORDER.get(dataset['groups'][i].get('stance'),1) for i in indices}
    if len(ranks)!=1:
        raise ValueError('一个成文点不能将工作台正面、讨论与负面簇混为同一立场')
    return ranks.pop()


def validate_section_heading(heading: str, ranks: list[int]) -> None:
    error = 'section_heading须按正文顺序概括具体选题。' + SECTION_HEADING_GUIDANCE
    if len(re.sub(r'\s+', '', heading)) > SECTION_HEADING_MAX or any(c in heading for c in '\r\n；;'):
        raise ValueError(error)
    positives = ranks.count(0)
    ordinary = positives or ranks.count(1)
    summary, separator, criticism = heading.partition('，少量声音')
    prefix = '肯定剧集' if positives else '关注剧集'
    topics = summary.removeprefix(prefix).split('、')
    if (not summary.startswith(prefix) or '，' in summary or not all(part.strip() for part in topics)
            or not min(2, ordinary) <= len(topics) <= min(3, ordinary)):
        raise ValueError(error)
    if 2 not in ranks:
        if separator:
            raise ValueError(error)
        return
    verbs = ('吐槽', '质疑', '担忧', '看衰', '认为')
    verb = next((verb for verb in verbs if criticism.startswith(verb)), None)
    if not separator or verb is None or '，' in criticism or '少量声音' in criticism:
        raise ValueError(error)
    issues = criticism[len(verb):].split('、')
    if len(issues) > 3 or not all(part.strip() for part in issues):
        raise ValueError(error)


def compile_plan(dataset:dict, plan:dict, *, chart_order=False) -> dict:
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
    if chart_order and result.get('chart_review'):
        from chart_coverage import coverage_data
        try:
            counts = [row['count'] for row in coverage_data(dataset, result, result['dataset_sha256'])['rows'][:-1]]
        except ValueError as exc:
            raise ValueError(f'图表：{exc}') from exc
        order = sorted(range(len(points)), key=lambda i: (ranks[i] == 2, -counts[i], i))
        # Equal counts keep the same shares; prefer an alternating verb where the tie permits.
        start = 0
        while start < len(order):
            end = start + 1
            while end < len(order) and ranks[order[end]] != 2 and counts[order[end]] == counts[order[start]]:
                end += 1
            if end - start > 1:
                tied = order[start:end]
                for position in range(start, end):
                    prior_verb = points[order[position - 1]]['claim'][:2] if position else None
                    index = next((i for i, point_index in enumerate(tied)
                                  if points[point_index]['claim'][:2] != prior_verb), 0)
                    order[position] = tied.pop(index)
            start = end
    else:
        order = sorted(range(len(points)), key=lambda i: (ranks[i], i))
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
    if isinstance(result.get('chart_review'), dict):
        for override in result['chart_review'].get('source_overrides', []):
            if override.get('point_index') in mapping:
                override['point_index'] = mapping[override['point_index']]
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
