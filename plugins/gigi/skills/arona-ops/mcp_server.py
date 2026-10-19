# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2","paramiko>=3.4"]
# ///
"""arona-ops MCP — sănătatea cronurilor ARONA, READ-ONLY: ~115 joburi pe serverul de scripturi (84.46.242.181,
/root/Scripturi + crontab-urile celorlalți useri) și ~30 pe gazda Order Hub (m23726.contaboserver.net).

De ce: pe 19-oct două cronuri erau stricate în tăcere (regimul de weekend Grandia pe Google Ads v22; split_sales_watch
crăpa din 9-oct, iar heartbeat-ul lui rula după `;`, deci watchdog-ul îl vedea viu). Tool-urile de aici citesc
crontab-ul, logurile, tabelul cron_heartbeat și sursele scripturilor și spun CE e stricat.

Cum: scripts/cron_collect.py (doar stdlib) e trimis pe stdin la `python3 -` pe fiecare server prin ssh cu cheie
(core/scripts/arona_ssh.py). Doar citiri: crontab, stat, coada logurilor, sqlite mode=ro, ps, journalctl.
NU editează crontab-ul, NU rulează joburi. data_health.py rulează fără --email (raport, nu scrie nimic);
deploy_paritate cheamă check() din deploy_parity.py fără `git fetch` și fără --email (nu atinge parity_state).

Register: claude mcp add --scope user arona-ops -- uv run <abs path>/mcp_server.py
"""
import json
import logging
import os
import shlex
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "core", "scripts"))
sys.path.insert(0, CORE)
import arona_ssh  # noqa: E402

logging.getLogger("paramiko").setLevel(logging.WARNING)

from mcp.server.fastmcp import FastMCP  # noqa: E402

mcp = FastMCP("arona-ops")
COLLECT = os.path.join(HERE, "scripts", "cron_collect.py")
MAX_OUT = 60000
SERVERS = {
    "scripturi": None,  # PROFIT_SSH_HOST (KB) / 84.46.242.181
    "orderhub": os.environ.get("ARONA_OPS_OH_HOST", "m23726.contaboserver.net"),
}
ALIASES = {"vps": "scripturi", "scripts": "scripturi", "s": "scripturi", "oh": "orderhub", "order-hub": "orderhub",
           "m23726": "orderhub"}
_CACHE = {}


def _servers(server):
    s = (server or "all").strip().lower()
    if s in ("all", "toate", "*", ""):
        return list(SERVERS)
    s = ALIASES.get(s, s)
    if s not in SERVERS:
        raise ValueError("server necunoscut: %r (scripturi / orderhub / all)" % server)
    return [s]


def _ssh(server, cmd, stdin_file=None, timeout=300):
    """(stdout, stderr, rc) pentru `cmd` pe server. Erorile de conexiune -> (None, mesaj, -1)."""
    host = SERVERS[server]
    if host is None:
        for k in ("PROFIT_SSH_HOST", "PROFIT_SSH_USER"):
            if not os.environ.get(k):
                os.environ[k] = arona_ssh._secret(k) or (arona_ssh.DEFAULT_HOST if k.endswith("HOST")
                                                          else arona_ssh.DEFAULT_USER)
    try:
        cl = arona_ssh.connect(host=host, user="root" if host else None)
    except Exception as e:
        return None, "SSH către %s a eșuat: %s" % (server, str(e)[:600]), -1
    try:
        i, o, e = cl.exec_command(cmd, timeout=timeout)
        if stdin_file:
            with open(stdin_file, "rb") as f:
                i.write(f.read())
        i.channel.shutdown_write()
        out = o.read().decode("utf-8", "replace")
        err = e.read().decode("utf-8", "replace")
        rc = o.channel.recv_exit_status()
        return out, err, rc
    except Exception as ex:
        return None, "eroare pe %s: %s: %s" % (server, type(ex).__name__, str(ex)[:300]), -1
    finally:
        cl.close()


def _collect(server, mode="full", args=None, ttl=90):
    key = (server, mode, json.dumps(args or {}, sort_keys=True))
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < ttl:
        return hit[1]
    cmd = "python3 - %s %s" % (mode, shlex.quote(json.dumps(args)) if args else "")
    out, err, rc = _ssh(server, cmd, COLLECT, timeout=300)
    if out is None or rc != 0:
        raise RuntimeError("[%s] colectorul a eșuat (rc %s): %s" % (server, rc, (err or "")[-800:]))
    d = json.loads(out)
    _CACHE[key] = (time.time(), d)
    return d


def _clip(s, n=MAX_OUT):
    return s if len(s) <= n else s[:n] + "\n… (trunchiat)"


