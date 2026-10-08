"""Static server for the roomlab viewer that never lets the browser cache anything.

`python -m http.server` sends Last-Modified but no Cache-Control, so Chrome applies heuristic
caching and will happily reuse a stale room JSON or atlas across a normal reload -- which looks
exactly like "my rebuild did nothing". This sends no-store on everything instead.

    python serve.py [port]
"""
import sys, os
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

WEB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")


class NoCacheHandler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=WEB, **kw)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, fmt, *args):
        if "304" not in (args[1] if len(args) > 1 else ""):
            super().log_message(fmt, *args)


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8430
    print(f"roomlab serving {WEB} on http://localhost:{port}  (no-store, always fresh)")
    ThreadingHTTPServer(("127.0.0.1", port), NoCacheHandler).serve_forever()
