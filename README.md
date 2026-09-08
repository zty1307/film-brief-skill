# film-brief-cleaning Skill

当前发布版本：v1.1.5。仓库包含完整的 Skill 说明、执行脚本、复核契约、媒体主体库和离线工作台模板，可独立安装和运行。

用于清洗中文影视综艺舆情导出、发现当期观点簇、筛选可核验原文样本并生成离线 HTML 工作台的 Codex Skill。

## 安装

将本仓库克隆或解压到 Codex Skill 目录：

```text
$CODEX_HOME/skills/film-brief-cleaning/
```

目录根部应直接包含 `SKILL.md`。

首次运行前安装唯一的第三方依赖：

```powershell
<python> -m pip install -r requirements.txt
```

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
- `REVIEW_REQUIRED`：只读取当前 `input_file` 和 `review_requirements`，从 `template` 填写 `required_file`，原样保留 `_workflow`；再次运行 `advance` 后再处理下一片。初次观点发现阶段需要依次读取完整 `input_files`。
- `BROKEN` / `BLOCKED`：按状态中的结构化诊断修复，不得跳过关口或复用旧运行的评审文件。
- `COMPLETE`：机器验收通过；交付前仍需打开 HTML 做视觉检查。

较大的来源复核和最终摘录复核会自动拆成每批最多60条；控制器每轮只发出下一片未完成内容，并由脚本账本累计各分片答案，执行模型不用重写历史记录。模板内含允许值、证据范围和当前阶段约束。多轮 `cluster_overrides.json` 也由控制器累计，当前轮只处理残余队列不会丢失前轮答案。

正式输出路径只能由控制器在 `verify: PASS` 后写入。运行中自行制作、复制或改名得到的 HTML 不属于本 Skill 的结果，也无法得到 `COMPLETE`。

详细合同见 `references/contracts.md`，完整流程见 `references/workflow.md`。

## 回归测试

```powershell
<python> tests/test_issue_regressions.py
<python> tests/test_workflow_controller.py
```

第一项覆盖跨剧错置、攻击性噪声、日期文件名、排除出口、人物名归簇降权、归簇双片段逐字定位、证据校验和多轮覆盖累计；第二项从原始 Excel 开始完整推进到“待验收页→验证→正式发布”，并确认链接检查只读取仍可能保留的候选来源。

## 数据边界

仓库不包含任何业务原始数据、人工成品报告、运行工作区或历史评审结果。历史报告不得作为样本准入或聚类答案。
