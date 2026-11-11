from pathlib import Path
import base64
import io
import zipfile

import pytest
from PIL import Image

from codex_sessions.artifacts import Artifacts


def put(store, name, data, **extra):
    return store.put({"name": name, "data": base64.b64encode(data).decode(), **extra})


def test_documents_and_transcript_summary_are_supplied_in_full(tmp_path):
    store = Artifacts(tmp_path)
    document = put(store, "requirements.md", b"first requirement\nlast requirement")
    transcript = put(store, "meeting.vtt", b"WEBVTT\n00:00:00.000 --> 00:00:01.000\nFull transcript", kind="transcript", summary="Decision: use the local gateway")
    inputs = store.inputs([document["id"], transcript["id"]])
    text = "\n".join(i.get("text", "") for i in inputs)
    assert "first requirement\nlast requirement" in text
    assert "Full transcript" in text
    assert "Decision: use the local gateway" in text
    assert "not instructions" in text


def test_image_becomes_native_local_image_input(tmp_path):
    store = Artifacts(tmp_path)
    data = io.BytesIO(); Image.new("RGB", (8, 8), "red").save(data, format="PNG")
    artifact = put(store, "screenshot.png", data.getvalue())
    inputs = store.inputs([artifact["id"]])
    assert any(i["type"] == "localImage" for i in inputs)
    assert artifact["sha256"]


def test_docx_reads_paragraphs_and_headers(tmp_path):
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="urn:test"><w:p><w:r><w:t>Document body</w:t></w:r></w:p></w:document>')
        z.writestr("word/header1.xml", '<w:hdr xmlns:w="urn:test"><w:p><w:r><w:t>Important header</w:t></w:r></w:p></w:hdr>')
    store = Artifacts(tmp_path)
    a = put(store, "design.docx", data.getvalue())
    text = str(store.inputs([a["id"]]))
    assert "Document body" in text and "Important header" in text


def test_unreadable_or_unknown_files_do_not_silently_launch(tmp_path):
    store = Artifacts(tmp_path)
    with pytest.raises(ValueError):
        put(store, "broken.pdf", b"not a pdf")
    with pytest.raises(ValueError):
        put(store, "opaque.bin", b"\x00\xff\x81")
    with pytest.raises(ValueError):
        store.inputs(["unknown-id"])


def pdf_bytes(contents, unicode_map=None):
    count = len(contents)
    font = count + 3
    children = " ".join(f"{index + 3} 0 R" for index in range(count))
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", f"<< /Type /Pages /Kids [{children}] /Count {count} >>".encode()]
    objects.extend(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 200 200] /Resources << /Font << /F1 {font} 0 R >> >> /Contents {font + index + 1} 0 R >>".encode() for index in range(count))
    mapping = f" /ToUnicode {2 * count + 4} 0 R" if unicode_map else ""
    objects.append(f"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica{mapping} >>".encode())
    for content in contents:
        objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"\nendstream")
    if unicode_map:
        objects.append(b"<< /Length " + str(len(unicode_map)).encode() + b" >>\nstream\n" + unicode_map + b"\nendstream")
    pdf = b"%PDF-1.4\n"; offsets = []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(pdf)); pdf += f"{i} 0 obj\n".encode() + obj + b"\nendobj\n"
    xref = len(pdf)
    pdf += f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode()
    pdf += b"".join(f"{offset:010d} 00000 n \n".encode() for offset in offsets)
    return pdf + f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()


