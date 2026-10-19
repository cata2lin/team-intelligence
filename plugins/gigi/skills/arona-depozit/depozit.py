# /// script
# requires-python = ">=3.10"
# dependencies = ["paramiko>=3.4"]
# ///
"""arona-depozit — operațiile de depozit, ca funcții care întorc TEXT (folosite de mcp_server.py și din CLI).

Surse (niciuna nu e AWBprint):
  * Order Hub (orderhub.arona.ro) — coada de print pe stații, istoricul printurilor, resetul unui print.
    Autentificare: accesul local OH (`/standalone/login`, parola comună ORDERHUB_LOCAL_PASSWORD din KB).
  * Aplicația de scanare AWB Arona (scripts.arona.ro, api/scan.py + routere) — rafturi, culegere, loturi,
    „NU GĂSESC", utilizatorii scanerului. Autentificare: contul de serviciu awb-svc (SCAN_SVC_USER/PASSWORD din KB).
  * Bazele scanerului citite DIRECT, read-only (scripts/depozit_sql.py, `mode=ro`, prin ssh) — doar ce nu are
    endpoint: etichete printate și neculese, anomalii de scanare.

Reguli: citirile sunt libere; orice scriere are PROBĂ implicită și execută doar cu apply=True. Secretele și
tokenurile nu se printează niciodată și stau doar în memoria procesului.
"""
import json
import os
import shlex
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
CORE = os.path.normpath(os.path.join(HERE, "..", "..", "..", "core", "scripts"))
if CORE not in sys.path:
    sys.path.insert(0, CORE)

OH_BASE = os.environ.get("OH_BASE", "https://orderhub.arona.ro")
# magazinul cu care se deschide sesiunea locală OH; coada se cere cu scope=all (toată organizația)
OH_SHOP = os.environ.get("OH_DEPOZIT_SHOP", "6f9e22-9d.myshopify.com")
SCAN_BASE = os.environ.get("SCAN_BASE", "https://scripts.arona.ro")
SQL = os.path.join(HERE, "scripts", "depozit_sql.py")
STATII_SCANER = ("Bartolomeu", "Uzina 2", "Parfumuri", "Teliu")
CAPABILITATI = ("stock", "locations")
MAX_OUT = 60000

_TOK = {}       # sursa -> (token, expiră_la)


class Eroare(Exception):
    pass


# ───────────────────────── autentificare + HTTP ─────────────────────────
def _secret(k):
    v = os.environ.get(k)
    if v:
        return v.strip()
    import arona_pg  # noqa: WPS433 — KB, nu printează valoarea
    return arona_pg.secret(k)


def _http(url, metoda="GET", corp=None, token=None, timeout=120):
    h = {"Content-Type": "application/json"}
    if token:
        h["Authorization"] = "Bearer " + token
    data = json.dumps(corp).encode() if corp is not None else None
    req = urllib.request.Request(url, data=data, headers=h, method=metoda)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            txt = r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        txt = e.read().decode("utf-8", "replace")
        try:
            det = json.loads(txt).get("detail", txt)
        except Exception:
            det = txt
        raise Eroare("HTTP %s: %s" % (e.code, str(det)[:500]))
    except urllib.error.URLError as e:
        raise Eroare("rețea: %s" % e.reason)
    return json.loads(txt) if txt.strip() else {}


def _token(sursa):
    t = _TOK.get(sursa)
    if t and t[1] > time.time() + 60:
        return t[0]
    if sursa == "oh":
        pw = _secret("ORDERHUB_LOCAL_PASSWORD")
        if not pw:
            raise Eroare("lipsește ORDERHUB_LOCAL_PASSWORD în KB")
        r = _http(OH_BASE + "/standalone/login", "POST", {"parola": pw, "shop": OH_SHOP}, timeout=30)
        tok, dur = r.get("token"), int(r.get("expira_in") or 3600)
    else:
        u, pw = _secret("SCAN_SVC_USER"), _secret("SCAN_SVC_PASSWORD")
        if not (u and pw):
            raise Eroare("lipsesc SCAN_SVC_USER / SCAN_SVC_PASSWORD în KB")
        r = _http(SCAN_BASE + "/api/auth/login", "POST", {"username": u, "password": pw}, timeout=30)
        tok, dur = r.get("access_token"), int(r.get("expires_in") or 3600)
    if not tok:
        raise Eroare("logarea %s n-a întors token" % sursa)
    _TOK[sursa] = (tok, time.time() + dur)
    return tok


