"""
LLM document extractor (#9, Phase B) — read canonical fields off a real document
with a *multimodal* LLM, producing the same `{field: {value, ocr_conf}}` shape the
Document Intelligence agent (#19) already consumes.

The hard constraint (§16.4): confidence must be **grounded, never self-reported**.
So we never ask the model "how sure are you?". Instead `ocr_conf` is derived from
two empirical signals computed here:

  • self-consistency — the same extraction is sampled N times (temperature > 0);
    a field's confidence is the fraction of samples that agree on its value.
  • provenance — each value comes with a VERBATIM `source_quote`; if that quote is
    not actually present in the document's text, the value is likely hallucinated
    and its confidence is knocked down.

The agent still does the rest of §16.4 (cross-source agreement + validators) on
top of this `ocr_conf`, and §2.1 still holds: the LLM extracts, deterministic code
decides. Bank statements are out of scope (→ #53); this covers the four
identity/income docs.

Everything is injectable: `make_llm_extractor(load_document, vision_pass)` takes a
document loader and a single-pass extractor, so tests run with fakes and no API
key. `gemini_vision_pass()` is the live pass; `python -m lending.adapters.llm_ocr
<file> <doc_type>` runs it against a local file.
"""
from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Callable, Optional

from pydantic import BaseModel

# Canonical fields each document type contributes (mirrors the mock's per-doc set;
# bank_statement is intentionally excluded — see #53).
FIELD_SCHEMAS: dict[str, list[str]] = {
    "identity_proof": ["name", "date_of_birth", "aadhaar", "address"],
    "address_proof": ["name", "date_of_birth", "pan"],
    "salary_slips": ["name", "employer_name", "gross_monthly_income", "net_monthly_income"],
    "form16": ["name", "pan", "employer_name", "gross_monthly_income"],
}

_NUMERIC_FIELDS = frozenset({"gross_monthly_income", "net_monthly_income"})


@dataclass(frozen=True)
class Document:
    data: bytes
    mime_type: str
    text: Optional[str] = None      # text layer (digital PDFs) → enables provenance


# A single LLM extraction pass over one document → a list of
# {"field", "value", "source_quote"} for the fields it found.
VisionPass = Callable[[Document, str, list[str]], list[dict]]
# Loads the stored document for (application_id, doc_type).
LoadDocument = Callable[[str, str], Document]


# ---------------------------------------------------------------------------
# Grounding (pure) — turn N sampled extractions into {field: {value, ocr_conf}}
# ---------------------------------------------------------------------------

def _num(v) -> Optional[float]:
    digits = re.sub(r"[^0-9.]", "", str(v))
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _normalize(field: str, value) -> str:
    """Canonical form for agreement comparison (not the stored value)."""
    if field in _NUMERIC_FIELDS:
        n = _num(value)
        return f"{n:.0f}" if n is not None else ""
    return " ".join(str(value).strip().lower().split())


def _coerce(field: str, value):
    """The value as stored: numbers for money fields, trimmed string otherwise."""
    if field in _NUMERIC_FIELDS:
        n = _num(value)
        return n if n is not None else None
    return str(value).strip()


def _squash(s: str) -> str:
    return re.sub(r"\s+", "", str(s)).lower()


def _quote_present(quote: str, doc_text: str) -> bool:
    q = _squash(quote)
    return bool(q) and q in _squash(doc_text)


def ground_fields(
    samples: list[list[dict]],
    doc_text: Optional[str] = None,
    *,
    hallucination_factor: float = 0.5,
) -> dict:
    """Combine N sampled extractions into a grounded `{field: {value, ocr_conf,
    source_quote}}`. ocr_conf = self-consistency × provenance. Fields never seen
    (or that coerce to empty) are omitted, matching the ExtractFn contract."""
    n = len(samples)
    if n == 0:
        return {}

    by_field: dict[str, list[tuple]] = defaultdict(list)
    for run in samples:
        for fe in run:
            field, value = fe.get("field"), fe.get("value")
            if field and value not in (None, ""):
                by_field[field].append((value, fe.get("source_quote", "")))

    out: dict = {}
    for field, observations in by_field.items():
        normalized = [(_normalize(field, v), v, q) for v, q in observations]
        counts = Counter(nv for nv, _, _ in normalized if nv != "")
        if not counts:
            continue
        modal_norm, agree = counts.most_common(1)[0]
        value_raw, quote = next((v, q) for nv, v, q in normalized if nv == modal_norm)
        coerced = _coerce(field, value_raw)
        if coerced in (None, ""):
            continue

        consistency = agree / n
        provenance = 1.0
        if doc_text:
            provenance = 1.0 if _quote_present(quote, doc_text) else hallucination_factor
        out[field] = {
            "value": coerced,
            "ocr_conf": round(consistency * provenance, 4),
            "source_quote": quote,
        }
    return out


