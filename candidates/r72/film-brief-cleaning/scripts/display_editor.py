"""One bounded editorial pass over titles and at most three selected quotes per group."""
from __future__ import annotations
import hashlib
import re
import time
from pathlib import Path
from rapid_film_workbench import save, resolve_verbatim_span, WORK_SCOPE_RULE, excerpt_min_chars, excerpt_length_error, CLOSEOUT_RESERVE_SECONDS

EDITOR_SECONDS = 240
EDITOR_MIN_REMAINING = CLOSEOUT_RESERVE_SECONDS + 150
TITLE_PATTERN = r"^(?:(?:观众|网友|网民|媒体|舆论|公众|影评人|评论者|部分观众|部分网友))?(?:认可|肯定|关注|讨论|质疑|批评|担忧|认为|指出|赞赏|称赞|期待)"
RULES = """你是末端文字编辑，只处理本包全部标题和每组最多2条已入选代表/风险句，不是全量来源复核。此前keep仅是初审，你需要独立判断这些被点名句子。
暂不采信原标题和keep。每组根据本包真实证据重新写成12—30字的一个明确核心判断，保留评价对象、方向和依据。标题中的具体数量必须逐字出现在所引用的evidence_ids对应原句里；没有则删去该数量，不能用其他数字或泛热度替代证明。不要把预约、品牌、主创等不同机制拼在一起；不要把只有部分句子支持的数字、演员名单、实拍/不用替身等细节塞进标题。保留本组原有核心方面和立场，不扩大为“整体表现”等万能标题迁就错样本；无法支持原核心的样本exclude。未经外部核验的司法/商业自述保持来源说法属性；不要用“媒体证实”“风波已经平息”等事实口吻。不要以低数样本推断普遍共识。证据来自本期本组给出的句子，不能创造新观点。
逐条检查本包samples：keep表示句子自足、直接支持最终标题核心、立场正确；trim仅从本条excerpt中选连续逐字完整句（短评50—360字、文章70—360字），实际去掉推广、无关尾巴或单边态度；exclude用于不支持、名单口号、无法独立理解的残句。不用未展示上下文补证据，不在reason中忽略实际仍展示的文字。客观簇中的赞誉、贬损、讽刺不能以“混合中性”放行；确有独立中性句才trim。没有可靠完整句则exclude，不补字。明显残尾如半截词句不能keep。不得改未提供的样本、不重新归组、不补新来源，不要求凑满10。
标题取本包仍保留句子共同支持的最小核心，不把某一句独有的细节套给全组。客观标题本身也不能加“表现突出”“顶尖”等赞贬；未核验商业/司法结论只作来源说法。裁剪不能丢掉原句已有的目标剧名或别名。若最终句有《其他名称》而没有target_terms中任何目标名，采取保守准入：保留同时点名目标的连续原句，做不到就exclude；不能只剩别剧、书名或曲名，再借上下文补对象。不改未提供的样本。
answer_file一次完整JSON；每group_index一行，title为最终标题，evidence_ids选本组至少1条仍保留的样本ID支撑标题，reviews覆盖本包所有candidate_id，每项action=keep/trim/exclude、reason短说明，trim须selected_excerpt，其余无需抄原文。若本组提供的全部样本均应排除，evidence_ids可以空，但保持原标题并说明原因；不能编造支持。当前主窗口写完本包答卷后执行返回的editor-collect命令，不启动Agent、不查网络、不操作其他答卷。"""

LOCKED_RULES = """你是末端摘录编辑，只审核本包每组最多2条已入选代表/风险句，不是全量来源复核。标题已在本期观点发现阶段形成完整报告式观点并固定，通常24—36字、机器范围12—48字，允许用逗号连接具体处理与评价意义；answer的title必须逐字照抄本组title，不缩窄、扩大或改变含义，不因个别句子改整组标题。错样本exclude，不为迁就它修改标题或分组。
每条samples独立判断keep/trim/exclude：keep须原句独立、对象正确、真正支持固定标题且立场一致。trim只能在本条excerpt中选短评50—360字、文章70—360字连续逐字原句；裁后重新检查同样四项，不得只剩定档消息、剧情简介、数字或人名而失去原先的评价态度。客观组不能保留赞贬或混合态度；其他组不能用中性消息当正负评价。原句已有目标名/别名不能裁掉；含《其他名称》但没有target_terms目标名的句子保守exclude。不借未展示上下文补观点。无法裁出完整支持句则exclude，不补字、不补来源、不凑数、不改未提供样本。
一次写answer_file完整JSON。每group_index对应一行，title照抄，不填写evidence_ids；脚本根据你实际保留的本包样本生成审计编号，不把编号绑定当作标题事实已经核实。reviews覆盖每个candidate_id，action和短reason必填；只有trim填selected_excerpt。修复也由同一主窗口顺序写，一次完整Write或逐项顺序Edit后回读确认，不在同一工具批次并行Edit同一个文件。写完立即editor-collect，不另起Agent，不查网络，不重审其他材料。"""

