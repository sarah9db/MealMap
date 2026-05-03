from __future__ import annotations

from typing import Any


def extract_text_from_pdf(uploaded_file: Any) -> str:
    """
    Best-effort text extraction for text-based PDFs.
    Returns "" on failure.

    Note: scanned PDFs (images) will likely extract as empty text; users should upload images instead.
    """
    try:
        from pypdf import PdfReader

        uploaded_file.seek(0)
        reader = PdfReader(uploaded_file)
        parts: list[str] = []
        for page in reader.pages[:15]:
            txt = page.extract_text() or ""
            if txt.strip():
                parts.append(txt.strip())
        return "\n\n".join(parts).strip()
    except Exception:
        return ""

