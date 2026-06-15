"""
Isolation tests for identity-field validators (#5).

Covers PAN structure, IFSC structure, and Aadhaar Verhoeff checksum
(round-trip generation + single-digit corruption detection).
"""
import pytest
from lending.confidence import validate_aadhaar, validate_ifsc, validate_pan
from lending.confidence.validators import _VERHOEFF_D, _VERHOEFF_P


# ---------------------------------------------------------------------------
# PAN
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pan", ["ABCDE1234F", "ZZZZZ9999Z"])
def test_valid_pan(pan):
    assert validate_pan(pan).valid is True


@pytest.mark.parametrize("pan", [
    "ABCD1234F",      # only 4 leading letters
    "ABCDE123F",      # only 3 digits
    "ABCDE12345",     # missing trailing letter
    "abcde1234f",     # lowercase
    "ABCDE1234FG",    # too long
    "",               # empty
])
def test_invalid_pan(pan):
    assert validate_pan(pan).valid is False


def test_pan_field_name_default():
    assert validate_pan("ABCDE1234F").field_name == "pan_number"


# ---------------------------------------------------------------------------
# IFSC
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ifsc", ["HDFC0001234", "SBIN0XYZ123"])
def test_valid_ifsc(ifsc):
    assert validate_ifsc(ifsc).valid is True


@pytest.mark.parametrize("ifsc", [
    "HDFC1001234",   # 5th char not 0
    "HDF0001234",    # only 3 leading letters
    "HDFC000123",    # too short
    "",              # empty
])
def test_invalid_ifsc(ifsc):
    assert validate_ifsc(ifsc).valid is False


# ---------------------------------------------------------------------------
# Aadhaar Verhoeff
# ---------------------------------------------------------------------------

def _verhoeff_check_digit(payload_11: str) -> int:
    """Generate the Verhoeff check digit for an 11-digit payload."""
    inv = [0, 4, 3, 2, 1, 5, 6, 7, 8, 9]
    c = 0
    for i, ch in enumerate(reversed(payload_11)):
        c = _VERHOEFF_D[c][_VERHOEFF_P[(i + 1) % 8][int(ch)]]
    return inv[c]


def _make_valid_aadhaar(payload_11: str) -> str:
    return payload_11 + str(_verhoeff_check_digit(payload_11))


def test_generated_aadhaar_validates():
    for payload in ["12345678901", "99887766554", "10000000000"]:
        aadhaar = _make_valid_aadhaar(payload)
        assert validate_aadhaar(aadhaar).valid is True, f"{aadhaar} should be valid"


def test_single_digit_corruption_fails():
    aadhaar = _make_valid_aadhaar("12345678901")
    # Flip the last payload digit → checksum must reject
    corrupted = aadhaar[:10] + str((int(aadhaar[10]) + 1) % 10) + aadhaar[11]
    assert validate_aadhaar(corrupted).valid is False


def test_wrong_check_digit_fails():
    aadhaar = _make_valid_aadhaar("12345678901")
    bad = aadhaar[:11] + str((int(aadhaar[11]) + 1) % 10)
    assert validate_aadhaar(bad).valid is False


@pytest.mark.parametrize("value", [
    "1234567890",     # 10 digits
    "1234567890123",  # 13 digits
    "12345678901a",   # non-numeric
    "",               # empty
])
def test_malformed_aadhaar_fails(value):
    assert validate_aadhaar(value).valid is False


def test_verhoeff_p_rows_are_permutations():
    """Guard: every permutation-table row must be a permutation of 0-9."""
    for row in _VERHOEFF_P:
        assert sorted(row) == list(range(10))
