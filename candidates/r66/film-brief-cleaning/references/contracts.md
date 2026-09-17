# 文件契约

使用 `film_workflow.py` 的生产运行时，控制器直接在状态文件指定的 `required_file` 生成当前可填写提交。执行者只补全预留字段，不复制模板、不手工新建评审文件。文件自带 `_workflow`，其中记录本次工作区 ID 和评审输入指纹；填写时必须原样保留。下列示例为便于阅读省略了 `_workflow`，不能据此另建无绑定文件。

标题格式错误的限定例外：状态含 `title_format_repair` 时，控制器给每个错误组独立的输入和 `required_files` 小答卷（仅 `title`、`reason`，初始空白）；`protected_parent_file` 不允许执行者直接修改。读 `operator_contract.read_now`，每个组只写自己的文件，全部完成后执行 `next_command` 的 `definition-title-repair-apply`。脚本验证当前工作区、原定义/配置/发现材料/规则代码指纹和全部提交，经原完整定义预检后一次带锁合并，仅改对应标题，其他字段与组不变。缺失、坏格式、空理由、额外字段、无效标题或指纹漂移均不部分合并；不是自动改标题或语义批准。非标题字段错误、JSON错误和覆盖缺口仍按各自当前状态处理，不套此小补丁。

## `period_config.json`

```json
{
  "batch_order": ["剧名 第一期"],
  "representative_sample_limit": 10,
  "targets": {
    "剧名 第一期": {
      "content_mode": "serial_drama",
      "strong_terms": ["剧名", "#电视剧剧名#"],
      "weak_terms": ["可能泛指其他内容的简称"],
      "auxiliary_terms": ["角色名", "主演名"],
      "comparison_terms": ["同期对比剧名", "对比剧角色名"]
    }
  },
  "quality_floor": {"long": 12.5, "social": 9.5},
  "media_subject_extensions": [
    {
      "id": "new_verified_media_subject",
      "canonical_name": "新增且已核验的媒体母机构",
      "tier": "major_mainstream",
      "aliases": [
        {"name": "平台实际账号全名", "account_type": "culture_vertical"}
      ],
      "domains": [
        {"host": "verified-first-party.example.cn", "account_type": "news_website"}
      ]
    }
  ],
  "background_cluster_ids": [],
  "period_windows": {
    "剧名 第一期": {"start": "2026-09-01 00:00:00", "end": "2026-09-01 23:59:59"}
  }
}
```

批次名称与原始数据子目录一致。`strong_terms` 只写能够独立确认目标作品的全名、完整节目名、官方专属话题，或从当期标题和话题标签实际确认的唯一简称；`weak_terms` 写可能泛指题材或其他内容的简称，只能触发语义复核；`auxiliary_terms` 写演员、嘉宾、主创和角色名，只辅助理解，不独立触发准入；`comparison_terms` 记录同期文章中容易混入的其他作品。旧版 `terms/regex` 已停用，防止人物名单或泛称被无意当成强锚点。若导出文件名自带“起始时间至结束时间”，脚本会据此判定期内；否则必须填写 `period_windows`。日期型 `end`（如 `2025-09-11`）按该日23:59:59处理，包含结束日全天；需要更精确口径时填写完整时间。只有确认输入本身已经严格限定期次时，才可设置 `assume_all_in_period: true`。

`content_mode` 仅允许 `serial_drama` 或 `episodic_variety`。电视剧使用前者；周更综艺使用后者，并要求来源全文复核补充 `episode_scope`、`episode_evidence`。`episode_scope` 取值为 `latest_episode`、`previous_episode_prominent`、`program_level_current`、`out_of_scope`；前一期突出话题还必须填写 `prominence_basis`。

旧字段 `cluster_min_independent_sources`、`cluster_max_sources_before_review`、`min_samples_per_cluster`、`max_samples_per_cluster`、`sample_budget`、`reading_budget` 和 `cluster_target_count` 仍停用，出现时报错。新工作区使用 `representative_sample_limit: 10`（只允许1—10的整数，不能是字符串/布尔值）；这只限制每簇代表选样，不删减后台来源，也不设置最低数。9—16仍只触发观点簇颗粒度复查。无新字段的旧工作区保留旧行为，不算新规则测试。

