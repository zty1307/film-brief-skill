"""Generic checks on selected evidence, never a source of additional opinions."""
from __future__ import annotations
import re
from workbench_completion import SUPPORTED_POSITIVE_POLICY

def normalize(value):
    return re.sub(r'[^\u3400-\u9fffA-Za-z0-9]', '', str(value)).lower()

def comparative_source(text, title, terms):
    names={normalize(v) for v in re.findall(r'《([^》]{1,60})》', text+' '+title)}
    target={normalize(v) for v in terms}
    return bool(names-target) or bool(re.search(r'另一部|另一边|两部剧|两剧|同档.{0,8}对比|对比.{0,12}剧',text+' '+title))

def scope_issue(item, record, state, review):
    """Conservative ambiguity guard; it does not assert semantic correctness."""
    quote=item['excerpt'];terms=state.get('terms',[state['target']])
    from rapid_film_workbench import display_excerpt
    visible=display_excerpt(quote)
    from report_evidence import release_notice_only
    if release_notice_only(visible):
        return '这段只有歌曲上线与发布信息，不能作为观点保留。选同一窗口有真实听感或创作判断的连续原文，找不到则reject。'
    music_count=sum(bool(re.search(cue,quote,re.I)) for cue in ('编曲','作曲','作词','演唱','歌词','嗓音|声线','旋律','主题曲|OST'))
    category=item.get('category') or item.get('evidence_focus')
    writing_claim=category=='人物书写' or (category=='社会文化价值' and re.search(r'剧作|剧本|旁白|叙事',item.get('core_mechanism','')))
    if writing_claim and music_count>=2 and not re.search(r'剧本|剧作|编剧|剧情安排|人物塑造',quote):
        return '本段中心是歌曲创作、歌词或演唱，不能借角色成长措辞证明剧作人物书写或演员表演；在本窗口另选本组实际核心评价，否则reject'
    # A passage explicitly about the literary work cannot borrow a series title.
    literary=re.search(r'笔下|原著|小说|书中|书里|原文',quote)
    screen=re.search(r'剧版|电视剧|电影|影片|剧集|剧中|改编|编剧|导演|饰演|演员|镜头|预告|正片|首播|开播|本剧|该剧|这部剧|整部剧|全剧|这剧|新剧|大剧|网剧|短剧|剧组|剧宣|本片|该片|播出|追剧',quote)
    adaptation_with_screen_format = (
        re.search(r'改自|改编自',quote)
        and re.search(r'单集\s*\d{1,3}\s*分钟|电视台.{0,12}独播|视频网站.{0,12}独播',quote))
    if literary and not (screen or adaptation_with_screen_format):
        return '本句仅说明原著/小说，未明确评价剧版或改编；在本窗口另选明确剧版的连续原句，否则reject，不借同名标题放行'
    if any(term in visible for term in terms):return None
    text=str(record['data'].get(item['text_field']) or '')
    title=str(record['data'].get('标题') or item.get('title') or '')
    if comparative_source(text,title,terms):
        from target_reference import local_binding,local_reference_issues
        problems=local_reference_issues(quote,local_binding(item,record,state),review,state)
        if problems:return '作品指代校验的实际问题：'+'；'.join(problems)
    return None

def collapse_display_copies(state,sources):
    """Exact displayed copies and mutual verbatim manuscript containment only."""
    protected={v['candidate_id'] for guide in state.get('editorial_writing_guidance',{}).values() for v in guide.get('preferred',[])}
    protected.update(v['candidate_id'] for row in state.get('editorial_evidence_bindings',[]) for v in row.get('title_support',[]))
    protected.update(v['candidate_id'] for review in state.get('editorial_history_reviews',{}).values() for v in review.get('current_evidence',[]) if v.get('candidate_id'))
    protected.update(state.get('locked_candidate_ids',[]))
    protected.update(state.get('supplement_committed_candidate_ids',[]))
    keeps=sorted((v for v in state['judgments'].values() if v.get('decision')=='keep'),
                 key=lambda v:(v['candidate_id'] not in protected,-v.get('score',0),v['candidate_id']))
    from difflib import SequenceMatcher
    full={};accepted=[]
    for item in keeps:
        excerpt=normalize(item['excerpt'])
        key=(item['source_id'],item['text_field'])
        if key not in full:full[key]=normalize(sources[key[0]]['data'].get(key[1]) or '')
        duplicate=None;kind=None;grams={excerpt[i:i+5] for i in range(max(0,len(excerpt)-4))}
        for prior,prior_excerpt,prior_full,prior_grams in accepted:
            if len(excerpt)>=50 and excerpt==prior_excerpt:
                duplicate=prior;kind='NORMALIZED_DISPLAY_COPY';break
            if (min(len(excerpt),len(prior_excerpt))>=70 and min(len(excerpt),len(prior_excerpt))/max(len(excerpt),len(prior_excerpt))>=.85
                    and len(grams & prior_grams)>=.8*max(1,min(len(grams),len(prior_grams)))):
                matcher=SequenceMatcher(None,excerpt,prior_excerpt,autojunk=False)
                if matcher.ratio()>=.90 and matcher.find_longest_match().size>=70:
                    duplicate=prior;kind='NEAR_VERBATIM_DISPLAY_WITH_LONG_IDENTICAL_SPAN';break
            if min(len(excerpt),len(prior_excerpt))>=50 and excerpt in prior_full and prior_excerpt in full[key]:
                duplicate=prior;kind='MUTUAL_VERBATIM_MANUSCRIPT_CONTAINMENT';break
        if duplicate:
            item.update(decision='reject',initial_decision='keep',duplicate_of=duplicate['candidate_id'],reason='已审展示同稿去重；保留一个来源，不将转载当独立表达')
            state.setdefault('selected_copy_audit',[]).append({'candidate_id':item['candidate_id'],
                'retained_candidate_id':duplicate['candidate_id'],'kind':kind,'ai_semantics_inferred':False})
        else:accepted.append((item,excerpt,full[key],grams))

