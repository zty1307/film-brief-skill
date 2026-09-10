from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

from openpyxl import Workbook


PYTHON = Path(sys.executable)
REPO_ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = REPO_ROOT / "scripts" / "film_workflow.py"
ROOT = Path(tempfile.gettempdir()) / f"film_workflow_integration_{uuid.uuid4().hex[:8]}"
SOURCE = ROOT / "raw" / "电视剧《测试剧》"
WORKSPACE = ROOT / "workspace"
OUTPUT = ROOT / "deliverables" / "影视样本清洗工作台.html"
CONFIG = ROOT / "period_config.json"


def load(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def dump(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def run(*args: str) -> dict:
    completed = subprocess.run(
        [str(PYTHON), str(CONTROLLER), *args],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if completed.returncode:
        raise AssertionError(completed.stderr or completed.stdout)
    return load(WORKSPACE / "workflow_status.json")


SOURCE.mkdir(parents=True)
workbook = Workbook()
sheet = workbook.active
sheet.title = "境内新闻"
sheet.append(["标题", "正文内容", "发表时间", "用户名", "发文链接"])
sheet.append([
    "《测试剧》首播获赞",
    "《测试剧》首播后，演员在克制的眼神和停顿中呈现人物从戒备到信任的变化，表演细腻自然，人物关系也显得真实可信。几场对手戏没有依赖夸张台词，而是通过动作和语气逐步推进情绪，让观众能够理解角色选择及其后果。",
    "2026-09-04 10:00:00",
    "人民网",
    "",
])
workbook.save(SOURCE / "测试剧_境内新闻.xlsx")

dump(CONFIG, {
    "batch_order": ["电视剧《测试剧》"],
    "targets": {
        "电视剧《测试剧》": {
            "content_mode": "serial_drama",
            "strong_terms": ["测试剧"],
            "weak_terms": [],
            "auxiliary_terms": [],
            "comparison_terms": [],
        }
    },
    "quality_floor": {"long": 12.5, "social": 9.5},
    "media_subject_extensions": [],
    "background_cluster_ids": [],
    "assume_all_in_period": True,
})

doctor = subprocess.run(
    [str(PYTHON), str(CONTROLLER), "doctor", "--source", str(ROOT / "raw"), "--period-config", str(CONFIG), "--workspace", str(WORKSPACE), "--output", str(OUTPUT)],
    text=True, encoding="utf-8", errors="replace", capture_output=True,
)
assert doctor.returncode == 0, doctor.stderr or doctor.stdout
assert json.loads(doctor.stdout)["status"] == "PASS"

state = run(
    "init", "--source", str(ROOT / "raw"), "--period-config", str(CONFIG),
    "--workspace", str(WORKSPACE), "--output", str(OUTPUT), "--timeout", "0.2",
)

for _ in range(30):
    stage = state["stage"]
    if state["status"] == "COMPLETE":
        break
    if state["status"] == "READY_TO_ADVANCE":
        if stage == "verify":
            assert not OUTPUT.exists(), "正式 HTML 只能在 verify PASS 后发布"
        state = run("advance", "--workspace", str(WORKSPACE), "--timeout", "0.2")
        continue
    assert state["status"] == "REVIEW_REQUIRED" and state.get("review_requirements"), state
    assert state.get("edit_file_ready") is True, state
    assert Path(state["required_file"]).exists(), state
    assert "template" not in state, state
    operator = state.get("operator_contract") or {}
    assert operator.get("mode") == "controller_prepared_edit_in_place", state
    assert operator.get("write_only") == state["required_file"], state
    assert any("临时驱动脚本" in value for value in operator.get("forbidden_in_normal_flow", [])), state
    if stage == "source_review":
        assert state.get("input_file") and state.get("full_input_file")
        assert state.get("full_source_file", "").endswith("normalized_sources.jsonl")
        assert state.get("chunk_index") == 1 and state.get("chunk_total") >= 1
        template = load(Path(state["required_file"]))
        queue = [json.loads(line) for line in Path(state["input_file"]).read_text(encoding="utf-8").splitlines() if line.strip()]
        assert queue and "evidence_source_text" not in queue[0]
        assert "full_source_lookup" not in queue[0] and "review_evidence_candidates" in queue[0]
        assert "stance" not in queue[0]
        template["reviews"] = []
        for item in queue:
            candidate = item["review_evidence_candidates"][0]
            template["reviews"].append({
                "source_id": item["id"], "decision": "retain_core",
                "reason": "原文明确评价目标剧集的表演及人物关系，并给出眼神、停顿和动作等细节",
                "evidence_candidate_index": candidate["candidate_index"],
            })
        dump(Path(state["required_file"]), template)
    elif stage == "cluster_discovery":
        assert state.get("input_files") and state.get("full_input_file")
        discovery_rows = [json.loads(line) for line in Path(state["input_file"]).read_text(encoding="utf-8").splitlines() if line.strip()]
        assert discovery_rows and "passage" in discovery_rows[0]
        assert "body" not in discovery_rows[0] and "quality" not in discovery_rows[0]
        assert len(state["input_files"]) == 1
        assert state.get("full_source_file")
        template = load(Path(state["required_file"]))
        template["batches"]["电视剧《测试剧》"] = [{
            "id": "P01",
            "title": "肯定演员细腻自然的表演，认为人物关系与情绪变化真实可信",
            "stance": "positive",
            "summary": "围绕表演细节、人物情绪和关系可信度的正面评价",
            "keywords": [["表演", 8], ["细腻", 7], ["自然", 6], ["人物关系", 7], ["眼神", 5], ["停顿", 5], ["动作", 4], ["语气", 4]],
            "required_any": ["表演", "人物"],
            "negative_cues": [],
            "background": False,
            "rare_signal": False,
        }]
        dump(Path(state["required_file"]), template)
    elif stage == "cluster_assignment_review":
        template = load(Path(state["required_file"]))
        queue = [json.loads(line) for line in Path(state["input_file"]).read_text(encoding="utf-8").splitlines() if line.strip()]
        template["overrides"] = {
            item["source_id"]: {
                "cluster": "P01", "reason": "完整片段直接支持演员表演及人物关系评价",
                "anchor_terms": ["表演细腻自然"], "passage_stance": "positive", "target_evidence": "《测试剧》",
            }
            for item in queue
        }
        dump(Path(state["required_file"]), template)
    elif stage == "cluster_set_review":
        template = load(Path(state["required_file"]))
        set_input = load(Path(state["input_files"][0]))
        count_input = load(Path(state["input_files"][1]))
        template["reviews"] = {}
        for item in set_input["clusters"]:
            ids = [sample["source_id"] for sample in item["samples"]]
            passage = item["samples"][0]["passage"]
            template["reviews"][item["review_key"]] = {
                "fingerprint": item["fingerprint"], "decision": "pass", "report_role": "report_point",
                "scope_type": "current_broadcast_reaction", "issues": [],
                "reason": "标题两项主张均有当前分配片段中的直接证据",
            }
            assert "表演细腻自然，人物关系也显得真实可信" in passage
        template["count_reviews"] = {}
        for item in count_input["batches"]:
            template["count_reviews"][item["review_key"]] = {
                "fingerprint": item["fingerprint"], "decision": "pass", "issues": [],
                "exception_reason": "合成测试只有一个明确观点，强行扩充会制造不存在的观点",
                "reason": "已检查簇的颗粒度，当前单簇准确覆盖唯一测试表达",
            }
        dump(Path(state["required_file"]), template)
    elif stage == "final_excerpt_review":
        assert state.get("input_file") and state.get("full_input_file")
        assert state.get("chunk_index") == 1 and state.get("chunk_total") >= 1
        template = load(Path(state["required_file"]))
        inputs = [json.loads(line) for line in Path(state["input_file"]).read_text(encoding="utf-8").splitlines() if line.strip()]
        template["reviews"] = {}
        for item in inputs:
            assert "body" not in item and "excerpt" not in item and "full_source_lookup" not in item
            assert item["target_review_required"] is False
            assert item["work_consistency_review_required"] is False
            assert "context_before" not in item and "context_after" not in item
            excerpt = "".join(item["excerpt_segments"].values())
            template["reviews"][item["view_id"]] = {
                "review_fingerprint": item["review_fingerprint"],
                "decision": "keep", "aspect_evidence_candidate_index": 1, "stance": "positive",
                "stance_evidence_candidate_index": 1, "self_contained": True,
            }
            if item.get("cluster_claim_review_required"):
                template["reviews"][item["view_id"]].update({
                    "cluster_claim_passed": True,
                    "cluster_claim_evidence_candidate_index": 1,
                })
            assert len(excerpt) >= 70
        dump(Path(state["required_file"]), template)
    else:
        raise AssertionError(state)
    state = run("advance", "--workspace", str(WORKSPACE), "--timeout", "0.2")
else:
    raise AssertionError("workflow did not converge")

assert state["status"] == "COMPLETE", state
assert OUTPUT.exists()
verification = load(WORKSPACE / "run" / "verification.json")
assert verification["status"] == "PASS", verification
order_audit = load(WORKSPACE / "run" / "workbench_order_audit.json")
assert order_audit["random"] is False
assert order_audit["clusters"][0]["ordered_sources"][0]["source_id"]
manifest = load(WORKSPACE / "workflow_manifest.json")
expected_stages = ["prepare", "link_check", "select", "cluster_base", "cluster_final", "render_probe", "render_final", "verify"]
history_stages = [item["stage"] for item in manifest["history"]]
positions = [history_stages.index(stage) for stage in expected_stages]
assert positions == sorted(positions), history_stages
assert all("elapsed_seconds" in item for item in manifest["history"] if item["stage"] != "extract")
cache_audit = load(WORKSPACE / "run" / "cluster_routing_cache_audit.json")
assert cache_audit["hits"] >= 1 and cache_audit["misses"] == 0
auto_semantic_rows = [json.loads(line) for line in (WORKSPACE / "run" / "excerpt_semantic_auto_accepted.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
assert len(auto_semantic_rows) == 0
semantic_input_rows = [json.loads(line) for line in (WORKSPACE / "run" / "excerpt_semantic_review_input.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
assert len(semantic_input_rows) == 1 and semantic_input_rows[0]["cluster_claim_review_required"] is True
cluster_final_command = manifest["stages"]["cluster_final"]["command"]
assert "--set-reviews" in cluster_final_command and "--member-reviews" not in cluster_final_command
link_command = manifest["stages"]["link_check"]["command"]
assert str((WORKSPACE / "run" / "source_link_candidates.jsonl").resolve()) in link_command
assert "--cache" in link_command
assert str((WORKSPACE / "run" / "workbench.verified-candidate.html").resolve()) in manifest["stages"]["render_final"]["output_hashes"]
assert str(OUTPUT.resolve()) in manifest["stages"]["verify"]["output_hashes"]
assert str((WORKSPACE / "run" / "verification.json").resolve()) in manifest["stages"]["verify"]["output_hashes"]

# An existing earlier workspace does not have the discovery seed file.  The
# controller must regenerate selection outputs instead of crashing at cluster discovery.
(WORKSPACE / "run" / "cluster_discovery_seed_input.jsonl").unlink()
upgrade_state = run("status", "--workspace", str(WORKSPACE))
assert upgrade_state["status"] == "READY_TO_ADVANCE" and upgrade_state["stage"] == "select", upgrade_state
print(json.dumps({"status": "PASS", "workspace": str(WORKSPACE), "output": str(OUTPUT), "verification": verification}, ensure_ascii=False, indent=2))