代表模式保存全量 `provisional_clustered_items.json` 和 `cluster_review_queue.deferred.jsonl`。未选材料不是被否决；后者是未确认脚本路由疑点，不是假装AI审过。主要覆盖缺口仍检查，每簇无合格候选时会送一个普通归簇候选避免稀有观点消失。最终审核仅给每组剩余名额的候选；待审占候选名额但不算确认，通过10条即停止。`representative_selection_audit.json` 分列 confirmed、reserved_pending、stopped 和未选原因。

最终条目明确含 `representative_selection:true` 时，可提交 `decision:"not_selected"`、当前 `excerpt_segments` 的整数 `evidence_candidate_index` 和具体 `reason`。当前指纹绑定仍核验；该决定只表示不作为本组代表，不能显示为keep、计入drop、声称来源无效或已经转簇，也不要求穷尽重截/其他簇检查。脚本保留该决定并补下一不同来源。真正的drop和转簇请求仍走原契约。每个展示keep仍须通过原全部语义、逐字追溯和离线验收。

普通keep确认全部当前展示句段；证据编号只定位支持原文，不会删掉其余句。若理由明确称第N句段是另一作品/无关并“不选、不展示、不作为证据”，但该编号仍在当前excerpt_segments，返回定向重截错误，不能用文字声明代替实际修改。纯转折或“能否、未知数”等不确定表达不能脚本自动keep；交AI核实或在代表模式不选，不自动删后台来源。直接抽奖邀请不能被“背景记录”的理由覆盖。

`episode_scope` 一经来源全文复核确认，后续聚类、摘录和页面生成只能读取，不能用观点簇的 `scope_type` 覆盖。除 `mixed_scope_explicit` 外，脚本必须逐成员核对二者完全一致，不一致即阻止发布。

Skill 自带“精简核心库 + 已核验补充库” `assets/media_subject_registry.json`。核心库预置中央重点媒体、广电及影视文化专业媒体和少量高频地方主流媒体；库外媒体只在真实数据中出现后，由AI或人工核验官方归属，再通过 `media_subject_extensions` 加入补充库。每个主体记录唯一ID、母机构名称、等级、库内分组、已核验账号别名和必要的第一方官网域名。账号名称会做空白、标点和大小写归一化后精确匹配，不做“包含新闻/日报/媒体/网”等模糊匹配，也不擅自删除“官方、娱乐、频道”等后缀来套主体。若账号没有命中，脚本再检查已核验官网域名，最后才使用认证信息辅助识别；认证类型必须为媒体，且认证主体必须唯一命中库内母机构或不少于4字的已核验别名。“认证类型=媒体”本身不构成主流媒体身份。聚合、转载、社交和分发平台主机不得入库。经核验的官方垂直账号继承母机构等级，媒体身份在各渠道通用；该字段只影响同簇排序，不改变来源准入和观点判断。

## `source_link_health.json`

```json
{
  "scope": "current_period_source_link_health",
  "generated_at": "2026-09-04T07:00:00+00:00",
  "counts": {"reachable": 80, "confirmed_dead": 2, "indeterminate": 18},
  "results": [
    {
      "url": "https://example.com/source",
      "classification": "confirmed_dead",
      "status_code": 404,
      "confirmation_status_code": 404,
      "checked_at": "2026-09-04T07:00:00+00:00",
      "reason": "http_404_or_410_confirmed_twice"
    }
  ]
}
```

来源复核通过后，控制器生成 `source_link_candidates.jsonl`。`check_links.py` 只检查其中仍可能保留的非空URL；`select --link-health` 若发现任一仍可能保留的URL未覆盖则拒绝继续。只有两次GET均返回404或410的 `confirmed_dead` 会硬排除来源。`reachable` 只说明检查时能访问；`indeterminate` 包括401、403、429、登录墙、反爬、5xx、DNS错误和超时，不能据此删除。`blocked` 不发起网络请求。

## `source_reviews.json`

