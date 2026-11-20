"""Keep model-written causes separate from fixed report wording and Excel counts."""
import re


def is_risk_topic(text):
    """Recognize explicit complaint and risk framing in default node content."""
    return bool(re.search(
        r'吐槽|弃剧|货不对版|工业糖精|差评|口碑(?:下滑|翻车|崩盘)|'
        r'(?:强行|硬)(?:凑|炒)\s*CP|为凑\s*CP硬|剧情争议|改编争议|唱衰|劝退',
        str(text), re.I,
    ))


def heat_ending(phrase):
    phrase = str(phrase).strip().rstrip('。')
    if phrase.startswith(('全网讨论', '带动', '推动', '拉动', '助推', '推高', '相关话题', '致舆情')) or phrase in (
        '热度迎来二轮高峰', '热度逐渐趋缓',
    ):
        return phrase
    if phrase == '热度回升':
        return '带动话题热度回升'
    if phrase in ('热度攀升', '热度走高', '热度升温', '热度持续走高', '热度升至阶段高位'):
        return '全网讨论' + phrase
    if phrase.startswith('热度'):
        return '助推舆论' + phrase
    raise ValueError('未识别的热度短语，须从Excel走势候选选择')


def heat_wording_error(text):
    if re.search(r'热度有所回升|当日(?:剧集|节目)?热度回升', text):
        return '回升用词库写法，如“带动话题热度回升”，避免“当日剧集热度有所回升”'
    return None


def prose_errors(text):
    text = str(text).strip()
    errors = []
    if not text or len(re.sub(r'\s', '', text)) > 60:
        errors.append('summary通常25—55字、最多60字（含标点，不计空白）；合并同类动作，保留有据原因和走势，不截字、不凑字')
    if re.search(r'Excel|MCP|WebFetch|本模型|该文|本文|据搜索|已核验|并置观察|检索结果|材料显示', text, re.I):
        errors.append('正文直接写事件和走势，不写取证、统计及成稿过程')
    if re.search(r'单日传播量|传播峰值|周期内峰值|\d[\d,.]*(?:万|亿)条', text):
        errors.append('周期峰值与单日量由程序核对滚动趋势后添加，不在summary重复手填')
    wording_error = heat_wording_error(text)
    if wording_error:
        errors.append(wording_error)
    if is_risk_topic(text):
        errors.append('业务节点优先写有据的正面亮点或中性传播因素，负面评价留待舆论观点或风险研判')
    return errors


def cause_errors(reason):
    reason = str(reason).strip()
    errors = prose_errors(reason)
    if re.search(r'热度|传播量|峰值', reason):
        errors.append('reason兼容字段只填原因，完整走势请填写summary')
    if re.fullmatch(r'(?:剧集|作品)?(?:上线|正式|当日|持续)?(?:开播|播出|更新)(?:后)?', reason):
        errors.append('只有开播或更新背景，缺少推动因素')
    return errors


def compose_summary(reason, phrase):
    reason = str(reason).strip().rstrip('，。 ')
    errors = cause_errors(reason)
    if errors:
        raise ValueError('；'.join(errors))
    return reason + '，' + heat_ending(phrase) + '。'


def normalize_reply(data, choices):
    """Keep complete editorial prose; reason-only replies remain compatible."""
    for key in ('include_daily_volume', 'include_peak'):
        if key in data and not isinstance(data[key], bool):
            raise ValueError(key + '必须为布尔值')
    if str(data.get('summary', '')).strip():
        errors = prose_errors(data['summary'])
        if errors:
            raise ValueError('；'.join(errors))
        return data
    if 'reason' not in data:
        return data
    phrase = data.get('trend_phrase')
    if phrase not in choices:
        raise ValueError('热度短语不在本次Excel候选中')
    return {**data, 'summary': compose_summary(data['reason'], phrase)}
