"""Small, deterministic comparison menus; never makes semantic keep decisions."""
from collections import Counter
from itertools import combinations
import math
import re

GENERIC = re.compile(r'肯定|认可|关注|讨论|质疑|批评|认为|观众|网友|网民|本期|本剧|这部剧|电视剧|剧集|确实|非常|真的|完全|整体|方面')
CONCEPTS=(
 (('互相利用','互相借力','彼此牵制','各有算计','权谋算计'),'关系博弈'),
 (('非黑即白','不完美','不单纯','灰度','人性复杂'),'复杂人物'),
 (('角色','人设'),'人物'), (('阶级','阶层'),'阶层'),
 (('私人复仇','报私仇','家仇','复仇'),'复仇'),
 (('朝堂博弈','权谋博弈','朝堂斗争'),'权谋'),
 (('童谣','童声','儿歌'),'童声'), (('原声','配乐','背景音乐','bgm'),'配乐'),
 (('妆发','妆容','妆造'),'妆造'), (('哭戏','情绪戏','情感爆发'),'情绪表演'),
 (('插叙','前史','倒叙','过往经历'),'前史叙述'),
)

def normalized_text(text,target=''):
    text=GENERIC.sub('',str(text).replace(target,'') if target else str(text))
    for aliases,canonical in CONCEPTS:
        for value in sorted(aliases,key=len,reverse=True):text=text.replace(value,canonical)
    return text.lower()

def features(text, target=''):
    text=normalized_text(text,target)
    words=re.findall(r'[\u4e00-\u9fff]+|[a-z0-9]+',text.lower())
    return Counter(w[i:i+n] for w in words for n in (2,3) for i in range(len(w)-n+1))

def ranked_passages(query, rows, target='', limit=3):
    """Search every old excerpt, regardless of its old stance/category."""
    q=features(query,target);bags=[features(r['text'],target) for r in rows]
    query_text=normalized_text(query,target);texts=[normalized_text(r['text'],target) for r in rows]
    concepts={canonical for _,canonical in CONCEPTS if canonical!='人物' and canonical in query_text}
    concept_df={c:sum(c in t for t in texts) for c in concepts}
    df=Counter(k for b in bags for k in b)
    weight=lambda k:1+math.log((len(rows)+1)/(df[k]+1))
    qv={k:min(v,2)*weight(k) for k,v in q.items()};qn=math.sqrt(sum(v*v for v in qv.values()))
    out=[]
    for row,b,body in zip(rows,bags,texts):
        common=q.keys() & b.keys()
        if len(common)<2:continue
        bv={k:min(v,2)*weight(k) for k,v in b.items()}
        norm=qn*math.sqrt(sum(v*v for v in bv.values()))
        score=sum(qv[k]*bv[k] for k in common)/norm if norm else 0
        # Rare domain concepts can be hidden inside otherwise unrelated old
        # groups. Boost their recall without deciding equivalence or novelty.
        score+=sum(.05*min(2,body.count(c))*math.log((len(rows)+1)/(concept_df[c]+1)) for c in concepts if c in body and concept_df[c]<max(2,len(rows)*.25))
        if score>=.025:out.append({**row,'score':round(score,5)})
    return sorted(out,key=lambda r:(-r['score'],r['id']))[:limit]

def similarity(left,right):
    a,b=set(features(left)),set(features(right))
    return len(a&b)/max(1,min(len(a),len(b)))

def boundary_pairs(clusters):
    """Review shared aspects/evidence and lexical neighbours, including neutral wrappers."""
    result=set()
    for a,b in combinations(clusters,2):
        shared=set(a.get('seed_ids',[])) & set(b.get('seed_ids',[]))
        score=similarity(a.get('core_mechanism','')+a.get('title',''),b.get('core_mechanism','')+b.get('title',''))
        neutral='objective' in (a.get('stance'),b.get('stance'))
        if a.get('category')==b.get('category') or shared or score>=(.12 if neutral else .22):
            result.add(frozenset((a['id'],b['id'])))
    return result

def old_candidates(excerpts, context, target):
    if not context:return {}
    rows=[{'id':s['id'],'text':s['text'],'group_id':g['id']} for g in context['previous_viewpoints'] for s in g['displayed_excerpts']]
    return {e['excerpt_id']:[{'history_sample_id':r['id'],'previous_group_id':r['group_id'],'score':r['score']} for r in ranked_passages(e['excerpt'],rows,target,2)] for e in excerpts}

