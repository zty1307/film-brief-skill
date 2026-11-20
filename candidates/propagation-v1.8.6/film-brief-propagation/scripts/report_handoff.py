"""Connect a completed cleaning run to propagation and assemble the report."""
from collections import Counter
from datetime import date, datetime
from pathlib import Path
import csv
import hashlib
import json
import re
from zipfile import ZipFile


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def verify_artifact(entry, base=None):
    path = Path(entry['path'])
    if base is not None:
        path = base / path
    path = path.resolve(strict=True)
    actual_hash = digest(path)
    if actual_hash != entry['sha256']:
        raise ValueError(f'文件哈希与清单不符：{path.name}')
    return {'path': str(path), 'sha256': actual_hash}


def verify_chart_revision(spec, base, original):
    """Accept a separately reviewed chart replacement for the same delivered Word."""
    from lxml import etree

    path = (base / spec['path']).resolve(strict=True)
    receipt_path = (base / spec['layout_receipt']).resolve(strict=True)
    receipt = read_json(receipt_path)
    if receipt.get('visual_review') != 'PASS_LAYOUT' or receipt.get('source_unchanged') is not True:
        raise ValueError('二期修正版缺少通过的分页核对')
    word = verify_artifact({'path': path, 'sha256': receipt['source_sha256']})
    document = 'word/document.xml'
    with ZipFile(original['path']) as before, ZipFile(path) as after:
        entries = set(before.namelist())
        if entries != set(after.namelist()):
            raise ValueError('二期修正版改变了正文或样本附件结构')
        changed_media = [n for n in entries if n.startswith('word/media/') and before.read(n) != after.read(n)]
        if len(changed_media) != 1 or any(
                before.read(n) != after.read(n) for n in entries - {document, *changed_media}):
            raise ValueError('二期修正版仅支持更换已核对的单张图表')
        namespace = {
            'wp': 'http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing',
            'a': 'http://schemas.openxmlformats.org/drawingml/2006/main',
        }
        documents = []
        for archive in (before, after):
            root = etree.fromstring(archive.read(document))
            for extent in root.xpath('.//wp:inline/wp:extent | .//wp:inline//a:xfrm/a:ext', namespaces=namespace):
                extent.attrib.pop('cy', None)
            documents.append(etree.tostring(root, method='c14n'))
        if documents[0] != documents[1]:
            raise ValueError('二期修正版改动了图表以外的正文或样本链接')
    return {'revised_word': word, 'revision_receipt': {'path': str(receipt_path), 'sha256': digest(receipt_path)}}


def read_handoff(spec, base, work_name):
    folder = (base / spec['workspace']).resolve(strict=True)
    status_path = folder / 'workflow_status.json'
    status = read_json(status_path)
    if status.get('stage') != 'COMPLETE' or not status.get('word'):
        raise ValueError('二期尚未交付 Word，先完成选材、写稿及终审')
    manifest_path = (folder / status['delivery_manifest']).resolve(strict=True)
    manifest = read_json(manifest_path)
    if status.get('target') != work_name or manifest.get('target') != work_name:
        raise ValueError('二期交付作品与本期传播分析不一致')
    if not manifest.get('review_state') or manifest['review_state'] != status.get('review_state'):
        raise ValueError('二期状态与交付清单中的审阅状态不一致')

    published = {}
    for item in manifest['artifacts']:
        kind = item.get('kind')
        if kind in ('word', 'workbench'):
            if kind in published:
                raise ValueError(f'二期交付清单有多个 {kind}')
            published[kind] = verify_artifact(item, folder)
    if 'word' not in published or Path(published['word']['path']) != (folder / status['word']).resolve():
        raise ValueError('二期状态与清单未指向同一份 Word')

    datasets = [x for x in manifest['internal_artifacts']
                if Path(x['path']).name == 'final_dataset.json']
    if len(datasets) != 1:
        raise ValueError('二期交付须有唯一的已审数据集')
    dataset = verify_artifact(datasets[0], folder)
    if dataset['sha256'] != manifest.get('dataset_sha256'):
        raise ValueError('二期已审数据集与写稿输入不一致')

    issue_date = date.fromisoformat(spec['issue_date'])
    start = datetime.fromisoformat(spec['sample_window']['start'])
    end = datetime.fromisoformat(spec['sample_window']['end'])
    if start.tzinfo or end.tzinfo or end < start or end.date() > issue_date:
        raise ValueError('二期样本窗口须为本期的北京时间，开始不能晚于结束')
    grouped = read_json(dataset['path'])
    if grouped.get('target') != work_name:
        raise ValueError('二期已审数据集作品不一致')
    raw_paths = sorted({s['source']['source_path']
                        for g in grouped['groups'] for s in g['samples']
                        if s.get('source', {}).get('source_path')})
    declared_days = {x['issue_date'] for x in (status, manifest) if x.get('issue_date')}
    export_windows = [match.groups() for path in raw_paths if (match := re.search(
        r'(\d{4}\.\d{2}\.\d{2} \d{2}_\d{2})至(\d{4}\.\d{2}\.\d{2} \d{2}_\d{2})', Path(path).name))]
    if declared_days and declared_days != {issue_date.isoformat()}:
        raise ValueError('二期期次和配置中的报告日期不一致')
    if not declared_days and end.date() != issue_date:
        raise ValueError('二期清单缺少期次，样本窗口结束日期须与本期相同')
    if export_windows:
        export_start = min(datetime.strptime(a, '%Y.%m.%d %H_%M') for a, _ in export_windows)
        export_end = max(datetime.strptime(b, '%Y.%m.%d %H_%M') for _, b in export_windows)
        if (start.replace(second=0, microsecond=0), end.replace(second=0, microsecond=0)) != (export_start, export_end):
            raise ValueError('配置样本窗口与二期已审数据的原表导出窗口不一致')
    if spec.get('chart_revision'):
        published.update(verify_chart_revision(spec['chart_revision'], base, published['word']))
    return {
        'issue_date': issue_date.isoformat(),
        'sample_window': {'start': start.isoformat(), 'end': end.isoformat()},
        'review_state': manifest['review_state'],
        'raw_paths': raw_paths,
        'window_basis': 'source_filenames' if export_windows else 'declared',
        'artifacts': {
            **published, 'dataset': dataset,
            'status': {'path': str(status_path), 'sha256': digest(status_path)},
            'manifest': {'path': str(manifest_path), 'sha256': digest(manifest_path)},
        },
    }


