import json

import pytest

from conftest import edge, make_flow, node


@pytest.fixture(autouse=True)
def _clean(clean_state, tmp_path, monkeypatch):
    monkeypatch.setenv("FLOWPILOT_FILES_DIR", str(tmp_path))
    yield


def convert(client, operation, value):
    wid = make_flow(client, [node("t", "trigger"), node("c", "convert", operation=operation, value=value)], [edge("t", "c")])
    return client.post(f"/api/workflows/{wid}/run").json()


def make_pdf(pages):
    """A tiny valid PDF with one line of text per page."""
    objs = []
    n = len(pages)
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(n))
    objs.append("<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {n} >>")
    for i, text in enumerate(pages):
        page_no, content_no = 3 + i * 2, 4 + i * 2
        objs.append(f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents {content_no} 0 R /Resources << /Font << /F1 {3 + n * 2} 0 R >> >> >>")
        stream = f"BT /F1 18 Tf 20 100 Td ({text}) Tj ET"
        objs.append(f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream")
    objs.append("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out, offsets = b"%PDF-1.4\n", []
    for i, body in enumerate(objs, 1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{body}\nendobj\n".encode()
    start = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(f"{o:010d} 00000 n \n".encode() for o in offsets)
    out += f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode()
    return out


def test_yaml_to_json(client):
    res = convert(client, "yaml_to_json", "name: Ada\nage: 36\ntags:\n  - a\n  - b\nnested:\n  ok: true\n")
    assert res["status"] == "success" and res["output"] == {"name": "Ada", "age": 36, "tags": ["a", "b"], "nested": {"ok": True}}


def test_yaml_is_safe(client):
    for bad in ("a: &x [1]\nb: *x\n", "!!python/object/apply:os.system ['echo hi']", "a: [1, 2", "- 1\n---\n- 2\n", "d: 2024-01-02\n", "s: !!set {a, b}"):
        res = convert(client, "yaml_to_json", bad)
        assert res["status"] == "failed", bad
    assert convert(client, "yaml_to_json", "text: 'cost *5 and **bold**'")["output"] == {"text": "cost *5 and **bold**"}


def test_json_to_yaml_round_trip(client):
    res = convert(client, "json_to_yaml", '{"name": "Zoë", "list": [1, 2], "z": null}')
    assert res["status"] == "success" and "name: Zoë" in res["output"] and "list:\n- 1\n- 2" in res["output"]
    assert convert(client, "yaml_to_json", res["output"].replace("\n", "\n") if False else res["output"])["output"] == {"name": "Zoë", "list": [1, 2], "z": None}
    assert convert(client, "json_to_yaml", "not json")["status"] == "failed"


def test_pdf_text_reads_pages_from_the_files_folder(client, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "a.pdf").write_bytes(make_pdf(["Invoice 1042", "Total 99 EUR"]))
    res = convert(client, "pdf_text", "docs/a.pdf")
    assert res["status"] == "success"
    out = res["output"]
    assert out["pages"] == 2 and out["read_pages"] == 2 and out["truncated"] is False and "Invoice 1042" in out["text"] and "Total 99 EUR" in out["text"]


def test_pdf_limits_and_errors(client, tmp_path):
    (tmp_path / "big.pdf").write_bytes(make_pdf([f"Page {i}" for i in range(60)]))
    out = convert(client, "pdf_text", "big.pdf")["output"]
    assert out["pages"] == 60 and out["read_pages"] == 50 and out["truncated"] is True
    (tmp_path / "junk.pdf").write_bytes(b"this is not a pdf")
    assert "Could not read" in convert(client, "pdf_text", "junk.pdf")["error"]
    assert "does not exist" in convert(client, "pdf_text", "missing.pdf")["error"]
    for path in ("../secret.pdf", "/etc/passwd", "a/../../b.pdf", "C:/x.pdf"):
        assert convert(client, "pdf_text", path)["status"] == "failed", path


def test_pdf_size_limit(client, tmp_path, monkeypatch):
    from app import main
    (tmp_path / "a.pdf").write_bytes(make_pdf(["x"]))
    monkeypatch.setattr(main, "FILE_MAX", 100)
    assert "larger than 5 MB" in convert(client, "pdf_text", "a.pdf")["error"]


def test_the_old_operations_still_work(client):
    assert convert(client, "json_parse", '{"a": 1}')["output"] == {"a": 1}
    assert convert(client, "url_encode", "a b&c")["output"] == "a%20b%26c"