来源候选交接仅合并同一原文中重叠或相邻的字符区间，减少重复阅读；不连接有空隙的区间，不丢失原候选覆盖的字符，不改变准入决定。合并后的 `candidate_index`、`start/end` 和 `text` 以本次输入为准，证据始终按原文位置复算。不得沿用旧分片的编号。

来源 `evidence_position` 使用 `[start,end]`；对绑定有效、已有明确决定的单区间 `[[start,end]]`，控制器仅去掉一层多余括号，不改变数值或原文，并继续校验越界和逐字一致性。多区间、空区间或含混格式仍返回具体条目的修复请求，不据此猜测证据。

```json
{
  "scope": "current_period_source_fulltext_reviews",
  "reviews": [
    {
      "source_id": "稳定来源ID",
      "decision": "retain_core",
      "reason": "原文包含具体观后判断及支撑细节",
      "evidence_candidate_index": 1,
      "episode_scope": "latest_episode",
      "episode_evidence": "原文中的本期嘉宾、选手、环节或话题证据",
      "prominence_basis": "仅 previous_episode_prominent 时填写"
    }
  ]
}
```

`decision` 仅允许 `retain_core`、`retain_consensus`、`exclude`。三种决定都必须提交逐字证据；`retain_core` 和 `retain_consensus` 无需重复写理由，`exclude` 若同意当前输入的 `auto_reason`，无需重复抄写，控制器在明确 exclude 后补齐；理由不同时另填 `reason`。控制器每轮提供下一批不超过60条且不超过约120KB的精简记录；分片不重复整篇正文、统一回查说明、质量中间量或候选命中词。优先填写 `evidence_candidate_index`，脚本自动还原逐字原文及位置；候选均不适用时，才按 `workflow_status.full_source_file` 和 `source_id` 回查全文并填写 `evidence` 或 `evidence_position`。提交由脚本账本累计，执行模型不复制历史答案。

## 同稿审计

完全相同和高置信同稿由脚本合并，结果写入 `dedup_audit.json`。中等相似候选默认保留为独立表达，并继续留在审计文件中；生产控制器不要求模型逐对填写去重决定。

## `cluster_definitions.json`

```json
{
  "scope": "current_period_data_derived_clusters",
  "batches": {
    "剧名 第一期": [
      {
        "id": "P01",
        "title": "肯定实力派阵容与生活化表演，认为人物群像自然可信",
        "stance": "positive",
        "summary": "簇的语义边界",
        "keywords": [["实力派", 6], ["自然", 4]],
        "required_any": [],
        "negative_cues": [],
        "background": false,
        "rare_signal": false
      }
    ]
  }
}
```

每个批次必须至少一个簇；ID在批次内唯一；`stance` 必须是 `positive`、`objective`、`negative` 之一；标题应是能直接理解的完整报告体观点句。“演员表现”“剧情张力”一类短标签不合格。每个非背景簇须在 `required_any` 填写简洁的方面词或短语及必要同义表达，脚本不截断显式补充的有效同义词；作品名、演员名、角色名以及“剧情、热度、好看”等通用词不能单独承担归簇。作品名不增加观点得分，人物名只给极低辅助权重。负向簇可填写 `negative_cues` 辅助自动发现。客观舆情概况可设 `objective_meta: true`。不要求每个批次三种立场齐全，也不要求每簇达到任何样本数量。

初次观点发现依次读取控制器 `input_files` 指向的 `cluster_discovery_chunks/chunk-*.jsonl`。这些片段按立场和渠道分层抽取，每批最多180条；脚本删除每条重复携带的回查路径、字符位置和质量中间量后，通常把全部样本放进一个紧凑交接文件。压缩只改变传递格式，不减少发现样本。全量来源随后都会归簇并接受覆盖检查。若片段不足以确认语义或作品归属，再按 `source_id` 到 `retained_sources.jsonl` 回查全文。若方面错配达到阈值，`cluster_definition_gap_samples.jsonl` 给出最多40条代表缺口，先修改簇定义，再处理少量残余归簇。

## `cluster_set_reviews.json`