STANCE_CUES = {
    '正向词倾向': ('期待','喜欢','认可','赞赏','好看','出色','惊艳','细腻','精彩','带感','满意','扎实','精良','鲜活','讨喜','流畅','新颖','最具话题性'),
    '负向词倾向': ('质疑','担忧','批评','失望','难看','尴尬','糟糕','僵硬','呆板','拖沓','无聊','敷衍','吐槽','不合理','不可信','油腻','悬浮'),
}
RULES += WORK_SCOPE_RULE
LOCKED_RULES += WORK_SCOPE_RULE

def stance_trim_guard(original, selected, stance):
    """Conservative lost-cue alarm only; never classifies or approves semantics."""
    cues = STANCE_CUES.get(stance, ())
    if any(cue in original for cue in cues) and not any(cue in selected for cue in cues):
        return '裁后丢失原有明确态度词；确认保留支持固定标题及立场的连续原句，否则exclude'
    return None


def risk(item):
    quote = item["excerpt"]
    score = 0
    if not re.search(r'[。！？!?…][”’」』"\')]*$', quote.rstrip()):
        score += 8
    if item.get("stance") == "中性或混合" and re.search(r"顶尖|顶级|最赚钱|强撑|拿什么输|匠心|心血|期待|好看|惊艳|割韭菜", quote):
        score += 6
    if re.search(r"星标|扫码|抽奖|关注我|点击|#[^ ]|https?://|@", quote):
        score += 4
    if len(quote) > 220:
        score += 2
    return score


def target_guard(original, selected, state, action):
    terms = [term for term in state.get("terms", [state["target"]]) if isinstance(term,str) and term]
    selected_has_target = any(term in selected for term in terms)
    if action == "trim" and any(term in original for term in terms) and not selected_has_target:
        return "裁剪丢失原句已有目标作品名/别名；保留含目标的连续原句或exclude"
    if re.search(r"《[^》]{1,60}》", selected) and not selected_has_target:
        return "最终句只点名其他作品/书曲名而未点名目标；保留同时含目标的原句或exclude"
    return None


