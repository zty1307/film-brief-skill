"""Import only opinion paragraphs from a previously delivered Word report."""
import re
from xml.etree import ElementTree as ET
from zipfile import BadZipFile, ZipFile


REPORT_SCOPE = 'PUBLISHED_REPORT_OPINIONS'
W = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
HEADING = re.compile(r'^(?:(?:[一二三四五六七八九十0-9Ⅱ]+[、.．：:]|第[一二三四五六七八九十0-9]+部分)\s*)?(?:主要)?舆[论情](?:观点|评价)(?:\s*[：:]|\s|$)')
END_SECTION = re.compile(r'^(?:[一二三四五六七八九十0-9]+[、.．]\s*)?(?:附(?:录|件|典型样本)|风险研判|总体传播特征|舆情风险|处置建议|工作建议|结语)')
POINT = re.compile(r'^(?:[一二三四五六七八九十]+是\s*|[（(]?[0-9一二三四五六七八九十]+[）)、.．]\s*|此外[，,:：]\s*)')
NOTE = re.compile(r'^(?:注[：:]|图\s*[一二三四五六七八九十0-9]|https?://)')
NAMED_SOURCE = re.compile(r'(?P<name>[^\s、；;：:，,。！？!?“”「」]{1,24}[“「][^”」\n]{1,80}[”」])\s*(?:称|认为|表示|指出|写道|评论|提到|直言|吐槽)[，,:：]\s*')
SOURCE_VERB = re.compile(r'\s*(?:称|认为|表示|指出|写道|评论|提到|直言|吐槽)[，,:：]\s*')
WORK_TITLE = re.compile(r'《([^》]+)》')
REPORT_TITLE = re.compile(r'^(?:(?:电视剧|网络剧|剧集|短剧|电影|纪录片|综艺(?:节目)?|节目)\s*)?《([^》]+)》(?:\s*(?:(?:舆情|舆论)(?:分析|简报|报告)|主要舆论观点))?$')


def paragraph_parts(paragraph):
    text, bold_spans = '', []
    for run in paragraph.iter(W+'r'):
        value = ''.join((node.text or '') if node.tag == W+'t' else ' ' if node.tag in (W+'br', W+'tab') else ''
                        for node in run)
        if not value:
            continue
        is_bold = run.find(f'{W}rPr/{W}b')
        if is_bold is not None and is_bold.get(W+'val') not in ('0', 'false', 'off'):
            if bold_spans and bold_spans[-1][1] == len(text):
                bold_spans[-1] = (bold_spans[-1][0], len(text)+len(value))
            else:
                bold_spans.append((len(text), len(text)+len(value)))
        text += value
    return text, bold_spans


def source_boundaries(text, bold_spans):
    boundaries = [(found.start('name'), found.end(), found['name']) for found in NAMED_SOURCE.finditer(text)]
    for start, end in bold_spans:
        # Some editors bold the claim and the first attribution in one run.
        suffix = re.search(r'[^\s、，,。！？!?；;：:]{2,24}(?:[“「][^”」]+[”」])?\s*$', text[start:end])
        if not suffix:
            continue
        name_start = start + suffix.start()
        verb = SOURCE_VERB.match(text, end)
        if verb and not any(s <= name_start < e for s, e, _ in boundaries):
            boundaries.append((name_start, verb.end(), suffix[0].strip()))
    return sorted(boundaries)


def report_groups(parts):
    current = None
    for text, bold in parts:
        stripped = text.strip()
        if not stripped or NOTE.match(stripped):
            continue
        if END_SECTION.match(stripped) or re.match(r'^[三四五六七八九十]+、', stripped):
            break
        leading = len(text)-len(text.lstrip())
        mark = POINT.match(stripped)
        if mark:
            if current:
                yield current
            current = [(text, bold, leading+mark.end())]
        elif current:
            current.append((text, bold, leading))
    if current:
        yield current


