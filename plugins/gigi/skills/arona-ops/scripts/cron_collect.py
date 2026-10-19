"""cron_collect.py — colectorul READ-ONLY al MCP-ului arona-ops. Rulează PE server (python3 din sistem, doar stdlib),
trimis pe stdin: `python3 - <mod> [json-args]`. Nu scrie nimic, nu rulează niciun job, nu atinge crontab-ul.

Citește: crontab-urile tuturor utilizatorilor (/var/spool/cron/crontabs/*) + /etc/cron.d (fără fișierele de sistem),
mtime + coada logurilor, tabelul cron_heartbeat din profitability.db (deschis mode=ro), sursele scripturilor (pentru
Google Ads v22) și, la `stare`, liniile CMD din journalctl (când a pornit cron-ul efectiv).

Moduri:  full                      -> JSON cu toate joburile (fără coada logului)
         stare '{"job": "...", "linii": 40}' -> JSON doar cu joburile potrivite + coada logului + journal
"""
import glob
import json
import os
import pwd
import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone

HB_DB = "/root/Scripturi/data/profitability.db"
SYSTEM_CRON_D = {"certbot", "e2scrub_all", "sysstat", "php", ".placeholder"}
SCRIPT_RE = re.compile(r'(?:"((?:/|~|\$)[^";&|]+?\.(?:py|sh|ts|js|mjs))"|(?<![=\w.\-/$~{}])([\w.\-/$~{}]+\.(?:py|sh|ts|js|mjs)))(?![\w.])')
REDIR_RE = re.compile(r'(?<![0-9&])(>>?|&>>?)\s*("([^"]+)"|[^\s;&|)]+)')
CD_RE = re.compile(r'\bcd\s+("([^"]+)"|[^\s;&|]+)')
HB_RE = re.compile(r'heartbeat\.py\s+([\w.\-]+)')
V22_LOG_RE = re.compile(r'[Vv]ersion v22 is deprecated|/v22/|googleads/v22')
V22_RE = re.compile(r'''["']v22["']|/v22/|googleads\.v22|\bv22\.services|version\s*=\s*["']?v22''')
TS_RE = re.compile(r'(20\d\d)-(\d\d)-(\d\d)[ T_](\d\d):(\d\d)(?::(\d\d))?')
HARD = re.compile(r'Traceback \(most recent|command not found|No such file or directory|ModuleNotFoundError|'
                  r'ImportError|SyntaxError|\bKilled\b|Permission denied|Segmentation fault|\[exit [1-9]|'
                  r'exit (?:code|status) [1-9]|Errno \d+')
SOFT = re.compile(r'\bERROR\b|\bError:|\bFAILED\b|\bFATAL\b|\bSTOP\b|[Dd]eprecated|DEPRECATED|\bException\b|'
                  r'Unauthorized|invalid_grant|[Tt]oken (?:has )?expired|\bHTTP(?:Error)? ?(?:4\d\d|5\d\d)\b|'
                  r'status(?:_code)?[=: ]+(?:401|403|429|5\d\d)\b|\bCRITICAL\b')
SECRET_RE = re.compile(r'(?i)((?:token|secret|passw\w*|api_?key|apikey|pwd)\s*[=:]\s*["\']?)([^\s"\'$&;]{8,})')


def mask(s):
    return SECRET_RE.sub(lambda m: m.group(1) + "***", s)


# ------------------------------------------------------------------ cron schedule
MACROS = {"@yearly": "0 0 1 1 *", "@annually": "0 0 1 1 *", "@monthly": "0 0 1 * *", "@weekly": "0 0 * * 0",
          "@daily": "0 0 * * *", "@midnight": "0 0 * * *", "@hourly": "0 * * * *"}
DOW_NAMES = {n: i for i, n in enumerate(["sun", "mon", "tue", "wed", "thu", "fri", "sat"])}
MON_NAMES = {n: i + 1 for i, n in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                              "nov", "dec"])}


