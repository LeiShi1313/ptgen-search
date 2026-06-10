from __future__ import annotations


def normalize_imdb_id(value: str) -> str:
    text = str(value).strip()
    if text.lower().startswith("tt"):
        digits = text[2:]
    else:
        digits = text
    if digits.isdigit():
        return f"tt{digits.zfill(7)}"
    return text if text.startswith("tt") else f"tt{text}"


def source_document_id(source: str, source_id: str) -> str:
    clean_id = str(source_id).strip()
    if source == "imdb":
        clean_id = normalize_imdb_id(clean_id)
    return f"{source}-{clean_id}"
