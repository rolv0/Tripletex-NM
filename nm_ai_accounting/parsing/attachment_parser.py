from __future__ import annotations

import base64
import io
from dataclasses import dataclass

from models import TaskFile


@dataclass
class ParsedAttachment:
    filename: str
    mime_type: str
    size: int
    extracted_text: str


def _extract_text_from_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore

        reader = PdfReader(io.BytesIO(data))
        chunks: list[str] = []
        for page in reader.pages:
            chunks.append(page.extract_text() or "")
        return "\n".join(chunks).strip()
    except Exception:
        # Fallback: decode printable sections if parser is unavailable.
        return data.decode("latin-1", errors="ignore")[:4000]


def _extract_text_fallback(data: bytes) -> str:
    return data.decode("utf-8", errors="ignore")[:4000]


def parse_attachments(files: list[TaskFile]) -> list[ParsedAttachment]:
    parsed: list[ParsedAttachment] = []
    for f in files:
        raw = base64.b64decode(f.content_base64 or "")
        if f.mime_type == "application/pdf":
            extracted = _extract_text_from_pdf(raw)
        else:
            extracted = _extract_text_fallback(raw)
        parsed.append(
            ParsedAttachment(
                filename=f.filename,
                mime_type=f.mime_type,
                size=len(raw),
                extracted_text=extracted,
            )
        )
    return parsed

