"""Diversify review opportunities within comparable relevance bands, never fabricate voices."""
from collections import Counter

def channel(record):
    value=record.get('channel','未知渠道')
    return '公众号' if value in ('公众文章','微信公众号','公众号') else value

def balance_band(items,sources,stance,counts=None):
    from rapid_film_workbench import source_priority
    counts=counts if counts is not None else Counter()
    pending=list(enumerate(items));result=[]
    negative=stance in ('negative','负向词倾向')
    while pending:
        def key(pair):
            order,item=pair;record=sources[item['source_id']];p=source_priority(record);c=channel(record)
            # Preserve verified media for nonnegative and direct-social priority for criticism.
            tier=(-p['negative_channel_priority'],p['platform_risk_rank'],-p['media_authority_rank']) if negative else (p['platform_risk_rank'],-p['media_authority_rank'])
            return (*tier,counts[c],c=='今日头条',order)
        pair=min(pending,key=key);pending.remove(pair);result.append(pair[1])
        counts[channel(sources[pair[1]['source_id']])]+=1
    return result

def audit(groups):
    total=Counter();rows=[]
    for i,g in enumerate(groups):
        counts=Counter(channel(s.get('source',{})) for s in g['samples']);total.update(counts)
        rows.append({'group_index':i,'channels':dict(counts),'toutiao_share':round(counts['今日头条']/sum(counts.values()),3),
                     'single_channel':len(counts)<2,'toutiao_majority':counts['今日头条']*2>sum(counts.values())})
    return {'policy':'COMPARABLE_RELEVANCE_CHANNEL_DIVERSITY_V1','overall':dict(total),'groups':rows,
            'scope':'来源比例只是留样分布，不是舆论声量；不为渠道配额放宽证据标准。'}
