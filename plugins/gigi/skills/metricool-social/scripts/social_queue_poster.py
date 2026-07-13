# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Arona social auto-poster — drains queue.json, 1 reel/brand/day, cross-posts FB+IG.

Runs headless via launchd (no LLM, ~$0). Content is PRE-VETTED (Gemini video + catalog +
blacklist + duration) and already on Vercel Blob (public), so no NAS needed at post-time.
Secrets (Meta SU token) come from KB via social_post.py — nothing secret stored here.

Usage:
  python social_queue_poster.py            # dry-run: what would post today
  python social_queue_poster.py --apply    # post 1 reel/brand for brands due today
  python social_queue_poster.py --brand Esteban --apply   # just one brand
  python social_queue_poster.py --limit 1 --apply         # cap #brands this run
"""
import json, os, sys, subprocess, argparse, datetime
from zoneinfo import ZoneInfo
RO_TZ = ZoneInfo("Europe/Bucharest")  # slots/when in real RO wall-clock (VPS runs on Berlin)

QDIR = os.path.dirname(os.path.abspath(__file__))
QFILE = os.path.join(QDIR, "queue.json")
LOG = os.path.join(QDIR, "poster.log")
SKILL = "/Users/gheorghebeschea/Downloads/Scripturi/team-intelligence/plugins/gigi/skills/social-post"

# queue-brand -> Metricool brand label (for TikTok via mc_post.py). Only these get TikTok.
MC_LABELS = {"Esteban": "Esteban", "GT": "George Talent", "Gento": "Gento",
             "Belasil": "Belasil", "Nocturna": "Nocturna", "Rossi": "ROSSI Nails",
             "Nubra": "Nubra", "Lab Noir": "Lab Noir"}

def today():
    return datetime.date.today().isoformat()

def logline(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line)
    with open(LOG, "a") as f:
        f.write(line + "\n")

REG_FILE = os.path.join(QDIR, "posted_registry.json")

def _reg():
    if os.path.exists(REG_FILE):
        return json.load(open(REG_FILE))
    return {"posted": []}

def reg_has(brand, src, url):
    keys = {(p["brand"], p.get("src") or p.get("url")) for p in _reg()["posted"]}
    labels = {brand, MC_LABELS.get(brand, brand)}
    return any((lb, src) in keys or (lb, url) in keys for lb in labels)

def reg_add(brand, src, url, platforms):
    reg = _reg()
    reg["posted"].append({"brand": brand, "src": src, "url": url,
                          "platforms": platforms, "at": today()})
    json.dump(reg, open(REG_FILE, "w"), ensure_ascii=False, indent=1)

def next_unposted(reels, brand=None):
    for r in reels:
        if r.get("posted"):
            continue
        if brand and reg_has(brand, r.get("src"), r.get("url")):
            r["posted"] = True  # already posted in a prior queue — sync flag
            continue
        return r
    return None

NETWORKS = "tiktok,instagram,facebook,youtube"  # everything via Metricool

def load_recipe():
    rp = os.path.join(QDIR, "recipe.json")
    if os.path.exists(rp):
        return json.load(open(rp))
    return {"hours": [13, 15, 18, 20], "slots": {"default": 1}}

def today_schedule(now):
    """Recipe-aware slots: good hours, more on Sat/Fri. Rolls to tomorrow if today's hours passed.
    Returns list of 'YYYY-MM-DDTHH:MM:00'."""
    rec = load_recipe()
    hours = rec.get("hours", [13, 15, 18, 20])
    upcoming = [h for h in hours if h > now.hour]
    base, picked_hours = (now, upcoming) if upcoming else (now + datetime.timedelta(days=1), hours)
    wd = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][base.weekday()]
    n = rec.get("slots", {}).get(wd, rec.get("slots", {}).get("default", 1))
    picked = picked_hours[:n]
    return [base.replace(hour=h, minute=0, second=0, microsecond=0).strftime("%Y-%m-%dT%H:%M:00") for h in picked]

def _mc(label, networks, reel, when, apply):
    cmd = ["uv", "run", "mc_post.py", "post", "--brand", label,
           "--network", networks, "--media", reel["url"], "--text", reel["caption"]]
    if when:
        cmd += ["--when", when]
    cmd.append("--publish" if apply else "--dry")  # dry = print only, no Metricool write
    p = subprocess.run(cmd, cwd=QDIR, capture_output=True, text=True)
    out = (p.stdout or "") + (p.stderr or "")
    ok = ("HTTP 200" in out) or ("[DRY]" in out and not apply)
    tail = "\n".join(l for l in out.splitlines()
                     if any(k in l for k in ("PUBLISH", "[DRY]", "HTTP", "⚠️", "negasit")))
    return ok, tail

def post(brand, reel, apply, when=None):
    """Publish to ALL networks; if that fails, retry WITHOUT youtube (some reels fail YT Shorts
    validation and would otherwise kill the whole post)."""
    label = MC_LABELS.get(brand, brand)
    ok, tail = _mc(label, NETWORKS, reel, when, apply)
    if not ok and apply and "youtube" in NETWORKS:
        nets = ",".join(n for n in NETWORKS.split(",") if n != "youtube")
        ok2, tail2 = _mc(label, nets, reel, when, apply)
        tail += "\n      ↻ retry fără youtube → " + ("OK" if ok2 else "tot EȘUAT")
        ok = ok2
    return ok, tail

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--brand", help="post only this brand (no rotation change)")
    ap.add_argument("--limit", type=int, default=0, help="#brands this run; 0 = recipe-driven (more on Sat/Fri)")
    ap.add_argument("--no-tiktok", dest="tiktok", action="store_false", help="skip TikTok (Metricool)")
    a = ap.parse_args()

    if not os.path.exists(QFILE):
        logline(f"NO QUEUE at {QFILE}"); sys.exit(1)
    q = json.load(open(QFILE))
    brands = q["brands"]
    last = q.setdefault("last_post_date", {})
    rotation = q.get("rotation", list(brands.keys()))
    rot_idx = q.get("rot_idx", 0) % max(1, len(rotation))
    td = today()
    logline(f"=== run {'APPLY' if a.apply else 'DRY'} — today {td} — rot@{rotation[rot_idx]} ===")

    posted_now = 0
    if a.brand:  # manual single-brand, no rotation advance
        reel = next_unposted(brands.get(a.brand, []), a.brand)
        if not reel:
            logline(f"  {a.brand}: coadă goală");
        else:
            logline(f"  {a.brand}: post {reel.get('dur')}s {reel['src'][:40]}")
            ok, tail = post(a.brand, reel, a.apply)
            for t in tail.splitlines(): logline(f"      {t}")
            if a.apply and ok:
                reel["posted"] = True; reel["posted_at"] = td; last[a.brand] = td; posted_now += 1
                reg_add(a.brand, reel.get("src"), reel["url"], ["fb", "ig"] + (["tiktok"] if a.tiktok else []))
            elif a.apply:
                logline(f"      ⚠️ {a.brand}: EȘUAT — nu marchez")
    else:  # round-robin: walk from rot_idx, post next brands at recipe-driven slots
        slots = today_schedule(datetime.datetime.now(RO_TZ))
        target = a.limit if a.limit > 0 else len(slots)
        logline(f"  rețetă: {len(slots)} slot(uri) azi @ {', '.join(s[11:16] for s in slots)} → {target} branduri")
        order = [(rot_idx + k) % len(rotation) for k in range(len(rotation))]
        for i in order:
            if posted_now >= target:
                break
            b = rotation[i]
            if last.get(b) == td:
                continue  # already posted today
            reel = next_unposted(brands.get(b, []), b)
            if not reel:
                continue
            when = slots[posted_now % len(slots)] if slots else None
            logline(f"  {b}: post {reel.get('dur')}s @ {when[11:16] if when else 'now'} {reel['src'][:32]}")
            ok, tail = post(b, reel, a.apply, when)
            for t in tail.splitlines(): logline(f"      {t}")
            if a.apply and ok:
                reel["posted"] = True; reel["posted_at"] = td; last[b] = td; posted_now += 1
                reg_add(b, reel.get("src"), reel["url"], ["fb", "ig"] + (["tiktok"] if a.tiktok else []))
                q["rot_idx"] = (i + 1) % len(rotation)
            elif a.apply:
                logline(f"      ⚠️ {b}: EȘUAT — nu marchez")

    if a.apply:
        json.dump(q, open(QFILE, "w"), ensure_ascii=False, indent=1)
    left = sum(1 for l in brands.values() for r in l if not r.get("posted"))
    logline(f"=== done: {posted_now} postate acum, {left} rămase în coadă ===")

if __name__ == "__main__":
    main()
