"""Serve a directory over HTTP with permissive CORS and Range support (for icechunk-js / gridlook tests).

Usage: python serve_cors.py <directory> <port>
"""

import sys
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from functools import partial


class CORSRangeHandler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Expose-Headers", "Content-Length, Content-Range, Accept-Ranges, ETag")
        self.send_header("Accept-Ranges", "bytes")
        super().end_headers()

    def do_OPTIONS(self):
        self.send_response(204)
        self.end_headers()

    def send_head(self):
        # minimal Range support: bytes=a-b on a regular file
        rng = self.headers.get("Range")
        if not rng or not rng.startswith("bytes="):
            return super().send_head()
        import os

        path = self.translate_path(self.path)
        if not os.path.isfile(path):
            return super().send_head()
        size = os.path.getsize(path)
        a, _, b = rng[len("bytes=") :].partition("-")
        start = int(a) if a else max(0, size - int(b))
        end = int(b) if (b and a) else size - 1
        end = min(end, size - 1)
        f = open(path, "rb")
        f.seek(start)
        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        data = f.read(end - start + 1)
        f.close()
        import io

        return io.BytesIO(data)

    def log_message(self, fmt, *args):
        sys.stderr.write("%s %s\n" % (self.command, self.path))


if __name__ == "__main__":
    directory, port = sys.argv[1], int(sys.argv[2])
    ThreadingHTTPServer(("127.0.0.1", port), partial(CORSRangeHandler, directory=directory)).serve_forever()