def _field(expr, lo, hi, names=None):
    out = set()
    for part in expr.lower().split(","):
        step = 1
        if "/" in part:
            part, st = part.split("/", 1)
            step = int(st)
        if part == "*":
            a, b = lo, hi
        elif "-" in part:
            x, y = part.split("-", 1)
            a = names.get(x, None) if names and x in names else int(x)
            b = names.get(y, None) if names and y in names else int(y)
        else:
            a = names[part] if names and part in names else int(part)
            b = hi if step > 1 else a
        out.update(range(a, b + 1, step))
    return out


class Sched:
    def __init__(self, expr):
        f = expr.split()
        self.mi = _field(f[0], 0, 59)
        self.h = _field(f[1], 0, 23)
        self.dom = _field(f[2], 1, 31)
        self.mon = _field(f[3], 1, 12, MON_NAMES)
        dw = _field(f[4], 0, 7, DOW_NAMES)
        self.dow = {d % 7 for d in dw}
        self.dom_star = f[2].startswith("*")
        self.dow_star = f[4].startswith("*")

    def _day_ok(self, d):
        if d.month not in self.mon:
            return False
        dom_ok = d.day in self.dom
        dow_ok = ((d.weekday() + 1) % 7) in self.dow
        if self.dom_star or self.dow_star:
            return dom_ok and dow_ok
        return dom_ok or dow_ok

    def runs_between(self, start, end):
        """Toate momentele (datetime naive, ora locală) din [start, end]."""
        out = []
        t = start.replace(minute=0, second=0, microsecond=0)
        while t <= end:
            if t.hour in self.h and self._day_ok(t):
                for m in sorted(self.mi):
                    x = t.replace(minute=m)
                    if start <= x <= end:
                        out.append(x)
            t += timedelta(hours=1)
        return out


# ------------------------------------------------------------------ parsing
def _crontab_sources():
    srcs = []
    for f in sorted(glob.glob("/var/spool/cron/crontabs/*")):
        srcs.append((os.path.basename(f), f, False))
    for f in sorted(glob.glob("/etc/cron.d/*")):
        if os.path.basename(f) not in SYSTEM_CRON_D:
            srcs.append(("cron.d/" + os.path.basename(f), f, True))
    return srcs


def _home(user):
    try:
        return pwd.getpwnam(user).pw_dir
    except Exception:
        return "/root"


def _expand(p, home, cwd):
    p = p.replace("$HOME", home).replace("${HOME}", home)
    if p.startswith("~"):
        p = home + p[1:]
    if not p.startswith("/") and cwd:
        p = os.path.normpath(os.path.join(cwd, p))
    return p


def parse_jobs():
    jobs = []
    for src, path, sysfmt in _crontab_sources():
        try:
            lines = open(path, encoding="utf-8", errors="replace").read().splitlines()
        except Exception:
            continue
        owner = src if not sysfmt else None
        for ln, raw in enumerate(lines, 1):
            s = raw.strip()
            if not s or s.startswith("#"):
                continue
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*\s*=", s):
                continue
            if s.startswith("@"):
                tok = s.split(None, 1)
                if tok[0] == "@reboot":
                    sched, rest = "@reboot", tok[1] if len(tok) > 1 else ""
                else:
                    sched, rest = MACROS.get(tok[0], tok[0]), tok[1] if len(tok) > 1 else ""
            else:
                tok = s.split(None, 5)
                if len(tok) < 6:
                    continue
                sched, rest = " ".join(tok[:5]), tok[5]
            user = owner
            if sysfmt:
                u, _, rest = rest.partition(" ")
                user = u
                rest = rest.strip()
            jobs.append({"src": src, "line": ln, "user": user, "schedule": sched, "cmd": rest})
    return jobs


