"""
Format / checksum validators for identity fields (pure, no I/O).

Each validator returns a ValidatorResult the Confidence Service consumes — a
failing validator drives the FORMAT_INVALID flag and zeroes the validator
ratio in the composite (§16.4).

Note on PAN: the Income-Tax PAN has no *public* check-digit algorithm (the
10th character is a checksum but its derivation is not published), so we
validate strict structure only and label it accordingly. Aadhaar format is
validated as 12 digits only (Verhoeff check omitted — UIDAI does not publish
the check-digit derivation for third-party use, and test/synthetic numbers
often fail it).
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
# Aadhaar — 12 digits (format-only; no Verhoeff check)
# ---------------------------------------------------------------------------
_AADHAAR_RE = re.compile(r"^[0-9]{12}$")


def validate_aadhaar(value: str, field_name: str = "aadhaar_number") -> ValidatorResult:
    # LLMs often return Aadhaar with spaces ("2233 4455 6677") — strip before checking.
    value = re.sub(r"\s+", "", value or "")
    return ValidatorResult(field_name=field_name, valid=bool(_AADHAAR_RE.match(value)))