def prepare_editor(workspace, state, digest):
    remaining = state["started_epoch"] + state["deadline_seconds"] - time.time()
    if remaining < EDITOR_MIN_REMAINING:
        state["editorial_status"] = "SKIPPED_INSUFFICIENT_TIME"
        return None
    groups = []
    for index, group in enumerate(state["pool"]):
        keeps = [item for item in state["judgments"].values() if item["group_index"] == index and item["decision"] == "keep"]
        if not keeps:
            continue
        ranked = sorted(keeps, key=lambda item: (-risk(item), -item.get("score", 0), item["candidate_id"]))
        picked = ranked[:1]
        if len(keeps) > 1:
            picked.append(min((item for item in keeps if item not in picked), key=lambda item: (risk(item), -item.get("score", 0))))
        groups.append({"group_index": index, "title": group["aspect"], "stance": group["stance"], "total_selected": len(keeps),
                       "samples": [{"candidate_id":item["candidate_id"], "excerpt":item["excerpt"], "risk_hint":risk(item), "source_channel":item.get("source_channel", ""), "min_excerpt_chars":excerpt_min_chars(item.get("source_channel"))} for item in picked]})
    if not groups:
        state["editorial_status"] = "SKIPPED_NO_SAMPLES"
        return None
    locked = state.get('title_contract') == 'FROZEN_SINGLE_CORE_V1'
    rules = LOCKED_RULES if locked else RULES
    binding = digest([state["binding"], groups, rules])
    answer = workspace/'display_editor_answer.json'
    payload = {"target":state["target"], "fingerprint":binding, "rules":rules, "groups":groups, "answer_file":str(answer),
               "title_policy":"COPY_FROZEN_TITLE" if locked else "BOUNDED_TITLE_EDITOR",
               "target_terms":state.get("terms",[state["target"]]),
               "execution_mode":"MAIN_WINDOW_SINGLE_WRITER", "execution_instruction":"当前主窗口直接完成这一次有限编辑，不另外启动Agent。一次写答卷后立即执行返回的editor-collect命令，不等其他编辑者。",
               "max_work_seconds":max(0,min(EDITOR_SECONDS,int(remaining-CLOSEOUT_RESERVE_SECONDS))),
               "answer_schema":{"fingerprint":binding, "groups":[{"group_index":"照抄整数", "title":"最终单一核心观点", "evidence_ids":["本组仍保留的ID"],
                   "reviews":[{"candidate_id":"本包ID", "action":"keep/trim/exclude", "reason":"短说明", "selected_excerpt":"仅trim填连续原句"}]}]}}
    if locked:
        payload['answer_schema']['groups'][0].pop('evidence_ids')
        payload['answer_schema']['groups'][0]['title'] = '逐字照抄固定标题'
    save(workspace/'display_editor_input.json',payload)
    save(answer,{"fingerprint":binding,"groups":[]})
    state.update(status="DISPLAY_EDITOR_REQUIRED", editorial_status="PENDING", editorial_binding=binding,
                 editorial_groups=groups, editorial_started_epoch=time.time())
    return {"read_now":str(workspace/'display_editor_input.json'), "answer_file":str(answer)}


