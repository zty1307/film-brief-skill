"""Scripts first; AI judges only proposed display excerpts. No upstream reviews."""
from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
from collections import defaultdict

from rapid_film_workbench import (render, save, text_field, resolve_verbatim_span, WORK_SCOPE_RULE,
                                 resolve_selection, source_selection_key, source_author, excerpt_min_chars, excerpt_length_error,
                                 source_exclusion, display_excerpt, CLOSEOUT_RESERVE_SECONDS)

MAX_PACKET_ITEMS = 10
EDITOR_RESERVE_SECONDS = 300

RULE = "只审核最终展示候选，不审核整篇来源、不复核其他组、不改组、不重截。keep表示你已确认：(1)摘录评价的是目标剧，不是别剧/别人的评价；(2)确实支持本组标题的完整核心观点，不只是同方面、演员名或关键词匹配；(3)正负态度符合本组，中性组只能是中性事实/客观观察，不能把混合正负当中性；(4)原句可独立理解，不是名单、宣传指令、残缺句或纯空泛表态。拿不准或不支持就reject并写一句短原因，不查网络。只读本包，向answer_file一次写入所有candidate_id的keep/reject；fingerprint和packet_id照抄。你是包审核者，不是整个Skill的操作者；不要重启Skill、调用控制器/Agent、读取handoff/其他包或全量来源。不要修标题、换组、改原文、生成报告或额外审查。"

SPAN_RULE = """你负责选择最终展示原句并审核，只读本包。对每项从selection_text中选一个连续逐字片段，填写selected_excerpt（短评至少50字，文章类至少70字，均最多360字，具体下限见每项min_excerpt_chars）；原候选已合格也必须照抄所选原句。可在给定局部窗口内去掉推广、无关八卦或残句前后缀，不去查全文，不拼接、不补字、不改写。不合适则reject和一句短原因，selected_excerpt留空。
keep同时确认：所选片段评价目标剧；直接支持aspect标题的核心判断；立场与本组一致；全文展示片段完整可读、有具体依据。context、target_context只用于确定指代和作品，不能借其未展示的评价证明keep。逐字选出的selected_excerpt才是读者会看到的全部文字，reason不能补充它没有的观点。泛泛称好不能证明实拍无替身；列演员或歌曲名单不能证明表演/音乐好；只说热度不能证明商业收益。客观组不得保留“拿什么输”“匠心心血”等单边赞誉，只能选择原文中真正中性的完整陈述；司法/商业说法是来源说法，未核验事实。删去星标/关注/购票/抽奖邀请和无关盘点；不能留下句首句尾截断、只剩期待的口号。每条keep给一句简短reason，指出所选原文支持的具体核心判断；不支持就reject，不能靠推测凑满10。
一次写自己的answer_file，所有candidate_id判keep/reject，fingerprint与packet_id照抄。你只审核本包，不调用控制器或Agent，不读其他包/全量来源，不改标题、不换组、不联网。"""

SPAN_RULE += " 所选原句若只有剧情简介、定档或开播消息而没有对应评价，不得放入正负组。未播期待不能证明播后成绩；某榜第几名不能证明另一榜登顶；角色设定不能代替演技评价。负面组优先送审的微博、小红书材料仍须实际批评目标作品；玩笑式‘吐槽’、幕后花絮、正面期待或剧宣不能仅凭平台和词语当作负评。标题是固定判据，不会在后面替错样本改标题。"
SPAN_RULE += " 对社交平台的混合语气评论，按所选连续展示片段里的具体评价判断：有明确的观剧不满或具体动作问题，即使同帖还有赞赏或表情符号，也不要仅因整帖偏正面一概判成玩笑；纯玩梗、没有实际批评对象的仍拒绝。必须逐字满足长度，并且只支持标题所写的窄主张；‘节奏慢’不能支持‘节奏仓促’，‘动作有舞蹈痕迹’不能单独支持‘整体演技差’或尚未开播的猜测。"
RULE += WORK_SCOPE_RULE
SPAN_RULE += WORK_SCOPE_RULE
SPAN_RULE += " 有selection_options时，它们是同一篇长文中最多两个备选连续片段，不是两条样本；先比较哪段最支持本组，再从其中一个片段选selected_excerpt。优先保留能解释判断的完整论据，文章类硬下限70字，通常80—200字，需要完整论据时可到300字，硬上限360字；不为凑长度硬加无关话，也不把有用解释裁成一句口号。保留原换行，禁止跨选项拼接。没有selection_options才用selection_text。媒体身份/渠道优先级只影响送审顺序，不代表内容正确，仍按同一语义标准审核。"


