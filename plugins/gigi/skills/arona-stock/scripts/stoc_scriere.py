# -*- coding: utf-8 -*-
"""stoc_scriere.py — SCRIERI de stoc în AWB Arona (locations.db): RECEPȚIE de container și MUTARE între stații.

Rulează PE serverul de scripturi (trimis pe stdin de MCP-ul arona-stock: `python - receptie|mutare ...`).
DRY-RUN implicit; scrie doar cu --apply. Ținta e numărătoarea din AWB Arona (D-023: la produsele numărate
scrierea în Shopify e ignorată) — masterul stock-sync se actualizează singur la rularea awb_to_stocksync
(L–V 15:30 și 23:30). NU scrie în stock-sync și nici în Shopify.

Reguli codificate (fiecare vine dintr-un incident):
- recepția se scrie pe „Fără raft" (LOC-<STAȚIE>) cu MOTIV, user `receptie-owner`, plus jurnalul `receipts`
  și `container_meta.arrived_at` (ghid C54–C56, 24-sep);
- sursa liniilor = container_expected (sincronizat din foaia „Tom - recepții containere") sau lista lipită;
  liniile fără SKU / ambalajele / Grandia (o recepționează depozitul) se sar; codul de bare se rezolvă și cu 0
  în față, iar dacă dă două produse e AMBIGUU → oprire (C58, negru-M vs roz-L);
- se scade ce a pus deja depozitul pe RAFTURI de la sosire (dublarea de 8.816 buc, 24-sep) și ce a recepționat
  deja altcineva (C53, colega la 31 de secunde);
- mutare către Bartolomeu = DOAR scădere la sursă, cât a scanat Bartolomeu (bartolomeu-fara-raft-dubla-numarare);
  ce a trecut deja prin aplicație („din Fără raft · Uzina 2", transfer între stații) nu se mai scade;
- mutare către Uzina 2 / Teliu = ADAUGĂ întâi la destinație, SCADE după, într-o singură tranzacție;
- backup consistent (sqlite backup API) înainte de scriere + jurnal `data/arona_stock_scrieri.jsonl`;
  aceeași listă de două ori în N ore e refuzată fără --force.
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys
from collections import defaultdict
from zoneinfo import ZoneInfo

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = "/root/Scripturi"
LOC_DB = ROOT + "/data/locations.db"
OGL_DB = ROOT + "/data/produse_oglinda.db"
JURNAL = ROOT + "/data/arona_stock_scrieri.jsonl"
RO = ZoneInfo("Europe/Bucharest")
STATII = ["Bartolomeu", "Uzina 2", "Teliu"]
SISTEM = {"cron-expediere", "revert-expediere", "sistem", "smoke", "backfill-v64", "verificare-fix",
          "corectie-sinonim"}
PRAG_ZI = 50          # peste ~50 buc într-o zi pe raft = preluare de container, nu reaprovizionare (ghid)


# ───────────────────────── utilitare ─────────────────────────

def acum():
    return dt.datetime.now(RO)


def statie_norm(s):
    k = re.sub(r"[^a-z0-9]", "", (s or "").lower())
    if k.startswith("bart"):
        return "Bartolomeu"
    if k.startswith("uz") or k in ("u2",):
        return "Uzina 2"
    if k.startswith("tel"):
        return "Teliu"
    raise SystemExit("Stație necunoscută: %r (Bartolomeu / Uzina 2 / Teliu)" % s)


def slug(st):
    s = "".join(ch if ch.isalnum() else "-" for ch in st.upper())
    while "--" in s:
        s = s.replace("--", "-")
    return s.strip("-")


def delta(r):
    return int(r["qty"] or 0) - int(r["qty_before"] or 0)


class Ctx:
    def __init__(self, apply):
        if apply:
            self.con = sqlite3.connect(LOC_DB, timeout=30)
            self.con.execute("PRAGMA busy_timeout=20000")
        else:
            self.con = sqlite3.connect("file:%s?mode=ro" % LOC_DB, uri=True, timeout=30)
        self.con.row_factory = sqlite3.Row
        c = self.con
        self.station = {r["code"]: (r["station"] or "Bartolomeu") for r in c.execute("SELECT code, station FROM shelves")}
        self.virtual = {r["code"]: int(r["virtual"] or 0) for r in c.execute("SELECT code, virtual FROM shelves")}
        self.name = {r["code"]: (r["name"] or r["code"]) for r in c.execute("SELECT code, name FROM shelves")}
        self.alias = {(r["alias"] or "").lower(): (r["canonical"] or "").lower()
                      for r in c.execute("SELECT alias, canonical FROM sku_aliases")}
        self.rude = defaultdict(set)              # canonic -> {canonic + alias-urile lui}
        for a, k in self.alias.items():
            self.rude[k].add(a)
        self.pe_raft = {r[0] for r in c.execute("SELECT DISTINCT lower(sku) FROM shelf_items")}
        self.cat_sku, self.cat_bc = set(), defaultdict(set)
        try:
            o = sqlite3.connect("file:%s?mode=ro" % OGL_DB, uri=True)
            for sku_l, bc in o.execute("SELECT sku_l, barcode FROM produse"):
                if sku_l:
                    self.cat_sku.add(sku_l)
                    for b in re.split(r"[,\s]+", bc or ""):
                        if b:
                            self.cat_bc[b].add(sku_l)
            o.close()
        except Exception as e:
            print("⚠️ catalogul local (produse_oglinda.db) nu s-a putut citi: %s" % e)

    def canonic(self, sku):
        k = (sku or "").strip().lower()
        return self.alias.get(k, k)

    def familie(self, sku):
        k = self.canonic(sku)
        return sorted({k} | self.rude.get(k, set()))

    def cunoscut(self, sku):
        k = (sku or "").lower()
        return k in self.cat_sku or k in self.pe_raft or k in self.alias

    def bucket(self, st):
        """Codurile „fără raft" ale stației, principalul (LOC-<STAȚIE>) primul (V88: și Depozit mic)."""
        princ = "LOC-" + slug(st)
        rows = [c for c, s in self.station.items() if s == st and self.virtual.get(c) and not c.startswith("LOC-REBUT")]
        return sorted(rows, key=lambda c: (c != princ, c))

    def randuri(self, sku):
        """[(code, qty)] cu qty>0 pe toată familia SKU-ului (stocul stă pe canonic)."""
        fam = self.familie(sku)
        q = "SELECT code, sku, qty FROM shelf_items WHERE lower(sku) IN (%s) AND qty>0" % ",".join("?" * len(fam))
        return [(r["code"], r["sku"], int(r["qty"])) for r in self.con.execute(q, fam)]

    def evenimente(self, sku, de_la):
        fam = self.familie(sku)
        q = ("SELECT id, ts, code, action, sku, qty, qty_before, username, COALESCE(order_ref,'') AS note "
             "FROM shelf_events WHERE lower(sku) IN (%s) AND substr(ts,1,10) >= ? ORDER BY id" % ",".join("?" * len(fam)))
        return list(self.con.execute(q, (*fam, de_la)))