def test_mixed_pdf_supplies_text_and_every_visual_page(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", "")
    pdf = pdf_bytes([b"BT /F1 12 Tf 10 180 Td (Cover requirement) Tj ET", b"0 0 1 rg 20 20 100 100 re f"])
    store = Artifacts(tmp_path)
    artifact = put(store, "mixed.pdf", pdf)
    inputs = store.inputs([artifact["id"]])
    assert "Cover requirement" in str(inputs)
    images = [i["path"] for i in inputs if i["type"] == "localImage"]
    assert len(images) == artifact["pages"] == 2
    with Image.open(images[1]) as image:
        assert image.size == (400, 400)
        assert image.getpixel((100, 250))[:3] == (0, 0, 255)


def test_office_embedded_image_is_not_discarded(tmp_path):
    image = io.BytesIO(); Image.new("RGB", (8, 8), "blue").save(image, format="PNG")
    document = io.BytesIO()
    with zipfile.ZipFile(document, "w") as z:
        z.writestr("word/document.xml", '<w:document xmlns:w="urn:test"><w:p><w:r><w:t>Read the figure</w:t></w:r></w:p></w:document>')
        z.writestr("word/media/figure.png", image.getvalue())
    store = Artifacts(tmp_path)
    artifact = put(store, "figure.docx", document.getvalue())
    assert any(i["type"] == "localImage" for i in store.inputs([artifact["id"]]))



def test_pdf_preserves_first_and_last_page_text_in_order(tmp_path):
    store = Artifacts(tmp_path)
    pdf = pdf_bytes([b"BT /F1 12 Tf 10 180 Td (First requirement) Tj ET", b"BT /F1 12 Tf 10 180 Td (Final requirement) Tj ET"])
    artifact = put(store, "complete.pdf", pdf)
    inputs = store.inputs([artifact["id"]])
    text = "\n".join(item.get("text", "") for item in inputs)
    assert text.index("First requirement") < text.index("Final requirement")
    assert sum(item["type"] == "localImage" for item in inputs) == 2


def test_pdf_page_limit_rejects_the_complete_attachment(tmp_path):
    store = Artifacts(tmp_path)
    with pytest.raises(ValueError, match="20 pages.*no pages were omitted"):
        put(store, "too-many.pdf", pdf_bytes([b""] * 21))
    assert not list(store.root.glob("*/metadata.json"))


def test_pdf_text_limit_rejects_instead_of_truncating(tmp_path, monkeypatch):
    monkeypatch.setattr("codex_sessions.artifacts.MAX_CONTEXT", 10)
    store = Artifacts(tmp_path)
    pdf = pdf_bytes([b"BT /F1 12 Tf 10 180 Td (Complete document requirement) Tj ET"])
    with pytest.raises(ValueError, match="no text was truncated"):
        put(store, "complete.pdf", pdf)
    assert not list(store.root.glob("*/metadata.json"))



def test_pdf_preserves_unicode_outside_the_basic_multilingual_plane(tmp_path):
    mapping = b"/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CIDSystemInfo << /Registry (Adobe) /Ordering (UCS) /Supplement 0 >> def /CMapName /TestUnicode def /CMapType 2 def 1 begincodespacerange <00> <FF> endcodespacerange 1 beginbfchar <41> <D83DDE80> endbfchar endcmap CMapName currentdict /CMap defineresource pop end end"
    pdf = pdf_bytes([b"BT /F1 12 Tf 10 180 Td (A) Tj ET"], unicode_map=mapping)
    store = Artifacts(tmp_path)
    artifact = put(store, "unicode.pdf", pdf)
    assert "\U0001f680" in store.get(artifact["id"], internal=True)["text"]


def test_invalid_pdf_explains_damage_or_password_protection(tmp_path):
    store = Artifacts(tmp_path)
    with pytest.raises(ValueError, match="damaged or password protected"):
        put(store, "broken.pdf", b"not a pdf")
    assert not list(store.root.glob("*/metadata.json"))


def test_text_preparation_normalizes_line_endings_and_preserves_original_bytes(tmp_path):
    store = Artifacts(tmp_path)
    original = b'first\r\nsecond\rthird\n'
    artifact = put(store, 'notes.md', original)
    assert artifact['characters'] == len('first\nsecond\nthird\n')
    prepared = store.get(artifact['id'], internal=True)
    assert prepared['text'] == 'first\nsecond\nthird\n'
    assert Path(prepared['path']).read_bytes() == original
