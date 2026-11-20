# 输入与执行契约

## config.json

最小实际配置如下。所有路径由本次文件填写，skill包不内置任何用户数据或本机绝对路径。

```json
{
  "work_name": "作品名称",
  "work_type": "电视剧",
  "current_heat": "本期热度分析.xlsx",
  "previous_trend": "上期热度分析.xlsx",
  "weibo_topics": "本期全网热搜分析.xlsx",
  "opinions": {
    "path": "本期清洗输出/delivery/主要舆论观点.docx",
    "start_heading": "二、主要舆论观点",
    "end_heading": "三、"
  },
  "reference_report": null,
  "node_samples": null,
  "topic_window": {"start": "2026-10-23 00:00", "end": "2026-10-25 23:59"},
  "topic_snapshot_at": null,
  "sentiment_method": "editorial_estimate"
}
```

previous_trend、reference_report、node_samples、topic_window、topic_snapshot_at均可省略。reference_report日常留空，使用内置Word模板；仅为用户明确要求更换版式保留兼容入口，不要求本期人工成稿。任何参考稿均不能作为本期统计或事件证据。work_type使用电视剧、网络剧或综艺等实际称呼。current_heat要求xlsx；previous_trend支持xlsx和本skill输出trend.json，用于合并日趋势数值。观点稿支持docx、txt；docx节选用标题前缀定位，缺失标题会报错，不退回读取整篇。纯观点文件不需要节选参数。

使用本期二期自动交付时，以 `cleaning_handoff` 替换上例的 `opinions`，可另配独立的 `sentiment_statistics`，见[清洗流程交接](cleaning-handoff.md)。手工提供本期观点稿时，`start_heading` 按实际文件标题填写。上例 `editorial_estimate` 表示编辑研判，缺少可靠依据和比例时保持缺项。

可用sheet_names覆写默认工作表：{"daily":"日趋势","hourly":"小时趋势","topics":"热搜话题分析"}。程序在前30行寻找字段，不依赖固定列号。日趋势必须有日期、热度；小时趋势必须有日期；热搜表必须有标题、榜单、阅读量（万）。字段中空格、全半角括号会规范化。数字中的千位逗号允许，缺失、负数和未缓存公式不能视为零。所有统计使用原始精度，最后显示才舍入。

topic_window是用户提供的检索范围，不能从话题最近上榜时间推断完整检索范围。topic_snapshot_at未知时保留null，文字写“按本期热搜导出表统计”；程序会标出晚于传播截止时间的话题。读取的是话题累计阅读量快照，不是窗口内新增阅读量。

## 节点事件证据

node_samples.json是JSON数组。最小样本结构：

```json
[
  {
    "id": "web_001",
    "date": "2026-10-25",
    "published_at": "2026-10-25 12:00",
    "platform": "新浪微博",
    "author": "发布者",
    "text": "可供核对的原文",
    "url": "https://weibo.com/实际帖子地址",
    "provenance": {
      "kind": "web_search",
      "source": "实际公开搜索及正文读取工具与调用标识",
      "query": "实际作品名与节点日期检索词",
      "retrieved_at": "2026-10-25 13:00",
      "verification": "page_content"
    }
  }
]
```

默认使用kind=web_search，按[公开搜索规则](node-search.md)保存真实query、retrieved_at、verification=page_content及实际工具source，并提供原文url。公开新闻、平台或官微动态均可；正文需已核验，发布时间不晚于报告截止。程序验证必要字段及时间，来源真实性与内容支持关系仍须审阅。也兼容kind=approved_mcp或user_provided。MCP记录的source应含实际工具名或调用记录标识；额外保存query_scope、task_id、filters、returned_record_count与total_volume等原字段。记录数不能放入total_volume。样本不能只有链接没有正文；date为节点日，published_at为来源实际发表时间。早发来源如有可核验的节点日传播记录，需填activity_at，并在text保存含完整日期及时分的连续原文。活动可早于节点日，传播记录须在节点日且不晚于报告截止。来源真实性仍需AI审阅。网页访问控制不由样本url授予。

## review.json

prepare生成已绑定输入指纹的模板。只填写该文件，不修改prepared.json或原始packet。

- topics：每个唯一话题标题一项，decision填include、exclude或uncertain。reason写具体归属依据，尤其注意只有演员名字、代言或其他作品的标题。跨榜同名话题共享一次语义判断。
- directions：电视剧、网络剧默认3项，结构为{"label":"剧情内容","topic_ids":["实际ID"]}。label为简短话题类别，以四字为主，允许必要的三字、五字常用词；类别尽量不重叠。必须引用已纳入话题，不以空标签凑数，材料不足3类时保留待核。综艺仍按本期材料概括至少3类。详见[话题规则](review-rules.md#话题)。
- nodes：每项date、summary、evidence_ids，可选include_peak和include_daily_volume（均为布尔值）。summary自行表达“有据原因＋热度变化”，按[人工稿写法](node-style.md)自然衔接，不重复节点日期，不手填周期峰值或传播量；可按需要保留少量有据细节。以“周期内，”或“从传播趋势看，”开头时，程序不再重复加日期前缀。include_peak默认true，仅当该日确为整个滚动统计周期最大值时追加峰值表述；设false可省略峰值表述。凡输出峰值，程序同时输出该日单日传播量。其余节点的include_daily_volume默认false，确有叙事需要时设true，由程序取数。后台仍兼容旧reason字段，但新写作优先填完整summary。evidence_ids须指向支持该节点日传播情况的已输入证据，缺证时summary和数组留空，不能用数字补位。
- sentiment：method与config一致；positive、neutral、negative为百分数，合计100。summaries中填三类概述，paragraph_ids引用审阅包内的本期观点正文、工作台摘录或统计样本ID，rationale解释估算依据。自动导入的计数、分母及范围从统计文件读取，比例由脚本换算。
- sentiment.summaries直接包含衔接词和观点，不重复态度类别、占比。电视剧、网络剧正面默认“舆论主要肯定……”，中立“舆论热议／关注／分析……”，负面占比较低时“少量舆论认为／吐槽……”。按本期观点与占比选择措辞，详见[情感规则](review-rules.md#情感)。
- 情感观点数量默认positive为3—4个、neutral为1—2个、negative为2—3个独立意思；先在审阅记录列出选中的观点及段落证据，再填summaries和paragraph_ids。材料不足时少写并说明，不按标点或引用数量凑数。每类概述仅保留选中的主要观点，省去例子和延伸说理；此规则不改变percentages的来源和计算。
- measured额外填写counts（positive/neutral/negative）、denominator和count_source；必须基于完整、同一范围的已分类样本，计数合计等于分母。程序由计数计算百分比，不接受另填一套不符的比例。
- provided额外填provided_source，指明谁或哪份输入提供了占比。已有成稿作为给定输入时只能称复现验证，不称独立测算。
- headline：整条标题含固定前缀不超过50字。电视剧、网络剧默认采用“两类推动因素＋热度判断＋积极正面声音占据主流”，选词及材料不足时的处理见[标题规则](review-rules.md#标题与审阅结果)。

## 状态

prepare → awaiting_review。build → complete或draft。未填话题决策和标题等机械错误会拒绝构建；uncertain、缺波峰样本、缺情感比例、情感比例与负面阈值冲突等可生成明确标注的draft。

build计算时重新核验输入哈希和审阅指纹。修改输入后必须换工作区prepare。修改review.json后重新build；verify会拒绝“旧输出＋新审阅”。build只改本工作区deliverables，不覆盖输入文件。