def sku_din_numar(ctx, num):
    """„1844" -> ha-1844 (cum scrie ownerul); un cod numeric Grandia rămâne așa dacă e în catalog."""
    cand = []
    if len(num) <= 4:
        cand.append("ha-" + num.zfill(4))
    cand.append(num)
    for c in cand:
        if ctx.cunoscut(c):
            return c
    return cand[0]


def parseaza_lista(ctx, text):
    """„1844 -400 0115-240", „HA-0115 240", „0302-80buc", „ha-1844:400" (pe linii, virgule sau spații)
    -> ([(sku, qty)], [erori]). SKU-urile cu cratimă (ha-2046-1) se recunosc după catalog/rafturi."""
    t = (text or "").lower()
    t = re.sub(r"(\d)\s*(buc(?:ati|ăți|\.)?|pcs|bucăți)\b", r"\1", t)
    t = re.sub(r"[,;\n\t]+", " ; ", t)
    toks = [x for x in t.split() if x not in ("x", "×", "buc", "bucati", "bucăți", "pcs", "=")]
    out, err, pending = [], [], None
    for tok in toks:
        if tok == ";":
            if pending:
                err.append("%s: lipsește cantitatea" % pending)
                pending = None
            continue
        if re.fullmatch(r"[-:=]?\d+", tok):
            num = tok.lstrip("-:=")
            if pending:
                out.append((pending, int(num)))
                pending = None
            elif tok[0] in "-:=":
                err.append("%s: cantitate fără produs" % tok)
            else:
                pending = sku_din_numar(ctx, num)
            continue
        if pending:
            err.append("%s: lipsește cantitatea" % pending)
            pending = None
        tok = tok.strip(":=")
        if ctx.cunoscut(tok):
            pending = tok
            continue
        gasit = False
        for i in range(len(tok) - 1, 0, -1):          # taie de la DREAPTA: ha-2046-1-50 -> ha-2046-1 + 50
            if tok[i] in "-:=" and tok[i + 1:].isdigit():
                st = tok[:i]
                sku = sku_din_numar(ctx, st) if st.isdigit() else st
                out.append((sku, int(tok[i + 1:])))
                gasit = True
                break
        if not gasit:
            pending = tok
    if pending:
        err.append("%s: lipsește cantitatea" % pending)
    return out, err


# ───────────────────────── backup + jurnal ─────────────────────────

