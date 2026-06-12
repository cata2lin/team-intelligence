# /// script
# requires-python = ">=3.10"
# dependencies = ["pg8000>=1.30"]
# ///
"""
richpanel_apply.py — împinge ENRICHMENT-ul din DB înapoi în Richpanel, ca să-l vadă agenții nativ:
  • TAGURI pe conversație: magazin / categorie / sentiment / flag-uri / lead-reclamație / awb-trimis
  • UN private note INTERN (invizibil clientului) cu identitatea + comanda + status + tracking DPD

⚠️ NU atinge NICIODATĂ răspunsul către client (fără mesaj, fără draft) și NU schimbă
status/assignee/prioritate — deci nu poate încurca agentul când scrie. Doar taguri + 1 notă internă.

Incremental + update-on-change: reține ce-a aplicat (applied_tags / applied_note_sig) și scrie DOAR
delta. Re-rulat des (intraday) = puține scrieri → sub rate limit. Nota se re-pune doar dacă
se schimbă comanda/AWB/status (marcată „🔄 UPDATE"), niciodată spam.

  uv run richpanel_apply.py                     # DRY-RUN pe tichetele OPEN (ce AR scrie)
  uv run richpanel_apply.py --apply             # scrie taguri + notă (OPEN)
  uv run richpanel_apply.py --recent 1 --apply  # doar tichete schimbate în ultima zi (intraday)
  uv run richpanel_apply.py --limit 50 --json
Read-only pe Richpanel în lipsa lui --apply. Rulează pe VPS (are SQLite local + profitability.db).
"""
import os, re, sys, json, time, hashlib, sqlite3, subprocess, urllib.parse, urllib.request, argparse, collections

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", ".."))
DB = os.environ.get("RICHPANEL_DB") or os.path.join(REPO, "data", "richpanel_tickets.db")
PROFIT_DB = os.path.join(REPO, "data", "profitability.db")
KB = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
MCP_URL = "https://mcp.richpanel.com/mcp"
import rp_db  # citește enrichment-ul (SQLite local pe VPS)

DPD_TRACK = "https://services.dpd.ro/tracking/?shipmentNumber=%s"
NOISE_CAT = {"spam_automat", "salut_fara_continut", "formular_contact"}  # nu merită taguite
# Richpanel normalizează tagurile (lowercase, scoate „:" și spațiile, păstrează „-").
# Deci formăm slug-uri cu cratimă: magazin-esteban, magazin-george-talent, flag-frustrare.
FLAG_TAG = {"ESCALADARE": "escaladare", "FRUSTRARE": "frustrare", "LENT": "lent",
            "VECHI-DESCHIS": "vechi-deschis", "FRICTIUNE": "frictiune"}
CTYPE_TAG = {"lead": "lead", "reclamatie": "reclamatie", "testimonial": "testimonial"}
_TAG_DEACC = str.maketrans("ăâîșşțţ", "aaisstt")


def slug(s):
    s = (s or "").lower().translate(_TAG_DEACC)
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def secret(k):
    return os.environ.get(k) or subprocess.run(["uv", "run", KB, "secret-get", k], capture_output=True, text=True).stdout.strip()