def _short(p, n=48):
    if not p:
        return "-"
    for pre in ("/root/Scripturi/", "/root/"):
        if p.startswith(pre):
            p = p[len(pre):]
            break
    return p if len(p) <= n else "…" + p[-(n - 1):]


def _gap_txt(m):
    if m is None:
        return "?"
    if m % 1440 == 0:
        return "%dz" % (m // 1440)
    if m % 60 == 0:
        return "%dh" % (m // 60)
    return "%dmin" % m


def _hb_txt(j):
    if not j.get("hb"):
        return "—"
    c = j.get("hb_conn") or "?"
    age = j.get("hb_age_min")
    a = "" if age is None else " (%s)" % _age(age)
    return "%s %s%s" % (c, j["hb"], a)


def _age(minutes):
    if minutes is None:
        return "?"
    if minutes < 120:
        return "%dmin" % minutes
    if minutes < 2880:
        return "%.1fh" % (minutes / 60)
    return "%.1fz" % (minutes / 1440)


def _since_min(now, ts):
    from datetime import datetime
    try:
        return int((datetime.strptime(now, "%Y-%m-%d %H:%M") - datetime.strptime(ts, "%Y-%m-%d %H:%M"))
                   .total_seconds() / 60)
    except Exception:
        return None


def _issues(j, now):
    """[(nivel, categorie, detaliu)] — nivel: CRIT / ATENȚIE / INFO."""
    out = []
    gap = j.get("max_gap_min")
    if j.get("missing"):
        out.append(("CRIT", "script lipsă", ", ".join(_short(p, 80) for p in j["missing"])))
    if j.get("v22") or j.get("v22_log"):
        det = ", ".join(_short(p, 70) for p in (j.get("v22") or []))
        out.append(("CRIT", "Google Ads v22 (blocat din 17-oct)",
                    det or "logul ultimei rulări: «Version v22 is deprecated»"))
    if j.get("hb_conn") in (";", "||", "?"):
        out.append(("CRIT", "heartbeat după `%s`" % j["hb_conn"],
                    "%s pinguie și când jobul CRAPĂ → watchdog-ul îl vede viu. Pune `&&` (sau tiparul "
                    "`rc=$?; [ $rc -le 1 ] && heartbeat.py` dacă exit 1 = „am găsit probleme”)" % j["hb"]))
    if j.get("hard"):
        out.append(("CRIT", "eroare în ultima rulare", " | ".join(j["hard"][-2:])[:400]))
    if j.get("hb"):
        row = j.get("hb_row")
        age = j.get("hb_age_min")
        if row is None and "hb_row" in j:
            out.append(("CRIT", "heartbeat fără niciun ping", "%s nu e în cron_heartbeat" % j["hb"]))
        elif age is not None and gap and age > gap * 2 + 30:
            out.append(("CRIT", "heartbeat întârziat", "%s: ultimul ping acum %s, jobul rulează la %s"
                        % (j["hb"], _age(age), _gap_txt(gap))))
    hb_fresh = j.get("hb_age_min") is not None and gap and j["hb_age_min"] <= gap * 2 + 30
    lm = j.get("log_mtime")
    if lm and gap and j.get("missed", 0) >= 2 and not j.get("quiet") and not hb_fresh:
        since = _since_min(now, lm)
        if since is not None and since > gap * 2 + 30:
            out.append(("ATENȚIE", "log tăcut", "ultima scriere %s (acum %s), %d rulări programate fără nicio "
                        "linie — jobul nu mai rulează, iese mut, sau flock-ul e ținut" % (lm, _age(since),
                                                                                          j["missed"])))
    if j.get("log_missing") and j.get("last_sched"):
        if j.get("log"):
            out.append(("ATENȚIE", "log inexistent", _short(j["log"], 80)))
        else:
            out.append(("INFO", "log ghicit inexistent", ""))
    for r in j.get("running") or []:
        if r["min"] > max((gap or 60) * 3, 120):
            out.append(("ATENȚIE", "proces agățat", "pid %s rulează de %s (interval %s) — dacă ține flock-ul, "
                        "rulările următoare sunt sărite în tăcere" % (r["pid"], _age(r["min"]), _gap_txt(gap))))
    if j.get("soft"):
        out.append(("ATENȚIE", "avertismente în ultima rulare", " | ".join(j["soft"][-3:])[:500]))
    if not j.get("hb"):
        out.append(("INFO", "fără heartbeat", ""))
    if not j.get("log") and not j.get("log_guess"):
        out.append(("INFO", "fără log", "ieșirea merge în /dev/null" if j.get("log_devnull") else
                    "ieșirea nu e redirecționată (mail local)"))
    return out


# ---------------------------------------------------------------------------------------------- tools
@mcp.tool()
def cron_lista(server: str = "all", filtru: str = "") -> str:
    """Crontab-urile ACTIVE, parsate: pe fiecare job — id (numele scurt, de folosit în cron_stare), programarea,
    intervalul maxim dintre rulări, fișierul de log, ultima scriere în log, heartbeat-ul și cu ce e legat
    (`&&` corect / `;` DEFECT — pinguie și când jobul crapă), plus semnale scurte (EROARE, v22, LIPSĂ, TĂCUT).
    server = scripturi (84.46.242.181: root + ceilalți useri + /etc/cron.d) / orderhub (m23726) / all.
    filtru = subșir din id sau din comandă (ex. „grandia”, „heartbeat”, „bucsa”). Doar citire."""
    try:
        srvs = _servers(server)
    except ValueError as e:
        return str(e)
    lines = []
    for s in srvs:
        try:
            d = _collect(s)
        except Exception as e:
            lines.append(str(e))
            continue
        jobs = d["jobs"]
        if filtru:
            f = filtru.lower()
            jobs = [j for j in jobs if f in j["id"].lower() or f in j["cmd"].lower()]
        lines.append("## %s (%s) — %d joburi active · acum %s %s" % (s, d["host"], len(jobs), d["now"], d["tz"]))
        lines.append("id | user | programare (max gap) | log (ultima scriere) | heartbeat | semnale")
        for j in jobs:
            sig = []
            iss = _issues(j, d["now"])
            for lvl, cat, _ in iss:
                if lvl != "INFO":
                    sig.append(("‼ " if lvl == "CRIT" else "") + cat)
            lines.append("%s | %s | %s (%s) | %s (%s) | %s | %s" % (
                j["id"], j.get("user") or "-", j["schedule"], _gap_txt(j.get("max_gap_min")),
                _short(j.get("log") or (("~" + j["log_guess"]) if j.get("log_guess") else None)),
                j.get("log_mtime") or "-", _hb_txt(j), "; ".join(sig) or "ok"))
        lines.append("")
    lines.append("Legendă: log „~…” = ghicit din corpul scriptului (cron-ul nu redirecționează). Heartbeat „;” = "
                 "defect. Detalii pe un job: cron_stare(job). Doar problemele: cron_probleme().")
    return _clip("\n".join(lines))


@mcp.tool()
def cron_stare(job: str, linii: int = 40, server: str = "all") -> str:
    """Starea unui job de cron: comanda (secretele mascate), programarea și ultima rulare PROGRAMATĂ, ultima scriere
    în log, ultimele `linii` linii din log, liniile de eroare din ULTIMA rulare (Traceback / ERROR / STOP /
    deprecated / HTTP 4xx-5xx), heartbeat-ul (ultimul ping, legătura && sau ;), procesele încă în viață, când a
    pornit efectiv cron-ul (journalctl CMD, ultimele 4 zile) și referințele Google Ads v22 din script.
    job = id din cron_lista (ex. split_sales_watch, grandia_weekend_regime) sau un subșir din comandă.
    server = scripturi / orderhub / all. Doar citire."""
    try:
        srvs = _servers(server)
    except ValueError as e:
        return str(e)
    linii = max(0, min(int(linii), 400))
    out = []
    found = 0
    for s in srvs:
        try:
            d = _collect(s, "stare", {"job": job, "linii": linii or 1}, ttl=0)
        except Exception as e:
            out.append(str(e))
            continue
        for j in d["jobs"]:
            found += 1
            out.append("## %s · %s (%s, %s)" % (j["id"], s, d["host"], d["tz"]))
            out.append("sursă: %s:%s · user %s" % (j["src"], j["line"], j.get("user")))
            out.append("programare: %s · interval maxim %s · ultima rulare programată %s"
                       % (j["schedule"], _gap_txt(j.get("max_gap_min")), j.get("last_sched") or "-"))
            out.append("comandă: %s" % j["cmd"][:900])
            out.append("log: %s · ultima scriere %s · %s" % (
                j.get("log") or (("~" + j["log_guess"] + " (ghicit)") if j.get("log_guess") else "—"),
                j.get("log_mtime") or "-",
                ("%s rulări programate fără scriere" % j["missed"]) if j.get("missed") else "la zi"))
            hb = j.get("hb_row")
            if j.get("hb"):
                out.append("heartbeat: %s legat cu `%s` · ultimul ping %s UTC (acum %s) · interval în tabel %s" % (
                    j["hb"], j.get("hb_conn"), (hb or {}).get("last_ping", "NICIUNUL"),
                    _age(j.get("hb_age_min")), (hb or {}).get("interval", "-")))
            else:
                out.append("heartbeat: — (jobul nu pinguie; dacă moare, nimeni nu observă)")
            if j.get("running"):
                out.append("procese în viață: " + ", ".join("pid %s de %s" % (r["pid"], _age(r["min"]))
                                                            for r in j["running"]))
            if j.get("journal"):
                out.append("pornit de cron (journal):")
                out += ["  " + x for x in j["journal"]]
            iss = [x for x in _issues(j, d["now"]) if x[0] != "INFO"]
            out.append("verdict: " + ("OK" if not iss else ""))
            for lvl, cat, det in iss:
                out.append("  [%s] %s — %s" % (lvl, cat, det))
            if j.get("hard") or j.get("soft"):
                out.append("linii de eroare în ultima rulare (%s):" % j.get("block_how", ""))
                out += ["  " + x for x in (j.get("hard") or []) + (j.get("soft") or [])]
            if linii and j.get("tail") is not None:
                out.append("ultimele %d linii din log:" % len(j["tail"]))
                out += ["  " + x for x in j["tail"]]
            out.append("")
    if not found:
        out.append("Niciun job nu se potrivește cu %r. Vezi id-urile cu cron_lista()." % job)
    return _clip("\n".join(out))


@mcp.tool()
def cron_probleme(server: str = "all", cu_info: bool = False) -> str:
    """Ce e STRICAT sau SUSPECT în cronuri acum, grupat pe gravitate: script lipsă, eroare în ultima rulare
    (Traceback / „command not found” / Permission denied), scripturi sau loguri încă pe Google Ads v22, heartbeat
    legat cu `;` (pinguie și la crash — exact cazul split_sales_watch), heartbeat întârziat față de programare sau
    fără niciun ping, log tăcut de mai multe rulări, proces agățat (ține flock-ul), avertismente (ERROR / STOP /
    deprecated / 401 / 404) și heartbeat-urile din cron_heartbeat fără cron (timere systemd, joburi retrase).
    cu_info=true adaugă lista joburilor FĂRĂ heartbeat și fără log. server = scripturi / orderhub / all. Doar citire."""
    try:
        srvs = _servers(server)
    except ValueError as e:
        return str(e)
    out = []
    for s in srvs:
        try:
            d = _collect(s)
        except Exception as e:
            out.append(str(e))
            continue
        groups = {"CRIT": [], "ATENȚIE": [], "INFO": {}}
        for j in d["jobs"]:
            for lvl, cat, det in _issues(j, d["now"]):
                if lvl == "INFO":
                    groups["INFO"].setdefault(cat, []).append(j["id"])
                else:
                    groups[lvl].append((j["id"], cat, det))
        n_c, n_a = len({x[0] for x in groups["CRIT"]}), len({x[0] for x in groups["ATENȚIE"]})
        out.append("## %s (%s) — %d joburi · %d CRITICE · %d de verificat · acum %s %s"
                   % (s, d["host"], len(d["jobs"]), n_c, n_a, d["now"], d["tz"]))
        for lvl, title in (("CRIT", "CRITIC"), ("ATENȚIE", "DE VERIFICAT")):
            if groups[lvl]:
                out.append("### %s" % title)
                for jid, cat, det in groups[lvl]:
                    out.append("- %s — %s: %s" % (jid, cat, det))
        orph = d.get("hb_orphans") or {}
        if orph:
            out.append("### heartbeat-uri fără cron (timere systemd / joburi retrase)")
            from datetime import datetime, timezone
            for name, r in sorted(orph.items()):
                age = None
                try:
                    lp = datetime.strptime(str(r["last_ping"])[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                    age = int((datetime.now(timezone.utc) - lp).total_seconds() / 60)
                except Exception:
                    pass
                iv = r.get("interval")
                stale = age is not None and iv and age > iv * 2 + 30
                out.append("- %s: ultimul ping %s (acum %s), interval %s%s%s" % (
                    name, r.get("last_ping"), _age(age), iv, " — ÎNTÂRZIAT" if stale else "",
                    (" · " + r["note"][:90]) if r.get("note") else ""))
        if cu_info:
            for cat, ids in groups["INFO"].items():
                out.append("### %s (%d)" % (cat, len(ids)))
                out.append(", ".join(ids))
        else:
            info = ", ".join("%s: %d" % (c, len(v)) for c, v in groups["INFO"].items())
            if info:
                out.append("(info — %s; cu_info=true le listează)" % info)
        out.append("")
    out.append("Note: „ultima rulare” = liniile din log de la ultima rulare programată (după timestamp-uri; fără "
               "timestamp — ultimele 40 de linii). Un log comun mai multor joburi apare la fiecare. Logurile "
               "scriptic tăcute (--quiet) nu sunt judecate după vechime. Detalii: cron_stare(job).")
    return _clip("\n".join(out))


@mcp.tool()
def data_health(din_log: bool = False) -> str:
    """Raportul data_health.py (watchdog-ul de prospețime a DATELOR pe serverul de scripturi): spend pe platforme,
    brand_pnl, fx, tokenuri, sync_runs, AWBprint, WMS, profit_orders și heartbeat-urile cronurilor (dead-man-switch),
    grupat CRITIC / ATENȚIE / OK. Rulează ACUM fără --email (doar raport, nu trimite mail, nu scrie nimic; ~1 min).
    din_log=true = nu rulează, citește ultimul raport din logs/data_health.log (cronul de 09:15). Doar citire."""
    if din_log:
        out, err, rc = _ssh("scripturi", "tail -n 400 /root/Scripturi/logs/data_health.log", timeout=60)
        if out is None:
            return err
        ls = out.splitlines()
        idx = max((i for i, x in enumerate(ls) if x.startswith("DATA HEALTH ARONA")), default=0)
        return _clip("(din log)\n" + "\n".join(ls[idx:]))
    cmd = ("cd /root/Scripturi && set -a && . ./.env && set +a && "
           "timeout 900 .venv/bin/python data_health.py 2>&1")
    out, err, rc = _ssh("scripturi", cmd, timeout=960)
    if out is None:
        return err
    tail = "" if rc in (0, 1) else "\n[exit %s] %s" % (rc, (err or "")[-400:])
    return _clip(out.strip() + tail)


PARITY_SNIPPET = r"""
import os, sys, subprocess
sys.argv = ['deploy_parity.py']
sys.path.insert(0, '/root/Scripturi')
import deploy_parity as d
rows = d.check()
drift = [r for r in rows if r[0] in ('DIFERĂ', 'FLAT-LIPSĂ', 'GIT-LIPSĂ')]
fh = os.path.join(d.REPO, '.git', 'FETCH_HEAD')
import datetime
age = datetime.datetime.fromtimestamp(os.path.getmtime(fh)).strftime('%Y-%m-%d %H:%M') if os.path.exists(fh) else '?'
head = subprocess.run(['git', '-C', d.REPO, 'log', '-1', '--format=%h %cd %s', '--date=format:%Y-%m-%d %H:%M', d.REF],
                      capture_output=True, text=True).stdout.strip()
print('PARITATE git(%s) ↔ VPS flat (%s) — %d fișiere verificate' % (d.REF, d.FLAT, len(rows)))
print('%s = %s · ultimul git fetch pe server: %s' % (d.REF, head, age))
if drift:
    print('DIVERGENȚE (%d):' % len(drift))
    for st, b, rel in drift:
        print('  %-11s %-30s %s' % (st, b, rel))
else:
    print('Toate fișierele flat sunt identice cu git.')
if SHOW_ALL:
    print('IDENTIC:')
    print('  ' + ', '.join(b for st, b, _ in rows if st == 'IDENTIC'))
"""


@mcp.tool()
def deploy_paritate(toate: bool = False) -> str:
    """Paritatea codului git(origin/main) ↔ fișierele flat din /root/Scripturi (deploy_parity.check): IDENTIC /
    DIFERĂ / FLAT-LIPSĂ pe fiecare script deployat flat. DIFERĂ = fie VPS-ul are un fix necomis (adu-l în git),
    fie git-ul are o versiune nedeployată (deploy.sh --apply — NU din tool-ul ăsta). Compară cu origin/main așa
    cum a fost adus de ultimul `git fetch` al serverului (cronul de 09:45) — tool-ul NU face fetch, NU trimite mail,
    NU scrie parity_state. toate=true listează și fișierele identice. Doar citire."""
    code = "SHOW_ALL = %s\n" % bool(toate) + PARITY_SNIPPET
    import base64
    b64 = base64.b64encode(code.encode()).decode()
    cmd = "cd /root/Scripturi && echo %s | base64 -d | .venv/bin/python - 2>&1" % b64
    out, err, rc = _ssh("scripturi", cmd, timeout=300)
    if out is None:
        return err
    return _clip(out.strip() + ("" if rc == 0 else "\n[exit %s] %s" % (rc, (err or "")[-400:])))


if __name__ == "__main__":
    mcp.run()