def analyse_cmd(j):
    cmd = j["cmd"]
    home = _home(j["user"] or "root")
    m = CD_RE.search(cmd)
    cwd = _expand((m.group(2) or m.group(1)), home, None) if m else None
    scripts = []
    for mm in SCRIPT_RE.finditer(cmd):
        p = mm.group(1) or mm.group(2)
        b = os.path.basename(p)
        if b in ("heartbeat.py",):
            continue
        scripts.append(_expand(p, home, cwd or "/root/Scripturi" if not p.startswith("/") else None))
    j["scripts"] = scripts
    if scripts:
        main = scripts[0]
        name = os.path.splitext(os.path.basename(main))[0]
        after = cmd.split(os.path.basename(main), 1)[1] if os.path.basename(main) in cmd else ""
        after = re.split(r"\s(?:\d?>>?|&>|&&|\|\|?|;)", " " + after.lstrip('"'), maxsplit=1)[0]
        arg = next((a.lstrip("-") for a in after.split() if "/" not in a and not a.isdigit() and a.lstrip("-")[:1].isalpha()), "")
        j["_arg"] = re.sub(r"[^\w-]", "", arg)[:20]
    else:
        first = [t for t in re.split(r"\s+", re.sub(r"^.*?(&&|;)\s*", "", cmd)) if t][:1]
        name = os.path.basename(first[0]) if first else "?"
        j["_arg"] = ""
    j["name"] = name
    # log
    log = None
    devnull = False
    for mm in REDIR_RE.finditer(cmd):
        tgt = mm.group(3) or mm.group(2)
        if tgt in ("&1", "&2"):
            continue
        if tgt == "/dev/null":
            devnull = True
            continue
        log = _expand(tgt, home, cwd)
        break
    j["log"] = log
    j["log_devnull"] = devnull and not log
    j["log_guess"] = None
    if not log and scripts:
        try:
            body = open(scripts[0], encoding="utf-8", errors="replace").read(200000)
            g = re.search(r'(?<![\w}])((?:/root|/var|/opt|/home|/tmp|/srv)/[\w./\-]+\.log)\b', body)
            if g:
                j["log_guess"] = g.group(1)
        except Exception:
            pass
    # heartbeat
    hm = HB_RE.search(cmd)
    j["hb"] = hm.group(1) if hm else None
    j["hb_conn"] = None
    if hm:
        pre = cmd[:hm.start()]
        idx = {k: pre.rfind(k) for k in ("&&", ";", "||")}
        k = max(idx, key=lambda x: idx[x])
        j["hb_conn"] = k if idx[k] >= 0 else "?"
        if k == "&&" and re.search(r"rc=\$\?;\s*\[\s*\$rc\s+-le\s+\d", pre[-120:]):
            j["hb_conn"] = "&& (rc)"
    j["quiet"] = bool(re.search(r"--quiet(-ok)?\b|-q\b", cmd))
    # scripturile rulate în containere (docker exec … /app/x.py) nu sunt pe discul gazdei
    j["missing"] = [] if "docker exec" in cmd else [p for p in scripts if "$" not in p and not os.path.exists(p)]


def v22_scan(paths, seen_cache):
    hits = []
    todo = list(paths)
    visited = set()
    depth = {p: 0 for p in todo}
    while todo:
        p = todo.pop()
        if p in visited or not os.path.isfile(p):
            continue
        visited.add(p)
        if p in seen_cache:
            body = seen_cache[p]
        else:
            try:
                body = open(p, encoding="utf-8", errors="replace").read(1500000)
            except Exception:
                body = ""
            seen_cache[p] = body
        for i, ln in enumerate(body.splitlines(), 1):
            st = ln.strip()
            if st.startswith("#") or st.startswith("//"):
                continue
            if V22_RE.search(ln):
                hits.append("%s:%d" % (p, i))
                if len(hits) >= 6:
                    return hits
        if depth[p] >= 1:
            continue
        d = os.path.dirname(p)
        refs = set()
        for mm in SCRIPT_RE.finditer(body):
            q = mm.group(1) or mm.group(2)
            if "$" in q or os.path.basename(q) == "heartbeat.py":
                continue
            refs.add(q if q.startswith("/") else os.path.join(d, q))
        if p.endswith(".py"):
            for mm in re.finditer(r"^\s*(?:from|import)\s+([A-Za-z_]\w*)", body, re.M):
                refs.add(os.path.join(d, mm.group(1) + ".py"))
        for q in refs:
            q = os.path.normpath(q)
            if q not in depth:
                depth[q] = depth[p] + 1
                todo.append(q)
    return hits


