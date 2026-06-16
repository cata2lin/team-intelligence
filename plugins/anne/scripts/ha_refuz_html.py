# /// script
# requires-python = ">=3.10"
# dependencies = ["paramiko>=3.0", "psycopg2-binary>=2.9"]
# ///
"""
Genereaza un fisier HTML cu analiza refuz HA si il deschide in browser.
"""
import json, os, sys, webbrowser, tempfile
import psycopg2, paramiko

REMOTE_PYTHON = "/root/Scripturi/.venv/bin/python"
SSH_HOST_KEY  = "PROFIT_SSH_HOST"
SSH_USER_KEY  = "PROFIT_SSH_USER"
SSH_PASS_KEY  = "PROFIT_SSH_PASS"

QUERY_SCRIPT = r"""
import sqlite3, json, os
os.chdir('/root/Scripturi')
db = sqlite3.connect('data/profitability.db')

PERIODS = [
    ("7d",  "date('now', '-7 days')",  None),
    ("30d", "date('now', '-30 days')", "date('now', '-7 days')"),
    ("90d", "date('now', '-90 days')", "date('now', '-30 days')"),
    ("old", None,                      "date('now', '-90 days')"),
]

results = {}
for period_name, date_from, date_to in PERIODS:
    where_parts = ["skus LIKE 'HA-%'",
                   "status_category IN ('Refuzata', 'Livrata', 'Anulata')"]
    if date_from:
        where_parts.append(f"DATE(created_at) >= {date_from}")
    if date_to:
        where_parts.append(f"DATE(created_at) < {date_to}")
    where_clause = " AND ".join(where_parts)
    rows = db.execute(f'''
        SELECT skus, status_category, COUNT(*) as cnt
        FROM profit_orders WHERE {where_clause}
        GROUP BY skus, status_category
    ''').fetchall()
    data = {}
    for skus_val, cat, cnt in rows:
        sku = skus_val.strip()
        if ";" in sku: continue
        if sku not in data:
            data[sku] = {"livrata": 0, "refuz": 0, "anulata": 0}
        if cat == 'Livrata':   data[sku]["livrata"] += cnt
        elif cat == 'Anulata': data[sku]["anulata"] += cnt
        elif cat == 'Refuzata': data[sku]["refuz"] += cnt
    period_out = {}
    for sku, v in data.items():
        liv, ref, an = v["livrata"], v["refuz"], v["anulata"]
        trimise = liv + ref
        period_out[sku] = {
            "livrata": liv, "refuz": ref, "anulata": an,
            "total": trimise,
            "refuz_pct": round(ref / trimise * 100, 1) if trimise else None,
        }
    results[period_name] = period_out

print(json.dumps(results))
"""

PERIODS_ORDER = ["old", "90d", "30d", "7d"]
PERIOD_LABELS = {
    "old": "91+ zile",
    "90d": "31–90 zile",
    "30d": "8–30 zile",
    "7d":  "Ult. 7 zile",
}


def kb_get(key):
    url = os.environ.get("KB_DATABASE_URL")
    if not url: return None
    try:
        conn = psycopg2.connect(url, connect_timeout=10)
        with conn, conn.cursor() as cur:
            cur.execute("SELECT value FROM secrets WHERE key=%s", (key,))
            row = cur.fetchone()
            return row[0] if row else None
    except Exception: return None
    finally: conn.close()


def run_remote():
    host = kb_get(SSH_HOST_KEY) or "84.46.242.181"
    user = kb_get(SSH_USER_KEY) or "root"
    pwd  = kb_get(SSH_PASS_KEY)
    if not pwd: sys.exit(f"Lipseste secretul {SSH_PASS_KEY}")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(host, username=user, password=pwd, timeout=30)
    sftp = client.open_sftp()
    with sftp.open("/tmp/_ha_html_q.py", "w") as f:
        f.write(QUERY_SCRIPT)
    sftp.close()
    _, stdout, stderr = client.exec_command(f"{REMOTE_PYTHON} /tmp/_ha_html_q.py", timeout=120)
    raw = stdout.read().decode()
    errs = stderr.read().decode().strip()
    client.close()
    if errs: print(f"[remote stderr]: {errs[:200]}", file=sys.stderr)
    return json.loads(raw)


def pct_color(val):
    if val is None: return "#f0f0f0", "#888"
    if val >= 40:   return "#c0392b", "#fff"
    if val >= 30:   return "#e67e22", "#fff"
    if val >= 20:   return "#f1c40f", "#333"
    if val >= 10:   return "#a8d8a8", "#333"
    return "#27ae60", "#fff"


