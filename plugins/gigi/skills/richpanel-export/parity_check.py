# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
parity_check.py — o SINGURA intrebare, zilnic: „am captat TOT ce a captat Richpanel?"

READ-ONLY. Nu scrie NIMIC in Richpanel (garda `cs_mirror.assert_read_only`) si nu scrie
niciun tichet in oglinda — un verificator care isi umple singur golurile trece mereu.
Atinge doar `parity_daily` + `sync_run` (jurnalul propriu).

Cele doua metrici, amandoua pe EFECT (nu pe cod):
  M1  pe fiecare ZI si fiecare CANAL: cate tichete are Richpanel vs cate are oglinda,
      potrivite pe ID (nu pe numar), cu lista ID-urilor LIPSA. Succes = 100%, lista goala.
      „99,x%" NU e succes.
  M3  tichete pe care Richpanel le marcheaza `first_responded_at` (deci CS a raspuns) dar
      pentru care oglinda NU are niciun mesaj de agent REAL cu text (botul canned
      „operator" nu conteaza). Trebuie sa fie 0 — astea sunt raspunsuri CS pierdute.

  uv run parity_check.py --days 7
  uv run parity_check.py --from 2026-08-29 --to 2026-08-30 --sample 10
  uv run parity_check.py --days 1 --today --json      # pentru cron/dashboard
  uv run parity_check.py --selftest                   # dovada logicii, fara API

Iesire: 0 = paritate dovedita | 1 = M1 < 100% sau M3 > 0 sau NEDOVEDIT | 2 = eroare de rulare.

Ce s-a MASURAT despre list_conversations (nu re-descoperi):
  - filtrul startDate/endDate e pe `created_at`, iar endDate e EXCLUSIV: o zi = [zi, zi+1).
  - `per_page` e plafonat de server la 50 (am cerut 100 si 200 -> tot 50).
  - `has_more` MINTE (a raportat False cu `returned`=50 si pagini ramase) -> singurul criteriu
    de oprire e `returned < per_page`.
  - sortarea implicita e updatedAt DESC -> paginarea unei zile e INSTABILA (un tichet atins de
    CS in timp ce paginam urca in fata si impinge altul peste granita de pagina = tichet SARIT,
    invizibil). Aici cerem explicit `sortKey=createdAt, order=asc`: created_at nu se mai
    schimba niciodata, deci feliile de pagina sunt fixe.
  - un `status` nesuportat e IGNORAT in tacere (raspunsul zice `warnings: Ignored unsupported
    status ...`) si interogarea devine NEFILTRATA -> citim `context_policy.warnings` si le
    raportam.
  - OPEN si CLOSED sunt singurele statusuri reale, dar un tichet INCHIS si REDESCHIS intre cele
    doua treceri ar cadea prin fisura; de asta trecerea implicita e reuniunea a trei:
    fara filtru + OPEN + CLOSED (raportam cat aduce UNIC fiecare, ca sa se vada daca merita).
"""
import argparse, datetime, json, os, sys, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # statiile CS/depozit = Windows cp1252

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import cs_mirror as cm                                             # noqa: E402

PER_PAGE = 50                    # plafon server (masurat)
PAGE_CAP = 50   # plafonul REAL al serverului (pagina 51 = eroare); mai sus = plasa moarta                    # plasa de siguranta: offsetul e plafonat pe la pagina ~50
DEFAULT_PASSES = ("any", "OPEN", "CLOSED")
UNKNOWN_CH = "(fara canal)"
_M3_UPD = "UPDATE parity_daily SET m3_no_agent=?, m3_sample=? WHERE day=? AND channel='all'"


# ────────────────────────────────────────────────────────────── zile & ajutoare
def day_list(days=None, dfrom=None, dto=None, include_today=False):
    """Implicit: ultimele N zile COMPLETE (pana ieri). Ziua curenta e inca in miscare —
    o comparam doar cu `--today`, si atunci o marcam ca atare in raport."""
    if dfrom or dto:
        a = datetime.date.fromisoformat(dfrom or dto)
        b = datetime.date.fromisoformat(dto or dfrom)
        if a > b:
            a, b = b, a
    else:
        b = datetime.date.today() - (datetime.timedelta(0) if include_today
                                     else datetime.timedelta(days=1))
        a = b - datetime.timedelta(days=max(1, int(days or 7)) - 1)
    out, d = [], a
    while d <= b:
        out.append(d.isoformat())
        d += datetime.timedelta(days=1)
    return out


def _chunks(seq, n=400):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ─────────────────────────────────────────────────── partea RICHPANEL (adevarul)
def fetch_rp_day(mcp, day, passes=DEFAULT_PASSES, page_cap=PAGE_CAP, log=None):
    """Toate tichetele CREATE in ziua `day`, ca {id: ticket}, plus diagnostic.

    Paginam pana cand `returned < per_page` (NU pe `has_more`, care minte) si reunim mai
    multe treceri de status, dedupland pe ID."""
    nxt = (datetime.date.fromisoformat(day) + datetime.timedelta(days=1)).isoformat()
    seen, diag = {}, {"passes": {}, "pages": 0, "calls": 0, "dups": 0, "capped": [],
                      "warnings": [], "out_of_day": 0, "unsorted": 0}
    for st in passes:
        before = set(seen)
        args = {"startDate": day, "endDate": nxt, "per_page": PER_PAGE,
                "sortKey": "createdAt", "order": "asc"}
        if st and st.lower() != "any":
            args["status"] = st
        page, npass = 1, 0
        while True:
            # call_raw: pastreaza raspunsul non-dict ca sa-l putem trata mai jos.
            # `call` ar arunca, iar `except Exception` de la main ar omori TOATE zilele.
            _call = getattr(mcp, "call_raw", mcp.call)
            d = _call("list_conversations", dict(args, page=page)) or {}
            diag["calls"] += 1
            diag["pages"] += 1
            # ⚠️ O EROARE DE LA SERVER NU E "0 TICHETE". Serverul intoarce un SIR
            # ('Error: Request failed with status code 400') de la pagina ~51 (plafonul de offset).
            # Tratat ca pagina goala, oprea paginarea si TRUNCHIA NUMITORUL => raporta fals 100%
            # cu tichete lipsa reale. Ziua devine NEDOVEDITA, nu "completa".
            if not isinstance(d, dict):
                diag["capped"].append(f"{day}/{st}: raspuns invalid la pagina {page}: {str(d)[:80]}")
                diag.setdefault("unproven", set()).add(day)
                break
            ts = d.get("tickets") or []
            pol = d.get("context_policy") if isinstance(d, dict) else None
            for w in ((pol or {}).get("warnings") or []):
                msg = f"{day}/{st}: {w}"
                if msg not in diag["warnings"]:
                    diag["warnings"].append(msg)
            prev_created = None
            for t in ts:
                tid = str(t.get("id") or "")
                if not tid:
                    continue
                npass += 1
                created = str(t.get("created_at") or "")
                if created[:10] and created[:10] != day:
                    diag["out_of_day"] += 1
                if prev_created and created < prev_created:
                    diag["unsorted"] += 1        # sortarea ceruta nu e respectata -> instabil
                prev_created = created
                if tid in seen:
                    diag["dups"] += 1
                    # pastram varianta mai bogata: un tichet re-vazut cu first_responded_at
                    # setat e mai adevarat decat unul fara (statusul se schimba intre treceri)
                    if not seen[tid].get("first_responded_at") and t.get("first_responded_at"):
                        seen[tid] = t
                else:
                    seen[tid] = t
            returned = d.get("returned", len(ts)) if isinstance(d, dict) else len(ts)
            if len(ts) < PER_PAGE and (returned or 0) < PER_PAGE:
                break
            page += 1
            if page > page_cap:
                diag["capped"].append(f"{day}/{st}")  # offset plafonat = NU putem dovedi ziua
                break
        diag["passes"][st] = {"vazute": npass, "unic_nou": len(set(seen) - before), "pagini": page}
        if log:
            log(f"    trecere {st:<7}{npass:6d} tichete, {len(set(seen) - before):5d} noi "
                f"({page} pagini)")
    return seen, diag


# ──────────────────────────────────────────────────── partea OGLINDA (a noastra)
def mirror_have(db, ids):
    """Care dintre ID-urile Richpanel EXISTA in oglinda (potrivire pe ID, nu pe numar)."""
    out = set()
    for part in _chunks(ids):
        q = "SELECT id FROM rp_ticket WHERE id IN (%s)" % ",".join("?" * len(part))
        out.update(r[0] for r in db.execute(q, [str(i) for i in part]))
    return out


def mirror_day_channels(db, day):
    """Ce crede OGLINDA ca are in ziua asta, pe canale (diagnostic: drift / tichete in plus)."""
    return {(r[0] or UNKNOWN_CH): r[1] for r in db.execute(
        "SELECT COALESCE(NULLIF(channel,''),?), COUNT(*) FROM rp_ticket"
        " WHERE substr(created_at,1,10)=? GROUP BY 1", (UNKNOWN_CH, day))}


def agent_reply_state(db, ids):
    """Pentru fiecare ID: 'ok' (are mesaj de agent REAL cu text), 'gol' (are rand de agent dar
    fara text — tot raspuns pierdut), 'lipsa' (niciun mesaj de agent). Botul canned
    „operator" NU e agent (45% din mesajele marcate agent sunt automatisme ale widgetului)."""
    state = {str(i): "lipsa" for i in ids}
    for part in _chunks(state):
        q = ("SELECT ticket_id,"
             " SUM(CASE WHEN is_agent=1 AND COALESCE(is_operator_bot,0)=0"
             "           AND LENGTH(COALESCE(text,''))>0 THEN 1 ELSE 0 END),"
             " SUM(CASE WHEN is_agent=1 AND COALESCE(is_operator_bot,0)=0 THEN 1 ELSE 0 END)"
             " FROM rp_message WHERE ticket_id IN (%s) GROUP BY ticket_id"
             ) % ",".join("?" * len(part))
        for tid, with_text, any_agent in db.execute(q, part):
            state[tid] = "ok" if with_text else ("gol" if any_agent else "lipsa")
    return state


def ensure_m3_columns(db):
    """Migrare idempotenta: parity_daily (definit in cs_mirror) capata doua coloane pentru M3.
    Coloane NULLABLE adaugate la final -> `INSERT OR REPLACE` cu lista explicita de coloane din
    cs_mirror.record_parity ramane valid."""
    cols = {r[1] for r in db.execute("PRAGMA table_info(parity_daily)")}
    if "m3_no_agent" not in cols:
        db.execute("ALTER TABLE parity_daily ADD COLUMN m3_no_agent INTEGER")
    if "m3_sample" not in cols:
        db.execute("ALTER TABLE parity_daily ADD COLUMN m3_sample TEXT")
    db.commit()


# ───────────────────────────────────────────────────────────────── verificarea
def check(db, mcp, days, sample=10, passes=DEFAULT_PASSES, log=None, today=None):
    """Intoarce raportul complet (dict). Nu printeaza nimic — printarea e separata."""
    today = today or datetime.date.today().isoformat()
    ensure_m3_columns(db)
    rep = {"days": [], "generated_at": cm.now_iso(), "passes": list(passes),
           "m3": {"total": 0, "lipsa_din_oglinda": 0, "fara_raspuns_captat": 0,
                  "agent_fara_text": 0, "candidati": 0, "sample": []},
           "unproven": [], "warnings": []}
    m3_cand = {}                       # id -> (zi, tichet)

    for day in days:
        if log:
            log(f"  {day}:")
        rp, diag = fetch_rp_day(mcp, day, passes, log=log)
        present = mirror_have(db, rp) if rp else set()
        mirror_ch = mirror_day_channels(db, day)

        by_ch = {}
        for tid, t in rp.items():
            by_ch.setdefault(t.get("channel") or UNKNOWN_CH, []).append(tid)
            if t.get("first_responded_at"):
                m3_cand[tid] = (day, t)

        chans = []
        for ch in sorted(set(by_ch) | set(mirror_ch)):
            ids = by_ch.get(ch, [])
            missing = sorted(i for i in ids if i not in present)
            chans.append({
                "channel": ch, "rp": len(ids), "mirror": mirror_ch.get(ch, 0),
                "matched": len(ids) - len(missing), "missing": len(missing),
                "missing_sample": missing[:sample],
                "coverage": (round(100.0 * (len(ids) - len(missing)) / len(ids), 2)
                             if ids else None)})

        n_rp = len(rp)
        n_missing = sum(c["missing"] for c in chans)
        d = {"day": day, "is_today": day >= today, "rp": n_rp,
             "mirror": sum(mirror_ch.values()), "matched": n_rp - n_missing,
             "missing": n_missing,
             "missing_sample": sorted(i for i in rp if i not in present)[:sample],
             "coverage": round(100.0 * (n_rp - n_missing) / n_rp, 2) if n_rp else None,
             "channels": chans, "diag": diag, "m3": 0}
        rep["days"].append(d)

        if diag["capped"]:
            rep["unproven"].append(f"{day}: paginare plafonata ({', '.join(diag['capped'])}) "
                                   f"— pot exista tichete NEVAZUTE")
        if diag["unsorted"]:
            rep["unproven"].append(f"{day}: {diag['unsorted']} randuri in afara ordinii cerute "
                                   f"(createdAt asc) — paginarea poate sari tichete")
        rep["warnings"].extend(diag["warnings"])
        if d["is_today"]:
            rep["warnings"].append(f"{day}: zi IN CURS — tichete noi pot aparea dupa aceasta "
                                   f"verificare")

    # M3: Richpanel zice ca s-a raspuns; are oglinda raspunsul agentului REAL?
    # Candidatii vin din datele PROASPETE de la Richpanel (nu din oglinda) — altfel, daca
    # oglinda n-ar salva deloc first_responded_at, M3 ar iesi 0 din nimic.
    for day in days:
        for r in db.execute("SELECT id, first_responded_at, conversation_no, channel"
                            " FROM rp_ticket WHERE substr(created_at,1,10)=?"
                            "   AND COALESCE(first_responded_at,'')<>''", (day,)):
            m3_cand.setdefault(r[0], (day, {"conversation_no": r[2], "channel": r[3],
                                            "first_responded_at": r[1]}))
    rep["m3"]["candidati"] = len(m3_cand)
    if m3_cand:
        state = agent_reply_state(db, m3_cand)
        in_mirror = mirror_have(db, m3_cand)
        per_day = {}
        for tid, (day, t) in sorted(m3_cand.items(), key=lambda kv: (kv[1][0], kv[0])):
            if state.get(tid, "lipsa") == "ok":
                continue
            if tid not in in_mirror:
                why, key = "lipseste complet din oglinda", "lipsa_din_oglinda"
            elif state.get(tid) == "gol":
                why, key = "mesaj de agent FARA text", "agent_fara_text"
            else:
                why, key = "tichet captat, raspunsul NU", "fara_raspuns_captat"
            rep["m3"]["total"] += 1
            rep["m3"][key] += 1
            per_day[day] = per_day.get(day, 0) + 1
            if len(rep["m3"]["sample"]) < sample:
                rep["m3"]["sample"].append({
                    "id": tid, "day": day, "conversation_no": t.get("conversation_no"),
                    "channel": t.get("channel"), "first_responded_at": t.get("first_responded_at"),
                    "de_ce": why})
        for d in rep["days"]:
            d["m3"] = per_day.get(d["day"], 0)

    # verdicte
    covs = [c["coverage"] for d in rep["days"] for c in d["channels"] if c["coverage"] is not None]
    rep["m1"] = min(covs) if covs else None
    # ⚠️ "zero adevar" NU e "trecut". Trei capcane reparate aici:
    #  a) all([]) e True — o zi fara niciun canal trecea automat;
    #  b) Richpanel intoarce 0 tichete (API mort) dar oglinda are 579 pe aceeasi zi => raportul
    #     isi tiparea singur contradictia si tot scria "PARITATE DOVEDITA", EXIT=0. Un cron care
    #     se uita la codul de iesire vedea VERDE fix in ziua in care API-ul era mort;
    #  c) o zi marcata NEDOVEDITA (paginare trunchiata) nu poate fi declarata OK.
    unproven = rep.setdefault("unproven", [])
    for d in rep["days"]:
        rp_tot = sum((c.get("rp") or 0) for c in d["channels"])
        mir_tot = sum((c.get("mirror") or 0) for c in d["channels"])
        if not d["channels"]:
            unproven.append(f'{d["day"]}: niciun canal returnat')
        elif rp_tot == 0 and mir_tot > 0:
            unproven.append(f'{d["day"]}: Richpanel a raportat 0 tichete dar oglinda are {mir_tot}')
        elif rp_tot == 0:
            unproven.append(f'{d["day"]}: Richpanel a raportat 0 tichete (API mort?)')
    all_ch = [c for d in rep["days"] for c in d["channels"]]
    rep["m1_ok"] = bool(all_ch) and not unproven and all(c["missing"] == 0 for c in all_ch)
    rep["m3_ok"] = rep["m3"]["total"] == 0
    rep["proven"] = not rep["unproven"]
    rep["ok"] = rep["m1_ok"] and rep["m3_ok"] and rep["proven"]

    # persista (parity_daily): un rand per canal + un rand agregat 'all' cu M3-ul zilei
    for d in rep["days"]:
        for c in d["channels"]:
            cm.record_parity(db, d["day"], c["channel"], c["rp"], c["mirror"], c["matched"],
                             c["missing_sample"])
        cm.record_parity(db, d["day"], "all", d["rp"], d["mirror"], d["matched"],
                         d["missing_sample"])
        db.execute(_M3_UPD, (d["m3"], json.dumps([s["id"] for s in rep["m3"]["sample"]
                                                  if s["day"] == d["day"]],
                                                 ensure_ascii=False), d["day"]))
    db.commit()
    return rep


# ───────────────────────────────────────────────────────────────────── raport
def print_report(rep, sample=10):
    W = 82
    print("\n" + "=" * W)
    print('PARITATE OGLINDA CS  vs  RICHPANEL   —   "am captat TOT ce a captat Richpanel?"')
    print("=" * W)
    for d in rep["days"]:
        head = (f"\n{d['day']}   Richpanel {d['rp']:>5}   oglinda {d['mirror']:>5}   "
                f"potrivite {d['matched']:>5}   lipsa {d['missing']:>4}")
        if d["coverage"] is not None:
            head += f"   {d['coverage']:6.2f}%"
        print(head + ("   [ZI IN CURS]" if d["is_today"] else ""))
        print(f"  {'canal':<24}{'RP':>6}{'oglinda':>9}{'potriv':>8}{'lipsa':>7}{'acoperire':>11}")
        print("  " + "-" * (W - 4))
        for c in d["channels"]:
            cov = "        —" if c["coverage"] is None else f"{c['coverage']:9.2f}%"
            mark = "" if c["missing"] == 0 else "  <-- LIPSA"
            if c["rp"] == 0:
                mark = "  (doar in oglinda)"
            print(f"  {c['channel']:<24}{c['rp']:>6}{c['mirror']:>9}{c['matched']:>8}"
                  f"{c['missing']:>7}{cov}{mark}")
            for i in c["missing_sample"]:
                print(f"       lipsa: {i}")
            if c["missing"] > len(c["missing_sample"]):
                print(f"       ... si inca {c['missing'] - len(c['missing_sample'])}")
        p = d["diag"]["passes"]
        print("  treceri: " + " | ".join(f"{k}={v['vazute']} (+{v['unic_nou']} unic)"
                                         for k, v in p.items())
              + f"   {d['diag']['calls']} apeluri API, {d['diag']['dups']} dubluri")

    m3 = rep["m3"]
    print("\n" + "-" * W)
    print("M1  acoperire pe ID, pe zi SI pe canal")
    print(f"    minim pe canal: {('%.2f%%' % rep['m1']) if rep['m1'] is not None else '—'}"
          f"    total ID-uri lipsa: {sum(d['missing'] for d in rep['days'])}")
    print("    " + ("TRECE (100%, zero ID-uri lipsa)" if rep["m1_ok"]
                    else "PICA — 99,x% NU e succes"))
    print("\nM3  Richpanel zice ca s-a raspuns, oglinda NU are raspunsul agentului")
    print(f"    candidati (first_responded_at non-null): {m3['candidati']}")
    print(f"    pierdute: {m3['total']}   (lipsesc complet: {m3['lipsa_din_oglinda']} | "
          f"tichet captat fara raspuns: {m3['fara_raspuns_captat']} | "
          f"agent fara text: {m3['agent_fara_text']})")
    for s in m3["sample"][:sample]:
        print(f"       {s['day']}  #{s['conversation_no']}  {str(s['channel'] or ''):<22}"
              f" raspuns {str(s['first_responded_at'])[:19]}  — {s['de_ce']}")
        print(f"          id: {s['id']}")
    print("    " + ("TRECE (0 raspunsuri pierdute)" if rep["m3_ok"] else "PICA"))
    if m3["candidati"] == 0 and any(d["rp"] for d in rep["days"]):
        print("    ATENTIE: 0 candidati — M3 e NEEVALUABIL (Richpanel n-a dat first_responded_at)")

    for w in rep["warnings"]:
        print(f"\n  avertisment: {w}")
    for u in rep["unproven"]:
        print(f"\n  NEDOVEDIT: {u}")
    print("\n" + "=" * W)
    print("VERDICT: " + ("PARITATE DOVEDITA (M1=100%, M3=0)" if rep["ok"]
                         else "PICA — oglinda NU are tot ce are Richpanel"))
    print("=" * W)


# ──────────────────────────────────────────────────────────────────── selftest
class FakeMCP:
    """Richpanel simulat: felie de zi pe created_at cu endDate EXCLUSIV, paginare de 50 si
    `has_more` care MINTE (mereu False). Ne lasa sa dovedim logica fara sa consumam rata
    partajata cu CS-ul live."""

    def __init__(self, tickets):
        self.tickets = list(tickets)
        self.calls = 0

    def call(self, tool, args):
        assert tool == "list_conversations", tool
        self.calls += 1
        s, e = args["startDate"], args["endDate"]
        rows = [t for t in self.tickets if s <= str(t.get("created_at"))[:10] < e]
        st = args.get("status")
        if st:
            rows = [t for t in rows if (t.get("status") or "").upper() == st.upper()]
        rows.sort(key=lambda t: t.get("created_at") or "")
        page, pp = int(args.get("page", 1)), int(args.get("per_page", PER_PAGE))
        chunk = rows[(page - 1) * pp: page * pp]
        return {"tickets": chunk, "page": page, "per_page": pp, "returned": len(chunk),
                "has_more": False}                     # <- minciuna documentata


def _chk(R, name, cond, detail=""):
    R.append((bool(cond), name, detail))
    print(f"  {'OK  ' if cond else 'PICA'} {name}" + (f"  — {detail}" if detail else ""))
    return bool(cond)


def selftest():
    import tempfile
    R = []
    day, day2 = "2026-08-29", "2026-08-30"
    path = os.path.join(tempfile.mkdtemp(prefix="parity_"), "mirror.db")
    db = cm.open_db(path)

    def mk(i, ch, status, fr=None, d=day):
        return {"id": f"t{i}", "conversation_no": 1000 + i, "channel": ch, "status": status,
                "created_at": f"{d}T{i // 60:02d}:{i % 60:02d}:00.000Z",
                "updated_at": f"{d}T23:00:00.000Z", "first_responded_at": fr,
                "subject": f"subiect {i}", "to": {"id": "775068272350568"}}

    # 120 tichete intr-o zi => 3 pagini (dovedeste ca nu ne oprim pe `has_more`)
    tickets = [mk(i, ("email" if i % 3 == 0 else "facebook_feed_comment"),
                  ("OPEN" if i % 10 == 0 else "CLOSED"),
                  f"{day}T12:00:00.000Z" if i % 4 == 0 else None) for i in range(120)]
    vecin = mk(999, "email", "CLOSED", d=day2)          # tichet in ziua URMATOARE
    fake = FakeMCP(tickets + [vecin])

    print("\n1) paginare, granita de zi si `has_more` mincinos")
    rp, diag = fetch_rp_day(fake, day, DEFAULT_PASSES)
    _chk(R, "toate cele 120 de tichete sunt vazute (3 pagini, nu 1)", len(rp) == 120,
         f"{len(rp)} tichete, {diag['pages']} pagini, {diag['calls']} apeluri")
    _chk(R, "endDate EXCLUSIV: tichetul din ziua urmatoare NU intra", "t999" not in rp)
    _chk(R, "ziua urmatoare il vede pe el si numai pe el",
         set(fetch_rp_day(fake, day2, ("any",))[0]) == {"t999"})
    _chk(R, "trecerea fara filtru le aduce pe toate; OPEN/CLOSED nu adauga unic",
         diag["passes"]["any"]["unic_nou"] == 120 and diag["passes"]["OPEN"]["unic_nou"] == 0
         and diag["passes"]["CLOSED"]["unic_nou"] == 0,
         str({k: v["unic_nou"] for k, v in diag["passes"].items()}))

    print("\n2) M1 — oglinda INCOMPLETA (118 din 120)")
    for t in tickets[:118]:
        cm.upsert_ticket(db, t)
    db.commit()
    rep = check(db, fake, [day], sample=10, today="2026-08-31")
    _chk(R, "lipsa e detectata pe ID, nu pe numar",
         rep["days"][0]["missing"] == 2
         and set(rep["days"][0]["missing_sample"]) == {"t118", "t119"},
         str(rep["days"][0]["missing_sample"]))
    _chk(R, "99,x% NU trece", rep["m1_ok"] is False and rep["days"][0]["coverage"] < 100,
         f"{rep['days'][0]['coverage']}%")
    _chk(R, "acoperirea e pe CANAL, nu doar pe zi",
         len(rep["days"][0]["channels"]) == 2
         and all(c["coverage"] is not None for c in rep["days"][0]["channels"]),
         ", ".join(f"{c['channel']}={c['coverage']}%" for c in rep["days"][0]["channels"]))
    row = db.execute("SELECT * FROM parity_daily WHERE day=? AND channel='all'", (day,)).fetchone()
    _chk(R, "rezultatul e scris in parity_daily",
         row and row["rp_count"] == 120 and row["matched"] == 118
         and json.loads(row["missing_sample"]) == ["t118", "t119"],
         f"rp={row['rp_count']} matched={row['matched']} cov={row['coverage_pct']}%")
    _chk(R, "exista si randuri per canal in parity_daily",
         db.execute("SELECT COUNT(*) FROM parity_daily WHERE day=? AND channel<>'all'",
                    (day,)).fetchone()[0] == 2)

    print("\n3) M3 — raspuns CS pierdut")
    for t in tickets:                                   # oglinda are ACUM toate tichetele...
        cm.upsert_ticket(db, t)
    db.commit()
    n_fr = sum(1 for t in tickets if t["first_responded_at"])
    rep = check(db, fake, [day], sample=5, today="2026-08-31")
    _chk(R, "M1 devine 100% dupa completare", rep["m1_ok"] and rep["m1"] == 100.0, f"{rep['m1']}%")
    _chk(R, "M3 prinde tichetele cu raspuns dar fara niciun mesaj de agent",
         rep["m3"]["total"] == n_fr and rep["m3"]["candidati"] == n_fr,
         f"{rep['m3']['total']} din {n_fr} candidati")
    _chk(R, "M3 > 0 => verdict PICA chiar daca M1 = 100%", rep["ok"] is False and rep["m1_ok"])

    for t in tickets:                                   # botul canned NU e raspuns CS
        if t["first_responded_at"]:
            cm.upsert_message(db, t["id"], {"id": "b1", "text": "Sunteti deja client?",
                                            "author_is_workspace_agent": True,
                                            "author": {"id": "operator", "name": "Operator"}},
                              idx=0)
    db.commit()
    rep = check(db, fake, [day], sample=5, today="2026-08-31")
    _chk(R, "botul 'operator' NU trece drept raspuns de agent", rep["m3"]["total"] == n_fr,
         f"M3={rep['m3']['total']}")

    t0 = next(t for t in tickets if t["first_responded_at"])
    cm.upsert_message(db, t0["id"], {"id": "a0", "text": "", "author_is_workspace_agent": True,
                                     "author": {"id": "usr_7", "name": "Raluca"}}, idx=1)
    db.commit()
    rep = check(db, fake, [day], sample=5, today="2026-08-31")
    _chk(R, "agent real dar FARA text = tot pierdut (numarat separat)",
         rep["m3"]["total"] == n_fr and rep["m3"]["agent_fara_text"] == 1,
         f"M3={rep['m3']['total']}, din care agent_fara_text={rep['m3']['agent_fara_text']}")

    for t in tickets:
        if t["first_responded_at"]:
            cm.upsert_message(db, t["id"], {"id": "a1", "text": "Buna ziua, verific si revin.",
                                            "author_is_workspace_agent": True,
                                            "author": {"id": "usr_7", "name": "Raluca"}}, idx=2)
    db.commit()
    rep = check(db, fake, [day], sample=5, today="2026-08-31")
    _chk(R, "cu raspunsul agentului captat, M3 = 0 si verdictul TRECE",
         rep["m3"]["total"] == 0 and rep["ok"] and rep["m1"] == 100.0)
    row = db.execute("SELECT m3_no_agent FROM parity_daily WHERE day=? AND channel='all'",
                     (day,)).fetchone()
    _chk(R, "M3 e persistat in parity_daily (coloana adaugata idempotent)",
         row["m3_no_agent"] == 0, str(row["m3_no_agent"]))

    print("\n4) cazuri-limita")
    gol = FakeMCP([])
    rep0 = check(db, gol, [day2], sample=5, today="2026-08-31")
    _chk(R, "o zi fara tichete in Richpanel nu da alarma falsa",
         rep0["ok"] and rep0["days"][0]["rp"] == 0 and rep0["days"][0]["coverage"] is None)
    cm.upsert_ticket(db, {"id": "extra1", "channel": "aircall", "status": "CLOSED",
                          "created_at": f"{day2}T10:00:00.000Z"})
    db.commit()
    rep0 = check(db, gol, [day2], sample=5, today="2026-08-31")
    _chk(R, "tichet DOAR in oglinda = raportat, dar nu strica paritatea",
         rep0["ok"] and any(c["channel"] == "aircall" and c["rp"] == 0 and c["mirror"] == 1
                            for c in rep0["days"][0]["channels"]))
    days7 = day_list(days=7)
    _chk(R, "--days 7 = ultimele 7 zile COMPLETE (ziua curenta e inca in miscare)",
         len(days7) == 7 and days7[-1] == (datetime.date.today()
                                           - datetime.timedelta(days=1)).isoformat(),
         f"{days7[0]} ... {days7[-1]}")
    _chk(R, "--today include ziua curenta",
         day_list(days=1, include_today=True)[-1] == datetime.date.today().isoformat())
    many = FakeMCP([mk(i, "email", "CLOSED", d=day2) for i in range(300)])
    # Regresie: serverul intoarce un SIR de la pagina ~51 (plafonul de offset). Trebuie
    # sa marcheze ziua NEDOVEDITA si sa mearga mai departe — NU sa arunce. O exceptie
    # aici urca pana la `except Exception` din main si omoara TOATE zilele, fara raport.
    class SirLaPagina2(FakeMCP):
        """Imita CONTRACTUL lui cm.MirrorMCP: `call_raw` da raspunsul brut, `call`
        arunca pe non-dict. Daca fetch_rp_day foloseste `call`, testul pica — exact
        regresia pe care o pazim."""

        def call_raw(self, tool, args):
            if int(args.get("page", 1)) >= 2:
                return "Error: Request failed with status code 400"
            return FakeMCP.call(self, tool, args)

        def call(self, tool, args):
            out = self.call_raw(tool, args)
            if out is not None and not isinstance(out, dict):
                raise cm.MCPError(tool, out)
            return out

    sir = SirLaPagina2([mk(i, "email", "CLOSED", d=day2) for i in range(120)])
    try:
        rp_s, diag_s = fetch_rp_day(sir, day2, ("any",))
        arunca = False
    except Exception as exc:
        rp_s, diag_s, arunca = {}, {}, exc
    _chk(R, "sir de la server = zi NEDOVEDITA, nu exceptie care omoara raportul",
         arunca is False and any("raspuns invalid" in c for c in diag_s.get("capped", [])),
         f"{type(arunca).__name__}: {arunca}" if arunca else
         "; ".join(diag_s.get("capped", []))[:80])
    _chk(R, "paginile de dinainte de eroare NU se pierd",
         arunca is False and len(rp_s) == 50, f"{len(rp_s)} tichete pastrate")

    rp_c, diag_c = fetch_rp_day(many, day2, ("any",), page_cap=2)
    _chk(R, "plafonul de paginare e RAPORTAT (nu tacut)",
         diag_c["capped"] == [f"{day2}/any"] and len(rp_c) == 100, str(diag_c["capped"]))
    rep_c = check(db, many, [day2], sample=3, today="2026-08-31")
    _chk(R, "o zi nedovedita nu poate fi declarata OK",
         rep_c["ok"] is False, f"missing={rep_c['days'][0]['missing']}")

    print("\n5) garda READ-ONLY se aplica si aici")
    blocked = []
    for w in ("create_draft", "add_private_note", "update_conversation_status"):
        try:
            cm.assert_read_only(w)
        except PermissionError:
            blocked.append(w)
    _chk(R, "uneltele de scriere raman interzise", len(blocked) == 3, ", ".join(blocked))
    n_before = db.execute("SELECT COUNT(*) FROM rp_ticket").fetchone()[0]
    check(db, fake, [day], sample=3, today="2026-08-31")
    _chk(R, "verificatorul NU adauga tichete in oglinda (nu-si umple singur golurile)",
         db.execute("SELECT COUNT(*) FROM rp_ticket").fetchone()[0] == n_before,
         f"{n_before} inainte si dupa")

    db.close()
    ok = sum(1 for c, _, _ in R if c)
    print("\n" + "=" * 72)
    print(f"{ok}/{len(R)} verificari trecute - baza de test: {path}")
    for c, n, d in R:
        if not c:
            print(f"  PICAT: {n} — {d}")
    return 0 if ok == len(R) else 1


# ───────────────────────────────────────────────────────────────────────── main
def main():
    ap = argparse.ArgumentParser(
        description='Paritate oglinda CS vs Richpanel — "am captat TOT?" (READ-ONLY)')
    ap.add_argument("--days", type=int, default=7, help="cate zile complete inapoi (implicit 7)")
    ap.add_argument("--from", dest="dfrom", help="prima zi (YYYY-MM-DD)")
    ap.add_argument("--to", dest="dto", help="ultima zi (YYYY-MM-DD, INCLUSIV)")
    ap.add_argument("--today", action="store_true", help="include si ziua curenta (in miscare)")
    ap.add_argument("--sample", type=int, default=10, help="cate ID-uri lipsa afisam (implicit 10)")
    ap.add_argument("--passes", default=",".join(DEFAULT_PASSES),
                    help="treceri de status reunite (implicit any,OPEN,CLOSED)")
    ap.add_argument("--rpm", type=int, help="plafon cereri/minut (implicit 30 ziua, 50 noaptea)")
    ap.add_argument("--db", default=cm.DB_DEFAULT)
    ap.add_argument("--json", action="store_true", help="raportul ca JSON (cron/dashboard)")
    ap.add_argument("--quiet", action="store_true", help="fara progres in timpul tragerii")
    ap.add_argument("--selftest", action="store_true", help="dovada logicii, fara API")
    a = ap.parse_args()

    if a.selftest:
        sys.exit(selftest())

    days = day_list(a.days, a.dfrom, a.dto, a.today)
    passes = tuple(p.strip() for p in a.passes.split(",") if p.strip())
    db = cm.open_db(a.db)
    log = None if (a.quiet or a.json) else (lambda s: print(s, flush=True))
    t0 = time.time()
    if log:
        log(f"Richpanel -> {days[0]} ... {days[-1]} ({len(days)} zile) | treceri: "
            f"{','.join(passes)}\noglinda: {a.db}")
    try:
        mcp = cm.MirrorMCP(cm.RateLimiter(rpm=a.rpm))
        with cm.sync_run(db, "parity") as run:
            rep = check(db, mcp, days, sample=a.sample, passes=passes, log=log)
            run.items = sum(d["rp"] for d in rep["days"])
            run.errors = sum(d["missing"] for d in rep["days"]) + rep["m3"]["total"]
            run.note = (f"{days[0]}..{days[-1]} M1={rep['m1']} "
                        f"lipsa={sum(d['missing'] for d in rep['days'])} "
                        f"M3={rep['m3']['total']} ok={int(rep['ok'])}")
    except Exception as e:                        # o eroare de rulare NU e „paritate ok"
        print(f"EROARE: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)
    rep["elapsed_s"] = round(time.time() - t0, 1)
    rep["api"] = mcp.limiter.stats()

    if a.json:
        print(json.dumps(rep, ensure_ascii=False, default=str))
    else:
        print_report(rep, a.sample)
        print(f"\n{rep['elapsed_s']}s | {rep['api']['calls']} apeluri API | ritm "
              f"{rep['api']['rpm_target']}/min | ramase in fereastra: {rep['api']['remaining']}"
              f" | 429: {rep['api']['http_429']}")
    db.close()
    sys.exit(0 if rep["ok"] else 1)


if __name__ == "__main__":
    main()
