"""Offline, source-bound workflow for the first section of film/TV briefs."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import html
import json
from pathlib import Path
import re
import sys
import unicodedata
import zipfile
from node_selection import select_nodes
import xml.etree.ElementTree as ET

VERSION = '1.8.5'
PREFIX = '一、总体传播特征：'
KINDS = ('positive', 'neutral', 'negative')
LABELS = {'positive': '积极正面', 'neutral': '客观中立', 'negative': '消极负面'}
NEGATIVE_REPORT_RANGE = (5, 10)
ROOT = Path(__file__).resolve().parents[1]


class InputError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def sha_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def dump(path, value):
    path = Path(path)
    temporary = path.with_suffix(path.suffix + '.writing')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def load(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def decimal(value, where):
    if value is None or isinstance(value, bool):
        raise InputError(f'{where} 缺少有效数值，不能作为0')
    try:
        number = Decimal(str(value).strip().replace(',', '').replace('，', ''))
    except InvalidOperation as exc:
        raise InputError(f'{where} 不是有效数字: {value!r}') from exc
    if not number.is_finite() or number < 0:
        raise InputError(f'{where} 必须为有限非负数')
    return number


def integer(value, where):
    number = decimal(value, where)
    if number != number.to_integral_value():
        raise InputError(f'{where} 传播量或计数必须为整数')
    return int(number)


def format_volume(value):
    value = Decimal(str(value))
    if value >= 100000000:
        return str((value / 100000000).quantize(Decimal('.01'), rounding=ROUND_HALF_UP)) + '亿'
    return str((value / 10000).quantize(Decimal('.1'), rounding=ROUND_HALF_UP)) + '万'


def normalize(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(value or '')))


def parse_datetime(value, where, hourly=False):
    if isinstance(value, datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, date):
        return datetime.combine(value, datetime.min.time())
    text = str(value or '').strip().replace('/', '-').replace('T', ' ').rstrip('时')
    formats = ('%Y-%m-%d %H:%M:%S', '%Y-%m-%d %H:%M', '%Y-%m-%d %H')
    if not hourly:
        formats += ('%Y-%m-%d',)
    for fmt in formats:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            pass
    raise InputError(f'{where} 日期格式无法识别: {value!r}')


def sheet_table(path, sheet, required):
    import openpyxl
    from openpyxl.utils import get_column_letter
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        if sheet not in workbook.sheetnames:
            raise InputError(f'{path.name} 缺少工作表 {sheet}')
        ws = workbook[sheet]
        ws.reset_dimensions()
        header, mapping, records = None, {}, []
        for row in ws.iter_rows():
            values = [c.value for c in row]
            if not any(v is not None for v in values):
                continue
            if header is None:
                if row[0].row > 30:
                    break
                found = {normalize(v): i for i, v in enumerate(values) if v is not None}
                if all(normalize(k) in found for k in required):
                    header = row[0].row
                    mapping = {k: found[normalize(k)] for k in required}
                    optional = ('最近上榜时间', '最近下榜时间', '话题链接', '主持人', '分类')
                    mapping.update({k: found[normalize(k)] for k in optional if normalize(k) in found})
                continue
            fields = {key: values[index] if index < len(values) else None for key, index in mapping.items()}
            refs = {key: f'{sheet}!{get_column_letter(index+1)}{row[0].row}' for key, index in mapping.items()}
            records.append({'row': row[0].row, 'fields': fields, 'cells': refs})
        if header is None:
            raise InputError(f'{path.name}/{sheet} 前30行中没有字段 {required}')
        if not records:
            raise InputError(f'{path.name}/{sheet} 没有数据记录')
        return records
    finally:
        workbook.close()


def daily_series(path, sheet):
    if path.suffix.lower() == '.json':
        blob = load(path)
        rows = blob.get('series') if isinstance(blob, dict) else blob
        if not isinstance(rows, list):
            raise InputError('上期trend.json必须包含series数组')
        items = [{'date': parse_datetime(x.get('date'), f'trend.json[{i}]').date().isoformat(),
                  'heat': integer(x.get('heat'), f'trend.json[{i}].heat'),
                  'source_cell': f'series[{i}]'} for i, x in enumerate(rows)]
    else:
        rows = sheet_table(path, sheet, ('日期', '热度'))
        items = [{'date': parse_datetime(r['fields']['日期'], r['cells']['日期']).date().isoformat(),
                  'heat': integer(r['fields']['热度'], r['cells']['热度']),
                  'source_cell': r['cells']['热度']} for r in rows]
    seen = Counter(x['date'] for x in items)
    duplicates = [k for k, v in seen.items() if v > 1]
    if duplicates:
        raise InputError(f'日趋势有重复日期，不能自动求和: {duplicates}')
    return sorted(items, key=lambda x: x['date'])


def roll_forward(previous, current, report_date):
    report_date = date.fromisoformat(report_date)
    report_key = report_date.isoformat()
    if any(x['date'] > report_key for x in current):
        raise InputError('日趋势包含晚于小时趋势截止日期的数据')
    if not any(x['date'] == report_key for x in current):
        raise InputError('日趋势缺少报告当日数据')
    if not previous:
        return [dict(x, source_role='current_heat', operation='首报采用', previous_heat=None) for x in current]
    if any(x['date'] > report_key for x in previous):
        raise InputError('上期趋势包含晚于本期的日期，请检查输入')
    updates = {(report_date-timedelta(days=1)).isoformat(), report_key}
    lookup = {x['date']: x for x in current}
    if not updates.issubset(lookup):
        raise InputError('续报本期表必须提供前一日及当日热度')
    baseline = {x['date']: dict(x, source_role='previous_trend', operation='保留历史', previous_heat=x['heat']) for x in previous}
    for key in updates:
        old = baseline.get(key)
        baseline[key] = dict(lookup[key], source_role='current_heat', operation='替换' if old else '新增',
                             previous_heat=old['heat'] if old else None)
    return [baseline[k] for k in sorted(baseline)]


def read_text_source(spec):
    path = Path(spec['path'])
    if path.suffix.lower() == '.docx':
        with zipfile.ZipFile(path) as archive:
            root = ET.fromstring(archive.read('word/document.xml'))
        ns = {'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'}
        paragraphs = [''.join(n.text or '' for n in p.findall('.//w:t', ns)) for p in root.findall('.//w:body//w:p', ns)]
    elif path.suffix.lower() in ('.txt', '.md'):
        paragraphs = [x.strip() for x in re.split(r'\n\s*\n', path.read_text(encoding='utf-8-sig'))]
    else:
        raise InputError('观点稿仅支持docx、txt、md')
    start = spec.get('start_heading')
    stop = spec.get('end_heading')
    active = not start
    found_start = not start
    result = []
    for index, text in enumerate(paragraphs):
        if not text.strip():
            continue
        if not active:
            if normalize(text).startswith(normalize(start)):
                active, found_start = True, True
            continue
        if stop and normalize(text).startswith(normalize(stop)):
            break
        result.append({'id': f'p{index:04d}', 'text': text, 'source_location': f'正文段落{index+1}'})
    if not found_start or not result:
        raise InputError(f'{path.name} 指定章节不存在或为空，不能替换为整篇')
    return result


def source_check(prepared):
    for record in prepared['sources']:
        path = Path(record['path'])
        if not path.is_file() or sha_file(path) != record['sha256']:
            raise InputError(f'输入已变化或缺失: {record["role"]}，请新建工作区prepare')


def resolve_config(config_path):
    config_path = Path(config_path).resolve()
    config = load(config_path)
    config.pop('previous_report', None)  # Accept a saved configuration from the retired workflow.
    if config.get('cleaning_handoff'):
        if not config.get('work_name') or config.get('opinions'):
            raise InputError('自动交接须指定作品，观点稿由二期交付清单提供')
        from report_handoff import read_handoff
        config['cleaning_handoff'] = read_handoff(config['cleaning_handoff'], config_path.parent, config['work_name'])
        config['opinions'] = {'path': config['cleaning_handoff']['artifacts']['word']['path'],
                              'start_heading': '二、', 'end_heading': '附典型样本'}
    if config.get('sentiment_statistics'):
        config.setdefault('sentiment_method', 'measured')
        if not config.get('cleaning_handoff') or config['sentiment_method'] not in ('measured', 'editorial_estimate'):
            raise InputError('情感统计交接要求同一期 cleaning_handoff，以及 measured 或 editorial_estimate 方法')
        from report_handoff import read_measured_sentiment
        config['sentiment_statistics'] = read_measured_sentiment(
            config['sentiment_statistics'], config_path.parent, config['work_name'], config['cleaning_handoff'])
    for key in ('work_name', 'work_type', 'current_heat', 'weibo_topics', 'opinions'):
        if not config.get(key):
            raise InputError(f'config缺少 {key}')
    if config.get('sentiment_method', 'editorial_estimate') not in ('provided', 'editorial_estimate', 'measured'):
        raise InputError('sentiment_method无效')
    config.setdefault('sentiment_method', 'editorial_estimate')
    for key in ('current_heat', 'previous_trend', 'weibo_topics', 'opinions', 'node_samples', 'chart_font', 'reference_report'):
        if not config.get(key):
            continue
        obj = config[key]
        path = Path(obj['path'] if isinstance(obj, dict) else obj)
        path = path if path.is_absolute() else config_path.parent / path
        path = path.resolve()
        if not path.is_file():
            raise InputError(f'{key} 文件不存在: {path}')
        config[key] = dict(obj, path=str(path)) if isinstance(obj, dict) else str(path)
    return config


def prepare(config_path, workspace):
    config = resolve_config(config_path)
    workspace = Path(workspace).resolve()
    if workspace.exists() and any(workspace.iterdir()):
        raise InputError('prepare需要空工作区，不能覆盖已有审阅')
    sheets = {'daily': '日趋势', 'hourly': '小时趋势', 'topics': '热搜话题分析'}
    sheets.update(config.get('sheet_names', {}))
    sources = []
    for key in ('current_heat', 'previous_trend', 'weibo_topics', 'opinions', 'node_samples', 'chart_font', 'reference_report'):
        if config.get(key):
            value = config[key]
            path = Path(value['path'] if isinstance(value, dict) else value)
            sources.append({'role': key, 'path': str(path), 'sha256': sha_file(path)})
    hours = sheet_table(Path(config['current_heat']), sheets['hourly'], ('日期',))
    stamps = [parse_datetime(x['fields']['日期'], x['cells']['日期'], hourly=True) for x in hours]
    if stamps != sorted(stamps) or len(set(stamps)) != len(stamps):
        raise InputError('小时趋势日期必须按时间递增且不能重复，不能直接选最大值掩盖错序')
    cutoff = stamps[-1]
    handoff = config.get('cleaning_handoff')
    if handoff:
        if handoff['issue_date'] != cutoff.date().isoformat():
            raise InputError('二期交付期次与传播截止日期不同')
        if parse_datetime(handoff['sample_window']['end'], '二期样本窗口') > cutoff:
            raise InputError('二期样本窗口晚于本期传播截止时间')
        for kind, item in handoff['artifacts'].items():
            sources.append({'role': f'cleaning_{kind}', **item})
    statistics = config.get('sentiment_statistics')
    if statistics:
        sources.extend({'role': role, **item} for role, item in statistics['artifacts'].items())
    current = daily_series(Path(config['current_heat']), sheets['daily'])
    previous = daily_series(Path(config['previous_trend']), sheets['daily']) if config.get('previous_trend') else []
    trend = roll_forward(previous, current, cutoff.date().isoformat())
    raw_topics = sheet_table(Path(config['weibo_topics']), sheets['topics'], ('标题', '榜单', '阅读量（万）'))
    groups, board_rows, board_seen = {}, [], set()
    for record in raw_topics:
        fields = record['fields']
        if not fields['标题'] or not fields['榜单']:
            raise InputError(f'热搜表第{record["row"]}行缺少标题或榜单')
        title = str(fields['标题']).strip().strip('#').strip()
        key = normalize(title)
        topic_id = 't_' + hashlib.sha256(key.encode()).hexdigest()[:12]
        group = groups.setdefault(topic_id, {'id': topic_id, 'title': title, 'boards': [], 'rows': []})
        board = str(fields['榜单']).strip()
        if board not in group['boards']:
            group['boards'].append(board)
        group['rows'].append(record['row'])
        if board == '微博热搜榜':
            if key in board_seen:
                raise InputError(f'微博热搜榜重复话题 {title}，先确认重复数据口径，不能直接相加')
            board_seen.add(key)
            amount = decimal(fields['阅读量（万）'], record['cells']['阅读量（万）'])
            time = fields.get('最近上榜时间')
            later = False
            if time:
                later = parse_datetime(time, record['cells']['最近上榜时间']) > cutoff
            board_rows.append({'topic_id': topic_id, 'title': title, 'read_wan': str(amount), 'row': record['row'],
                               'source_cell': record['cells']['阅读量（万）'], 'latest_on_board': str(time or ''),
                               'later_than_volume_cutoff': later})
    if not board_rows:
        raise InputError('本期热搜表没有微博热搜榜记录')
    opinions_spec = config['opinions'] if isinstance(config['opinions'], dict) else {'path': config['opinions']}
    opinions = read_text_source(opinions_spec)
    if handoff:
        from report_handoff import opinion_inputs
        opinions = opinion_inputs(opinions, handoff)
    if statistics:
        opinions.extend(statistics['evidence'])
    samples = load(config['node_samples']) if config.get('node_samples') else []
    if not isinstance(samples, list):
        raise InputError('node_samples必须为数组')
    ids = set()
    for sample in samples:
        required = ('id', 'date', 'published_at', 'platform', 'author', 'text', 'provenance')
        if any(not sample.get(k) for k in required):
            raise InputError(f'波峰样本缺少必要字段: {required}')
        if sample['id'] in ids:
            raise InputError('波峰样本id重复')
        ids.add(sample['id'])
        stamp = parse_datetime(sample['published_at'], f'{sample["id"]}.published_at')
        if stamp.date().isoformat() != sample['date'] or sample.get('activity_at'):
            from node_search_pipeline import evidence_activity_time
            try:
                evidence_activity_time(sample, sample['date'], cutoff.isoformat())
            except (ValueError, TypeError) as exc:
                raise InputError(f'波峰样本 {sample["id"]}：{exc}') from exc
        provenance = sample['provenance']
        if provenance.get('kind') not in ('web_search', 'local_export', 'approved_mcp', 'user_provided') or not provenance.get('source'):
            raise InputError('波峰样本缺少可辨认的来源种类或出处')
        if provenance.get('kind') == 'web_search':
            from urllib.parse import urlparse
            if not re.search(r'\d{2}:\d{2}', str(sample['published_at'])):
                raise InputError('公开搜索证据须有可核验的发布时分，不能将日期默认作零点')
            parsed = urlparse(sample.get('url', ''))
            host = (parsed.hostname or '').lower().rstrip('.')
            if parsed.scheme not in ('http', 'https') or not host or host == 'ydata.woa.com' or host.endswith('.ydata.woa.com'):
                raise InputError('公开搜索证据须有可访问的公开原文链接，不接受屿海任务链接')
            if not all(provenance.get(k) for k in ('query', 'retrieved_at', 'verification')):
                raise InputError('公开搜索证据缺少检索词、获取时间或核验方式')
            if provenance['verification'] != 'page_content':
                raise InputError('公开搜索证据须核对正文，不能只凭搜索摘要')
            parse_datetime(provenance['retrieved_at'], 'retrieved_at')
            if stamp > cutoff:
                raise InputError('公开搜索证据发布时间晚于报告截止时间')
    fingerprint = hashlib.sha256(canonical({'config': config, 'sources': sources}).encode()).hexdigest()
    candidate_nodes = select_nodes(trend)
    candidate_dates = [n['date'] for n in candidate_nodes]
    prepared = {'version': VERSION, 'input_fingerprint': fingerprint, 'config': config, 'sources': sources,
                'cutoff': cutoff.isoformat(sep=' ', timespec='minutes'), 'cutoff_cell': hours[-1]['cells']['日期'],
                'trend': trend, 'raw_current_trend': current, 'topic_groups': list(groups.values()), 'board_rows': board_rows,
                'opinions': opinions, 'node_samples': samples,
                'candidate_node_dates': candidate_dates, 'candidate_node_context': candidate_nodes, 'raw_topic_rows': len(raw_topics)}
    packet = {'input_fingerprint': fingerprint, 'work_name': config['work_name'], 'cutoff': prepared['cutoff'],
              'trend': trend, 'candidate_node_dates': candidate_dates, 'candidate_node_context': candidate_nodes, 'topics': prepared['topic_groups'],
              'opinions': opinions, 'node_samples': samples,
              'cleaning_handoff': handoff,
              'sentiment_statistics': {k: statistics[k] for k in ('status', 'review_fields', 'window')} if statistics else None,
              'topic_statistics_scope': {'window': config.get('topic_window'), 'snapshot_at': config.get('topic_snapshot_at'),
                                         'board': '微博热搜榜', 'board_rows': len(board_rows)}}
    review = {'input_fingerprint': fingerprint,
              'topics': [{'id': g['id'], 'decision': None, 'reason': ''} for g in groups.values()],
              'directions': [],
              'nodes': [{'date': d, 'summary': '', 'evidence_ids': []} for d in candidate_dates],
              'sentiment': {'method': config['sentiment_method'], 'percentages': dict.fromkeys(KINDS),
                            'summaries': dict.fromkeys(KINDS, ''), 'paragraph_ids': {k: [] for k in KINDS},
                            'rationale': '', 'provided_source': '', 'counts': None, 'denominator': None,
                            'count_source': '', 'scope': ''},
              'headline': ''}
    if statistics and config['sentiment_method'] == 'measured':
        review['sentiment'].update(statistics['review_fields'])
    workspace.mkdir(parents=True, exist_ok=True)
    dump(workspace/'prepared.json', prepared)
    dump(workspace/'review_packet.json', packet)
    dump(workspace/'review.json', review)
    dump(workspace/'status.json', {'stage': 'awaiting_review', 'input_fingerprint': fingerprint,
                                  'prepared_sha256': sha_file(workspace/'prepared.json')})
    return {'stage': 'awaiting_review', 'workspace': str(workspace), 'topics': len(groups), 'board_rows': len(board_rows)}


def load_workspace(workspace):
    workspace = Path(workspace).resolve()
    state, prepared = load(workspace/'status.json'), load(workspace/'prepared.json')
    if state.get('prepared_sha256') != sha_file(workspace/'prepared.json'):
        raise InputError('prepared.json被修改，请新建工作区prepare')
    source_check(prepared)
    review = load(workspace/'review.json')
    if review.get('input_fingerprint') != prepared['input_fingerprint']:
        raise InputError('review.json输入指纹不符，不能复用旧审阅')
    return workspace, state, prepared, review


def compute_sentiment(review, opinions, issues):
    method = review.get('method')
    if method not in ('provided', 'editorial_estimate', 'measured'):
        raise InputError('情感方法无效')
    percentages = review.get('percentages') or {}
    if all(percentages.get(k) is None for k in KINDS) and method != 'measured':
        issues.append('尚未给定或研判情感比例')
        return None
    if method == 'measured':
        counts = review.get('counts') or {}
        counts = {k: integer(counts.get(k), f'情感计数{k}') for k in KINDS}
        denominator = integer(review.get('denominator'), '情感分母')
        if not denominator or sum(counts.values()) != denominator or not review.get('count_source'):
            raise InputError('情感完整样本计数合计、分母或来源不成立')
        exact = {k: Decimal(counts[k])*100/denominator for k in KINDS}
        negative_for_range = exact['negative']
        floors = {k: int(exact[k]) for k in KINDS}
        for k in sorted(KINDS, key=lambda x: exact[x]-floors[x], reverse=True)[:100-sum(floors.values())]:
            floors[k] += 1
        pct = {k: Decimal(floors[k]) for k in KINDS}
        if any(percentages.get(k) is not None and decimal(percentages[k], k) != pct[k] for k in KINDS):
            raise InputError('手填比例与完整样本计数计算比例不符')
    else:
        pct = {k: decimal(percentages.get(k), f'情感比例{k}') for k in KINDS}
        negative_for_range = pct['negative']
        if sum(pct.values()) != 100 or any(v > 100 for v in pct.values()):
            raise InputError('三类情感占比须在0至100之间且合计100')
        if method == 'provided' and not review.get('provided_source'):
            raise InputError('给定情感比例必须注明给定来源')
        if method == 'editorial_estimate' and not review.get('rationale'):
            raise InputError('观点稿估算必须填写比例研判依据')
    outside_range = not NEGATIVE_REPORT_RANGE[0] <= negative_for_range <= NEGATIVE_REPORT_RANGE[1]
    if method == 'editorial_estimate' and outside_range:
        raise InputError('简报情感研判的负面比例须为5%–10%（含两端），三类合计100%')
    ids = {p['id'] for p in opinions}
    summaries = review.get('summaries', {})
    for k in KINDS:
        evidence = review.get('paragraph_ids', {}).get(k, [])
        if not summaries.get(k) or (pct[k] > 0 and not evidence) or not set(evidence).issubset(ids):
            raise InputError(f'{LABELS[k]}概述缺少文字或有效观点段落依据')
    if outside_range:
        issues.append('给定或实测负面比例超出5%–10%，原值保留在核对记录，情感正文及配图待审')
    return {'method': method, 'percentages': {k: str(pct[k]) for k in KINDS}, 'summaries': summaries,
            'paragraph_ids': review['paragraph_ids'], 'rationale': review.get('rationale', ''),
            'provided_source': review.get('provided_source', ''), 'counts': review.get('counts'),
            'denominator': review.get('denominator'), 'count_source': review.get('count_source', ''),
            'scope': str(review.get('scope') or '').strip(), 'outside_report_range': outside_range}


def compute(prepared, review):
    issues = []
    scope_notes = []
    groups = {g['id']: g for g in prepared['topic_groups']}
    decisions = review.get('topics', [])
    if len(decisions) != len(groups) or {x.get('id') for x in decisions} != set(groups):
        raise InputError('话题审阅ID必须完整且唯一，不能少审或混入其他期')
    by_id = {d['id']: d for d in decisions}
    for item in decisions:
        if item.get('decision') not in ('include', 'exclude', 'uncertain') or not str(item.get('reason', '')).strip():
            raise InputError(f'话题 {item.get("id")} 缺少取舍或具体依据')
    pending = [g['id'] for g in groups.values() if by_id[g['id']]['decision'] == 'uncertain']
    if pending:
        issues.append(f'{len(pending)}个话题关联待核，未计入前三和汇总')
    directions = review.get('directions', [])
    direction_names = set()
    for direction in directions:
        refs = direction.get('topic_ids', [])
        if not direction.get('label') or not refs or len(refs) != len(set(refs)) or any(i not in by_id or by_id[i]['decision'] != 'include' for i in refs):
            raise InputError('讨论方向必须引用已纳入的有效话题')
        if direction['label'] in direction_names:
            raise InputError('讨论方向标签重复')
        direction_names.add(direction['label'])
    if len(directions) < 3:
        issues.append('有依据的微博讨论方向不足3个')
    selected = [dict(row, decision=by_id[row['topic_id']]['decision'], reason=by_id[row['topic_id']]['reason'])
                for row in prepared['board_rows'] if by_id[row['topic_id']]['decision'] == 'include']
    selected.sort(key=lambda x: (-Decimal(x['read_wan']), x['row']))
    if len(selected) < 3:
        issues.append('微博热搜榜纳入话题不足3个')
    read_wan = sum((Decimal(x['read_wan']) for x in selected), Decimal(0))
    if not prepared['config'].get('topic_snapshot_at'):
        scope_notes.append('微博阅读量快照时间未确认，按热搜导出表口径统计，不能还原传播截止时刻阅读量')
    elif parse_datetime(prepared['config']['topic_snapshot_at'], 'topic_snapshot_at') != parse_datetime(prepared['cutoff'], 'cutoff'):
        scope_notes.append('微博阅读量快照与传播截止时间不同，按各自口径统计')
    if any(x['later_than_volume_cutoff'] for x in selected):
        scope_notes.append('纳入话题包含晚于传播截止时刻上榜的记录，微博按导出表口径单列')
    samples = {x['id']: x for x in prepared['node_samples']}
    trend_dates = {x['date'] for x in prepared['trend']}
    nodes = []
    seen_dates = set()
    for node in review.get('nodes', []):
        d = node.get('date')
        if d not in trend_dates or d in seen_dates:
            raise InputError('节点日期必须属于当前趋势且不能重复')
        seen_dates.add(d)
        summary, refs = str(node.get('summary', '')).strip(), node.get('evidence_ids', [])
        if summary:
            if not refs or any(i not in samples for i in refs):
                raise InputError(f'{d}节点解释缺少已输入样本')
            if any(samples[i]['date'] != d or (samples[i]['provenance']['kind'] != 'web_search' and samples[i]['platform'] not in ('新浪微博', '微博')) for i in refs):
                raise InputError('节点只能引用对应日期的公开搜索证据或微博样本')
        else:
            issues.append(f'{d}节点分析待补，正文暂不写入该节点')
        if summary and re.search(r'单日传播量|传播峰值|周期内峰值|\d[\d,.]*(?:万|亿)条', summary):
            raise InputError('节点summary只填原因和热度判断；峰值与单日量由程序从滚动趋势添加，不能重复手填')
        if summary:
            from node_writing import heat_wording_error
            wording_error = heat_wording_error(summary)
            if wording_error:
                raise InputError(f'{d}节点：{wording_error}')
        options = {}
        for key, default in (('include_peak', True), ('include_daily_volume', False)):
            value = node.get(key, default)
            if not isinstance(value, bool):
                raise InputError(key + '必须为布尔值')
            options[key] = value
        nodes.append({'date': d, 'summary': summary, 'evidence_ids': refs, **options})
    for missing_date in prepared['candidate_node_dates']:
        if missing_date not in seen_dates:
            issues.append(f'{missing_date}节点分析待补，正文暂不写入该节点')
    sentiment_review = review.get('sentiment', {})
    if sentiment_review.get('method') != prepared['config']['sentiment_method']:
        raise InputError('情感方法与本次config不一致，请新建工作区变更方法')
    statistics = prepared['config'].get('sentiment_statistics')
    sample_statistics = None
    if statistics:
        if sentiment_review['method'] == 'measured' and any(
                sentiment_review.get(k) != v for k, v in statistics['review_fields'].items()):
            raise InputError('情感计数、分母或范围与已验收统计不一致')
        sample_statistics = {**statistics['review_fields'], 'status': statistics['status'], 'window': statistics['window']}
        if statistics['status'] == 'COMPLETED_AI_PILOT_NOT_HUMAN_APPROVED':
            issues.append('独立导出样本的情感AI试算待分析师确认')
    sentiment = compute_sentiment(sentiment_review, prepared['opinions'], issues)
    held_sentiment = None
    if sentiment and sentiment['outside_report_range']:
        held_sentiment, sentiment = sentiment, None
    headline = str(review.get('headline', '')).strip()
    if not headline.startswith(PREFIX) or len(headline) > 50 or len(headline) == len(PREFIX) or '\n' in headline or '\r' in headline:
        raise InputError('一级标题须含固定前缀及具体内容，整条不超过50字')
    trend = prepared['trend']
    volume = sum(x['heat'] for x in trend)
    peak = max(trend, key=lambda x: x['heat'])
    used_evidence = sorted({i for n in nodes for i in n['evidence_ids']})
    return {'version': VERSION, 'stage': 'draft' if issues else 'complete', 'issues': issues, 'scope_notes': scope_notes,
            'input_fingerprint': prepared['input_fingerprint'], 'work_name': prepared['config']['work_name'],
            'work_type': prepared['config']['work_type'], 'cutoff': prepared['cutoff'], 'cutoff_cell': prepared['cutoff_cell'],
            'volume': volume, 'volume_display': format_volume(volume), 'peak': peak, 'series': trend,
            'topic_read_wan': str(read_wan), 'topic_read_display': format_volume(read_wan*10000),
            'topic_count': len(selected), 'top3': selected[:3], 'selected_topics': selected,
            'pending_topic_ids': pending, 'directions': directions, 'nodes': nodes,
            'node_evidence': [samples[i] for i in used_evidence],
            'sentiment': sentiment, 'held_sentiment': held_sentiment,
            'sentiment_sample_statistics': sample_statistics, 'headline': headline,
            'sources': prepared['sources'], 'topic_window': prepared['config'].get('topic_window'),
            'topic_snapshot_at': prepared['config'].get('topic_snapshot_at')}


def report_nodes(metrics):
    sentences = []
    daily = {r['date']: r['heat'] for r in metrics['series']}
    peak = max(daily.values())
    for node in metrics['nodes']:
        if node['summary']:
            heat = daily[node['date']]
            at_peak = heat == peak and node.get('include_peak', True)
            peak_text = '，达到周期内传播峰值' if at_peak else ''
            show_volume = at_peak or node.get('include_daily_volume', False)
            volume_text = f'，单日传播量约{format_volume(heat)}条' if show_volume else ''
            summary = node['summary'].rstrip('。 ')
            prefix = '' if re.match(r'^(?:周期内|从传播趋势看)[，,]', summary) else f'{int(node["date"][-2:])}日，'
            sentence = f'{prefix}{summary}{peak_text}{volume_text}。'
            if len(re.sub(r'\s', '', sentence)) > 90:
                raise InputError(f'{node["date"]}完整节点超过90字，须合并同类动作、保留主要原因后重写，不能截字')
            sentences.append(sentence)
    return ''.join(sentences)


def report_reference(prepared):
    reference = prepared['config'].get('reference_report')
    return reference['path'] if isinstance(reference, dict) else reference


def build(workspace):
    workspace, state, prepared, review = load_workspace(workspace)
    metrics = compute(prepared, review)
    output = workspace/'deliverables'
    output.mkdir(exist_ok=True)
    from charts import make_charts
    charts = make_charts(metrics, output, prepared['config'].get('chart_font'))
    style_info=dict(charts['trend_fidelity'])
    if charts.get('sentiment_fidelity'):style_info['sentiment']=charts['sentiment_fidelity']
    dump(output/'chart_style.json',style_info)
    from word_export import fill_template, write_word
    reference = report_reference(prepared)
    word, paragraphs, extents = fill_template(metrics, report_nodes(metrics), reference)
    notes = list(metrics['issues']) + list(metrics['scope_notes'])
    if metrics['sentiment']:
        method = metrics['sentiment']['method']
        notes.append({'provided': '情感比例为输入给定值，未在本流程独立测算',
                      'editorial_estimate': '情感比例为观点稿编辑研判，不是全网样本实测',
                      'measured': '情感比例来自所提供完整样本计数，范围见来源'}[method])
    text = metrics['headline']+'\n\n'+'\n\n'.join(paragraphs)
    (output/'report.txt').write_text(text, encoding='utf-8')
    write_word(word, metrics, output, extents)
    dump(output/'metrics.json', metrics)
    dump(output/'trend.json', {'work_name': metrics['work_name'], 'cutoff': metrics['cutoff'],
                              'series': [{'date': x['date'], 'heat': x['heat']} for x in metrics['series']]})
    def cell(v):
        return html.escape(str(v if v is not None else '—'))
    trend_rows = ''.join('<tr>'+''.join('<td>'+cell(v)+'</td>' for v in (x['date'], x['previous_heat'], x['heat'], x['operation'], x['source_role'], x['source_cell']))+'</tr>' for x in metrics['series'])
    decision_map = {x['id']: x for x in review['topics']}
    topic_rows = ''.join('<tr>'+''.join('<td>'+cell(v)+'</td>' for v in (x['row'], x['title'], x['read_wan'],
                        {'include': '纳入', 'exclude': '剔除', 'uncertain': '待核'}[decision_map[x['topic_id']]['decision']],
                        decision_map[x['topic_id']]['reason'], x['latest_on_board'],
                        '晚于传播截止' if x['later_than_volume_cutoff'] else ''))+'</tr>' for x in prepared['board_rows'])
    sources = ''.join('<li>'+cell(x['role'])+'：'+cell(x['path'])+'</li>' for x in prepared['sources'])
    node_notes = ''.join('<p>'+cell(n['date'])+' '+cell(n['summary'] or '节点分析待补，正文暂不写入该节点')+'；证据ID：'+cell('、'.join(n['evidence_ids']))+'</p>' for n in metrics['nodes'])
    node_notes += ''.join('<p><strong>'+cell(x['id'])+'</strong> '+cell(x['published_at'])+' '+cell(x['author'])+'：'+cell(x['text'])+'<br>来源：'+cell(canonical(x['provenance']))+'<br>链接：'+cell(x.get('url', '未提供'))+'</p>' for x in metrics['node_evidence'])
    basis = ''
    if metrics['sentiment']:
        s = metrics['sentiment']
        basis = ''.join('<p>'+cell(s[k])+'</p>' for k in ('provided_source','count_source','rationale') if s[k])
        basis += '<p>观点段落依据：'+cell(canonical(s['paragraph_ids']))+'</p>'
    if metrics['held_sentiment']:
        basis += '<p>待审比例：'+cell(canonical(metrics['held_sentiment']['percentages']))+'</p>'
    if metrics['sentiment_sample_statistics']:
        sample = metrics['sentiment_sample_statistics']
        basis += '<p>独立样本统计：'+cell(canonical(sample))+'</p>'
    template = (ROOT/'assets'/'report.html').read_text(encoding='utf-8')
    replacements = {'TITLE': cell(f'{metrics["work_type"]}《{metrics["work_name"]}》传播特征'),
                    'STATUS': '待核试稿' if metrics['stage']=='draft' else '本次审阅完成',
                    'HEADLINE': cell(metrics['headline']), 'PARAGRAPHS': '<p>'+cell(paragraphs[0])+'</p>',
                    'SENTIMENT_PARAGRAPH': '<p>'+cell(paragraphs[1])+'</p>' if len(paragraphs)>1 else '',
                    'TREND_CHART': charts['trend_svg'], 'SENTIMENT_CHART': charts.get('sentiment_svg', '<p>情感比例待审，暂不制图。</p>'),
                    'NOTES': ''.join('<li>'+cell(x)+'</li>' for x in notes), 'TREND_ROWS': trend_rows,
                    'TOPIC_ROWS': topic_rows, 'SOURCES': sources, 'NODE_NOTES': node_notes, 'SENTIMENT_BASIS': basis,
                    'OPINIONS': ''.join('<p><strong>'+cell(x['id'])+'</strong> '+cell(x['text'])+'</p>' for x in prepared['opinions']),
                    'CUTOFF': cell(metrics['cutoff']), 'TOPIC_SNAPSHOT': cell(metrics.get('topic_snapshot_at') or '未确认；采用导出表快照'),
                    'DATA': json.dumps(metrics, ensure_ascii=False).replace('<', '\\u003c')}
    for key, value in replacements.items():
        template = template.replace('{{'+key+'}}', value)
    (output/'report.html').write_text(template, encoding='utf-8')
    files = ['report.docx', 'report.html', 'report.txt', 'metrics.json', 'trend.json', 'trend.png', 'trend.svg', 'chart_style.json']
    if metrics['sentiment']:
        files += ['sentiment.png', 'sentiment.svg']
    else:
        for name in ('sentiment.png', 'sentiment.svg'):
            if (output/name).exists():
                (output/name).unlink()
    manifest = {name: sha_file(output/name) for name in files}
    dump(workspace/'status.json', {'stage': metrics['stage'], 'issues': metrics['issues'],
                                  'input_fingerprint': prepared['input_fingerprint'],
                                  'prepared_sha256': state['prepared_sha256'], 'review_sha256': sha_file(workspace/'review.json'),
                                  'outputs': manifest})
    return {'stage': metrics['stage'], 'issues': metrics['issues'], 'report': str(output/'report.docx'),
            'audit_html':str(output/'report.html')}


def verify(workspace):
    workspace, state, prepared, review = load_workspace(workspace)
    if state.get('stage') not in ('complete', 'draft'):
        raise InputError('尚未构建报告')
    if state.get('review_sha256') != sha_file(workspace/'review.json'):
        raise InputError('审阅已修改，当前输出过期，请重新build')
    for name, digest in state['outputs'].items():
        path = workspace/'deliverables'/name
        if not path.is_file() or sha_file(path) != digest:
            raise InputError(f'输出缺失或已被改动: {name}')
    actual = load(workspace/'deliverables'/'metrics.json')
    expected = compute(prepared, review)
    if canonical(actual) != canonical(expected):
        raise InputError('已导出统计与当前输入、审阅不一致')
    html_text = (workspace/'deliverables'/'report.html').read_text(encoding='utf-8')
    from word_export import fill_template
    expected_word, paragraphs, _ = fill_template(expected, report_nodes(expected), report_reference(prepared))
    if any(html.escape(p) not in html_text for p in paragraphs):
        raise InputError('HTML正文与统计不一致')
    from docx import Document
    word=Document(workspace/'deliverables'/'report.docx')
    if [p.text for p in word.paragraphs] != [p.text for p in expected_word.paragraphs]:
        raise InputError('Word正文与统计不一致')
    labels = '、'.join(x['label'] for x in expected['directions'])
    if labels and not any(r.text == labels and r.bold is True for r in word.paragraphs[4].runs):
        raise InputError('Word微博讨论话题未加粗')
    if len(word.inline_shapes)!=(2 if expected['sentiment'] else 1):
        raise InputError('Word配图数量与本次数据不一致')
    from trend_template import binding
    chart=load(workspace/'deliverables'/'chart_style.json')
    if chart['data_binding']!=binding(expected):
        raise InputError('折线图数据绑定与本次统计不一致')
    image_id=word.inline_shapes[0]._inline.graphic.graphicData.pic.blipFill.blip.embed
    embedded_hash=hashlib.sha256(word.part.related_parts[image_id].blob).hexdigest()
    if embedded_hash!=sha_file(workspace/'deliverables'/'trend.png'):
        raise InputError('Word内嵌折线图与本次图片不一致')
    if chart['mode'] != 'generated_from_input':
        raise InputError('图表必须由本期输入绘制，不能复用人工稿原图')
    if expected['sentiment']:
        image_id=word.inline_shapes[1]._inline.graphic.graphicData.pic.blipFill.blip.embed
        sentiment_hash=hashlib.sha256(word.part.related_parts[image_id].blob).hexdigest()
        if sentiment_hash!=sha_file(workspace/'deliverables'/'sentiment.png'):
            raise InputError('Word内嵌情感图与本次图片不一致')
        if chart['sentiment']['mode'] != 'generated_from_input':
            raise InputError('情感图必须由本期比例绘制')
    return {'verification': 'pass', 'stage': state['stage'], 'source_files_unchanged': True,
            'output_files': len(state['outputs']), 'issues': expected['issues']}


def assemble(workspace):
    workspace = Path(workspace).resolve()
    metrics_check = verify(workspace)
    _, _, prepared, _ = load_workspace(workspace)
    handoff = prepared['config'].get('cleaning_handoff')
    if not handoff:
        raise InputError('合稿须先通过 cleaning_handoff 接入已完成的二期交付')
    from report_handoff import assemble_word
    output = workspace / 'deliverables'
    destination = output / '合稿.docx'
    selected_word = handoff['artifacts'].get('revised_word', handoff['artifacts']['word'])
    counts = assemble_word(output / 'report.docx', selected_word['path'], destination)
    receipt = {'status': 'draft' if metrics_check['stage'] == 'draft' or handoff['review_state'] == 'AI_PREVIEW' else 'complete',
               'word': str(destination), 'sha256': sha_file(destination),
               'issue_date': handoff['issue_date'], 'issues': metrics_check['issues'],
               'opinion_review_state': handoff['review_state'], 'opinion_sample_window': handoff['sample_window'],
               'opinion_window_basis': handoff['window_basis'], 'opinion_inputs': handoff['artifacts'],
               'opinion_word_used': selected_word,
               'sentiment_inputs': {x['role']: x for x in prepared['sources'] if x['role'].startswith('sentiment_')},
               **counts}
    dump(output / '合稿核对.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    p = sub.add_parser('prepare')
    p.add_argument('--config', required=True)
    p.add_argument('--workspace', required=True)
    for command in ('build', 'verify', 'status', 'assemble'):
        p = sub.add_parser(command)
        p.add_argument('--workspace', required=True)
    args = parser.parse_args()
    try:
        if args.command == 'prepare':
            result = prepare(args.config, args.workspace)
        elif args.command == 'build':
            result = build(args.workspace)
        elif args.command == 'verify':
            result = verify(args.workspace)
        elif args.command == 'assemble':
            result = assemble(args.workspace)
        else:
            result = load(Path(args.workspace)/'status.json')
    except (ValueError, OSError, KeyError, ImportError) as exc:
        print(json.dumps({'error': str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
