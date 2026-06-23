"""
Isolation tests for identity-field validators (#5).

Covers PAN structure, IFSC structure, and Aadhaar format validation
(12 digits, spaces stripped — Verhoeff check omitted per policy).
"""
import pytest
from lending.confidence import validate_aadhaar, validate_ifsc, validate_pan


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
# Aadhaar — format-only (12 digits, spaces stripped)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [
    "223344556677",        # plain 12 digits
    "2233 4455 6677",      # LLM-style spaces stripped before check
    "123456789012",
])
def test_valid_aadhaar(value):
    assert validate_aadhaar(value).valid is True


@pytest.mark.parametrize("value", [
    "1234567890",     # 10 digits
    "1234567890123",  # 13 digits
    "12345678901a",   # non-numeric
    "",               # empty
])
def test_malformed_aadhaar_fails(value):
    assert validate_aadhaar(value).valid is False


def test_aadhaar_spaces_stripped():
    """LLMs often return Aadhaar with spaces; validator must strip them."""
    assert validate_aadhaar("2233 4455 6677").valid is True
    assert validate_aadhaar("2233-4455-6677").valid is False  # hyphens not stripped
