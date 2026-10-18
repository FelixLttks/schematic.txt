"""Minimalist HTTP server for the Schematic.txt web application."""

from __future__ import annotations
import json
import mimetypes
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import threading
import traceback
from typing import Optional

from schematic import ParseError

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
PROJECT_DIR = BASE_DIR.parent
EXAMPLES_DIR = PROJECT_DIR / "examples"

_render_lock = threading.Lock()


class SchematicHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Clean terminal logging
        print(f"[{self.log_date_time_string()}] {args[0]} {args[1]}")

    def do_GET(self):
        url_path = self.path.split("?")[0]

        if url_path in ("/", "/index.html"):
            self._serve_file(STATIC_DIR / "index.html", "text/html")
        elif url_path.startswith("/static/"):
            rel_path = url_path[len("/static/"):]
            file_path = (STATIC_DIR / rel_path).resolve()
            if not str(file_path).startswith(str(STATIC_DIR)) or not file_path.is_file():
                self._send_error(HTTPStatus.NOT_FOUND, "File not found")
                return
            mime_type, _ = mimetypes.guess_type(str(file_path))
            self._serve_file(file_path, mime_type or "application/octet-stream")
        elif url_path.startswith("/parts/"):
            rel_path = url_path[len("/parts/"):]
            parts_dir = PROJECT_DIR / "parts"
            file_path = (parts_dir / rel_path).resolve()
            if not str(file_path).startswith(str(parts_dir)) or not file_path.is_file():
                self._send_error(HTTPStatus.NOT_FOUND, "Part image not found")
                return
            mime_type, _ = mimetypes.guess_type(str(file_path))
            self._serve_file(file_path, mime_type or "application/octet-stream")
        elif url_path == "/api/examples":
            self._handle_get_examples()
        else:
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self):
        url_path = self.path.split("?")[0]

        if url_path == "/api/render":
            self._handle_render()
        elif url_path == "/api/upload_part":
            self._handle_upload_part()
        else:
            self._send_error(HTTPStatus.NOT_FOUND, "Not found")

    def _serve_file(self, file_path: Path, content_type: str):
        try:
            with open(file_path, "rb") as fp:
                data = fp.read()
            self.send_response(HTTPStatus.OK)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)
        except OSError:
            self._send_error(HTTPStatus.NOT_FOUND, "File read error")

    def _handle_get_examples(self):
        examples = {}
        if EXAMPLES_DIR.is_dir():
            for f in sorted(EXAMPLES_DIR.glob("*.txt")):
                try:
                    examples[f.name] = f.read_text(encoding="utf-8")
                except OSError:
                    pass
        self._send_json({"examples": examples})

    def _handle_render(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            req = json.loads(body)
            code = req.get("code", "")
            dark_theme = req.get("dark_theme", True)

            with _render_lock:
                # Hot-reload the whole schematic package safely
                import importlib
                import sys

                for name in list(sys.modules.keys()):
                    if name == "schematic" or name.startswith("schematic."):
                        sys.modules.pop(name, None)

                schematic = importlib.import_module("schematic")
                config = importlib.import_module("schematic.layout.config")

                effort = req.get("effort", "balanced")
                if effort not in config.EFFORT_LEVELS:
                    effort = "balanced"
                svg = schematic.generate_schematic_svg(
                    code, dark_theme=dark_theme, config=config.config_for_effort(effort)
                )

            self._send_json({"status": "ok", "svg": svg})
        except Exception as exc:
            traceback.print_exc()
            # Matched by name: after a hot reload the parser raises a freshly
            # created ParseError class, not the one imported at start-up.
            if isinstance(exc, ParseError) or type(exc).__name__ == "ParseError":
                self._send_json(
                    {
                        "status": "error",
                        "error": {
                            "type": "parse_error",
                            "message": exc.message,
                            "line": exc.line_num,
                            "line_content": exc.line_content,
                        },
                    }
                )
                return

            self._send_json(
                {
                    "status": "error",
                    "error": {
                        "type": "internal_error",
                        "message": str(exc),
                    },
                }
            )

    def _handle_upload_part(self):
        try:
            content_length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(content_length).decode("utf-8")
            req = json.loads(body)
            filename = req.get("filename", "custom_part.svg")
            import re
            filename = re.sub(r"[^A-Za-z0-9_\-\.]", "_", Path(filename).name)
            if not filename:
                filename = "custom_part.png"

            data_b64 = req.get("data", "")
            if "," in data_b64:
                data_b64 = data_b64.split(",", 1)[1]

            import base64, io, time
            img_bytes = base64.b64decode(data_b64)

            # Auto-trim transparent borders for PNG/WebP so the board bounds align cleanly
            img_w, img_h = None, None
            try:
                from PIL import Image
                im = Image.open(io.BytesIO(img_bytes))
                if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
                    bbox = im.getbbox()
                    if bbox and bbox != (0, 0, im.width, im.height):
                        im = im.crop(bbox)
                        buf = io.BytesIO()
                        im.save(buf, format="PNG")
                        img_bytes = buf.getvalue()
                img_w, img_h = im.width, im.height
            except Exception:
                pass

            parts_dir = PROJECT_DIR / "parts"
            parts_dir.mkdir(exist_ok=True, parents=True)
            target_path = parts_dir / filename
            target_path.write_bytes(img_bytes)

            cache_buster = int(time.time() * 1000)
            self._send_json({
                "status": "ok",
                "filename": filename,
                "path": f"parts/{filename}",
                "url": f"/parts/{filename}?v={cache_buster}",
                "width": img_w,
                "height": img_h,
            })
        except Exception as exc:
            self._send_json(
                {"status": "error", "error": {"message": str(exc)}},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def _send_json(self, data: dict, status: int = HTTPStatus.OK):
        body = json.dumps(data).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(message.encode("utf-8"))


def run_server(host: str = "127.0.0.1", port: int = 5000) -> None:
    # Auto-try adjacent ports if chosen port is in use
    server = None
    for p in range(port, port + 20):
        try:
            server = ThreadingHTTPServer((host, p), SchematicHandler)
            port = p
            break
        except OSError:
            continue

    if server is None:
        raise RuntimeError(f"Could not bind to any port near {port}")

    url = f"http://{host}:{port}"
    print("=" * 60)
    print("  Schematic.txt - Text-to-Schematic 2D Engine")
    print(f"  Web application running at: {url}")
    print("  Press Ctrl+C to stop.")
    print("=" * 60)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
    finally:
        server.server_close()
