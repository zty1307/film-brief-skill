# 文件契约

使用 `film_workflow.py` 的生产运行时，控制器直接在状态文件指定的 `required_file` 生成当前可填写提交。执行者只补全预留字段，不复制模板、不手工新建评审文件。文件自带 `_workflow`，其中记录本次工作区 ID 和评审输入指纹；填写时必须原样保留。下列示例为便于阅读省略了 `_workflow`，不能据此另建无绑定文件。

## `period_config.json`

```json
{
  "batch_order": ["剧名 第一期"],
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

旧字段 `cluster_min_independent_sources`、`cluster_max_sources_before_review`、`min_samples_per_cluster`、`max_samples_per_cluster`、`sample_budget`、`reading_budget` 和 `cluster_target_count` 已停用，出现时脚本直接报错。现行流程不按样本数量或阅读预算删减合格来源；9—16只触发整期观点簇颗粒度复查。

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

`decision` 仅允许 `retain_core`、`retain_consensus`、`exclude`。三种决定都必须提交逐字证据；`retain_core` 和 `retain_consensus` 无需重复写理由，`exclude` 另填简短具体理由。控制器每轮提供下一批不超过60条且不超过约120KB的精简记录；分片不重复整篇正文、统一回查说明、质量中间量或候选命中词。优先填写 `evidence_candidate_index`，脚本自动还原逐字原文及位置；候选均不适用时，才按 `workflow_status.full_source_file` 和 `source_id` 回查全文并填写 `evidence` 或 `evidence_position`。提交由脚本账本累计，执行模型不复制历史答案。

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

每个批次必须至少一个簇；ID在批次内唯一；`stance` 必须是 `positive`、`objective`、`negative` 之一；标题应是能直接理解的完整报告体观点句。“演员表现”“剧情张力”一类短标签不合格。每个非背景簇须在 `required_any` 填1—4个方面词或短语；作品名、演员名、角色名以及“剧情、热度、好看”等通用词不能单独承担归簇。作品名不增加观点得分，人物名只给极低辅助权重。负向簇可填写 `negative_cues` 辅助自动发现。客观舆情概况可设 `objective_meta: true`。不要求每个批次三种立场齐全，也不要求每簇达到任何样本数量。

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

页面展示前会执行当前 `remove_social_markup_v3`：仅删除话题标签、账号标记、平台表情、链接和共创视频等非观点标记，并在 `excerptProvenance.displayNormalization` 中记录版本。`fragments/positions` 仍保存未经改写的原文，展示文字必须能由这些原文片段按该规则复算。

## `final_excerpt_semantic_reviews.json`

```json
{
  "scope": "current_period_final_excerpt_semantic_reviews",
  "reviews": {
    "来源ID::簇ID": {
      "review_fingerprint": "模板预填值，保持不变",
      "decision": "keep",
      "aspect_evidence_candidate_index": 1,
      "stance": "positive",
      "stance_evidence_candidate_index": 2,
      "self_contained": true,
      "cluster_claim_passed": true,
      "cluster_claim_evidence_candidate_index": 1
    }
  }
}
```

该文件只包含脚本无法高置信确认的最终展示文字。脚本会依次尝试已选片段和后续候选，自动修复能够通过换候选解决的边界、标记、长度和引号问题。对于70—150字、句段完整，并且同一局部句段同时包含具体方面与单一立场、没有转折反向、对象不明、跨作品、促销或无关前缀风险的摘录，脚本直接生成可审计的保留决定。方面和态度分散在不同句段时仍可保留，但必须进入AI复核确认语义关系。`群像、竞争、白月光、特效、制作、质感` 等宽泛词可以召回候选，不能单独触发脚本自动通过。其余项目每轮最多80条，同时把单批文件限制在90KB以内。

每条复核保留模板预填的 `review_fingerprint`。普通 `keep` 只填决定、方面证据编号、立场、立场证据编号和 `self_contained:true`，无需填写理由或复制原文。脚本按编号回填逐字证据；候选均不适用时才可填写不带 `_candidate_index` 的逐字原文字段。只修改少数摘录时，控制器按条目指纹保留其余未变化的已审结果。

输入标记 `target_review_required:true` 时，模板另含 `target_relation_passed` 和预填的 `target_evidence_candidate_index`。目标证据必须来自 `target_evidence_segments`，并包含作品、节目、演员或角色锚点；通过仍要求该锚点与当前观点属于同一评价对象。目标只在标签、作品名单或综合盘点中出现时不得通过。输入标记 `work_consistency_review_required:true` 时，须确认方面证据评价目标作品，不能用另一作品的演员、角色或剧情支撑当前簇。`display_operational_promotion_markers` 或 `irrelevant_leading_segment_indexes` 非空时，当前展示段不能直接 keep，先在同一来源重截，确无合格片段再 drop。

输入标记 `cluster_claim_review_required:true` 时，模板另含 `cluster_claim_passed` 和 `cluster_claim_evidence_candidate_index`。只有摘录能直接支持 `cluster_title`、无需分析者补充缺失推理时才能设为通过。该标记主要用于只命中宽泛路由词或成员很少的观点簇；它是复核触发器，不是删除规则。无法支持当前标题时先尝试重截、转入更合适的簇或修正有数据依据的簇名。

清洁后的摘录优先为70—150字。短于70字时先回看全文，仍无法补足才填写短摘录例外和具体依据编号；具体依据须包含动作、台词、场景、数据或因果分析。作品标签、人物名及“好看、封神、绝了、笑点拉满、期待”等泛泛态度不算具体依据，批量复用同一个例外理由会被脚本拒绝。全文中没有可替换合格片段时使用 `decision:"drop"`。终审不设每簇样本数量规则。

若终审 drop 使实际非空簇数变化，控制器要求 `post_excerpt_cluster_count_reviews.json`。模板已预填当前指纹；AI只填写 `decision:"pass"`、空 `issues` 和具体理由，9—16以外另填 `exception_reason`。实际簇数与范围由脚本计算，不要求AI重复填写。

## `*.merge-verification.json`

`merge` 自动生成累计工作台合并报告。`status` 必须为 `PASS`；`inputs` 记录每个输入文件的绝对路径、SHA-256、批次数、样本数、兼容校验模式、数据格式分布和失败数；`batch_sources` 记录最终每个批次来自哪个输入；`batches/items` 是合并后的总数；`backup` 是被替换工作台的备份路径；`remote_assets` 必须为零。若任何输入、合并数据或最终 HTML 校验失败，报告写为 `FAIL` 并记录 `output_not_replaced`，目标文件不得被覆盖。
