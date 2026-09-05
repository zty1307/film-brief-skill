# film-brief-cleaning Skill

用于清洗中文影视综艺舆情导出、发现当期观点簇、筛选可核验原文样本并生成离线 HTML 工作台的 Codex Skill。

## 安装

将本仓库克隆或解压到 Codex Skill 目录：

```text
$CODEX_HOME/skills/film-brief-cleaning/
```

目录根部应直接包含 `SKILL.md`。

## 运行

生产运行统一使用状态控制器：

```powershell
<python> scripts/film_workflow.py doctor --source <原始数据目录> --period-config <period_config.json> --workspace <独立工作区> --output <单期工作台.html>
<python> scripts/film_workflow.py init --source <原始数据目录> --period-config <period_config.json> --workspace <独立工作区> --output <单期工作台.html>
<python> scripts/film_workflow.py advance --workspace <独立工作区>
```

反复运行 `advance`，并依据 `workflow_status.json` 中的 `review_requirements`、`input_file(s)`、`template` 和 `required_file` 完成语义审核。状态达到 `COMPLETE` 后仍需打开 HTML 做视觉检查。

详细合同见 `references/contracts.md`，完整流程见 `references/workflow.md`。

## 数据边界

仓库不包含任何业务原始数据、人工成品报告、运行工作区或历史评审结果。历史报告不得作为样本准入或聚类答案。