def oh(cale, metoda="GET", corp=None, **q):
    q = {k: v for k, v in q.items() if v not in (None, "")}
    url = OH_BASE + cale + (("?" + urllib.parse.urlencode(q)) if q else "")
    return _http(url, metoda, corp, _token("oh"), timeout=180)


def scan(cale, metoda="GET", corp=None, **q):
    q = {k: v for k, v in q.items() if v not in (None, "")}
    url = SCAN_BASE + cale + (("?" + urllib.parse.urlencode(q)) if q else "")
    return _http(url, metoda, corp, _token("scan"), timeout=180)


def _sql(argv, timeout=300):
    """depozit_sql.py pe serverul de scripturi, trimis pe stdin (fără copiere). Bazele se deschid mode=ro."""
    import arona_ssh
    for k in ("PROFIT_SSH_HOST", "PROFIT_SSH_USER"):
        if not os.environ.get(k):
            os.environ[k] = arona_ssh._secret(k) or (arona_ssh.DEFAULT_HOST if k.endswith("HOST") else arona_ssh.DEFAULT_USER)
    cl = arona_ssh.connect()
    try:
        cmd = "cd /root/Scripturi && .venv/bin/python - " + " ".join(shlex.quote(str(a)) for a in argv)
        i, o, e = cl.exec_command(cmd, timeout=timeout)
        with open(SQL, "rb") as f:
            i.write(f.read())
        i.channel.shutdown_write()
        out, err = o.read().decode("utf-8", "replace"), e.read().decode("utf-8", "replace")
        rc = o.channel.recv_exit_status()
    finally:
        cl.close()
    if rc:
        out += "\n[exit %s] %s" % (rc, err.strip()[-600:])
    return out.strip()


def _ora(ts):
    return (ts or "")[:16].replace("T", " ")


def _taie(s):
    return s if len(s) <= MAX_OUT else s[:MAX_OUT] + "\n… (trunchiat)"


# ───────────────────────── PRINT (Order Hub) ─────────────────────────
def print_coada(statie="", zile=30, facute="", detaliu=False):
    d = oh("/api/print/statii", scope="all", zile=int(zile), facute=facute)
    st = d.get("statii") or []
    if statie:
        st = [s for s in st if statie.lower() in (s.get("statie") or "").lower()]
    L = ["Coada de print Order Hub (sursa de adevăr, ce vede omul în Printing) — fereastră %s zile%s: %s etichete / %s "
         "colete%s." % (d.get("zile"), (", doar " + facute) if facute else "", d.get("total"), d.get("colete"),
                        " ⚠ PLAFONAT (cifra e un minim)" if d.get("plafonat") else "")]
    for s in st:
        L.append("\n== %s: %d etichete · %d colete" % (s.get("statie"), s.get("etichete", 0), s.get("colete", 0)))
        L.append("   magazine: " + " · ".join("%s %d" % (m.get("magazin"), m.get("etichete", 0))
                                             for m in (s.get("magazine") or [])))
        for c in (s.get("categorii") or []):
            gr = c.get("grupe") or []
            extra = ("  [" + ", ".join("%s %d" % (g.get("grup"), g.get("etichete", 0)) for g in gr[:6 if detaliu else 3])
                     + "]") if gr else ""
            L.append("   %-14s %5d%s" % (c.get("categorie"), c.get("etichete", 0), extra))
    if d.get("swap_pe_grup"):
        L.append("\nSwap-uri CS scoase din coadă (canalul lor e grupul): %s" % d.get("swap_pe_grup"))
    if d.get("neplecate_doar_predare"):
        L.append("Neplecate doar de predat (printate, nepreluate): %s" % d.get("neplecate_doar_predare"))
    L.append("\nPrintul se face din Order Hub → Printing (https://orderhub.arona.ro/app/printing). Aici doar numărăm.")
    return "\n".join(L)