def required_history_checks(group, hints, context=None, target=''):
    if context:
        rows=[{'id':s['id'],'text':s['text'],'group_id':g['id']} for g in context['previous_viewpoints'] for s in g['displayed_excerpts']]
        query=group.get('title','')+'。'+group.get('core_mechanism','')
        result=[];seen=set()
        for row in ranked_passages(query,rows,target,len(rows)):
            if row['group_id'] in seen:continue
            result.append({'history_sample_id':row['id'],'previous_group_id':row['group_id'],'score':row['score']});seen.add(row['group_id'])
            if len(result)==3:break
        return result
    rows={}
    for eid in group.get('seed_ids',[]):
        for r in hints.get(eid,[]):
            sid=r['history_sample_id']
            if sid not in rows or rows[sid]['score']<r['score']:rows[sid]=r
    # At most one candidate per old group; preserve cross-category matches.
    result=[];seen=set()
    for r in sorted(rows.values(),key=lambda r:(-r['score'],r['history_sample_id'])):
        if r['previous_group_id'] in seen:continue
        seen.add(r['previous_group_id']);result.append(r)
        if len(result)==3:break
    return result

def generic_quote(text):
    value=re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]','',text)
    value=re.sub(r'这部|这剧|真的|非常|完全|就是|整体|好看|难看|不错|不行|喜欢|不喜欢|好难看|好好看|太|很|好|差|剧情|妆造|服化道|演技|表演|演员|扬短避长|质感|在线|自然|尴尬','',value)
    return len(value)<5

def quote_options(text):
    """Only literal spans of the already displayed excerpt, never original-table backfill."""
    spans=[(m.start(),m.end()) for m in re.finditer(r'[^。！？!?\n]+[。！？!?\n]*',text)]
    options={}
    for i,(a,_) in enumerate(spans):
        for _,b in spans[i:i+4]:
            q=text[a:b].strip()
            if 20<=len(q)<=180 and not generic_quote(q):
                # Content cues are a reading order hint, not approval.
                score=2*bool(re.search(r'因为|通过|比如|例如|而是|反而|细节|前后|比例|位置|对比|不同|变化',q))
                score+=bool(re.search(r'第.{0,3}集|镜头|眼神|台词|动作|配乐|头饰|发型|构图|剪辑|配音|逻辑|铺垫|人设',q))
                score-=2*bool(re.search(r'预约|定档|应援|招商|预热|爆款|必爆',q))
                options[q]=(score,-abs(len(q)-100))
    selected=sorted(options,key=lambda q:(-options[q][0],-options[q][1],text.find(q)))
    distinct=[]
    for q in selected:
        if any(q in old or old in q for old in distinct):continue
        distinct.append(q)
        if len(distinct)==3:break
    return [{'quote_option_id':f'Q{i+1}','quote':q} for i,q in enumerate(distinct)]

def resolve_quote(sample,chosen):
    full=(sample.get('display_excerpt') or sample['excerpt']).strip()
    option=chosen.get('quote_option_id')
    if option:
        choices={r['quote_option_id']:r['quote'] for r in quote_options(full)}
        if option not in choices:raise ValueError('未知quote_option_id，只能选当前已审展示句菜单')
        if 'quote' in chosen and chosen['quote']!=choices[option]:raise ValueError('quote与quote_option_id不一致')
        return choices[option]
    return chosen.get('quote',full)


def same_manuscript_evidence(left,right):
    """Both reviewed passages occur in the other's supplied original text.

    Uses provenance already embedded in the workbench, not new raw retrieval.
    Similar opinions alone do not qualify: require long shared literal spans.
    """
    clean=lambda value:re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]','',str(value)).lower()
    def covered(sample,other):
        q=clean(sample.get('display_excerpt') or sample['excerpt'])
        body=clean(other['source']['data'].get(other['text_field'],'') or '')
        if len(q)<50:return False
        if q in body:return True
        if not any(q[i:i+40] in body for i in range(0,len(q)-39,10)):return False
        grams=[q[i:i+5] for i in range(len(q)-4)]
        return sum(g in body for g in grams)/len(grams)>=.8
    return covered(left,right) and covered(right,left)

def manuscript_families(samples):
    parents={s['candidate_id']:s['candidate_id'] for s in samples}
    def root(cid):
        while parents[cid]!=cid:cid=parents[cid]
        return cid
    for i,a in enumerate(samples):
        for b in samples[i+1:]:
            if root(a['candidate_id'])!=root(b['candidate_id']) and same_manuscript_evidence(a,b):
                x,y=sorted((root(a['candidate_id']),root(b['candidate_id'])));parents[y]=x
    groups={}
    for cid in parents:groups.setdefault(root(cid),[]).append(cid)
    duplicate_groups=[sorted(v) for v in groups.values() if len(v)>1]
    return {cid:f'F{i+1}' for i,g in enumerate(sorted(duplicate_groups)) for cid in g}
