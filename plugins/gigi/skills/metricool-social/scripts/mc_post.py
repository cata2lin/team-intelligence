# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31","psycopg2-binary>=2.9"]
# ///
"""Metricool poster — schedule/publish a post to any connected network (TikTok, FB, IG...).

Token from KB (METRICOOL_API_TOKEN, X-Mc-Auth header). userId + blogId auto-resolved
from /admin/simpleProfiles (cached in mc_brands.json). Draft by default (safe).

Usage:
  python mc_post.py brands                          # list brands + connected networks
  python mc_post.py post --brand "George Talent" --network tiktok --media URL --text "..."          # DRAFT
  python mc_post.py post --brand "George Talent" --network tiktok --media URL --text "..." --publish # schedule+autopublish
"""
import sys, os, json, argparse, subprocess, datetime, requests

QDIR = os.path.dirname(os.path.abspath(__file__))
KB = "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/core/scripts/kb.py"
BASE = "https://app.metricool.com/api"
BRANDS_CACHE = os.path.join(QDIR, "mc_brands.json")

def token():
    # 1) direct DB via KB_DATABASE_URL (portable: Mac + VPS). secrets.value is plaintext.
    url = os.environ.get("KB_DATABASE_URL") or subprocess.run(
        ["/bin/zsh", "-lc", "echo $KB_DATABASE_URL"], capture_output=True, text=True).stdout.strip()
    if url:
        try:
            import psycopg2
            with psycopg2.connect(url, connect_timeout=15) as c, c.cursor() as cur:
                cur.execute("SELECT value FROM secrets WHERE key='METRICOOL_API_TOKEN'")
                row = cur.fetchone()
                if row and row[0]:
                    return row[0].strip()
        except Exception:
            pass
    # 2) fallback: kb.py (Mac path)
    if os.path.exists(KB):
        t = subprocess.run(["/bin/zsh", "-lc", f"uv run '{KB}' secret-get METRICOOL_API_TOKEN"],
                           capture_output=True, text=True).stdout.strip()
        if t:
            return t
    sys.exit("no METRICOOL_API_TOKEN (nici KB_DATABASE_URL, nici kb.py)")

def H(tok):
    return {"X-Mc-Auth": tok, "Content-Type": "application/json"}

def brands(tok, refresh=False):
    if not refresh and os.path.exists(BRANDS_CACHE):
        return json.load(open(BRANDS_CACHE))
    d = requests.get(f"{BASE}/admin/simpleProfiles", headers=H(tok), timeout=30).json()
    json.dump(d, open(BRANDS_CACHE, "w"), ensure_ascii=False)
    return d

def resolve(tok, name):
    d = brands(tok)
    nl = name.lower()
    for b in d:
        if b.get("label", "").lower() == nl:
            return b
    for b in d:
        if nl in b.get("label", "").lower():
            return b
    sys.exit(f"brand '{name}' negasit. Disponibile: " + ", ".join(b.get('label','') for b in d))

def net_connected(b, network):
    key = {"tiktok": "tiktok", "facebook": "facebookPageId", "instagram": "instagram",
           "youtube": "youtube", "twitter": "twitter", "linkedin": "linkedin"}.get(network, network)
    return bool(b.get(key))

def post(a):
    tok = token()
    b = resolve(tok, a.brand)
    uid = b["userId"]; blog = b["id"]
    nets = [n.strip() for n in a.network.split(",") if n.strip()]
    connected = [n for n in nets if net_connected(b, n)]
    missing = [n for n in nets if n not in connected]
    if missing:
        print(f"⚠️  {b['label']}: {','.join(missing)} NU conectat in Metricool — sar peste.")
    if not connected and not a.force:
        return
    connected = connected or nets  # --force fallback
    text = a.text.replace("\n", " ").strip() if "tiktok" in connected else a.text  # TikTok API: no line breaks
    # real Bucharest wall-clock, independent of system TZ (VPS runs on Berlin = RO-1h)
    from zoneinfo import ZoneInfo
    when = a.when or (datetime.datetime.now(ZoneInfo("Europe/Bucharest")) + datetime.timedelta(minutes=20)).strftime("%Y-%m-%dT%H:%M:00")
    body = {
        "publicationDate": {"dateTime": when, "timezone": "Europe/Bucharest"},
        "text": text,
        "providers": [{"network": n} for n in connected],
        "media": [a.media],
        "autoPublish": bool(a.publish),
        "draft": (not a.publish),
    }
    if "tiktok" in connected:
        body["tiktokData"] = {"privacyOption": "PUBLIC_TO_EVERYONE", "disableComment": False,
                              "disableDuet": False, "disableStitch": False, "commercialContentThirdParty": False,
                              "commercialContentOwnBrand": False}
    if "instagram" in connected:
        body["instagramData"] = {"autoPublish": bool(a.publish), "type": "REEL", "showReelOnFeed": True}
    if "youtube" in connected:
        title = (text.split("\n")[0].split("#")[0]).strip()[:90] or b["label"]
        body["youtubeData"] = {"title": title, "privacy": "public", "type": "SHORT", "madeForKids": False}
    url = f"{BASE}/v2/scheduler/posts?userId={uid}&blogId={blog}"
    mode = "PUBLISH(scheduled+autopublish)" if a.publish else "DRAFT"
    print(f"[{mode}] {b['label']} → {'+'.join(connected)} @ {when}")
    if a.dry:
        print(json.dumps(body, ensure_ascii=False, indent=1)); return
    r = requests.post(url, headers=H(tok), json=body, timeout=60)
    print("HTTP", r.status_code)
    try:
        j = r.json(); print(json.dumps(j, ensure_ascii=False)[:600])
    except Exception:
        print(r.text[:600])

def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("brands")
    p = sub.add_parser("post")
    p.add_argument("--brand", required=True)
    p.add_argument("--network", default="tiktok,instagram,facebook,youtube")
    p.add_argument("--media", required=True)
    p.add_argument("--text", default="")
    p.add_argument("--when", help="YYYY-MM-DDTHH:MM:00 local; default +20min")
    p.add_argument("--publish", action="store_true", help="schedule + autopublish (else DRAFT)")
    p.add_argument("--dry", action="store_true")
    p.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.cmd == "brands":
        for b in brands(token(), refresh=True):
            nets = [n for n in ("tiktok","facebook","instagram","youtube","twitter","linkedin") if net_connected(b,n)]
            print(f"{b['label']:18} blogId={b['id']:<9} {', '.join(nets)}")
    else:
        post(a)

if __name__ == "__main__":
    main()