def print_istoric(statie="", cauta="", limit=15):
    d = oh("/api/print/logs", limit=int(limit), statie=statie, q=cauta)
    L = ["Istoricul printurilor Order Hub%s%s (cel mai nou primul). Stații cu printuri în 7 zile: %s" % (
        (" · stația " + statie) if statie else "", (" · caută " + cauta) if cauta else "",
        ", ".join("%s %s" % (s.get("statie"), s.get("printuri")) for s in d.get("statii") or []))]
    for z, v in sorted((d.get("zile") or {}).items(), reverse=True):
        L.append("Ziua %s: %s printuri, %s comenzi, %s colete" % (z, v.get("printuri"), v.get("comenzi"), v.get("colete")))
    for lg in d.get("logs") or []:
        lot = ", ".join(x.get("cod", "") + (" (anulat)" if x.get("anulat") else "") for x in lg.get("loturi") or [])
        L.append("  #%-5s %s  %-11s %-34s %4s etichete%s%s" % (
            lg.get("id"), _ora(lg.get("created_at")), lg.get("statie") or "", (lg.get("ce") or lg.get("name") or "")[:34],
            lg.get("awb_count"), ("  loturi: " + lot) if lot else "",
            ("  RESETĂRI: %d" % len(lg["resetari"])) if lg.get("resetari") else ""))
    if not d.get("logs"):
        L.append("  (niciun print)")
    L.append("Ora e UTC (ora RO = +3 vara / +2 iarna). Lotul „WhatsApp <stație>” = trimiterea de dimineață a OH pe grup.")
    return "\n".join(L)


def print_reset(print_id=0, awb="", motiv="", parola="", apply=False):
    """Pune înapoi în coadă un print întreg (print_id) sau o etichetă (awb), prin rutele OH (parolă + motiv + audit)."""
    if bool(print_id) == bool(awb):
        raise Eroare("dă EXACT unul: print_id (tot printul) sau awb (o etichetă)")
    if awb:
        logs = oh("/api/print/logs", limit=5, q=awb.strip()).get("logs") or []
        if not logs:
            return "AWB-ul %s n-a intrat în niciun print din Order Hub — nu am ce reseta (verifică cu oh_comanda)." % awb
        print_id = logs[0]["id"]
    det = oh("/api/print/logs/%d" % int(print_id))
    com = det.get("comenzi") or []
    tinta = [c for c in com if not awb or c.get("awb") == awb.strip()]
    printate = [c for c in tinta if c.get("printata")]
    L = ["%s RESET PRINT — printul #%s „%s” din %s (UTC): %d etichete, ținta %d, din care încă PRINTATE %d." % (
        "EXECUȚIE" if apply else "PROBĂ (nu scrie nimic)", det.get("id"), det.get("name"), _ora(det.get("created_at")),
        len(com), len(tinta), len(printate))]
    for c in printate[:40]:
        L.append("  %s  %-12s %-18s colete %s%s" % (c.get("awb"), c.get("comanda"), c.get("magazin"), c.get("colete"),
                                                   ("  lot " + c["lot"]) if c.get("lot") else ""))
    if len(printate) > 40:
        L.append("  … încă %d" % (len(printate) - 40))
    if det.get("loturi"):
        L.append("Loturi pe scaner: " + ", ".join(str(x.get("cod") or x) for x in det["loturi"]) +
                 (" — se anulează și se șterg de pe scaner." if not awb else " — pierd eticheta asta."))
    if not printate:
        L.append("Nimic de resetat: etichetele sunt deja înapoi în coadă.")
        return "\n".join(L)
    if not apply:
        L.append("\nEfect la apply=true: etichetele redevin NEPRINTATE și reapar în coada stației lor din Printing; OH scrie "
                 "auditul (motiv, autorizat_cu). Cere omului MOTIVUL și PAROLA DE RESET PRINT (o știe administratorul; "
                 "nu e în KB). ⚠ Fă-l doar dacă etichetele chiar NU au ajuns pe hârtie — altfel se printează de două ori.")
        return "\n".join(L)
    if not (motiv or "").strip() or not (parola or "").strip():
        raise Eroare("execuția cere motiv și parola de reset print (de la om)")
    corp = {"parola": parola, "motiv": motiv.strip()}
    if awb:
        corp["awb"] = awb.strip()
        r = oh("/api/print/awb/neprintat", "POST", corp)
    else:
        r = oh("/api/print/logs/%d/reset" % int(print_id), "POST", corp)
    dupa = oh("/api/print/logs/%d" % int(print_id)).get("comenzi") or []
    inca = sum(1 for c in dupa if c.get("printata") and (not awb or c.get("awb") == awb.strip()))
    L.append("\nRăspuns OH: %s" % json.dumps(r, ensure_ascii=False)[:800])
    L.append("Verificare după: încă printate în țintă = %d (0 = reușit)." % inca)
    return "\n".join(L)


