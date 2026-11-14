"""One-time YouTube sign-in for the sylusnix channel.

  python auth.py          # prints the consent link, waits on 127.0.0.1:8765 for Google's redirect, saves the token
Open the link in a browser signed in to sylusnix@gmail.com and approve. Re-run when the token stops working."""
import sys
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer

from clipper import upload


class Handler(BaseHTTPRequestHandler):
    result = "waiting"

    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        try:
            if "code" not in q:
                raise RuntimeError(q.get("error", ["no code in redirect"])[0])
            upload.exchange(q["code"][0])
            Handler.result = "ok: refresh token saved, channel = " + str(upload.whoami())
        except Exception as exc:  # shown in the browser tab and the log, never the token itself
            Handler.result = f"failed: {exc}"
        body = Handler.result.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


if __name__ == "__main__":
    print(upload.auth_url(), flush=True)
    srv = HTTPServer(("127.0.0.1", 8765), Handler)
    srv.timeout = 600
    for _ in range(20):  # stray connections (port probes, favicon) must not end the wait
        srv.handle_request()
        if Handler.result != "waiting":
            break
    print(Handler.result, flush=True)
    sys.exit(0 if Handler.result.startswith("ok") else 1)
