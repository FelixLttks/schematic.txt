"""Local live editor: ``schematic serve [FILE]``.

A small HTTP server (standard library only) behind a single-page editor
with a live preview.  Bound to a file, it reads and writes that file:

* ``.txt``: the whole file is one schematic;
* ``.md``: every ```` ```schematic ```` block is a schematic of its own.

Edits in the browser are saved to disk; edits on disk (in any editor) show
up in the browser, which polls the document version.

API (JSON):

* ``GET  /api/documents``       -> ``{file, version, documents: [{id, title, code}]}``
* ``PUT  /api/documents/<id>``  ``{code}`` -> ``{version}``
* ``POST /api/render``          ``{code, theme, effort}`` -> ``{svg}`` or ``{error}``
* ``POST /api/parts``           ``{filename, data}`` -> ``{path}`` (image saved to ``parts/``)
* ``GET  /files/<path>``        an image below the working directory
"""

from __future__ import annotations

import base64
import binascii
import json
import mimetypes
import re
import threading
import traceback
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from schematic_txt.errors import ParseError, SchematicError
from schematic_txt.layout import EFFORT_LEVELS, config_for_effort, layout_schematic
from schematic_txt.markdown import find_blocks
from schematic_txt.parser import parse_schematic
from schematic_txt.svg import THEMES, render_svg

STATIC_DIR = Path(__file__).resolve().parent / "static"
MAX_BODY = 32 * 1024 * 1024


class Workspace:
    """The file being edited (if any) and the directory part images are
    resolved against."""

    def __init__(self, file: Path | None):
        self.file = file.resolve() if file is not None else None
        self.root = self.file.parent if self.file is not None else Path.cwd().resolve()
        self.lock = threading.Lock()  # one render / file write at a time

    @property
    def is_markdown(self) -> bool:
        return self.file is not None and self.file.suffix.lower() == ".md"

    def version(self) -> int:
        try:
            return self.file.stat().st_mtime_ns if self.file is not None else 0
        except OSError:
            return 0

    def documents(self) -> list[dict[str, Any]]:
        if self.file is None:
            return []
        text = self._read()
        if not self.is_markdown:
            return [{"id": 0, "title": self.file.name, "code": text}]
        return [
            {"id": i, "title": f"Block {i + 1} (line {b.fence_line + 1})", "code": b.source}
            for i, b in enumerate(find_blocks(text))
        ]

    def save(self, doc_id: int, code: str) -> None:
        if self.file is None:
            raise LookupError("no file is open")
        if not self.is_markdown:
            if doc_id != 0:
                raise LookupError(f"no document {doc_id}")
            self.file.write_text(code, encoding="utf-8")
            return
        text = self._read()
        blocks = find_blocks(text)
        if not 0 <= doc_id < len(blocks):
            raise LookupError(f"no schematic block {doc_id + 1} in {self.file.name}")
        block = blocks[doc_id]
        lines = text.split("\n")
        lines[block.fence_line + 1 : block.close_line] = code.rstrip("\n").split("\n") if code.strip() else []
        self.file.write_text("\n".join(lines), encoding="utf-8")

    def resolve(self, rel: str) -> Path | None:
        """``rel`` below the root, or None if it points elsewhere."""
        path = (self.root / rel).resolve()
        return path if path.is_relative_to(self.root) else None

    def _read(self) -> str:
        assert self.file is not None
        return self.file.read_text(encoding="utf-8")