SPECIFIC_CUES=(
    (r'无台词|不靠台词|不用台词',r'无台词|不靠台词|不用台词|没有台词|无声胜有声'),
    (r'调色|饱和度|低饱和',r'调色|色调|饱和|滤镜|明暗'),
    (r'电影质感|胶片质感',r'电影|胶片'),
    (r'全程实景|全部实景',r'全程实景|全部实景|全剧实景'),
    (r'分层编排|分层叙事结构',r'分层|分为|专属曲.{0,20}(?:主题曲|纯乐)|主题曲.{0,20}纯乐'),
)

def specific_claim_issues(claim,quotes):
    support='\n'.join(quotes)
    return [pattern for pattern,evidence in SPECIFIC_CUES if re.search(pattern,claim) and not re.search(evidence,support)]

def title_parts(title):
    return [v.strip() for v in re.split(r'[，,；;]',title) if v.strip()]

def validate_title_support(title,rows,samples):
    parts=title_parts(title);errors=[];seen=set();proofs=set();by_claim={}
    if not isinstance(rows,list):return ['title_support须逐分句给claim_index、candidate_id与quote_snippet']
    for row in rows:
        i=row.get('claim_index') if isinstance(row,dict) else None
        if type(i) is not int or i<0 or i>=len(parts):
            errors.append('title_support的claim_index须为当前标题分句的0起始整数');continue
        cid=row.get('candidate_id');quote=row.get('quote_snippet')
        if not isinstance(cid,str) or cid not in samples or not isinstance(quote,str) or len(quote)<8 or quote not in samples[cid].get('display_excerpt',samples[cid]['excerpt']):
            errors.append(f'标题分句{i}须绑定本组最终仍保留样本的至少8字连续原句');continue
        key=(i,cid,quote)
        if key in proofs:
            errors.append(f'标题分句{i}不能重复提交完全相同的证据');continue
        proofs.add(key);seen.add(i);by_claim.setdefault(i,[]).append(quote)
    for i,quotes in by_claim.items():
        if specific_claim_issues(parts[i],quotes):
            errors.append(f'标题分句{i}的无台词、调色/电影或全程等具体说法未出现在所绑定证据；删去失据细节，不扩大论断')
    missing={i:parts[i] for i in range(len(parts)) if i not in seen}
    if missing:
        errors.append(f'title_support缺少以下标题分句（索引与分句）：{missing}；'
                      '从实际展示句按对应索引补逐字证据，无支持则删该分句')
    return errors


def validate_writing_guidance(value,samples,max_chars=180,require_dual_support=False):
    if not isinstance(value,dict) or type(value.get('eligible')) is not bool or len(str(value.get('reason','')).strip())<12:
        raise ValueError('writing_guidance须有eligible布尔值及至少12字具体取舍理由')
    standalone=value.get('standalone_eligible');voices=value.get('independent_voice_ids')
    if type(standalone) is not bool or not isinstance(voices,list) or any(not isinstance(cid,str) or cid not in samples for cid in voices) or len(set(voices))!=len(voices):
        raise ValueError('须区分eligible可引用与standalone_eligible可独立成段；independent_voice_ids列实际独立声音ID，不把近同稿当两份')
    if standalone and (not value['eligible'] or len(voices)<2):
        raise ValueError('独立成段至少两份有效独立声音；单源或近同稿只能作补充，不得标standalone_eligible=true')
    identities=[samples[cid].get('identity') for cid in voices if samples[cid].get('identity')]
    if len(set(identities))!=len(identities):raise ValueError('同一作者不能作为两份独立声音')
    preferred=value.get('preferred',[])
    if not isinstance(preferred,list) or len(preferred)>2 or (value['eligible'] and not preferred) or (not value['eligible'] and preferred):
        raise ValueError('可入稿组须选1—2条具体互补preferred；只有泛赞或不适合创作观点时eligible=false且preferred为空')
    seen=set()
    for position,row in enumerate(preferred):
        cid=row.get('candidate_id') if isinstance(row,dict) else None;quote=row.get('quote_snippet') if isinstance(row,dict) else None
        if cid not in samples or cid in seen or not isinstance(quote,str) or len(quote)<12 or len(quote)>max_chars or quote not in samples[cid].get('display_excerpt',samples[cid]['excerpt']):
            raise ValueError(f'preferred[{position}]（{cid}）须为本组仍保留的不同样本ID及12—{max_chars}字连续具体支持原句，不能借未展示上下文')
        from evidence_quality import generic_quote
        if generic_quote(quote):raise ValueError('preferred只有泛泛态度，须有具体做法、变化、情节或观感依据；没有则不入稿')
        from report_evidence import release_notice_only
        if release_notice_only(samples[cid].get('display_excerpt') or samples[cid]['excerpt']):
            raise ValueError('preferred来源的展示句只有歌曲上线资讯，不能用署名或歌词取向充当评价；换真实评价，或将本组标为不入稿')
        from report_writing import (UNVERIFIED_QUANTITIES,UNVERIFIED_SCALE,UNVERIFIED_CHINESE_QUANTITIES,
                                    UNVERIFIED_PROCESS_OR_HEARSAY,UNVERIFIED_PRODUCTION_DURATION,
                                    has_absolute_production_assertion,has_dangling_scene_fragment)
        if any(p.search(quote) for p in (UNVERIFIED_QUANTITIES,UNVERIFIED_SCALE,UNVERIFIED_CHINESE_QUANTITIES,UNVERIFIED_PROCESS_OR_HEARSAY,UNVERIFIED_PRODUCTION_DURATION)) or has_absolute_production_assertion(quote) or has_dangling_scene_fragment(quote):
            raise ValueError(f'preferred[{position}]（{cid}）含写稿禁止的未核数量、传闻或绝对制作断言，须换可安全入稿的具体片段；没有则eligible和standalone_eligible均为false，preferred置空')
        seen.add(cid)
    if require_dual_support and standalone and (len(seen)!=2 or not seen.issubset(voices)):
        raise ValueError('正面独立成段须在preferred提交两条独立声音的连续原句，每条对本段集中主题提供实际评价，互补子评价允许共同支撑标题。'
                         '如果两句实际评价不同对象或做法，收窄到两句都支持的判断；仍不足则standalone_eligible=false。')
    return value

