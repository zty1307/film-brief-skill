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


print("PASS: issue-report regressions")