class Handler(BaseHTTPRequestHandler):
    workspace: Workspace  # set on the subclass created by ``serve``

    def log_message(self, format: str, *args: Any) -> None:
        pass  # the editor polls; per-request logs would drown the terminal

    # ---------------------------------------------------------------- routes
    def do_GET(self) -> None:
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html"):
            self._send_file(STATIC_DIR / "index.html")
        elif path.startswith("/static/"):
            self._send_file(STATIC_DIR / path[len("/static/") :], within=STATIC_DIR)
        elif path.startswith("/files/"):
            target = self.workspace.resolve(path[len("/files/") :])
            mime = mimetypes.guess_type(target.name)[0] if target else None
            if target is None or not (mime or "").startswith("image/"):
                self._send_error(HTTPStatus.NOT_FOUND, "not found")
            else:
                self._send_file(target)
        elif path == "/api/documents":
            ws = self.workspace
            self._send_json(
                {
                    "file": ws.file.name if ws.file else None,
                    "version": ws.version(),
                    "documents": ws.documents(),
                }
            )
        else:
            self._send_error(HTTPStatus.NOT_FOUND, "not found")

    def do_PUT(self) -> None:
        m = re.fullmatch(r"/api/documents/(\d+)", self.path)
        if not m:
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        body = self._read_json()
        if body is None:
            return
        try:
            with self.workspace.lock:
                self.workspace.save(int(m.group(1)), str(body.get("code", "")))
        except LookupError as exc:
            self._send_error(HTTPStatus.CONFLICT, str(exc))
            return
        self._send_json({"version": self.workspace.version()})

    def do_POST(self) -> None:
        if self.path == "/api/render":
            self._render()
        elif self.path == "/api/parts":
            self._upload_part()
        else:
            self._send_error(HTTPStatus.NOT_FOUND, "not found")

    # ---------------------------------------------------------------- actions
    def _render(self) -> None:
        body = self._read_json()
        if body is None:
            return
        theme = body.get("theme") if body.get("theme") in THEMES else "dark"
        effort = body.get("effort") if body.get("effort") in EFFORT_LEVELS else "fast"
        try:
            with self.workspace.lock:
                schematic = parse_schematic(str(body.get("code", "")))
                layout_schematic(schematic, config_for_effort(effort))
                svg = render_svg(schematic, theme=theme, base_dir=self.workspace.root)
        except ParseError as exc:
            self._send_json({"error": {"message": exc.message, "line": exc.line_num}})
            return
        except SchematicError as exc:
            self._send_json({"error": {"message": str(exc), "line": None}})
            return
        except Exception as exc:  # a bug: keep serving, show it in the editor
            traceback.print_exc()
            self._send_json({"error": {"message": f"internal error: {exc}", "line": None}})
            return
        self._send_json({"svg": svg})

    def _upload_part(self) -> None:
        body = self._read_json()
        if body is None:
            return
        name = re.sub(r"[^A-Za-z0-9_.-]", "_", Path(str(body.get("filename", ""))).name)
        if not (mimetypes.guess_type(name)[0] or "").startswith("image/"):
            self._send_error(HTTPStatus.BAD_REQUEST, "expected an image file (SVG, PNG, JPEG, WebP)")
            return
        data = str(body.get("data", ""))
        try:
            content = base64.b64decode(data.split(",", 1)[-1], validate=True)
        except (binascii.Error, ValueError):
            self._send_error(HTTPStatus.BAD_REQUEST, "image data is not valid base64")
            return
        parts = self.workspace.root / "parts"
        parts.mkdir(exist_ok=True)
        (parts / name).write_bytes(content)
        self._send_json({"path": f"parts/{name}"})

    # ---------------------------------------------------------------- plumbing
    def _read_json(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            self._send_error(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "request too large")
            return None
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            body = None
        if not isinstance(body, dict):
            self._send_error(HTTPStatus.BAD_REQUEST, "expected a JSON object")
            return None
        return body

    def _send_file(self, path: Path, within: Path | None = None) -> None:
        path = path.resolve()
        if (within is not None and not path.is_relative_to(within)) or not path.is_file():
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self._send(HTTPStatus.OK, mime, path.read_bytes())

    def _send_json(self, data: dict[str, Any]) -> None:
        self._send(HTTPStatus.OK, "application/json", json.dumps(data).encode("utf-8"))

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        self._send(status, "application/json", json.dumps({"error": {"message": message}}).encode("utf-8"))

    def _send(self, status: HTTPStatus, content_type: str, data: bytes) -> None:
        try:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True  # the browser gave up (common while typing)


def serve(file: Path | None, host: str = "127.0.0.1", port: int = 8000, open_browser: bool = True) -> int:
    """Runs the editor until interrupted; tries the next ports if ``port`` is taken."""
    if file is not None and not file.is_file():
        file.parent.mkdir(parents=True, exist_ok=True)
        file.touch()
    handler = type("BoundHandler", (Handler,), {"workspace": Workspace(file)})
    for candidate in range(port, port + 20):
        try:
            server = ThreadingHTTPServer((host, candidate), handler)
            break
        except OSError:
            continue
    else:
        raise OSError(f"no free port in {port}..{port + 19}")

    url = f"http://{host}:{server.server_address[1]}/"
    target = f"editing {file}" if file is not None else "scratch mode (nothing is saved)"
    print(f"schematic.txt editor on {url} ({target}), Ctrl+C to stop", flush=True)
    if open_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0