def final_reference_issues(state):
    from rapid_film_workbench import display_excerpt
    kept={cid:v for cid,v in state['judgments'].items() if v.get('decision')=='keep'}
    errors=[]
    for row in state.get('editorial_evidence_bindings',[]):
        for proof in row.get('title_support',[]):
            cid=proof['candidate_id'];item=kept.get(cid)
            if item is None or item['group_index']!=row['group_index'] or proof['quote_snippet'] not in display_excerpt(item['excerpt']):
                errors.append(f'最终去重后组{row["group_index"]}标题证据失去实际展示句：{cid}')
    if state.get('final_history_check_policy')=='ALL_FINAL_RELATIONS_V1':
        for index,review in state.get('editorial_history_reviews',{}).items():
            for proof in review.get('current_evidence',[]):
                cid=proof['candidate_id'];item=kept.get(cid)
                if item is None or item['group_index']!=int(index) or proof['quote_snippet'] not in display_excerpt(item['excerpt']):
                    errors.append(f'最终去重后组{index}跨日当前证据失去实际展示句：{cid}')
    for index,guide in state.get('editorial_writing_guidance',{}).items():
        for proof in guide.get('preferred',[]):
            cid=proof['candidate_id'];item=kept.get(cid)
            if item is None or item['group_index']!=int(index) or proof['quote_snippet'] not in display_excerpt(item['excerpt']):
                errors.append(f'最终去重后组{index}入稿优选失去实际展示句：{cid}')
    for row in state.get('editorial_overlap_reviews',[]):
        for side in ('left','right'):
            proof=row[side+'_evidence']
            item=kept.get(proof['candidate_id'])
            if (item is None or item['group_index']!=row[side]
                    or proof['quote_snippet'] not in display_excerpt(item['excerpt'])):
                errors.append(f"最终去重后组{row[side]}的区别证据失去实际展示句")
    return errors

