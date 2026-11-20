"""Charts use retained human-reference templates rather than a free redraw."""
from trend_template import render_trend, render_sentiment


def make_charts(metrics, output, requested_font=None):
    result = render_trend(metrics, output, requested_font)
    if metrics.get("sentiment"):
        result.update(render_sentiment(metrics, output, requested_font))
    return result
