"""Duplicate-patient matching for the Data Quality page's Duplicates tab.

Operates entirely in Python over a pre-fetched patient roster (one row per
person_id, on the order of a thousand rows) -- bucketing on a normalised key
per rule rather than comparing pairwise, then merging overlapping matches
from different rules with a union-find so no patient is counted twice.

Expects the roster as a pandas DataFrame with columns: person_id,
family_name, given_name, gender, birthdate, identifier, village, ta,
home_district, cell, facility, encounter_count, first_encounter,
last_encounter.
"""
import re

RULES = {
    "D1": {
        "label": "Exact identity",
        "fields": "identifier + given name + family name + gender + birthdate + home district + TA + village",
        "confidence": 0.98,
    },
    "D2": {
        "label": "Without identifier",
        "fields": "given name + family name + gender + birthdate + home district + TA + village",
        "confidence": 0.90,
    },
    "D3": {
        "label": "Name without location",
        "fields": "given name + family name + gender + birthdate",
        "confidence": 0.75,
    },
    "D4": {
        "label": "Other",
        "fields": "user-selected fields",
        "confidence": 0.50,
    },
}
RULE_ORDER = ["D1", "D2", "D3", "D4"]

# Roster columns available to D4 ("Other") for the user to build a custom
# rule from -- see FIELD_OPTIONS for the (value, label) pairs shown in the
# Duplicates tab's field picker.
FIELD_OPTIONS = [
    ("identifier", "Identifier"),
    ("given_name", "Given name"),
    ("family_name", "Family name"),
    ("gender", "Gender"),
    ("birthdate", "Birthdate"),
    ("home_district", "Home district"),
    ("ta", "TA"),
    ("village", "Village"),
    ("cell", "Phone"),
]


def normalize_text(value):
    """Lowercase, strip non-alphanumerics."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def normalize_phone(value):
    """Digits only; empty unless there are enough digits to mean something (9+)."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) >= 9 else ""


def _birthdate_key(value):
    if value is None or (isinstance(value, float) and value != value):  # NaN
        return ""
    return str(value)[:10]


# Per-field normaliser used when building a rule's key -- most fields are
# plain text, birthdate and phone need their own normalisation.
_FIELD_NORMALIZERS = {
    "identifier": normalize_text,
    "given_name": normalize_text,
    "family_name": normalize_text,
    "gender": normalize_text,
    "birthdate": _birthdate_key,
    "home_district": normalize_text,
    "ta": normalize_text,
    "village": normalize_text,
    "cell": normalize_phone,
}


def _make_key_fn(rule_id, fields):
    """A key function requiring every one of `fields` to be present
    (normalised, non-empty) on the row -- returns None otherwise, so a
    patient missing any of the rule's fields never falls into a bucket."""
    def key_fn(row):
        values = tuple(_FIELD_NORMALIZERS.get(f, normalize_text)(row.get(f)) for f in fields)
        if not fields or not all(values):
            return None
        return (rule_id,) + values
    return key_fn


_key_d1 = _make_key_fn("D1", ["identifier", "given_name", "family_name", "gender", "birthdate", "home_district", "ta", "village"])
_key_d2 = _make_key_fn("D2", ["given_name", "family_name", "gender", "birthdate", "home_district", "ta", "village"])
_key_d3 = _make_key_fn("D3", ["given_name", "family_name", "gender", "birthdate"])

_KEY_FNS = {"D1": _key_d1, "D2": _key_d2, "D3": _key_d3}


class _UnionFind:
    def __init__(self):
        self.parent = {}

    def find(self, x):
        self.parent.setdefault(x, x)
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[ra] = rb


def _bucket(roster_df, key_fn):
    buckets = {}
    for row in roster_df.itertuples(index=False):
        key = key_fn(row._asdict())
        if key is None:
            continue
        buckets.setdefault(key, []).append(row.person_id)
    return {k: v for k, v in buckets.items() if len(v) > 1}


def match_duplicates(roster_df, enabled_rules, other_fields=None):
    """Returns (groups, per_rule_counts).

    groups: list of {members: [person_id...], confidence: float, rules: [rule_id...]},
    sorted by confidence descending. Only groups with 2+ members are included.

    per_rule_counts: {rule_id: {"groups": n, "records": n}} for ALL four
    rules, independent of which are enabled -- lets the per-rule table stay
    informative even for a rule the user has switched off.

    other_fields: roster columns (see FIELD_OPTIONS) the user picked for D4
    ("Other") -- D4 matches nothing until at least one field is picked.
    """
    key_fns = dict(_KEY_FNS)
    key_fns["D4"] = _make_key_fn("D4", other_fields or [])

    per_rule_buckets = {rid: _bucket(roster_df, fn) for rid, fn in key_fns.items()}
    per_rule_counts = {
        rid: {"groups": len(buckets), "records": sum(len(v) for v in buckets.values())}
        for rid, buckets in per_rule_buckets.items()
    }

    uf = _UnionFind()
    for rid in enabled_rules:
        for members in per_rule_buckets.get(rid, {}).values():
            first = members[0]
            for m in members[1:]:
                uf.union(first, m)

    groups_map = {}
    for pid in roster_df["person_id"]:
        root = uf.find(pid) if pid in uf.parent else pid
        groups_map.setdefault(root, []).append(pid)
    groups_map = {root: members for root, members in groups_map.items() if len(members) > 1}

    group_rules = {root: set() for root in groups_map}
    for rid in enabled_rules:
        for members in per_rule_buckets.get(rid, {}).values():
            root = uf.find(members[0])
            if root in group_rules:
                group_rules[root].add(rid)

    groups = [
        {
            "members": members,
            "rules": sorted(group_rules.get(root, set())),
            "confidence": max((RULES[r]["confidence"] for r in group_rules.get(root, set())), default=0.0),
        }
        for root, members in groups_map.items()
    ]
    groups.sort(key=lambda g: g["confidence"], reverse=True)
    return groups, per_rule_counts
