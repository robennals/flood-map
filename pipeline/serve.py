"""Serve the website locally, with the byte-range support the map files need.

Usage: python -m pipeline.serve [port]     then open http://localhost:<port>/?data=local
(Python's built-in http.server ignores Range requests, which PMTiles map files rely on.)
"""
import os
import re
import sys
from functools import partial
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

from . import config

RANGE = re.compile(r"bytes=(\d*)-(\d*)$")


class RangeRequestHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def send_head(self):
        match = RANGE.match(self.headers.get("Range", ""))
        path = self.translate_path(self.path)
        if not match or not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        first, last = match.groups()
        if first:
            start, end = int(first), min(int(last), size - 1) if last else size - 1
        else:  # "bytes=-N": the last N bytes
            start, end = max(size - int(last), 0), size - 1
        if start >= size or start > end:
            self.send_error(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            return None
        f = open(path, "rb")
        f.seek(start)
        self.send_response(HTTPStatus.PARTIAL_CONTENT)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        self.range_remaining = end - start + 1
        return f

    def copyfile(self, source, outputfile):
        remaining = getattr(self, "range_remaining", None)
        if remaining is None:
            return super().copyfile(source, outputfile)
        while remaining > 0:
            chunk = source.read(min(64 * 1024, remaining))
            if not chunk:
                break
            outputfile.write(chunk)
            remaining -= len(chunk)

    def log_message(self, format, *args):
        pass  # keep the terminal quiet


def main(port=8765):
    handler = partial(RangeRequestHandler, directory=str(config.REPO / "web"))
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    print(f"Serving the website at http://localhost:{port}/?data=local  (Ctrl-C to stop)", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 8765)
