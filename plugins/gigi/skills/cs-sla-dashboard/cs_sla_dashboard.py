# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""
cs_sla_dashboard.py — Dashboard SLA Richpanel (READ-ONLY pe sumar).

Citește LIVE din analytics-ul Richpanel (MCP query_analytics, business-hours) și arată
unde stă rău Customer Service-ul:
  • VOLUM NOU + BACKLOG per CANAL (email, facebook_feed_comment, messenger, instagram…)
  • FRT MEDIAN (first response time, median p50) + % NEATINSE per canal și per AGENT
  • AGENȚII SUPRAÎNCĂRCAȚI (volum nou mare, backlog mare, % neatinse mare)

ATENȚIE date Richpanel: toate duratele sunt în MILISECUNDE; sufixul _bh = doar ore
de program (business hours). Aici converim ms→ore citibil.

  uv run cs_sla_dashboard.py                       # sumar (canale + agenți + alerte)
  uv run cs_sla_dashboard.py --days 7              # alt interval (default 30 zile)
  uv run cs_sla_dashboard.py --channel             # detaliu pe canale
  uv run cs_sla_dashboard.py --agent               # detaliu pe agenți
  uv run cs_sla_dashboard.py --json                # ieșire JSON (pt automatizări)

TRIAJ (opțional, intern — NICIODATĂ mesaj la client):
  uv run cs_sla_dashboard.py --triage             # DRY-RUN: ce tichete AR marca + ce prioritate
  uv run cs_sla_dashboard.py --triage --apply     # chiar setează prioritate HIGH + tag (intern)

REGULI DE SIGURANȚĂ:
  - DEFAULT = doar citire. Nu scrie nimic în Richpanel.
  - --triage fără --apply = DRY-RUN (arată ce ar face, nu scrie).
  - --triage --apply = scrie DOAR operații interne (prioritate HIGH + tag de triaj).
  - NICIODATĂ nu trimite mesaj/răspuns la client. Nu există cale către send_message aici.
