"""Offline source admission, platform preference, and cross-cluster identity."""
from __future__ import annotations
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit

POLICY_VERSION = 'R132_SOURCE_POLICY_V3'
URL_FIELDS = ('链接','发文链接','原文链接','内容链接','文章链接','URL','url')
AUTHOR_FIELDS = ('作者','作者名称','媒体名称','账号名称','账号昵称','帐号昵称','发文账号','来源','发布者','昵称','用户名')
TEXT_FIELDS = ('正文内容','正文','内容','视频ASR')
DISCLOSURE = re.compile(
    r'(?:本文|本篇|本稿|本内容|该文|文章|内容)(?:(?:全部|部分|主要|完全|全文|的?文字|内容|系|为|是|[，：:\s])){0,5}(?:由|使用|借助|采用|经|系|为|是).{0,6}'
    r'(?:AI|人工智能|ChatGPT|DeepSeek|豆包|AIGC|GPT).{0,18}(?:生成|创作|撰写|改写|润色|写作|代写|写的)|'
    r'(?m:^\s*[【\[]?(?:AI|人工智能|AIGC)(?:生成|创作|撰写)(?:的)?(?:文章|内容|文本)[】\]]?\s*[。！!]?\s*$)|'
    r'声明[：:]?\s*(?:本文|内容)?.{0,8}(?:AI|人工智能).{0,10}(?:生成|辅助)',re.I)
AI_TOOL_NAME = r'(?:(?:微博)?智搜|ChatGPT|DeepSeek|豆包|腾讯元宝|通义千问|文心一言|腾讯混元|Kimi|Claude|Gemini|GPT(?:-[A-Za-z0-9.]+)?|AI(?:助手|工具|模型)?(?![A-Za-z]|助手|工具|模型|专家|学者|研究员|工程师)|人工智能(?:助手|工具|模型)?(?!助手|工具|模型|专家|学者|研究员|工程师)|大模型(?!专家|学者|研究员|工程师))'
TOOL_ANSWER_SHARE = re.compile('|'.join((
    r'(?:问了问|问了(?:一下)?|请教了|向)\s*@?\s*'+AI_TOOL_NAME+r'[^。！？；\n]{0,90}(?:回答|答复|回复|给出了)',
    r'(?:问问|问)\s*@?\s*'+AI_TOOL_NAME+r'[^。！？；\n]{0,90}(?:回答不错|回答如下|回答是|答复如下|回答[：:])',
    r'(?:使用|借助|通过|用|让)\s*@?\s*'+AI_TOOL_NAME+r'[^。！？；\n]{0,60}(?:生成|撰写|写了|写的|写出|代写|润色|改写)',
    AI_TOOL_NAME+r'\s*(?:的)?(?:回答|答复|生成内容)\s*(?:如下|是[：:]|[：:])')),re.I)
MARKDOWN_EMPHASIS = re.compile(r'\*\*[^*\r\n]{2,100}\*\*')
MARKDOWN_BULLET = re.compile(r'(?m)^\s*\*\s+\S')
WORK_DECLARATION = re.compile(r'(?:作品|创作|内容)?\s*声明\s*[：:]\s*(?:本(?:文|作品|内容)[，,：:]?\s*)?(?:内容|文章|本文|本作品)?\s*(?:由|使用|采用|借助)\s*(?:AI|人工智能|AIGC)\s*(?:生成|创作|辅助生成)', re.I)
ASSISTANT_DRAFT = re.compile(r'(?:我(?:已|已经)?(?:理解|了解|明白)[^。\n]{0,35}(?:您|你)的(?:要求|需求)|(?:以下|下面)是(?:我)?为(?:您|你)[^。\n]{0,20}(?:改写|创作|撰写)的(?:文章|内容))')

def first(data,names):
    return next((str(data[k]).strip() for k in names if data.get(k) is not None and str(data[k]).strip()),'')

def normalize_text(text):
    return re.sub(r'\s+','',str(text or '')).strip()

