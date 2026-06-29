"""Range-capable static server for local preview.

`python -m http.server` does NOT honour HTTP Range requests, but PMTiles
(used by the national map) fetches tiles via Range — so the plain server makes
the national page fail locally. This tiny server adds 206/Range support.
GitHub Pages already supports Range, so this is only needed for local preview.

    python serve.py [port]        # serves ./web/ , default port 8000
"""

from __future__ import annotations

import http.server
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent / "web"


class RangeHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **k):
        super().__init__(*a, directory=str(ROOT), **k)

    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def do_GET(self):  # noqa: N802
        rng = self.headers.get("Range")
        path = self.translate_path(self.path)
        m = re.match(r"bytes=(\d+)-(\d*)", rng or "")
        if not rng or not m or not os.path.isfile(path):
            return super().do_GET()
        size = os.path.getsize(path)
        start = int(m.group(1))
        end = int(m.group(2)) if m.group(2) else size - 1
        end = min(end, size - 1)
        if start > end:
            self.send_error(416, "Requested Range Not Satisfiable")
            return
        length = end - start + 1
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(length))
        self.end_headers()
        with open(path, "rb") as f:
            f.seek(start)
            self.wfile.write(f.read(length))


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
    print(f"Serving {ROOT} on http://127.0.0.1:{port}  (Range-capable)", flush=True)
    http.server.HTTPServer(("127.0.0.1", port), RangeHandler).serve_forever()
