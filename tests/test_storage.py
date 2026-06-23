"""
Tests for document storage (#9, Phase A) — the local-volume store + the
store→extractor loader.
"""
import io

from lending.adapters.llm_ocr import make_store_loader
from lending.storage import LocalDocumentStore


def test_local_store_roundtrip(tmp_path):
    store = LocalDocumentStore(str(tmp_path))
    ref = store.put("app1", "salary_slips", b"PDFBYTES", "application/pdf")
    assert ref.startswith("file://")
    got = store.get("app1", "salary_slips")
    assert got is not None
    assert got.data == b"PDFBYTES"
    assert got.content_type == "application/pdf"


def test_missing_document_returns_none(tmp_path):
    assert LocalDocumentStore(str(tmp_path)).get("nope", "salary_slips") is None


def test_latest_write_wins(tmp_path):
    store = LocalDocumentStore(str(tmp_path))
    store.put("app1", "form16", b"v1", "application/pdf")
    store.put("app1", "form16", b"v2", "application/pdf")
    assert store.get("app1", "form16").data == b"v2"


def test_path_traversal_is_defanged(tmp_path):
    store = LocalDocumentStore(str(tmp_path))
    store.put("../../etc", "../passwd", b"x", "text/plain")
    # nothing escaped the root directory
    escaped = [p for p in tmp_path.parent.rglob("passwd*") if tmp_path not in p.parents and p != tmp_path]
    assert escaped == []


def test_store_loader_reads_pdf(tmp_path):
    store = LocalDocumentStore(str(tmp_path))
    store.put("app1", "form16", b"%PDF-1.4 not-really-a-pdf", "application/pdf")
    load = make_store_loader(store)
    doc = load("app1", "form16")
    assert doc.mime_type == "application/pdf"
    assert doc.data  # bytes present (text layer may be None for a non-parseable PDF)


def test_store_loader_feeds_extractor(tmp_path):
    # the live wiring chain: stored bytes → loader → extractor → grounded fields
    from lending.adapters.llm_ocr import make_llm_extractor

    store = LocalDocumentStore(str(tmp_path))
    store.put("app1", "pan_card", b"%PDF-1.4 PAN ABCDE1234F", "application/pdf")

    def fake_pass(_doc, _doc_type, _fields):
        return [{"field": "pan", "value": "ABCDE1234F", "source_quote": "PAN ABCDE1234F"}]

    extract = make_llm_extractor(make_store_loader(store), fake_pass, samples=2)
    out = extract("app1", "pan_card")
    assert out["pan"]["value"] == "ABCDE1234F"


def test_store_loader_handles_image(tmp_path):
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, format="PNG")
    store = LocalDocumentStore(str(tmp_path))
    store.put("app1", "aadhaar_card", buf.getvalue(), "image/png")
    doc = make_store_loader(store)("app1", "aadhaar_card")
    assert doc.mime_type == "image/jpeg"   # downscale path re-encodes to JPEG
    assert doc.text is None                # images have no text layer