FINAL_EDITOR_RULES='''本包只含已审展示句，不含原始表或候选池。完成一次末端标题证据核对、去交叉和入稿选句，不增加新观点/新来源、不重新查原表。
观点的评价对象可以是实际放出的预告、角色短片、海报或宣传视频。只要原句对物料的处理方式和观感有具体评价，可按物料范围认定为独立正面证据；标题和写稿必须说清媒介与范围，不外推到正片、全剧演技或播后成绩。纯上线消息、宣传口号、演员名单、未播期待、配文转述和重复通稿仍不能当作独立评价。用海报和默片作为同一判断的互补证据时，标题须覆盖二者共同支持的处理和效果，不把两者都写成默片内容。
先核对各组单一核心：同方面且同一评价机制的近义组应合并，用merge_into指向证据更具体的组；同category、同stance且实质近义可合并。overlap_pairs标明的跨category正面重复可指向更有证据的一组，被合并组不迁入原引文，避免自动改分类；不能把不同机制硬并。换主演/配角、眼神/小动作例子不当然构成独立机制；没有交叉就不合并，不为三天不同拆点。merged来源组只填group_index、merge_into、merge_reason、reviews。
retained_evidence是本组全部已审留存展示句。title_support、independent_voice_ids和preferred都可从这里选。未列入editable_samples的已审句仍然保留。最终title通常24—36字，12—48字，以认可/肯定/关注/质疑等判断开头；每个分句按逗号分为claim_index=0,1...，用title_support绑定至少8字实际展示原句；每个分句至少一条有效证据，同一claim_index可以有多条互补证据，但不能重复同一ID的同一原句。原标题的演员名单、无台词、调色、电影质感等细节若失去实际支持就删除，仅收窄原主张；不得换新观点、扩大为整体好坏或借最初seed和未展示上下文补证。core_mechanism同步概括已有评价，保留category与stance；不能无故收窄到单一作者的细节，排除本来同主题的其他有效评价。合并目标可用被合并组仍保留的候选ID支撑，目标标题仍须是一个共同核心。
writing_guidance选择1—2条有具体做法、情节、变化或观感依据且互补的样本，preferred逐项填candidate_id和12—180字连续quote_snippet，写稿必须保留该支持信息。片段不能只有泛赞、演员名单或履历；只有泛态度、纯宣发/热度、书评或难以安全入稿时eligible=false、preferred=[]并说明，不硬凑素材。取自display_excerpt，不借原文、上下文补样。eligible表示可引用；另填standalone_eligible与independent_voice_ids。独立成段须至少两条真实独立有效声音，近同稿按一份声音，不因不同账号而重复计数。不同作者对同一具体场面得出相同结论，若原句各有明确评价且无同稿、照搬或同一转述来源，是两份独立声音。不要仅因评价方向或结论相近而判只有一份。先核声音独立，再核该组是否与其他组实质交叉；两份独立声音成立，不自动等于足以独立成段。若两句只是同义的简短褒贬（如都说打戏流畅或节奏不错），没有具体场面、处理方式、变化或其他可展开的信息，可留在工作台作补充，但standalone_eligible=false。独立方面本身不是短段例外的重要性理由；不能为了达到六段把这种点计入成稿数量。两份具体、信息充分的独立评价仍可支持简洁短段，不机械用字数否定。每个独立声音须对同一个核心判断提供实际支持；仅提到相同人物、同一音乐或同一事件不能算支持。比如发布消息不能证明听感，旧作口碑不能证明本剧表演，剧情介绍不能自动算认可。不同观点先对照核心判断和实际引句，若一个只是另一个的细节或例证，合并；确有独立机制才保留，标题和引句分别落在该机制上。单条可eligible=true但standalone_eligible=false；如已识别近同稿，在reviews中排除可编辑的重复项并说明。未核数量、传闻、购买参与邀请不要作为preferred。所有建议只是AI选择，不冒称人工确认。
以能否独立成文核对本组。标题每项判断须由真实评价支持，歌曲上线、词曲署名和歌词主题介绍只作背景。可用的音乐评价要说清具体歌曲或物料、实际演唱/旋律/歌词判断及其作用。总体声量与口碑分布不算正文点。批评组将在成稿末段合写，只计一段。材料不足以支撑6段时据实记录，不把一个判断切成两组。
editable_samples是每组最多2条可编辑的风险/代表句，reviews完整覆盖这个子集。它的条数不能用于判断独立声音数，计数应检查retained_evidence中的全部已审句。trim仅在本条原excerpt内选连续原文，保留原有目标名、立场及70/50字下限；不得改其他展示句。上下文只确认作品指代。仅小说、别剧、错方面或单边赞贬冒充客观的本包句须exclude。无足够连续完整句就排除，不补字。
有history_candidates的组另填history_check：对照实际旧句而非旧标题，复核continuing/developed/opposing/unseen_angle，previous_sample_ids只选包内相关H句ID，reason至少15字。旧句已写出的算计、灰度、改编批评等不能改名当新增；换作者、人物、例子、集数和从预告到正片仍可延续。developed还须当前展示句的novel_information_quote和why_not_old（至少15字），具体说明新增机制；没有新增配额。给定候选都不相关可unseen_angle，不声称全网新增。
主窗口一次写完整答卷后立即editor-collect；只修反馈，不另起编辑Agent或全量复审，不改时间、原表或规则。'''

