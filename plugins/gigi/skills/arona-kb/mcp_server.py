# /// script
# requires-python = ">=3.11"
# dependencies = ["mcp>=1.2,<2", "psycopg2-binary"]
# ///
"""arona-kb MCP — citiri din baza de cunoștințe a echipei (SharedClaude, env KB_DATABASE_URL).

Răspunde la: cine a folosit ce skill / MCP, ce skill-uri lipsesc (evenimentele skill_gap scrise de hook-ul
skill_router), activitatea recentă a echipei, resursele și lecțiile. Fiecare query rulează într-o tranzacție
READ ONLY. Tabelul `secrets` NU e citit de niciun tool (nici valori, nici nume).

Register: claude mcp add --scope user arona-kb -- uv run <abs path>/plugins/gigi/skills/arona-kb/mcp_server.py
"""
import difflib
import json
import os
import re
import subprocess
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import psycopg2
from mcp.server.fastmcp import FastMCP

TZ = ZoneInfo("Europe/Bucharest")
SKILL_SAU_ARONA = "(entity_type='skill' OR (entity_type='mcp' AND entity_name LIKE 'arona-%%'))"


def _dsn():
    """KB_DATABASE_URL din mediu; altfel din ~/.claude/settings.json (env, unde îl pune onboarding-ul),
    apoi din launchctl (macOS). Valoarea nu se printează niciodată."""
    url = os.environ.get("KB_DATABASE_URL", "").strip()
    if url:
        return url
    try:
        with open(os.path.expanduser("~/.claude/settings.json"), encoding="utf-8") as f:
            url = (json.load(f).get("env") or {}).get("KB_DATABASE_URL", "").strip()
        if url:
            return url
    except Exception:
        pass
    try:
        return subprocess.run(["launchctl", "getenv", "KB_DATABASE_URL"], capture_output=True, text=True,
                              timeout=10).stdout.strip()
    except Exception:
        return ""


def _q(sql, args=()):
    dsn = _dsn()
    if not dsn:
        raise RuntimeError("KB_DATABASE_URL lipsește din mediul serverului (re-rulează onboarding-ul).")
    conn = psycopg2.connect(dsn, connect_timeout=12)
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '60s'")
            cur.execute(sql, args)
            return cur.fetchall()
    finally:
        conn.rollback()
        conn.close()


def _ts(s, sfarsit=False):
    """'2026-10-12' = miezul nopții la București (cu sfarsit=True: ziua următoare, deci ziua e inclusă);
    '2026-10-19T04:14Z' / '+00:00' = moment exact; fără fus orar = ora României."""
    s = (s or "").strip()
    if not s:
        raise ValueError("lipsește data")
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s):
        d = datetime.fromisoformat(s).replace(tzinfo=TZ)
        return d + timedelta(days=1) if sfarsit else d
    d = datetime.fromisoformat(s.replace(" ", "T"))
    return d if d.tzinfo else d.replace(tzinfo=TZ)


def _fmt(d):
    return d.astimezone(TZ).strftime("%Y-%m-%d %H:%M")


def _metrici(lo, hi, top=10):
    r = _q(f"""
        WITH s AS (
          SELECT session_uid,
                 count(*) FILTER (WHERE entity_type='skill') sk,
                 count(*) FILTER (WHERE entity_type='skill' AND action='used') sk_used,
                 count(*) FILTER (WHERE {SKILL_SAU_ARONA}) sa,
                 count(*) FILTER (WHERE entity_type='mcp' AND entity_name LIKE 'arona-%%') ar,
                 count(*) FILTER (WHERE entity_type='skill_gap') gap
          FROM events WHERE occurred_at >= %s AND occurred_at < %s AND session_uid IS NOT NULL
          GROUP BY 1)
        SELECT count(*), coalesce(sum(sk),0), coalesce(sum(sk_used),0), coalesce(sum(ar),0), coalesce(sum(gap),0),
               count(*) FILTER (WHERE sk>0), count(*) FILTER (WHERE sa>0), count(*) FILTER (WHERE ar>0)
        FROM s""", (lo, hi))[0]
    n, sk, sk_used, ar, gap, ses_sk, ses_sa, ses_ar = [int(x) for x in r]
    oameni = _q("SELECT count(DISTINCT employee_id) FROM events WHERE occurred_at>=%s AND occurred_at<%s "
                "AND session_uid IS NOT NULL", (lo, hi))[0][0]
    skills = _q("SELECT entity_name, count(*), count(DISTINCT session_uid) FROM events WHERE entity_type='skill' "
                "AND occurred_at>=%s AND occurred_at<%s GROUP BY 1 ORDER BY 2 DESC LIMIT %s", (lo, hi, top))
    mcps = _q("SELECT entity_name, count(*), count(DISTINCT session_uid) FROM events WHERE entity_type='mcp' "
              "AND occurred_at>=%s AND occurred_at<%s GROUP BY 1 ORDER BY 2 DESC LIMIT %s", (lo, hi, top))
    return dict(sesiuni=n, oameni=oameni, skill_ev=sk, skill_used=sk_used, arona_ev=ar, goluri=gap,
                ses_skill=ses_sk, ses_skill_sau_arona=ses_sa, ses_arona=ses_ar,
                skill_per_ses=sk / n if n else 0.0, pct_skill_sau_arona=ses_sa / n if n else 0.0,
                zile=(hi - lo).total_seconds() / 86400, top_skills=skills, top_mcp=mcps)


