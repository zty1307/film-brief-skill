from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
PIPELINE_PATH = REPO_ROOT / "scripts" / "film_pipeline.py"
WORKFLOW_PATH = REPO_ROOT / "scripts" / "film_workflow.py"


def load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


p = load("film_pipeline_regression", PIPELINE_PATH)
w = load("film_workflow_regression", WORKFLOW_PATH)


# Date-only export names are common and should not require period_windows.
start, end = p.filename_period("测试剧3-2025.09.24至2025.09.26-微博最热样本.xlsx")
assert start.isoformat() == "2025-09-24T00:00:00"
assert end.isoformat() == "2025-09-26T23:59:59"

# A date-only configured end includes that entire calendar day.  Previously it
# became 00:00 and silently discarded almost every record on the report date.
period_config = {
    "period_windows": {"测试剧": {"start": "2025-09-09", "end": "2025-09-11"}},
}
assert p.period_state(
    {"batch": "测试剧", "published": "2025-09-11 20:15:00", "source_file": ""},
    period_config,
)[0] == "in_period"
assert p.period_state(
    {"batch": "测试剧", "published": "2025-09-12 00:00:00", "source_file": ""},
    period_config,
)[0] == "out_of_period"


# Anchor terms supplied after full-text review must control passage selection.
row = {
    "id": "window-1",
    "title": "《测试剧》主创信息",
    "body": "《测试剧》公布了主创名单。后半段对手戏层层递进，演员只用眼神和停顿就把人物的戒备与信任演得细腻自然。",
    "post_type": "原帖",
}
definition = {
    "id": "P01", "title": "肯定演员细腻自然的表演",
    "stance": "positive", "keywords": [["演员", 8], ["细腻自然", 8]],
}
window = p.best_window(
    row, definition, ["眼神", "停顿", "细腻自然"],
    {"strong_terms": ["测试剧"], "weak_terms": [], "auxiliary_terms": [], "comparison_terms": []},
)
assert "眼神和停顿" in window["text"] and window["anchor_hits"]

# required_any is only a routing vocabulary. A verbatim reviewed anchor may
# express the same aspect with different wording, while an unreviewed miss must
# still fail the preliminary gate.
semantic_definition = {
    "id": "P02", "title": "肯定场景营造出的武侠质感",
    "stance": "positive", "required_any": ["古早武侠", "武侠味"],
}
semantic_text = "《测试剧》把黄沙、旧城与人物的风霜感拍得很落地。"
semantic_window = {
    "start": 0, "end": len(semantic_text), "text": semantic_text,
    "anchor_hits": ["风霜感拍得很落地"],
}
semantic_row = {"id": "semantic", "title": "《测试剧》", "body": semantic_text, "decision": "retain_consensus"}
semantic_target = {"strong_terms": ["《测试剧》"], "weak_terms": [], "auxiliary_terms": [], "comparison_terms": []}
alignment = p.passage_alignment(
    semantic_row, semantic_window, semantic_definition, semantic_target,
    {"anchor_terms": ["风霜感拍得很落地"], "passage_stance": "positive"},
)
assert alignment["aspect"]["passed"] and alignment["aspect"]["basis"] == "ai_reviewed_anchor"
assert not p.passage_alignment(
    semantic_row, {**semantic_window, "anchor_hits": []}, semantic_definition, semantic_target, {}
)["aspect"]["passed"]


# A coherent viewpoint may use two non-adjacent verbatim spans before the
# cluster-set gate; intervening cross-work text must not enter the passage.
two_span_body = (
    "《测试剧》上线前几乎没有常规宣发，作者认为作品质量比铺量更重要。"
    "另一部《对比剧》依靠密集热搜制造声量，但这里不评价目标剧。"
    "《测试剧》的强剧情和人物冲突，才是最吸引普通观众继续关注的地方。"
)
two_span_row = {"id": "two-span", "title": "", "body": two_span_body, "post_type": "原帖"}
first_fragment = "《测试剧》上线前几乎没有常规宣发，作者认为作品质量比铺量更重要。"
second_fragment = "《测试剧》的强剧情和人物冲突，才是最吸引普通观众继续关注的地方。"
first_start = two_span_body.index(first_fragment)
second_start = two_span_body.index(second_fragment)
two_span_override = {
    "cluster": "P01", "reason": "两段共同说明作品质量和强剧情形成路人吸引力",
    "passage_fragments": [first_fragment, second_fragment],
    "passage_positions": [
        [first_start, first_start + len(first_fragment)],
        [second_start, second_start + len(second_fragment)],
    ],
    "passage_stance": "positive", "target_evidence": "《测试剧》",
}
two_span_window = p.reviewed_window(
    two_span_row, definition, two_span_override,
    {"strong_terms": ["测试剧"], "weak_terms": [], "auxiliary_terms": [], "comparison_terms": ["对比剧"]},
)
assert two_span_window["reviewed_fragments"] is True
assert "对比剧" not in two_span_window["text"]
assert p.excerpt_candidates(two_span_body, two_span_window, definition)[0]["fragments"] == [first_fragment, second_fragment]

