# film-brief-cleaning Skill

用于清洗中文影视综艺舆情导出、发现当期观点簇、筛选可核验原文样本并生成离线 HTML 工作台的 Codex Skill。

## 安装

将本仓库克隆或解压到 Codex Skill 目录：

```text
$CODEX_HOME/skills/film-brief-cleaning/
```

目录根部应直接包含 `SKILL.md`。

## 运行

生产运行只使用状态控制器。不要让执行模型自行拼接 `prepare/select/cluster/render/verify`：

```powershell
<python> scripts/film_workflow.py doctor --source <原始数据目录> --period-config <period_config.json> --workspace <独立工作区> --output <单期工作台.html>
<python> scripts/film_workflow.py init --source <原始数据目录> --period-config <period_config.json> --workspace <独立工作区> --output <单期工作台.html>
<python> scripts/film_workflow.py advance --workspace <独立工作区>
```

没有现成 `period_config.json` 时，从 `doctor` 和 `init` 同时省略 `--period-config`；控制器会生成绑定本次工作区的配置模板。

之后只反复运行 `advance`，并严格依据 `workflow_status.json` 行动：

- `READY_TO_ADVANCE`：再次运行 `advance`。
- `REVIEW_REQUIRED`：读取 `input_file(s)` 和 `review_requirements`，从 `template` 填写 `required_file`，原样保留 `_workflow`。
- `BROKEN` / `BLOCKED`：按状态中的结构化诊断修复，不得跳过关口或复用旧运行的评审文件。
- `COMPLETE`：机器验收通过；交付前仍需打开 HTML 做视觉检查。

模板内含允许值、证据范围和当前阶段约束。多轮 `cluster_overrides.json` 由控制器累计，当前轮只处理残余队列也不会丢失前轮答案。

详细合同见 `references/contracts.md`，完整流程见 `references/workflow.md`。

## 回归测试

```powershell
<python> tests/test_issue_regressions.py
<python> tests/test_workflow_controller.py
```

第一项覆盖跨剧错置、攻击性噪声、日期文件名、排除出口、归簇双片段逐字定位、结构化业务错误和多轮覆盖累计；第二项从原始 Excel 开始完整推进到 `verification.json: PASS`。

## 数据边界

仓库不包含任何业务原始数据、人工成品报告、运行工作区或历史评审结果。历史报告不得作为样本准入或聚类答案。