def opinion_inputs(paragraphs, handoff):
    """Keep the approved prose and traceable current workbench groups for review."""
    prose = [item for item in paragraphs
             if not re.match(r'^\s*(?:注[:：]|图\s*\d|数据来源[:：])', item['text'])]
    if not prose:
        raise ValueError('二期观点 Word 没有可用于情感概述的正文')
    dataset = read_json(handoff['artifacts']['dataset']['path'])
    for index, group in enumerate(dataset.get('groups', [])):
        aspect = str(group.get('aspect') or '').strip()
        if not aspect:
            continue
        samples = {s['candidate_id']: s.get('display_excerpt', '') for s in group['samples']}
        excerpts = [s for s in (group.get('writing_guidance') or {}).get('preferred', [])
                    if s.get('quote_snippet') and s['quote_snippet'] in samples.get(s.get('candidate_id'), '')]
        if not excerpts:
            continue
        quotes = ' / '.join(s['quote_snippet'] for s in excerpts[:2])
        prose.append({
            'id': f'wb{index:03d}', 'text': aspect + (f'。已审支持原句：{quotes}' if quotes else ''),
            'source_location': f'本期工作台第{index + 1}组',
            'evidence_ids': [s['candidate_id'] for s in excerpts[:2] if s.get('candidate_id')],
            'stance': group.get('stance'),
        })
    return prose


