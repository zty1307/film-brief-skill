"""Current-evidence aspect boundaries, with no category quotas."""
import re
from evidence_quality import boundary_pairs
CATEGORIES=('剧情与叙事','人物书写','演员表演','动作设计','服化道与视觉美术','视听技术与音乐','社会文化价值','播出与传播观察','节目机制与互动','其他独立方面')
BOUNDARY_RULE='''先按剧情与叙事、人物书写、演员表演、动作设计、服化道与视觉美术、视听技术与音乐、社会文化价值等方面理清边界，再在每个方面内形成确有独立机制的观点。category仅用于归类和导航，不是必须填满的维度配额；同方面可以没有观点，也可以有多个机制不同的观点。
每簇只填一个category字符串及8—80字core_mechanism，说明具体评价对象、处理方式和评价效果。不同演员/角色的近义演技评价要合并；服装、妆容、造型若都在评价同一种视觉效果，应合并而非逐件拆组。人物由剧本如何书写与演员如何演，不混在一点；不要把演员演技、服化道、剧情节奏等不同方面用“同时”“以及”拼成一个标题。已有具体正负评价时，不另列复述它们的“整体口碑两极分化”总括簇；取舍总括与子点，不靠客观类别重复装一遍。视觉整体泛赞与具体造型细节有交叉时，合并，或把前者收窄为确有独立证据的场景/镜头判断，并裁去重复的服装评价。
只对有交叉风险的簇写cluster_boundaries：同category、共享seed，或核心判断文字相近的簇，包括客观与正负面的跨立场组合。每对填left/right及至少12字distinction，比较双方实际seed支持的判断。没有必要为明显无关的每一对写套话；缺少风险对时控制器一次列明。客观不是另一个装赞贬的容器：“关注改编削弱格局”和“批评改编削弱格局”应先合并；正反两种真实评价可以并存，不能因此抹平分歧。先合并近义、总括与子例，再提交；不靠换category、改为关注、换演员或实例规避。每个title_evidence须加quote_snippet，逐字摘出支持该分句的当前seed原句。客观簇填objective_kind及seed_stance_review：neutral_fact的seed须是中性判断；opinion_distribution须有实际正反两面，不能把单边赞贬或未经核实的传播结果当客观。正面不足6组如实写明，先保留真实发现供有界补缺，未达6组不能作为完整成品；最多16个非空簇，不凑数。'''
from viewpoint_granularity import GRANULARITY_RULE
BOUNDARY_RULE += "\n" + GRANULARITY_RULE