def apply_editor(workspace, state, answer):
    if not isinstance(answer,dict) or state.get("editorial_status") != "PENDING" or answer.get("fingerprint") != state.get("editorial_binding"):
        raise ValueError("末端编辑阶段或本期指纹不匹配")
    expected = {group["group_index"]:group for group in state["editorial_groups"]}
    rows = answer.get("groups")
    if not isinstance(rows,list) or len(rows) != len(expected):
        raise ValueError("编辑答卷须覆盖全部本包观点组")
    updates, titles, seen_groups, audit, target_errors = {}, {}, set(), [], []
    locked = state.get('title_contract') == 'FROZEN_SINGLE_CORE_V1'
    title_evidence_notes, evidence_bindings = [], []
    for row in rows:
        index = row.get("group_index") if isinstance(row,dict) else None
        if not isinstance(index,int) or isinstance(index,bool) or index not in expected or index in seen_groups:
            raise ValueError("未知或重复group_index")
        seen_groups.add(index)
        title = row.get("title")
        if locked and title != expected[index]['title']:
            raise ValueError(f'组{index}标题已固定，必须逐字照抄；不能根据有限样本改全组含义')
        no_retained_evidence = ((locked or row.get("evidence_ids") == []) and isinstance(row.get("reviews"),list)
                               and all(isinstance(review,dict) and review.get("action") == "exclude" for review in row["reviews"]))
        unchanged_unsupported = no_retained_evidence and title == expected[index]["title"]
        if not isinstance(title,str) or (not 12 <= len(title) <= (48 if locked else 30) and not unchanged_unsupported) or not re.match(TITLE_PATTERN,title):
            raise ValueError(f"组{index}标题须12—{48 if locked else 30}字、完整报告式核心观点")
        samples = {item["candidate_id"]:item for item in expected[index]["samples"]}
        reviews = row.get("reviews")
        if not isinstance(reviews,list) or len(reviews) != len(samples):
            raise ValueError(f"组{index}须逐项覆盖本包samples")
        seen, retained = set(), set()
        for review in reviews:
            identity = review.get("candidate_id") if isinstance(review,dict) else None
            if identity not in samples or identity in seen or review.get("action") not in ("keep","trim","exclude") or not str(review.get("reason") or "").strip():
                raise ValueError(f"组{index}未知/重复ID或缺action/reason")
            seen.add(identity)
            original = state["judgments"][identity]
            updated = dict(original)
            action = review["action"]
            if action == "exclude":
                updated.update(decision="reject", initial_decision="keep")
            else:
                retained.add(identity)
            if action == "trim":
                quote = review.get("selected_excerpt")
                resolved = resolve_verbatim_span(original["excerpt"], quote)
                if resolved is None:
                    raise ValueError(f"{identity}: trim须是本条原excerpt的短评50—360字、文章70—360字连续子串")
                quote, offset, restored_spaces = resolved
                if restored_spaces:
                    updated["literal_transport_normalized"] = True
                updated.update(start=original["start"]+offset,end=original["start"]+offset+len(quote),excerpt=quote,
                               excerpt_hash=hashlib.sha256(quote.encode('utf-8')).hexdigest())
            if action != "exclude":
                length_error = excerpt_length_error(updated["excerpt"], original.get("source_channel"))
                if length_error:
                    target_errors.append(f"{identity}: {length_error}")
                issue = target_guard(original["excerpt"], updated["excerpt"], state, action)
                if issue:
                    target_errors.append(f"{identity}: {issue}")
                if action == 'trim':
                    stance_issue = stance_trim_guard(original['excerpt'], updated['excerpt'], expected[index]['stance'])
                    if stance_issue:
                        target_errors.append(f'{identity}: {stance_issue}')
            updated.update(editorial_action=action, editorial_reason=review["reason"])
            updates[identity] = updated
            audit.append({"candidate_id":identity,"group_index":index,"action":action,"reason":review["reason"],
                          "before":original["excerpt"],"after":updated["excerpt"] if action != "exclude" else None})
        if locked:
            # Fixed titles are not being composed from this limited packet.
            # Bind only the actual validated keep/trim results. Ignore any old
            # model-supplied evidence_ids; never revive an excluded or foreign ID.
            evidence = sorted(retained)
            evidence_bindings.append({'group_index': index, 'retained_candidate_ids': evidence,
                'status': 'SCRIPT_BOUND_RETAINED_EDITOR_SAMPLES', 'title_unchanged': True,
                'provided_evidence_ids_used': False, 'semantic_approval_inferred': False})
        else:
            evidence = row.get("evidence_ids")
            if not isinstance(evidence,list) or any(not isinstance(identity,str) or identity not in retained for identity in evidence):
                raise ValueError(f"组{index}标题证据必须来自本组仍保留的本包样本")
            if not evidence and (retained or title != expected[index]["title"]):
                raise ValueError(f"组{index}缺标题支持证据；无支持不能改写新标题")
        quantities = re.findall(r"(?:\d+(?:\.\d+)?|[一二三四五六七八九十百千万亿]+)(?:余|多)?(?:万|亿|家|人|部|场|集|天|年)",title)
        unsupported = [value for value in quantities if not any(value in updates[identity]["excerpt"] for identity in evidence)]
        if unsupported and locked:
            # This packet edits <=3 samples, not the already fixed group title.
            # Requiring its numeric proof here creates an impossible "copy and
            # change" instruction. Keep an audit note, not a semantic approval.
            title_evidence_notes.append({'group_index': index, 'quantities': unsupported,
                'status': 'NOT_ESTABLISHED_BY_LIMITED_EDITOR_PACKET',
                'title_unchanged': True, 'semantic_approval_inferred': False})
        elif unsupported and not unchanged_unsupported:
            raise ValueError(f"组{index}标题数量未出现在引用证据原句：{unsupported}；删去无据数字或引用本包真正支持的句子")
        titles[str(index)] = title
    if target_errors:
        raise ValueError("对象保护、立场保护或摘录长度校验失败，仅修以下项，不重审其他样本：" + "; ".join(target_errors))
    # Commit only after all rows validate; no partial title/quote changes on a bad answer.
    state["judgments"].update(updates)
    state.update(editorial_status="COMPLETE", editorial_titles=titles, editorial_audit=audit,
                 editorial_title_evidence_notes=title_evidence_notes,
                 editorial_evidence_bindings=evidence_bindings,
                 editorial_elapsed_seconds=round(time.time()-state["editorial_started_epoch"],3))
    save(workspace/'display_editor_audit.json',{"scope":"ALL_TITLES_AND_MAX_2_SELECTED_SAMPLES_PER_GROUP", "groups":state["editorial_groups"],
        "titles":titles,"reviews":audit,"title_evidence_notes":title_evidence_notes,
        "evidence_bindings":evidence_bindings,"elapsed_seconds":state["editorial_elapsed_seconds"]})