def prepare_final_editor(workspace,state,digest,editor_seconds=480):
    import time
    from rapid_film_workbench import save,display_excerpt,CLOSEOUT_RESERVE_SECONDS,excerpt_min_chars
    from final_sample_review import source_map
    from target_reference import local_binding
    from display_editor import risk
    remaining=state['started_epoch']+state['deadline_seconds']-time.time()
    if remaining<CLOSEOUT_RESERVE_SECONDS+150:
        state['editorial_status']='SKIPPED_INSUFFICIENT_TIME';return None
    sources=source_map(workspace);groups=[];context=state.get('discovery_history_context')
    old={s['id']:{**s,'previous_group_id':g['id']} for g in (context or {}).get('previous_viewpoints',[]) for s in g['displayed_excerpts']}
    for index,g in enumerate(state['pool']):
        keeps=[v for v in state['judgments'].values() if v['group_index']==index and v['decision']=='keep']
        if not keeps:continue
        ranked=sorted(keeps,key=lambda v:(-risk(v),-v.get('score',0),v['candidate_id']));picked=ranked[:1]
        if len(keeps)>1 and (g['stance']!='正向词倾向' or risk(picked[0])>=6):
            picked.append(min((v for v in keeps if v not in picked),key=lambda v:(risk(v),-v.get('score',0))))
        row={'group_index':index,'title':g['aspect'],'category':g['category'],'stance':g['stance'],
             'core_mechanism':g['core_mechanism'],'total_selected':len(keeps),
             'title_candidates':[{'candidate_id':v['candidate_id'],'display_excerpt':display_excerpt(v['excerpt']),'channel':v.get('source_channel','')} for v in keeps],
             'samples':[{'candidate_id':v['candidate_id'],'excerpt':v['excerpt'],'risk_hint':risk(v),'source_channel':v.get('source_channel',''),
                         'work_reference':local_binding(v,sources[v['source_id']],state),'min_excerpt_chars':excerpt_min_chars(v.get('source_channel'))} for v in picked]}
        hr=g.get('history_review') or {}
        if context and hr.get('relation') in ('continuing','developed','opposing','unseen_angle'):
            from evidence_quality import required_history_checks
            hints=required_history_checks({'title':g['aspect'],'core_mechanism':g['core_mechanism']},{},context,state['target'])
            ids=[v['history_sample_id'] for v in hints]
            ids+= [v.get('history_sample_id') for v in hr.get('previous_evidence',[])]
            row.update(initial_history={k:hr.get(k) for k in ('relation','reason','previous_group_ids','change_statement')},
                       history_candidates=[old[sid] for sid in dict.fromkeys(ids) if sid in old])
        groups.append(row)
    if not groups:state['editorial_status']='SKIPPED_NO_SAMPLES';return None
    preferred_limit=120 if state.get('candidate_closeout_policy') else 180
    from rapid_film_workbench import WORK_SCOPE_RULE
    editor_rules=FINAL_EDITOR_RULES.replace('12—180',f'12—{preferred_limit}') + WORK_SCOPE_RULE
    editor_rules += '\n概括层级：先从本组全部retained_evidence选出两份支持同一评价的独立声音，再概括最终标题。标题概括评价，不把其中一条的场景、动作或人物名字变成整组必须满足的条件。原核心有两份充分支持时保留；只有失据的判断才删减。两个不同场景都具体支持同一种表演效果，可以相互印证，不必同演一场戏。反过来，不得把实际上不同的评价装入“整体很好”。如果最终仅一条证据成立，照实标记补充，不通过改标题制造第二条。'
    editor_rules += '\n独立性只按来源判断：不同作者独立作出的同一角度评价可互相印证，不要求第二类角度、不同例子或不同措辞。若判近同稿，reason必须写出识别转载或照搬的具体证据；不得仅因措辞相近或结论一致推断同源。共同核心可以由不同具体例子支持，例如角色反差与手势表现共同支持演绎细腻，不能因为细节层级不同否定；若两句实际对象或评价机制无共同判断才判不支持。保留同向互证，合并的是观点组，不是独立作者声音。独立成段同时预估写作展开：从全部留样择出通常3—4条具体句，两条充分可用两条。只有几字泛赞而其余都是别点内容时，不独立成段；不要求每个样本都包含长篇分析。'
    editor_rules+='\n每个输入group_index都须返回实际答卷，reviews须恰好覆盖对应editable_samples。禁止把输入组原样复制到答卷。先做整包去交叉：同一演技机制不按演员拆；总体泛赞不能与其造型/人物子例并列。可合并同方面同立场近义组，或收窄为有独立证据的不同处理并去掉交叉尾句，不用“宏观与细节”充区别。'
    editor_rules+='\n有历史时，全部关系都按最终retained_evidence复核，不能沿用已丢弃seed细节。history_candidates是编号列表，实际旧句只读顶层history_samples[id]。history_check除旧句编号与具体reason外，current_evidence填1—2条本组最终保留candidate_id及12—140字连续quote_snippet。换为人物曲就不能再说童谣插曲；书写设定与表演执行不是自动同一判断。已在旧簇尾句出现的细节也算旧材料。'
    history_samples={v['id']:{'id':v['id'],'text':v['text'],'previous_group_id':v['previous_group_id']}
                     for row in groups for v in row.get('history_candidates',[])}
    payload_groups=[]
    for row in groups:
        exposed={key:value for key,value in row.items()
                 if key not in ('title_candidates','samples','history_candidates')}
        exposed['retained_evidence']=row['title_candidates']
        exposed['editable_samples']=row['samples']
        if 'history_candidates' in row:
            exposed['history_candidates']=[v['id'] for v in row['history_candidates']]
        payload_groups.append(exposed)
    import json
    definition_path=workspace/'viewpoint_definitions.json'
    definitions=json.loads(definition_path.read_text(encoding='utf-8-sig')) if definition_path.is_file() else {}
    positive_lookup={state['pool'][g['group_index']].get('viewpoint_id'):g['group_index']
                     for g in groups if g['stance']=='正向词倾向' and g['total_selected']>=2}
    overlap_pairs=[]
    for row in definitions.get('cluster_boundaries',[]):
        if row.get('left') in positive_lookup and row.get('right') in positive_lookup:
            a,b=sorted((positive_lookup[row['left']],positive_lookup[row['right']]))
            if a!=b and [a,b] not in overlap_pairs:
                overlap_pairs.append([a,b])
    editor_rules+=('\n正面独立成段时preferred必须有两条，且每条都从independent_voice_ids中选。证据按完整观点核对，不按一个细节卡死：同一集中评价主题可以由互补的具体子评价支撑，如演唱听感与歌曲人物适配共同支撑原声音乐感染力，服饰与置景共同支撑同一视觉风格。每份声音都须有对本主题的实际评价，每个标题分句至少一条具体证据，全段至少两份独立声音；不要求每条引文重复同一细节或同时覆盖所有分句。不能仅因都提到演员、音乐或预告就合组；不能把无关问题装进泛泛的整体很好，也不能与别段重复核心评价。独立来源同向互证与不同细节互补均可，纯资讯、名单、期待及同稿转载均不能补足证据。实际证据仍不足时设standalone_eligible=false，一条留样保留作补充。')
    editor_rules+=('\n先逐对检查overlap_pairs。将双方准备计数的实际原句放在一起比较。'
                   '相同评价只因换例子、换媒介、换题头或从预告转到剧集，不构成两段；应合并或收窄。'
                   '逐句检查双方各两条preferred；删掉会进入另一组的旁枝，只保留本组特有的处理和观感。'
                   '若一边两个声音真正共同支持的内容仍是另一边同一评价，哪怕两段标题不同也不能一起计数。'
                   '只有两组最终都可独立成段时才填该对overlap_reviews：left/right按给定顺序，'
                   '分别提交本组实际已留的left_evidence/right_evidence（candidate_id和逐字quote_snippet），'
                   'distinction解释两个原句所支持的不同评价对象或机制，至少16字。不能沿用初始边界话术。')
    binding=digest([state['binding'],groups,editor_rules,overlap_pairs]);answer=workspace/'display_editor_answer.json'
    payload={'target':state['target'],'target_terms':state.get('terms',[state['target']]),'fingerprint':binding,
        'rules':editor_rules,'title_policy':'FINAL_DISPLAY_EVIDENCE_V2','groups':payload_groups,
        'overlap_pairs':overlap_pairs,'history_samples':history_samples,'answer_file':str(answer),
        'execution_mode':'MAIN_WINDOW_SINGLE_WRITER','max_work_seconds':max(0,min(editor_seconds,int(remaining-CLOSEOUT_RESERVE_SECONDS))),
        'answer_schema':{'fingerprint':binding,'overlap_reviews':[{
            'left':'较小group_index','right':'较大group_index',
            'left_evidence':{'candidate_id':'左组仍保留的已审ID','quote_snippet':'左组连续8字以上原句'},
            'right_evidence':{'candidate_id':'右组仍保留的已审ID','quote_snippet':'右组连续8字以上原句'},
            'distinction':'至少16字的实际区别'}],
            'groups':[{'group_index':'integer','merge_into':None,
            'title':'12—48字本期原核心的有据标题','core_mechanism':'8—80字，与本轮定稿标题同步，只写留样能证明的核心。原标题删掉的因果、阶段、对象也从core_mechanism删除；两者会一起用于补缺审样',
            'title_support':[{'claim_index':0,'candidate_id':'最终保留ID','quote_snippet':'展示句连续至少8字'}],
            'writing_guidance':{'eligible':True,'standalone_eligible':True,'independent_voice_ids':['独立声音ID1','独立声音ID2'],'reason':'至少12字','preferred':[{'candidate_id':'已审ID','quote_snippet':f'具体支持原句12—{preferred_limit}字'}]},
            'history_check':'有history_candidates的组填写relation、previous_sample_ids、reason、current_evidence:[{candidate_id,quote_snippet}]；developed另填novel_information_quote、why_not_old',
            'reviews':[{'candidate_id':'editable_samples ID','action':'keep/trim/exclude','reason':'具体依据','selected_excerpt':'仅trim填'}]}]}}
    # A new evidence set requires a new answer binding. Retain the old
    # stage's artifacts for audit, away from the current writer's paths.
    stamp = time.time_ns()
    for previous in (answer, workspace/'display_editor_feedback.json'):
        if previous.is_file():
            previous.rename(previous.with_name(f'{previous.stem}.previous-{stamp}.json'))
    save(workspace/'display_editor_input.json',payload)
    save(answer,{'fingerprint':binding,'groups':[],'overlap_reviews':[]})
    state.update(status='DISPLAY_EDITOR_REQUIRED',editorial_status='PENDING',editorial_binding=binding,
                 editorial_groups=groups,editorial_overlap_pairs=overlap_pairs,editorial_started_epoch=time.time(),
                 final_history_check_policy='ALL_FINAL_RELATIONS_V1' if context else None,editorial_history_samples=history_samples)
    return {'read_now':str(workspace/'display_editor_input.json'),'answer_file':str(answer)}