def cheie(op, parti):
    return hashlib.sha1(json.dumps([op, parti], sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def repetare(key, ore):
    if not os.path.exists(JURNAL):
        return None
    lim = acum() - dt.timedelta(hours=ore)
    gasit = None
    with open(JURNAL, encoding="utf-8") as f:
        for ln in f:
            try:
                j = json.loads(ln)
            except Exception:
                continue
            if j.get("key") == key and dt.datetime.fromisoformat(j["ts"]) >= lim:
                gasit = j
    return gasit


def backup(op):
    dst = "%s.bak-%s-%s" % (LOC_DB, op, acum().strftime("%Y%m%d-%H%M%S"))
    src = sqlite3.connect("file:%s?mode=ro" % LOC_DB, uri=True)
    b = sqlite3.connect(dst)
    with b:
        src.backup(b)
    b.close()
    src.close()
    return dst


def scrie_jurnal(rec):
    with open(JURNAL, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def pune(ctx, code, sku, q, user, motiv, ts):
    """+q pe (code, sku) — aceeași formă ca api.scan.station_stock_set (mode=add)."""
    c = ctx.con
    r = c.execute("SELECT qty FROM shelf_items WHERE code=? AND sku=?", (code, sku)).fetchone()
    have = int(r[0]) if r else 0
    newq = have + q
    c.execute("INSERT INTO shelf_items (code, sku, qty, updated_by, updated_at, placed_at, reason) VALUES (?,?,?,?,?,?,?) "
              "ON CONFLICT(code, sku) DO UPDATE SET qty=excluded.qty, updated_by=excluded.updated_by, "
              "updated_at=excluded.updated_at, reason=COALESCE(NULLIF(excluded.reason,''), shelf_items.reason)",
              (code, sku, newq, user, ts, ts, motiv))
    c.execute("INSERT INTO shelf_events (ts, code, name, action, sku, qty, username, order_ref, qty_before) "
              "VALUES (?,?,?,?,?,?,?,?,?)", (ts, code, ctx.name.get(code), "add", sku, newq, user, "MOTIV: " + motiv, have))
    return have, newq


def scade(ctx, code, sku, q, user, motiv, ts):
    c = ctx.con
    r = c.execute("SELECT qty FROM shelf_items WHERE code=? AND sku=?", (code, sku)).fetchone()
    have = int(r[0]) if r else 0
    if have < q:
        raise RuntimeError("%s pe %s are %d < %d (s-a schimbat între timp) — nimic scris" % (sku, code, have, q))
    newq = have - q
    if newq <= 0:
        c.execute("DELETE FROM shelf_items WHERE code=? AND sku=?", (code, sku))
    else:
        c.execute("UPDATE shelf_items SET qty=?, updated_by=?, updated_at=?, reason=? WHERE code=? AND sku=?",
                  (newq, user, ts, motiv, code, sku))
    c.execute("INSERT INTO shelf_events (ts, code, name, action, sku, qty, username, order_ref, qty_before) "
              "VALUES (?,?,?,?,?,?,?,?,?)",
              (ts, code, ctx.name.get(code), "remove" if newq <= 0 else "reduce", sku, max(newq, 0), user,
               "MOTIV: " + motiv, have))
    return have, max(newq, 0)


def total_depozit(ctx, sku):
    return sum(q for code, _, q in ctx.randuri(sku) if not code.startswith("LOC-REBUT"))


# ───────────────────────── RECEPȚIE ─────────────────────────

def refs_din(text):
    out = []
    for m in re.findall(r"(?<![\w.\-])[cC#]?(\d{1,3})(?![\w.])", text or ""):
        r = "C%d" % int(m)
        if r not in out:
            out.append(r)
    return out


LUNI = {"ian": 1, "jan": 1, "feb": 2, "mar": 3, "apr": 4, "mai": 5, "may": 5, "iun": 6, "jun": 6, "iul": 7, "jul": 7,
        "aug": 8, "sep": 9, "oct": 10, "noi": 11, "nov": 11, "dec": 12}


def data_din_titlu(title):
    """„C54-24Sept" / „C55- 23 Sept" / „C34 27.05" -> ISO; sync-ul foii ratează formele lipite („24Sept")."""
    rest = re.sub(r"^\s*C\d+\s*-?\s*", "", title or "")
    azi = acum().date()
    try:
        m = re.search(r"(\d{1,2})[./](\d{1,2})", rest)
        if m:
            d = dt.date(azi.year, int(m.group(2)), int(m.group(1)))
        else:
            m = re.search(r"(\d{1,2})\s*([A-Za-z]{3})", rest)
            if not m or m.group(2).lower() not in LUNI:
                return ""
            d = dt.date(azi.year, LUNI[m.group(2).lower()], int(m.group(1)))
        if d > azi + dt.timedelta(days=30):
            d = d.replace(year=azi.year - 1)
        return d.isoformat()
    except ValueError:
        return ""


def rezolva_linie(ctx, raw):
    """(sku_canonic | None, motiv_sarire | None, ambiguu: bool)."""
    k = (raw or "").strip().lower()
    if not k or k.startswith("fără sku") or k.startswith("fara sku"):
        return None, "fără SKU în foaie (ambalaj / necodificat)", False
    if ctx.cunoscut(k):
        return ctx.canonic(k), None, False
    if k.isdigit() and len(k) >= 8:                                   # cod de bare
        forme = {k, "0" + k, k.lstrip("0")}
        hit = set()
        for f in forme:
            hit |= ctx.cat_bc.get(f, set())
        if len(hit) == 1:
            return ctx.canonic(hit.pop()), None, False
        if len(hit) > 1:
            return None, "cod de bare AMBIGUU -> %s (întreabă, nu ghici)" % ", ".join(sorted(hit)), True
        return None, "cod de bare necunoscut în catalog", False
    if k.isdigit():                                                   # 52956 -> GD-KID-52956
        hit = sorted(s for s in ctx.cat_sku if s.endswith("-" + k))
        if len(hit) == 1:
            return ctx.canonic(hit[0]), None, False
        if len(hit) > 1:
            return None, "cod numeric AMBIGUU -> %s" % ", ".join(hit[:5]), True
    return None, "nu e în catalog și n-a stat pe niciun raft (ambalaj / consumabil / produs nelistat)", False


def receptie(a):
    statie = statie_norm(a.statie)
    ctx = Ctx(a.apply)
    if a.apply:
        ctx.con.isolation_level = None
        ctx.con.execute("BEGIN IMMEDIATE")          # planul se calculează sub lacăt -> exact ce se scrie
    refs = refs_din(a.container)
    if not refs and not a.lista:
        raise SystemExit("Dă containerul (ex. C66 sau „C54, 55, 56”).")
    print("RECEPȚIE %s -> „Fără raft · %s\"%s\n" % (", ".join(refs) or "(listă lipită)", statie,
                                                   "" if a.apply else "   [DRY-RUN]"))
    blocaje = []
    if statie == "Bartolomeu" and not a.force:
        blocaje.append("Bartolomeu: marfa se pune pe RAFT de colegii de acolo (bucketul „Fără raft · Bartolomeu” trebuie să "
                       "rămână gol, altfel se numără de două ori). Recepția se face la Uzina 2 / Teliu; force=true doar "
                       "dacă ownerul cere explicit.")

    # liniile
    linii = []                                      # (ref, raw, qty, categorie)
    if a.lista:
        perechi, err = parseaza_lista(ctx, a.lista)
        if err:
            blocaje += ["listă: " + e for e in err]
        ref0 = refs[0] if refs else "LISTA"
        linii = [(ref0, s, q, "") for s, q in perechi]
        if len(refs) > 1:
            blocaje.append("cu `lista` dă UN singur container (am primit %s)" % ", ".join(refs))
    else:
        for ref in refs:
            rows = list(ctx.con.execute("SELECT sku, qty, COALESCE(category,'') AS cat FROM container_expected "
                                        "WHERE ref=? ORDER BY qty DESC", (ref,)))
            if not rows:
                blocaje.append("%s nu are linii în AWB Arona (container_expected). Sync-ul din foaie rulează lunea "
                               "07:12; lipește lista cu `lista=`." % ref)
            linii += [(ref, r["sku"], int(r["qty"]), r["cat"]) for r in rows]
    meta = {r["ref"]: r for r in ctx.con.execute("SELECT ref, title, arrived_at FROM container_meta")}
    for ref in refs:
        m = meta.get(ref)
        if m is None or not m["arrived_at"]:
            print("⚠️ %s: în foaie e încă PE DRUM (titlul tabului fără dată%s). Foaia rămâne des în urmă — "
                  "confirmă că a sosit fizic." % (ref, (", „%s\"" % m["title"]) if m else ""))
    de_la_ref = {}
    for ref in refs:
        m = meta.get(ref)
        sosit = (m["arrived_at"] or "")[:10] if m is not None else ""
        if not sosit and m is not None:
            sosit = data_din_titlu(m["title"])
            if sosit:
                print("   (%s: data sosirii luată din titlul tabului „%s\" = %s)" % (ref, m["title"], sosit))
        if a.de_la:
            de_la_ref[ref] = a.de_la
        elif sosit:
            de_la_ref[ref] = (dt.date.fromisoformat(sosit) - dt.timedelta(days=1)).isoformat()
        else:
            de_la_ref[ref] = (acum().date() - dt.timedelta(days=3)).isoformat()
    if not refs:
        de_la_ref["LISTA"] = a.de_la or (acum().date() - dt.timedelta(days=3)).isoformat()

    sarite, plan, deja_tot = [], [], defaultdict(int)
    for ref, raw, q, cat in linii:
        if cat.upper() == "GRANDIA" and not a.include_grandia:
            sarite.append((ref, raw, q, "Grandia — o recepționează depozitul pe rafturi (include_grandia=true ca s-o pui)"))
            continue
        sku, motiv, amb = rezolva_linie(ctx, raw)
        if sku is None:
            if amb:
                blocaje.append("%s %s: %s" % (ref, raw, motiv))
            if not (a.include_necunoscute and not amb and not raw.lower().startswith("fără sku")):
                sarite.append((ref, raw, q, motiv))
                continue
            sku = raw.strip().lower()
        de_la = de_la_ref.get(ref) or de_la_ref.get("LISTA")
        ev = ctx.evenimente(sku, de_la_ref.get(ref, de_la))
        rx = re.compile(r"\b%s\b" % re.escape(ref), re.I)
        # 1) recepționat deja (oricine: Sonia prin AWB Arona, scriptul ăsta, app) — evenimente cu „recep" + ref
        ev_all = [e for e in ctx.con.execute(
            "SELECT qty, qty_before, COALESCE(order_ref,'') note FROM shelf_events WHERE lower(sku) IN (%s) "
            "AND lower(COALESCE(order_ref,'')) LIKE '%%recep%%'" % ",".join("?" * len(ctx.familie(sku))),
            ctx.familie(sku))]
        deja_ev = sum(max(delta(e), 0) for e in ev_all if rx.search(e["note"]))
        deja_rc = sum(int(r[0] or 0) for r in ctx.con.execute(
            "SELECT qty FROM receipts WHERE ref=? AND lower(sku) IN (%s)" % ",".join("?" * len(ctx.familie(sku))),
            (ref, *ctx.familie(sku))))
        deja = max(deja_ev, deja_rc)
        # 2) pus deja de la sosire (rafturi SAU „Fără raft", orice stație), fără nota de recepție de mai sus.
        #    Suma adăugărilor pe locație (fără „din …"/„Repus"/mutări/cron).
        #    „Fără raft" (orice stație) și rafturile stației de recepție contează oricât de mic; rafturile ALTEI stații
        #    doar de la min(PRAG_ZI, q) buc (sub, e reaprovizionare / retur).
        pana_la = (dt.date.fromisoformat(de_la_ref.get(ref, de_la)) + dt.timedelta(days=a.zile)).isoformat()
        net, zi0 = defaultdict(int), {}
        for e in ev:
            if e["ts"][:10] > pana_la or e["code"].startswith("LOC-REBUT"):
                continue
            d, n, u = delta(e), e["note"].lower(), (e["username"] or "")
            if u in SISTEM or "recep" in n:
                continue
            if d > 0 and e["action"] in ("add", "set"):
                if n.startswith("din ") or "repus" in n or "mutat" in n or "mutare" in n:
                    continue
                net[e["code"]] += d
                zi0.setdefault(e["code"], e["ts"][5:10])
            # corecturile în jos NU se scad: o adăugare nelogată urmată de corectura ei (C53, 8-sep, Uzina 2) ar
            # anula o parte reală. Eroarea rămasă e în direcția sigură (puțin MAI PUȚIN pus, nu stoc fantomă).
        pe_raft = {}
        for code_, v in net.items():
            st_ = ctx.station.get(code_, "?")
            sigur = ctx.virtual.get(code_) or st_ == statie     # „Fără raft" oriunde / rafturile stației de recepție
            if v > 0 and (sigur or v >= min(PRAG_ZI, q)):
                pe_raft[(zi0.get(code_, ""), "%s %s" % (st_, ctx.name.get(code_, code_)))] = v
        raft = sum(pe_raft.values())
        de_pus = max(q - deja - raft, 0)
        deja_tot[ref] += deja
        bk = ctx.bucket(statie)
        code = bk[0] if bk else "LOC-" + slug(statie)
        cur = next((x for c, s, x in ctx.randuri(sku) if c == code and s.lower() == sku), 0)
        plan.append(dict(ref=ref, raw=raw, sku=sku, qty=q, deja=deja, raft=raft,
                         raft_det=", ".join("%s %s +%d" % (k[0], k[1], v) for k, v in sorted(pe_raft.items())),
                         de_pus=de_pus, code=code, cur=cur, tot=total_depozit(ctx, sku)))

    print("%-5s %-22s %8s %8s %8s %8s  %-28s %s" % ("REF", "SKU", "foaie", "recept.", "pus deja", "DE PUS",
                                                    "Fără raft înainte->după", "pus deja de la sosire (pe locație)"))
    for p in plan:
        print("%-5s %-22s %8d %8d %8d %8d  %-28s %s" % (
            p["ref"], p["sku"] + ("" if p["raw"].lower() == p["sku"] else " (%s)" % p["raw"])[:22], p["qty"],
            p["deja"], p["raft"], p["de_pus"], "%d -> %d" % (p["cur"], p["cur"] + p["de_pus"]), p["raft_det"]))
    tot = sum(p["de_pus"] for p in plan)
    print("\nDE PUS pe „Fără raft · %s\": %d linii, %d buc (din %d buc în foaie pe liniile de marfă)." % (
        statie, sum(1 for p in plan if p["de_pus"]), tot, sum(p["qty"] for p in plan)))
    if sarite:
        print("\nSĂRITE (%d linii, %d buc):" % (len(sarite), sum(s[2] for s in sarite)))
        for ref, raw, q, motiv in sarite:
            print("   %-5s %-28s %9d  %s" % (ref, raw[:28], q, motiv))
    for ref, d in deja_tot.items():
        if d and not a.force:
            blocaje.append("%s e deja recepționat (parțial: %d buc cu MOTIV „Receptie %s\" / jurnal receipts). "
                           "Liniile rămase arată ce LIPSEȘTE; scrii doar cu force=true după ce verifici." % (ref, d, ref))

    parti = sorted((p["ref"], p["sku"], p["de_pus"]) for p in plan if p["de_pus"])
    key = cheie("receptie", [statie, parti])
    rep = repetare(key, a.ore)
    if rep and not a.force:
        blocaje.append("ACEEAȘI recepție a fost scrisă la %s (backup %s). Refuz repetarea în %dh fără force=true."
                       % (rep["ts"][:16], rep.get("backup"), a.ore))
    print("\nCheie idempotență: %s" % key)
    if blocaje:
        print("\n⛔ OPRIT — nimic scris:" if a.apply else "\n⛔ De rezolvat înainte de apply:")
        for b in blocaje:
            print("   - " + b)
        if a.apply:
            ctx.con.execute("ROLLBACK")
            sys.exit(3)
    if not a.apply:
        print("\n[DRY-RUN] nimic scris. apply=true scrie exact coloana DE PUS (backup locations.db + jurnal).")
        print("Masterul stock-sync se actualizează la următoarea rulare awb_to_stocksync (L–V 15:30 / 23:30).")
        return
    if not tot:
        ctx.con.execute("ROLLBACK")
        print("\nNimic de pus.")
        return
    bak = backup("receptie")
    ts = acum().isoformat(timespec="seconds")
    scrise = []
    try:
        for p in plan:
            if not p["de_pus"]:
                continue
            motiv = "Receptie %s %d (%s) - container descarcat la %s, fara raft (MCP arona-stock%s)" % (
                p["ref"], p["de_pus"], acum().strftime("%d.%m"), statie, ("; " + a.motiv) if a.motiv else "")
            have, newq = pune(ctx, p["code"], p["sku"], p["de_pus"], "receptie-owner", motiv, ts)
            ctx.con.execute("INSERT INTO receipts (sku, qty, source, ref, created_by, created_at) VALUES (?,?,?,?,?,?)",
                            (p["sku"], p["de_pus"], "reception", p["ref"], "receptie-owner", ts))
            scrise.append({"ref": p["ref"], "sku": p["sku"], "code": p["code"], "inainte": have, "dupa": newq,
                           "adaugat": p["de_pus"]})
        for ref in sorted({s["ref"] for s in scrise} & set(refs)):
            ctx.con.execute("INSERT INTO container_meta (ref, title, arrived_at, updated_at) VALUES (?,?,?,?) "
                            "ON CONFLICT(ref) DO UPDATE SET arrived_at=COALESCE(container_meta.arrived_at, excluded.arrived_at), "
                            "updated_at=excluded.updated_at", (ref, ref, ts[:10], ts))
        ctx.con.execute("COMMIT")
    except Exception:
        ctx.con.execute("ROLLBACK")
        raise
    scrie_jurnal({"ts": ts, "op": "receptie", "key": key, "statie": statie, "refs": refs, "backup": bak,
                  "motiv": a.motiv, "scrise": scrise})
    print("\n✅ SCRIS (%s): %d linii, %d buc pe „Fără raft · %s\". Backup: %s" % (ts[:16], len(scrise), tot, statie, bak))
    for s in scrise:
        print("   %-5s %-18s %-14s %6d -> %6d (+%d)" % (s["ref"], s["sku"], s["code"], s["inainte"], s["dupa"], s["adaugat"]))
    print("Jurnal: %s. Masterul se actualizează la următoarea rulare awb_to_stocksync." % JURNAL)


# ───────────────────────── MUTARE ─────────────────────────

def clasifica_dest(ctx, ev, din, in_):
    """Ce a apărut la destinație în fereastră: (din_sursa_deja_scazut, scanat_nou, note_mutare_manuale)."""
    by_ts = defaultdict(list)
    for e in ev:
        by_ts[(e["ts"][:19], (e["sku"] or "").lower())].append(e)
    din_src = scan_nou = manual = 0
    for e in ev:
        code = e["code"]
        if ctx.station.get(code) != in_:
            continue
        d = delta(e)
        if d <= 0 or e["action"] not in ("add", "set", "transfer"):
            continue
        n = e["note"]
        nl = n.lower()
        if e["action"] == "transfer":
            if n.startswith("←"):
                pere = [x for x in by_ts[(e["ts"][:19], (e["sku"] or "").lower())]
                        if x["action"] == "transfer" and x["note"].startswith("→") and ctx.station.get(x["code"]) == din]
                if pere:
                    din_src += d                      # transfer între stații prin aplicație: sursa e deja scăzută
            continue
        if nl.startswith("din ") and nl.rstrip().endswith(din.lower()):
            din_src += d                              # V81: „din Fără raft · Uzina 2" — scăzut deja la sursă
            continue
        if nl.startswith("din "):
            continue                                  # mutare în interiorul stației (din raftul X / Fără raft propriu)
        if (e["username"] or "") in SISTEM or "repus" in nl or e["action"] == "return":
            continue
        if "mutat" in nl or "mutare" in nl:
            manual += d                               # notă manuală de mutare (ex. listă Sonia) — dest o are deja
            continue
        if ctx.virtual.get(code) and (e["username"] or "") == "awb-svc" and not nl.startswith("motiv"):
            continue
        scan_nou += d                                 # „marfă nouă", fără sursă, corecție în sus
    return din_src, scan_nou, manual


def scaderi_sursa(ctx, ev, din, in_):
    """Scăderi la sursă în fereastră: (cu MOTIV de mutare spre destinație = făcute deja, alte scăderi omenești)."""
    cu_motiv = alte = 0
    for e in ev:
        if ctx.station.get(e["code"]) != din:
            continue
        d = delta(e)
        if d >= 0 or e["action"] not in ("set", "remove", "reduce"):
            continue
        nl = e["note"].lower()
        if (e["username"] or "") in SISTEM or nl.startswith("numarat la raftul") or nl.startswith("mutat la raftul"):
            continue
        if ("mutare" in nl or "mutat" in nl) and in_.lower().split()[0] in nl:
            cu_motiv += -d
        else:
            alte += -d
    return cu_motiv, alte


def mutare(a):
    din, in_ = statie_norm(a.din), statie_norm(a.in_)
    if din == in_:
        raise SystemExit("Sursa și destinația sunt aceeași stație.")
    ctx = Ctx(a.apply)
    if a.apply:
        ctx.con.isolation_level = None
        ctx.con.execute("BEGIN IMMEDIATE")
    de_la = a.de_la or acum().date().isoformat()
    la_dest = in_ != "Bartolomeu"
    print("MUTARE %s -> %s, fereastra de la %s%s\n" % (din, in_, de_la, "" if a.apply else "   [DRY-RUN]"))
    print("Regula: " + ("Bartolomeu scanează singur pe raft -> se SCADE DOAR la sursă, cât a apărut pe rafturile "
                        "Bartolomeu și n-a fost deja scăzut prin aplicație; la Bartolomeu nu se scrie nimic."
                        if not la_dest else
                        "se ADAUGĂ pe „Fără raft · %s\" (minus ce a scanat deja %s) și se SCADE la %s, "
                        "într-o singură tranzacție (întâi adaugă, apoi scade)." % (in_, in_, din)) + "\n")
    blocaje = []
    forced = {}
    for part in re.split(r"[,\s]+", a.rafturi or ""):
        if "=" in part:
            s, c = part.split("=", 1)
            forced[ctx.canonic(s)] = c.strip().upper()

    if (a.lista or "").strip().lower() in ("azi", "today", "auto"):
        # „ce a fost adăugat azi la Bartolomeu trebuie scăzut din Uzina": lista = scanat nou la destinație
        cand = {r[0] for r in ctx.con.execute(
            "SELECT DISTINCT lower(e.sku) FROM shelf_events e JOIN shelves s ON s.code=e.code WHERE s.station=? "
            "AND substr(e.ts,1,10) >= ? AND e.action IN ('add','set') AND COALESCE(e.qty,0) > COALESCE(e.qty_before,0)",
            (in_, de_la))}
        perechi, mici = [], []
        for s in sorted({ctx.canonic(x) for x in cand}):
            if not any(ctx.station.get(c) == din for c, _, _ in ctx.randuri(s)):
                continue
            _, nou, _ = clasifica_dest(ctx, ctx.evenimente(s, de_la), din, in_)
            if nou >= a.prag:
                perechi.append((s, nou))
            elif nou > 0:
                mici.append((s, nou))
        print("Listă DERIVATĂ (scanat nou la %s din %s, SKU-uri care au stoc la %s): %d SKU, %d buc.\n"
              "⚠️ Include și retururi / corecții în sus făcute de mână — ownerul confirmă lista înainte de apply.\n"
              % (in_, de_la, din, len(perechi), sum(q for _, q in perechi)))
        if mici:
            print("Lăsate deoparte (sub prag=%d buc/SKU — de regulă retururi / corecții): %s\n"
                  % (a.prag, ", ".join("%s %d" % x for x in mici)))
        err = []
    else:
        perechi, err = parseaza_lista(ctx, a.lista)
    blocaje += ["listă: " + e for e in err]
    agg = defaultdict(int)
    for s, q in perechi:
        agg[ctx.canonic(s)] += q

    plan = []
    for sku, Q in agg.items():
        if not ctx.cunoscut(sku):
            blocaje.append("%s: SKU necunoscut (nici în catalog, nici pe vreun raft)" % sku)
            continue
        ev = ctx.evenimente(sku, de_la)
        din_src, nou, manual = clasifica_dest(ctx, ev, din, in_)
        cu_motiv, alte = scaderi_sursa(ctx, ev, din, in_)
        deja = min(Q, din_src + cu_motiv)
        rest = Q - deja
        la_dest_are = nou + manual
        if la_dest:
            de_scazut = rest
            de_adaugat = max(rest - la_dest_are, 0)
            nescanat = 0
        else:
            de_scazut = min(rest, la_dest_are)
            nescanat = rest - de_scazut
            if a.fara_scanare:
                de_scazut, nescanat = rest, 0
            de_adaugat = 0
        rows = ctx.randuri(sku)
        src_rows = [(c, s, q) for c, s, q in rows if ctx.station.get(c) == din]
        luari, ramas = [], de_scazut
        if sku in forced and ramas > 0:
            c = forced[sku]
            have = next((q for cc, s, q in src_rows if cc == c), 0)
            if ctx.station.get(c) != din:
                blocaje.append("%s: raftul %s nu e la %s" % (sku, c, din))
            elif have < ramas:
                blocaje.append("%s: %s are %d < %d" % (sku, c, have, ramas))
            else:
                s_ = next(s for cc, s, q in src_rows if cc == c)
                luari.append((c, s_, have, ramas))
                ramas = 0
        else:
            for bcode in ctx.bucket(din):
                if ramas <= 0:
                    break
                for c, s, q in src_rows:
                    if c == bcode and q > 0:
                        t = min(q, ramas)
                        luari.append((c, s, q, t))
                        ramas -= t
        if ramas > 0:
            rafturi = ", ".join("%s(%s)=%d" % (ctx.name.get(c, c), c, q) for c, s, q in src_rows
                                if not ctx.virtual.get(c)) or "niciunul"
            blocaje.append("%s: „Fără raft · %s” nu ajunge (lipsesc %d). Rafturi la %s: %s — alege cu rafturi=\"%s=<COD>\""
                           % (sku, din, ramas, din, rafturi, sku))
        bk = ctx.bucket(in_)
        dcode = bk[0] if bk else "LOC-" + slug(in_)
        dcur = next((q for c, s, q in rows if c == dcode and s.lower() == sku), 0)
        tot = sum(q for c, _, q in rows if not c.startswith("LOC-REBUT"))
        dupa_tot = tot - de_scazut + de_adaugat
        plan.append(dict(sku=sku, Q=Q, deja=deja, nou=nou, manual=manual, alte=alte, de_scazut=de_scazut,
                         de_adaugat=de_adaugat, nescanat=nescanat, luari=luari, dcode=dcode, dcur=dcur,
                         tot=tot, dupa_tot=dupa_tot,
                         src_tot=sum(q for c, _, q in src_rows), dst_tot=sum(q for c, _, q in rows if ctx.station.get(c) == in_)))

    print("%-14s %6s %6s %8s %8s %9s %9s %9s  %-30s %s" % (
        "SKU", "cerut", "deja", "scan.%s" % in_[:4], "nescan.", "SCAD %s" % din[:4], "ADAUG %s" % in_[:4],
        "%s tot" % din[:4], "de unde (înainte->după)", "total depozit"))
    for p in plan:
        de_unde = "; ".join("%s %d->%d" % (ctx.name.get(c, c) if not c.startswith("LOC-") else c, q, q - t)
                            for c, s, q, t in p["luari"]) or "-"
        if p["de_adaugat"]:
            de_unde += "; +%s %d->%d" % (p["dcode"], p["dcur"], p["dcur"] + p["de_adaugat"])
        print("%-14s %6d %6d %8d %8d %9d %9d %9d  %-30s %d -> %d%s" % (
            p["sku"], p["Q"], p["deja"], p["nou"] + p["manual"], p["nescanat"], p["de_scazut"], p["de_adaugat"],
            p["src_tot"], de_unde[:60], p["tot"], p["dupa_tot"], "  ⚠️ ZERO" if p["dupa_tot"] <= 0 else ""))
    print("\nTotal: de scăzut la %s %d buc, de adăugat la %s %d buc, rămas nescanat (neatins) %d buc." % (
        din, sum(p["de_scazut"] for p in plan), in_, sum(p["de_adaugat"] for p in plan), sum(p["nescanat"] for p in plan)))
    for p in plan:
        if p["deja"]:
            print("   · %s: %d deja scăzute la sursă (aplicație „din … · %s\" / transfer / MOTIV mutare) — nu se mai scad."
                  % (p["sku"], p["deja"], din))
        if p["nescanat"]:
            print("   · %s: %d NU apar încă pe rafturile %s — rămân numărate la %s (dacă le scazi acum, dispar de pe "
                  "site). Rulează din nou după ce scanează, sau fara_scanare=true dacă ownerul confirmă."
                  % (p["sku"], p["nescanat"], in_, din))
        if p["manual"]:
            print("   · %s: %d la %s au notă manuală de mutare — verifică dacă sursa n-a fost deja scăzută."
                  % (p["sku"], p["manual"], in_))
        if p["alte"]:
            print("   · %s: ATENȚIE %d buc scăzute la %s fără motiv de mutare în fereastră (poate o corectură, poate "
                  "mutarea făcută de mână) — NU le împerechez automat; verifică." % (p["sku"], p["alte"], din))
        if p["dupa_tot"] <= 0:
            print("   · %s: ajunge la 0 pe toate rafturile -> zero_auto pune masterul pe 0 în ~5 min (magazinele nu-l "
                  "mai vând)." % p["sku"])

    parti = sorted((p["sku"], p["de_scazut"], p["de_adaugat"]) for p in plan)
    key = cheie("mutare", [din, in_, parti])
    rep = repetare(key, a.ore)
    if rep and not a.force:
        blocaje.append("ACEEAȘI mutare a fost scrisă la %s (backup %s). Refuz repetarea în %dh fără force=true."
                       % (rep["ts"][:16], rep.get("backup"), a.ore))
    print("\nCheie idempotență: %s" % key)
    if blocaje:
        print("\n⛔ OPRIT — nimic scris:" if a.apply else "\n⛔ De rezolvat înainte de apply:")
        for b in blocaje:
            print("   - " + b)
        if a.apply:
            ctx.con.execute("ROLLBACK")
            sys.exit(3)
    if not a.apply:
        print("\n[DRY-RUN] nimic scris. apply=true scrie exact coloanele SCAD/ADAUG (backup locations.db + jurnal).")
        print("Masterul stock-sync se actualizează la următoarea rulare awb_to_stocksync (L–V 15:30 / 23:30).")
        return
    if not any(p["de_scazut"] or p["de_adaugat"] for p in plan):
        ctx.con.execute("ROLLBACK")
        print("\nNimic de scris.")
        return
    bak = backup("mutare")
    ts = acum().isoformat(timespec="seconds")
    motiv = "mutare %s -> %s (%s)%s" % (din, in_, ts[:10], ("; " + a.motiv) if a.motiv else "")
    scrise = []
    try:
        for p in plan:
            if p["de_adaugat"]:                         # ADAUGĂ întâi (mutare-adauga-intai-scade-dupa)
                h, n = pune(ctx, p["dcode"], p["sku"], p["de_adaugat"], "corectie-owner",
                            motiv + " - adus de la " + din, ts)
                scrise.append({"sku": p["sku"], "code": p["dcode"], "inainte": h, "dupa": n})
            for c, s, q, t in p["luari"]:
                h, n = scade(ctx, c, s, t, "corectie-owner",
                             motiv + (" - destinatia scaneaza singura pe raft" if not la_dest else ""), ts)
                scrise.append({"sku": s, "code": c, "inainte": h, "dupa": n})
        ctx.con.execute("COMMIT")
    except Exception:
        ctx.con.execute("ROLLBACK")
        raise
    scrie_jurnal({"ts": ts, "op": "mutare", "key": key, "din": din, "in": in_, "backup": bak, "motiv": a.motiv,
                  "scrise": scrise})
    print("\n✅ SCRIS (%s). Backup: %s" % (ts[:16], bak))
    for s in scrise:
        print("   %-14s %-16s %6d -> %6d" % (s["sku"], s["code"], s["inainte"], s["dupa"]))
    print("Jurnal: %s. Masterul se actualizează la următoarea rulare awb_to_stocksync." % JURNAL)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("receptie")
    r.add_argument("--container", default="")
    r.add_argument("--statie", required=True)
    r.add_argument("--lista", default="")
    r.add_argument("--include-grandia", action="store_true")
    r.add_argument("--include-necunoscute", action="store_true")
    r.add_argument("--de-la", default="")
    r.add_argument("--zile", type=int, default=10, help="cât după sosire se socotesc punerile pe raft drept recepție")
    m = sub.add_parser("mutare")
    m.add_argument("--lista", required=True)
    m.add_argument("--din", required=True)
    m.add_argument("--in", dest="in_", required=True)
    m.add_argument("--rafturi", default="")
    m.add_argument("--de-la", default="")
    m.add_argument("--fara-scanare", action="store_true")
    m.add_argument("--prag", type=int, default=5, help="lista=azi: minim buc/SKU scanate nou ca să intre în listă")
    for p in (r, m):
        p.add_argument("--motiv", default="")
        p.add_argument("--ore", type=int, default=24)
        p.add_argument("--force", action="store_true")
        p.add_argument("--apply", action="store_true")
        p.add_argument("--db", default="", help=argparse.SUPPRESS)        # test pe o COPIE a bazei
        p.add_argument("--jurnal", default="", help=argparse.SUPPRESS)
    a = ap.parse_args()
    global LOC_DB, JURNAL
    LOC_DB, JURNAL = a.db or LOC_DB, a.jurnal or JURNAL
    receptie(a) if a.cmd == "receptie" else mutare(a)


if __name__ == "__main__":
    main()