```json
{
  "scope": "current_period_cluster_set_reviews",
  "reviews": {
    "剧名 第一期\tP01": {
      "fingerprint": "从 cluster_set_review_input.json 原样复制",
      "decision": "pass",
      "report_role": "report_point",
      "scope_type": "current_broadcast_reaction",
      "issues": [],
      "reason": "代表样本与低对齐样本均支持同一报告体观点，立场和范围一致"
    }
  },
  "count_reviews": {
    "剧名 第一期": {
      "fingerprint": "从 cluster_count_review_input.json 原样复制",
      "decision": "pass",
      "issues": [],
      "reason": "已检查过度切碎和大口袋簇，11个观点均有独立评价机制且阅读量适中"
    }
  }
}
```

第一次运行 `cluster` 后生成 `cluster_set_review_input.json`。审查键固定为“批次名 + 制表符 + 簇ID”，`fingerprint` 必须逐字复制本次输入；标题、立场或成员来源变化都会令旧审查失效。每个非空簇必须覆盖。`report_role` 仅允许 `report_point`、`subtopic`、`data_note`、`rare_signal`；`data_note` 必须使用 `objective` 立场。角色只帮助人工理解观点在报告中的用途，不形成页面层级，也不影响来源去留。

脚本重新生成审查表时，只复用单条 `fingerprint` 与当前输入完全相同的显式结论，不把旧记录的指纹覆盖进新模板。发生变化的组/整期数量记录恢复为当前指纹的空白判断；未变化的组保持已有结论。这不是默认通过，也不重做来源全文复核。

在 `cluster_set_review` 发现具体成员错放，可直接执行 `operator_contract.member_repair.prepare_command`，替换并列出1—60个当前成员来源ID。读取返回的小输入，填写唯一小补丁，再按返回apply/advance实际合并；无需修改词表触发重新绑定。脚本绑定当前真实成员、当前原文和既有归属，仅做普通归属/逐字证据校验，不代替AI判断，不部分合并。错放且有效的材料应转组或修片段；确无可报告观点才使用普通有证据 `__exclude__`。副簇受影响不在该主簇小补丁简化覆盖。返回 `APPLIED_PENDING_REVIEW` 不是最终语义通过。仅簇自身主张/边界不成立时按明示的 `definition_repair` 修改定义，不能为解锁改归属做无关微调。

`cluster_set_review_input.json` 每簇最多给出8条样本，脚本同时覆盖高质量代表项、低对齐项和分布位置，完整成员ID仍进入 `fingerprint`。AI只填写通过或返修结论、来源角色、范围类型和一句理由，不再拆标题主张或抄写来源ID。`decision:"pass"` 与空 `issues` 表示已经综合检查标题、立场、范围、来源角色和颗粒度。电视剧的 `scope_type` 取 `pre_broadcast_expectation`、`current_broadcast_reaction`、`later_reputation`、`mixed_time_explicit`；综艺取 `latest_episode`、`previous_episode_prominent`、`program_level_current`、`mixed_scope_explicit`。客观簇另填合法 `objective_subtype`。

`count_reviews` 是页面生成前的整期簇数审查。脚本计算指纹、实际簇数和9—16范围状态；AI只填写 `decision`、`issues` 和综合理由，不再抄写这些确定数据。范围外另填 `exception_reason`，说明继续拆分或合并为何损害语义质量。脚本不会为了达标自动调整观点。

## `cluster_overrides.json`

```json
{
  "scope": "current_period_source_cluster_reviews",
  "overrides": {
    "稳定来源ID": {
      "cluster": "P01",
      "secondary_cluster": "P03",
      "reason": "全文包含两个互不重叠、各自完整的观点",
      "anchor_terms": ["原文定位词"],
      "passage_fragments": ["同一观点的第一段连续原文", "同一观点的第二段连续原文"],
      "passage_positions": [[120, 146], [188, 224]],
      "passage_stance": "positive",
      "target_evidence": "同一来源中逐字摘录的目标作品锚点",
      "secondary_anchor_terms": ["第二观点定位词"],
      "secondary_passage_stance": "negative"
    }
  }
}
```

