"""Render a trend on the retained human reference's fixed visual layout."""
from __future__ import annotations

import base64
import hashlib
import json
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ASSETS=Path(__file__).resolve().parents[1]/'assets'


def binding(metrics):
    return {'work_type':metrics['work_type'],'work_name':metrics['work_name'],'volume':metrics['volume'],
            'series':[{'date':x['date'],'heat':x['heat']} for x in metrics['series']]}


def cardinal_curve(points):
    """Uniform cardinal spline, matching the reference's smooth-line treatment."""
    if len(points)<2:return points
    padded=[(2*points[0][0]-points[1][0],2*points[0][1]-points[1][1])]+points+[(2*points[-1][0]-points[-2][0],2*points[-1][1]-points[-2][1])]
    result=[]
    for i in range(1,len(padded)-2):
        p0,p1,p2,p3=padded[i-1:i+3]
        for j in range(65):
            t=j/64
            result.append(tuple(.5*((2*p1[k])+(-p0[k]+p2[k])*t+(2*p0[k]-5*p1[k]+4*p2[k]-p3[k])*t*t+(-p0[k]+3*p1[k]-3*p2[k]+p3[k])*t*t*t) for k in (0,1)))
    return result


def render_trend(metrics,output,requested_font=None):
    output=Path(output)
    style=json.loads((ASSETS/'trend-style.json').read_text(encoding='utf-8'))
    base=Image.open(ASSETS/style['template_image']).convert('RGBA')
    width,height=style['canvas'];scale=4
    layer=Image.new('RGBA',(width*scale,height*scale))
    draw=ImageDraw.Draw(layer)
    cleaner=ImageDraw.Draw(base)
    def font(role):
        name=style[role]['font']
        candidates=[Path(requested_font)] if requested_font else [Path('C:/Windows/Fonts')/name,
                      Path.home()/'.fonts'/name,Path('/usr/share/fonts/truetype/msttcorefonts')/name]
        found=next((p for p in candidates if p.is_file()),None)
        if found is None:
            raise ValueError(f'固定模板缺少字体{name}；不能静默换字体，请提供chart_font或相同字体文件')
        return ImageFont.truetype(str(found),round(style[role]['size']*scale))
    def text(role,xy,value,anchor='mm'):
        draw.text((xy[0]*scale,xy[1]*scale),str(value),font=font(role),fill='black',anchor=anchor)
    def clear(box):cleaner.rectangle(box,fill=(0,0,0,0))
    title=f'{metrics["work_type"]}《{metrics["work_name"]}》舆情走势'
    clear((0,0,width,62))
    if draw.textlength(title,font=font('title'))>(width-24)*scale:
        raise ValueError('作品名超过固定单行标题容量，需要明确标题换行方案，不能擅自缩小模板字体')
    text('title',(style['title']['x'],style['title']['y']),title)
    clear((0,63,width,104))
    text('subtitle',(style['subtitle']['x'],style['subtitle']['y']),'总量：'+metrics['volume_display']+'条')
    maximum=max(p['heat'] for p in metrics['series'])
    power=10**math.floor(math.log10(maximum)) if maximum else 1
    ymax=next(v*power for v in (1,2,4,5,8,10) if v*power>=maximum)
    plot=style['plot'];left,right,top,bottom=[plot[k] for k in ('left','right','top','bottom')]
    clear((0,105,94,455))
    for j in range(6):text('axis',(style['axis']['x'],bottom-j*(bottom-top)/5),str(round(ymax*j/5)),'rm')
    series=metrics['series'];n=len(series);slot=(right-left)/n
    points=[(left+(i+.5)*slot,bottom-p['heat']/ymax*(bottom-top)) for i,p in enumerate(series)]
    clear((0,456,width,494))
    step=max(1,math.ceil(n/12))
    for i,p in enumerate(series):
        if i%step==0 or i==n-1:text('date',(points[i][0],style['date']['y']),p['date'][5:].replace('-','/'))
    curve=cardinal_curve(points)
    if len(curve)>1:draw.line([(x*scale,y*scale) for x,y in curve],fill=style['curve_color'],width=round(style['line_width']*scale))
    for i in sorted({0,n-1}):
        x,y=points[i];r=style['marker_radius']
        draw.ellipse(((x-r)*scale,(y-r)*scale,(x+r)*scale,(y+r)*scale),fill=style['curve_color'])
    peak=max(range(n),key=lambda i:series[i]['heat'])
    for i in sorted({0,peak}):
        x,y=points[i]
        text('value',(x+(style['value']['first_offset_x'] if i==0 else 0),y+style['value']['offset_y']),str(series[i]['heat']))
    base.alpha_composite(layer.resize((width,height),Image.Resampling.LANCZOS))
    base.save(output/'trend.png',dpi=(style['dpi'],style['dpi']))
    mode='generated_from_input'
    raw=(output/'trend.png').read_bytes()
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="856" height="532" viewBox="0 0 856 532"><image width="856" height="532" href="data:image/png;base64,{base64.b64encode(raw).decode()}"/></svg>'
    (output/'trend.svg').write_text(svg,encoding='utf-8')
    fidelity={'mode':mode,'output_sha256':hashlib.sha256(raw).hexdigest(),
              'canvas':style['canvas'],'data_binding':binding(metrics),
              'labels':['first','peak'],'point_markers':['first','last'],
              'note':'SVG为同一PNG的封装，保证Word、HTML及图片显示一致；新数据须另做视觉核验。'}
    return {'trend_svg':svg,'trend_fidelity':fidelity}


