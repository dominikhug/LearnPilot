import pytest
from pdfs import make_pdf

from learnpilot.ingestion import TARGET_CHUNK_CHARS, IngestionError, extract_chunks

LONG = "A sentence about recursion that is long enough to count as real text. " * 2


def test_pdf_chunks_carry_page_numbers():
    chunks = extract_chunks("book.pdf", make_pdf([f"Page one. {LONG}", "", f"Page three. {LONG}"]))
    assert [c.page for c in chunks] == [1, 3]
    assert chunks[0].text.startswith("Page one.")
    assert chunks[1].text.startswith("Page three.")


def test_pdf_without_text_is_rejected_as_scanned():
    with pytest.raises(IngestionError, match="scanned"):
        extract_chunks("scan.pdf", make_pdf(["", ""]))


def test_broken_pdf_is_rejected():
    with pytest.raises(IngestionError, match="not a readable PDF"):
        extract_chunks("broken.pdf", b"%PDF-1.7 this is not really a pdf")


def test_unsupported_file_type_is_rejected():
    with pytest.raises(IngestionError, match="Unsupported"):
        extract_chunks("notes.docx", b"whatever")


def test_empty_text_file_is_rejected():
    with pytest.raises(IngestionError, match="no text"):
        extract_chunks("empty.txt", b"  \n\n  ")


def test_text_paragraphs_are_grouped_up_to_target_size():
    paragraph = "x" * 600
    chunks = extract_chunks("notes.txt", "\n\n".join([paragraph] * 5).encode())
    assert [len(c.text) for c in chunks] == [1202, 1202, 600]
    assert all(len(c.text) <= TARGET_CHUNK_CHARS for c in chunks)
    assert all(c.page is None and c.section is None for c in chunks)


def test_long_paragraph_stays_whole():
    chunks = extract_chunks("notes.txt", ("y" * 4000).encode())
    assert [len(c.text) for c in chunks] == [4000]


def test_markdown_sections_are_heading_paths():
    text = f"""Intro {LONG}

# Basics

{LONG}

## Recursion

{LONG}

```python
# not a heading
```

# Advanced

{LONG}
"""
    chunks = extract_chunks("notes.md", text.encode())
    assert [c.section for c in chunks] == [None, "Basics", "Basics › Recursion", "Advanced"]
    assert "# not a heading" in chunks[2].text


def test_text_encoding_and_nul_bytes():
    windows = f"Größe und Maß. {LONG}".encode("cp1252")
    assert extract_chunks("de.txt", windows)[0].text.startswith("Größe und Maß.")

    with_bom_and_nul = b"\xef\xbb\xbfHello\x00 world. " + LONG.encode()
    assert extract_chunks("en.txt", with_bom_and_nul)[0].text.startswith("Hello world.")