`secondary_cluster` 可省略。多剧文章或自动立场与簇不一致时，必须填写 `target_evidence` 和 `passage_stance`，且理由说明判断归属。不能为了增加样本数重复分配同一段文字。

`required_any` 是自动归簇的初步路由词表，不要求穷尽所有同义表达。当前片段以不同措辞表达相同方面时，可在 `anchor_terms` 中填写片段里实际存在的评价短语；脚本只据此通过初步方面门，后续独立成员复核仍须判断其是否真正支持簇标题。默认 `anchor_terms` 只从相邻一至两句中选取成员片段。同一观点的完整判断确实分布在两个不相邻位置、且中间文字属于旁支或另一作品时，填写一至两个 `passage_fragments`；必须是本来源全文中按顺序、互不重叠的逐字原文。`passage_positions` 可省略，由脚本按原文顺序定位；原文中存在重复片段、需要消歧时再提供精确位置。第二观点使用 `secondary_passage_fragments` 与可选的 `secondary_passage_positions`。这些片段进入集合及成员指纹，并优先成为最终摘录候选，不能手工把两段改写成一个伪造连续句。

`negated_sentiment_requires_review` 表示当前片段含否定情绪词、双重否定或条件表述，词表不能确认立场。不能把“不出戏”中的“出戏”直接作为负面证据，也不能把“希望以后不出戏”自动当作既有好评。只对当前原文判断立场，明确提交 `passage_stance` 与逐字证据；来源不因此无效，已完成的来源全文复核不重跑。无此风险的直接评价保留原自动路径。

当前模板只包含本轮最多60条残差，可以只提交当轮记录。控制器维护绑定当前保留池和簇定义的 `cluster_overrides.ledger.json`，自动合并此前已通过记录；当前同一来源ID的提交优先。执行模型不读取或复制账本，也不用维护累计大文件。

明确不应进入工作台的来源可使用：

```json
"稳定来源ID": {
  "cluster": "__exclude__",
  "exclude_reason_code": "cross_work_mismatch",
  "reason": "正文人物和情节属于另一作品，无法形成目标作品观点",
  "exclusion_evidence": "本来源全文中的连续逐字冲突证据"
}
```

`exclude_reason_code` 仅允许 `out_of_scope`、`no_reportable_viewpoint`、`cross_work_mismatch`、`abusive_non_viewpoint`、`promotion_only`、`duplicate_or_corrupt`。这只是展示层排除，来源仍留在筛选审计中；不得用它缩减合格独立表达。

排除证据优先写入 `exclusion_evidence`；为兼容不同执行模型，也接受同义字段 `evidence`。内容必须从该来源全文连续逐字复制，不能写总结。控制器会在正式归簇前一次列出全部缺字段、非法代码、未知簇和非逐字证据，并保持 `REVIEW_REQUIRED`，不会把这类可修复输入错误报成 `BROKEN`。

`cluster_review_queue.jsonl` 的 `issue` 用于说明复核原因：`passage_stance_conflict` 为局部立场与簇冲突，`negative_cluster_missing_negative_cue` 为未复核负面窗口未命中辅助词，`no_positive_cluster_evidence` 或 `low_cluster_evidence` 为当前窗口缺少足够簇证据，`multi_work_passage_requires_review` 为同段涉及多作品，`no_passage_target_anchor` 为局部未重复作品锚点。两个相近观点分差小时默认采用得分更高者，不单独阻塞；代表样本审核仍会检查簇边界。提供精确 `anchor_terms` 时，脚本优先选择含锚点的窗口，再用同来源 `target_evidence` 处理作品归属。

自动负面归簇若没有有效 `negative_cues`，且局部态度仅为 `deferred_to_independent_member_review`，同样进入 `negative_cluster_missing_negative_cue` 队列。辅助词仍可选，不强制造词；这里只前移模糊项的AI确认，不自动改立场或排除来源。明确局部负向、已有AI修正、正面和客观簇不受此新增条件影响。