# ───────────────────────── utilizatorii scanerului ─────────────────────────
def _utilizatori():
    r = scan("/api/auth/users")
    return r if isinstance(r, list) else r.get("users", [])


def _gaseste(uid):
    for u in _utilizatori():
        if int(u.get("id")) == int(uid):
            return u
    raise Eroare("utilizatorul #%s nu există" % uid)


def _rand_u(u):
    return "#%-3s %-22s %-34s %-5s %-9s %-26s %s" % (
        u.get("id"), (u.get("name") or "")[:22], (u.get("username") or "")[:34], u.get("role"),
        ("aprobat" if u.get("approved") else "NEAPROBAT") + ("" if u.get("email_verified") else "*"),
        ", ".join(u.get("stations") or []) or "(toate)",
        ",".join((u.get("permissions") or {}).get("scanner") or []) or ("tot" if u.get("role") == "admin" else "-"))


def scanner_utilizatori(statie="", cauta="", doar_neaprobati=False):
    us = _utilizatori()
    if doar_neaprobati:
        us = [u for u in us if not u.get("approved") or not u.get("email_verified")]
    if statie:
        us = [u for u in us if any(statie.lower() == s.lower() for s in (u.get("stations") or []))]
    if cauta:
        c = cauta.lower()
        us = [u for u in us if c in (u.get("name") or "").lower() or c in (u.get("username") or "").lower()]
    L = ["Utilizatorii aplicației de scanare (scripts.arona.ro): %d. Coloane: id · nume · cont · rol · stare (* = email "
         "neconfirmat) · stații ((toate) = nealocat) · drepturi scaner." % len(us)]
    L += ["  " + _rand_u(u) for u in us]
    nume = Counter((u.get("name") or "").strip().lower() for u in _utilizatori() if (u.get("name") or "").strip())
    dub = [n for n, k in nume.items() if k > 1]
    if dub:
        L.append("\nPosibile conturi DUBLE (același nume): " + "; ".join(
            "%s → %s" % (n, ", ".join("#%s" % u["id"] for u in _utilizatori() if (u.get("name") or "").strip().lower() == n))
            for n in dub))
        L.append("Comasarea NU e expusă aici (nu există endpoint; istoricul de scanări stă pe username) — vezi SKILL.md.")
    return "\n".join(L)


def scanner_aproba(user_id, rol="user", apply=False):
    u = _gaseste(user_id)
    rol = "admin" if rol == "admin" else "user"
    L = ["%s APROBARE cont scaner:\n  %s" % ("EXECUȚIE" if apply else "PROBĂ (nu scrie)", _rand_u(u)),
         "Efect: approved=1, rol=%s%s." % (rol, " (ADMIN = vede și schimbă TOT, inclusiv utilizatorii)" if rol == "admin" else "")]
    if not u.get("email_verified"):
        # login-ul cere email_verified=1 (api/auth.py), iar endpoint-ul de aprobare NU îl atinge
        L.append("⚠ EMAIL NECONFIRMAT: omul NU se poate loga până nu dă click pe linkul din email. Aprobarea de aici nu "
                 "rezolvă asta (nu există încă endpoint de admin pentru confirmare). Dacă are și alt cont confirmat cu "
                 "același nume, acela e cel bun.")
    if u.get("approved") and u.get("role") == rol:
        L.append("Deja aprobat cu rolul ăsta — nimic de făcut.")
        return "\n".join(L)
    if not apply:
        L.append("Rulează cu apply=true după OK-ul omului.")
        return "\n".join(L)
    scan("/api/auth/users/%d/approve" % int(user_id), "POST", {"role": rol})
    L.append("Făcut. Acum: " + _rand_u(_gaseste(user_id)))
    return "\n".join(L)


