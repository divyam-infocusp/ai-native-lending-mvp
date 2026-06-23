"""
Tests for the LLM document extractor (#9, Phase B) — grounded confidence + the
injectable extractor orchestration. No API key / SDK required: the vision pass is
faked.
"""
from lending.adapters.llm_ocr import FIELD_SCHEMAS, ground_fields, make_llm_extractor


def _f(field, value, quote=""):
    return {"field": field, "value": value, "source_quote": quote}


# ---------------------------------------------------------------------------
# Grounding — self-consistency
# ---------------------------------------------------------------------------

def test_unanimous_agreement_is_full_confidence_without_doc_text():
    samples = [[_f("pan", "ABCDE1234F")] for _ in range(3)]
    out = ground_fields(samples)                       # no doc_text → provenance neutral
    assert out["pan"]["value"] == "ABCDE1234F"
    assert out["pan"]["ocr_conf"] == 1.0


def test_partial_agreement_lowers_confidence():
    samples = [[_f("name", "Ravi Kumar")], [_f("name", "Ravi Kumar")], [_f("name", "Rave Kumar")]]
    out = ground_fields(samples)
    assert out["name"]["value"] == "Ravi Kumar"        # modal value wins
    assert out["name"]["ocr_conf"] == round(2 / 3, 4)  # 2 of 3 agreed


def test_field_missing_in_some_samples_lowers_confidence():
    samples = [[_f("aadhaar", "234567890124")], [_f("aadhaar", "234567890124")], []]
    out = ground_fields(samples)
    assert out["aadhaar"]["ocr_conf"] == round(2 / 3, 4)


def test_field_never_present_is_omitted():
    out = ground_fields([[_f("name", "X")], [_f("name", "X")]])
    assert "pan" not in out


# ---------------------------------------------------------------------------
# Grounding — provenance (anti-hallucination)
# ---------------------------------------------------------------------------

def test_quote_present_keeps_confidence():
    samples = [[_f("pan", "ABCDE1234F", "PAN: ABCDE1234F")] for _ in range(2)]
    out = ground_fields(samples, doc_text="Name: Ravi\nPAN: ABCDE1234F\nDOB: 1990")
    assert out["pan"]["ocr_conf"] == 1.0


def test_hallucinated_quote_is_penalised():
    # consistent value, but the cited quote is nowhere in the document → likely made up
    samples = [[_f("pan", "ZZZZZ9999Z", "PAN: ZZZZZ9999Z")] for _ in range(2)]
    out = ground_fields(samples, doc_text="Name: Ravi\nPAN: ABCDE1234F", hallucination_factor=0.5)
    assert out["pan"]["ocr_conf"] == 0.5              # 1.0 consistency × 0.5 provenance


# ---------------------------------------------------------------------------
# Grounding — numeric coercion
# ---------------------------------------------------------------------------

def test_money_fields_are_coerced_to_numbers():
    samples = [[_f("gross_monthly_income", "₹90,000")], [_f("gross_monthly_income", "90000")]]
    out = ground_fields(samples)
    assert out["gross_monthly_income"]["value"] == 90000.0   # symbols/commas stripped, both agree
    assert out["gross_monthly_income"]["ocr_conf"] == 1.0


# ---------------------------------------------------------------------------
# Extractor orchestration
# ---------------------------------------------------------------------------

def test_make_llm_extractor_samples_and_grounds():
    calls = {"n": 0}

    def fake_pass(document, doc_type, fields):
        calls["n"] += 1
        return [_f("name", "Ravi Kumar", "Name: Ravi Kumar"),
                _f("pan", "ABCDE1234F", "PAN: ABCDE1234F")]

    def load(_app, _doc):
        from lending.adapters.llm_ocr import Document
        return Document(data=b"", mime_type="application/pdf",
                        text="Name: Ravi Kumar PAN: ABCDE1234F")

    extract = make_llm_extractor(load, fake_pass, samples=3)
    out = extract("app-1", "address_proof")
    assert calls["n"] == 3                              # sampled N times
    assert out["name"]["value"] == "Ravi Kumar"
    assert out["pan"]["ocr_conf"] == 1.0               # unanimous + quotes present


def test_unknown_doc_type_returns_empty():
    extract = make_llm_extractor(lambda a, d: None, lambda *a: [], samples=2)
    assert extract("app-1", "bank_statement") == {}    # out of scope (#53)
    assert "bank_statement" not in FIELD_SCHEMAS