转簇小补丁的观点菜单也可引用当前 `workbench_dataset.staged.json` 中的原始摘录 provenance；该数据进入计划 guards。仅作品/来源/簇/view一致、清理后展示及句段完全对应当前请求、原始片段位置逐字有效时，增加 `origin: current_view_raw_provenance` 的原文编号。它只解决清洁文本不连续或重复原文的位置传输，不能确认归簇、态度或作品关系；变动/无效 provenance 不复用。

转簇补丁 apply 在写入前调用与普通归簇相同的作品、方面、态度和评分门槛，失败时返回来源级问题，原归类表不变。只在当前补丁修复并重新 apply，不因预检失败改整张表或重做来源清洗。这是检查前移，不降低门槛；apply通过也不等于最终摘录通过。原文确实用同义表达支持该方面时，AI可填写有解释的逐字 `anchor_terms`；不得用人物名或随意词凑分。

## `excerpt_reviews.json`

```json
{
  "scope": "current_period_verbatim_excerpt_reviews",
  "reviews": {
    "来源ID::簇ID": {
      "fragments": ["原文逐字片段一", "原文逐字片段二"],
      "positions": [[12, 48], [96, 137]],
      "reason": "保留完整判断和同主题支撑"
    }
  }
}
```

最多两个片段；必须来自同一来源正文、顺序不变、语义仍指向同一簇。原文存在重复段落时填写 `positions` 指定准确字符位置。脚本拒绝任何增字、改写、乱序、重叠或无法定位的片段。

控制器为当前有效重截生成只读 `_verbatim_repair_scopes`，绑定该条原文、当前簇定义、本期配置及实际修改内容。无关材料换组时仅保留仍匹配的逐条修改，并留档旧提交；原文/定义/配置/修改变更、已移出簇、跨工作流或没有有效旧绑定的提交不沿用。此处保留的是逐字截取请求，不是keep结论；实际渲染后仍走当前摘录语义验收。不要手填这些脚本元数据。

页面展示前会执行当前 `remove_social_markup_v3`：仅删除话题标签、账号标记、平台表情、链接和共创视频等非观点标记，并在 `excerptProvenance.displayNormalization` 中记录版本。`fragments/positions` 仍保存未经改写的原文，展示文字必须能由这些原文片段按该规则复算。

## `final_excerpt_semantic_reviews.json`

```json
{
  "scope": "current_period_final_excerpt_semantic_reviews",
  "_workflow": {"workflow_id": "控制器预填，保持不变", "input_sha256": "控制器预填，保持不变"},
  "reviews": {
    "来源ID::簇ID": {"decision": "keep", "evidence_candidate_index": 1}
  }
}
```


该文件只包含脚本无法高置信确认的最终展示文字。脚本会依次尝试已选片段和后续候选，自动修复能够通过换候选解决的边界、标记、长度和引号问题。对于70—150字、句段完整，并且同一局部句段同时包含具体方面与单一立场、没有转折反向、对象不明、跨作品、促销或无关前缀风险的摘录，脚本直接生成可审计的保留决定。方面和态度分散在不同句段时仍可保留，但必须进入AI复核确认语义关系。`群像、竞争、白月光、特效、制作、质感` 等宽泛词可以召回候选，不能单独触发脚本自动通过。其余项目每轮最多80条，同时把单批文件限制在90KB以内。

保持 `_workflow` 不变；控制器只对匹配当前输入的简写提交回填逐条 `review_fingerprint`，不会把旧判断绑定到变化后的原文。普通 `keep` 只填决定与 `evidence_candidate_index`，一个编号同时引用方面和立场证据。keep 明确表示AI已阅读并确认作品、核心方面、局部立场和完整性；控制器补齐指纹、立场和确认字段。原文支持不足时不可 keep。两项证据分散时可分填方面、立场编号；旧的完整字段提交仍受支持。脚本按编号回填逐字证据；候选均不适用时才可填写不带 `_candidate_index` 的逐字原文字段。只修改少数摘录时，控制器按条目指纹保留其余未变化的已审结果。