def read_measured_sentiment(spec, base, work_name, handoff):
    """Bind the separate sample-classification receipt and reproduce its counts."""
    result_path = (base / spec['result']).resolve(strict=True)
    receipt_path = (base / spec['validation']).resolve(strict=True)
    result, receipt = read_json(result_path), read_json(receipt_path)
    artifacts = {
        'sentiment_result': verify_artifact({'path': result_path, 'sha256': receipt['outputs'][result_path.name]}),
        'sentiment_validation': {'path': str(receipt_path), 'sha256': digest(receipt_path)},
    }
    manifest = receipt['manifest']
    checks = receipt['checks']
    if manifest['work'] != work_name or not all(checks.get(k) is True for k in ('source_hashes_unchanged', 'counts_reconciled')):
        raise ValueError('情感统计的作品或验收状态不符')
    window = {side: datetime.fromisoformat(result[f'window_{side}']).isoformat() for side in ('start', 'end')}
    if window != handoff['sample_window'] or any(
            datetime.fromisoformat(manifest[f'window_{side}']).isoformat() != value for side, value in window.items()):
        raise ValueError('情感统计与本期二期导出窗口不一致')
    files = {Path(entry['path']).name: verify_artifact(entry, receipt_path.parent) for entry in manifest['files']}
    for raw_path in handoff['raw_paths']:
        raw = Path(raw_path)
        if raw.name not in files or files[raw.name]['sha256'] != digest(raw):
            raise ValueError(f'情感统计与二期原表不同：{raw.name}')
    artifacts.update({f'sentiment_source_{i}': entry for i, entry in enumerate(files.values(), 1)})
    counts = {k: result['counts'][k] for k in ('positive', 'neutral', 'negative')}
    denominator = result['denominator']
    if (type(denominator) is not int or denominator <= 0
            or any(type(v) is not int or v < 0 for v in counts.values())
            or sum(counts.values()) != denominator):
        raise ValueError('情感统计三类计数与分母不符')
    evidence = []
    if spec.get('evidence_ids'):
        detail = (base / spec['details']).resolve(strict=True)
        artifacts['sentiment_details'] = verify_artifact({'path': detail, 'sha256': receipt['outputs'][detail.name]})
        wanted = set(spec['evidence_ids'])
        with detail.open(encoding='utf-8-sig', newline='') as stream:
            for row in csv.DictReader(stream):
                if row['样本ID'] in wanted:
                    quote = row['原文摘录']
                    source_file = Path(row['源文件']).name
                    if not quote or quote not in row['标题与正文'] or source_file not in files:
                        raise ValueError(f'情感统计摘录无法溯源：{row["样本ID"]}')
                    evidence.append({'id': 'sent_' + row['样本ID'], 'text': quote, 'stance': row['最终分类'],
                                     'source_location': f'{files[source_file]["path"]}/{row["工作表"]}!{row["Excel行号"]}'})
        if {x['id'][5:] for x in evidence} != wanted:
            raise ValueError('有指定的情感统计样本不在分类明细中')
    return {
        'artifacts': artifacts,
        'evidence': evidence,
        'status': result['status'],
        'window': window,
        'review_fields': {'counts': counts, 'denominator': denominator,
                          'count_source': result.get('scope', '') + f'（{result_path.name}）',
                          'scope': f'本期导出样本中可明确归入三类情感的{denominator}条文本'},
    }


def document_inventory(document):
    """Count body images and clickable links by content, not relationship ID."""
    from docx.oxml.ns import qn

    images = Counter()
    links = Counter()
    for element in document.element.body.xpath('.//a:blip'):
        rid = element.get(qn('r:embed'))
        if rid:
            images[hashlib.sha256(document.part.related_parts[rid].blob).hexdigest()] += 1
    for element in document.element.body.xpath('.//w:hyperlink'):
        rid = element.get(qn('r:id'))
        if rid:
            links[document.part.rels[rid].target_ref] += 1
    return images, links


def assemble_word(first_path, second_path, destination):
    from docx import Document
    from docxcompose.composer import Composer
    from word_export import apply_manual_paragraph_layout

    first, second = Document(first_path), Document(second_path)
    first_text = [p.text for p in first.paragraphs if p.text.strip()]
    second_text = [p.text for p in second.paragraphs if p.text.strip()]
    if not first_text or not any(t.startswith('一、总体传播特征') for t in first_text):
        raise ValueError('第一部分 Word 缺少总体传播特征标题')
    if not second_text or not second_text[0].startswith(('二、主要舆论观点', '二、舆论观点')):
        raise ValueError('二期输入须是观点段独立 Word，避免二次追加整份报告')
    inventories = [document_inventory(document) for document in (first, second)]
    expected_images = inventories[0][0] + inventories[1][0]
    expected_links = inventories[0][1] + inventories[1][1]
    composer = Composer(first)
    composer.append(second, remove_property_fields=False)
    apply_manual_paragraph_layout(first)
    paragraphs = first.paragraphs
    boundary = next(i for i, paragraph in enumerate(paragraphs) if paragraph.text == second_text[0])
    for paragraph in reversed(paragraphs[:boundary]):
        if paragraph.text.strip() or paragraph._p.xpath('.//w:drawing | .//w:pict | .//w:object | .//w:fldChar'):
            break
        paragraph._p.getparent().remove(paragraph._p)
    paragraphs[boundary].insert_paragraph_before()
    first.core_properties.subject = '影视舆情分析合稿'
    temporary = destination.with_name(destination.stem + '.writing.docx')
    try:
        composer.save(temporary)
        combined = Document(temporary)
        texts = [p.text for p in combined.paragraphs if p.text.strip()]
        if texts != first_text + second_text:
            raise ValueError('合稿后正文顺序或文字发生变化')
        if document_inventory(combined) != (expected_images, expected_links):
            raise ValueError('合稿前后的图像或样本链接不一致')
        temporary.replace(destination)
    finally:
        if temporary.exists():
            temporary.unlink()
    return {'body_images': sum(expected_images.values()), 'clickable_links': sum(expected_links.values())}