def apply_final_editor(workspace,state,answer):
    import hashlib,time
    from rapid_film_workbench import save,display_excerpt,resolve_verbatim_span,excerpt_length_error,source_selection_key
    from final_sample_review import source_map
    from display_editor import target_guard,stance_trim_guard,TITLE_PATTERN
    if not isinstance(answer,dict) or state.get('editorial_status')!='PENDING' or answer.get('fingerprint')!=state.get('editorial_binding'):
        raise ValueError('末端证据核对指纹或阶段不匹配')
    expected={g['group_index']:g for g in state['editorial_groups']};rows=answer.get('groups')
    if not isinstance(rows,list) or len(rows)!=len(expected):raise ValueError('答卷须覆盖本包全部group_index')
    indexed={r.get('group_index'):r for r in rows if isinstance(r,dict)}
    if len(indexed)!=len(rows) or set(indexed)!=set(expected):raise ValueError('group_index未知、重复或遗漏')
    errors=[];merges={};cross_category_merges={}
    requested_overlap={tuple(pair) for pair in state.get('editorial_overlap_pairs',[])}
    for i,r in indexed.items():
        target=r.get('merge_into')
        if target is None:continue
        if type(target) is not int or target not in expected or target==i or indexed[target].get('merge_into') is not None:
            errors.append(f'组{i}合并目标须为不再合并的其他实际组');continue
        same_category=expected[i]['category']==expected[target]['category']
        if expected[i]['stance']!=expected[target]['stance'] or len(str(r.get('merge_reason','')).strip())<12:
            errors.append(f'组{i}仅允许同立场近义机制合并并说明至少12字原因');continue
        if same_category:
            merges[i]=target
        elif tuple(sorted((i,target))) in requested_overlap:
            cross_category_merges[i]=target
        else:
            errors.append(f'组{i}仅允许overlap_pairs给出的跨方面正面重复合并')

    sources=source_map(workspace);updates={};audit=[]
    for i,r in indexed.items():
        samples={s['candidate_id']:s for s in expected[i]['samples']};reviews=r.get('reviews',[])
        if not isinstance(reviews,list) or len(reviews)!=len(samples) or {v.get('candidate_id') for v in reviews if isinstance(v,dict)}!=set(samples):
            errors.append(f'组{i}reviews须恰好覆盖samples');continue
        for review in reviews:
            cid=review['candidate_id'];action=review.get('action');original=state['judgments'][cid];updated=dict(original)
            if action not in ('keep','trim','exclude') or not str(review.get('reason','')).strip():errors.append(f'{cid}缺action/reason');continue
            if action=='exclude':updated.update(decision='reject',initial_decision='keep')
            else:
                if action=='trim':
                    resolved=resolve_verbatim_span(original['excerpt'],review.get('selected_excerpt'))
                    if resolved is None:errors.append(f'{cid}裁剪不是原excerpt连续子串');continue
                    quote,offset,_=resolved;updated.update(excerpt=quote,start=original['start']+offset,end=original['start']+offset+len(quote),excerpt_hash=hashlib.sha256(quote.encode('utf-8')).hexdigest())
                reference_review=dict(review)
                if action=='keep' and 'target_scope' not in reference_review and original.get('target_scope'):
                    reference_review['target_scope']=original['target_scope']
                issue=excerpt_length_error(updated['excerpt'],original.get('source_channel')) or target_guard(original['excerpt'],updated['excerpt'],state,action,samples[cid].get('work_reference'),reference_review) or scope_issue(updated,sources[updated['source_id']],state,reference_review)
                if not issue and action=='trim':issue=stance_trim_guard(original['excerpt'],updated['excerpt'],expected[i]['stance'])
                if issue:errors.append(f'{cid}: {issue}');continue
            updated.update(editorial_action=action,editorial_reason=review['reason']);updates[cid]=updated
            if isinstance(review.get('target_scope'),dict):updated['target_scope']=review['target_scope']
            audit.append({'candidate_id':cid,'group_index':i,'action':action,'reason':review['reason'],'before':original['excerpt'],'after':updated['excerpt'] if action!='exclude' else None})
    all_items={cid:{**updates.get(cid,v),'display_excerpt':display_excerpt(updates.get(cid,v)['excerpt'])} for cid,v in state['judgments'].items() if updates.get(cid,v).get('decision')=='keep'}
    titles={};cores={};guidance={};history={};bindings=[];required={}
    for i,r in indexed.items():
        if i in merges or i in cross_category_merges:continue
        available={cid:v for cid,v in all_items.items() if merges.get(v['group_index'],v['group_index'])==i}
        if not available:continue
        title=r.get('title');core=r.get('core_mechanism')
        if not isinstance(title,str) or not 12<=len(title)<=48 or not re.match(TITLE_PATTERN,title):errors.append(f'组{i}title须12—48字完整判断');continue
        if not isinstance(core,str) or not 8<=len(core)<=80:errors.append(f'组{i}core_mechanism须8—80字');continue
        errors.extend(f'组{i}: {e}' for e in validate_title_support(title,r.get('title_support'),available))
        try:guide=validate_writing_guidance(
            r.get('writing_guidance'),available,120 if state.get('candidate_closeout_policy') else 180,
            require_dual_support=(state.get('quantity_contract')==SUPPORTED_POSITIVE_POLICY
                                  and expected[i]['stance']=='正向词倾向'))
        except ValueError as exc:errors.append(f'组{i}: {exc}');guide=None
        if guide is not None:guidance[str(i)]=guide
        titles[str(i)]=title;cores[str(i)]=core
        required[i]={v.get('candidate_id') for v in r.get('title_support',[]) if isinstance(v,dict)}|{v['candidate_id'] for v in (guide or {}).get('preferred',[])}
        if guide and guide['standalone_eligible']:
            required[i].update(guide['independent_voice_ids'][:2])
        bindings.append({'group_index':i,'title':title,'title_support':r.get('title_support'),'semantic_approval_inferred':False})
        if expected[i].get('history_candidates') is not None:
            hc=r.get('history_check')
            old=(state.get('editorial_history_samples',{}) if state.get('final_history_check_policy')=='ALL_FINAL_RELATIONS_V1'
                 else {v['id']:v for v in expected[i]['history_candidates']})
            if not isinstance(hc,dict) or hc.get('relation') not in ('continuing','developed','opposing','unseen_angle') or len(str(hc.get('reason','')).strip())<15:
                errors.append(f'组{i}须填写history_check实际旧句比较');continue
            ids=hc.get('previous_sample_ids')
            if not isinstance(ids,list) or len(ids)!=len(set(ids)) or any(v not in old for v in ids) or (hc['relation']=='unseen_angle')!= (not ids):
                errors.append(f'组{i}历史匹配须引用给定实际H句，未展示角度才可空');continue
            if hc['relation']=='developed':
                q=hc.get('novel_information_quote','')
                if len(q)<12 or not any(q in v['display_excerpt'] for v in available.values()) or len(str(hc.get('why_not_old','')).strip())<15:
                    errors.append(f'组{i}developed须有当前展示原句及真实新增机制说明');continue
            current=hc.get('current_evidence',[])
            if state.get('final_history_check_policy')=='ALL_FINAL_RELATIONS_V1':
                if (not isinstance(current,list) or not 1<=len(current)<=2 or any(not isinstance(v,dict) for v in current)
                        or any(v.get('candidate_id') not in available or not isinstance(v.get('quote_snippet'),str)
                               or not 12<=len(v['quote_snippet'])<=140 or v['quote_snippet'] not in available[v['candidate_id']]['display_excerpt'] for v in current)):
                    errors.append(f'组{i}history_check.current_evidence须绑定1—2条最终仍保留的当前ID及12—140字连续展示句，不用发现seed替代');continue
                required[i].update(v['candidate_id'] for v in current)
            original=state['pool'][i].get('history_review') or {}
            history[str(i)]={**original,'initial_relation':original.get('relation'),'relation':hc['relation'],
                'previous_group_ids':sorted({old[v]['previous_group_id'] for v in ids}),'reason':hc['reason'],
                'previous_evidence':[{'history_sample_id':sid,'quote_snippet':old[sid]['text']} for sid in ids],
                **({'current_evidence':current,'current_evidence_scope':'FINAL_KEPT_DISPLAY_ONLY'} if state.get('final_history_check_policy')=='ALL_FINAL_RELATIONS_V1' else {}),
                'final_display_history_check':hc,'review_stage':'FINAL_DISPLAY_EVIDENCE_V2'}
    if requested_overlap:
        counted={int(i) for i,guide in guidance.items() if guide['standalone_eligible']}
        needed={pair for pair in requested_overlap if set(pair)<=counted}
        reviewed={}
        rows=answer.get('overlap_reviews',[])
        if not isinstance(rows,list):
            errors.append('overlap_reviews须为数组')
            rows=[]
        for row in rows:
            if not isinstance(row,dict) or type(row.get('left')) is not int or type(row.get('right')) is not int:
                errors.append('overlap_reviews须填写实际风险对的整数编号')
                continue
            pair=(row['left'],row['right'])
            # Extra answers for non-counted groups cannot qualify a group and
            # are not published. Invalid guides still block the full answer.
            if pair in requested_overlap and pair not in needed:
                continue
            if pair not in needed or pair in reviewed or len(str(row.get('distinction','')).strip())<16:
                errors.append(f'overlap_reviews 组{pair[0]}与组{pair[1]}须为仍计数的真实风险对，不能重复且须有至少16字区别')
                continue
            valid=True
            for side,index in zip(('left','right'),pair):
                proof=row.get(side+'_evidence',{})
                item=all_items.get(proof.get('candidate_id')) if isinstance(proof,dict) else None
                quote=proof.get('quote_snippet') if isinstance(proof,dict) else None
                if (not item or merges.get(item['group_index'],item['group_index'])!=index
                        or not isinstance(quote,str) or len(quote)<8 or quote not in item['display_excerpt']):
                    errors.append(f'overlap_reviews 组{index}须用本组实际保留句证明独立区别')
                    valid=False
            if valid:
                reviewed[pair]=row
                for side,index in zip(('left','right'),pair):
                    required[index].add(row[side+'_evidence']['candidate_id'])
        for a,b in sorted(needed-set(reviewed)):
            errors.append(f'overlap_reviews缺组{a}与组{b}的两侧原句及实际区别；若判断相同，合并或收窄后重新计数')
    else:
        reviewed={}
    if errors:raise ValueError('只修以下末端答卷项，不重审其他材料：'+'; '.join(errors))
    state['judgments'].update(updates)
    for cid,item in state['judgments'].items():
        if item.get('decision')=='keep' and item['group_index'] in cross_category_merges:
            item.update(decision='keep_not_displayed_cross_category_merge',initial_decision='keep',
                        display_selection_reason='跨方面重复判断归并；原引文保留审计，不自动跨方面迁入')
        if item.get('decision')=='keep' and item['group_index'] in merges:
            item['initial_group_index']=item['group_index'];item['group_index']=merges[item['group_index']]
    for i in expected:
        if i in merges:continue
        keeps=[v for v in state['judgments'].values() if v.get('decision')=='keep' and v['group_index']==i]
        keeps.sort(key=lambda v:(v['candidate_id'] not in required.get(i,set()),source_selection_key(sources[v['source_id']],state['pool'][i]['stance'],v.get('score',0),v['source_id'])))
        for item in keeps[10:]:item.update(decision='keep_not_displayed_merge_limit',initial_decision='keep',display_selection_reason='近义观点合并后最多10条，已审判断保留在后台，不伪装内容reject')
    state.update(editorial_status='COMPLETE',editorial_titles=titles,editorial_core_mechanisms=cores,
        editorial_writing_guidance=guidance,editorial_history_reviews=history,editorial_audit=audit,
        editorial_evidence_bindings=bindings,editorial_overlap_reviews=list(reviewed.values()),
        editorial_elapsed_seconds=round(time.time()-state['editorial_started_epoch'],3))
    save(workspace/'display_editor_audit.json',{'scope':'ALL_FINAL_TITLE_EVIDENCE_AND_MAX_2_EXCERPT_EDITS_PER_GROUP',
        'title_contract':'FINAL_DISPLAY_EVIDENCE_V2','titles':titles,'cores':cores,'merges':merges,
        'cross_category_merges':cross_category_merges,'overlap_reviews':list(reviewed.values()),'reviews':audit,
        'title_support':bindings,'writing_guidance':guidance,'history_reviews':history,'elapsed_seconds':state['editorial_elapsed_seconds']})