def digest(text):return hashlib.sha256(text.encode('utf-8')).hexdigest()

def canonical_url(value):
    try:
        p=urlsplit(str(value or '').strip());host=(p.hostname or '').lower().rstrip('.')
        if p.scheme.lower() not in ('http','https') or not host:return ''
        query=[(k,v) for k,v in parse_qsl(p.query,keep_blank_values=True)
               if not k.lower().startswith('utm_') and k.lower() not in ('from','spm','spm_id_from','isappinstalled','share_source','share_medium','share_token')]
        if not p.path.strip('/') and not query:return ''  # A platform homepage is not an article identity.
        return host+p.path.rstrip('/')+('?' + urlencode(sorted(query)) if query else '')
    except (ValueError,UnicodeError):return ''

def platform_policy(record):
    data=record.get('data',{});url=first(data,URL_FIELDS)
    try:host=(urlsplit(url).hostname or '').lower().rstrip('.')
    except ValueError:host=''
    site=first(data,('站点名称','网站名称','来源网站','发布网站','媒体名称'))
    if any(host==d or host.endswith('.'+d) for d in ('weibo.com','weibo.cn')):
        return {'platform_risk_rank':0,'deprioritized_platform':None}
    platform=''
    for name,domains,labels in [
        ('sina',('sina.com.cn','sina.cn'),('新浪','新浪新闻','新浪看点')),
        ('netease',('163.com',),('网易','网易新闻','网易号')),
        ('sohu',('sohu.com',),('搜狐','搜狐新闻','搜狐号'))]:
        if any(host==d or host.endswith('.'+d) for d in domains) or site in labels:
            platform=name;break
    # Weibo remains its own social lane; Sina ownership is not a URL match.
    return {'platform_risk_rank':int(bool(platform)),'deprioritized_platform':platform or None}

def policy_exclusion(record):
    data=record.get('data',{});platform=platform_policy(record)['deprioritized_platform']
    for field in AUTHOR_FIELDS:
        author=re.sub(r'[^\u4e00-\u9fffA-Za-z0-9]','',str(data.get(field) or ''))
        if author in ('新浪新闻每日新闻摘','新浪新闻每日新闻摘录') or (author in ('每日新闻摘','每日新闻摘录') and platform=='sina'):
            return {'type':'blacklisted_publisher','field':field,'evidence':str(data[field]),'policy':POLICY_VERSION}
    declaration_fields=[k for k in data if re.search(r'作品声明|创作声明|内容声明|AI.*(?:标识|声明)|人工智能.*标识',str(k),re.I)]
    for field in dict.fromkeys(('标题',*TEXT_FIELDS,'AI生成声明','生成式人工智能标识','AI标识',*declaration_fields)):
        text=str(data.get(field) or '')
        declaration=WORK_DECLARATION.search(text)
        if declaration or (field in declaration_fields and re.fullmatch(r'\s*(?:内容)?\s*由\s*(?:AI|人工智能|AIGC)\s*生成[。！!]?\s*',text,re.I)):
            return {'type':'explicit_ai_generation_disclosure','field':field,'evidence':declaration.group(0) if declaration else text,'policy':POLICY_VERSION}
        draft=ASSISTANT_DRAFT.search(text)
        if draft and re.search(r'我将.{0,45}(?:为您|为你)|以下是.{0,25}(?:改写|创作|撰写)|完全避免抄袭原文|调整原文结构',text):
            return {'type':'explicit_assistant_draft_text','field':field,'evidence':draft.group(0),'policy':POLICY_VERSION}
        for match in DISCLOSURE.finditer(text):
            clause=re.split(r'[。！？；\n]',text[max(0,match.start()-15):match.end()])[-1]
            if re.search(r'(?:不是|并非|非|未使用|未借助|未经|未由|不使用|禁止).{0,8}(?:AI|人工智能|AIGC|ChatGPT|DeepSeek|豆包|GPT)',clause,re.I):continue
            return {'type':'explicit_ai_generation_disclosure','field':field,'evidence':match.group(0),'policy':POLICY_VERSION}
        for match in TOOL_ANSWER_SHARE.finditer(text):
            before=text[max(0,match.start()-16):match.start()]
            if re.search(r'(?:不是|并非|不|没(?:有)?|未(?:曾)?|禁止|严禁|不要|别|勿)(?:我|作者|他|她)?(?:\s*(?:使用|采用|引用|借助|用|问|让))?\s*$',before):continue
            evidence=match.group(0)
            if re.search(r'(?:不是|并非|不|没(?:有)?|未).{0,8}(?:生成|创作|撰写|改写|润色|代写|写的|写了|回答|答复)',evidence[-25:]):continue
            if re.search(r'(?:可以|能够|可用于|可用来|可实现|支持|能用来).{0,12}(?:生成|撰写|写出|润色|改写)',evidence):continue
            return {'type':'explicit_ai_tool_answer_share','field':field,'evidence':evidence,
                    'policy':POLICY_VERSION,'note':'原文明确声明引用工具答复或使用工具生成；不按文风推测作者身份'}
        if field in ('AI生成声明','生成式人工智能标识','AI标识') and re.fullmatch(r'\s*(?:是|true|1|AI生成|AIGC|人工智能生成)\s*',text,re.I):
            return {'type':'explicit_ai_generation_metadata','field':field,'evidence':text,'policy':POLICY_VERSION}
        mark=MARKDOWN_EMPHASIS.search(text)
        if mark or len(MARKDOWN_BULLET.findall(text))>=2:
            return {'type':'conspicuous_draft_format','field':field,'evidence':mark.group(0) if mark else '多行星号项目符号',
                    'policy':POLICY_VERSION,'note':'按用户样本格式筛选要求排除；不是AI作者身份鉴定'}
    return None