# ---------------------------------------------------------------------------
# Extractor factory
# ---------------------------------------------------------------------------

def make_llm_extractor(
    load_document: LoadDocument,
    vision_pass: VisionPass,
    *,
    samples: int = 3,
    on_progress: Optional[Callable[[str], None]] = None,
):
    """Build an ExtractFn: load the document, sample the LLM `samples` times, and
    ground the result. `load_document` and `vision_pass` are injected (fakes in
    tests, live impls in prod). `on_progress` (optional) receives human-readable
    progress lines — the CLI prints them; the worker leaves it None (silent)."""

    def emit(msg: str) -> None:
        if on_progress:
            on_progress(msg)

    def extract(application_id: str, doc_type: str) -> dict:
        fields = FIELD_SCHEMAS.get(doc_type)
        if not fields:
            emit(f"[{doc_type}] not in scope — skipping")
            return {}
        emit(f"[{doc_type}] loading document…")
        document = load_document(application_id, doc_type)
        emit(f"[{doc_type}] {document.mime_type}, text layer: "
             f"{'yes' if getattr(document, 'text', None) else 'no (provenance unverified)'}")
        import time

        runs = []
        for i in range(samples):
            emit(f"[{doc_type}] sample {i + 1}/{samples}: calling model… (waiting on the API)")
            t0 = time.monotonic()
            run = vision_pass(document, doc_type, fields)
            dt = time.monotonic() - t0
            runs.append(run)
            found = ", ".join(f"{fe['field']}={fe['value']!r}" for fe in run) or "(nothing)"
            emit(f"[{doc_type}] sample {i + 1}/{samples}: {found}  ({dt:.1f}s)")
        result = ground_fields(runs, getattr(document, "text", None))
        for field, rec in result.items():
            emit(f"[{doc_type}]   ⮑ {field} = {rec['value']!r}  (conf {rec['ocr_conf']})")
        return result

    return extract


# ---------------------------------------------------------------------------
# Live Gemini vision pass + helpers (lazy imports — no SDK/key needed to import)
# ---------------------------------------------------------------------------

class _ExtractedField(BaseModel):
    field: str
    value: str
    source_quote: str            # verbatim text the value was read from


class _Extraction(BaseModel):
    fields: list[_ExtractedField]


_PROMPT = (
    "You are extracting fields from an Indian KYC / income document of type '{doc_type}'.\n"
    "Extract ONLY these fields, if present: {fields}.\n"
    "For each field you find, return its value and a short `source_quote` copied "
    "VERBATIM from the document where you read it. Do NOT guess or infer values that "
    "are not visibly present — omit a field rather than fabricate it. For monetary "
    "fields return digits only (no currency symbols or commas)."
)


