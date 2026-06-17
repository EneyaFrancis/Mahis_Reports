"""Compatibility entry point for the MNID dashboard."""
from mnid.app import update_heatmap_view, update_compare_charts


def render_mnid_dashboard(data_opd, config,
                          facility_code, start_date, end_date,
                          scope_meta: dict | None = None,
                          sub_tab: str = 'Default'):
    from mnid.app import render_mnid_dashboard as _render
    return _render(data_opd, config, facility_code, start_date, end_date, scope_meta, sub_tab)

__all__ = [
    'render_mnid_dashboard',
    'update_heatmap_view',
    'update_compare_charts',
]