def _statii_valide():
    try:
        r = scan("/api/stations").get("stations") or []
        v = [s["station"] for s in r if s.get("station") and "suspect" not in s["station"].lower()]
        return v or list(STATII_SCANER)
    except Eroare:
        return list(STATII_SCANER)


def scanner_aloca_statie(user_id, statii, apply=False):
    u = _gaseste(user_id)
    valide = _statii_valide()
    cerute = [s.strip() for s in (statii or "").split(",") if s.strip()]
    norm = []
    for s in cerute:
        m = [v for v in valide if v.lower() == s.lower()]
        if not m:
            raise Eroare("stație necunoscută: %r (valide: %s)" % (s, ", ".join(valide)))
        if m[0] not in norm:
            norm.append(m[0])
    L = ["%s ALOCARE stații pe scaner:\n  %s" % ("EXECUȚIE" if apply else "PROBĂ (nu scrie)", _rand_u(u)),
         "Înainte: %s  →  după: %s" % (", ".join(u.get("stations") or []) or "(toate)", ", ".join(norm) or "(toate)")]
    if (u.get("stations") or []) == norm:
        L.append("Neschimbat — nimic de făcut.")
        return "\n".join(L)
    if not apply:
        L.append("Stațiile hotărăsc ce rafturi/„Fără raft” vede omul în aplicație. Rulează cu apply=true după OK.")
        return "\n".join(L)
    scan("/api/auth/users/%d" % int(user_id), "PUT", {"stations": norm})
    L.append("Făcut. Acum: " + _rand_u(_gaseste(user_id)))
    return "\n".join(L)


def permisiuni_noi(perm_vechi, capabilitati):
    """Doar cheia `scanner` se schimbă; restul permisiunilor (module/tab-uri din dashboard) rămân neatinse —
    PUT-ul rescrie permissions_json întreg."""
    p = dict(perm_vechi or {})
    caps = [c.strip() for c in (capabilitati or "").split(",") if c.strip()]
    rele = [c for c in caps if c not in CAPABILITATI]
    if rele:
        raise Eroare("capabilitate necunoscută: %s (valide: %s)" % (", ".join(rele), ", ".join(CAPABILITATI)))
    p["scanner"] = sorted(set(caps))
    return p


def scanner_acces(user_id, capabilitati, apply=False):
    u = _gaseste(user_id)
    if u.get("role") == "admin":
        return "Contul #%s e ADMIN: are deja tot; drepturile de scaner nu se aplică." % user_id
    p = permisiuni_noi(u.get("permissions"), capabilitati)
    L = ["%s DREPTURI scaner:\n  %s" % ("EXECUȚIE" if apply else "PROBĂ (nu scrie)", _rand_u(u)),
         "scanner: %s → %s (stock = editează stocul Shopify din app; locations = locații/rafturi admin). "
         "Celelalte permisiuni rămân: %s" % ((u.get("permissions") or {}).get("scanner"), p["scanner"],
                                           ", ".join(k for k in p if k != "scanner") or "-")]
    if (u.get("permissions") or {}).get("scanner") == p["scanner"]:
        L.append("Neschimbat — nimic de făcut.")
        return "\n".join(L)
    if not apply:
        L.append("Rulează cu apply=true după OK.")
        return "\n".join(L)
    scan("/api/auth/users/%d" % int(user_id), "PUT", {"permissions": p})
    L.append("Făcut. Acum: " + _rand_u(_gaseste(user_id)))
    return "\n".join(L)


# ───────────────────────── rafturi ─────────────────────────
def _rafturi():
    return scan("/api/shelf-list", with_items=1).get("shelves") or []


