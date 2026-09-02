"""Colour tokens and Plotly template for the Data Quality page.

Everything here is built from mnid.core.constants -- the app's single
existing colour source -- plus the handful of tokens that module doesn't
define (brand chrome, coverage-grid present/missing, severity mapping).
"""
import plotly.graph_objects as go
import plotly.io as pio

from mnid.core.constants import (
    OK_C, WARN_C, DANGER_C, INFO_C, MUTED, GRID_C, BG, BORDER, TEXT, DIM, FONT,
)


def _tint(hex_color, amount):
    """Blend hex_color toward white by `amount` (0-1)."""
    hex_color = hex_color.lstrip('#')
    r, g, b = (int(hex_color[i:i + 2], 16) for i in (0, 2, 4))
    r = round(r + (255 - r) * amount)
    g = round(g + (255 - g) * amount)
    b = round(b + (255 - b) * amount)
    return f'#{r:02X}{g:02X}{b:02X}'


# Primary chrome / headings / active nav -- not in mnid.core.constants.
BRAND = '#006401'
# Pale wash of BRAND, for selected-tab/active backgrounds.
BRAND_TINT = _tint(BRAND, 0.92)

# Coverage grid cells.
COVERAGE_PRESENT = '#15803D'
# Pale tint of DANGER_C, per the fixed colour table in new_page.md.
COVERAGE_MISSING = _tint(DANGER_C, 0.85)

# Fixed severity -> colour mapping used everywhere on this page.
SEVERITY = {
    'high':   DANGER_C,
    'medium': WARN_C,
    'low':    DIM,
}

# One Plotly template for this page's figures. Registered under pio.templates
# so it can be referenced by name, but NOT set as the process-wide
# pio.templates.default -- this app is a single Dash process serving every
# other page's charts too, and flipping the global default here would
# silently restyle all of them. Data-quality figures opt in explicitly with
# `template="dq"` (or fig.update_layout(template=DQ_TEMPLATE)).
DQ_TEMPLATE = go.layout.Template(
    layout=go.Layout(
        paper_bgcolor=BG,
        plot_bgcolor=BG,
        font=dict(family=FONT, color=DIM),
        colorway=[COVERAGE_PRESENT, OK_C, WARN_C, DANGER_C, INFO_C, MUTED],
        margin=dict(l=40, r=20, t=30, b=40),
        xaxis=dict(showgrid=False, tickfont=dict(color=DIM), linecolor=BORDER),
        yaxis=dict(showgrid=True, gridcolor=GRID_C, zerolinecolor=GRID_C, tickfont=dict(color=DIM), linecolor=BORDER),
    )
)
pio.templates['dq'] = DQ_TEMPLATE
