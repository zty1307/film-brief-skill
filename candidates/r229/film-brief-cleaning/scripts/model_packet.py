"""Readable, compact JSON for model inputs without changing evidence values."""
import json
from pathlib import Path


def voice_identity(entry):
    """Reuse sampling's author identity, with a separate fallback for unknowns."""
    return entry.get('identity') or ('row:' + entry['source_id'])


def voice_refs(entries):
    identities = {}
    result = {}
    for row in entries:
        key = voice_identity(row)
        result[row['excerpt_id']] = identities.setdefault(key, f'V{len(identities)+1:03d}')
    return result


def review_item(item):
    """Expose source wording and the review task, without routing internals."""
    keys = ('candidate_id', 'group_index', 'aspect', 'category', 'core_mechanism',
            'stance', 'title', 'source_channel', 'source_name', 'min_excerpt_chars',
            'work_reference')
    result = {key: item[key] for key in keys if item.get(key) not in (None, '')}
    if 'reference_eligible_window_numbers' in item:
        result['reference_eligible_window_numbers']=item['reference_eligible_window_numbers']
    if item.get('selection_options'):
        result['selection_options'] = [{key:option[key] for key in ('text','work_reference') if key in option} for option in item['selection_options']]
    elif item.get('selection_text'):
        result['selection_text'] = item['selection_text']
    return result


def write_packet(path, value):
    def format_value(item, level=0):
        compact = json.dumps(item, ensure_ascii=False, separators=(',', ':'))
        if len(compact) <= 1400 or not isinstance(item, (dict, list)):
            return compact
        indent = ' ' * (level + 1)
        if isinstance(item, dict):
            # Match json.dumps object-key semantics even for integer group IDs.
            rows = [json.dumps(key if isinstance(key, str) else json.dumps(key, ensure_ascii=False), ensure_ascii=False) + ': ' + format_value(val, level + 1)
                    for key, val in item.items()]
            opening, closing = '{', '}'
        else:
            rows = [format_value(val, level + 1) for val in item]
            opening, closing = '[', ']'
        return opening + '\n' + ',\n'.join(indent + row for row in rows) + '\n' + ' ' * level + closing

    rendered = format_value(value) + '\n'
    json.loads(rendered)  # Never hand an invalid input packet to the model.
    Path(path).write_text(rendered, encoding='utf-8')


def writing_input(inventory, outdir):
    data = dict(inventory)
    data['groups'] = []
    redundant = {'source_file', 'sheet', 'row', 'allowed_attributions', 'selection_hint',
                 'writing_supplement'}
    for group in inventory['groups']:
        row = dict(group)
        row['samples'] = [{key: value for key, value in sample.items()
                           if key not in redundant and (key!='primary_support_snippets' or value)}
                          for sample in group['samples']]
        data['groups'].append(row)
    data['source_metadata_rule'] = '出处与溯源由脚本从冻结工作台回填，填写candidate_id即可；每条完整display_excerpt仍是唯一写稿证据。'
    write_packet(Path(outdir) / 'writing_input.json', data)


def readable_rules(value, width=900):
    """Split instructional text into lossless chunks that Read will not truncate."""
    chunks = []
    while value:
        end = min(width, len(value))
        if end < len(value):
            cut = max(value.rfind(mark, 0, end) for mark in ('。', '\n', '；'))
            if cut >= width // 2:
                end = cut + 1
        chunks.append(value[:end])
        value = value[end:]
    return chunks
