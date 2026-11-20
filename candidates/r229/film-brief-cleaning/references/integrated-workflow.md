# 默认入口说明

默认清洗完成后交工作台，停在HUMAN_SELECTION_REQUIRED，按writing-choice.md询问一次人工还是自动选材；收到明确选择后接相应分支。用户开始时已明确选材方式则不重复问。人工分支等待勾选导出；收到选材文件后直接from-selection自动写稿，不常规提问。下面涉及清洗后自动接写稿、固定六段的说明仅适用于用户明确要求的--auto-write或from-workbench自动选材模式。正文终审后脚本自动附链接再交付Word，不需要截图或额外确认。

# 原表到工作台和报告的一次运行

> r130 在现有发现包中补充具体评价线索。它们来自本期已提取来源，每个方面最多2条，不增加模型轮次。选中的具体评价种子保留到逐条审样。歌曲上线资讯与实际听感分开。最终工作台按独立、可入稿的判断计算正文段数上限，批评合计一段。上限不足6时交接报告缺口并保留工作台。成稿仍为6—7段，段前句用评价动词起句。


在45分钟清洗内预留最多480秒做一次末端标题证据核对：使用全部实际留样确认标题、同方面近义合并及入稿优选，只对每组最多2条风险/代表句做逐条编辑。没有完成该关口不冒称完整通过。FINAL_DISPLAY_EVIDENCE_V2及本期schema优先于旧固定标题兼容说明。有历史时，全部关系重新比较最终展示句：旧句从顶层history_samples按ID读取，history_check.current_evidence绑定本组最终仍保留的1—2条candidate_id及12—140字连续quote_snippet，不用最初seed或已弃样本解释变化。工作台与写稿各自计时，各阶段内续跑保留原开始时间。

从Skill目录执行：

```powershell
<python> -X utf8 scripts/integrated_workflow.py start --source <原表目录> --target <剧名> --workspace <不存在的新目录>
```

连续出刊时使用重复的 --history <前期已交付报告.docx> 传同作品前期Word。脚本只取观点正文的判断和引用，避免让模型通读整份报告。旧工作台HTML、final_dataset.json及相对应的--history-plan继续兼容。工作台默认且最多2700秒，写稿默认且最多900秒，可用--cleaning-budget-seconds和--writing-budget-seconds调整。工作台从start计时，写稿从确定选材方案后计时，分别报告实际用时。

已有完整工作台时可从同一入口接续，不重新清洗或改写旧完成状态：

```powershell
<python> -X utf8 scripts/integrated_workflow.py from-workbench --dataset <原工作区/final_dataset.json> --workspace <不存在的新目录>
```

该模式默认只给接续写稿计900秒，验证原工作台HTML与展示数据一致，冻结复制已审数据和原状态后进入WRITING_OUTLINE；原清洗耗时与接续写稿耗时分别报告。支持--history、--history-plan与--writing-budget-seconds。

主窗口按返回stage执行：

- CLEANING：沿用本Skill既有发现、分包审样、有限编辑和原生成员关停流程，工作区固定为本目录cleaning。所有当前清洗命令只由主窗口执行。完成后运行返回的integrated_workflow advance。
- 连续模式：历史对照已合入CLEANING的观点发现答卷；完成清洗直接进入WRITING_OUTLINE，不默认再跑COMPARING。历史只作点位取舍比较，不回灌本期证据。
- WRITING_OUTLINE：只读outline_input.json，先写每段核心判断、边界和实际支持原句，通常3—4个互补声音、充分时2个；每段至少2条独立有效声音，不足不独立成段，不用角色介绍或上线资讯作例外。不能支撑6段时明确insufficient，保留工作台。通过后自动写入editorial_plan，避免重复抄写提纲。
- WRITING：只读writing_input.json、已预填的editorial_plan.json和writing.md，完成分句证据、署名及每点通常3—4条具体互补引文（充分时2条，短评有新增信息时可选5—6条，不为数量凑样本；每段至少2条独立有效声音，单条不独立成段），再执行advance。已有合格微博/小红书直接批评优先使用；确不支持当前判断时按schema逐条解释，不强配平台数量。不得读原表、候选池或联网补样。先出草稿，不等待人工批准，不伪造人工确认。跨日模式每点填continuity_reason，只认清单内最终history_review；日重点是建议，可基于证据取舍。
- FINAL_PROSE_REVIEW：先通读整篇最终稿与实际引文，按[final-prose-review.md](final-prose-review.md)填final_prose_review.json。有问题选revise并具体指出删点或修订，控制器返回WRITING；改计划后重新render与终审，不复用旧pass。无未解决问题才能pass并发布。这是AI成稿终审，不要求用户人工确认，继续计入同一总预算。
- INCOMPLETE / BUDGET_EXHAUSTED：不继续轮询或等待答卷；根据真实注册表停止仍运行的本会话任务并关闭本会话团队，及时结束。保留已完成文件和实际耗时，不把部分结果当成功。
- COMPLETE：仅交付delivery/观点工作台.html和delivery/主要舆论观点.docx，分别与cleaning/workbench.html和writing/opinion_draft.docx逐字节一致；返回workbench、word及两个deliverables路径，不生成index.html合并预览页。核对单、Markdown、独立图表、JSON与日志留在工作区后台，观点图嵌入Word。报告正文不加状态小字。Word需要由宿主渲染并检查版式，静态脚本通过不能代替视觉验收。

新结果只有FINAL_SELECTED_COMPLETE才允许自动交接，正面不足6组为FINAL_POSITIVE_MIN_NOT_MET，不交成品或接写稿；部分审样仍不能交接。旧版仅因正面数不足而为POSITIVE_VIEWPOINT_MIN_NOT_MET时，须逐项验证待审为0、结构与有限编辑完成、原预算内完成及展示样本的逐字出处和唯一归属，才按数量提示接写稿；保留旧数据和状态，不改标旧测试成功。交接复制带完成状态的final_dataset.json到writing_inputs并冻结指纹；后续只认这份已审展示数据，任何改变都拒绝继续。写稿的逐字位置校验由脚本核对内嵌溯源，不提供额外材料给模型。

stage_timings、elapsed_seconds和delivery_manifest分别记录阶段用时、总用时、产物哈希与AI_PREVIEW状态。完成阶段表示草稿交齐，不表示人工定稿。超过总预算保留现有工作台并如实报告，禁止修改门槛或开始时间。字段错误只修当前答卷，不反复改规则或重跑清洗。

本版写稿计划保留report_style=MANUAL_BRIEF_V1。脚本将正文按正面、讨论、负面稳定排序，同步重编号；唯一负面段按criticism_opening选择编号或此外起句，保留实际批评动词，字数口径和弹性范围见writing.md。正文与图表排序独立。新增compiled_editorial_plan.json和paragraph_lengths.json仅留后台审计；不改变原始模型计划或工作台。

绘图与正文同轮完成。一个正文点对应一条主条形，余下候选主题留审计，按实际入图来源计算占比。“其他”只收少量未作为正文主判断的真实已审批评，并固定最后。见[chart-coverage.md](chart-coverage.md)。

校验会集中反馈各段可检查的问题，并同时检查边界和图表。集中修正反馈后仍须完整校验和最终全稿通读，不能把错误汇总当作放宽审核。writing_input仅减少重复字段，所有实际display_excerpt完整保留；后台candidate_inventory保留用于兼容审计。
