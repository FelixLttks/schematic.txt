"""Live editor backend: documents of the edited file and the HTTP API."""

import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from schematic_txt.server import Handler, Workspace

MD = "# Doc\n\n```schematic\nA.1 -> B.1\n```\n\ntext\n\n```schematic\n```\n"


def test_markdown_blocks_are_documents(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text(MD)
    docs = Workspace(path).documents()
    assert [d["code"] for d in docs] == ["A.1 -> B.1", ""]
    assert docs[0]["title"] == "Block 1 (line 3)"


def test_saving_a_block_keeps_the_rest_of_the_file(tmp_path):
    path = tmp_path / "doc.md"
    path.write_text(MD)
    ws = Workspace(path)
    ws.save(0, "A.1 -> B.1\nA.2 -> B.2\n")
    ws.save(1, "C.1 -> D.1")
    assert path.read_text() == (
        "# Doc\n\n```schematic\nA.1 -> B.1\nA.2 -> B.2\n```\n\ntext\n\n```schematic\nC.1 -> D.1\n```\n"
    )
    with pytest.raises(LookupError):
        ws.save(2, "")


def test_text_file_is_one_document(tmp_path):
    path = tmp_path / "board.txt"
    path.write_text("A.1 -> B.1\n")
    ws = Workspace(path)
    assert ws.documents() == [{"id": 0, "title": "board.txt", "code": "A.1 -> B.1\n"}]
    ws.save(0, "C.1 -> D.1\n")
    assert path.read_text() == "C.1 -> D.1\n"


def test_scratch_mode_has_no_documents(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ws = Workspace(None)
    assert ws.documents() == []
    assert ws.resolve("../outside.png") is None


@pytest.fixture
def server(tmp_path):
    path = tmp_path / "board.txt"
    path.write_text("A.1 -> B.1\n")
    handler = type("TestHandler", (Handler,), {"workspace": Workspace(path)})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def _request(url, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())


def test_api_roundtrip(server):
    assert _request(server + "/api/documents")["documents"][0]["code"] == "A.1 -> B.1\n"
    _request(server + "/api/documents/0", "PUT", {"code": "C.1 -> D.1\n"})
    assert _request(server + "/api/documents")["documents"][0]["code"] == "C.1 -> D.1\n"


def test_api_render(server):
    ok = _request(server + "/api/render", "POST", {"code": "A.1 -> B.1", "effort": "fast"})
    assert ok["svg"].startswith("<svg")
    bad = _request(server + "/api/render", "POST", {"code": "A.1 ->", "effort": "fast"})
    assert bad["error"]["line"] == 1


def test_api_part_upload_and_file_access(server):
    png = "data:image/png;base64,iVBORw0KGgo="
    assert _request(server + "/api/parts", "POST", {"filename": "my board.png", "data": png}) == {
        "path": "parts/my_board.png"
    }
    with urllib.request.urlopen(server + "/files/parts/my_board.png") as resp:
        assert resp.headers["Content-Type"] == "image/png"
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(server + "/files/board.txt")
    assert exc.value.code == 404
