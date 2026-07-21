# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Auto-refill al cozii de postare: completeaza singur brandurile care scad sub prag.

Ruleaza din cron (cu ACELASI flock ca posterul, ca sa nu scrie simultan in queue.json).
Pentru fiecare brand din rotatie cu mai putin de --min posturi nepostate, cheama pickerul
potrivit (Drive pt branduri, HA pt magazinele de deals) ca sa-l duca la --target.

  uv run refill_queue.py [--min 3] [--target 5] [--dry]

Brandurile fara sursa (folder gol pe Drive) sunt no-op ieftin: pickerul gaseste 0
candidati si nu consuma Gemini. Se auto-repara singur cand apare continut in folder.
"""
import json, os, subprocess, sys, datetime

QDIR = os.path.dirname(os.path.abspath(__file__))
DEALS = {"Ofertele Zilei", "Magdeal", "Reduceri bune", "Casa Ofertelor"}


def log(msg):
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')}  {msg}"
    print(line, flush=True)
    try:
        with open(f"{QDIR}/refill.log", "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def main():
    a = sys.argv[1:]
    def opt(name, default):
        return int(a[a.index(name) + 1]) if name in a else default
    MIN, TARGET, dry = opt("--min", 3), opt("--target", 5), "--dry" in a

    with open(f"{QDIR}/queue.json", encoding="utf-8") as f:
        q = json.load(f)
    brands = q.get("rotation") or sorted(q.get("brands", {}))

    todo = []
    for b in brands:
        left = sum(1 for x in q.get("brands", {}).get(b, []) if not x.get("posted"))
        if left < MIN:
            todo.append((b, max(1, TARGET - left), left))

    if not todo:
        log(f"coada OK — niciun brand sub pragul de {MIN}")
        return

    log("de completat: " + ", ".join(f"{b} ({have}→{have+n})" for b, n, have in todo))
    if dry:
        log("[dry] nu rulez pickerele")
        return

    for b, n, _have in todo:
        script = "pick_ha_deals.py" if b in DEALS else "pick_drive_brand.py"
        log(f"-> {b}: {script} --per {n}")
        try:
            r = subprocess.run(["uv", "run", script, b, "--per", str(n)],
                               cwd=QDIR, capture_output=True, text=True, timeout=5400)
        except subprocess.TimeoutExpired:
            log(f"   TIMEOUT la {b}"); continue
        for line in r.stdout.splitlines():
            if "adaugat" in line or "candidati" in line or "sarite" in line:
                log("   " + line.strip())
        if r.returncode != 0:
            log(f"   EROARE {b} (rc={r.returncode}): {(r.stderr or '')[-300:]}")

    # bilant final
    with open(f"{QDIR}/queue.json", encoding="utf-8") as f:
        q2 = json.load(f)
    tot = sum(1 for v in q2.get("brands", {}).values() for x in v if not x.get("posted"))
    log(f"refill terminat — {tot} posturi nepostate in coada")


if __name__ == "__main__":
    main()
