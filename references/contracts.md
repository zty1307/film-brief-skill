# 文件契约

使用 `film_workflow.py` 的生产运行时，所有 AI 评审文件都从当前工作区 `review_templates` 中的对应模板复制。模板自带 `_workflow`，其中记录本次工作区 ID 和评审输入指纹；填写内容时必须原样保留该对象。下列示例为便于阅读省略了 `_workflow`，不能据此手工新建一个无绑定的生产评审文件。

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

批次名称与原始数据子目录一致。`strong_terms` 只写能够独立确认目标作品的全名、完整节目名、官方专属话题，或从当期标题和话题标签实际确认的唯一简称；`weak_terms` 写可能泛指题材或其他内容的简称，只能触发语义复核；`auxiliary_terms` 写演员、嘉宾、主创和角色名，只辅助理解，不独立触发准入；`comparison_terms` 记录同期文章中容易混入的其他作品。旧版 `terms/regex` 已停用，防止人物名单或泛称被无意当成强锚点。若导出文件名自带“起始时间至结束时间”，脚本会据此判定期内；否则必须填写 `period_windows`。只有确认输入本身已经严格限定期次时，才可设置 `assume_all_in_period: true`。

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

`check_links.py` 必须覆盖 `normalized_sources.jsonl` 中全部非空URL；`select --link-health` 缺少任一链接结果时拒绝继续。只有两次GET均返回404或410的 `confirmed_dead` 会硬排除来源。`reachable` 只说明检查时能访问；`indeterminate` 包括401、403、429、登录墙、反爬、5xx、DNS错误和超时，不能据此删除。`blocked` 不发起网络请求，禁用域名及重定向到禁用域名均按此记录。

## `source_reviews.json`

```json
{
  "scope": "current_period_source_fulltext_reviews",
  "reviews": [
    {
      "source_id": "稳定来源ID",
      "decision": "retain_core",
      "reason": "原文包含具体观后判断及支撑细节",
      "evidence_position": [128, 196],
      "episode_scope": "latest_episode",
      "episode_evidence": "原文中的本期嘉宾、选手、环节或话题证据",
      "prominence_basis": "仅 previous_episode_prominent 时填写"
    }
  ]
}
```

`decision` 仅允许 `retain_core`、`retain_consensus`、`exclude`。三种决定都必须填写当前来源专属的 `reason`，并提供连续逐字 `evidence` 或 `[start,end]` 形式的 `evidence_position`；`exclude` 的证据应直接支撑“跨剧、无观点、纯推广、期次不符”等排除理由。位置基准是队列给出的 `evidence_source_text`：普通原帖通常为标题与正文的无重复拼接，评论和转帖为其自身文字。`prepare` 同时给出候选片段和字符位置，优先直接复制候选位置，避免漏掉 emoji、空格或中间句。来源复核文件必须覆盖 `source_review_queue.jsonl` 的全部项目；控制器会在耗时的链接检查之前生成 `source_review_validation.json`，一次列出全部缺失、越界或字段错误。低于 `quality_floor` 但已经具备目标、判断和依据的来源进入 `retain_consensus`，质量分只影响排序。

## `dedup_reviews.json`

```json
{
  "scope": "current_period_copy_reviews",
  "reviews": [
    {
      "left_id": "来源ID一",
      "right_id": "来源ID二",
      "decision": "independent",
      "reason": "观点相近，但论据、措辞和作者表达独立"
    }
  ]
}
```

`decision` 仅允许 `same_copy` 或 `independent`。高阈值文本重复默认合并；中等相似候选默认保留，AI确认共享同一稿件骨架后才合并。

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