def raft_continut(raft, statie="", istoric=10):
    r = raft.strip().lower()
    sh = [s for s in _rafturi() if (s.get("code") or "").lower() == r or (s.get("name") or "").lower() == r
          or (s.get("display") or "").lower() == r]
    if statie:
        sh = [s for s in sh if (s.get("station") or "").lower() == statie.lower()]
    if not sh:
        return "Niciun raft „%s”%s. (cod RF-… sau numele, ex. A1; numele se repetă pe stații)" % (
            raft, (" la " + statie) if statie else "")
    L = []
    for s in sh:
        prod = s.get("products") or []
        L.append("== %s (%s) · stația %s%s · %d produse, %d buc · ultima schimbare %s de %s" % (
            s.get("name"), s.get("code"), s.get("station"), " · VIRTUAL (fără raft)" if s.get("virtual") else "",
            len(prod), sum(p.get("qty") or 0 for p in prod), _ora(s.get("updated_at")), s.get("updated_by") or "?"))
        for p in sorted(prod, key=lambda x: -(x.get("qty") or 0)):
            L.append("   %-16s %6s buc  %-10s %s  (pe raft din %s)" % (p.get("sku"), p.get("qty"), p.get("category") or "",
                                                                     (p.get("title") or "")[:60], _ora(p.get("placed_at"))[:10]))
        if istoric and len(sh) <= 3:
            ev = scan("/api/shelf-history", code=s.get("code"), limit=int(istoric)).get("events") or []
            if ev:
                L.append("   istoric (nou → vechi):")
                for e in ev:
                    L.append("     %s %-8s %-14s %s%s %s%s" % (
                        _ora(e.get("ts")), e.get("action"), e.get("sku") or "",
                        ("%s→" % e["qty_before"]) if e.get("qty_before") is not None else "", e.get("qty") if e.get("qty") is not None else "",
                        e.get("username") or "", ("  " + e["order_ref"]) if e.get("order_ref") else ""))
    return "\n".join(L)


def unde_e_produsul(sku, picks=8):
    k = sku.strip().lower()
    loc = []
    for s in _rafturi():
        for p in s.get("products") or []:
            if (p.get("sku") or "").lower() == k:
                loc.append((s.get("station"), s.get("name"), s.get("code"), p.get("qty") or 0, bool(s.get("virtual")),
                            p.get("title"), p.get("placed_at")))
    if not loc:
        L = ["%s nu e pe niciun raft al scanerului (cu cantitate > 0). Poate fi sinonim / alt cod: încearcă "
             "arona-stock stoc_produs (are sinonimele și stock-sync)." % sku]
    else:
        L = ["%s — %s" % (sku, loc[0][5] or ""), "Total pe rafturi: %d buc în %d locuri." % (sum(x[3] for x in loc), len(loc))]
        for st, n in sorted(Counter(x[0] for x in loc).items()):
            L.append("  %s: %d buc" % (st, sum(x[3] for x in loc if x[0] == st)))
        for st, nume, cod, q, virt, _, pl in sorted(loc, key=lambda x: (x[0] or "", -x[3])):
            L.append("    %-11s %-22s %-12s %6d%s  din %s" % (st, nume, cod, q, "  (fără raft)" if virt else "", _ora(pl)[:10]))
    if picks:
        L.append("")
        L.append(_sql(["produs", "--sku", sku, "--top", int(picks)]))
    return "\n".join(L)