def gemini_vision_pass(*, model: Optional[str] = None, temperature: float = 0.4,
                       retries: int = 3, backoff_s: float = 1.5) -> VisionPass:
    """A live single-pass extractor backed by Gemini (multimodal). Temperature > 0
    so repeated samples vary, which is what self-consistency measures. Transient
    connection/DNS failures are retried with backoff (they're common on flaky
    networks — a single name-resolution blip shouldn't fail an extraction)."""
    from lending.agents.llm import model_pro

    chosen = model or model_pro()

    def vision_pass(document: Document, doc_type: str, fields: list[str]) -> list[dict]:
        import sys
        import time

        from google import genai
        from google.genai import types

        def _t(label: str, since: float) -> None:
            print(f"      · {label}: {time.monotonic() - since:.2f}s", file=sys.stderr, flush=True)

        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GOOGLE_API_KEY is not set")

        t = time.monotonic()
        client = genai.Client(api_key=api_key)
        _t("client init", t)

        size_kb = len(document.data) / 1024
        print(f"      · payload: {size_kb:.0f} KB {document.mime_type}, model={chosen}",
              file=sys.stderr, flush=True)

        import httpx

        def _call():
            return client.models.generate_content(
                model=chosen,
                contents=[
                    types.Part.from_bytes(data=document.data, mime_type=document.mime_type),
                    _PROMPT.format(doc_type=doc_type, fields=", ".join(fields)),
                ],
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_Extraction,
                    temperature=temperature,
                ),
            )

        t = time.monotonic()
        for attempt in range(1, retries + 1):
            try:
                response = _call()
                break
            except httpx.TransportError as err:  # ConnectError / DNS / read errors
                if attempt == retries:
                    raise
                print(f"      · attempt {attempt}/{retries} failed ({type(err).__name__}: {err}); "
                      f"retrying in {backoff_s * attempt:.1f}s…", file=sys.stderr, flush=True)
                time.sleep(backoff_s * attempt)
        _t("generate_content (the API call)", t)

        t = time.monotonic()
        parsed = getattr(response, "parsed", None)
        if isinstance(parsed, _Extraction):
            items = [f.model_dump() for f in parsed.fields]
        else:
            items = json.loads(response.text).get("fields", [])
        _t("parse", t)
        return [{"field": i["field"], "value": i["value"],
                 "source_quote": i.get("source_quote", "")} for i in items]

    return vision_pass


def pdf_text(data: bytes) -> Optional[str]:
    """Extract a PDF's text layer (for provenance / digital-first). Returns None if
    PyMuPDF isn't installed or the bytes aren't a parseable PDF."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        return None
    try:
        with fitz.open(stream=data, filetype="pdf") as doc:
            return "\n".join(page.get_text() for page in doc)
    except Exception:
        return None


def downscale_image(data: bytes, mime_type: str, *, max_side: int = 1600, quality: int = 85):
    """Shrink an oversized image (longest side → max_side, re-encoded JPEG) before
    sending it to the model — big images dominate latency/cost. No-op for non-images
    or if Pillow is unavailable. Returns (data, mime_type)."""
    if not mime_type.startswith("image/"):
        return data, mime_type
    try:
        import io

        from PIL import Image
    except ImportError:
        return data, mime_type
    try:
        img = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = img.size
        scale = max_side / max(w, h)
        if scale < 1:
            img = img.resize((int(w * scale), int(h * scale)))
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality)
        return buf.getvalue(), "image/jpeg"
    except Exception:
        return data, mime_type


def load_file(path: str, *, max_side: int = 1600) -> Document:
    """Build a Document from a local file (smoke testing against real docs). Images
    are downscaled to `max_side` to speed up the vision call."""
    import mimetypes

    data = open(path, "rb").read()
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    text = pdf_text(data)
    if mime.startswith("image/"):
        data, mime = downscale_image(data, mime, max_side=max_side)
    return Document(data=data, mime_type=mime, text=text)


def _load_dotenv(path: str = ".env") -> None:
    """Load KEY=VALUE pairs from a local .env into the environment (smoke CLI only;
    compose already does this for the containers). Doesn't override existing vars."""
    if not os.path.exists(path):
        return
    for line in open(path):
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _main() -> None:  # python -m lending.adapters.llm_ocr <file> <doc_type> [samples]
    import sys

    _load_dotenv()  # so you don't have to `export` when running from the repo root

    if len(sys.argv) < 3:
        print("usage: python -m lending.adapters.llm_ocr <file> <doc_type> [samples]")
        raise SystemExit(2)
    from lending.agents.llm import model_lite

    path, doc_type = sys.argv[1], sys.argv[2]
    samples = int(sys.argv[3]) if len(sys.argv) > 3 else 3
    raw_kb = os.path.getsize(path) / 1024
    document = load_file(path)
    # progress to stderr so stdout stays clean JSON (pipe-friendly)
    log = lambda msg: print(msg, file=sys.stderr, flush=True)
    log(f"→ extracting {path} as '{doc_type}' with {samples} samples")
    log(f"  size: {raw_kb:.0f} KB → {len(document.data) / 1024:.0f} KB ({document.mime_type})")
    extractor = make_llm_extractor(
        lambda _a, _d: document,
        gemini_vision_pass(model=model_lite()),     # lite model for speed
        samples=samples, on_progress=log,
    )
    result = extractor("cli", doc_type)
    log("─" * 40)
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    _main()