"""
import os, sys, json, time, argparse, subprocess, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MCP_URL = "https://mcp.richpanel.com/mcp"

# tag intern folosit la triaj (--apply). Doar pt organizare internă, nu se vede la client.
TRIAGE_TAG_NAME = "sla-backlog-urgent"

# Prag „supraîncărcat" la nivel de agent (pe interval): volum nou mare + multe neatinse.
OVERLOAD_NEW = 1000          # >1000 conversații noi alocate / interval
OVERLOAD_UNATTENDED_PCT = 25  # ≥25% neatinse
OVERLOAD_BACKLOG = 500        # backlog ≥500

# nume citibile pt canalele Richpanel
CHANNEL_RO = {
    "email": "Email",
    "email_from_widget": "Email (widget)",
    "facebook_message": "Facebook Messenger (msg)",
    "facebook_feed_comment": "Facebook (comentarii)",
    "instagram_comment": "Instagram (comentarii)",
    "instagram_message": "Instagram (DM)",
    "messenger": "Messenger (chat)",
}


def fmt_dur(ms):
    """Convertește milisecunde → text citibil (min / h / zile)."""
    if ms is None:
        return "—"
    try:
        ms = float(ms)
    except (TypeError, ValueError):
        return "—"
    if ms <= 0:
        return "0h"
    h = ms / 3_600_000.0
    if h < 1:
        return "%.0f min" % (ms / 60_000.0)
    if h < 48:
        return "%.1fh" % h
    return "%.1f zile" % (h / 24.0)


def pct(part, whole):
    return (100.0 * part / whole) if whole else 0.0


def secret(k):
    v = os.environ.get(k)
    if v:
        return v
    kb = os.path.join(HERE, "..", "..", "..", "core", "scripts", "kb.py")
    return subprocess.run(["uv", "run", kb, "secret-get", k], capture_output=True, text=True).stdout.strip()


class MCP:
    """JSON-RPC către endpoint-ul MCP Richpanel (parsare SSE: ultima linie 'data:')."""

    def __init__(self, token):
        self.token = token
        self._init()

    def _post(self, payload):
        h = {"Authorization": "Bearer " + self.token, "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        req = urllib.request.Request(MCP_URL, data=json.dumps(payload).encode(), headers=h)
        with urllib.request.urlopen(req, timeout=90) as r:
            body = r.read().decode()
        lines = [l for l in body.splitlines() if l.startswith("data:")]
        return json.loads(lines[-1][5:]) if lines else json.loads(body)

    def _init(self):
        self._post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "arona-sla-dashboard", "version": "1.0"}}})

    def call(self, name, args, retries=3):
        last = None
        for i in range(retries):
            try:
                res = self._post({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                  "params": {"name": name, "arguments": args}})
                if res.get("error"):
                    raise RuntimeError("JSON-RPC error: %s" % res["error"])
                txt = res.get("result", {}).get("content", [{}])[0].get("text", "")
                return json.loads(txt) if txt.strip().startswith("{") else {}
            except Exception as e:
                last = e
                if i == retries - 1:
                    raise
                time.sleep(3 * (i + 1))
                try:
                    self._init()
                except Exception:
                    pass
        raise last  # pragma: no cover


def metric_map(metrics):
    """[{name,value,...}] → {name: value}."""
    return {m["name"]: m.get("value") for m in (metrics or [])}


def query(mcp, metrics, dimension=None, days=30):
    args = {"metrics": metrics, "period_days": days}
    if dimension:
        args["dimensions"] = dimension
    d = mcp.call("query_analytics", args).get("data", {})
    rows = []
    for b in d.get("breakdowns", []):
        dims = b.get("dimensions", [])
        key = dims[0].get("value") if dims else None
        nm = dims[0].get("name") if dims else None
        rows.append({"key": key, "name": nm, "m": metric_map(b.get("metrics"))})
    totals = metric_map(d.get("aggregations", {}).get("totals"))
    period = d  # for startDate/endDate in context isn't in data; pull separately
    return rows, totals


def fetch(mcp, days):
    """Trage tot ce ne trebuie: per canal, per agent, total."""
    ch_metrics = ["new_conversations", "backlog", "closed_conversations",
                  "p50_first_response_time_bh", "unattended_new_conversations"]
    ag_metrics = ["new_conversations", "backlog", "closed_conversations",
                  "p50_first_response_time_bh", "first_response_time_bh",
                  "unattended_new_conversations", "awaiting_agent_reply"]
    channels, totals = query(mcp, ch_metrics, "channel", days)
    agents, _ = query(mcp, ag_metrics, "agent", days)
    return {"channels": channels, "agents": agents, "totals": totals}


def overloaded(a):
    m = a["m"]
    newc = m.get("new_conversations") or 0
    unatt = m.get("unattended_new_conversations") or 0
    backlog = m.get("backlog") or 0
    up = pct(unatt, newc)
    reasons = []
    if newc >= OVERLOAD_NEW:
        reasons.append("volum %d" % newc)
    if up >= OVERLOAD_UNATTENDED_PCT and unatt >= 50:
        reasons.append("%.0f%% neatinse" % up)
    if backlog >= OVERLOAD_BACKLOG:
        reasons.append("backlog %d" % backlog)
    return reasons


# ─────────────────────────── PREZENTARE ───────────────────────────

def print_summary(data, days):
    tot = data["totals"]
    newc = tot.get("new_conversations") or 0
    backlog = tot.get("backlog") or 0
    closed = tot.get("closed_conversations") or 0
    unatt = tot.get("unattended_new_conversations") or 0
    frt = tot.get("p50_first_response_time_bh")

    print("=" * 78)
    print("  DASHBOARD SLA RICHPANEL — ultimele %d zile  (ore = business hours)" % days)
    print("=" * 78)
    print("  Conversații noi: %-7d   Backlog (deschise): %-7d   Închise: %d" % (newc, backlog, closed))
    print("  FRT median (primă reacție): %-8s   Neatinse: %d  (%.0f%% din noi)"
          % (fmt_dur(frt), unatt, pct(unatt, newc)))
    print()

    # ── PER CANAL ──
    print("  PER CANAL — volum, backlog, FRT median, %% neatinse")
    print("  %-26s %7s %8s %9s %10s" % ("canal", "nou", "backlog", "FRT med", "neatinse"))
    print("  " + "-" * 70)
    chans = sorted(data["channels"], key=lambda r: -(r["m"].get("new_conversations") or 0))
    for r in chans:
        m = r["m"]
        nc = m.get("new_conversations") or 0
        bk = m.get("backlog") or 0
        un = m.get("unattended_new_conversations") or 0
        name = CHANNEL_RO.get(r["key"], r["key"] or "?")
        flag = "  <<" if (pct(un, nc) >= 60 and nc >= 100) or bk >= 1000 else ""
        print("  %-26s %7d %8d %9s %6d (%2.0f%%)%s"
              % (name[:26], nc, bk, fmt_dur(m.get("p50_first_response_time_bh")),
                 un, pct(un, nc), flag))
    print()

    # ── PER AGENT ──
    print("  PER AGENT — volum alocat, backlog, închise, FRT median, %% neatinse")
    print("  %-20s %7s %8s %8s %9s %10s" % ("agent", "nou", "backlog", "inchise", "FRT med", "neatinse"))
    print("  " + "-" * 74)
    ags = [a for a in data["agents"] if (a["m"].get("new_conversations") or 0) > 0
           or (a["m"].get("backlog") or 0) > 0]
    ags.sort(key=lambda a: -(a["m"].get("new_conversations") or 0))
    for a in ags:
        m = a["m"]
        nc = m.get("new_conversations") or 0
        bk = m.get("backlog") or 0
        cl = m.get("closed_conversations") or 0
        un = m.get("unattended_new_conversations") or 0
        name = a["name"] or a["key"] or "?"
        flag = "  <<" if overloaded(a) else ""
        print("  %-20s %7d %8d %8d %9s %6d (%2.0f%%)%s"
              % (name[:20], nc, bk, cl, fmt_dur(m.get("p50_first_response_time_bh")),
                 un, pct(un, nc), flag))
    print()

    # ── ALERTE ──
    print("  ALERTE")
    alerts = []
    for r in chans:
        m = r["m"]
        nc = m.get("new_conversations") or 0
        un = m.get("unattended_new_conversations") or 0
        bk = m.get("backlog") or 0
        if nc >= 200 and pct(un, nc) >= 70:
            alerts.append("Canal %s: %.0f%% neatinse (%d din %d noi) — backlog %d"
                          % (CHANNEL_RO.get(r["key"], r["key"]), pct(un, nc), un, nc, bk))
        elif bk >= 1000:
            alerts.append("Canal %s: backlog mare %d (FRT median %s)"
                          % (CHANNEL_RO.get(r["key"], r["key"]), bk, fmt_dur(m.get("p50_first_response_time_bh"))))
    for a in data["agents"]:
        rs = overloaded(a)
        if rs:
            alerts.append("Agent SUPRAÎNCĂRCAT %s: %s"
                          % (a["name"] or a["key"], ", ".join(rs)))
    if alerts:
        for al in alerts:
            print("   ! " + al)
    else:
        print("   (nicio alertă peste praguri)")
    print()
    print("  Detalii: --channel  |  --agent  |  --json   ·  Triaj intern (dry-run): --triage")


def print_channels(data, days):
    print("=" * 78)
    print("  CANALE — ultimele %d zile" % days)
    print("=" * 78)
    chans = sorted(data["channels"], key=lambda r: -(r["m"].get("new_conversations") or 0))
    for r in chans:
        m = r["m"]
        nc = m.get("new_conversations") or 0
        un = m.get("unattended_new_conversations") or 0
        print("\n  %s  [%s]" % (CHANNEL_RO.get(r["key"], r["key"] or "?"), r["key"]))
        print("    Noi: %d   Backlog: %d   Închise: %d"
              % (nc, m.get("backlog") or 0, m.get("closed_conversations") or 0))
        print("    FRT median (primă reacție): %s" % fmt_dur(m.get("p50_first_response_time_bh")))
        print("    Neatinse: %d  (%.0f%% din noi)" % (un, pct(un, nc)))


def print_agents(data, days):
    print("=" * 78)
    print("  AGENȚI — ultimele %d zile" % days)
    print("=" * 78)
    ags = [a for a in data["agents"] if (a["m"].get("new_conversations") or 0) > 0
           or (a["m"].get("backlog") or 0) > 0]
    ags.sort(key=lambda a: -(a["m"].get("new_conversations") or 0))
    for a in ags:
        m = a["m"]
        nc = m.get("new_conversations") or 0
        un = m.get("unattended_new_conversations") or 0
        rs = overloaded(a)
        tag = "  [SUPRAÎNCĂRCAT: %s]" % ", ".join(rs) if rs else ""
        print("\n  %s%s" % (a["name"] or a["key"], tag))
        print("    Volum nou alocat: %d   Backlog: %d   Închise: %d"
              % (nc, m.get("backlog") or 0, m.get("closed_conversations") or 0))
        print("    FRT median: %s   (medie: %s)"
              % (fmt_dur(m.get("p50_first_response_time_bh")), fmt_dur(m.get("first_response_time_bh"))))
        print("    Neatinse: %d  (%.0f%%)   Așteaptă răspuns agent: %d"
              % (un, pct(un, nc), m.get("awaiting_agent_reply") or 0))


def build_json(data, days):
    def chrow(r):
        m = r["m"]
        nc = m.get("new_conversations") or 0
        un = m.get("unattended_new_conversations") or 0
        frt = m.get("p50_first_response_time_bh")
        return {"channel": r["key"], "label": CHANNEL_RO.get(r["key"], r["key"]),
                "new": nc, "backlog": m.get("backlog") or 0,
                "closed": m.get("closed_conversations") or 0,
                "frt_median_ms": frt, "frt_median_h": (frt / 3_600_000.0) if frt else 0,
                "unattended": un, "unattended_pct": round(pct(un, nc), 1)}

    def agrow(a):
        m = a["m"]
        nc = m.get("new_conversations") or 0
        un = m.get("unattended_new_conversations") or 0
        frt = m.get("p50_first_response_time_bh")
        return {"agent_id": a["key"], "agent": a["name"],
                "new": nc, "backlog": m.get("backlog") or 0,
                "closed": m.get("closed_conversations") or 0,
                "frt_median_ms": frt, "frt_median_h": (frt / 3_600_000.0) if frt else 0,
                "frt_avg_ms": m.get("first_response_time_bh"),
                "unattended": un, "unattended_pct": round(pct(un, nc), 1),
                "awaiting_agent_reply": m.get("awaiting_agent_reply") or 0,
                "overloaded": overloaded(a)}

    t = data["totals"]
    return {
        "period_days": days,
        "totals": {"new": t.get("new_conversations") or 0,
                   "backlog": t.get("backlog") or 0,
                   "closed": t.get("closed_conversations") or 0,
                   "unattended": t.get("unattended_new_conversations") or 0,
                   "frt_median_ms": t.get("p50_first_response_time_bh"),
                   "frt_median_h": (t.get("p50_first_response_time_bh") / 3_600_000.0)
                   if t.get("p50_first_response_time_bh") else 0},
        "channels": [chrow(r) for r in sorted(data["channels"],
                     key=lambda r: -(r["m"].get("new_conversations") or 0))],
        "agents": [agrow(a) for a in sorted(data["agents"],
                   key=lambda a: -(a["m"].get("new_conversations") or 0))
                   if (a["m"].get("new_conversations") or 0) > 0 or (a["m"].get("backlog") or 0) > 0],
    }


# ─────────────────────────── TRIAJ (intern, gated) ───────────────────────────

def get_tag_id(mcp, name, create=False):
    """Caută tag-ul după nume; întoarce id sau None. Creează doar dacă create=True."""
    d = mcp.call("list_tags", {})
    tags = d.get("tags") or d.get("data") or (d if isinstance(d, list) else [])
    if isinstance(tags, dict):
        tags = tags.get("tags") or []
    for t in tags:
        nm = (t.get("name") or "").lower()
        if nm == name.lower().replace(" ", "-"):
            return t.get("id") or t.get("tag_id")
    if create:
        r = mcp.call("create_tag", {"name": name, "description": "SLA dashboard: backlog urgent (intern)"})
        return r.get("id") or (r.get("tag") or {}).get("id")
    return None


def triage(mcp, days, apply):
    """Identifică tichetele cele mai vechi din backlog pe canalele cu cel mai mare % neatinse
    și (DRY-RUN by default) arată ce AR face: prioritate HIGH + tag intern.
    --apply scrie DOAR operații interne (prioritate + tag). NICIODATĂ mesaj la client."""
    print("=" * 78)
    print("  TRIAJ BACKLOG — %s" % ("APPLY (scrie prioritate+tag intern)" if apply else "DRY-RUN (nu scrie nimic)"))
    print("=" * 78)
    print("  Operații permise: setare prioritate HIGH + tag intern '%s'." % TRIAGE_TAG_NAME)
    print("  NICIODATĂ nu se trimite mesaj/răspuns la client.\n")

    # canalul cu cel mai mare backlog & % neatinse mare = ținta de triaj
    data = fetch(mcp, days)
    cand = []
    for r in data["channels"]:
        m = r["m"]
        nc = m.get("new_conversations") or 0
        un = m.get("unattended_new_conversations") or 0
        bk = m.get("backlog") or 0
        if bk >= 100 and pct(un, nc) >= 50:
            cand.append((bk, r["key"]))
    cand.sort(reverse=True)
    if not cand:
        print("  Niciun canal peste praguri (backlog≥100 & neatinse≥50%). Nimic de triat.")
        return
    targets = [k for _, k in cand[:3]]
    print("  Canale-țintă (backlog mare + multe neatinse): %s\n" % ", ".join(targets))

    tag_id = get_tag_id(mcp, TRIAGE_TAG_NAME, create=apply) if apply else None

    total_seen = 0
    for ch in targets:
        # cele mai vechi conversații DESCHISE pe canal (oldest first)
        d = mcp.call("list_conversations", {"status": "OPEN", "channel": ch,
                                            "per_page": 25, "sortKey": "createdAt", "order": "asc"})
        convs = d.get("tickets") or d.get("conversations") or d.get("data") or []
        if isinstance(convs, dict):
            convs = convs.get("tickets") or []
        print("  Canal %s — %d tichete vechi deschise (afișez max 15):" % (ch, len(convs)))
        for c in convs[:15]:
            total_seen += 1
            cid = c.get("id") or c.get("conversation_id")
            no = c.get("conversation_no") or c.get("ticket_id") or cid
            subj = (c.get("subject") or c.get("first_message") or "")[:48].replace("\n", " ")
            created = c.get("created_at") or ""
            if apply:
                try:
                    mcp.call("update_conversation", {"conversation_id": cid, "priority": "HIGH"})
                    if tag_id:
                        mcp.call("add_tags_to_conversation", {"conversation_id": cid, "tags": [tag_id]})
                    act = "APLICAT: prioritate HIGH + tag"
                except Exception as e:
                    act = "EROARE: %s" % e
            else:
                act = "AR seta: prioritate HIGH + tag '%s'" % TRIAGE_TAG_NAME
            print("    #%-8s %-50s %s | %s" % (str(no), subj, str(created)[:10], act))
        print()
    print("  Total tichete %s: %d" % ("procesate" if apply else "care AR fi atinse", total_seen))
    if not apply:
        print("  (Nimic scris. Pentru a aplica efectiv: adaugă --apply — doar prioritate+tag, fără mesaje.)")


def main():
    ap = argparse.ArgumentParser(description="Dashboard SLA Richpanel (read-only).")
    ap.add_argument("--days", type=int, default=30, help="interval în zile (default 30)")
    ap.add_argument("--channel", action="store_true", help="detaliu pe canale")
    ap.add_argument("--agent", action="store_true", help="detaliu pe agenți")
    ap.add_argument("--json", action="store_true", help="ieșire JSON")
    ap.add_argument("--triage", action="store_true", help="triaj backlog (DRY-RUN by default)")
    ap.add_argument("--apply", action="store_true",
                    help="cu --triage: scrie efectiv prioritate+tag intern (NICIODATĂ mesaj la client)")
    a = ap.parse_args()

    tok = secret("RICHPANEL_MCP_TOKEN")
    if not tok:
        print("Lipsește RICHPANEL_MCP_TOKEN în KB.", file=sys.stderr)
        sys.exit(1)
    mcp = MCP(tok)

    if a.triage:
        if a.apply:
            print("  [APPLY activ] — scriu DOAR operații interne (prioritate+tag). Niciun mesaj la client.\n")
        triage(mcp, a.days, a.apply)
        return

    data = fetch(mcp, a.days)
    if a.json:
        print(json.dumps(build_json(data, a.days), ensure_ascii=False, indent=2))
        return
    if a.channel:
        print_channels(data, a.days)
        return
    if a.agent:
        print_agents(data, a.days)
        return
    print_summary(data, a.days)


if __name__ == "__main__":
    main()