def trend_str(vals):
    non_null = [(i, v) for i, v in enumerate(vals) if v is not None]
    if len(non_null) < 2: return "", ""
    old_v, new_v = non_null[0][1], non_null[-1][1]
    diff = new_v - old_v
    if abs(diff) < 0.5: return "→ stabil", "#888"
    if diff < 0: return f"▼ {abs(diff):.1f}pp", "#27ae60"
    return f"▲ +{diff:.1f}pp", "#c0392b"


def build_html(data):
    all_skus = set()
    for pd in data.values():
        all_skus.update(pd.keys())

    def total_orders(sku):
        return sum(data.get(p, {}).get(sku, {}).get("total", 0) for p in PERIODS_ORDER)

    skus = sorted(
        [s for s in all_skus if total_orders(s) >= 30],
        key=lambda s: (data.get("90d", {}).get(s, {}).get("refuz_pct") or
                       data.get("30d", {}).get(s, {}).get("refuz_pct") or 0),
        reverse=True,
    )

    # Aggregate per period
    agg = {}
    for p in PERIODS_ORDER:
        pd = data.get(p, {})
        liv = ref = an = 0
        for v in pd.values():
            liv += v["livrata"]; ref += v["refuz"]; an += v["anulata"]
        trimise = liv + ref
        agg[p] = {
            "trimise": trimise, "livrata": liv, "refuz": ref, "anulata": an,
            "refuz_pct": round(ref / trimise * 100, 1) if trimise else None,
        }

    # Changes 90d vs 30d
    improved, worsened = [], []
    for sku in skus:
        old_v = data.get("90d", {}).get(sku, {}).get("refuz_pct")
        new_v = data.get("30d", {}).get(sku, {}).get("refuz_pct")
        if old_v is None or new_v is None: continue
        diff = new_v - old_v
        n_new = data.get("30d", {}).get(sku, {}).get("total", 0)
        if n_new < 10: continue
        if diff <= -5:  improved.append((sku, old_v, new_v, diff))
        elif diff >= 5: worsened.append((sku, old_v, new_v, diff))
    improved.sort(key=lambda x: x[3])
    worsened.sort(key=lambda x: x[3], reverse=True)

    H = []
    H.append("""<!DOCTYPE html>
<html lang="ro">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Analiza Refuz HA</title>
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Arial, sans-serif; background: #f4f6f9; color: #333; padding: 20px; }
  h1 { color: #1a3a5c; font-size: 22px; margin-bottom: 4px; }
  .subtitle { color: #666; font-size: 13px; margin-bottom: 24px; }
  h2 { color: #1a3a5c; font-size: 16px; margin: 28px 0 10px; border-left: 4px solid #2e5fa3; padding-left: 10px; }
  table { width: 100%; border-collapse: collapse; background: #fff; border-radius: 8px;
          overflow: hidden; box-shadow: 0 1px 4px rgba(0,0,0,.1); font-size: 13px; }
  th { background: #1f3864; color: #fff; padding: 10px 12px; text-align: center; font-weight: 600; }
  th.left { text-align: left; }
  td { padding: 8px 12px; text-align: center; border-bottom: 1px solid #eee; }
  td.left { text-align: left; font-weight: 600; }
  tr:last-child td { border-bottom: none; }
  tr:nth-child(even) td { background: #f8fafc; }
  .pct { font-weight: 700; border-radius: 4px; padding: 2px 8px; display: inline-block; min-width: 52px; }
  .trend-good { color: #27ae60; font-weight: 700; }
  .trend-bad  { color: #c0392b; font-weight: 700; }
  .trend-flat { color: #888; }
  .section-improved { background: #eafaf1; border-left: 4px solid #27ae60; border-radius: 6px;
                      padding: 12px 16px; margin-bottom: 20px; }
  .section-worsened { background: #fdedec; border-left: 4px solid #c0392b; border-radius: 6px;
                      padding: 12px 16px; margin-bottom: 20px; }
  .change-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; margin-top: 8px; }
  .change-card { background: #fff; border-radius: 6px; padding: 10px 14px;
                 box-shadow: 0 1px 3px rgba(0,0,0,.08); }
  .change-sku { font-weight: 700; font-size: 14px; }
  .change-vals { font-size: 12px; color: #555; margin-top: 2px; }
  .change-diff { font-size: 13px; font-weight: 700; margin-top: 4px; }
  .note { background: #fff8e1; border: 1px solid #f9ca24; border-radius: 6px;
          padding: 10px 14px; font-size: 12px; color: #7d6608; margin-bottom: 20px; }
  .period-sub { font-size: 11px; font-weight: 400; color: #aac; display: block; }
</style>
</head>
<body>
<h1>Analiza Rata de Refuz — Produse HA</h1>
<p class="subtitle">Refuz% = colete intorse / colete trimise (livrate + intorse) &nbsp;|&nbsp;
Data: """ + __import__('datetime').date.today().strftime('%d.%m.%Y') + """</p>

<div class="note">
  <strong>Formula:</strong> Refuz% = Intorse / (Livrate + Intorse) × 100 &nbsp;·&nbsp;
  Incluse in "Intorse": orice colet cu status <em>Refuzata</em> — refuz la usa + retur fizic dupa primire.
  Comenzile Anulate sunt excluse din calcul (nu au plecat).
</div>
""")

    # ── Tabel agregat ──────────────────────────────────────────────────────────
    H.append("<h2>Evolutie agregata HA — toate produsele</h2>")
    H.append("<table>")
    H.append("<tr><th class='left'>Perioada</th><th>Trimise</th><th>Livrate</th>"
             "<th>Intorse</th><th>Anulate</th><th>Refuz%</th></tr>")
    for p in PERIODS_ORDER:
        a = agg[p]
        bg, fg = pct_color(a["refuz_pct"])
        pct_html = (f'<span class="pct" style="background:{bg};color:{fg}">'
                    f'{a["refuz_pct"]:.1f}%</span>' if a["refuz_pct"] is not None else "—")
        H.append(f"<tr><td class='left'>{PERIOD_LABELS[p]}</td>"
                 f"<td>{a['trimise']:,}</td><td>{a['livrata']:,}</td>"
                 f"<td>{a['refuz']:,}</td><td>{a['anulata']:,}</td>"
                 f"<td>{pct_html}</td></tr>")
    H.append("</table>")

    # ── Tabel per produs ───────────────────────────────────────────────────────
    H.append("<h2>Per produs — sortat dupa refuz% curent</h2>")
    H.append("<table>")

    period_headers = "".join(
        f"<th>{PERIOD_LABELS[p]}<span class='period-sub'>Trimise / Refuz%</span></th>"
        for p in PERIODS_ORDER
    )
    H.append(f"<tr><th class='left'>SKU</th>{period_headers}<th>Trend (91+ vs 31-90)</th></tr>")

    for sku in skus:
        row_cells = [f"<td class='left'>{sku}</td>"]
        pct_vals = []
        for p in PERIODS_ORDER:
            v = data.get(p, {}).get(sku)
            if v and v["total"] > 0:
                pct = v["refuz_pct"]
                pct_vals.append(pct)
                bg, fg = pct_color(pct)
                pct_html = (f'<span class="pct" style="background:{bg};color:{fg}">'
                            f'{pct:.1f}%</span>' if pct is not None else "—")
                row_cells.append(f"<td>{v['total']:,} / {pct_html}</td>")
            else:
                pct_vals.append(None)
                row_cells.append("<td style='color:#ccc'>—</td>")

        trend_txt, trend_color = trend_str(pct_vals)
        css = "trend-good" if trend_color == "#27ae60" else ("trend-bad" if trend_color == "#c0392b" else "trend-flat")
        row_cells.append(f"<td class='{css}'>{trend_txt}</td>")
        H.append("<tr>" + "".join(row_cells) + "</tr>")

    H.append("</table>")

    # ── Schimbari semnificative ────────────────────────────────────────────────
    H.append("<h2>Schimbari semnificative (31–90 zile vs 8–30 zile, minim ±5pp)</h2>")

    H.append(f"<div class='section-improved'><strong>▼ Imbunatatite ({len(improved)} produse)</strong>")
    H.append("<div class='change-grid'>")
    for sku, old_v, new_v, diff in improved:
        H.append(f"""<div class='change-card'>
          <div class='change-sku'>{sku}</div>
          <div class='change-vals'>{old_v:.1f}% → {new_v:.1f}%</div>
          <div class='change-diff trend-good'>{diff:+.1f}pp</div>
        </div>""")
    if not improved:
        H.append("<p style='color:#555;margin-top:6px'>Niciun produs.</p>")
    H.append("</div></div>")

    H.append(f"<div class='section-worsened'><strong>▲ Inrautatite ({len(worsened)} produse)</strong>")
    H.append("<div class='change-grid'>")
    for sku, old_v, new_v, diff in worsened:
        H.append(f"""<div class='change-card'>
          <div class='change-sku'>{sku}</div>
          <div class='change-vals'>{old_v:.1f}% → {new_v:.1f}%</div>
          <div class='change-diff trend-bad'>+{diff:.1f}pp</div>
        </div>""")
    if not worsened:
        H.append("<p style='color:#555;margin-top:6px'>Niciun produs.</p>")
    H.append("</div></div>")

    H.append("</body></html>")
    return "\n".join(H)


print("Se conecteaza...", file=sys.stderr)
data = run_remote()
print("Se genereaza HTML...", file=sys.stderr)
html = build_html(data)

out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "ha_refuz_report.html")
out = os.path.normpath(out)
with open(out, "w", encoding="utf-8") as f:
    f.write(html)

print(f"Salvat: {out}", file=sys.stderr)
webbrowser.open(f"file:///{out.replace(os.sep, '/')}")
