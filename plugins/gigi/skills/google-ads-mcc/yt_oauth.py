#!/usr/bin/env python3
"""One-time YouTube OAuth (Desktop loopback). Prints consent URL, catches the redirect,
exchanges for a refresh token, writes it to OUT_FILE. Secrets via env."""
import os, sys, json, urllib.parse, urllib.request, http.server, socketserver

CID = os.environ.get("YT_CLIENT_ID") or os.environ["YOUTUBE_OAUTH_CLIENT_ID"]
CSEC = os.environ.get("YT_CLIENT_SECRET") or os.environ["YOUTUBE_OAUTH_CLIENT_SECRET"]
PORT = int(os.environ.get("YT_PORT", "8765"))
REDIR = f"http://localhost:{PORT}"
# upload (pt videos.insert) + readonly (ca să pot confirma pe ce canal a aterizat consimțământul).
# Suprascrie cu YT_SCOPE (space-separated) dacă vrei doar upload.
SCOPE = os.environ.get("YT_SCOPE", "https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly")
OUT = os.environ.get("YT_OUT", "/tmp/yt_refresh_belasil.txt")

auth = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
    "client_id": CID, "redirect_uri": REDIR, "response_type": "code",
    "scope": SCOPE, "access_type": "offline", "prompt": "consent"})
print("\n=== DESCHIDE ACEST URL ÎN BROWSER (logat cu contul canalului Belasil) ===\n")
print(auth)
print("\n(aștept redirect-ul pe", REDIR, "...)\n", flush=True)

code_holder = {}
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
        code_holder["code"] = q.get("code", [None])[0]
        code_holder["err"] = q.get("error", [None])[0]
        self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.end_headers()
        self.wfile.write("<h2>Gata. Poți închide tab-ul și revii în chat.</h2>".encode())

with socketserver.TCPServer(("", PORT), H) as srv:
    srv.timeout = 600
    while "code" not in code_holder and "err" not in code_holder:
        srv.handle_request()

if code_holder.get("err"):
    print("EROARE consimțământ:", code_holder["err"]); sys.exit(1)
code = code_holder["code"]
data = urllib.parse.urlencode({"code": code, "client_id": CID, "client_secret": CSEC,
    "redirect_uri": REDIR, "grant_type": "authorization_code"}).encode()
r = urllib.request.urlopen(urllib.request.Request("https://oauth2.googleapis.com/token", data=data))
tok = json.load(r)
rt = tok.get("refresh_token")
if not rt:
    print("Nu am primit refresh_token (răspuns:", list(tok.keys()), ") — verifică prompt=consent + scope."); sys.exit(1)
with open(OUT, "w") as f: f.write(rt)
os.chmod(OUT, 0o600)
print("SUCCESS — refresh token salvat în", OUT, "(scope upload OK).")
