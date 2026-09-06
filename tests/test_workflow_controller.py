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
        state = run("advance", "--workspace", str(WORKSPACE), "--timeout", "0.2")
        continue
    assert state["status"] == "REVIEW_REQUIRED" and state.get("review_requirements"), state
    if stage == "source_review":
        template = load(Path(state["template"]))
        queue = [json.loads(line) for line in Path(state["input_file"]).read_text(encoding="utf-8").splitlines() if line.strip()]
        template["reviews"] = []
        for item in queue:
            candidate = item["review_evidence_candidates"][0]
            template["reviews"].append({
                "source_id": item["id"], "decision": "retain_core",
                "reason": "原文明确评价目标剧集的表演及人物关系，并给出眼神、停顿和动作等细节",
                "evidence_position": [candidate["start"], candidate["end"]],
            })
        dump(Path(state["required_file"]), template)
    elif stage == "dedup_review":
        template = load(Path(state["template"]))
        audit = load(Path(state["input_file"]))
        template["reviews"] = [
            {"left_id": item["left_id"], "right_id": item["right_id"], "decision": "independent", "reason": "独立表达"}
            for item in audit.get("medium_similarity_candidates", [])
        ]
        dump(Path(state["required_file"]), template)
    elif stage == "cluster_discovery":
        template = load(Path(state["template"]))
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
        template_path = Path(state.get("template", WORKSPACE / "review_templates" / "cluster_overrides.template.json"))
        template = load(template_path)
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
        template = load(Path(state["template"]))
        set_input = load(Path(state["input_files"][0]))
        count_input = load(Path(state["input_files"][1]))
        template["reviews"] = {}
        for item in set_input["clusters"]:
            ids = [sample["source_id"] for sample in item["samples"]]
            passage = item["samples"][0]["passage"]
            template["reviews"][item["review_key"]] = {
                "fingerprint": item["fingerprint"], "decision": "pass", "report_role": "report_point",
                "scope_type": "current_broadcast_reaction", "title_claims_passed": True,
                "title_claims": [
                    {"claim": "演员表演细腻自然", "supporting_source_ids": ids},
                    {"claim": "人物关系与情绪变化真实可信", "supporting_source_ids": ids},
                ],
                "member_support": {source_id: {"claim_indices": [0, 1], "evidence": "表演细腻自然，人物关系也显得真实可信"} for source_id in ids},
                "stance_purity_passed": True, "scope_purity_passed": True,
                "scope_reason": "均为当前播出后的剧集评价", "granularity_passed": True,
                "granularity_reason": "成员共同评价表演细节及其带来的人物可信度",
                "source_role_checked": True, "source_role_reason": "来源角色已核对",
                "reason": "标题两项主张均有当前分配片段中的直接证据",
            }
            assert "表演细腻自然，人物关系也显得真实可信" in passage
        template["count_reviews"] = {}
        for item in count_input["batches"]:
            template["count_reviews"][item["review_key"]] = {
                "fingerprint": item["fingerprint"], "decision": "pass", "cluster_count": item["cluster_count"],
                "range_status": item["expected_range_status"], "reader_load_reviewed": True,
                "overfragmentation_checked": True, "overbreadth_checked": True, "no_forced_merge_or_split": True,
                "exception_approved": True, "exception_reason": "合成测试只有一个明确观点，强行扩充会制造不存在的观点",
                "reason": "已检查簇的颗粒度，当前单簇准确覆盖唯一测试表达",
            }
        dump(Path(state["required_file"]), template)
    elif stage == "cluster_member_review":
        template = load(Path(state["template"]))
        member_input = load(Path(state["input_file"]))
        template["reviews"] = {}
        for item in member_input["members"]:
            template["reviews"][item["review_key"]] = {
                "fingerprint": item["fingerprint"], "decision": "pass", "target_passed": True,
                "title_support_passed": True, "stance_passed": True, "scope_checked": True,
                "source_role_checked": True, "supported_claim_indices": [0, 1],
                "evidence": "表演细腻自然，人物关系也显得真实可信",
                "reason": "片段直接肯定表演细腻自然，并说明人物关系真实可信",
            }
        dump(Path(state["required_file"]), template)
    elif stage == "final_excerpt_review":
        template = load(Path(state["template"]))
        inputs = [json.loads(line) for line in Path(state["input_file"]).read_text(encoding="utf-8").splitlines() if line.strip()]
        template["reviews"] = {}
        for item in inputs:
            excerpt = item["excerpt"]
            template["reviews"][item["view_id"]] = {
                "decision": "keep", "target_passed": True, "target_evidence": "《测试剧》",
                "aspect_passed": True, "aspect_evidence": "表演细腻自然", "stance": "positive",
                "stance_evidence": "表演细腻自然", "work_consistency_passed": True,
                "work_consistency_evidence": "《测试剧》", "self_contained": True,
                "short_excerpt_justified": False, "short_excerpt_reason": "", "specific_support_passed": False,
                "specific_support_evidence": "", "independent_opinion_passed": True,
                "opinion_evidence": "表演细腻自然", "reason": "最终摘录完整呈现判断和具体表演依据",
            }
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
expected_stages = ["prepare", "link_check", "select", "cluster_base", "cluster_set", "cluster_final", "render_probe", "render_final", "verify"]
history_stages = [item["stage"] for item in manifest["history"]]
positions = [history_stages.index(stage) for stage in expected_stages]
assert positions == sorted(positions), history_stages
cluster_set_command = manifest["stages"]["cluster_set"]["command"]
cluster_final_command = manifest["stages"]["cluster_final"]["command"]
assert "--set-reviews" in cluster_set_command and "--member-reviews" not in cluster_set_command
assert "--set-reviews" in cluster_final_command and "--member-reviews" in cluster_final_command
assert str(OUTPUT.resolve()) in manifest["stages"]["render_final"]["output_hashes"]
assert str((WORKSPACE / "run" / "verification.json").resolve()) in manifest["stages"]["verify"]["output_hashes"]
print(json.dumps({"status": "PASS", "workspace": str(WORKSPACE), "output": str(OUTPUT), "verification": verification}, ensure_ascii=False, indent=2))