def sample_data(chunks):
    lead, bold, start = chunks[0]
    boundaries = source_boundaries(lead, bold)
    prefix = lead[start:(boundaries[0][0] if boundaries else len(lead))].strip()
    claim = re.split(r'[。！？!?]', prefix, maxsplit=1)[0].strip()
    if not claim:
        return '', []
    evidence = []
    for index, (text, spans, pos) in enumerate(chunks):
        markers = boundaries if index == 0 else source_boundaries(text, spans)
        if markers:
            for j, (_, right, name) in enumerate(markers):
                end = markers[j+1][0] if j+1 < len(markers) else len(text)
                quote = text[right:end].strip()
                if len(quote) >= 12:
                    evidence.append((quote, name))
        elif index == 0:
            rest = text[pos+text[pos:].find(claim)+len(claim):].lstrip('。！？!? \t')
            if len(rest.strip()) >= 12:
                evidence.append((rest.strip(), ''))
        elif len(text.strip()) >= 12:
            evidence.append((text.strip(), ''))
    # An editorial paragraph can legitimately summarize views without attribution.
    if not evidence and len(claim) >= 12:
        evidence.append((claim, ''))
    return claim, evidence


def check_work(path, target, preface, opinions, core_title):
    named = set(WORK_TITLE.findall(path.stem)) | set(WORK_TITLE.findall(core_title))
    named.update(match[1] for text, _ in preface if (match := REPORT_TITLE.fullmatch(text.strip())))
    if named and named != {target}:
        raise ValueError(f'历史Word的作品名与《{target}》不一致：{path.name}')
    if not named and target not in path.stem and not any(f'《{target}》' in text for text, _ in opinions):
        raise ValueError(f'无法核对历史Word的作品名，请使用标题或文件名含《{target}》的同剧报告：{path.name}')


def read_published_report(path, target):
    try:
        with ZipFile(path) as doc:
            content = ET.fromstring(doc.read('word/document.xml'))
            core = ET.fromstring(doc.read('docProps/core.xml')) if 'docProps/core.xml' in doc.namelist() else None
    except (BadZipFile, KeyError, ET.ParseError) as exc:
        raise ValueError(f'无法读取历史Word报告正文：{path.name}') from exc
    body = content.find(W+'body')
    if body is None:
        raise ValueError(f'历史Word没有可读取的正文：{path.name}')
    parts = [paragraph_parts(paragraph) for paragraph in body.iter(W+'p')]
    first = next((i for i, (text, _) in enumerate(parts) if HEADING.match(text.strip())), None)
    if first is None:
        raise ValueError(f'历史Word缺少「主要舆论观点」或「舆论观点」章节：{path.name}')
    chunks = list(report_groups(parts[first+1:]))
    core_title = core.findtext('{http://purl.org/dc/elements/1.1/}title', default='') if core is not None else ''
    check_work(path, target, parts[:first], [part[:2] for group in chunks for part in group], core_title)
    groups = []
    for index, chunk in enumerate(chunks, 1):
        claim, evidence = sample_data(chunk)
        if not claim or not evidence:
            continue
        if re.match(r'^(?:部分(?:舆论|声音|观众|网民)?\s*)?(?:吐槽|质疑|批评|担忧|诟病|不满)', claim):
            stance = '负向词倾向'
        elif re.match(r'^(?:肯定|认可|称赞|点赞|赞扬|赞赏|赞誉|看好|盛赞)', claim):
            stance = '正向词倾向'
        elif re.search(r'[，、；]\s*(?:吐槽|质疑|批评|担忧|诟病)', claim):
            stance = '负向词倾向'
        else:
            stance = '中性或混合'
        samples = []
        for serial, (text, name) in enumerate(evidence, 1):
            ref = f'report-{index}-{serial}'
            source = {'data':{'正文内容':text, '作者':name}}
            samples.append({'candidate_id':ref, 'source':source,
                            'display_excerpt':text, 'excerpt':text})
        groups.append({'aspect':claim, 'stance':stance, 'samples':samples})
    if not groups:
        raise ValueError(f'历史Word的观点章节里没有可比较的编号段或「此外」段：{path.name}')
    return {'target':target, 'groups':groups, 'historical_import_scope':REPORT_SCOPE}
