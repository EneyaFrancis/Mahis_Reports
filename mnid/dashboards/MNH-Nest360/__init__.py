"""Nest360 indicator definitions only -- the standalone compact-dashboard
layout was removed (duplicated render_country_profile/render_operational_
readiness, was only reachable via the disabled MNH switcher tabs). Kept as a
package (not a bare module) because mnid.aggregation.engine still loads
indicators.py from here for the aggregate build -- see that file's own
docstring for why this is on the list to migrate into validated_dashboard.json
properly rather than staying a separate Python source of indicator defs."""