输入标记 `target_review_required:true` 时，模板另含预填的 `target_evidence_candidate_index`；简写 keep 同时声明目标关系已核实，脚本回填 `target_relation_passed`。目标证据必须来自 `target_evidence_segments`，并包含作品、节目、演员或角色锚点；通过仍要求该锚点与当前观点属于同一评价对象。目标只在标签、作品名单或综合盘点中出现时不得通过。输入标记 `work_consistency_review_required:true` 时，须确认方面证据评价目标作品，不能用另一作品的演员、角色或剧情支撑当前簇。`display_operational_promotion_markers` 或 `irrelevant_leading_segment_indexes` 非空时，当前展示段不能直接 keep，先在同一来源重截，确无合格片段再 drop。

输入标记 `cluster_claim_review_required:true` 时，简写 keep 包含对核心主张的确认；脚本回填 `cluster_claim_passed`，必要时另选 `cluster_claim_evidence_candidate_index`。摘录支持簇的核心评价方向即可，不要求每条证明标题全部并列细节。标题过度具体时先调整标题，不能因此逐条删样本。该标记主要用于只命中宽泛路由词或成员很少的观点簇；它是复核触发器，不是删除规则。无法支持当前标题时先尝试重截、转入更合适的簇或修正有数据依据的簇名。

清洁后的摘录优先为70—150字。短于70字时先回看全文，仍无法补足才填写短摘录例外和具体依据编号；具体依据须包含动作、台词、场景、数据或因果分析。作品标签、人物名及“好看、封神、绝了、笑点拉满、期待”等泛泛态度不算具体依据，批量复用同一个例外理由会被脚本拒绝。全文中没有可替换合格片段时使用 `decision:"drop"`。终审不设每簇样本数量规则。

提交不合格的具体字段和修复方法直接放在当前输入的 `submission_errors`。词表对方面、立场或短评依据存疑时，允许AI用 `reason` 一句话解释实际语义并保留，不必迎合词表；跨作品项也填写一句归属说明。正常样本不增加理由。证据须来自原文，只有连接词或名字不能充当评价证据。

跨作品证据的编号来自本条 `excerpt_segments`。生成页面时脚本将已选编号回填成证据文字，并记录 `selected_display_segment`；最终验收重新核对编号、展示句段和原文经既有清理后的逐字关系。直接填写的 `work_consistency_evidence` 则仍按原始片段逐字检查。二者都不能改写证据，也不能用编号代替模型对作品归属的判断。

若终审 drop 使实际非空簇数变化，控制器要求 `post_excerpt_cluster_count_reviews.json`。模板已预填当前指纹；AI只填写 `decision:"pass"`、空 `issues` 和具体理由，9—16以外另填 `exception_reason`。实际簇数与范围由脚本计算，不要求AI重复填写。

终审清空的簇由渲染器自然省略，完成后置数量复核即可；不要为使前后数字相同而删除簇定义，这会使归簇输入变化。已接受的逐条判断由控制器账本保存，当前编辑文件只包含本轮待审项。

`objective_subtype` 的合法值为 `neutral_fact`、`sentiment_distribution`、`balanced_observation`。客观簇标题含“记录”时，`report_role` 使用 `data_note`。`work_consistency_evidence` 必须是 `raw_excerpt` 的连续逐字子串（未提供该字段时使用拼接的 `excerpt_segments`），包括原有空格；不要把展示清洁后的句子误作未清洁原文。

## `*.merge-verification.json`

`merge` 自动生成累计工作台合并报告。`status` 必须为 `PASS`；`inputs` 记录每个输入文件的绝对路径、SHA-256、批次数、样本数、兼容校验模式、数据格式分布和失败数；`batch_sources` 记录最终每个批次来自哪个输入；`batches/items` 是合并后的总数；`backup` 是被替换工作台的备份路径；`remote_assets` 必须为零。若任何输入、合并数据或最终 HTML 校验失败，报告写为 `FAIL` 并记录 `output_not_replaced`，目标文件不得被覆盖。

按ID回查原文时，使用状态中的 `operator_contract.source_lookup_command`。可在 `--source-id` 后传入多个ID。返回的 `source_text` 是完整、未截断的统一原文，位置为从0开始、右端不含的 `[start,end)`；不再自行猜测 `id/source_id` 或 `body/text` 字段，也不手工重组标题和正文。该查询不改变工作流状态。