def rafturi_statie(statie, top=25):
    sh = [s for s in _rafturi() if (s.get("station") or "").lower() == statie.lower()]
    if not sh:
        raise Eroare("stație fără rafturi: %r (valide: %s)" % (statie, ", ".join(_statii_valide())))
    reale = [s for s in sh if not s.get("virtual")]
    goale = [s for s in reale if not (s.get("products") or [])]
    virt = [s for s in sh if s.get("virtual")]
    cat = Counter()
    for s in reale:
        for p in s.get("products") or []:
            cat[p.get("category") or "?"] += p.get("qty") or 0
    L = ["Stația %s: %d rafturi reale (%d GOALE), %d bucket-uri fără raft." % (statie, len(reale), len(goale), len(virt)),
         "Pe categorii (buc pe rafturi reale): " + " · ".join("%s %d" % kv for kv in cat.most_common())]
    for v in virt:
        prod = v.get("products") or []
        if not prod:
            continue
        L.append("\n„%s” (%s): %d SKU, %d buc — marfă încă NEpusă pe raft. Top:" % (
            v.get("name"), v.get("code"), len(prod), sum(p.get("qty") or 0 for p in prod)))
        for p in sorted(prod, key=lambda x: -(x.get("qty") or 0))[:top]:
            L.append("   %-16s %7s  %s" % (p.get("sku"), p.get("qty"), (p.get("title") or "")[:60]))
    if goale:
        L.append("\nRafturi goale (%d): %s" % (len(goale), ", ".join(sorted(s.get("name") or s.get("code") for s in goale))[:3000]))
    L.append("\nPozițiile NEGATIVE și stocul vs stock-sync: arona-stock stoc_statie / stoc_produs.")
    return "\n".join(L)


# ───────────────────────── culegere / scanări ─────────────────────────
def picking_live():
    d = scan("/api/picking-live")
    L = ["Culegerea de AZI (live, %s): %s AWB / %s colete. Pe stație: %s. Ritm ultima oră: %s colete; ultimele 10 min: %s." % (
        _ora(d.get("acum")), d.get("total_azi"), d.get("colete_azi"),
        " · ".join("%s %s" % (s.get("locatie"), s.get("colete")) for s in d.get("colete_pe_statie") or []),
        d.get("ritm_ora"), d.get("colete_10_min"))]
    L.append("\nOameni (comenzi · colete · ultima scanare · lot alocat):")
    for o in d.get("oameni") or []:
        lot = o.get("lot") or {}
        L.append("  %-18s %5s %5s  %s%s  %s%s" % (
            o.get("nume"), o.get("comenzi_azi"), o.get("colete_azi"), _ora(o.get("ultima"))[11:], " ACTIV" if o.get("activ") else "",
            ", ".join("%s %s" % kv for kv in (o.get("statii") or {}).items()),
            ("  · lot %s (%s/%s) %s" % (lot.get("lot"), lot.get("culese"), lot.get("n"), lot.get("eticheta") or "")) if lot else ""))
    lot = [x for x in d.get("loturi") or [] if (x.get("culese") or 0) < (x.get("n") or 0)]
    if lot:
        L.append("\nLoturi de azi neterminate (culese/total · alocat):")
        for x in lot[:30]:
            L.append("  %s %s/%s %-10s %s  %s" % (x.get("lot"), x.get("culese"), x.get("n"), x.get("statie") or "",
                                                  x.get("alocat_lui") or "-", (x.get("eticheta") or "")[:70]))
    return "\n".join(L)


def picking_raport(de_la="", pana_la="", luna="", statie="", om=""):
    d = scan("/api/picking-raport", de_la=de_la, pana_la=pana_la, luna=luna, statie=statie, om=om)
    t = d.get("total") or {}
    L = ["Productivitate la cules %s → %s%s%s: %s AWB / %s colete, %s oameni, %s zile lucrate, medie/zi %s colete." % (
        d.get("de_la"), d.get("pana_la"), (" · " + statie) if statie else "", (" · " + om) if om else "",
        t.get("awb"), t.get("colete"), t.get("oameni"), t.get("zile_lucrate"), t.get("medie_zi"))]
    f = d.get("filtre") or {}
    if f.get("statii"):
        L.append("Pe stație: " + " · ".join("%s %s" % (s.get("locatie"), s.get("colete")) for s in f["statii"]))
    L.append("\nPe om (AWB · colete · zile · colete/zi · prima → ultima):")
    for o in d.get("pe_om") or []:
        L.append("  %-18s %6s %6s %3s %8s  %s → %s" % (o.get("nume"), o.get("awb"), o.get("colete"), o.get("zile"),
                                                      o.get("pe_zi"), _ora(o.get("prima")), _ora(o.get("ultima"))[11:]))
    for k in ("pe_zi", "pe_ora"):
        if d.get(k):
            L.append("\n%s: %s" % (k, json.dumps(d[k], ensure_ascii=False)[:1500]))
    return "\n".join(L)