def _linie_metrici(m):
    n = m["sesiuni"] or 1
    return (f"sesiuni {m['sesiuni']} ({m['sesiuni'] / max(m['zile'], 1e-9):.1f}/zi) · oameni {m['oameni']} · "
            f"skill {m['skill_ev']} (din care used {m['skill_used']}) = {m['skill_per_ses']:.2f}/sesiune · "
            f"sesiuni cu skill sau arona-MCP {m['ses_skill_sau_arona']} = {m['pct_skill_sau_arona']:.0%} "
            f"(doar skill {m['ses_skill'] / n:.0%}, doar arona {m['ses_arona'] / n:.0%}) · "
            f"apeluri arona-* {m['arona_ev']} · goluri {m['goluri']}")


def _top(titlu, rows):
    if not rows:
        return f"{titlu}: —"
    return f"{titlu}: " + ", ".join(f"{n} {c}×/{s}ses" for n, c, s in rows)


mcp = FastMCP("arona-kb")


@mcp.tool()
def kb_utilizare_skilluri(de_la: str, pana_la: str = "", pe_om: bool = False) -> str:
    """Utilizarea skill-urilor și a MCP-urilor într-un interval: sesiuni, evenimente skill per sesiune,
    % sesiuni cu skill SAU MCP arona-*, top skill-uri, top servere MCP. Opțional defalcat pe om.
    de_la / pana_la: '2026-10-12' (zi întreagă, ora României; pana_la e INCLUSĂ) sau moment exact
    '2026-10-19T04:14Z'. pana_la gol = acum."""
    lo = _ts(de_la)
    hi = _ts(pana_la, sfarsit=True) if pana_la.strip() else datetime.now(timezone.utc)
    m = _metrici(lo, hi, top=15)
    out = [f"Interval {_fmt(lo)} → {_fmt(hi)} (ora RO, {m['zile']:.1f} zile)", _linie_metrici(m),
           _top("Top skill-uri", m["top_skills"]), _top("Top MCP", m["top_mcp"])]
    if pe_om:
        rows = _q(f"""
            WITH s AS (SELECT e.employee_id, e.session_uid,
                         count(*) FILTER (WHERE entity_type='skill') sk,
                         count(*) FILTER (WHERE {SKILL_SAU_ARONA}) sa,
                         count(*) FILTER (WHERE entity_type='skill_gap') gap
                       FROM events e WHERE occurred_at>=%s AND occurred_at<%s AND session_uid IS NOT NULL
                       GROUP BY 1,2)
            SELECT coalesce(emp.handle, '?'), count(*), sum(sk), count(*) FILTER (WHERE sa>0), sum(gap)
            FROM s LEFT JOIN employees emp ON emp.id=s.employee_id GROUP BY 1 ORDER BY 2 DESC""", (lo, hi))
        out.append("Pe om (sesiuni · skill/ses · % cu skill sau arona · goluri):")
        for h, n, sk, sa, gap in rows:
            out.append(f"  {h}: {n} · {int(sk) / n:.2f} · {int(sa) / n:.0%} · {int(gap)}")
        top_om = _q("""SELECT coalesce(emp.handle,'?'), e.entity_name, count(*) FROM events e
                       LEFT JOIN employees emp ON emp.id=e.employee_id
                       WHERE e.entity_type='skill' AND occurred_at>=%s AND occurred_at<%s
                       GROUP BY 1,2 ORDER BY 1, 3 DESC""", (lo, hi))
        per = defaultdict(list)
        for h, n, c in top_om:
            if len(per[h]) < 6:
                per[h].append(f"{n} {c}×")
        for h, v in per.items():
            out.append(f"  skill-uri {h}: " + ", ".join(v))
    return "\n".join(out)