def render_sentiment(metrics,output,requested_font=None):
    output=Path(output)
    style=json.loads((ASSETS/'sentiment-style.json').read_text(encoding='utf-8'))
    data={'work_name':metrics['work_name'],'work_type':metrics['work_type'],
          'percentages':metrics['sentiment']['percentages']}
    width,height=style['canvas'];scale=4
    base=Image.open(ASSETS/style['template_image']).convert('RGBA')
    layer=Image.new('RGBA',(width*scale,height*scale));draw=ImageDraw.Draw(layer)
    cx,cy=style['center'];start=style['start_angle']
    for key,color in zip(('positive','neutral','negative'),style['colors']):
        amount=float(data['percentages'][key]);end=start+amount*3.6
        rgb=tuple(int(color[i:i+2],16) for i in (1,3,5))
        light=tuple(round(v*.5+255*.5) for v in rgb)
        if amount:
            for r,c in zip(style['radii'][:2],(color,light)):
                draw.pieslice(((cx-r)*scale,(cy-r)*scale,(cx+r)*scale,(cy+r)*scale),start,end,fill=c)
        start=end
    r=style['radii'][2]
    draw.ellipse(((cx-r)*scale,(cy-r)*scale,(cx+r)*scale,(cy+r)*scale),fill='white')
    font_file=Path(requested_font) if requested_font else Path('C:/Windows/Fonts')/style['label_font']
    if not font_file.is_file():raise ValueError('情感图固定模板缺少原字体，不能静默替换')
    label_font_size = 26 * scale
    font = ImageFont.truetype(str(font_file), label_font_size)
    labels = [(f'{entry["text"]} {data["percentages"][key]}%', entry) for key, entry in style['labels'].items()]
    fit = min(entry['max_width'] * scale / draw.textlength(text, font=font) for text, entry in labels)
    if fit < 1:
        font = ImageFont.truetype(str(font_file), max(1, int(label_font_size * fit)))
    cleaner=ImageDraw.Draw(base)
    for text, entry in labels:
        cleaner.rectangle(entry['clear_box'], fill=(0, 0, 0, 0))
        _, top, _, bottom = draw.textbbox((0, 0), text, font=font)
        y = entry['swatch_center_y'] * scale - (top + bottom) / 2
        draw.text((entry['x'] * scale, y), text, font=font, fill='black')
    title_font=Path('C:/Windows/Fonts')/style['title_font'] if not requested_font else font_file
    if not title_font.is_file():raise ValueError('情感图固定模板缺少标题字体')
    title=f'{metrics["work_type"]}《{metrics["work_name"]}》相关话题舆论情感态度分布'
    title_font=ImageFont.truetype(str(title_font),32*scale)
    if draw.textlength(title,font=title_font)>(width-24)*scale:raise ValueError('情感图标题超过固定单行容量')
    cleaner.rectangle((0,0,width,65),fill=(0,0,0,0))
    draw.text((width/2*scale,32*scale),title,font=title_font,fill='black',anchor='mm')
    base.alpha_composite(layer.resize((width,height),Image.Resampling.LANCZOS))
    base.save(output/'sentiment.png',dpi=(150,150))
    raw=(output/'sentiment.png').read_bytes()
    svg=f'<svg xmlns="http://www.w3.org/2000/svg" width="865" height="520" viewBox="0 0 865 520"><image width="865" height="520" href="data:image/png;base64,{base64.b64encode(raw).decode()}"/></svg>'
    (output/'sentiment.svg').write_text(svg,encoding='utf-8')
    return {'sentiment_svg':svg,'sentiment_fidelity':{'mode':'generated_from_input',
            'output_sha256':hashlib.sha256(raw).hexdigest(),'data_binding':data}}