class MCP:
    def __init__(self, token):
        self.t = token
        self._post({"jsonrpc": "2.0", "id": 0, "method": "initialize",
                    "params": {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "apply", "version": "1"}}})

    def _post(self, p):
        h = {"Authorization": "Bearer " + self.t, "Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        req = urllib.request.Request(MCP_URL, data=json.dumps(p).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=60) as r:
            body = r.read().decode()
        ln = [l for l in body.splitlines() if l.startswith("data:")]
        return json.loads(ln[-1][5:]) if ln else json.loads(body)

    def call(self, name, args):
        try:
            r = self._post({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
            txt = r["result"]["content"][0]["text"]
            try:
                return json.loads(txt)
            except Exception:
                return {"_text": txt}
        except Exception as e:
            return {"_error": str(e)}


def load_tag_map(mcp):
    """name(lower) -> id, din list_tags (acceptă mai multe forme de răspuns)."""
    r = mcp.call("list_tags", {})
    items = r if isinstance(r, list) else (r.get("tags") or r.get("results") or r.get("data") or [])
    m = {}
    for t in items if isinstance(items, list) else []:
        if isinstance(t, dict):
            nm, tid = t.get("name") or t.get("label"), t.get("id") or t.get("tag_id") or t.get("_id")
            if nm and tid:
                m[nm.strip().lower()] = tid
    return m


def tag_id(mcp, tagmap, name):
    """id-ul tagului; îl creează dacă lipsește. None dacă nu reușește."""
    k = name.strip().lower()
    if k in tagmap:
        return tagmap[k]
    r = mcp.call("create_tag", {"name": name})
    tid = None
    if isinstance(r, dict):
        tid = r.get("id") or r.get("tag_id")
        if not tid and isinstance(r.get("tag"), dict):
            tid = r["tag"].get("id")
    if not tid:  # fallback robust: reîncarcă lista și caută după nume
        tagmap.update(load_tag_map(mcp))
        tid = tagmap.get(k)
    if tid:
        tagmap[k] = tid
    return tid


NOT_STORE = {"nocturna", "operator", "admin"}  # handle-uri de agent care s-au scurs uneori în resolved_store


def desired_tags(store, cat, sentiment, qflags, ctype, has_awb):
    tags = []
    store = " ".join((store or "").split())  # fără spații la coadă/duble
    if store and store.lower() not in NOT_STORE and store not in ("necunoscut", "?", ""):
        tags.append("magazin-" + slug(store))
    if cat and cat not in NOISE_CAT and cat not in ("altele", "", None):
        tags.append("cat-" + slug(cat))
    if sentiment in ("negativ", "pozitiv"):
        tags.append("sentiment-" + sentiment)
    for f in (qflags or "").split(","):
        if f in FLAG_TAG:
            tags.append("flag-" + FLAG_TAG[f])
    if ctype in CTYPE_TAG:
        tags.append(CTYPE_TAG[ctype])
    if has_awb:
        tags.append("awb-trimis")
    # set ordonat, fără dubluri
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t); out.append(t)
    return out


def build_note(name, store, order, prof, qflags):
    """notă internă concisă pt agent. (text, sig) sau (None,None) dacă nu-i nimic util."""
    if not order:
        return None, None
    p = prof.get(order, {})
    status = p.get("st") or "?"
    awb = p.get("awb") or ""
    skus = (p.get("skus") or "")[:70]
    lines = ["🤖 [auto] profil identificat de sistem (intern)"]
    who = name or "(necunoscut)"
    lines.append("Client: %s · %s" % (who, store or "?"))
    lines.append("Comandă %s — status: %s%s" % (order, status, (" · " + skus) if skus else ""))
    if awb:
        lines.append("📦 Tracking DPD: " + (DPD_TRACK % awb))
    fl = [FLAG_TAG[f].split(":")[-1] for f in (qflags or "").split(",") if f in FLAG_TAG]
    if fl:
        lines.append("⚠ semnale: " + ", ".join(fl))
    sig = hashlib.sha1(("%s|%s|%s" % (order, status, awb)).encode()).hexdigest()[:12]
    return "\n".join(lines), sig


def load_profit(orders):
    """status_category/awb/skus pt comenzile date — din profitability.db local (VPS)."""
    out = {}
    if not orders or not os.path.exists(PROFIT_DB):
        return out
    pc = sqlite3.connect("file:" + PROFIT_DB + "?mode=ro", uri=True, timeout=30)
    lst = list(orders)
    for i in range(0, len(lst), 900):
        chunk = lst[i:i + 900]
        q = "SELECT order_name,status_category,awb,skus FROM profit_orders WHERE order_name IN (%s)" % ",".join("?" * len(chunk))
        for on, st, awb, skus in pc.execute(q, chunk):
            out[on] = {"st": st, "awb": awb, "skus": skus}
    pc.close()
    return out


def ensure_cols():
    w = sqlite3.connect(DB, timeout=60)
    w.execute("PRAGMA busy_timeout=60000")
    for c in ("applied_tags", "applied_note_sig"):
        try:
            w.execute("ALTER TABLE tickets ADD COLUMN %s TEXT" % c)
        except Exception:
            pass
    w.commit()
    return w


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--recent", type=int, help="doar tichete updatate în ultimele N zile (intraday)")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--apply", action="store_true", help="scrie efectiv în Richpanel (altfel DRY-RUN)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if not os.path.exists(DB):
        print("Rulează pe VPS (lipsește SQLite-ul local %s)." % DB); sys.exit(1)

    con = rp_db.open(DB)
    cols = {r[1] for r in con.execute("PRAGMA table_info(tickets)")}
    at = "applied_tags" if "applied_tags" in cols else "NULL"
    an = "applied_note_sig" if "applied_note_sig" in cols else "NULL"
    where = "upper(status)='OPEN'"
    params = ()
    if a.recent:
        where = "(upper(status)='OPEN' OR updated_at >= date('now', ?))"
        params = ("-%d days" % a.recent,)
    rows = con.execute(
        "SELECT id,conversation_no,resolved_store,category,sentiment,quality_flags,comment_type,"
        "match_order,customer_name,%s,%s FROM tickets WHERE %s" % (at, an, where), params).fetchall()
    con.close()
    if a.limit:
        rows = rows[:a.limit]

    prof = load_profit({r[7] for r in rows if r[7]})

    plan = []  # (id, conv, add[], rem[], note_or_None, note_sig, desired[])
    for (tid, conv, store, cat, sent, qflags, ctype, order, cname, atags, anote) in rows:
        has_awb = bool(prof.get(order, {}).get("awb")) if order else False
        D = desired_tags(store, cat, sent, qflags, ctype, has_awb)
        note, sig = build_note(cname, store, order, prof, qflags)
        A = [t for t in (atags or "").split(",") if t]
        add = [t for t in D if t not in A]
        rem = [t for t in A if t not in D]
        note_due = bool(note) and sig != (anote or "")
        if add or rem or note_due:
            plan.append((tid, conv, add, rem, (note if note_due else None), sig, D, bool(anote)))

    if a.json:
        print(json.dumps([{"conv": p[1], "add": p[2], "rem": p[3], "note": bool(p[4])} for p in plan], ensure_ascii=False, indent=1))
        return

    head = "APLICAT în Richpanel" if a.apply else "DRY-RUN (nu scriu nimic)"
    print("═" * 78)
    print("  RICHPANEL APPLY — %s | %d tichete în scope | %d de actualizat" % (head, len(rows), len(plan)))
    print("═" * 78)
    cnt = collections.Counter()
    for p in plan[:40]:
        print("  #%-7s +[%s]%s%s" % (p[1] or "?", ",".join(p[2]),
              ("  -[%s]" % ",".join(p[3])) if p[3] else "", "  📝nota" if p[4] else ""))
    if len(plan) > 40:
        print("  … încă %d" % (len(plan) - 40))

    if not a.apply:
        print("\n  → rulează cu --apply ca să scrie (taguri + notă internă; niciun mesaj la client).")
        return

    mcp = MCP(secret("RICHPANEL_MCP_TOKEN"))
    tagmap = load_tag_map(mcp)
    w = ensure_cols()
    done = nadd = nrem = nnote = 0
    for (tid, conv, add, rem, note, sig, D, had_note) in plan:
        cid = tid
        if add:
            ids = [i for i in (tag_id(mcp, tagmap, t) for t in add) if i]
            if ids:
                mcp.call("add_tags_to_conversation", {"conversation_id": cid, "tags": ids}); nadd += 1; cnt.update(add)
        if rem:
            ids = [tagmap[t.lower()] for t in rem if t.lower() in tagmap]
            if ids:
                mcp.call("remove_tags_from_conversation", {"conversation_id": cid, "tags": ids}); nrem += 1
        if note:
            body = note if not had_note else ("🔄 UPDATE\n" + note)
            mcp.call("add_private_note", {"conversation_id": cid, "body": body}); nnote += 1
        w.execute("UPDATE tickets SET applied_tags=?, applied_note_sig=? WHERE id=?", (",".join(D), sig, tid))
        done += 1
        if done % 100 == 0:
            w.commit(); print("  …%d/%d aplicate" % (done, len(plan)), flush=True)
    w.commit(); w.close()
    print("\n════ %d tichete actualizate | %d cu taguri noi, %d curățate, %d note ════" % (done, nadd, nrem, nnote))
    print("  taguri puse:", dict(cnt.most_common(12)))


if __name__ == "__main__":
    main()