每个批次必须至少一个簇；ID在批次内唯一；`stance` 必须是 `positive`、`objective`、`negative` 之一；标题应是能直接理解的完整报告体观点句，并按实现要求以报告谓语开头，或在逗号后的判断分句使用明确谓语。“演员表现”“剧情张力”一类短标签不合格。负向簇可填写 `negative_cues` 辅助自动发现；它只用于未复核条目的路由，AI已用 `passage_stance` 和逐字证据确认的负面片段不会因未命中固定词表而被否决。客观舆情概况可设 `objective_meta: true`。不要求每个批次三种立场齐全，也不要求每簇达到任何样本数量。

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
      "title_claims_passed": true,
      "title_claims": [
        {
          "claim": "演员表演自然生活化",
          "supporting_source_ids": ["来源ID一", "来源ID二"]
        }
      ],
      "member_support": {
        "来源ID一": {
          "claim_indices": [0],
          "evidence": "公安系统里每一个不被大众熟知的岗位，都被镜头温柔且细致地记录下来"
        },
        "来源ID二": {
          "claim_indices": [0],
          "evidence": "剧中每个警种都有涉及，每个人物的刻画都紧贴现实"
        }
      },
      "stance_purity_passed": true,
      "scope_purity_passed": true,
      "scope_reason": "均为播出后对当前剧情的评价",
      "granularity_passed": true,
      "granularity_reason": "共同评价机制为生活化表演，人物仅作论据",
      "source_role_checked": true,
      "source_role_reason": "含独立媒体和网民表达，节目方内容仅作背景",
      "reason": "标题各项均有簇内证据，所有成员共同支持同一报告体观点"
    }
  },
  "count_reviews": {
    "剧名 第一期": {
      "fingerprint": "从 cluster_count_review_input.json 原样复制",
      "decision": "pass",
      "cluster_count": 11,
      "range_status": "within_range",
      "reader_load_reviewed": true,
      "overfragmentation_checked": true,
      "overbreadth_checked": true,
      "no_forced_merge_or_split": true,
      "exception_approved": false,
      "reason": "已检查过度切碎和大口袋簇，11个观点均有独立评价机制且阅读量适中"
    }
  }
}
```

第一次运行 `cluster` 后生成 `cluster_set_review_input.json`。审查键固定为“批次名 + 制表符 + 簇ID”，`fingerprint` 必须逐字复制本次输入；标题、立场或成员来源变化都会令旧审查失效。每个非空簇必须覆盖。`report_role` 仅允许 `report_point`、`subtopic`、`data_note`、`rare_signal`；`data_note` 必须使用 `objective` 立场。角色只帮助人工理解观点在报告中的用途，不形成页面层级，也不影响来源去留。

`title_claims` 要把簇标题中的并列判断拆开，每项至少列出一个实际簇内来源ID，并且所有 `supporting_source_ids` 的并集必须覆盖该簇全部成员。`member_support` 还必须逐条覆盖全部成员：`evidence` 是该成员当前分配片段中的连续原文，`claim_indices` 指向它实际支持的 `title_claims` 序号。脚本会核对原文包含关系、索引有效性和双向对应，单纯把全部ID复制进支持列表无法通过。分句分别有证据只证明标题覆盖，集合复核还必须确认成员共享同一评价关系或因果机制；不得用“并且”“也肯定”或“都提升吸引力”等宽泛结果连接互不推出的判断。聚类以当前分配片段为语义边界；全文只用于判定对象、范围、来源角色或重新截取，不能把当前片段没有表达的全文词语带入簇名。`stance_purity_passed` 检查局部摘录立场；`scope_purity_passed` 检查时间或期次纯度。电视剧的 `scope_type` 取 `pre_broadcast_expectation`、`current_broadcast_reaction`、`later_reputation`、`mixed_time_explicit`；曝光片段、路透或预告解读属于播前范围，不能按发布日期直接算作开播后观感。综艺取 `latest_episode`、`previous_episode_prominent`、`program_level_current`、`mixed_scope_explicit`。`granularity_passed` 同时检查大簇混合多个方面和小簇仅按人物、段子机械切碎；`source_role_checked` 检查官方、节目参与者、宣传稿与独立受众表达。客观簇还必须填写 `objective_purity_passed:true`，以及 `objective_subtype` 为 `neutral_fact`、`sentiment_distribution`、`balanced_observation` 之一。`data_note` 必须记录与本期分析相关的具体事实或背景，不能用“未形成完整评价”“当前片段中的相关事实”“当前片段直接表达正向/负向判断”等兜底标题收容难归类或无观点、无相关信息的片段。任何项未通过时先修改簇定义或成员归属，再重新生成指纹和审查，不能用解释文字绕过。

`count_reviews` 是页面生成前的整期簇数审查。脚本从全部非空最终簇、簇名、立场和成员计算批次指纹。普通单期9—16个簇时使用 `within_range`；低于9个或高于16个时分别使用 `below_range`、`above_range`，并设置 `exception_approved:true` 和不少于12个规范化字符的 `exception_reason`，具体说明为何继续拆分或合并会损害语义质量。每期都必须确认已经检查过度切碎、过度宽泛和机械调数。该审查不改变成员，也不能代替逐簇证据审查；脚本不会为了达标自动调整观点。`cluster_count_review_queue.unresolved.jsonl` 非空时，`render` 和 `verify` 均不得发布。

## `cluster_member_semantic_reviews.json`

```json
{
  "scope": "current_period_cluster_member_semantic_reviews",
  "reviews": {
    "剧名 第一期\tP01\t来源ID一": {
      "fingerprint": "从 cluster_member_semantic_review_input.json 原样复制",
      "decision": "pass",
      "target_passed": true,
      "title_support_passed": true,
      "stance_passed": true,
      "scope_checked": true,
      "source_role_checked": true,
      "supported_claim_indices": [0],
      "evidence": "当前分配片段中直接支撑簇名的连续原文",
      "reason": "说明这段原文如何支撑标题，而非只复述题材词"
    }
  }
}
```

该文件用于聚类完成后的独立成员级语义复核，应由未参与本轮簇命名的第二个AI执行，或在清空前轮推理上下文后重新审核。审查对象固定为“当前分配片段 + 当前簇名 + 当前立场”；全文只辅助确认对象、期次和来源角色，不能替当前片段补造观点。每条复核的 `fingerprint` 绑定簇名、立场、来源ID和片段，任何一项变化都会令旧审查失效。`evidence` 必须是当前片段的连续原文，`supported_claim_indices` 必须指向该簇的 `title_claims`。如果片段只与标题共享人物名或题材词，却没有表达同一判断，必须退回重归簇、缩窄标题或重截，不能填写 `pass`。`cluster_member_semantic_review_queue.unresolved.jsonl` 非空时，`render` 和 `verify` 均拒绝通过。

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

该文件是**累计复核文件**。每轮都在已有 `overrides` 上补充或修改，不能只提交当轮残差。控制器同时维护绑定当前保留池和簇定义的 `cluster_overrides.ledger.json`：即使其他AI误把用户文件替换成仅含本轮记录的版本，执行时也会把此前已通过记录合并回来，当前同一来源ID的提交优先。

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

`cluster_review_queue.jsonl` 的 `issue` 用于说明复核原因：`passage_stance_conflict` 为局部立场与簇冲突，`negative_cluster_missing_negative_cue` 为未复核负面窗口未命中辅助词，`small_top_two_margin` 为前两簇分差小，`no_positive_cluster_evidence` 或 `low_cluster_evidence` 为当前窗口缺少足够簇证据，`multi_work_passage_requires_review` 为同段涉及多作品，`no_passage_target_anchor` 为局部未重复作品锚点。提供精确 `anchor_terms` 时，脚本优先选择含锚点的窗口，再用同来源 `target_evidence` 处理作品归属。

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
      "decision": "keep",
      "target_passed": true,
      "target_evidence": "最终摘录中的目标作品或角色原文",
      "aspect_passed": true,
      "aspect_evidence": "最终摘录中的评价方面原文",
      "stance": "positive",
      "stance_evidence": "最终摘录中的明确判断原文",
      "work_consistency_passed": true,
      "work_consistency_evidence": "最终摘录中能够确认作品内部信息一致的原文",
      "self_contained": true,
      "short_excerpt_justified": false,
      "short_excerpt_reason": "",
      "specific_support_passed": false,
      "specific_support_evidence": "",
      "independent_opinion_passed": true,
      "opinion_evidence": "最终摘录中能够独立成立的判断原文",
      "reason": "摘录独立支持本簇，未混入相反立场"
    }
  }
}
```