def picking_comanda(cod):
    d = scan("/api/picking-comanda/" + urllib.parse.quote(cod.strip()))
    L = ["Culegerea comenzii / AWB-ului %s:" % cod]
    for e in d.get("evenimente") or []:
        L.append("  %s  %-9s %-16s %s %s %s" % (_ora(e.get("cand")), e.get("actiune"), e.get("de_cine") or "",
                                               e.get("comanda") or "", e.get("awb") or "", e.get("nota") or ""))
    if not d.get("evenimente"):
        L.append("  NICIO scanare în aplicație (n-a fost culeasă).")
    for x in d.get("loturi") or []:
        L.append("  lot %s · %s · creat %s · alocat %s" % (x.get("lot"), x.get("eticheta"), _ora(x.get("creat_la")),
                                                       x.get("alocat_lui") or "-"))
    L.append("Starea la curier / ce se poate face: arona-orderhub oh_comanda.")
    return "\n".join(L)


def lot_continut(lot):
    d = scan("/api/lot/" + urllib.parse.quote(lot.strip().upper()))
    aw = d.get("awbs") or []
    L = ["%s · %s · stația %s · %s etichete · seq %s · creat %s · alocat %s %s" % (
        d.get("lot"), d.get("eticheta"), d.get("statie"), d.get("n"), d.get("seq"), _ora(d.get("creat_la")),
        d.get("alocat_lui") or "-", _ora(d.get("alocat_la")))]
    L.append("Comenzi: " + ", ".join(a.get("comanda") or a.get("awb") for a in aw))
    return "\n".join(L)


def lipsa_marfa(zile=7, sku="", toate=False):
    d = scan("/api/lipsa-marfa", zile=int(zile), sku=sku, deschise=0 if toate else 1)
    L = ["Semnalări „NU GĂSESC” (ultimele %d zile%s): %s." % (int(zile), (", " + sku) if sku else "", d.get("n"))]
    for r in (d.get("randuri") or [])[:60]:
        L.append("  " + json.dumps({k: r.get(k) for k in ("ts", "comanda", "sku", "raft_nume", "statie", "user", "gasit", "hold")
                                    if k in r}, ensure_ascii=False))
    if d.get("pe_sku"):
        L.append("Pe SKU: " + json.dumps(d["pe_sku"], ensure_ascii=False)[:2000])
    return "\n".join(L)


def picking_neculese(ore=24, zile=7, statie="", top=30):
    return _sql(["neculese", "--ore", int(ore), "--zile", int(zile), "--statie", statie, "--top", int(top)])


def produs_istoric(sku, zile=30, top=30):
    return _sql(["produs", "--sku", sku, "--zile", int(zile), "--top", int(top)])


def scanari_anomalii(zile=1, top=20):
    return _sql(["anomalii", "--zile", int(zile), "--top", int(top)])


FUNCTII = {f.__name__: f for f in (print_coada, print_istoric, print_reset, scanner_utilizatori, scanner_aproba,
                                   scanner_aloca_statie, scanner_acces, raft_continut, unde_e_produsul, rafturi_statie,
                                   picking_live, picking_raport, picking_comanda, lot_continut, lipsa_marfa,
                                   picking_neculese, scanari_anomalii, produs_istoric)}


def ruleaza(nume, **kw):
    """Punctul unic de intrare: erorile devin text, nu traceback; ieșirea e tăiată la MAX_OUT."""
    try:
        return _taie(FUNCTII[nume](**kw))
    except Eroare as e:
        return "Eroare: %s" % e
    except Exception as e:  # rețea / ssh
        return "Eroare neașteptată: %s: %s" % (type(e).__name__, str(e)[:400])


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2 or sys.argv[1] not in FUNCTII:
        print("folosire: depozit.py <%s> ['{json kwargs}']" % "|".join(FUNCTII))
        sys.exit(2)
    print(ruleaza(sys.argv[1], **(json.loads(sys.argv[2]) if len(sys.argv) > 2 else {})))
