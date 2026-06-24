"""
Document storage (#9, Phase A) — persist uploaded file bytes so the OCR/LLM
extractor (#9 Phase B) can read them later, in a different process (the worker).

Today "upload" only recorded a reference string; real extraction needs the actual
bytes. `LocalDocumentStore` writes them to a directory (a shared volume in the
demo compose, so the API can write and the worker can read). The `DocumentStore`
protocol lets an object store (S3/GCS) drop in for pilot without touching callers.

Files are PII (Aadhaar/PAN/payslip) — in production this directory must be
encrypted-at-rest and access-controlled (DPDP). The demo store is plaintext local.
"""
from __future__ import annotations

import mimetypes
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol


@dataclass(frozen=True)
class StoredDocument:
    data: bytes
    content_type: str


class DocumentStore(Protocol):
    def put(self, application_id: str, doc_type: str, data: bytes, content_type: str) -> str: ...
    def get(self, application_id: str, doc_type: str) -> Optional[StoredDocument]: ...
    def delete(self, application_id: str, doc_type: str) -> bool: ...


_SAFE = re.compile(r"[^A-Za-z0-9_-]")


def _safe(part: str) -> str:
    """Defang a path segment so application_id / doc_type can't escape the root."""
    return _SAFE.sub("", part) or "x"


class LocalDocumentStore:
    """Stores one file per (application_id, doc_type) under a root directory, with a
    sidecar `.type` recording the content type. Latest write wins."""

    def __init__(self, root: str) -> None:
        self._root = Path(root)

    def _dir(self, application_id: str) -> Path:
        return self._root / _safe(application_id)

    def put(self, application_id: str, doc_type: str, data: bytes, content_type: str) -> str:
        directory = self._dir(application_id)
        directory.mkdir(parents=True, exist_ok=True)
        ext = mimetypes.guess_extension(content_type or "") or ".bin"
        path = directory / f"{_safe(doc_type)}{ext}"
        path.write_bytes(data)
        (directory / f"{_safe(doc_type)}.type").write_text(content_type or "application/octet-stream")
        return f"file://{path}"

    def get(self, application_id: str, doc_type: str) -> Optional[StoredDocument]:
        directory = self._dir(application_id)
        if not directory.exists():
            return None
        safe_doc = _safe(doc_type)
        files = [p for p in directory.glob(f"{safe_doc}.*") if p.suffix != ".type"]
        if not files:
            return None
        path = files[0]
        type_file = directory / f"{safe_doc}.type"
        content_type = type_file.read_text().strip() if type_file.exists() else "application/octet-stream"
        return StoredDocument(data=path.read_bytes(), content_type=content_type)

    def delete(self, application_id: str, doc_type: str) -> bool:
        """Remove the stored file (+ its sidecar) so the slot can be re-attached.
        Returns True if anything was deleted. Idempotent."""
        directory = self._dir(application_id)
        if not directory.exists():
            return False
        removed = False
        for p in directory.glob(f"{_safe(doc_type)}.*"):
            p.unlink(missing_ok=True)
            removed = True
        return removed


def make_document_store() -> DocumentStore:
    """The configured store. `DOC_STORE_DIR` points at the shared volume in compose;
    defaults to a local temp dir for dev/tests."""
    return LocalDocumentStore(os.environ.get("DOC_STORE_DIR", "/tmp/lending-docstore"))