该文件只审核最终展示文字，不得复制归簇阶段的 `passage_stance` 作为结论。输入逐条同时提供 `raw_excerpt`、`cleaned_excerpt`、`full_source_text` 和 `evidence_scopes`，无需猜测校验范围：`target_evidence`、`work_consistency_evidence` 必须是 `raw_excerpt` 的逐字子串，允许使用显示清洗前的话题标签确认作品；`aspect_evidence`、`stance_evidence`、`specific_support_evidence` 必须是 `cleaned_excerpt` 的逐字子串，确保读者实际看见的文字能够支撑观点。合格样本使用 `decision: "keep"`。清洁后的摘录优先为70—150字；超过150字禁止保留，短于70字时必须先回看全文，仍无法补足同观点依据才填写 `short_excerpt_justified:true` 和具体 `short_excerpt_reason`，同时必须填写 `specific_support_passed:true` 和最终摘录中的逐字 `specific_support_evidence`。作品标签、人物名及“好看、封神、绝了、笑点拉满、期待”等泛泛态度不算具体依据；全文再无信息时使用 `decision:"drop"`，并在 `failed_checks` 中填写 `evidence_specificity`。当输入中的 `promotion_markers` 非空时，还必须填写 `independent_opinion_passed:true` 和最终摘录中的逐字 `opinion_evidence`，纯抽奖、购票、报名、扫码、礼包、活动规则或演员资料不能通过。全文中没有可替换合格片段时，可使用 `{"decision":"drop","failed_checks":["aspect"],"reexcerpt_attempted":true,"reassignment_attempted":true,"reassignment_reason":"全文只有排播信息，不存在可转入其他簇的目标作品评价","reason":"回看全文后只找到排播信息，没有形成任何可用观点"}`。`failed_checks` 只允许 `target`、`aspect`、`stance`、`self_contained`、`work_consistency`、`evidence_specificity`；若包含 `aspect` 或 `stance`，必须填写 `reassignment_attempted:true` 和逐来源 `reassignment_reason`，说明为何无法转入其他现有簇或形成新簇；若包含 `work_consistency`，还须提供全文中的逐字 `conflict_evidence`。`reviews` 可使用以 `viewId` 为键的对象，也可使用每项带 `view_id` 的列表；列表不得误用 `source_id`。已审 `drop` 写入 `excerpt_semantic_review_rejected.jsonl` 并从工作台省略，不算未决项；脚本会剥离理由中的逐条引文后检查模板复用。终审不设每簇样本数量规则；缺少结构化失败项、缺少决定与理由或 keep 所需证据会阻止发布。簇级结果写入 `cluster_display_coverage_audit.json`。首次 `render` 可以在缺少该文件时生成 `excerpt_semantic_review_input.jsonl`；完成复核后再次运行。`verify` 会再次核对语义复核、字符位置、边界、引号和同来源片段重叠情况。

若终审 drop 使实际非空簇数变化，控制器要求 `post_excerpt_cluster_count_reviews.json`。每批次以 `post_excerpt_cluster_count_review_input.json` 中的 `fingerprint` 为准，填写 `decision:"pass"`、实际 `cluster_count`、`range_status`、`reader_load_reviewed:true`、`no_forced_merge_or_split:true` 和具体 `reason`；9—16以外另填 `exception_approved:true` 与具体 `exception_reason`。该文件只审核终审后实际结果，不修改终审前的 `count_reviews`。

## `*.merge-verification.json`

`merge` 自动生成累计工作台合并报告。`status` 必须为 `PASS`；`inputs` 记录每个输入文件的绝对路径、SHA-256、批次数、样本数、兼容校验模式、数据格式分布和失败数；`batch_sources` 记录最终每个批次来自哪个输入；`batches/items` 是合并后的总数；`backup` 是被替换工作台的备份路径；`remote_assets` 必须为零。若任何输入、合并数据或最终 HTML 校验失败，报告写为 `FAIL` 并记录 `output_not_replaced`，目标文件不得被覆盖。
