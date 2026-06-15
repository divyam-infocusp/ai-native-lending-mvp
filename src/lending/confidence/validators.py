"""
Format / checksum validators for identity fields (pure, no I/O).

Each validator returns a ValidatorResult the Confidence Service consumes — a
failing validator drives the FORMAT_INVALID flag and zeroes the validator
ratio in the composite (§16.4).

Note on PAN: the Income-Tax PAN has no *public* check-digit algorithm (the
10th character is a checksum but its derivation is not published), so we
validate strict structure only and label it accordingly. Aadhaar uses the
Verhoeff scheme, which we verify in full.
"""
import re

from .models import ValidatorResult

# ---------------------------------------------------------------------------
# PAN — structure: 5 letters, 4 digits, 1 letter (e.g. ABCDE1234F)
# ---------------------------------------------------------------------------
_PAN_RE = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")


def validate_pan(value: str, field_name: str = "pan_number") -> ValidatorResult:
    return ValidatorResult(field_name=field_name, valid=bool(_PAN_RE.match(value or "")))


# ---------------------------------------------------------------------------
# IFSC — 4 letters, a literal 0, then 6 alphanumerics (e.g. HDFC0001234)
# ---------------------------------------------------------------------------
_IFSC_RE = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")


def validate_ifsc(value: str, field_name: str = "ifsc") -> ValidatorResult:
    return ValidatorResult(field_name=field_name, valid=bool(_IFSC_RE.match(value or "")))


# ---------------------------------------------------------------------------
# Aadhaar — 12 digits with a trailing Verhoeff check digit
# ---------------------------------------------------------------------------

# Dihedral D5 multiplication table.
_VERHOEFF_D = [
    [0, 1, 2, 3, 4, 5, 6, 7, 8, 9],
    [1, 2, 3, 4, 0, 6, 7, 8, 9, 5],
    [2, 3, 4, 0, 1, 7, 8, 9, 5, 6],
    [3, 4, 0, 1, 2, 8, 9, 5, 6, 7],
    [4, 0, 1, 2, 3, 9, 5, 6, 7, 8],
    [5, 9, 8, 7, 6, 0, 4, 3, 2, 1],
    [6, 5, 9, 8, 7, 1, 0, 4, 3, 2],
    [7, 6, 5, 9, 8, 2, 1, 0, 4, 3],
    [8, 7, 6, 5, 9, 3, 2, 1, 0, 4],
    [9, 8, 7, 6, 5, 4, 3, 2, 1, 0],
]

# Permutation table, derived from the base permutation to avoid transcription
# errors: p[0] = identity; p[i][j] = base[p[i-1][j]].
_VERHOEFF_BASE_PERM = [1, 5, 7, 6, 2, 8, 3, 0, 9, 4]
_VERHOEFF_P = [list(range(10))]
for _i in range(1, 8):
    _VERHOEFF_P.append([_VERHOEFF_BASE_PERM[_VERHOEFF_P[_i - 1][_j]] for _j in range(10)])


def _verhoeff_check(number: str) -> bool:
    """True if the full digit string (incl. check digit) satisfies Verhoeff."""
    c = 0
    for i, ch in enumerate(reversed(number)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[i % 8][int(ch)]]
    return c == 0


_AADHAAR_RE = re.compile(r"^[0-9]{12}$")


def validate_aadhaar(value: str, field_name: str = "aadhaar_number") -> ValidatorResult:
    value = value or ""
    valid = bool(_AADHAAR_RE.match(value)) and _verhoeff_check(value)
    return ValidatorResult(field_name=field_name, valid=valid)