@mcp.tool()
def kb_comparatie(cutover: str, zile: int = 7) -> str:
    """ÎNAINTE vs DUPĂ un moment (ex. intrarea în producție a skill_router: '2026-10-19T04:14Z' = merge PR #618).
    Fereastra ÎNAINTE = [cutover − zile, cutover); DUPĂ = [cutover, cutover + zile) tăiată la acum.
    Aceleași metrici ca kb_utilizare_skilluri + diferențele. Fără fus orar în cutover = ora României."""
    cut = _ts(cutover)
    acum = datetime.now(timezone.utc)
    b = _metrici(cut - timedelta(days=zile), cut)
    a_hi = min(cut + timedelta(days=zile), acum)
    if a_hi <= cut:
        return f"Cutover {_fmt(cut)} e în viitor: nu există încă fereastra DUPĂ."
    a = _metrici(cut, a_hi)
    out = [f"Cutover {_fmt(cut)} (ora RO) · fereastră {zile} zile",
           f"ÎNAINTE {_fmt(cut - timedelta(days=zile))} → {_fmt(cut)}: " + _linie_metrici(b),
           f"DUPĂ    {_fmt(cut)} → {_fmt(a_hi)}: " + _linie_metrici(a)]
    if a["zile"] < zile - 0.01:
        out.append(f"⚠ Fereastra DUPĂ are doar {a['zile']:.1f} din {zile} zile: compară ratele (pe sesiune, %), nu totalurile.")
    out.append(f"Δ skill/sesiune {b['skill_per_ses']:.2f} → {a['skill_per_ses']:.2f} "
               f"({a['skill_per_ses'] - b['skill_per_ses']:+.2f}) · Δ % sesiuni cu skill sau arona "
               f"{b['pct_skill_sau_arona']:.0%} → {a['pct_skill_sau_arona']:.0%} "
               f"({(a['pct_skill_sau_arona'] - b['pct_skill_sau_arona']) * 100:+.0f} pp)")
    out += [_top("Top skill-uri ÎNAINTE", b["top_skills"]), _top("Top skill-uri DUPĂ", a["top_skills"]),
            _top("Top MCP ÎNAINTE", b["top_mcp"]), _top("Top MCP DUPĂ", a["top_mcp"])]
    out.append("Notă: din PR #625 hook-ul numără ca skill și citirile din cache și read_skill/run_skill din "
               "second-brain (action 'read' / 'used'), deci o parte din creșterea DUPĂ vine din instrumentare.")
    return "\n".join(out)


def _cheie(t):
    t = re.sub(r"[^\w#]+", " ", t.lower(), flags=re.UNICODE)
    return re.sub(r"\s+", " ", t).strip()


@mcp.tool()
def kb_goluri(zile: int = 7, min_cuvinte: int = 6) -> str:
    """Raportul „ce skill-uri lipsesc": cererile la care skill_router n-a găsit niciun skill (events
    entity_type='skill_gap'), din ultimele `zile` zile. Scoate cererile scurte (< min_cuvinte cuvinte:
    „da", „continuă"), grupează duplicatele și aproape-duplicatele, cu număr, oameni și ultima apariție."""
    rows = _q("""SELECT e.summary, coalesce(emp.handle,'?'), e.occurred_at FROM events e
                 LEFT JOIN employees emp ON emp.id=e.employee_id
                 WHERE e.entity_type='skill_gap' AND e.occurred_at >= now() - make_interval(days => %s)
                 ORDER BY e.occurred_at DESC""", (zile,))
    total = len(rows)
    grupuri = []  # [cheie, text, count, set(oameni), ultima]
    scurte = 0
    for summ, om, ts in rows:
        text = (summ or "").strip()
        if len(text.split()) < min_cuvinte:
            scurte += 1
            continue
        k = _cheie(text)[:160]
        for g in grupuri:
            if g[0] == k or difflib.SequenceMatcher(None, g[0], k).ratio() >= 0.85:
                g[2] += 1; g[3].add(om)
                break
        else:
            grupuri.append([k, text, 1, {om}, ts])
    grupuri.sort(key=lambda g: (-g[2], -len(g[3]), -g[4].timestamp()))
    out = [f"Goluri (skill_gap) ultimele {zile} zile: {total} total · {scurte} sub {min_cuvinte} cuvinte ignorate · "
           f"{len(grupuri)} cereri distincte"]
    for i, (_, text, c, oameni, ts) in enumerate(grupuri, 1):
        out.append(f"{i}. {c}× · {','.join(sorted(oameni))} · {_fmt(ts)} · {text[:200]}")
    if not grupuri:
        out.append("(niciun gol peste prag)")
    return "\n".join(out)


