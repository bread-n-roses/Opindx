"""Serve the repository locally for a preview, without browser caching.

Usage: python tools/serve_site.py [port]   (default 8000)
Then open http://localhost:8000/site/index.html. No caching means an edited file is always the one you see.
"""
import functools
import http.server
import sys
from pathlib import Path


class NoCache(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


port = int(sys.argv[1]) if len(sys.argv) > 1 else 8000
handler = functools.partial(NoCache, directory=str(Path(__file__).resolve().parent.parent))
print(f"Open http://localhost:{port}/site/index.html")
http.server.ThreadingHTTPServer(("", port), handler).serve_forever()