def read_tail(path, max_bytes=262144, keep=400):
    try:
        with open(path, "rb") as f:
            f.seek(0, 2)
            sz = f.tell()
            f.seek(max(0, sz - max_bytes))
            data = f.read().decode("utf-8", "replace")
        lines = data.splitlines()
        if sz > max_bytes:
            lines = lines[1:]
        return lines[-keep:]
    except Exception:
        return None


def last_run_block(lines, run_start_local, mtime):
    """Liniile ultimei rulări: de la primul timestamp >= începutul rulării (ora logului = locală sau UTC,
    dedusă din ultimul timestamp vs mtime). Fără timestamp-uri: ultimele 40 de linii."""
    if not lines:
        return [], "gol"
    last_ts = None
    for ln in reversed(lines[-200:]):
        m = TS_RE.search(ln)
        if m:
            try:
                last_ts = datetime(*[int(x or 0) for x in m.groups()])
            except ValueError:
                last_ts = None
            break
    if last_ts is None or run_start_local is None:
        return lines[-40:], "ultimele 40 (fără timestamp)"
    mt_local = datetime.fromtimestamp(mtime)
    mt_utc = datetime.fromtimestamp(mtime, timezone.utc).replace(tzinfo=None)
    if abs((last_ts - mt_local).total_seconds()) <= abs((last_ts - mt_utc).total_seconds()):
        start = run_start_local
    else:
        start = run_start_local - (mt_local - mt_utc)
    start -= timedelta(minutes=1)
    idx = None
    for i, ln in enumerate(lines):
        m = TS_RE.search(ln)
        if m:
            try:
                ts = datetime(*[int(x or 0) for x in m.groups()])
            except ValueError:
                continue
            if ts >= start:
                idx = i
                break
    if idx is None:
        return lines[-40:], "ultimele 40 (timestamp-uri vechi)"
    # o rulare MANUALĂ după cea programată: începe după ultimul salt de >30 min între timestamp-uri
    prev = None
    for i in range(idx, len(lines)):
        m = TS_RE.search(lines[i])
        if not m:
            continue
        try:
            ts = datetime(*[int(x or 0) for x in m.groups()])
        except ValueError:
            continue
        if prev is not None and (ts - prev).total_seconds() > 1800:
            idx = i
        prev = ts
    return lines[idx:], "ultima rulare"


def hb_table():
    try:
        cx = sqlite3.connect("file:%s?mode=ro" % HB_DB, uri=True, timeout=10)
        rows = cx.execute("SELECT name, last_ping, expected_interval_min, note FROM cron_heartbeat").fetchall()
        cx.close()
        return {r[0]: {"last_ping": r[1], "interval": r[2], "note": r[3]} for r in rows}
    except Exception:
        return None