@mcp.tool()
def kb_activitate(zile: int = 1, om: str = "") -> str:
    """Activitatea recentă a echipei din KB: pe om × tip (skill, mcp, db, skill_gap, file, resource, project,
    incident…) și ultimele evenimente notabile (fără zgomotul apelurilor MCP/db). om = handle (ex. 'gigi'),
    gol = toată echipa."""
    om = om.lower().strip()
    filt, args = "", [zile]
    if om:
        filt, args = " AND emp.handle=%s", [zile, om]
    base = ("FROM events e LEFT JOIN employees emp ON emp.id=e.employee_id "
            "WHERE e.occurred_at >= now() - make_interval(days => %s)" + filt)
    pe_tip = _q(f"SELECT coalesce(emp.handle,'?'), e.entity_type, count(*), count(DISTINCT e.session_uid) {base} "
                "GROUP BY 1,2 ORDER BY 1, 3 DESC", tuple(args))
    if not pe_tip:
        return f"Nicio activitate în ultimele {zile} zile" + (f" pentru {om}." if om else ".")
    per = defaultdict(list)
    for h, t, c, s in pe_tip:
        per[h].append(f"{t} {c}")
    out = [f"Activitate ultimele {zile} zile" + (f" · {om}" if om else "") + ":"]
    for h, v in per.items():
        ses = _q(f"SELECT count(DISTINCT e.session_uid) {base} AND coalesce(emp.handle,'?')=%s",
                 tuple(args) + (h,))[0][0]
        out.append(f"  {h} ({ses} sesiuni): " + ", ".join(v))
    mcps = _q(f"SELECT e.entity_name, count(*) {base} AND e.entity_type='mcp' GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
              tuple(args))
    if mcps:
        out.append("Top MCP: " + ", ".join(f"{n} {c}" for n, c in mcps))
    ev = _q(f"""SELECT e.occurred_at, coalesce(emp.handle,'?'), e.entity_type, e.action, e.entity_name, e.summary {base}
                AND e.entity_type NOT IN ('mcp','db','session','skill_gap') ORDER BY e.occurred_at DESC LIMIT 40""",
            tuple(args))
    if ev:
        out.append("Ultimele evenimente notabile:")
        for ts, h, t, a, n, s in ev:
            out.append(f"  {_fmt(ts)} {h} {t}/{a} {n or ''}" + (f" — {s[:140]}" if s else ""))
    return "\n".join(out)


@mcp.tool()
def kb_resurse(cauta: str = "", categorie: str = "", limita: int = 40) -> str:
    """Resursele și lecțiile echipei din KB (tabelul resources: url, ip, doc, lesson, app, repo…). cauta = text
    căutat în etichetă, valoare, descriere, serviciu și taguri (gol = cele mai recente). categorie = filtru exact
    (ex. 'lesson'). Fără argumente întoarce și numărul pe categorii."""
    cond, args = ["true"], []
    if cauta.strip():
        cond.append("(label ILIKE %s OR value ILIKE %s OR description ILIKE %s OR service ILIKE %s "
                    "OR array_to_string(tags,' ') ILIKE %s)")
        args += [f"%{cauta.strip()}%"] * 5
    if categorie.strip():
        cond.append("category=%s"); args.append(categorie.strip())
    rows = _q(f"""SELECT category, label, value, service, description, coalesce(updated_at, created_at)
                  FROM resources WHERE {' AND '.join(cond)}
                  ORDER BY coalesce(updated_at, created_at) DESC NULLS LAST LIMIT %s""", tuple(args) + (limita,))
    out = []
    if not cauta.strip() and not categorie.strip():
        cat = _q("SELECT category, count(*) FROM resources GROUP BY 1 ORDER BY 2 DESC")
        out.append("Categorii: " + ", ".join(f"{c} {n}" for c, n in cat))
    out.append(f"{len(rows)} resurse" + (f" pentru „{cauta}”" if cauta.strip() else "") + ":")
    for c, l, v, s, d, ts in rows:
        out.append(f"- [{c}] {l}" + (f" ({s})" if s else "") + f" · {(v or '')[:160]}"
                   + (f" — {d[:220]}" if d else "") + (f" · {ts:%Y-%m-%d}" if ts else ""))
    return "\n".join(out)


if __name__ == "__main__":
    mcp.run()