def cluster_field_issues(g,excerpts=None):
    """Explain invalid fields without changing the evidence acceptance standard."""
    identity=g.get('id','未知簇');issues=[]
    if g.get('category') not in CATEGORIES:
        issues.append(f"{identity}: category须为规定方面之一，且是单个字符串；当前={g.get('category')!r}；可选={list(CATEGORIES)}")
    core=str(g.get('core_mechanism') or '').strip()
    if not 8<=len(core)<=80:issues.append(f"{identity}: core_mechanism须用8—80字写一个具体评价机制；当前{len(core)}字")
    if g.get('category')=='其他独立方面' and len(str(g.get('category_reason') or '').strip())<12:
        issues.append(f'{identity}: category_reason须至少12字说明为何不能归入已有方面；仅category=其他独立方面时必填')
    if g.get('performance_focus') is True and g.get('category')!='演员表演':
        issues.append(f"{identity}: 以表演为核心须归演员表演，不借其他方面拆近义演技簇")
    if g.get('stance')=='objective':
        rows=g.get('seed_stance_review',[]);seeds=g.get('seed_ids',[])
        if (not isinstance(rows,list) or any(not isinstance(r,dict) for r in rows)
                or any(not isinstance(r.get('excerpt_id'),str) for r in rows)
                or not isinstance(seeds,list) or any(not isinstance(s,str) for s in seeds)
                or {r.get('excerpt_id') for r in rows}!=set(seeds) or len(rows)!=len(seeds)):
            issues.append(f'{identity}: 客观簇须逐条填写seed_stance_review，对应当前seed_ids且不重复')
        else:
            kinds={str(r.get('stance')) for r in rows};kind=g.get('objective_kind')
            if not kinds <= {'positive','negative','mixed','neutral'} or any(len(str(r.get('reason','')).strip())<8 for r in rows):
                issues.append(f'{identity}: 客观seed须按原句填写实际立场与具体依据；当前立场={sorted(str(v) for v in kinds)}')
            if kind=='neutral_fact' and kinds!={'neutral'}:
                issues.append(f'{identity}: 中性事实不能用单边赞贬或混合评价支撑，请改归属或取舍；seed_stance_review当前={sorted(str(v) for v in kinds)}')
            if kind=='opinion_distribution' and not ('mixed' in kinds or {'positive','negative'}<=kinds):
                issues.append(f'{identity}: 评价分歧须有真实正反两面，不能将单边批评包装成客观；当前={sorted(str(v) for v in kinds)}')
            if kind not in ('neutral_fact','opinion_distribution'):issues.append(f'{identity}: 客观簇须填写objective_kind')
    if excerpts is not None:
        evidence=g.get('title_evidence')
        if not isinstance(evidence,list) or not 1<=len(evidence)<=3:
            count=len(evidence) if isinstance(evidence,list) else type(evidence).__name__
            issues.append(f'{identity}: title_evidence须为1—3条分句证据，当前={count}；每个标题分句只需一条，不是每个seed另写一条')
        if isinstance(evidence,list):
            for index,ev in enumerate(evidence):
                field=f'{identity}: title_evidence[{index}]'
                if not isinstance(ev,dict):issues.append(field+'须为含claim、seed_ids、quote_snippet的对象');continue
                if not str(ev.get('claim') or '').strip():issues.append(field+'.claim不能为空')
                seeds=ev.get('seed_ids')
                if not isinstance(seeds,list) or not 1<=len(seeds)<=3:
                    issues.append(field+f'.seed_ids须为1—3个当前E编号，当前={seeds!r}')
                    valid=[]
                else:
                    valid=[sid for sid in seeds if isinstance(sid,str) and sid in excerpts]
                    unknown=[sid for sid in seeds if not isinstance(sid,str) or sid not in excerpts]
                    if unknown:issues.append(field+f'.seed_ids含未知编号={unknown!r}')
                quote=ev.get('quote_snippet')
                if not isinstance(quote,str) or len(quote)<8:
                    issues.append(field+'.quote_snippet须为当前seed至少8字连续原句；当前长度='+str(len(quote) if isinstance(quote,str) else 0))
                elif not any(quote in excerpts[sid]['excerpt'] for sid in valid):
                    context_only=[sid for sid in valid if quote in str(excerpts[sid].get('context') or '')]
                    issues.append(field+f'.quote_snippet未在seed的excerpt字段逐字出现；仅context匹配={context_only}。context不能代替命名证据，请从所列E句excerpt裁取连续原句')
    return issues

def validate_cluster_boundaries(answer,clusters,excerpts=None):
    seen=set();issues=[]
    for g in clusters:
        issues.extend(cluster_field_issues(g,excerpts))
        key=re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]','',str(g.get('core_mechanism') or '').strip())
        if key in seen:issues.append(f"{g.get('id')}: 核心机制重复，不能用客观或其他类别另列同一判断")
        seen.add(key)
    if issues:raise ValueError('一次修复以下具体字段，保留已通过内容：\n'+'\n'.join(issues))
    expected=boundary_pairs(clusters)
    possible={frozenset((a['id'],b['id'])) for i,a in enumerate(clusters) for b in clusters[i+1:]}
    rows=answer.get('cluster_boundaries')
    if not isinstance(rows,list):raise ValueError('须填写cluster_boundaries，无交叉风险对时填空列表')
    actual=set()
    for row in rows:
        if not isinstance(row,dict):raise ValueError('cluster_boundaries每项须为对象')
        pair=frozenset((row.get('left'),row.get('right')))
        if pair not in possible or pair in actual or len(str(row.get('distinction') or '').strip())<12:
            raise ValueError(f'cluster_boundaries当前项={row!r}；须用left/right填写两个不同的现有簇ID，distinction至少12字；同一无序对仅一次。有效ID={[g["id"] for g in clusters]}')
        actual.add(pair)
    if not expected<=actual:raise ValueError('cluster_boundaries缺少以下交叉风险对（含客观/赞贬跨类）；先合并重叠判断，只为独立点写区别：'+str([sorted(p) for p in expected-actual]))
    if sum(g.get('stance')=='positive' for g in clusters)<6:
        if len(str(answer.get('evidence_shortfall_reason') or '').strip())<20:raise ValueError('独立观点不足时须写至少20字证据缺口；不强行拆组达标')