# Models may omit character offsets; the script locates exact fragments in
# source order. Explicit positions remain available when repeated text needs
# disambiguation.
auto_position_override = {key: value for key, value in two_span_override.items() if key != "passage_positions"}
auto_position_window = p.reviewed_window(
    two_span_row, definition, auto_position_override,
    {"strong_terms": ["测试剧"], "weak_terms": [], "auxiliary_terms": [], "comparison_terms": ["对比剧"]},
)
assert auto_position_window["positions"] == two_span_override["passage_positions"]


# Wrong-work character sets and abusive text route to AI review, never auto-pass silently.
target = {
    "content_mode": "serial_drama", "strong_terms": ["测试剧"], "weak_terms": [],
    "auxiliary_terms": ["许妍", "沈皓明"], "comparison_terms": [],
}
bad_row = {
    "id": "wrong-work", "batch": "测试", "channel": "微博", "post_type": "原帖",
    "title": "", "body": "测试剧难看的原因很多。宁夕的角色设定失真，男主角陆霆骁与江牧野都围着她转，完全是另一套故事。文中继续围绕这些人物的职业、感情和成长展开了很长篇幅，却没有出现目标作品配置中的任何角色，也没有给出能够核对目标作品的具体情节。",
    "asr": "", "parent_body": "", "author": "a", "url": "", "published": "2026-01-01",
}
decision = p.evaluate(bad_row, target, {"quality_floor": {"social": 9.5, "long": 12.5}})
assert decision["entity_consistency_risk"] is True and decision["needs_ai_review"] is True
attack_row = {**bad_row, "id": "attack", "body": "测试剧这个话题下有人只顾人身攻击，骂别人是走狗和狗东西，没有讨论剧情。"}
attack = p.evaluate(attack_row, target, {"quality_floor": {"social": 9.5, "long": 12.5}})
assert attack["abusive_content_review_risk"] is True and attack["needs_ai_review"] is True


# The source-review preflight returns all failures at once, including exclude evidence.
sources = {"a": {**bad_row, "id": "a"}, "b": {**bad_row, "id": "b"}}
queue = [{"id": "a"}, {"id": "b"}]
reviews = {"a": {"source_id": "a", "decision": "exclude", "reason": "", "evidence": "不存在的文字"}}
issues = p.source_review_validation_issues(
    sources, queue, reviews,
    {"targets": {"测试": {"content_mode": "serial_drama"}}},
)
assert {item["issue"] for item in issues} >= {"missing_review", "missing_reason", "invalid_evidence"}
assert {item["source_id"] for item in issues} == {"a", "b"}

# Ordinary models can select a generated evidence candidate by index; the
# pipeline resolves the exact source substring and character offsets.
candidate_source = p.source_text(sources["a"])
candidate_start = candidate_source.index("测试剧难看")
candidate_end = candidate_start + len("测试剧难看的原因很多。")
candidate_queue = [{
    "id": "a",
    "review_evidence_candidates": [{
        "candidate_index": 1,
        "start": candidate_start,
        "end": candidate_end,
        "text": candidate_source[candidate_start:candidate_end],
    }],
}]
candidate_review = {
    "source_id": "a", "decision": "exclude", "reason": "正文评价的是另一套人物故事",
    "evidence_candidate_index": 1,
}
assert not p.source_review_validation_issues(
    {"a": sources["a"]}, candidate_queue, {"a": candidate_review},
    {"targets": {"测试": {"content_mode": "serial_drama"}}},
)
resolved, resolved_position = p.resolve_source_review_evidence(
    candidate_review, sources["a"], candidate_queue[0]
)
assert resolved == "测试剧难看的原因很多。" and resolved_position == [candidate_start, candidate_end]

# Initial clustering receives one compact verbatim passage per retained source,
# while the full body remains available only by source-id lookup.
discovery_row = {**sources["a"], "decision": "retain_consensus", "quality": 8.0, "stance": "负向"}
discovery = p.cluster_discovery_record(discovery_row, {
    "review_evidence_candidates": candidate_queue[0]["review_evidence_candidates"],
})
assert len(discovery["discovery_passages"]) == 1 and "body" not in discovery
discovery_passage = discovery["discovery_passages"][0]
assert p.source_text(discovery_row)[discovery_passage["start"]:discovery_passage["end"]] == discovery_passage["text"]