def load(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def source_map(workspace):
    with (workspace / "raw_extraction/records.jsonl").open(encoding="utf-8") as handle:
        return {row["id"]: row for row in map(json.loads, handle)}


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


def plan_wave(candidate_count, seconds_per_item, remaining_seconds, editor_pending):
    """Find the largest safe packet, shrinking the wave before stopping.

    Every estimate includes the same editor/closeout reserves exactly once.
    No source, judgment or quality requirement changes with the available time.
    """
    requested = min(MAX_PACKET_ITEMS, max(1, (candidate_count + 7) // 8))
    fixed = CLOSEOUT_RESERVE_SECONDS + (EDITOR_RESERVE_SECONDS if editor_pending else 0)
    def cost(size):
        return max(90, seconds_per_item * size) * 1.25 + fixed
    plan = {"requested_packet_size": requested, "original_wave_required_seconds": round(cost(requested), 3),
            "remaining_seconds": round(remaining_seconds, 3), "fixed_reserve_seconds": fixed,
            "packet_size": 0, "candidate_count": 0, "required_seconds": round(cost(1), 3),
            "shrunk": False}
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
        closeout = CLOSEOUT_RESERVE_SECONDS + (EDITOR_RESERVE_SECONDS if state.get("bounded_editor") and not state.get("editorial_status") else 0)
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
    sources = source_map(workspace)
    pending = []
    full = []
    for index, group in enumerate(state["pool"]):
        keeps = [value for value in state["judgments"].values() if value["group_index"] == index and value["decision"] == "keep"]
        if len(keeps) >= 10:
            full.append(index)
            continue
        identities = {value["identity"] for value in keeps}
        excerpt_hashes = {value.get(key) for value in state["judgments"].values() if value["group_index"] == index for key in ("excerpt_hash", "proposal_excerpt_hash") if value.get(key)}
        needed = 10 - len(keeps)
        for candidate in group["candidates"]:
            candidate_id = digest([state["binding"], index, candidate])[:20]
            if candidate_id in state["judgments"] or candidate_id in state.get("abandoned_candidates", {}) or candidate_id in state.get("length_ineligible_candidates", {}) or candidate["identity"] in identities or (candidate.get("excerpt_hash") and candidate["excerpt_hash"] in excerpt_hashes):
                continue
            record = sources[candidate["source_id"]]
            text = str(record["data"].get(candidate["text_field"]) or "")
            start, end = candidate["start"], candidate["end"]
            excerpt = text[start:end]
            anchor = next((text.find(term) for term in state["terms"] if term in text), -1)
            item = {**candidate, "candidate_id": candidate_id, "group_index": index, "aspect": group["aspect"], "stance": group["stance"], "title": text_field(record["data"], "标题"), "excerpt": excerpt, "context": text[max(0,start-100):min(len(text),end+100)], "target_context": text[max(0,anchor-40):anchor+100] if anchor >= 0 and not any(term in excerpt for term in state["terms"]) else "", "source_channel": record["channel"]}
            if state.get("selection_contract"):
                if candidate.get('selection_options'):
                    item.update(selection_start=start, selection_text='', context='',
                                source_name=source_author(record))
                else:
                    item.update(selection_start=max(0, start-100), selection_text=item["context"])
            item["min_excerpt_chars"] = excerpt_min_chars(record["channel"])
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
            needed -= 1
            if needed == 0:
                break
    state["stopped_full_groups"] = full
    if not pending:
        state["finish_reason"] = "ALL_GROUPS_FULL_OR_UNUSED_CANDIDATES_EXHAUSTED"
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
    plan = plan_wave(len(pending), state.get('max_wave_per_item_seconds', 0),
                     state['started_epoch'] + state['deadline_seconds'] - time.time(),
                     bool(state.get('bounded_editor') and not state.get('editorial_status')))
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
        packet = {"packet_id": packet_id, "target": state["target"], "rules": SPAN_RULE if state.get("selection_contract") else RULE, "answer_file": str(answer), "items": items}
        packet["fingerprint"] = digest(packet)
        template = {"packet_id": packet_id, "fingerprint": packet["fingerprint"], "decisions": [{"candidate_id": item["candidate_id"], "decision": "", "reason": ""} for item in items]}
        if state.get("selection_contract"):
            for row in template["decisions"]:
                row["selected_excerpt"] = ""
        packet["answer_template"] = template
        input_file = directory / f"input-{index+1}.json"
        save(input_file, packet)
        save(answer, template)
        packets.append({**packet, "input_file": str(input_file)})
    state.update(packets=packets, status="FINAL_AI_REVIEW_REQUIRED")
    handoff = {"stage": "FINAL_DISPLAY_CANDIDATES_ONLY", "candidate_count": len(pending), "no_upstream_ai_reviews": True, "jobs": [{"packet_id": packet["packet_id"], "input_file": packet["input_file"], "answer_file": packet["answer_file"], "workbuddy_agent_input": {"description": "最终候选摘录审核", "subagent_type": "general-purpose", "run_in_background": True, "prompt": "完整读取UTF-8文件 " + packet["input_file"] + "，只按包内rules写answer_file。所有条目必须有keep/reject。一次写完，不调用控制器，不审任何其他材料。"}} for packet in packets], "collect_command": f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" collect --workspace "{workspace}"', "native_model_selection": "omit_model_to_inherit_current_host", "closeout": "记录每个Agent启动返回的原生ID；答卷写完不代表任务已结束。finish后用原生TaskOutput确认全部已终止，必要时TaskStop只结束自己本期剩余Agent，不启动新审查。不要只勾选Todo完成。"}
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
    if state.get("bounded_editor") and not state.get("editorial_status"):
        from display_editor import prepare_editor
        editorial = prepare_editor(workspace, state, digest)
        if editorial:
            return {**status(workspace,state), **editorial,
                    "next_command": f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" editor-collect --workspace "{workspace}"'}
    if state.get("editorial_status") == "PENDING":
        state["editorial_status"] = "SKIPPED_EXPLICIT_FINISH"
    sources = source_map(workspace)
    dataset = load(workspace / "dataset.json")
    groups = []
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
                raise ValueError(f"{record['id']}: 明确AI稿不得发布，必须在来源预处理阶段排除")
            text = str(record["data"].get(item["text_field"]) or "")
            length_error = excerpt_length_error(item["excerpt"], record.get("channel"))
            if length_error:
                raise ValueError(f"{item.get('candidate_id')}: {length_error}")
            assert text[item["start"]:item["end"]] == item["excerpt"]
            samples.append({**item, "source": record, "source_text": text, "ai_decision": "keep",
                            "display_excerpt": display_excerpt(item["excerpt"])})
        if samples:
            title = state.get("editorial_titles", {}).get(str(index),group["aspect"])
            groups.append({"aspect": title, "stance": group["stance"], "candidate_count": len(group["candidates"]), "samples": samples})
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
    if state.get("ai_viewpoint_discovery"):
        state.update(min_positive_clusters=10, positive_cluster_count=positive_count,
                     positive_cluster_shortfall=positive_count < 10)
        if positive_count < 10:
            dataset["notice"] += f" 正面观点不足：实际{positive_count}组，要求至少10组；当前材料未达数量要求。"
    save(workspace / "final_dataset.json", dataset)
    (workspace / "workbench.html").write_text(render(dataset), encoding="utf-8")
    within = time.time() < state["started_epoch"] + state["deadline_seconds"]
    state["status"] = "FINAL_SELECTED_COMPLETE" if groups and within else "TIME_LIMIT_REACHED" if not within else "NO_ACCEPTED_SAMPLES"
    if within and groups and state.get("ai_viewpoint_discovery") and not 10 <= len(groups) <= 16:
        state["status"] = "VIEWPOINT_RANGE_NOT_MET"
    elif within and groups and state.get("ai_viewpoint_discovery") and positive_count < 10:
        state["status"] = "POSITIVE_VIEWPOINT_MIN_NOT_MET"
    result = status(workspace, state)
    if state.get("ai_viewpoint_discovery"):
        result.update(min_positive_clusters=10, positive_cluster_count=positive_count,
                      positive_cluster_shortfall=positive_count < 10)
    result.update(output=str(workspace / "workbench.html"), group_count=len(groups), structure_verification="PASS", semantic_verification="FINAL_SELECTED_ONLY", finish_reason=state.get("finish_reason", "EXPLICIT_FINISH_WITH_ALREADY_ACCEPTED_SAMPLES"), completed_within_30_minutes=within and state["status"] == "FINAL_SELECTED_COMPLETE", groups_below_ten=[{"aspect": group["aspect"], "stance": group["stance"], "count": len(group["samples"])} for group in groups if len(group["samples"]) < 10])
    result["editorial_reviewed_samples"] = len(state.get("editorial_audit", []))
    result["editorial_elapsed_seconds"] = state.get("editorial_elapsed_seconds")
    result["closeout_instruction"] = "立即核对本期native_worker_registry.json。只对本主窗口本期真实登记且未确认终止的task_id调用TaskStop，停止尚在执行的包；再向本期成员shutdown_request并确认结束。已超时或已发布后不要继续等待其交卷，不重开编辑，不处理其他团队。HTML已生成不代表主窗口已正常退出。"
    result['closeout_instruction'] += ' 已收到completed/TaskStop已结束的任务不要再逐个查询或睡眠等重复确认。确认本期没有运行任务后，仅清理登记的本会话自动团队（TeamDelete），及时结束主会话；不操作其他会话团队。最终回复只报实际状态、数量和路径，不重写一篇过程报告。'
    save(workspace / "final_review_status.json", result)
    return result


def collect(workspace, state):
    if state["status"] != "FINAL_AI_REVIEW_REQUIRED":
        return status(workspace, state)
    if digest(load(workspace / state.get("pool_file", "candidate_pool.json"))) != state["pool_hash"] or digest(load(workspace / "raw_extraction/manifest.json")) != state["raw_hash"]:
        raise ValueError("本期原始提取或候选池变更，拒绝旧审查答卷")
    errors = []
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
                if row.get("decision") not in ("keep", "reject") or (row.get("decision") == "reject" and not str(row.get("reason") or "").strip()):
                    errors.append({"answer_file": packet["answer_file"], "candidate_id": candidate_id, "error": "填keep或reject；reject须短原因"})
                    continue
                item = expected[candidate_id]
                selected = dict(item)
                if row["decision"] == "keep" and state.get("selection_contract"):
                    quote = row.get("selected_excerpt")
                    resolved = resolve_selection(item, quote)
                    if resolved is None or not str(row.get("reason") or "").strip():
                        errors.append({"answer_file": packet["answer_file"], "candidate_id": candidate_id, "error": "keep须selected_excerpt为一个selection_options片段（无选项时selection_text）中连续逐字原句（短评50—360字，文章70—360字），不跨段拼接，并给短reason；只修此项，不重审已通过项"})
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
                        errors.append({"answer_file": packet["answer_file"], "candidate_id": candidate_id, "error": length_error})
                        continue
                state["judgments"][candidate_id] = {**selected, "decision": row["decision"], "reason": str(row.get("reason") or "")}
            for candidate_id in expected.keys() - seen:
                if candidate_id not in state["judgments"]:
                    errors.append({"answer_file": packet["answer_file"], "candidate_id": candidate_id, "error": "漏答"})
        except (ValueError, OSError) as exc:
            errors.append({"answer_file": packet["answer_file"], "error": str(exc)})
    save(workspace / "final_ai_feedback.json", {"errors": errors, "transport_normalizations": state.get("transport_normalizations", []), "instruction": "只修列出的缺答/格式错误，保留已完成判断；不要重审keep。"})
    if len(state["judgments"]) > previous_count:
        state["last_progress_epoch"] = time.time()
    if errors:
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
                if load(Path(packet["answer_file"])) == packet.get("answer_template"):
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
            result["waiting_for_original_writers"] = True
            result["repeat_command"] = f'"{sys.executable}" -X utf8 "{Path(__file__).resolve()}" await-collect --workspace "{workspace}"'
            result["instruction"] = "仅等待原写者，直接执行repeat_command；不另睡眠、不查文件列表、不重复派发、不审已通过项。"
            return result
        time.sleep(min(2, remaining))


def prepare(args):
    started = time.time()
    argv = [sys.executable, "-X", "utf8", str(Path(__file__).with_name("rapid_film_workbench.py")), "--source", str(args.source), "--workspace", str(args.workspace), "--target", args.target, "--per-group", "10", "--deadline-seconds", str(args.deadline_seconds)]
    for alias in args.alias:
        argv.extend(["--alias", alias])
    result = subprocess.run(argv, capture_output=True, text=True, encoding="utf-8", timeout=args.deadline_seconds+5)
    if result.returncode:
        raise RuntimeError(result.stdout + result.stderr)
    workspace = args.workspace.resolve()
    (workspace / "workbench.html").rename(workspace / "script_proposal.html")
    pool = load(workspace / "candidate_pool.json")
    sources = source_map(workspace) if args.discover_viewpoints else None
    if args.discover_viewpoints:
        from social_criticism_probe import social_criticism_probe_group
        probe = social_criticism_probe_group(sources, [args.target, *args.alias])
        if probe:
            pool.insert(0, probe)
            save(workspace / "candidate_pool.json", pool)
    raw_hash = digest(load(workspace / "raw_extraction/manifest.json"))
    state = {"status": "PREPARING_FINAL_AI", "started_at": dt.datetime.now().astimezone().isoformat(), "started_epoch": started, "deadline_seconds": args.deadline_seconds, "target": args.target, "terms": [args.target,*args.alias], "pool": pool, "pool_hash": digest(pool), "raw_hash": raw_hash, "binding": digest([raw_hash,pool,args.target,args.alias,RULE]), "round": 0, "packets": [], "judgments": {}}
    if args.discover_viewpoints:
        state.update(selection_contract="VERBATIM_DISPLAY_SPAN_V1", fresh_wave_workers=True, bounded_editor=True)
        state["binding"] = digest([state["binding"], SPAN_RULE, state["selection_contract"]])
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
    init.add_argument("--deadline-seconds", type=float, default=1800)
    init.add_argument("--discover-viewpoints", action="store_true")
    for command in ("collect", "await-collect", "finish", "discover-collect", "editor-collect", "recover-stalled"):
        commands.add_parser(command).add_argument("--workspace", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        if not 0 < args.deadline_seconds <= 1800:
            raise ValueError("总预算必须为(0,1800]秒")
        result = prepare(args)
    else:
        workspace = args.workspace.resolve()
        state = load(workspace / "final_review_state.json")
        if args.command == "recover-stalled":
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
            # The model may finish writing just before the editor budget while
            # this collector wakes a few seconds later.  Once a non-empty,
            # fingerprint-matching answer exists, apply it during the remaining
            # overall budget; only the hard closeout reserve may skip it.
            if remaining <= CLOSEOUT_RESERVE_SECONDS:
                state["editorial_status"] = "SKIPPED_EDITOR_TIME_LIMIT"
                result = finish(workspace,state)
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
                    save(workspace/'display_editor_feedback.json',{"error":str(exc),"instruction":"仅修编辑答卷字段，不重审其他材料；保持总截止和180秒收尾"})
                    result = {**status(workspace,state),"read_now":str(workspace/'display_editor_feedback.json')}
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
