"""Scripts first; AI judges only proposed display excerpts. No upstream reviews."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
import subprocess
import sys
import time
from collections import defaultdict
from difflib import SequenceMatcher
from source_policy import sample_keys, verify_unique_samples

from rapid_film_workbench import (render, save, text_field, resolve_verbatim_span, WORK_SCOPE_RULE,
                                 resolve_selection, source_selection_key, source_author, excerpt_min_chars, excerpt_length_error,
                                 source_exclusion, display_excerpt, CLOSEOUT_RESERVE_SECONDS)

MAX_PACKET_ITEMS = 6
EDITOR_RESERVE_SECONDS = 480

def collapse_cross_group_sources(state,sources):
    owners={};audit=state.setdefault('single_source_audit',[])
    locked=set(state.get('locked_candidate_ids',[])) | set(state.get('supplement_committed_candidate_ids',[]))
    keeps=sorted((v for v in state['judgments'].values() if v['decision']=='keep'),
                 key=lambda v:(v['candidate_id'] not in locked,-v.get('score',0),v['group_index'],v['candidate_id']))
    for item in keeps:
        keys=sample_keys(item,sources[item['source_id']])
        prior=next((owners[k] for k in keys if k in owners),None)
        if prior is not None:
            item.update(decision='reject',initial_decision='keep',script_exclusion_reason='ONE_SOURCE_ONE_VIEWPOINT',
                        reason='脚本按样本唯一归属规则排除，保留与核心观点匹配得分更高的已审项')
            audit.append({'candidate_id':item['candidate_id'],'source_id':item['source_id'],
                          'group_index':item['group_index'],'duplicate_of':prior['candidate_id'],
                          'retained_group':prior['group_index']})
            owners.update({key:prior for key in keys})
        else:owners.update({key:item for key in keys})

def unique_wave_candidates(state,sources):
    """Reserve each source once before dispatch; rejected owners free later routes."""
    from rapid_film_workbench import source_priority
    from sampling_budget import sample_stops
    sampling_stopped=sample_stops(state)
    used=set();counts=defaultdict(int);options=[];group_identities=set()
    from workbench_completion import SUPPORTED_POSITIVE_POLICY
    seed_sources = {row['excerpt_id']: row['source_id']
                    for row in state.get('discovery_excerpts', [])}
    protected_seeds = set()
    previously_reviewed={item['source_id'] for item in state['judgments'].values()} if state.get('locked_candidate_ids') else set()
    for item in state['judgments'].values():
        if item['decision']=='keep':
            used.update(sample_keys(item,sources[item['source_id']]))
            counts[item['group_index']]+=1
            group_identities.add((item['group_index'],item.get('identity')))
    for index,group in enumerate(state['pool']):
        if counts[index]>=10 or index in sampling_stopped:continue
        group_seed_sources = {seed_sources[sid] for sid in group.get('seed_ids', [])
                              if sid in seed_sources}
        for candidate in group['candidates']:
            cid=digest([state['binding'],index,candidate])[:20]
            if candidate['source_id'] in previously_reviewed:continue
            if state.get('coverage_repair_active') and cid not in state.get('coverage_allowed_ids',[]):continue
            if (cid in state['judgments'] or cid in state.get('abandoned_candidates',{}) or
                    cid in state.get('length_ineligible_candidates',{})):continue
            if (index,candidate.get('identity')) in group_identities:continue
            prior_items=[v for v in state['judgments'].values() if v['group_index']==index]
            prior_hashes={v.get(k) for v in prior_items for k in ('excerpt_hash','proposal_excerpt_hash') if v.get(k)}
            if candidate.get('excerpt_hash') and candidate['excerpt_hash'] in prior_hashes:continue
            record=sources[candidate['source_id']]
            text=str(record['data'].get(candidate['text_field']) or '')
            quote=text[candidate['start']:candidate['end']]
            if candidate.get('media_authority_rank') and any(near_identical_media_excerpt(quote,v.get('excerpt','')) for v in prior_items if v['decision']=='keep' and v.get('media_authority_rank')):continue
            windows=[v['text'] for v in candidate.get('selection_options',[])] if state.get('selection_contract') else [quote]
            if not windows:windows=[text[max(0,candidate['start']-100):candidate['end']+100]]
            if max((len(display_excerpt(w)) for w in windows),default=0)<excerpt_min_chars(record['channel']):
                state.setdefault('length_ineligible_candidates',{})[cid]={'source_id':candidate['source_id'],'group_index':index,'reason':'NO_WINDOW_MEETS_VISIBLE_LENGTH_MINIMUM','ai_review_performed':False}
                continue
            keys=sample_keys({**candidate,'excerpt':quote},record)
            priority=source_priority(record);negative=group.get('stance') in ('negative','负向词倾向')
            preference=(-priority['negative_channel_priority'] if negative else 0,
                        priority['platform_risk_rank'],-priority['media_authority_rank'],priority['history_uses'],
                        -priority['channel_priority'] if not negative else 0)
            options.append((-candidate.get('score',0),counts[index],index,cid,keys,candidate.get('identity'),preference))
            if (state.get('quantity_contract') == SUPPORTED_POSITIVE_POLICY
                    and group['stance'] in ('positive', '正向词倾向')
                    and candidate['source_id'] in group_seed_sources):
                protected_seeds.add(cid)
    # Keep source preference and history reuse active in comparable score bands;
    # otherwise unique-author reservations would silently undo the ranked pool.
    remaining=sorted(options,key=lambda row:row[:4]);ordered=[]
    while remaining:
        ceiling=remaining[0][0]+20
        cut=next((i for i,row in enumerate(remaining) if row[0]>ceiling),len(remaining))
        band,remaining=remaining[:cut],remaining[cut:]
        ordered.extend(sorted(band,key=lambda row:(row[6],*row[:4])))
    selected=set();reserved_counts=defaultdict(int)
    seed_groups = defaultdict(list)
    available_voices = defaultdict(set)
    for row in ordered:
        available_voices[row[2]].add(row[5])
        if row[3] in protected_seeds and counts[row[2]] < 2:
            seed_groups[row[2]].append(row)
    # Give scarce discovery evidence a review opportunity before rich groups
    # consume a shared source. These reservations never imply an AI keep.
    for index in sorted(seed_groups, key=lambda i: (len(available_voices[i]),
                                                    len({row[5] for row in seed_groups[i]}),
                                                    counts[i], i)):
        for _, _, _, cid, keys, identity, _ in seed_groups[index]:
            if counts[index] + reserved_counts[index] >= 2:
                break
            if keys & used or (index, identity) in group_identities:
                continue
            selected.add(cid)
            used.update(keys)
            group_identities.add((index, identity))
            reserved_counts[index] += 1
    for _,_,index,cid,keys,identity,_ in ordered:
        # Reserve only the group's actual remaining display capacity. Surplus
        # claims must not hide unreviewed sources from other eligible viewpoints.
        if counts[index]+reserved_counts[index]>=10:continue
        if keys & used or (index,identity) in group_identities:continue
        selected.add(cid);used.update(keys);group_identities.add((index,identity));reserved_counts[index]+=1
    return selected


def selection_failure_details(item,row):
    """Explain a failed binding without choosing or rewriting evidence."""
    options=item.get('selection_options') or []
    windows=[v['text'] for v in options] or [item.get('selection_text') or item.get('excerpt','')]
    contexts=[v.get('work_reference',{}).get('local_target_context','') for v in options]
    contexts.append(item.get('work_reference',{}).get('local_target_context',''))
    quote=row.get('selected_excerpt')
    in_window=isinstance(quote,str) and bool(quote) and any(quote in text for text in windows)
    context_only=isinstance(quote,str) and bool(quote) and not in_window and any(quote in text for text in contexts)
    return {'submitted_window':row.get('selected_window'),'valid_window_numbers':list(range(1,len(windows)+1)),
            'context_only_quote':context_only,
            'selection_instruction':('当前selected_excerpt取自work_reference上下文，不在允许选句窗口中；上下文仅用于作品指代，不能用作展示句。' if context_only else '')+
                '只从selection_options的text（无选项才用selection_text）选一个完整窗口或其连续原句；不得从work_reference补字或取句。无法满足内容、指代及长度要求则reject，已通过其他项保持不动。'}


def near_identical_media_excerpt(left: str, right: str) -> bool:
    """Collapse near-verbatim media copy while preserving distinct reporting."""
    a, b = (re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", value).lower()
            for value in (left, right))
    if min(len(a), len(b)) < 70 or min(len(a), len(b)) / max(len(a), len(b)) < .85:
        return False
    return SequenceMatcher(None, a, b, autojunk=False).ratio() >= .93


def collapse_selected_media_copies(state, sources):
    """Deduplicate actual selected quotes, which may differ from proposal windows."""
    audit = state.setdefault("selected_media_copy_audit", [])
    audited = {row["candidate_id"] for row in audit}
    for index, group in enumerate(state["pool"]):
        keeps = [item for item in state["judgments"].values()
                 if item["group_index"] == index and item["decision"] == "keep"
                 and item.get("media_authority_rank")]
        protected={v['candidate_id'] for guide in state.get('editorial_writing_guidance',{}).values() for v in guide.get('preferred',[])}
        protected.update(v['candidate_id'] for row in state.get('editorial_evidence_bindings',[]) for v in row.get('title_support',[]))
        protected.update(state.get('locked_candidate_ids',[]))
        protected.update(state.get('supplement_committed_candidate_ids',[]))
        keeps.sort(key=lambda item: (item['candidate_id'] not in protected,source_selection_key(
            sources[item["source_id"]], group["stance"], item.get("score", 0), item["source_id"])))
        distinct = []
        for item in keeps:
            original = next((prior for prior in distinct if near_identical_media_excerpt(
                item["excerpt"], prior["excerpt"])), None)
            if original is None:
                distinct.append(item)
                continue
            item.update(decision="reject", initial_decision="keep",
                        reason="脚本对最终展示原句去重：与同组已保留媒体引文高度相似",
                        selected_media_copy_of=original["candidate_id"])
            if item["candidate_id"] not in audited:
                audit.append({"candidate_id": item["candidate_id"],
                              "source_id": item["source_id"],
                              "duplicate_of": original["candidate_id"],
                              "group_index": index,
                              "reason": "NEAR_VERBATIM_SELECTED_MEDIA_EXCERPT"})
                audited.add(item["candidate_id"])

SAMPLE_SUPPORT_RULE = '样本级与观点级分开：单条原句须具体支持本组同一核心机制中的一个实质判断，不要求独自覆盖标题列出的全部人物、实例或细节。比如聚合主演表演评价时，一条具体谈演员甲、另一条具体谈演员乙可以互补，不能只因一条未谈另一主演就拒绝。每条仍须评价本剧、态度正确且有具体表现；演员名单、角色简介、别剧表演和泛赞不能补证。完整标题的所有实质判断及两份独立有效声音，由末端按全组证据共同核对；没有证据的标题部分仍须收窄，不从单个细节推出整体结论，也不允许无关方面拼成一组。'

RULE = SAMPLE_SUPPORT_RULE + "只审核最终展示候选，不审核整篇来源、不复核其他组、不改组、不重截。keep表示你已确认：(1)摘录评价的是目标剧，不是别剧/别人的评价；(2)确实具体支持本组核心机制中的实质判断，不只是同方面、演员名或关键词匹配；(3)正负态度符合本组，中性组只能是中性事实/客观观察，不能把混合正负当中性；(4)原句可独立理解，不是名单、宣传指令、残缺句或纯空泛表态。拿不准或不支持就reject并写一句短原因，不查网络。只读本包，向answer_file一次写入所有candidate_id的keep/reject；fingerprint和packet_id照抄。你是包审核者，不是整个Skill的操作者；不要重启Skill、调用控制器/Agent、读取handoff/其他包或全量来源。不要修标题、换组、改原文、生成报告或额外审查。"

SPAN_RULE = SAMPLE_SUPPORT_RULE + """你负责选择最终展示原句并审核，只读本包。能完整保留一个合格窗口时填selected_window=1或2（selection_options从1计数，无options时selection_text是窗口1），由脚本逐字复制该窗口；要裁剪时填selected_excerpt为同一窗口内的连续逐字片段，两种填法二选一。短评至少50字，文章类至少70字，均最多360字，具体下限见每项min_excerpt_chars。可在给定局部窗口内去掉推广、无关八卦或残句前后缀，不去查全文，不拼接、不补字、不改写。不合适则reject和一句短原因，不选窗口或摘录。
keep同时确认：所选片段评价目标剧；直接支持aspect标题的核心判断；立场与本组一致；全文展示片段完整可读、有具体依据。context、target_context只用于确定指代和作品，不能借其未展示的评价证明keep。逐字选出的selected_excerpt才是读者会看到的全部文字，reason不能补充它没有的观点。泛泛称好不能证明实拍无替身；列演员或歌曲名单不能证明表演/音乐好；只说热度不能证明商业收益。客观组不得保留“拿什么输”“匠心心血”等单边赞誉，只能选择原文中真正中性的完整陈述；司法/商业说法是来源说法，未核验事实。删去星标/关注/购票/抽奖邀请和无关盘点；不能留下句首句尾截断、只剩期待的口号。每条keep给一句简短reason，指出所选原文支持的具体核心判断；不支持就reject，不能靠推测凑满10。
按包内规则写自己的answer_file，所有candidate_id判keep/reject，fingerprint与packet_id照抄。你只审核本包，不调用控制器或Agent，不读其他包/全量来源，不改标题、不换组、不联网。"""

SPAN_RULE += " 所选原句若只有剧情简介、定档或开播消息而没有对应评价，不得放入正负组。未播期待不能证明播后成绩；某榜第几名不能证明另一榜登顶；角色设定不能代替演技评价。负面组优先送审的微博、小红书材料仍须实际批评目标作品；玩笑式‘吐槽’、幕后花絮、正面期待或剧宣不能仅凭平台和词语当作负评。标题是固定判据，不会在后面替错样本改标题。"
SPAN_RULE += " 对社交平台的混合语气评论，按所选连续展示片段里的具体评价判断：有明确的观剧不满或具体动作问题，即使同帖还有赞赏或表情符号，也不要仅因整帖偏正面一概判成玩笑；纯玩梗、没有实际批评对象的仍拒绝。必须逐字满足长度，并且具体支持标题的同一核心评价机制；‘节奏慢’不能支持‘节奏仓促’，‘局部动作有问题’不能单独支持‘整体演技差’或尚未开播的猜测。"
RULE += WORK_SCOPE_RULE
SPAN_RULE += WORK_SCOPE_RULE
SPAN_RULE += ' 对预告、正式发布物料或角色默片中的具体表演、画面、声音评价，可以按该物料范围审核，不能仅因不是正片或尚未开播拒绝。须保留预告/物料范围，不能写成正片播后结论；戏外真人互动、别剧表演与纯演员名单仍不算本剧表演证据。局部上下文已明确点名本剧且无对象切换时，按work_reference核对实际承接，不能忽略给定锚点。'
from target_reference import REFERENCE_RULE
SPAN_RULE += ' '+REFERENCE_RULE
SPAN_RULE += " 有selection_options时，它们是同一篇长文中最多两个备选连续片段，不是两条样本；先比较哪段最支持本组，再选择完整窗口或从其中一个窗口裁剪。优先保留能解释判断的完整论据，文章类硬下限70字，通常80—200字，需要完整论据时可到300字，硬上限360字；不为凑长度硬加无关话，也不把有用解释裁成一句口号。保留原换行，禁止跨选项拼接。没有selection_options才用selection_text。媒体身份/渠道优先级只影响送审顺序，不代表内容正确，仍按同一语义标准审核。"


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def source_map(workspace):
    with (workspace / "raw_extraction/records.jsonl").open(encoding="utf-8") as handle:
        records={row["id"]: row for row in map(json.loads, handle)}
    history_file=workspace/'period_history_context.json'
    if history_file.exists():
        from period_history import annotate_sources
        annotate_sources(records,load(history_file))
    return records


def status(workspace, state):
    selected = sum(value["decision"] == "keep" for value in state["judgments"].values())
    result = {"status": state["status"], "started_at": state["started_at"], "elapsed_seconds": round(time.time() - state["started_epoch"], 3), "sample_count": selected, "pending_count": sum(item["candidate_id"] not in state["judgments"] for packet in state["packets"] for item in packet["items"]), "round": state["round"], "full_source_ai_reviews": 0, "cluster_set_ai_reviews": 0, "judged_candidates": len(state["judgments"]), "stopped_full_groups": state.get("stopped_full_groups", []), "deadline_seconds": state["deadline_seconds"], "workspace": str(workspace), "ai_viewpoint_discovery": state.get("ai_viewpoint_discovery", False)}
    save(workspace / "final_review_state.json", state)
    result["closeout_reserve_seconds"] = CLOSEOUT_RESERVE_SECONDS
    result["next_wave_required_seconds"] = state.get("next_wave_required_seconds")
    result["budget_includes_closeout_and_editor"] = True
    result["budget_instruction"] = "next_wave_required_seconds已含收尾与编辑预算，禁止重复加收尾预算。只跟随next_action/read_now/next_command。"
    result["next_action"] = {"FINAL_AI_REVIEW_REQUIRED": "EXECUTE_CURRENT_HANDOFF_OR_COLLECT",
                             "DISPLAY_EDITOR_REQUIRED": "EDIT_THEN_EDITOR_COLLECT",
                             "STALLED_REVIEW_RECOVERY_REQUIRED": "STOP_LISTED_WRITERS_THEN_CONFIRM_RECOVERY"}.get(state["status"], state["status"])
    result["next_packet_size"] = state.get("next_packet_size")
    result["wave_budget_plan"] = state.get("wave_budget_plan")
    result["script_length_excluded_candidates"] = len(state.get("length_ineligible_candidates", {}))
    result["editorial_status"] = state.get("editorial_status", "NOT_REQUESTED")
    result['unreviewed_cancelled_candidates'] = len(state.get('abandoned_candidates', {}))
    result['candidate_closeout_policy'] = state.get('candidate_closeout_policy')
    save(workspace / "final_review_status.json", result)
    return result


def request_stalled_recovery(workspace, state):
    pending = [{"packet_id": packet["packet_id"], "candidate_ids": [item["candidate_id"] for item in packet["items"]
                 if item["candidate_id"] not in state["judgments"]]} for packet in state["packets"]]
    pending = [row for row in pending if row["candidate_ids"]]
    binding = digest([state["binding"], state["round"], pending])
    state.update(status="STALLED_REVIEW_RECOVERY_REQUIRED", recovery_request={"fingerprint": binding, "packets": pending})
    confirmation = workspace / "stalled_recovery_confirmation.json"
    save(confirmation, {"fingerprint": binding, "stopped_tasks": []})
    payload = {"packets": pending, "registry": str(workspace / "native_worker_registry.json"),
               "confirmation_file": str(confirmation),
               "instruction": "先对列出旧包的实际task_id调用TaskStop并确认停止，再向本期旧成员shutdown_request。不能向原写者重派、不能代填答卷。然后一次写确认文件，每包记录packet_id、task_id和status=stopped。仅允许本期一次恢复；脚本保留已通过项，用新包补选其他候选，旧包晚答不再采纳。",
               "confirmation_schema": {"fingerprint": binding, "stopped_tasks": [{"packet_id": "照抄", "task_id": "实际已停止的原生任务ID", "status": "stopped"}]}}
    save(workspace / "stalled_recovery_input.json", payload)
    return {**status(workspace, state), "read_now": str(workspace / "stalled_recovery_input.json"),
            "next_command": f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" recover-stalled --workspace "{workspace}"'}


def recover_stalled(workspace, state):
    if state.get("status") != "STALLED_REVIEW_RECOVERY_REQUIRED" or state.get("stalled_recovery_count", 0):
        raise ValueError("当前没有可恢复的超时包，或本期已恢复一次")
    request = state["recovery_request"]
    answer = load(workspace / "stalled_recovery_confirmation.json")
    rows = answer.get("stopped_tasks", [])
    expected = {row["packet_id"] for row in request["packets"]}
    if (answer.get("fingerprint") != request["fingerprint"] or not isinstance(rows, list)
            or len(rows) != len(expected) or {r.get("packet_id") for r in rows if isinstance(r, dict)} != expected
            or any(not isinstance(r, dict) or r.get("status") != "stopped" or not str(r.get("task_id") or "").strip() for r in rows)):
        raise ValueError("须先停止全部指定旧包写者，再提交对应本期指纹、packet_id和实际task_id；不能并发重派")
    abandoned = state.setdefault("abandoned_candidates", {})
    for packet in request["packets"]:
        for identity in packet["candidate_ids"]:
            abandoned[identity] = {"packet_id": packet["packet_id"], "reason": "UNREVIEWED_STALLED_WRITER_STOPPED"}
    state.setdefault("stalled_recovery_audit", []).append({**request, "stopped_tasks": rows, "recovered_at": time.time()})
    state.update(packets=[], status="PREPARING_FINAL_AI", stalled_recovery_count=1, last_measured_round=state["round"])
    state.pop("recovery_request")
    return next_round(workspace, state)


def registered_team_workers(workspace):
    registry_path = workspace / "native_worker_registry.json"
    if not registry_path.exists():
        return []
    registry = load(registry_path)
    if not isinstance(registry, dict):
        raise ValueError("本期原生成员登记须为JSON对象；先修登记，不重复启动成员")
    workers = registry.get("entries", registry.get("workers", []))
    if not isinstance(workers, list):
        raise ValueError("本期原生成员entries/workers须为列表")
    available, seen = [], set()
    for worker in workers:
        if not isinstance(worker, dict):
            continue
        agent_id, team = worker.get("agent_id"), worker.get("team_name")
        if not isinstance(agent_id, str) or not isinstance(team, str) or not team:
            continue
        if agent_id.count("@") != 1 or agent_id.split("@", 1)[1] != team:
            continue
        if any(term in str(worker.get("status", "")).lower() for term in ("closed", "stopped", "terminated")):
            continue
        if agent_id not in seen:
            available.append(agent_id)
            seen.add(agent_id)
    return available[:8]


def plan_wave(candidate_count, seconds_per_item, remaining_seconds, editor_pending,
              observed_wave_seconds=0, safe_budget=False, coverage_pending=False,
              targeted_first_submission=False, editor_reserve_seconds=EDITOR_RESERVE_SECONDS):
    """Find the largest safe packet, shrinking the wave before stopping.

    Every estimate includes the same editor/closeout reserves exactly once.
    No source, judgment or quality requirement changes with the available time.
    """
    requested = min(MAX_PACKET_ITEMS, max(1, (candidate_count + 7) // 8))
    fixed = CLOSEOUT_RESERVE_SECONDS + (editor_reserve_seconds if editor_pending else 0)
    # Preserve time for the first evidence check, one possible coverage
    # pass and a second evidence check if anything changes.
    if coverage_pending:fixed=max(fixed,1350 if editor_pending else 1050)
    # One item per parallel writer can use the measured first submission
    # as its baseline, with a 150-second floor plus the usual safety margin.
    if safe_budget and targeted_first_submission and candidate_count <= 8:
        startup_floor = max(150, observed_wave_seconds)
    else:
        startup_floor = max(180, min(600, observed_wave_seconds * .6)) if safe_budget else 90
    item_cost = max(seconds_per_item, 45) if safe_budget else seconds_per_item
    margin = 60 if safe_budget else 0
    def cost(size):
        return max(startup_floor, item_cost * size) * 1.25 + fixed + margin
    plan = {"requested_packet_size": requested, "original_wave_required_seconds": round(cost(requested), 3),
            "remaining_seconds": round(remaining_seconds, 3), "fixed_reserve_seconds": fixed,
            "packet_size": 0, "candidate_count": 0, "required_seconds": round(cost(1), 3),
            "shrunk": False, 'startup_floor_seconds': startup_floor,
            'extra_uncertainty_seconds': margin,'coverage_reserve_pending':bool(coverage_pending)}
    for size in range(requested, 0, -1):
        if cost(size) < remaining_seconds:
            plan.update(packet_size=size, candidate_count=min(candidate_count, size * 8),
                        required_seconds=round(cost(size), 3), shrunk=size < requested)
            break
    return plan


def next_round(workspace, state):
    if state["packets"] and any(item["candidate_id"] not in state["judgments"] for packet in state["packets"] for item in packet["items"]):
        remaining = state["started_epoch"] + state["deadline_seconds"] - time.time()
        stalled = time.time() - state.get("last_progress_epoch", state.get("round_started_epoch", time.time()))
        from supplement_schedule import PUBLISH_RESERVE_SECONDS
        closeout = PUBLISH_RESERVE_SECONDS if state.get('locked_candidate_ids') else CLOSEOUT_RESERVE_SECONDS + (EDITOR_RESERVE_SECONDS if state.get("bounded_editor") and not state.get("editorial_status") else 0)
        if (state.get('candidate_closeout_policy') and any(v['decision']=='keep' for v in state['judgments'].values())
                and (remaining <= closeout + (0 if state.get('locked_candidate_ids') else 120) or stalled >= 240)):
            from candidate_closeout import request_closeout
            return request_closeout(workspace,state,digest,status,'RETAIN_EDITOR_TIME_OR_STALLED_UNUSED_CANDIDATES')
        if stalled >= 300 and remaining > 900 and state.get("fresh_wave_workers") and not state.get("stalled_recovery_count", 0):
            return request_stalled_recovery(workspace, state)
        if remaining <= closeout or stalled >= 300:
            state["finish_reason"] = "PENDING_WAVE_TIME_RESERVE" if remaining <= closeout else "PENDING_WAVE_NO_PROGRESS_300_SECONDS"
            return finish(workspace, state)
        return status(workspace, state)
    now = time.time()
    if state.get("round_started_epoch") is not None and state.get("last_measured_round") != state["round"]:
        observed_seconds = max(0, now - state["round_started_epoch"])
        observed_packet_size = max((len(packet["items"]) for packet in state["packets"]), default=1)
        state["max_wave_seconds"] = max(state.get("max_wave_seconds", 0), observed_seconds)
        state["max_wave_per_item_seconds"] = max(state.get("max_wave_per_item_seconds", 0), observed_seconds / max(1, observed_packet_size))
        state["last_measured_round"] = state["round"]
    if now >= state["started_epoch"] + state["deadline_seconds"]:
        state["finish_reason"] = "INSUFFICIENT_TIME_FOR_ANOTHER_OBSERVED_WAVE"
        return finish(workspace, state)
    if state.get('coverage_review_started') and not state.get('locked_candidate_ids'):
        return finish(workspace,state)
    sources = source_map(workspace)
    collapse_cross_group_sources(state,sources)
    from workbench_enrichment import precheck_ready
    if precheck_ready(state):
        return finish(workspace, state)
    from collections import Counter
    from source_balance import channel
    from rapid_film_workbench import rank_candidates
    for index,group in enumerate(state['pool']):
        counts=Counter(channel(sources[v['source_id']]) for v in state['judgments'].values() if v['decision']=='keep' and v['group_index']==index)
        # Ranking must not alter candidate identity fields; reorder original objects only.
        ranked=rank_candidates(group['candidates'],sources,stance=group['stance'],channel_counts=counts)
        order={(v['source_id'],v['start'],v['end'],v['text_field']):i for i,v in enumerate(ranked)}
        group['candidates'].sort(key=lambda v:order[(v['source_id'],v['start'],v['end'],v['text_field'])])
    from sampling_budget import sample_stops
    sampling_stopped=sample_stops(state)
    reserved_candidates=unique_wave_candidates(state,sources)
    pending = []
    full = []
    for index, group in enumerate(state["pool"]):
        if index in sampling_stopped:continue
        keeps = [value for value in state["judgments"].values() if value["group_index"] == index and value["decision"] == "keep"]
        if len(keeps) >= 10:
            full.append(index)
            continue
        identities = {value["identity"] for value in keeps}
        excerpt_hashes = {value.get(key) for value in state["judgments"].values() if value["group_index"] == index for key in ("excerpt_hash", "proposal_excerpt_hash") if value.get(key)}
        media_quotes = [value.get("excerpt", "") for value in keeps if value.get("media_authority_rank")]
        needed = 10 - len(keeps)
        for candidate in group["candidates"]:
            candidate_id = digest([state["binding"], index, candidate])[:20]
            if candidate_id not in reserved_candidates:continue
            if candidate_id in state["judgments"] or candidate_id in state.get("abandoned_candidates", {}) or candidate_id in state.get("length_ineligible_candidates", {}) or candidate["identity"] in identities or (candidate.get("excerpt_hash") and candidate["excerpt_hash"] in excerpt_hashes):
                continue
            record = sources[candidate["source_id"]]
            text = str(record["data"].get(candidate["text_field"]) or "")
            start, end = candidate["start"], candidate["end"]
            excerpt = text[start:end]
            if candidate.get("media_authority_rank") and any(
                    near_identical_media_excerpt(excerpt, prior) for prior in media_quotes):
                continue
            anchor = next((text.find(term) for term in state["terms"] if term in text), -1)
            item = {**candidate, "candidate_id": candidate_id, "group_index": index, "aspect": group["aspect"], "category":group.get("category",""), "core_mechanism":group.get("core_mechanism",""), "stance": group["stance"], "title": text_field(record["data"], "标题"), "excerpt": excerpt, "context": text[max(0,start-100):min(len(text),end+100)], "target_context": text[max(0,anchor-40):anchor+100] if anchor >= 0 and not any(term in excerpt for term in state["terms"]) else "", "source_channel": record["channel"]}
            if state.get('coverage_repair_active'):
                # The supplement was requested against the editor's latest claim.
                item['aspect'] = state.get('editorial_titles', {}).get(str(index), item['aspect'])
                item['core_mechanism'] = state.get('editorial_core_mechanisms', {}).get(str(index), item['core_mechanism'])
            if state.get("selection_contract"):
                if candidate.get('selection_options'):
                    item.update(selection_start=start, selection_text='', context='',
                                source_name=source_author(record))
                else:
                    item.update(selection_start=max(0, start-100), selection_text=item["context"])
            item["min_excerpt_chars"] = excerpt_min_chars(record["channel"])
            from target_reference import review_references
            item=review_references(item,record,state)
            windows = [option['text'] for option in item.get('selection_options', [])]
            if not windows:
                windows = [item.get('selection_text', excerpt)]
            available_length = max((len(display_excerpt(window)) for window in windows), default=0)
            if available_length < item['min_excerpt_chars']:
                state.setdefault('length_ineligible_candidates', {})[candidate_id] = {
                    'source_id': candidate['source_id'], 'group_index': index,
                    'reason': 'NO_WINDOW_MEETS_VISIBLE_LENGTH_MINIMUM',
                    'maximum_visible_length': available_length, 'minimum': item['min_excerpt_chars'],
                    'ai_review_performed': False}
                continue
            pending.append(item)
            identities.add(candidate["identity"])
            if candidate.get("excerpt_hash"):
                excerpt_hashes.add(candidate["excerpt_hash"])
            if candidate.get("media_authority_rank"):
                media_quotes.append(excerpt)
            needed -= 1
            if needed == 0:
                break
    state["stopped_full_groups"] = full
    if not pending:
        state["finish_reason"] = "LOW_YIELD_BACKFILL_STOP_OR_OTHER_GROUPS_FULL" if sampling_stopped else "ALL_GROUPS_FULL_OR_UNUSED_CANDIDATES_EXHAUSTED"
        return finish(workspace, state)
    # Scale observed cost to the next packet, not the original large packet.
    # Retain a fixed overhead floor, uncertainty margin and bounded closeout.
    # Spread all viewpoints across small packets, so one slow writer cannot empty an entire viewpoint.
    grouped = defaultdict(list)
    for item in pending:
        grouped[item['group_index']].append(item)
    kept_counts = {index: sum(value['group_index'] == index and value['decision'] == 'keep'
                             for value in state['judgments'].values()) for index in grouped}
    group_order = sorted(grouped, key=lambda index: (kept_counts[index], index))
    pending = [grouped[index][offset] for offset in range(max(map(len, grouped.values())))
               for index in group_order if offset < len(grouped[index])]
    if (not state.get('workbench_positive_preferred_min') and state.get('candidate_closeout_policy')
            and not state.get('coverage_repair_active') and state['round'] > 0 and len(pending) < 8
            and all(kept_counts[item['group_index']] >= 2 for item in pending)):
        state['finish_reason']='OPTIONAL_SMALL_TAIL_AFTER_TWO_CONFIRMED_VOICES'
        return finish(workspace,state)
    from workbench_completion import POSITIVE_POLICIES
    coverage_pending=(state.get('quantity_contract') in POSITIVE_POLICIES and
                      not state.get('coverage_repair_attempted'))
    coverage_active = bool(state.get('coverage_repair_active'))
    observed_wave_seconds = state.get('max_wave_seconds', 0)
    first_submission = state.get('max_first_submission_wave_seconds')
    if coverage_active and first_submission:
        # A targeted first pass should not pay the startup floor of an
        # earlier full wave including every correction of its last item.
        observed_wave_seconds = first_submission
    editor_pending = not state.get('locked_candidate_ids') and state.get('bounded_editor') and (coverage_active or not state.get('editorial_status'))
    from coverage_repair import editor_reserve
    measured_editor_reserve = editor_reserve(state, EDITOR_RESERVE_SECONDS) if coverage_active else EDITOR_RESERVE_SECONDS
    plan = plan_wave(len(pending), state.get('max_wave_per_item_seconds', 0),
                     state['started_epoch'] + state['deadline_seconds'] - time.time(),
                     bool(editor_pending), observed_wave_seconds, bool(state.get('candidate_closeout_policy')),
                     coverage_pending=coverage_pending,
                     targeted_first_submission=bool(coverage_active and first_submission),
                     editor_reserve_seconds=measured_editor_reserve)
    if state.get('locked_candidate_ids'):
        from supplement_schedule import review_plan
        plan=review_plan(state,len(pending))
    size, reserve = plan['packet_size'], plan['required_seconds']
    state["next_packet_size"] = size
    state["next_wave_required_seconds"] = reserve
    state['wave_budget_plan'] = plan
    state.setdefault('wave_budget_audit', []).append({'next_round': state['round'] + 1, **plan})
    if not size:
        state["finish_reason"] = "INSUFFICIENT_TIME_FOR_ANOTHER_OBSERVED_WAVE"
        return finish(workspace, state)
    pending = pending[:plan['candidate_count']]
    # Validate registry before creating a new round, so malformed metadata is resumable.
    team_workers = registered_team_workers(workspace) if state["round"] > 0 else []
    state["round"] += 1
    if state.get('coverage_repair_active'):state['coverage_review_started']=True
    state["round_started_epoch"] = time.time()
    state["last_progress_epoch"] = state["round_started_epoch"]
    state.pop("finish_reason", None)
    directory = workspace / f"final_ai_round_{state['round']}"
    directory.mkdir(exist_ok=False)
    packets = []
    # Eight inheriting native workers at most; no external model orchestration.
    for index, offset in enumerate(range(0, len(pending), size)):
        items = pending[offset:offset+size]
        packet_id = f"round{state['round']}-packet{index+1}"
        answer = directory / f"answer-{index+1}.json"
        packet = {"packet_id": packet_id, "target": state["target"], "rules": (SPAN_RULE if state.get("selection_contract") else RULE) + " 先读本条category和core_mechanism，只截支持这个核心判断的内容。keep另填evidence_focus，为所选句实际核心所属category，不能照抄期望分类：演员怎么演归演员表演，剧本如何塑造角色归人物书写，镜头调度/剪辑/音乐归视听技术与音乐。演技句不能凭导演标题入组；客观句不能仅是单边赞贬。若句子核心与本组category不同，换片段或reject，不为保样本改写或转簇。", "answer_file": str(answer), "items": items}
        if state.get('progressive_review'):
            packet['progressive_review'] = True
            packet['rules'] += ' 按顺序每完成2条就保存一次完整答卷JSON，未审条目保留原空白。所有ID和指纹保持不变，最后完成全包。写入同目录临时文件后原子替换answer_file，避免收卷读到半截JSON。未审不能写keep/reject；已提交判定不再改，实际校验失败项由主窗口按反馈修。'
        from model_packet import readable_rules
        if state.get('locked_candidate_ids'):
            packet['rules'] += ' 只审本包未审新样本。若是原观点，标题和核心冻结；只判断新原句是否具体支持、合格且是独立表达。不要放宽作品指代、字数或证据要求。'
        packet["rules"] = readable_rules(packet["rules"])
        packet["fingerprint"] = digest(packet)
        template = {"packet_id": packet_id, "fingerprint": packet["fingerprint"], "decisions": [{"candidate_id": item["candidate_id"], "decision": "", "reason": ""} for item in items]}
        if state.get("selection_contract"):
            for row in template["decisions"]:
                row["selected_excerpt"] = ""
                row["selected_window"] = None
                row["evidence_focus"] = ""
        for row,item in zip(template['decisions'],items):
            if item.get('work_reference',{}).get('comparative_or_multiwork_source'):
                row['target_scope']={'reference':'','anchor_quote':'','reason':''}
        packet['conditional_fields']={'target_scope':'对比/多作品来源中所选句未点名本剧时必填：reference=current_work_via_context；anchor_quote为work_reference.local_target_context内含本剧名的至少8字连续原句；reason至少15字说明局部实际承接。不能确认则换窗口内片段或reject。',
                                      'selection':'keep仅选择一种：selected_window为1起始整数且selected_excerpt为空，或selected_window=null且selected_excerpt是原窗口内连续逐字原句。reject不需要选句。'}
        packet["answer_template"] = template
        input_file = directory / f"input-{index+1}.json"
        from model_packet import review_item, write_packet
        visible = {**packet, "items": [review_item(item) for item in items]} if state.get("selection_contract") else packet
        write_packet(input_file, visible)
        save(answer, template)
        packets.append({**packet, "input_file": str(input_file)})
    state.update(packets=packets, status="FINAL_AI_REVIEW_REQUIRED")
    handoff = {"stage": "FINAL_DISPLAY_CANDIDATES_ONLY", "candidate_count": len(pending), "no_upstream_ai_reviews": True, "jobs": [{"packet_id": packet["packet_id"], "input_file": packet["input_file"], "answer_file": packet["answer_file"], "workbuddy_agent_input": {"description": "最终候选摘录审核", "subagent_type": "general-purpose", "run_in_background": True, "prompt": "完整读取UTF-8文件 " + packet["input_file"] + "，只按包内rules写answer_file。所有条目必须有keep/reject。每完成2条原子保存一次完整答卷，未审条目留空，最后审完本包。不调用控制器，不审其他材料。"}} for packet in packets], "collect_command": f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" collect --workspace "{workspace}"', "native_model_selection": "omit_model_to_inherit_current_host", "closeout": "记录每个Agent启动返回的原生ID；答卷写完不代表任务已结束。finish后用原生TaskOutput确认全部已终止，必要时TaskStop只结束自己本期剩余Agent，不启动新审查。不要只勾选Todo完成。"}
    handoff["closeout"] = "保存每次Agent启动返回的agent_id、team_name、task_id（不是Todo号）。若描述含team_name/mailbox，成员是常驻队员：后续波次给空闲成员SendMessage分配新包，不重复另开团队。finish后由主窗口向本期自己启动的每个agent_id发送SendMessage type=shutdown_request，等待shutdown_response同意和成员停止。普通一次性子任务则TaskOutput确认完成，必要时TaskStop自己未结束任务。不要仅将Todo标完或等待常驻成员自动消失；不删除原始数据、工作区或其他团队。"
    handoff["native_registry"] = str(workspace / "native_worker_registry.json")
    for index, job in enumerate(handoff["jobs"]):
        agent_input = job["workbuddy_agent_input"]
        if index < len(team_workers) and not state.get("fresh_wave_workers"):
            job["native_dispatch_tool"] = "SendMessage"
            job["workbuddy_send_message_input"] = {
                "type": "message", "recipient": team_workers[index],
                "summary": "只审本期新包 " + job["packet_id"], "content": agent_input["prompt"]}
            del job["workbuddy_agent_input"]
        else:
            job["native_dispatch_tool"] = "Agent"
            agent_input["name"] = f"film-final-r{state['round']}-reviewer-{index+1}" if state.get("fresh_wave_workers") else f"film-final-reviewer-{index+1}"
    handoff["dispatch_rule"] = "严格按每job的native_dispatch_tool派发；SendMessage复用已登记本期成员，不另起Agent。每人一包，全部本波任务发出后等待。登记只由主窗口累计更新；不使用其他团队。"
    handoff["wait_rule"] = "常驻成员不靠反复TaskOutput轮询。全部派出后等原生通知，或最多45秒短等后执行一次collect_command，只读紧凑状态/反馈；同一轮仍缺答时不要重启任何写者，坏格式只修反馈字段。新轮只按SendMessage派新包，已完成答卷不重审。普通一次性任务TaskOutput确认即可，避免每隔几秒查询所有任务。"
    handoff["closeout"] += " 主窗口累计登记所有启动/终止，不只最后一轮；逐项确认首轮至末轮的未关闭成员均已关闭。登记文件只有主窗口写，包审核者不接管全流程。"
    if state.get("fresh_wave_workers"):
        handoff["dispatch_rule"] = "每个新轮使用handoff指定的唯一新名字Agent启动。本波开始前确认上波写者已完成，对本期上波团队成员发shutdown_request；最多8个同时执行的包。completed任务不靠SendMessage唤醒。只给新轮新文件启动一次写者，累计登记所有本期成员。"
        handoff["wait_rule"] = "全部派出后等通知或最多45秒后collect；同一轮未答不要重启任何写者。连续300秒无新增判定或只剩180秒预算，控制器会发布已有通过项并要求关闭本期成员。新轮使用新handoff的Agent；已通过条目不重审，坏格式只修反馈字段。"
        handoff["closeout"] = "逐轮累计登记真实agent_id/team_name/task_id。上轮写者完成后向上轮本期成员shutdown_request，再派新轮；最终向所有尚未关闭的本期成员shutdown_request并确认结束。不要等待已completed任务的mailbox继续执行，不删除其他团队或文件。"
        handoff["collect_command"] = f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" await-collect --workspace "{workspace}"'
        handoff["wait_rule"] = "全部本波包派发后立即执行collect_command，不先Start-Sleep或查看文件列表。脚本每2秒自动收卷，每次最多45秒；返回repeat_command只重跑该命令，同轮不重启写者。有read_now则读紧凑反馈或下一轮handoff；已通过项不重审。一次只运行一个控制器命令，若工具仍在后台运行先等该命令结束，不能再开一个。"
    handoff.update(budget_approved=True, budget_includes_closeout_and_editor=True,
                   instruction="脚本已批准本轮。直接派发当前jobs并收卷；next_wave_required_seconds已包含全部保留预算，不能重复加任何保留时间或自行提前finish。")
    save(workspace / "final_ai_handoff.json", handoff)
    result = status(workspace, state)
    result["read_now"] = str(workspace / "final_ai_handoff.json")
    return result


def finish(workspace, state):
    if state.get('status')=='COVERAGE_REPAIR_REQUIRED':
        from coverage_repair import response
        return response(workspace,state,status)
    if (state.get('candidate_closeout_policy') and state.get('status')=='CANDIDATE_CLOSEOUT_REQUIRED'):
        return load(workspace/'final_review_status.json')
    if (state.get('candidate_closeout_policy') and any(v['decision']=='keep' for v in state['judgments'].values())
            and any(item['candidate_id'] not in state['judgments'] for p in state['packets'] for item in p['items'])):
        from candidate_closeout import request_closeout
        return request_closeout(workspace,state,digest,status,'FINAL_DISPLAY_ONLY_REQUIRES_UNUSED_WRITERS_CLOSED')
    sources=source_map(workspace)
    collapse_cross_group_sources(state,sources)
    from final_evidence_gate import collapse_display_copies
    collapse_display_copies(state,sources)
    if state.get('locked_candidate_ids'):
        from locked_supplement import finish_locked
        return finish_locked(workspace,state,sources,digest,status)
    if state.get('coverage_repair_active'):
        state['coverage_repair_active'] = False
        changed = any(state['judgments'].get(cid, {}).get('decision') == 'keep'
                      for cid in state.get('coverage_allowed_ids', []))
        if changed:
            for key in list(state):
                if key.startswith('editorial_') or key == 'final_history_check_policy':
                    state.pop(key)
        state['coverage_changed_display_evidence'] = changed
    from coverage_repair import prepare as prepare_coverage
    # An empty draft cannot yet produce an editor packet. All nonempty
    # drafts get the evidence check before the decision to repair coverage.
    if not any(v['decision']=='keep' for v in state['judgments'].values()):
        repair=prepare_coverage(workspace,state,sources,digest,status)
        if repair:return repair
    if state.get("bounded_editor") and not state.get("editorial_status"):
        from display_editor import prepare_editor
        editorial = prepare_editor(workspace, state, digest)
        if editorial:
            return {**status(workspace,state), **editorial,
                    "next_command": f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" editor-collect --workspace "{workspace}"'}
    if state.get('title_contract') == 'FINAL_DISPLAY_EVIDENCE_V2' and state.get('editorial_status') == 'COMPLETE':
        from cluster_claim_audit import prepare as prepare_claim_audit
        claim_audit = prepare_claim_audit(workspace, state, digest)
        if claim_audit:
            return {**status(workspace, state), **claim_audit,
                    'next_command': f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" editor-collect --workspace "{workspace}"'}
    if state.get("editorial_status") == "PENDING":
        state["editorial_status"] = "SKIPPED_EXPLICIT_FINISH"
    if state.get('title_contract')=='FINAL_DISPLAY_EVIDENCE_V2' and state.get('editorial_status')!='COMPLETE':
        state['status']='FINAL_TITLE_EVIDENCE_REVIEW_INCOMPLETE'
        result=status(workspace,state)
        result.update(status=state['status'],instruction='最终展示证据与标题核对未完成，保留真实失败和原预算，不发布完整工作台或接写稿，不重置时钟。')
        save(workspace/'final_review_status.json',result)
        return result
    sources = source_map(workspace)
    collapse_selected_media_copies(state, sources)
    if state.get('title_contract')=='FINAL_DISPLAY_EVIDENCE_V2':
        from final_evidence_gate import final_reference_issues
        reference_errors=final_reference_issues(state)
        if reference_errors:
            state['status']='FINAL_TITLE_EVIDENCE_REVIEW_INCOMPLETE'
            result=status(workspace,state);result.update(status=state['status'],reference_errors=reference_errors,
                instruction='末端去重后标题或入稿证据不再实际展示，不能冒称完成；保留真实失败与预算，不借未展示句补证。')
            save(workspace/'final_review_status.json',result);return result
    repair=prepare_coverage(workspace,state,sources,digest,status)
    if repair:return repair
    dataset = load(workspace / "dataset.json")
    groups = []
    displayed_index = {}
    for index, group in enumerate(state["pool"]):
        keeps = [value for value in state["judgments"].values() if value["group_index"] == index and value["decision"] == "keep"]
        keeps.sort(key=lambda item: source_selection_key(sources[item['source_id']], group['stance'],
                                                         item.get('score', 0), item['source_id']))
        samples = []
        assert len(keeps) <= 10
        assert len({item["identity"] for item in keeps}) == len(keeps)
        for item in keeps:
            record = sources[item["source_id"]]
            if source_exclusion(record):
                raise ValueError(f"{record['id']}: 命中AI稿、明显草稿格式或账号黑名单，须在来源预处理阶段排除")
            text = str(record["data"].get(item["text_field"]) or "")
            length_error = excerpt_length_error(item["excerpt"], record.get("channel"))
            if length_error:
                raise ValueError(f"{item.get('candidate_id')}: {length_error}")
            assert text[item["start"]:item["end"]] == item["excerpt"]
            samples.append({**item, "source": record, "source_text": text, "display_excerpt":display_excerpt(item['excerpt']), "ai_decision": "keep",
                            "display_excerpt": display_excerpt(item["excerpt"])})
        if samples:
            displayed_index[index] = len(groups)
            title = state.get("editorial_titles", {}).get(str(index),group["aspect"])
            groups.append({"aspect": title, "category":group.get('category',''),
                           "objective_kind":group.get('objective_kind'),
                           "core_mechanism":state.get('editorial_core_mechanisms',{}).get(str(index),group.get('core_mechanism','')),
                           "writing_guidance":state.get('editorial_writing_guidance',{}).get(str(index)),
                           "viewpoint_id":group.get("viewpoint_id"),"history_review":state.get('editorial_history_reviews',{}).get(str(index),group.get("history_review")),
                           "stance": group["stance"], "candidate_count": len(group["candidates"]), "samples": samples})
    dataset.update(mode="SCRIPT_THEN_FINAL_AI_ONLY", semantic_verification="FINAL_SELECTED_ONLY", groups=groups, notice="脚本完成粗分，AI仅审核最终展示摘录；未全量审核来源、未复核整个观点组。每组最多10条。", footer="仅展示AI判定keep的原句。其余后台材料不参与AI审查；没有联网核实事实或链接。组名是通用方面词分组，不是精细观点结论。")
    if state.get("ai_viewpoint_discovery"):
        dataset.update(mode="AI_VIEWPOINTS_SCRIPT_ROUTING_FINAL_AI", notice="AI从本期代表材料形成观点簇；脚本排序，AI仅审展示句，每组最多10条。未全量审核来源。", footer="观点标题来自本期AI发现，不是通用方面大类。每句展示样本仅审核一次；不联网核实事实或链接。")
    if state.get("selection_contract"):
        dataset["footer"] = "AI在局部原文中选择展示句，脚本校验逐字出处；没有联网核实事实或链接。"
    if state.get("editorial_status") == "COMPLETE":
        dataset["notice"] = "AI形成本期观点并选择展示句；标题和每组最多2条代表/风险句另做一次有限编辑。每组最多10条，未全量审核来源。"
        if state.get('title_contract') == 'FROZEN_SINGLE_CORE_V1':
            dataset['notice'] = 'AI先形成本期单一核心观点并固定标题，再选择展示句；每组最多2条风险/代表句做有限编辑。每组最多10条，未全量审核来源。'
        dataset["footer"] += " 有限编辑不等于全部展示句二次复核。"
    positive_count = sum(group["stance"] == "正向词倾向" for group in groups)
    from workbench_completion import POSITIVE_POLICIES, SUPPORTED_POSITIVE_POLICY, supported_positive_count
    quantity_policy = state.get('quantity_contract')
    supported_count = supported_positive_count(groups)
    count_for_gate = supported_count if quantity_policy == SUPPORTED_POSITIVE_POLICY else positive_count
    if state.get("ai_viewpoint_discovery"):
        hard=quantity_policy in POSITIVE_POLICIES;target=6 if hard else 10
        state.update(min_positive_clusters=6 if hard else 0, positive_cluster_target=target,
                     positive_count_policy="HARD_MINIMUM" if hard else "ADVISORY_ONLY",positive_cluster_count=positive_count,
                     positive_cluster_shortfall=count_for_gate < target)
        from workbench_completion import positive_count_notice
        notice = ({'code':'POSITIVE_MINIMUM_NOT_MET','actual':count_for_gate,'target':6,'blocks_writing':True,
                   'message':'达到本版证据要求的正面观点不足6组；保留已审工作台，报告实际缺口。'} if count_for_gate<6 else None) if hard else positive_count_notice(groups)
        dataset['quantity_notices'] = [notice] if notice else []
    if state.get('discovery_continuity'):
        initial_continuity=state['discovery_continuity']
        dataset['discovery_continuity']={**initial_continuity,
            'initial_group_reviews':initial_continuity.get('group_reviews',[]),
            'group_reviews':[{'viewpoint_id':g['viewpoint_id'],**g['history_review']} for g in groups if g.get('history_review')],
            'final_display_applied':True,'final_selection_note':'末端合并和删空后，以实际非空组及其最终旧句核对记录判断延续或差异；初次取舍原记录另保留，不推断新增比例。'}
    if state.get('low_yield_sample_stops'):dataset['sampling_stop_audit']=state['low_yield_sample_stops']
    dataset['single_source_policy']='ONE_SOURCE_ONE_VIEWPOINT'
    dataset['title_contract']=state.get('title_contract')
    if state.get('candidate_closeout_policy'):
        dataset['writing_contract']='FOCUSED_QUOTES_V2'
        dataset['adjacent_lead_policy']='VARIED_V1'
        dataset['candidate_coverage']={
            'policy':state['candidate_closeout_policy'], 'scope':'FINAL_DISPLAY_SAMPLES_ONLY',
            'reviewed_candidate_count':len(state['judgments']),
            'unreviewed_not_selected':[{'candidate_id':cid,**row,'ai_review_performed':False,'semantic_decision':None}
                                       for cid,row in state.get('abandoned_candidates',{}).items()],
            'closeout_audit':state.get('candidate_closeout_audit',[]),
            'stalled_recovery_audit':state.get('stalled_recovery_audit',[])}
    if state.get('title_contract')=='FINAL_DISPLAY_EVIDENCE_V2':
        dataset['final_title_evidence_review']={'status':'COMPLETE','contract':'FINAL_DISPLAY_EVIDENCE_V2','scope':'ALL_FINAL_TITLE_CANDIDATES_AND_MAX_2_EXCERPT_EDITS_PER_GROUP'}
        if state.get('final_history_check_policy'):
            dataset['final_title_evidence_review']['history_policy']=state['final_history_check_policy']
        dataset['notice']='本期观点发现后，最终标题按实际已审展示句核对；摘录仍只有每组最多2条接受有限编辑。未全量复核原始来源。'
    pairs = state.get('editorial_overlap_reviews', [])
    if pairs:
        dataset['writing_overlap_pairs'] = [
            {**row, 'left': displayed_index[row['left']], 'right': displayed_index[row['right']]}
            for row in pairs if row['left'] in displayed_index and row['right'] in displayed_index
        ]
    verify_unique_samples(dataset)
    from source_balance import audit as balance_audit
    dataset['source_balance_audit']=balance_audit(groups)
    dataset['quantity_contract']=state.get('quantity_contract')
    if quantity_policy == SUPPORTED_POSITIVE_POLICY:
        dataset['supported_positive_cluster_count'] = supported_count
    if state.get('report_evidence_policy') and state.get('editorial_status') == 'COMPLETE':
        from report_evidence import writing_readiness
        dataset['writing_readiness'] = writing_readiness(groups)
    history_file=workspace/'period_history_context.json'
    if history_file.exists():
        from period_history import continuity_review
        dataset['continuity_review']=continuity_review(dataset,load(history_file))
    save(workspace / "final_dataset.json", dataset)
    within = time.time() < state["started_epoch"] + state["deadline_seconds"]
    pending_reviews = sum(item['candidate_id'] not in state['judgments']
                          for packet in state['packets'] for item in packet['items'])
    state["status"] = "FINAL_SELECTED_COMPLETE" if groups and within else "TIME_LIMIT_REACHED" if not within else "NO_ACCEPTED_SAMPLES"
    if within and groups and state.get("ai_viewpoint_discovery") and not 1 <= len(groups) <= 16:
        state["status"] = "VIEWPOINT_RANGE_NOT_MET"
    elif within and groups and pending_reviews:
        state['status'] = 'FINAL_SELECTED_PARTIAL'
    if quantity_policy in POSITIVE_POLICIES and count_for_gate<6 and state['status']=='FINAL_SELECTED_COMPLETE':
        state['status']='FINAL_POSITIVE_MIN_NOT_MET'
    result = status(workspace, state)
    if state.get("ai_viewpoint_discovery"):
        result.update(min_positive_clusters=0, positive_cluster_target=10, positive_count_policy="ADVISORY_ONLY", positive_cluster_count=positive_count,
                      positive_cluster_shortfall=positive_count < 10, quantity_notices=dataset.get('quantity_notices', []))
    result.update(output=str(workspace / "workbench.html"), group_count=len(groups), structure_verification="PASS", semantic_verification="FINAL_SELECTED_ONLY", finish_reason=state.get("finish_reason", "EXPLICIT_FINISH_WITH_ALREADY_ACCEPTED_SAMPLES"), completed_within_30_minutes=(time.time()-state["started_epoch"] <= 1800) and state["status"] == "FINAL_SELECTED_COMPLETE", completed_within_45_minutes=(time.time()-state["started_epoch"] <= 2700) and state["status"] == "FINAL_SELECTED_COMPLETE", completed_within_budget=within and state["status"] == "FINAL_SELECTED_COMPLETE", groups_below_ten=[{"aspect": group["aspect"], "stance": group["stance"], "count": len(group["samples"])} for group in groups if len(group["samples"]) < 10])
    result["editorial_reviewed_samples"] = len(state.get("editorial_audit", []))
    if quantity_policy in POSITIVE_POLICIES:
        result.update(quantity_contract=state['quantity_contract'],min_positive_clusters=6,positive_cluster_target=6,
                      positive_count_policy='HARD_MINIMUM',positive_cluster_shortfall=count_for_gate<6,
                      supported_positive_cluster_count=supported_count,
                      coverage_repair_attempted=state.get('coverage_repair_attempted',False),
                      coverage_repair_skip_reason=state.get('coverage_repair_skip_reason'),source_balance_audit=dataset['source_balance_audit'])
    result["editorial_elapsed_seconds"] = state.get("editorial_elapsed_seconds")
    result["closeout_instruction"] = "立即核对本期native_worker_registry.json。只对本主窗口本期真实登记且未确认终止的task_id调用TaskStop，停止尚在执行的包；再向本期成员shutdown_request并确认结束。已超时或已发布后不要继续等待其交卷，不重开编辑，不处理其他团队。HTML已生成不代表主窗口已正常退出。"
    result['closeout_instruction'] += ' 已收到completed/TaskStop已结束的任务不要再逐个查询或睡眠等重复确认。确认本期没有运行任务后，仅清理登记的本会话自动团队（TeamDelete），及时结束主会话；不操作其他会话团队。最终回复只报实际状态、数量和路径，不重写一篇过程报告。'
    save(workspace / "final_review_status.json", result)
    (workspace / "workbench.html").write_text(render(dataset, result), encoding="utf-8")
    from workbench_enrichment import protect_and_enrich
    return protect_and_enrich(workspace, state, dataset, result, sources, digest, status)


def collect(workspace, state):
    if state.get('last_collected_round') != state['round']:
        state['last_collected_round'] = state['round']
        state['last_progress_epoch'] = time.time()
    if state.get('status')=='CANDIDATE_CLOSEOUT_REQUIRED':
        return load(workspace/'final_review_status.json')
    if state["status"] != "FINAL_AI_REVIEW_REQUIRED":
        return status(workspace, state)
    if digest(load(workspace / state.get("pool_file", "candidate_pool.json"))) != state["pool_hash"] or digest(load(workspace / "raw_extraction/manifest.json")) != state["raw_hash"]:
        raise ValueError("本期原始提取或候选池变更，拒绝旧审查答卷")
    errors = []
    sources=source_map(workspace)
    previous_count = len(state["judgments"])
    for packet in state["packets"]:
        try:
            answer = load(Path(packet["answer_file"]))
            if answer.get("packet_id") != packet["packet_id"] or answer.get("fingerprint") != packet["fingerprint"]:
                raise ValueError("packet_id/fingerprint不匹配")
            rows = answer.get("decisions")
            # Transport-only alias: retain actual AI decisions and all current ID/fingerprint gates.
            # Never rewrite a worker's file or infer missing judgments.
            if "decisions" not in answer and isinstance(answer.get("answers"), list):
                rows = answer["answers"]
                normalization = {"answer_file": packet["answer_file"], "from_key": "answers", "to_key": "decisions", "semantic_judgments_inferred": False}
                audit = state.setdefault("transport_normalizations", [])
                if normalization not in audit:
                    audit.append(normalization)
            if not isinstance(rows, list):
                raise ValueError("decisions必须为列表")
            expected = {item["candidate_id"]: item for item in packet["items"]}
            seen = set()
            for row in rows:
                candidate_id = row.get("candidate_id") if isinstance(row, dict) else None
                if candidate_id not in expected or candidate_id in seen:
                    errors.append({"answer_file": packet["answer_file"], "candidate_id": candidate_id, "error": "未知或重复ID"})
                    continue
                seen.add(candidate_id)
                if candidate_id in state["judgments"]:
                    continue
                if state.get('progressive_review') and row.get('decision') == '':
                    continue
                if row.get("decision") not in ("keep", "reject") or (row.get("decision") == "reject" and not str(row.get("reason") or "").strip()):
                    errors.append({"answer_file": packet["answer_file"], "candidate_id": candidate_id, "error": "填keep或reject；reject须短原因"})
                    continue
                item = expected[candidate_id]
                selected = dict(item)
                if row['decision']=='keep' and state.get('ai_viewpoint_discovery'):
                    if row.get('evidence_focus')!=item.get('category'):
                        errors.append({'answer_file':packet['answer_file'],'candidate_id':candidate_id,'error':'keep须填写所选句实际evidence_focus并匹配本组category；若评价对象/机制不符，换本窗口内支持片段或reject，不照抄分类绕过。'})
                        continue
                    selected['evidence_focus']=row['evidence_focus']
                if row["decision"] == "keep" and state.get("selection_contract"):
                    quote = row.get("selected_excerpt")
                    resolved = resolve_selection(item, quote, row.get("selected_window"))
                    if resolved is None or not str(row.get("reason") or "").strip():
                        errors.append({"answer_file": packet["answer_file"], "candidate_id": candidate_id, "error": "keep二选一：有效selected_window序号，或一个窗口内连续逐字selected_excerpt；给短reason，只修此项，不重审已通过项", **selection_failure_details(item,row)})
                        continue
                    quote, offset, restored_spaces = resolved
                    if restored_spaces:
                        state.setdefault("transport_normalizations", []).append({"candidate_id":candidate_id,
                            "kind":"UNAMBIGUOUS_NBSP_RESTORED_FROM_SOURCE", "source_offset":offset,
                            "semantic_judgments_inferred":False})
                    selected.update(proposal_start=item["start"], proposal_end=item["end"], proposal_excerpt_hash=item.get("excerpt_hash"),
                                    start=offset, end=offset+len(quote),
                                    excerpt=quote, excerpt_hash=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                                    selection_contract=state["selection_contract"])
                if row["decision"] == "keep":
                    length_error = excerpt_length_error(selected["excerpt"], item.get("source_channel"))
                    if length_error:
                        errors.append({"answer_file": packet["answer_file"], "candidate_id": candidate_id, "error": length_error,
                                       "selected_excerpt": selected["excerpt"],
                                       "displayed_excerpt": display_excerpt(selected["excerpt"]),
                                       "actual_display_chars": len(display_excerpt(selected["excerpt"])),
                                       "minimum_display_chars": excerpt_min_chars(item.get("source_channel")),
                                       "length_instruction": "按displayed_excerpt计算，话题标签、表情和平台尾巴清理后不计字数。选同一给定窗口内完整有效原句；不得加标签、无关句或拼接凑长。没有满足原标准的选句才reject并写实际内容原因。"})
                        continue
                    from final_evidence_gate import scope_issue
                    issue=scope_issue(selected,sources[selected['source_id']],state,row)
                    if issue:
                        from target_reference import local_binding
                        errors.append({'answer_file':packet['answer_file'],'candidate_id':candidate_id,
                            'error':issue+'；本次实际选句的指代上下文见actual_work_reference，它替换初始窗口的work_reference，只用于指代，不补观点。',
                            'selected_excerpt':selected['excerpt'],
                            'actual_work_reference':local_binding(selected,sources[selected['source_id']],state)})
                        continue
                    if isinstance(row.get('target_scope'),dict):selected['target_scope']=row['target_scope']
                state["judgments"][candidate_id] = {**selected, "decision": row["decision"], "reason": str(row.get("reason") or "")}
            for candidate_id in expected.keys() - seen:
                if candidate_id not in state["judgments"]:
                    errors.append({"answer_file": packet["answer_file"], "candidate_id": candidate_id, "error": "漏答"})
        except (ValueError, OSError) as exc:
            errors.append({"answer_file": packet["answer_file"], "error": str(exc)})
    collapse_selected_media_copies(state, sources)
    from final_evidence_gate import collapse_display_copies
    collapse_display_copies(state,sources)
    packet_paths={packet['answer_file']:packet.get('input_file') for packet in state['packets']}
    untouched=set()
    for packet in state['packets']:
        try:
            answer = load(Path(packet['answer_file']))
            if answer == packet.get('answer_template') or (state.get('progressive_review') and
                    any(row.get('decision') == '' for row in answer.get('decisions', []))):
                untouched.add(str(packet['answer_file']))
        except (ValueError,OSError):pass
    if (not untouched and state.get('round_started_epoch') is not None
            and state.get('first_submission_measured_round') != state['round']):
        submitted_seconds = max(0, time.time() - state['round_started_epoch'])
        state['first_submission_measured_round'] = state['round']
        state['max_first_submission_wave_seconds'] = max(
            state.get('max_first_submission_wave_seconds', 0), submitted_seconds)
    actionable_errors=[error for error in errors if str(error.get('answer_file')) not in untouched]
    for error in errors:
        if packet_paths.get(error.get('answer_file')):error['input_file']=packet_paths[error['answer_file']]
    from model_packet import review_item
    repair_items = []
    by_path = {str(packet['answer_file']): packet for packet in state['packets']}
    for error in actionable_errors:
        packet = by_path.get(str(error.get('answer_file')), {})
        item = next((item for item in packet.get('items', []) if item['candidate_id'] == error.get('candidate_id')), None)
        if item is not None:
            visible = review_item(item) if state.get('selection_contract') else item
            repair_items.append({'answer_file': error['answer_file'], 'candidate_id': item['candidate_id'], 'error': error['error'], 'item': visible})
    from model_packet import write_packet
    write_packet(workspace / 'final_ai_feedback.json', {
        'errors': actionable_errors, 'pending_writer_errors': [e for e in errors if e not in actionable_errors],
        'waiting_templates': sorted(untouched), 'transport_normalizations': state.get('transport_normalizations', []),
        'original_packets': packet_paths,
        'repair_rules': 'repair_items仅为原审核包中报错的同一条完整材料，没有新增来源。直接读这里修字段，不写临时提取程序；原写者仍有空白项时先等其完成，由唯一写者负责答卷。已通过项不重审，不以格式错误为由伪填拒绝。',
        'repair_items': repair_items,
    })
    if len(state["judgments"]) > previous_count:
        state["last_progress_epoch"] = time.time()
    if errors:
        if (state.get('candidate_closeout_policy') and any(v['decision']=='keep' for v in state['judgments'].values())
                and state['started_epoch']+state['deadline_seconds']-time.time() <= (45 if state.get('locked_candidate_ids') else EDITOR_RESERVE_SECONDS+CLOSEOUT_RESERVE_SECONDS+120)):
            from candidate_closeout import request_closeout
            return request_closeout(workspace,state,digest,status,'PRESERVE_EDITOR_TIME_WITH_UNRESOLVED_CANDIDATES')
        if actionable_errors and state['started_epoch']+state['deadline_seconds']-time.time()>(45 if state.get('locked_candidate_ids') else CLOSEOUT_RESERVE_SECONDS):
            result=status(workspace,state)
            result.update(next_action='REPAIR_SUBMITTED_ANSWERS',read_now=str(workspace/'final_ai_feedback.json'),
                next_command=f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" collect --workspace "{workspace}"',
                instruction='先修当前反馈缺答，不能把坏格式当成等空包；原写者结束时仅补读所列原句实际审核。保留全部已通过判断和原预算，不提前finish。')
            return result
        result = next_round(workspace, state)
        if state["status"] == "FINAL_AI_REVIEW_REQUIRED":
            result["read_now"] = str(workspace / "final_ai_feedback.json")
        return result
    return next_round(workspace, state)


def await_collect(workspace, state, max_wait_seconds=45):
    """Poll the current wave only; never judge, dispatch or write worker answers."""
    if not 0 <= max_wait_seconds <= 45:
        raise ValueError("单次等待必须在0—45秒内")
    if state.get("status") != "FINAL_AI_REVIEW_REQUIRED":
        # Preserve final publication metadata; do not call status() over it.
        return load(workspace / "final_review_status.json")
    current_round = state["round"]
    until = time.monotonic() + max_wait_seconds
    while True:
        result = collect(workspace, state)
        if state["status"] != "FINAL_AI_REVIEW_REQUIRED" or state["round"] != current_round:
            return result
        # Untouched templates are writers still at work, not malformed submissions.
        # Genuine errors return immediately so the model repairs only those fields.
        blank_files = set()
        for packet in state["packets"]:
            try:
                answer = load(Path(packet["answer_file"]))
                if answer == packet.get("answer_template") or (state.get("progressive_review") and any(row.get("decision") == "" for row in answer.get("decisions", []))):
                    blank_files.add(str(packet["answer_file"]))
            except (ValueError, OSError):
                pass
        feedback = load(workspace / "final_ai_feedback.json")
        actionable = [error for error in feedback.get("errors", [])
                      if str(error.get("answer_file")) not in blank_files]
        if actionable:
            result.update(next_action='REPAIR_SUBMITTED_ANSWERS',
                          instruction='这是已提交答卷的具体校验问题，不是等待空模板。读取read_now，仅修反馈项；原写者已经结束时由主窗口修复。窗口内无法满足字数或观点要求就reject，不反复照抄同一段，不重新审已通过项。')
            save(workspace / 'final_review_status.json', result)
            return result
        remaining = until - time.monotonic()
        if remaining <= 0:
            result.pop("read_now", None)
            result['next_action']='WAITING_FOR_ORIGINAL_WRITERS'
            result["waiting_for_original_writers"] = True
            result["repeat_command"] = f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" await-collect --workspace "{workspace}"'
            result["instruction"] = "仅等待原写者，直接执行repeat_command；不另睡眠、不查文件列表、不重复派发、不审已通过项。"
            return result
        time.sleep(min(2, remaining))


def prepare(args):
    started = time.time()
    from period_history import load_history
    history=load_history(getattr(args,'history',[]),args.target,getattr(args,'history_plan',[]))
    argv = [sys.executable, "-X", "utf8", str(Path(__file__).with_name("rapid_film_workbench.py")), "--source", str(args.source), "--workspace", str(args.workspace), "--target", args.target, "--per-group", "10", "--deadline-seconds", str(args.deadline_seconds)]
    for alias in args.alias:
        argv.extend(["--alias", alias])
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=args.deadline_seconds+5)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    workspace = args.workspace.resolve()
    if history['entries']:save(workspace/'period_history_context.json',history)
    (workspace / "workbench.html").rename(workspace / "script_proposal.html")
    pool = load(workspace / "candidate_pool.json")
    sources = source_map(workspace) if args.discover_viewpoints else None
    if args.discover_viewpoints:
        from social_criticism_probe import social_criticism_probe_group
        from media_viewpoint_probe import media_viewpoint_probe_groups
        from report_evidence import report_evidence_probe_groups
        probe = social_criticism_probe_group(sources, [args.target, *args.alias])
        if probe:
            pool.insert(0, probe)
        media_groups, media_audit = media_viewpoint_probe_groups(sources, [args.target, *args.alias])
        pool[1 if probe else 0:1 if probe else 0] = media_groups
        report_groups = report_evidence_probe_groups(sources, [args.target, *args.alias])
        start = int(bool(probe)) + len(media_groups)
        pool[start:start] = report_groups
        save(workspace / "media_recall_audit.json", media_audit)
        if probe or media_groups or report_groups:
            save(workspace / "candidate_pool.json", pool)
    raw_hash = digest(load(workspace / "raw_extraction/manifest.json"))
    state = {"status": "PREPARING_FINAL_AI", "started_at": dt.datetime.now().astimezone().isoformat(), "started_epoch": started, "deadline_seconds": args.deadline_seconds, "target": args.target, "terms": [args.target,*args.alias], "pool": pool, "pool_hash": digest(pool), "raw_hash": raw_hash, "binding": digest([raw_hash,pool,args.target,args.alias,RULE]), "round": 0, "packets": [], "judgments": {}}
    if args.discover_viewpoints:
        state.update(selection_contract="VERBATIM_DISPLAY_SPAN_V1", fresh_wave_workers=True, bounded_editor=True, progressive_review=True)
        from workbench_completion import SUPPORTED_POSITIVE_POLICY
        state['quantity_contract']=SUPPORTED_POSITIVE_POLICY
        state['report_evidence_policy'] = 'CONCRETE_OPINION_RECALL_V1'
        from candidate_closeout import POLICY
        state['candidate_closeout_policy']=POLICY
        state["binding"] = digest([state["binding"], SPAN_RULE, state["selection_contract"]])
        state['workbench_positive_preferred_min'] = 8
        state['protect_ready_workbench'] = bool(getattr(args,'protect_ready_workbench',False))
        state['binding']=digest([state['binding'],state['candidate_closeout_policy'],'FOCUSED_QUOTES_V2',
                                 state['workbench_positive_preferred_min'],state['protect_ready_workbench']])
    if args.discover_viewpoints:
        from viewpoint_discovery import prepare_discovery
        handoff = prepare_discovery(workspace,state,sources,digest)
        return {**status(workspace,state), **handoff}
    return next_round(workspace, state)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    init = commands.add_parser("prepare")
    init.add_argument("--source", type=Path, required=True)
    init.add_argument("--workspace", type=Path, required=True)
    init.add_argument("--target", required=True)
    init.add_argument("--alias", action="append", default=[])
    init.add_argument("--deadline-seconds", type=float, default=2700)
    init.add_argument("--discover-viewpoints", action="store_true")
    init.add_argument("--protect-ready-workbench", action="store_true")
    init.add_argument('--history',type=Path,action='append',default=[],help='同一作品前期已交付Word报告（.docx），可重复；也接受原有工作台格式')
    init.add_argument('--history-plan',type=Path,action='append',default=[],help='对应前期实际成稿的editorial_plan.json，须匹配历史工作台指纹')
    for command in ("collect", "await-collect", "finish", "discover-collect", "sketch-collect", "editor-collect", "recover-stalled", "close-candidates", "repair-collect", "supplement-collect", "skip-supplement-batch"):
        commands.add_parser(command).add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        if not 0 < args.deadline_seconds <= 2700:
            raise ValueError("工作台预算必须为(0,2700]秒")
        result = prepare(args)
    else:
        workspace = args.workspace.resolve()
        state = load(workspace / "final_review_state.json")
        if args.command == 'skip-supplement-batch':
            if not state.get('locked_candidate_ids') or state['status'] not in ('COVERAGE_REPAIR_REQUIRED','FINAL_AI_REVIEW_REQUIRED','SUPPLEMENT_VIEWPOINTS_REQUIRED','CANDIDATE_CLOSEOUT_REQUIRED'):
                raise ValueError('没有可以结束的追加批次')
            state['supplement_batches'][-1]['incomplete'] = True
            if state['status']=='SUPPLEMENT_VIEWPOINTS_REQUIRED':
                skipped=state.setdefault('supplement_groups_skipped',{})
                skipped.update({str(i):'本批标题审核未交有效答卷' for i in state['supplement_new_indices']})
            state.update(packets=[], status='PREPARING_FINAL_AI')
            from locked_supplement import finish_locked
            result=finish_locked(workspace,state,source_map(workspace),digest,status)
        elif args.command == 'supplement-collect':
            from locked_supplement import collect_new_groups,finish_locked
            sources=source_map(workspace)
            if time.time()<state['started_epoch']+state['deadline_seconds']:
                try:
                    collect_new_groups(workspace,state,load(workspace/'supplement_viewpoints_answer.json'),sources)
                except (ValueError,OSError,TypeError) as exc:
                    skipped=state.setdefault('supplement_groups_skipped',{})
                    skipped.update({str(i):str(exc) for i in state['supplement_new_indices']})
            result=finish_locked(workspace,state,sources,digest,status)
        elif args.command == 'repair-collect':
            from coverage_repair import apply as apply_repair, response as repair_response, editor_reserve
            from supplement_schedule import PUBLISH_RESERVE_SECONDS
            reserve=(PUBLISH_RESERVE_SECONDS+30 if state.get('locked_candidate_ids')
                     else CLOSEOUT_RESERVE_SECONDS+editor_reserve(state,EDITOR_RESERVE_SECONDS))
            if state['started_epoch']+state['deadline_seconds']-time.time()<=reserve:
                state.update(coverage_repair_attempted=True,coverage_repair_closed=True,status='PREPARING_FINAL_AI',coverage_repair_skip_reason='补缺作答耗尽可用窗口，保留末端核对与收尾，不跳审')
                result=finish(workspace,state)
            else:
                try:apply_repair(workspace,state,load(workspace/'coverage_repair_answer.json'),source_map(workspace),digest)
                except (ValueError,OSError,TypeError) as exc:
                    save(workspace/'coverage_repair_feedback.json',{'error':str(exc)})
                    result=repair_response(workspace,state,status)
                    result['read_now']=[str(workspace/'coverage_repair_input.json'),str(workspace/'coverage_repair_feedback.json')]
                else:result=next_round(workspace,state)
        elif args.command == 'close-candidates':
            from candidate_closeout import confirm_closeout
            confirm_closeout(workspace,state,digest)
            result=finish(workspace,state)
        elif args.command == "recover-stalled":
            result = recover_stalled(workspace, state)
        elif args.command == "finish" and state.get("status") == "STALLED_REVIEW_RECOVERY_REQUIRED":
            result = {**status(workspace, state), "read_now": str(workspace / "stalled_recovery_input.json"),
                      "next_command": f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" recover-stalled --workspace "{workspace}"'}
        elif args.command == "finish" and state.get("editorial_status") == "PENDING":
            result = {**status(workspace, state), "next_command": f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" editor-collect --workspace "{workspace}"',
                      "instruction": "编辑已经启动，执行editor-collect让脚本校验答卷或按硬截止回退，不提前跳过。"}
        elif args.command == "finish" and state.get("ai_viewpoint_discovery") and state.get("status") == "FINAL_AI_REVIEW_REQUIRED":
            result = collect(workspace, state)
            if state.get("status") == "FINAL_AI_REVIEW_REQUIRED":
                result.update(instruction="脚本尚未触发停止条件，不接受提前finish。执行当前handoff或await-collect，不重复计算保留预算。",
                              read_now=str(workspace / "final_ai_handoff.json"))
        elif args.command == "editor-collect":
            from display_editor import apply_editor, EDITOR_SECONDS
            remaining = state["started_epoch"] + state["deadline_seconds"] - time.time()
            editor_elapsed = time.time() - state.get("editorial_started_epoch", time.time())
            if state.get("editorial_status") != "PENDING":
                raise ValueError("当前不是有限编辑阶段")
            # An already submitted review still needs full validation. Do not
            # discard it solely for a static cleanup reserve; the cleaning hard
            # deadline remains binding, and unfinished reviews never pass.
            ready_answer = False
            try:
                submitted = load(workspace / 'display_editor_answer.json')
                ready_answer = (isinstance(submitted, dict) and
                                submitted.get('fingerprint') == state.get('editorial_binding') and
                                isinstance(submitted.get('groups'), list) and bool(submitted['groups']))
            except (ValueError, OSError):
                pass
            if remaining <= 0 or (remaining <= CLOSEOUT_RESERVE_SECONDS and not ready_answer):
                state["editorial_status"] = "SKIPPED_EDITOR_TIME_LIMIT"
                result = finish(workspace,state)
            elif not (workspace/'display_editor_answer.json').exists():
                if editor_elapsed >= EDITOR_SECONDS:
                    state['editorial_status'] = 'SKIPPED_EDITOR_TIME_LIMIT'
                    result = finish(workspace,state)
                else:
                    result = {**status(workspace,state),"instruction":"编辑答卷尚未写出；等原写者完成后再执行editor-collect。"}
            else:
                try:
                    editor_answer = load(workspace/'display_editor_answer.json')
                    if isinstance(editor_answer,dict) and editor_answer.get("groups") == [] and editor_answer.get("fingerprint") == state.get("editorial_binding"):
                        if editor_elapsed >= EDITOR_SECONDS:
                            state["editorial_status"] = "SKIPPED_EDITOR_TIME_LIMIT"
                            result = finish(workspace,state)
                        else:
                            result = {**status(workspace,state),"instruction":"独立编辑答卷仍为空模板；等原写者，不启动重复编辑，不代填。最多45秒后editor-collect。"}
                    else:
                        apply_editor(workspace,state,editor_answer)
                        state["editorial_collection_grace_applied"] = editor_elapsed >= EDITOR_SECONDS
                        result = finish(workspace,state)
                except (ValueError,OSError,TypeError) as exc:
                    save(workspace/'display_editor_feedback.json',{"error":str(exc),"instruction":"仅修编辑答卷字段，不重审其他材料；保持本次工作台的硬截止。已提交非空答卷可在清洗剩余时间内修正并完整校验，不因静态180秒预留丢弃。"})
                    result = {**status(workspace,state),"read_now":str(workspace/'display_editor_feedback.json')}
        elif args.command == "sketch-collect":
            from discovery_sketch import collect as collect_sketch
            try:
                if digest(load(workspace / "candidate_pool.json")) != state["pool_hash"] or digest(load(workspace / "raw_extraction/manifest.json")) != state["raw_hash"]:
                    raise ValueError("本期原始材料/候选池变更")
                handoff = collect_sketch(workspace, state, load(workspace / "discovery_sketch_answer.json"))
            except (ValueError, OSError) as exc:
                save(workspace / "discovery_sketch_feedback.json", {"error": str(exc), "instruction": "只修草案字段，不预先通过任何样本。"})
                result = {**status(workspace, state), "read_now": str(workspace / "discovery_sketch_feedback.json")}
            else:
                result = {**status(workspace, state), **handoff,
                          "next_command": f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" discover-collect --workspace "{workspace}"'}
        elif args.command == "discover-collect":
            from viewpoint_discovery import apply_discovery
            try:
                if digest(load(workspace / "candidate_pool.json")) != state["pool_hash"] or digest(load(workspace / "raw_extraction/manifest.json")) != state["raw_hash"]:
                    raise ValueError("本期原始材料/候选池变更")
                apply_discovery(workspace,state,load(workspace / "viewpoint_discovery_answer.json"),source_map(workspace),digest)
            except (ValueError,OSError) as exc:
                save(workspace / "viewpoint_discovery_feedback.json", {"error":str(exc),"instruction":"只修答卷所列结构/标题错误，不重读全量来源；不捏造观点。"})
                result = {**status(workspace,state),"read_now":str(workspace / "viewpoint_discovery_feedback.json")}
            else:
                result = next_round(workspace,state)
        elif args.command == "await-collect":
            result = await_collect(workspace, state)
        else:
            result = collect(workspace,state) if args.command == "collect" else finish(workspace,state)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