def collect(needle=None, nlines=0):
    now = datetime.now().replace(second=0, microsecond=0)
    jobs = parse_jobs()
    for j in jobs:
        analyse_cmd(j)
    # nume unice pe server
    cnt = {}
    for j in jobs:
        cnt[j["name"]] = cnt.get(j["name"], 0) + 1
    seen = {}
    for j in jobs:
        n = j["name"]
        if cnt[n] > 1 and j.get("_arg"):
            n = "%s_%s" % (n, j["_arg"])
        seen[n] = seen.get(n, 0) + 1
        j["id"] = n if seen[n] == 1 else "%s#%d" % (n, seen[n])
    if needle:
        nl = needle.lower()
        exact = [j for j in jobs if j["id"].lower() == nl]
        jobs = exact or [j for j in jobs if nl in j["id"].lower() or nl in j["cmd"].lower()]
    hbs = hb_table()
    cache = {}
    for j in jobs:
        j["cmd"] = mask(j["cmd"])
        if j["schedule"] == "@reboot":
            j["max_gap_min"] = None
            j["last_sched"] = None
        else:
            try:
                sc = Sched(j["schedule"])
                runs = sc.runs_between(now - timedelta(days=35), now)
                j["last_sched"] = runs[-1].strftime("%Y-%m-%d %H:%M") if runs else None
                gaps = [(b - a).total_seconds() / 60 for a, b in zip(runs, runs[1:])]
                j["max_gap_min"] = int(max(gaps)) if gaps else None
                j["_runs"] = runs
            except Exception as e:
                j["sched_err"] = str(e)[:80]
                j["max_gap_min"] = None
                j["last_sched"] = None
        lg = j["log"] or j["log_guess"]
        j["log_mtime"] = None
        if lg and os.path.isfile(lg):
            st = os.stat(lg)
            j["log_mtime"] = datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M")
            j["log_size"] = st.st_size
            runs = j.get("_runs") or []
            mt = datetime.fromtimestamp(st.st_mtime)
            # rulări programate după ultima scriere în log (cu 10 min marjă pentru durata jobului)
            j["missed"] = len([r for r in runs if r > mt + timedelta(minutes=2) and r < now - timedelta(minutes=10)])
            before = [r for r in runs if r <= mt + timedelta(minutes=1)]
            rs = before[-1] if before else None
            tail = read_tail(lg)
            block, how = last_run_block(tail, rs, st.st_mtime)
            j["block_how"] = how
            j["block_len"] = len(block)
            j["hard"] = [mask(x)[:300] for x in block if HARD.search(x)][-8:]
            j["soft"] = [mask(x)[:300] for x in block if SOFT.search(x) and not HARD.search(x)][-8:]
            j["v22_log"] = any(V22_LOG_RE.search(x) for x in block)
            if nlines:
                j["tail"] = [mask(x)[:400] for x in (tail or [])[-nlines:]]
        elif lg:
            j["log_missing"] = True
        if j["hb"] and hbs is not None:
            h = hbs.get(j["hb"])
            j["hb_row"] = h
            if h and h.get("last_ping"):
                try:
                    lp = datetime.strptime(h["last_ping"][:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    j["hb_age_min"] = int((datetime.now(timezone.utc) - lp).total_seconds() / 60)
                except ValueError:
                    pass
        j["v22"] = v22_scan(j["scripts"], cache)
        j.pop("_runs", None)
        j.pop("_arg", None)
        if needle and nlines:
            base = os.path.basename(j["scripts"][0]) if j["scripts"] else j["name"]
            try:
                p = subprocess.run("journalctl -u cron -S -4d --no-pager -o short-iso 2>/dev/null | grep ' CMD ' | "
                                   "grep -F -- %s | tail -5" % json.dumps(base), shell=True, capture_output=True,
                                   text=True, timeout=60)
                j["journal"] = [re.sub(r"CMD \((.{0,140}).*", r"CMD (\1…)", x) for x in p.stdout.splitlines()]
            except Exception:
                j["journal"] = []
    # procese încă în viață (ex. o instanță agățată care ține flock-ul și face ca TOATE rulările să fie sărite)
    try:
        ps = subprocess.run(["ps", "-eo", "pid,etimes,args"], capture_output=True, text=True, timeout=20).stdout
    except Exception:
        ps = ""
    procs = []
    for ln in ps.splitlines()[1:]:
        parts = ln.split(None, 2)
        if len(parts) == 3 and parts[1].isdigit():
            procs.append((parts[0], int(parts[1]), parts[2]))
    for j in jobs:
        if not j["scripts"]:
            continue
        base = os.path.basename(j["scripts"][0])
        mine = [(pid, et) for pid, et, a in procs if base in a and "flock" not in a.split()[0]
                and "cron_collect" not in a and " grep " not in a]
        if mine:
            j["running"] = [{"pid": pid, "min": et // 60} for pid, et in mine][:5]
    orphans = None
    if hbs is not None and not needle:
        used = {j["hb"] for j in jobs if j["hb"]}
        orphans = {k: v for k, v in hbs.items() if k not in used}
    return {"host": os.uname()[1], "now": now.strftime("%Y-%m-%d %H:%M"), "tz": time.strftime("%Z"),
            "hb_db": hbs is not None, "jobs": jobs, "hb_orphans": orphans}


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "full"
    args = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}
    if mode == "stare":
        res = collect(args.get("job") or "", int(args.get("linii", 40)))
    else:
        res = collect()
    sys.stdout.write(json.dumps(res, ensure_ascii=False, default=str))
