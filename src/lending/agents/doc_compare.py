"""
Type-aware value comparison for cross-source document checks (#19).

The Document Intelligence Agent cross-checks a canonical field across every
document that reported it (see `document_intelligence.py`). *How* two values are
compared depends on the field's **semantic type**, not on which documents they
came from — so this stays fully dynamic (no hardcoded document pairs):

  - id / reference fields (PAN, Aadhaar) → exact match after normalization
  - name fields                          → token-set match tolerant of order +
                                           initials ("SHARMA PRIYA" ≡ "Priya S.")
  - money fields                         → percentage tolerance (Form-16/12 won't
                                           byte-match a payslip due to bonuses)
  - date fields                          → parsed/normalized equality
  - everything else                      → normalized string equality

Tolerances come from the versioned CONFIDENCE_POLICY (§16.9), never hardcoded.
"""
from __future__ import annotations

import re

from lending.policy import CONFIDENCE_POLICY

# Canonical field → comparison strategy. Keyed by *semantic type* of the field.
ID_FIELDS = frozenset({"pan", "aadhaar", "ifsc"})
NAME_FIELDS = frozenset({"name", "full_name", "employer_name"})
MONEY_FIELDS = frozenset({"gross_monthly_income", "net_monthly_income", "monthly_income"})
DATE_FIELDS = frozenset({"date_of_birth"})


def _norm_text(value) -> str:
    """Uppercase, strip punctuation, collapse internal whitespace."""
    s = re.sub(r"[^A-Za-z0-9 ]+", " ", str(value)).upper()
    return re.sub(r"\s+", " ", s).strip()


def _norm_id(value) -> str:
    """Identifiers compare with all spaces/punctuation removed."""
    return re.sub(r"[^A-Za-z0-9]+", "", str(value)).upper()


def _norm_date(value) -> str:
    """Best-effort date normalization to YYYYMMDD. Handles ISO (YYYY-MM-DD) and
    DD/MM/YYYY (the common Indian forms); falls back to digit string."""
    digits = re.findall(r"\d+", str(value))
    if len(digits) == 3:
        a, b, c = digits
        if len(a) == 4:                      # YYYY-MM-DD
            return f"{a}{int(b):02d}{int(c):02d}"
        if len(c) == 4:                      # DD/MM/YYYY
            return f"{c}{int(b):02d}{int(a):02d}"
    return "".join(digits)


def _tokens(value) -> list[str]:
    return [t for t in _norm_text(value).split(" ") if t]


def _name_match(a, b, *, min_ratio: float) -> bool:
    """True when the two names plausibly refer to the same person.

    Order-insensitive and initial-tolerant: each token of the *shorter* name must
    prefix-match some token of the longer name. The fraction that match must meet
    `min_ratio`. ("PRIYA SHARMA" vs "SHARMA P" → P prefixes nothing? P prefixes
    SHARMA? no — P prefixes PRIYA; SHARMA matches SHARMA → 2/2.)"""
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    short, long = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    matched = sum(
        1 for s in short
        if any(l.startswith(s) or s.startswith(l) for l in long)
    )
    return (matched / len(short)) >= min_ratio


def _money_match(a, b, *, tol_pct: float) -> bool:
    try:
        x, y = float(a), float(b)
    except (TypeError, ValueError):
        return False
    if x == y:
        return True
    biggest = max(abs(x), abs(y))
    if biggest == 0:
        return True
    return abs(x - y) / biggest <= tol_pct


def values_match(field_name: str, a, b, *, policy_version: str = "v1") -> bool:
    """Type-aware equality for a canonical field. Both values are assumed present
    (the caller skips comparison when either side is missing)."""
    if policy_version not in CONFIDENCE_POLICY:
        raise ValueError(f"Unknown policy_version: {policy_version!r}")
    cfg = CONFIDENCE_POLICY[policy_version]

    if field_name in ID_FIELDS:
        return _norm_id(a) == _norm_id(b)
    if field_name in NAME_FIELDS:
        return _name_match(a, b, min_ratio=cfg["name_match_min_ratio"])
    if field_name in MONEY_FIELDS:
        return _money_match(a, b, tol_pct=cfg["income_match_tolerance_pct"])
    if field_name in DATE_FIELDS:
        return _norm_date(a) == _norm_date(b)
    return _norm_text(a) == _norm_text(b)