def source_keys(record):
    """Same row, same canonical URL, or same full text. Never merge an author."""
    data=record.get('data',{});keys=set()
    if record.get('id'):keys.add('row:'+str(record['id']))
    url=canonical_url(first(data,URL_FIELDS))
    if url:keys.add('url:'+url)
    body=normalize_text(first(data,TEXT_FIELDS))
    if len(body)>=12:keys.add('body:'+digest(body))
    return keys

def sample_keys(sample,record=None):
    keys=source_keys(record or sample.get('source',{}))
    if sample.get('source_id'):keys.add('row:'+str(sample['source_id']))
    from film_pipeline import clean_display_excerpt
    excerpt=normalize_text(sample.get('display_excerpt') or clean_display_excerpt(str(sample.get('excerpt') or '')))
    if len(excerpt)>=12:keys.add('quote:'+digest(excerpt))
    return keys

def verify_unique_samples(dataset):
    owners={}
    for index,group in enumerate(dataset.get('groups',[])):
        for sample in group.get('samples',[]):
            keys=sample_keys(sample)
            collision=next((owners[k] for k in keys if k in owners),None)
            if collision is not None:raise ValueError(f'同一样本只能归一个观点：组{index}与组{collision}来源/网址/全文/展示句重复')
            owners.update({key:index for key in keys})
    return len(owners)

def unique_groups(groups):
    """For script-only previews; the reviewed workflow uses its judgment ledger."""
    options=sorted(((-s.get('score',0),gi,si,s) for gi,g in enumerate(groups) for si,s in enumerate(g['samples'])),key=lambda row:row[:3])
    used=set();keep=set();audit=[]
    for _,gi,si,sample in options:
        keys=sample_keys(sample)
        if keys & used:
            audit.append({'group_index':gi,'sample_index':si,'source_id':sample.get('source_id',sample.get('source',{}).get('id'))})
        else:used.update(keys);keep.add((gi,si))
    return [{**g,'samples':[s for si,s in enumerate(g['samples']) if (gi,si) in keep]}
            for gi,g in enumerate(groups) if any((gi,si) in keep for si in range(len(g['samples'])))],audit