# Post-excerpt count review uses the actual display clusters and has one clear contract.
display_clusters = [{
    "id": "P01", "title": "肯定演员细腻自然的表演", "stance": "positive",
    "items": [{"viewId": "s1::P01"}],
}]
count_issues, count_audit = p.post_excerpt_count_review_issues("测试", display_clusters, None)
assert count_issues == ["missing_review"]
valid_count_review = {
    "fingerprint": count_audit["fingerprint"], "decision": "pass", "cluster_count": 1,
    "range_status": "below_range", "reader_load_reviewed": True,
    "no_forced_merge_or_split": True,
    "reason": "终审后仅剩一个真实观点，其他簇均无合格展示证据",
    "exception_approved": True,
    "exception_reason": "强行补到九个簇会制造当前样本中不存在的观点",
}
count_issues, _ = p.post_excerpt_count_review_issues("测试", display_clusters, valid_count_review)
assert not count_issues


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    input_path = root / "input.jsonl"
    input_path.write_text("x", encoding="utf-8")
    binding = w.binding("wf", [input_path])
    ledger = {
        "scope": "current_period_source_cluster_reviews", "_workflow": binding,
        "overrides": {"old": {"cluster": "P01", "reason": "earlier round"}},
    }
    ledger_path = root / "ledger.json"
    ledger_path.write_text(json.dumps(ledger, ensure_ascii=False), encoding="utf-8")
    current = {
        "scope": "current_period_source_cluster_reviews", "_workflow": binding,
        "overrides": {
            "new": {"cluster": "P02", "reason": "current round"},
            "old": {"cluster": "P03", "reason": "current correction"},
        },
    }
    merged = w.cumulative_override_payload("wf", [input_path], current, ledger_path)
    assert set(merged["overrides"]) == {"old", "new"}
    assert merged["overrides"]["old"]["cluster"] == "P03"

    # Pretty-printed business JSON must survive; an actual program error still raises useful text.
    child = root / "business_stop.py"
    child.write_text(
        "import json\nprint(json.dumps({'status':'REVIEW_REQUIRED','failures':[{'view_id':'v1','issue':'bad quote'}]}, ensure_ascii=False, indent=2))\nraise SystemExit(1)\n",
        encoding="utf-8",
    )
    result = w.run_command([sys.executable, str(child)], allow_review_stop=True)
    assert result["business_payload"]["failures"][0]["view_id"] == "v1"
    try:
        w.run_command([sys.executable, str(child)])
    except RuntimeError as exc:
        assert "REVIEW_REQUIRED" in str(exc) and "v1" in str(exc) and str(exc) != "}"
    else:
        raise AssertionError("business failure was silently accepted")

    # A retained-but-nondisplayable source has a formal, audited cluster exit.
    run_dir = root / "cluster_run"
    run_dir.mkdir()
    retained = {
        **bad_row,
        "id": "excluded-at-cluster", "decision": "retain_consensus", "quality": 8.0,
        "stance": "混合或中性", "fact_warning": "", "media_authority_rank": 0,
        "media_authority_tier": "unclassified",
    }
    (run_dir / "retained_sources.jsonl").write_text(
        json.dumps(retained, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (run_dir / "period_config.json").write_text(json.dumps({
        "batch_order": ["测试"], "targets": {"测试": target},
    }, ensure_ascii=False), encoding="utf-8")
    clusters = root / "clusters.json"
    clusters.write_text(json.dumps({
        "scope": "current_period_data_derived_clusters",
        "batches": {"测试": [{
            "id": "P01", "title": "肯定演员细腻自然的表演，认为人物关系更加真实可信",
            "stance": "positive", "keywords": [["表演", 8]],
        }]},
    }, ensure_ascii=False), encoding="utf-8")
    cluster_payload = json.loads(clusters.read_text(encoding="utf-8"))
    invalid_override_payload = {"overrides": {
        "excluded-at-cluster": {
            "cluster": "__exclude__", "exclude_reason_code": "cross_work_mismatch",
            "reason": "角色集合属于另一作品，不能作为目标剧评价展示",
            "exclusion_evidence": "并不存在于来源中的概括文字",
        },
        "missing-source": {"cluster": "P01", "reason": "错误来源"},
    }}
    validation = w.cluster_override_validation_issues([retained], cluster_payload, invalid_override_payload)
    assert {item["issue"] for item in validation} == {"exclusion_evidence_not_verbatim", "unknown_source_id"}

    overrides = root / "overrides.json"
    overrides.write_text(json.dumps({
        "scope": "current_period_source_cluster_reviews",
        "overrides": {"excluded-at-cluster": {
            "cluster": "__exclude__", "exclude_reason_code": "cross_work_mismatch",
            "reason": "角色集合属于另一作品，不能作为目标剧评价展示",
            "evidence": "宁夕的角色设定失真",
        }},
    }, ensure_ascii=False), encoding="utf-8")
    assert not w.cluster_override_validation_issues(
        [retained], cluster_payload, json.loads(overrides.read_text(encoding="utf-8"))
    )

    retained_two_span = {
        **retained, "id": "two-span", "title": "", "body": two_span_body,
    }
    valid_two_span_payload = {"overrides": {"two-span": two_span_override}}
    assert not w.cluster_override_validation_issues(
        [retained_two_span], cluster_payload, valid_two_span_payload
    )
    auto_position_payload = {"overrides": {"two-span": auto_position_override}}
    assert not w.cluster_override_validation_issues(
        [retained_two_span], cluster_payload, auto_position_payload
    )
    invalid_two_span_payload = json.loads(json.dumps(valid_two_span_payload, ensure_ascii=False))
    invalid_two_span_payload["overrides"]["two-span"]["passage_positions"][1][0] -= 1
    fragment_issues = w.cluster_override_validation_issues(
        [retained_two_span], cluster_payload, invalid_two_span_payload
    )
    assert any(item["issue"] == "passage_fragment_not_verbatim_at_position" for item in fragment_issues)
    completed = subprocess.run(
        [sys.executable, str(PIPELINE_PATH), "cluster", "--run", str(run_dir),
         "--clusters", str(clusters), "--overrides", str(overrides)],
        text=True, encoding="utf-8", errors="replace", capture_output=True,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout
    exclusions = [json.loads(line) for line in (run_dir / "cluster_exclusions.jsonl").read_text(encoding="utf-8").splitlines()]
    assert exclusions[0]["source_id"] == "excluded-at-cluster"
    assert not (run_dir / "cluster_review_queue.jsonl").read_text(encoding="utf-8").strip()


# Person names may help locate a source but cannot dominate viewpoint assignment.
target_config = {
    "strong_terms": ["《赴山海》", "赴山海"],
    "weak_terms": [],
    "auxiliary_terms": ["成毅"],
    "comparison_terms": [],
}
acting_cluster = {
    "id": "P03", "title": "期待成毅一人分饰三角的演技挑战，认为角色展现表演层次感",
    "stance": "positive", "keywords": [["成毅", 10], ["演技", 8], ["分饰", 4]],
    "required_any": [],
}
music_cluster = {
    "id": "P07", "title": "肯定OST阵容与歌曲质量，认为音乐提升观剧沉浸感",
    "stance": "positive", "keywords": [["OST", 4], ["音乐", 3]],
    "required_any": [],
}
assert p.effective_required_terms(acting_cluster, target_config) == ["演技", "分饰"]
music_text = "《赴山海》OST阵容很豪华，成毅演唱人物曲，周深演唱片尾曲，音乐很有江湖意境。"
acting_score, _ = p.cluster_score(music_text, "", acting_cluster, target_config)
music_score, _ = p.cluster_score(music_text, "", music_cluster, target_config)
assert music_score > acting_score

# An override target evidence span must itself contain a configured work anchor.
target_row = {
    **retained, "id": "target-check", "batch": "测试",
    "title": "几部新剧集中开播", "body": "先介绍其他作品。随后《赴山海》登场，打戏利落。",
}
target_issue_payload = {"overrides": {"target-check": {
    "cluster": "P01", "reason": "尝试确认目标",
    "target_evidence": "先介绍其他作品。",
}}}
target_issues = w.cluster_override_validation_issues(
    [target_row], cluster_payload, target_issue_payload,
    {"targets": {"测试": {"strong_terms": ["《赴山海》", "赴山海"], "weak_terms": []}}},
)
assert any(item["issue"] == "target_evidence_missing_target_anchor" for item in target_issues)

# Ordinary-model templates are directly fillable but blank shells do not count as completed reviews.
set_template = w.cluster_set_review_template_payload(
    "wf", [],
    {"clusters": [{"review_key": "测试\tP01", "fingerprint": "abc", "stance": "positive"}]},
    {"batches": [{"review_key": "测试", "fingerprint": "def", "cluster_count": 1, "expected_range_status": "below_range"}]},
)
assert "测试\tP01" in set_template["reviews"]
assert not w.cluster_set_review_is_filled(set_template["reviews"]["测试\tP01"])
excerpt_template = w.final_excerpt_review_template_payload(
    "wf", [], [{"view_id": "source::P01"}]
)
assert "source::P01" in excerpt_template["reviews"]
blank_excerpt_review = excerpt_template["reviews"]["source::P01"]
assert not w.final_excerpt_review_is_filled(blank_excerpt_review)
blank_excerpt_review.update({"decision": "keep", "reason": "已查看"})
assert not w.final_excerpt_review_is_filled(blank_excerpt_review), "只有决定和理由不得跳过必填语义字段"
blank_excerpt_review.update({
    "aspect_evidence": "表演自然",
    "stance": "positive", "stance_evidence": "表演自然",
    "self_contained": True,
})
assert w.final_excerpt_review_is_filled(blank_excerpt_review)

conditional_template = w.final_excerpt_review_template_payload(
    "wf", [], [{
        "view_id": "conditional::P01",
        "target_review_required": True,
        "work_consistency_review_required": True,
    }]
)
conditional_input = {
    "view_id": "conditional::P01",
    "target_review_required": True,
    "work_consistency_review_required": True,
}
conditional_review = conditional_template["reviews"]["conditional::P01"]
conditional_review.update({
    "decision": "keep", "reason": "已核对条件项",
    "aspect_evidence": "表演自然", "stance": "positive",
    "stance_evidence": "表演自然", "self_contained": True,
})
assert not w.final_excerpt_review_is_filled(conditional_review, conditional_input)
conditional_review.update({
    "target_passed": True, "target_evidence": "《测试剧》",
    "work_consistency_passed": True, "work_consistency_evidence": "《测试剧》",
})
assert w.final_excerpt_review_is_filled(conditional_review, conditional_input)

# Weak-model chunk submissions are accumulated by the controller, so a model
# never has to rewrite prior answers or the full review file.
ledger_root = Path(tempfile.mkdtemp(prefix="film-ledger-test-"))
ledger_input = ledger_root / "ledger-input.jsonl"
ledger_input.write_text('{"id":"s1"}\n{"id":"s2"}\n', encoding="utf-8")
ledger_path = ledger_root / "source-ledger.json"
first_submission = {
    "scope": "current_period_source_fulltext_reviews",
    "_workflow": w.binding("wf-ledger", [ledger_input]),
    "reviews": [{
        "source_id": "s1", "decision": "retain_core", "reason": "第一片有效",
        "evidence_candidate_index": 1,
    }],
}
first_ledger = w.cumulative_review_payload(
    "wf-ledger", [ledger_input], first_submission, ledger_path,
    "source_id", "current_period_source_fulltext_reviews",
)
w.write_json(ledger_path, first_ledger)
second_submission = {
    "scope": "current_period_source_fulltext_reviews",
    "_workflow": w.binding("wf-ledger", [ledger_input]),
    "reviews": [{
        "source_id": "s2", "decision": "retain_consensus", "reason": "第二片有效",
        "evidence_candidate_index": 1,
    }],
}
second_ledger = w.cumulative_review_payload(
    "wf-ledger", [ledger_input], second_submission, ledger_path,
    "source_id", "current_period_source_fulltext_reviews",
)
assert set(w.list_review_map(second_ledger, "source_id")) == {"s1", "s2"}
chunk_one = ledger_root / "chunk-001.jsonl"
chunk_two = ledger_root / "chunk-002.jsonl"
chunk_one.write_text('{"id":"s1"}\n', encoding="utf-8")
chunk_two.write_text('{"id":"s2"}\n', encoding="utf-8")
next_chunk, chunk_index, chunk_total = w.next_incomplete_chunk(
    [chunk_one, chunk_two], "id", {"s2"}
)
assert next_chunk == chunk_two and chunk_index == 2 and chunk_total == 2
override_submission = w.cluster_override_submission_template(
    {
        "scope": "current_period_source_cluster_reviews",
        "_workflow": w.binding("wf-ledger", [ledger_input]),
        "contract": {"cumulative_file": True},
        "overrides": {"old": {"cluster": "P01", "reason": "历史记录"}},
    },
    [{"source_id": "new", "provisional_cluster": "P02"}],
)
assert set(override_submission["overrides"]) == {"new"}
assert override_submission["contract"]["cumulative_file"] is False
assert override_submission["contract"]["script_owned_cumulative_ledger"] is True

malformed_review = ledger_root / "malformed-review.json"
malformed_review.write_text('{"reviews": [', encoding="utf-8")
malformed_payload = w.review_payload(malformed_review)
assert w.binding_issue(malformed_payload, "wf-ledger", [ledger_input]).startswith("invalid_review_file:")


print("PASS: issue-report regressions")
