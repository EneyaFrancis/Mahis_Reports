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
    "D1": {"label": "Exact identity", "fields": "family + given + birthdate + gender", "confidence": 0.97},
    "D2": {"label": "Name and date of birth", "fields": "family + given + birthdate", "confidence": 0.90},
    "D3": {"label": "Phonetic name and DOB", "fields": "phonetic(family) + phonetic(given) + birthdate", "confidence": 0.78},
    "D4": {"label": "Name, gender, village", "fields": "family + given + gender + village", "confidence": 0.55},
    "D5": {"label": "Shared phone", "fields": "normalised cell, 9+ digits", "confidence": 0.42},
    "D6": {"label": "Shared identifier", "fields": "normalised identifier", "confidence": 0.99},
}
RULE_ORDER = ["D1", "D2", "D3", "D4", "D5", "D6"]

_SOUNDEX_CODES = {}
for _chars, _code in [("BFPV", "1"), ("CGJKQSXZ", "2"), ("DT", "3"), ("L", "4"), ("MN", "5"), ("R", "6")]:
    for _ch in _chars:
        _SOUNDEX_CODES[_ch] = _code


def soundex(word):
    """Standard Soundex phonetic key. DuckDB has no soundex() -- see new_page.md -- so this runs in Python."""
    word = "".join(ch for ch in str(word or "").upper() if ch.isalpha())
    if not word:
        return ""
    codes = [_SOUNDEX_CODES.get(ch, "") for ch in word]
    result = [word[0]]
    prev_code = codes[0]
    for ch, code in zip(word[1:], codes[1:]):
        if code and code != prev_code:
            result.append(code)
        if ch not in ("H", "W"):
            prev_code = code
    return ("".join(result) + "000")[:4]


def normalize_text(value):
    """Lowercase, strip non-alphanumerics."""
    return re.sub(r"[^a-z0-9]", "", str(value or "").strip().lower())


def normalize_phone(value):
    """Digits only; empty unless there are enough digits to mean something (9+, per D5)."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits if len(digits) >= 9 else ""


def _birthdate_key(value):
    if value is None or (isinstance(value, float) and value != value):  # NaN
        return ""
    return str(value)[:10]


def _key_d1(row):
    fam, giv, dob, gender = normalize_text(row["family_name"]), normalize_text(row["given_name"]), _birthdate_key(row["birthdate"]), normalize_text(row["gender"])
    return ("D1", fam, giv, dob, gender) if (fam and giv and dob and gender) else None


def _key_d2(row):
    fam, giv, dob = normalize_text(row["family_name"]), normalize_text(row["given_name"]), _birthdate_key(row["birthdate"])
    return ("D2", fam, giv, dob) if (fam and giv and dob) else None


def _key_d3(row):
    fam, giv, dob = normalize_text(row["family_name"]), normalize_text(row["given_name"]), _birthdate_key(row["birthdate"])
    if not (fam and giv and dob):
        return None
    return ("D3", soundex(row["family_name"]), soundex(row["given_name"]), dob)


def _key_d4(row):
    fam, giv, gender, village = normalize_text(row["family_name"]), normalize_text(row["given_name"]), normalize_text(row["gender"]), normalize_text(row["village"])
    return ("D4", fam, giv, gender, village) if (fam and giv and gender and village) else None


def _key_d5(row):
    phone = normalize_phone(row["cell"])
    return ("D5", phone) if phone else None


def _key_d6(row):
    ident = normalize_text(row["identifier"])
    return ("D6", ident) if ident else None


_KEY_FNS = {"D1": _key_d1, "D2": _key_d2, "D3": _key_d3, "D4": _key_d4, "D5": _key_d5, "D6": _key_d6}


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


def match_duplicates(roster_df, enabled_rules):
    """Returns (groups, per_rule_counts).

    groups: list of {members: [person_id...], confidence: float, rules: [rule_id...]},
    sorted by confidence descending. Only groups with 2+ members are included.

    per_rule_counts: {rule_id: {"groups": n, "records": n}} for ALL six rules,
    independent of which are enabled -- lets the per-rule table stay
    informative even for a rule the user has switched off.
    """
    per_rule_buckets = {rid: _bucket(roster_df, fn) for rid, fn in _KEY_FNS.items()}
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
