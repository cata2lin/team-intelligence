# /// script
# requires-python = ">=3.10"
# dependencies = ["mcp>=1.2,<2", "psycopg2-binary"]
# ///
"""arona-scentum MCP — citiri tipate din Scentum ERP (producția de parfumuri), READ ONLY.

Înlocuiește SQL-ul brut repetat prin `postgres-scentum` cu tool-uri pe întrebările de zi cu zi: stocul de esențe
(și ambalaje / alcool / bază) cu semnalul de recomandă, fișa unei arome (rețetă, stoc pe tot lanțul, maturare,
magazine), livrările LIV-n, necesarele de producție, loturile la maturat, consumul pe o perioadă, comenzile la
furnizor. Plus `scentum_sql` — un SELECT liber, păzit (o singură instrucțiune, READ ONLY, LIMIT forțat).

Conexiune: secretul `DATABASE_URL_SCENTUM` (același ca `postgres-scentum`), luat prin core/scripts/arona_pg.py
(env întâi, apoi KB) și niciodată printat. Fiecare query = tranzacție READ ONLY + statement_timeout 30s.
Scrierile în Scentum NU se fac de aici: rămân la `gigi:scentum` (CLI-ul app-ului, cu validările lui).

Orele din DB sunt UTC fără fus; tool-urile le afișează în ora României.

Register: claude mcp add --scope user arona-scentum -- uv run <abs path>/plugins/gigi/skills/arona-scentum/mcp_server.py
"""
import math
import os
import re
import sys
from decimal import Decimal
from pathlib import Path

import psycopg2
from mcp.server.fastmcp import FastMCP


def _core_scripts(need="arona_pg.py"):
    h = Path(__file__).resolve()
    c = [Path(os.environ["ARONA_CORE_SCRIPTS"])] if os.environ.get("ARONA_CORE_SCRIPTS") else []
    for up in h.parents:
        c += [up / "core" / "scripts", up / "plugins" / "core" / "scripts"] + \
             (sorted((up / "core").glob("*/scripts")) if (up / "core").is_dir() else [])
    ok = [x for x in c if (x / need).exists()]
    return next((x for x in ok if x.parent.name in h.parts), ok[0] if ok else None)


_cs = _core_scripts()
if _cs is None:
    sys.exit("core/scripts (arona_pg.py) negăsit — actualizează plugin-urile sau setează ARONA_CORE_SCRIPTS")
sys.path.insert(0, str(_cs))
import arona_pg  # noqa: E402

SECRET = "DATABASE_URL_SCENTUM"
# Pragurile aplicației (src/lib/services/factory-health.service.ts): lead furnizor + maturare + tampon.
PRAG_ACOPERIRE_ZILE = 74
PRAG_CRITIC_ZILE = 14
FEREASTRA_CONSUM_ZILE = 60
TIPURI = ("ESSENCE", "PACKAGING", "ALCOHOL", "PERFUME_BASE", "FINISHED_GOOD")
CONSUM = "('MATURATION_ISSUE','CONSUMPTION','PRODUCTION_ISSUE')"
TERMINAL_NECESAR = ("PRODUCED", "CLOSED", "CANCELLED")
_DSN = None


def _ro(col):
    """Coloana timestamp (UTC fără fus) → text ora României."""
    return f"to_char(({col} AT TIME ZONE 'UTC') AT TIME ZONE 'Europe/Bucharest', 'YYYY-MM-DD HH24:MI')"


def _dsn():
    global _DSN
    if not _DSN:
        v = arona_pg.secret(SECRET)
        if not v:
            raise RuntimeError(f"{SECRET} lipsește (env / KB). Verifică KB_DATABASE_URL.")
        _DSN = arona_pg.clean_dsn(v)
    return _DSN


def _q(sql, args=()):
    """Interogare read-only. `args=None` = SQL BRUT, trimis neatins.

    ⚠️ psycopg2 face interpolare `%` ori de câte ori `vars` nu e None — inclusiv
    pentru tuplul gol. Un SELECT scris de om conține `%` literal (`LIKE 'CUT-%'`,
    `ILIKE '%print%'`), iar driverul îl citea ca placeholder și arunca
    `IndexError: tuple index out of range`. De aceea `scentum_sql` trimite
    `args=None`, iar aici nu mai dăm `vars` deloc în cazul ăla.
    """
    conn = psycopg2.connect(_dsn(), connect_timeout=20, application_name="arona-scentum-mcp")
    try:
        conn.set_session(readonly=True, autocommit=False)
        with conn.cursor() as cur:
            cur.execute("SET LOCAL statement_timeout = '30s'")
            cur.execute("SET LOCAL TIME ZONE 'UTC'")
            if args is None:
                cur.execute(sql)
            else:
                cur.execute(sql, args)
            cols = [d[0] for d in cur.description]
            return cols, cur.fetchall()
    finally:
        conn.rollback()
        conn.close()


def _v(x):
    if x is None:
        return ""
    if isinstance(x, (Decimal, float)):
        f = float(x)
        if math.isnan(f) or math.isinf(f):
            return ""
        if f == int(f) and abs(f) < 1e12:
            return str(int(f))
        return f"{f:.3f}".rstrip("0").rstrip(".") if abs(f) < 100 else f"{f:.1f}"
    if isinstance(x, bool):
        return "da" if x else "nu"
    return str(x).replace("\n", " ").replace("|", "/")


def _tab(cols, rows, titlu="", gol="(nimic)"):
    out = [titlu] if titlu else []
    if not rows:
        return "\n".join(out + [gol])
    out.append(" | ".join(cols))
    out += [" | ".join(_v(x) for x in r) for r in rows]
    return "\n".join(out)


def _like(s):
    return "%" + (s or "").strip().replace("%", r"\%").replace("_", r"\_") + "%"


def _err(e):
    return f"EROARE: {type(e).__name__}: {str(e).strip()[:400]}"


mcp = FastMCP("arona-scentum")


# ───────────────────────── STOC MATERII PRIME ─────────────────────────
@mcp.tool()
def esente_stoc(cauta: str = "", tip: str = "ESSENCE", doar_de_comandat: bool = False, limita: int = 80) -> str:
    """Stocul de ESENȚE (sau alt tip de materie: PACKAGING, ALCOHOL, PERFUME_BASE) + semnalul de recomandă.
    Pe produs: stoc din loturi (remaining_qty, în unitatea de bază: kg la esențe), consumul real pe 60 de zile
    (mișcări MATURATION_ISSUE + PRODUCTION_ISSUE + CONSUMPTION postate), zile de acoperire, cantitatea deja pe
    comenzi la furnizor deschise, și SEMNAL: CRITIC (< 14 zile), COMANDĂ (< 74 zile = lead + maturare + tampon,
    pragurile din tabloul fabricii), OK, sau „fără consum". de_comandat = cât aduce produsul la 74 de zile,
    minus ce e deja comandat. cauta = cod aromă (ex. '73'), nume, cod produs (ESS-73) sau furnizor.
    doar_de_comandat=true → doar CRITIC/COMANDĂ. Sortat după zile de acoperire crescător."""
    try:
        tip = (tip or "ESSENCE").strip().upper()
        if tip not in TIPURI:
            return f"tip necunoscut: {tip}. Valori: {', '.join(TIPURI)}"
        limita = max(1, min(int(limita), 500))
        c = (cauta or "").strip()
        cols, rows = _q(f"""
            WITH stoc AS (
              SELECT l.product_id, sum(l.remaining_qty) AS stoc, count(*) AS loturi
              FROM inventory_lots l WHERE l.remaining_qty > 0 GROUP BY 1),
            cons AS (
              SELECT m.product_id, sum(-m.qty) AS c60,
                     sum(-m.qty) FILTER (WHERE m.created_at >= now() - interval '14 days') AS c14
              FROM inventory_movements m
              WHERE m.movement_status = 'POSTED' AND m.movement_type IN {CONSUM}
                AND m.created_at >= now() - interval '{FEREASTRA_CONSUM_ZILE} days'
              GROUP BY 1),
            po AS (
              SELECT poi.product_id,
                     sum(GREATEST(poi.quantity_ordered - COALESCE(poi.quantity_received, 0), 0)) AS deschis,
                     string_agg(DISTINCT po.number, ',') AS po_uri
              FROM purchase_order_items poi JOIN purchase_orders po ON po.id = poi.purchase_order_id
              WHERE po.status IN ('DRAFT','APPROVED','PARTIALLY_RECEIVED') AND poi.product_id IS NOT NULL
              GROUP BY 1),
            r AS (
              SELECT p.product_code AS produs, COALESCE(sm.internal_code, '') AS cod,
                     COALESCE(sm.display_name, p.name) AS nume,
                     COALESCE(su.name, (SELECT s2.name FROM inventory_lots l2 JOIN suppliers s2 ON s2.id = l2.supplier_id
                                        WHERE l2.product_id = p.id ORDER BY l2.receipt_date DESC LIMIT 1), '') AS furnizor,
                     p.base_uom::text AS um, COALESCE(s.stoc, 0) AS stoc, COALESCE(s.loturi, 0) AS loturi,
                     COALESCE(c.c60, 0) / {FEREASTRA_CONSUM_ZILE}.0 AS pe_zi, COALESCE(c.c14, 0) / 14.0 AS pe_zi_14z,
                     COALESCE(po.deschis, 0) AS in_comanda, COALESCE(po.po_uri, '') AS po
              FROM products p
              LEFT JOIN scent_masters sm ON sm.id = p.scent_master_id
              LEFT JOIN suppliers su ON su.id = p.supplier_id
              LEFT JOIN stoc s ON s.product_id = p.id
              LEFT JOIN cons c ON c.product_id = p.id
              LEFT JOIN po ON po.product_id = p.id
              WHERE p.product_type = %s AND p.is_active
                AND (%s = '' OR sm.internal_code = %s OR p.product_code ILIKE %s OR p.name ILIKE %s
                     OR sm.display_name ILIKE %s OR sm.inspired_by_name ILIKE %s OR su.name ILIKE %s))
            SELECT *, CASE WHEN pe_zi > 0 THEN stoc / pe_zi END AS zile FROM r
            WHERE stoc > 0 OR pe_zi > 0 OR in_comanda > 0
            ORDER BY CASE WHEN pe_zi > 0 THEN stoc / pe_zi END ASC NULLS LAST, stoc DESC""",
            (tip, c, c, _like(c), _like(c), _like(c), _like(c), _like(c)))
        out_rows, n_crit, n_cmd, kg_cmd = [], 0, 0, 0.0
        for r in rows:
            d = dict(zip(cols, r))
            zile = float(d["zile"]) if d["zile"] is not None else None
            pe_zi, stoc, inc = float(d["pe_zi"]), float(d["stoc"]), float(d["in_comanda"])
            if zile is None:
                sem, de_cmd = "fără consum", 0
            elif zile < PRAG_CRITIC_ZILE:
                sem = "CRITIC"
            elif zile < PRAG_ACOPERIRE_ZILE:
                sem = "COMANDĂ"
            else:
                sem = "OK"
            if zile is not None:
                de_cmd = max(0, math.ceil(pe_zi * PRAG_ACOPERIRE_ZILE - stoc - inc)) if sem != "OK" else 0
            if sem in ("CRITIC", "COMANDĂ"):
                n_crit += sem == "CRITIC"
                n_cmd += sem == "COMANDĂ"
                kg_cmd += de_cmd
            elif doar_de_comandat:
                continue
            out_rows.append((d["cod"], d["nume"], d["produs"], d["furnizor"], d["um"], stoc, d["loturi"],
                             round(pe_zi, 3), round(float(d["pe_zi_14z"]), 3),
                             None if zile is None else round(zile), inc, d["po"], sem, de_cmd))
        head = (f"{tip}: {len(rows)} produse cu stoc/consum · CRITIC {n_crit} · COMANDĂ {n_cmd} · "
                f"de comandat ~{kg_cmd:.0f} (unitatea de bază) · consum = medie {FEREASTRA_CONSUM_ZILE}z, "
                f"praguri {PRAG_CRITIC_ZILE}/{PRAG_ACOPERIRE_ZILE} zile")
        more = f"\n… încă {len(out_rows) - limita} rânduri (mărește limita)" if len(out_rows) > limita else ""
        return _tab(["cod", "aroma/nume", "produs", "furnizor", "um", "stoc", "loturi", "consum/zi 60z",
                     "consum/zi 14z", "zile acoperire", "pe PO deschis", "PO", "semnal", "de comandat"],
                    out_rows[:limita], head) + more
    except Exception as e:
        return _err(e)


# ───────────────────────── FIȘA AROMEI ─────────────────────────
def _gaseste_arome(x):
    x = (x or "").strip()
    if not x:
        return []
    _, rows = _q("SELECT id FROM scent_masters WHERE internal_code = %s", (x,))
    if rows:
        return [r[0] for r in rows]
    _, rows = _q("""SELECT DISTINCT l.scent_master_id FROM shopify_variants v
                    JOIN shopify_variant_scent_links l ON l.variant_id = v.id
                    WHERE v.deleted_at IS NULL AND (v.sku ILIKE %s OR v.barcode = %s) LIMIT 5""", (x, x))
    if rows:
        return [r[0] for r in rows]
    _, rows = _q("""SELECT DISTINCT p.scent_master_id FROM products p
                    WHERE p.scent_master_id IS NOT NULL AND (p.product_code ILIKE %s OR p.barcode = %s) LIMIT 5""",
                 (x, x))
    if rows:
        return [r[0] for r in rows]
    _, rows = _q("""SELECT id FROM scent_masters WHERE display_name ILIKE %s OR inspired_by_name ILIKE %s
                    ORDER BY is_active DESC, (display_name ILIKE %s) DESC, internal_code LIMIT 8""",
                 (_like(x), _like(x), x))
    return [r[0] for r in rows]


def _fisa(sid):
    out = []
    _, r = _q("SELECT internal_code, display_name, inspired_by_name, is_active, forecast_excluded_reason "
              "FROM scent_masters WHERE id = %s", (sid,))
    cod, nume, insp, activ, excl = r[0]
    out.append(f"## Aroma {cod} — {nume} (inspirat din {insp or '—'}) · activă: {_v(activ)}"
               + (f" · EXCLUSĂ din forecast: {excl}" if excl else ""))

    cols, rows = _q(f"""
        SELECT p.product_type::text AS tip, COALESCE(b.brand_code, '') AS brand, p.product_code, p.base_uom::text AS um,
               COALESCE(sum(l.remaining_qty), 0) AS stoc, count(l.id) AS loturi,
               COALESCE(sum(l.remaining_qty) FILTER (WHERE l.matures_on > now()), 0) AS la_maturat,
               {_ro("min(l.matures_on) FILTER (WHERE l.matures_on > now())")} AS gata_primul,
               (SELECT sum(-m.qty) FROM inventory_movements m WHERE m.product_id = p.id AND m.movement_status='POSTED'
                  AND m.movement_type IN {CONSUM} AND m.created_at >= now() - interval '60 days') AS consum_60z,
               p.avg_weighted_cost AS cost_mediu, p.cost_currency AS moneda
        FROM products p
        LEFT JOIN brands b ON b.id = p.brand_id
        LEFT JOIN inventory_lots l ON l.product_id = p.id AND l.remaining_qty > 0
        WHERE p.scent_master_id = %s AND p.is_active
        GROUP BY p.id, p.product_type, b.brand_code, p.product_code, p.base_uom
        ORDER BY array_position(ARRAY['ESSENCE','PERFUME_BASE','FINISHED_GOOD']::text[], p.product_type::text), 2, 3""",
                    (sid,))
    out.append(_tab(cols, rows, "Stoc în fabrică pe lanț (esență kg → bază L → produs finit buc):"))

    cols, rows = _q(f"""
        SELECT COALESCE(b.brand_code, '') AS brand, v.sku, v.barcode, sv.title AS produs_shopify, v.title AS varianta,
               lnk.qty_multiplier AS multiplicator,
               (SELECT sum(il.available) FROM shopify_inventory_levels il WHERE il.variant_id = v.id) AS disponibil_shopify,
               ps.sales_status::text AS vanzari, ps.velocity_per_day AS buc_zi, ps.days_of_stock AS zile_stoc,
               (SELECT string_agg(r.code, ',') FROM recipe_variant_links rv JOIN recipes r ON r.id = rv.recipe_id
                 WHERE rv.variant_id = v.id) AS reteta
        FROM shopify_variant_scent_links lnk
        JOIN shopify_variants v ON v.id = lnk.variant_id AND v.deleted_at IS NULL
        JOIN shopify_products sv ON sv.id = v.product_id AND sv.deleted_at IS NULL
        JOIN shopify_stores s ON s.id = v.store_id
        LEFT JOIN brands b ON b.id = s.brand_id
        LEFT JOIN LATERAL (SELECT sales_status, velocity_per_day, days_of_stock FROM product_sales_performance_daily d
                           WHERE d.variant_id = v.id ORDER BY d.date DESC LIMIT 1) ps ON true
        WHERE lnk.scent_master_id = %s
        ORDER BY 1, 2 LIMIT 40""", (sid,))
    out.append(_tab(cols, rows, "Variante în magazine (stoc oglindă Scentum, viteză din performanța zilnică):"))

    cols, rows = _q("""
        SELECT DISTINCT ON (r.code, rc.line_no) r.code AS reteta, rc.role::text AS rol, rc.resolve_by::text AS rezolvare,
               rc.qty_per_unit AS cant_per_buc, rc.uom::text AS um, COALESCE(p.product_code || ' ' || p.name, '(din aromă)') AS componenta
        FROM shopify_variant_scent_links lnk
        JOIN recipe_variant_links rv ON rv.variant_id = lnk.variant_id
        JOIN recipes r ON r.id = rv.recipe_id
        JOIN recipe_components rc ON rc.recipe_id = r.id
        LEFT JOIN products p ON p.id = rc.product_id
        WHERE lnk.scent_master_id = %s
        ORDER BY r.code, rc.line_no LIMIT 40""", (sid,))
    out.append(_tab(cols, rows, "Rețeta (BOM) legată de variante:", "(nicio rețetă legată de variantele aromei)"))

    cols, rows = _q(f"""
        SELECT pr.number AS necesar, pr.status::text AS status_necesar, COALESCE(b.brand_code, '') AS brand, pri.sku,
               pri.status::text AS status_linie, pri.requested_qty AS cerut, pri.manufactured_qty AS produs,
               pri.delivered_qty AS livrat, {_ro("pr.created_at")} AS creat
        FROM production_requirement_items pri
        JOIN production_requirements pr ON pr.id = pri.production_requirement_id
        LEFT JOIN brands b ON b.id = pri.brand_id
        WHERE pri.scent_master_id = %s AND pri.status IN ('OPEN','COMPLETED','IN_DELIVERY','PARTIALLY_DELIVERED')
        ORDER BY pr.created_at DESC LIMIT 15""", (sid,))
    out.append(_tab(cols, rows, "Necesar de producție deschis:", "(nicio linie deschisă)"))

    cols, rows = _q(f"""
        SELECT d.number AS livrare, d.status::text AS status, d.delivery_date AS data, di.sku, di.quantity AS buc,
               di.status::text AS linie
        FROM delivery_items di JOIN deliveries d ON d.id = di.delivery_id
        WHERE di.scent_master_id = %s ORDER BY d.created_at DESC LIMIT 8""", (sid,))
    out.append(_tab(cols, rows, "Ultimele livrări:", "(nicio livrare)"))
    return "\n\n".join(out)


@mcp.tool()
def produs_scentum(sku_sau_nume: str) -> str:
    """FIȘA unei arome / unui produs Scentum: stocul pe tot lanțul în fabrică (esență kg → bază L, cu ce e încă la
    maturat și când e gata → produs finit buc, pe brand), consumul de esență pe 60 de zile, variantele din magazine
    (SKU, barcode, stoc disponibil oglindă, statut vânzări + buc/zi), rețeta (BOM) legată, liniile de necesar
    deschise și ultimele livrări. sku_sau_nume = cod aromă ('73'), SKU de magazin ('73-50ml'), barcode, cod produs
    (ESS-73, BAZ-ESTEBAN-73, FG-ESTEBAN-73-50) sau o bucată din nume / din parfumul de inspirație ('Good Girl').
    Mai multe potriviri → fișa primelor 2 + lista celorlalte."""
    try:
        ids = _gaseste_arome(sku_sau_nume)
        if not ids:
            return f"Nicio aromă nu se potrivește cu „{sku_sau_nume}”."
        out = [_fisa(i) for i in ids[:2]]
        if len(ids) > 2:
            _, rows = _q("SELECT internal_code, display_name, inspired_by_name FROM scent_masters WHERE id = ANY(%s)",
                         (ids[2:],))
            out.append("Alte potriviri: " + "; ".join(f"{a} {b} ({c})" for a, b, c in rows))
        return "\n\n".join(out)
    except Exception as e:
        return _err(e)


# ───────────────────────── LIVRĂRI ─────────────────────────
@mcp.tool()
def livrari_recente(zile: int = 14, produs: str = "", livrare: str = "") -> str:
    """Livrările LIV-n din fabrică spre magazine. Fără `livrare`: lista din ultimele `zile` (după data creării) cu
    branduri, linii, bucăți, câte linii sunt recepționate / eșuate / anulate, câte au stocul NEîmpins
    (stock_sync_skipped — recepție „fără sincronizare”) și câte au intrarea în ledger (shopify_adjustment_id),
    plus starea facturii SmartBill. produs = filtru pe SKU / cod aromă / nume. Cu `livrare='LIV-58'`: liniile
    livrării (SKU, aromă, cantitate, status, skipped, eroarea de sync). Regula din andreea:receptie-livrari-erp:
    skipped == linii → stocul e de împins; skipped 0 și ledger == linii → deja împinsă."""
    try:
        if livrare.strip():
            nr = livrare.strip().upper()
            if re.fullmatch(r"\d+", nr):
                nr = f"LIV-{nr}"
            cols, rows = _q(f"""
                SELECT d.number, d.status::text, d.delivery_date, {_ro("d.received_at")} AS receptionat,
                       d.smartbill_status::text AS smartbill, d.smartbill_invoice_series || ' ' || d.smartbill_invoice_number AS factura,
                       d.notes
                FROM deliveries d WHERE d.number = %s""", (nr,))
            if not rows:
                return f"Livrarea {nr} nu există."
            head = _tab(cols, rows)
            cols, rows = _q(f"""
                SELECT di.line_no AS nr, COALESCE(b.brand_code, '') AS brand, di.sku, di.scent_code AS cod,
                       di.scent_name AS aroma, di.quantity AS buc, di.status::text AS status,
                       di.stock_sync_skipped AS skipped, (di.shopify_adjustment_id IS NOT NULL) AS in_ledger,
                       {_ro("di.received_at")} AS receptionat, left(di.last_sync_error, 120) AS eroare
                FROM delivery_items di JOIN deliveries d ON d.id = di.delivery_id
                LEFT JOIN brands b ON b.id = di.brand_id
                WHERE d.number = %s ORDER BY di.line_no""", (nr,))
            tot = sum(int(r[5] or 0) for r in rows)
            return head + "\n\n" + _tab(cols, rows, f"{len(rows)} linii · {tot} buc")
        zile = max(1, min(int(zile), 365))
        p = (produs or "").strip()
        cols, rows = _q(f"""
            SELECT d.number AS livrare, d.status::text AS status, d.delivery_date AS data,
                   {_ro("d.received_at")} AS receptionat,
                   string_agg(DISTINCT b.brand_code, ',') AS branduri, count(di.id) AS linii,
                   COALESCE(sum(di.quantity), 0) AS buc,
                   count(*) FILTER (WHERE di.status = 'RECEIVED') AS rec,
                   count(*) FILTER (WHERE di.status = 'FAILED') AS esuate,
                   count(*) FILTER (WHERE di.status = 'CANCELLED') AS anulate,
                   count(*) FILTER (WHERE di.stock_sync_skipped) AS skipped,
                   count(*) FILTER (WHERE di.shopify_adjustment_id IS NOT NULL) AS in_ledger,
                   d.smartbill_status::text AS smartbill
            FROM deliveries d
            LEFT JOIN delivery_items di ON di.delivery_id = d.id
            LEFT JOIN brands b ON b.id = di.brand_id
            WHERE d.created_at >= now() - make_interval(days => %s)
              AND (%s = '' OR EXISTS (SELECT 1 FROM delivery_items x WHERE x.delivery_id = d.id AND
                    (x.sku ILIKE %s OR x.scent_code = %s OR x.scent_name ILIKE %s OR x.product_title ILIKE %s)))
            GROUP BY d.id ORDER BY d.created_at DESC""", (zile, p, _like(p), p, _like(p), _like(p)))
        return _tab(cols, rows, f"Livrări din ultimele {zile} zile" + (f" cu „{p}”" if p else "") + f": {len(rows)}")
    except Exception as e:
        return _err(e)


# ───────────────────────── PLAN PRODUCȚIE (NECESAR) ─────────────────────────
@mcp.tool()
def plan_productie(status: str = "", brand: str = "", necesar: str = "") -> str:
    """Necesarele de producție (Andreea → Vali). Fără `necesar`: lista cu brand, status, linii, bucăți cerute /
    produse / livrate și RESTUL de produs (pe liniile OPEN), cea mai veche linie, SLA. status gol = doar cele
    active (DRAFT, APPROVED, PARTIALLY_PRODUCED); altfel unul din DRAFT, APPROVED, PARTIALLY_PRODUCED, PRODUCED,
    CLOSED, CANCELLED sau 'toate'. brand = ESTEBAN, NUBRA, GEORGE-TALENT, LAB-NOIR… Cu `necesar='EST-OCT'`
    (sau PR-ESTEBAN-12): liniile, întâi cele cu rest de produs, apoi cele produse / livrate."""
    try:
        if necesar.strip():
            nr = necesar.strip()
            cols, rows = _q(f"""
                SELECT pr.number, pr.status::text, COALESCE(b.brand_code, 'multi') AS brand, pr.title,
                       pr.sla_tier::text AS sla, {_ro("pr.created_at")} AS creat, {_ro("pr.approved_at")} AS aprobat
                FROM production_requirements pr LEFT JOIN brands b ON b.id = pr.brand_id
                WHERE pr.number ILIKE %s""", (nr,))
            if not rows:
                return f"Necesarul {nr} nu există."
            head = _tab(cols, rows)
            cols, rows = _q(f"""
                SELECT pri.line_no AS nr, COALESCE(b.brand_code, '') AS brand, pri.sku, pri.scent_code AS cod,
                       pri.scent_name AS aroma, pri.status::text AS status, pri.requested_qty AS cerut,
                       pri.manufactured_qty AS produs, pri.delivered_qty AS livrat,
                       CASE WHEN pri.status = 'OPEN' THEN GREATEST(pri.requested_qty - COALESCE(pri.manufactured_qty, 0), 0) ELSE 0 END AS rest,
                       pri.sla_tier::text AS sla, {_ro("pri.manufactured_at")} AS produs_la
                FROM production_requirement_items pri
                JOIN production_requirements pr ON pr.id = pri.production_requirement_id
                LEFT JOIN brands b ON b.id = pri.brand_id
                WHERE pr.number ILIKE %s
                ORDER BY (pri.status = 'OPEN') DESC, pri.status, pri.line_no""", (nr,))
            rest = sum(int(r[9] or 0) for r in rows)
            return head + "\n\n" + _tab(cols, rows, f"{len(rows)} linii · rest de produs {rest} buc")
        st = (status or "").strip().upper()
        if st in ("", "ACTIVE"):
            sts = ["DRAFT", "APPROVED", "PARTIALLY_PRODUCED"]
        elif st == "TOATE":
            sts = ["DRAFT", "APPROVED", "PARTIALLY_PRODUCED", *TERMINAL_NECESAR]
        else:
            sts = [st]
        bd = (brand or "").strip().upper()
        cols, rows = _q(f"""
            SELECT pr.number AS necesar, COALESCE(b.brand_code, string_agg(DISTINCT bi.brand_code, ','), '') AS brand,
                   pr.status::text AS status,
                   left(pr.title, 40) AS titlu, count(pri.id) AS linii,
                   count(*) FILTER (WHERE pri.status = 'OPEN') AS linii_open,
                   COALESCE(sum(pri.requested_qty) FILTER (WHERE pri.status <> 'CANCELLED'), 0) AS cerut,
                   COALESCE(sum(pri.manufactured_qty), 0) AS produs, COALESCE(sum(pri.delivered_qty), 0) AS livrat,
                   COALESCE(sum(GREATEST(pri.requested_qty - COALESCE(pri.manufactured_qty, 0), 0))
                            FILTER (WHERE pri.status = 'OPEN'), 0) AS rest_de_produs,
                   pr.sla_tier::text AS sla, {_ro("pr.created_at")} AS creat,
                   EXTRACT(day FROM now() - pr.created_at)::int AS vechime_zile
            FROM production_requirements pr
            LEFT JOIN brands b ON b.id = pr.brand_id
            LEFT JOIN production_requirement_items pri ON pri.production_requirement_id = pr.id
            LEFT JOIN brands bi ON bi.id = pri.brand_id
            WHERE pr.status::text = ANY(%s)
              AND (%s = '' OR b.brand_code = %s OR EXISTS (SELECT 1 FROM production_requirement_items x
                    JOIN brands bx ON bx.id = x.brand_id WHERE x.production_requirement_id = pr.id AND bx.brand_code = %s))
            GROUP BY pr.id, b.brand_code ORDER BY pr.created_at DESC LIMIT 100""", (sts, bd, bd, bd))
        rest = sum(int(r[9] or 0) for r in rows)
        return _tab(cols, rows, f"Necesare {','.join(sts)}{' · ' + bd if bd else ''}: {len(rows)} · rest de produs {rest} buc")
    except Exception as e:
        return _err(e)


# ───────────────────────── MATURARE ─────────────────────────
@mcp.tool()
def maturare(zile_ramase: int = 7, brand: str = "", cauta: str = "", include_maturate: bool = False) -> str:
    """Loturile de BAZĂ de parfum (esență + alcool) puse la maturat, cu stoc rămas: lot, brand, aromă, litri
    rămași, concentrație, data punerii, data când e gata (matures_on = recepție + zilele de maturare din setări)
    și câte zile mai are. Implicit: cele care se termină în ≤ zile_ramase zile (încă la maturat).
    include_maturate=true adaugă și loturile deja gata, cu bază rămasă neîmbuteliată (zile negative = de câte
    zile e gata). brand / cauta = filtre (cod aromă, nume, cod lot). Plus sumarul pe total și ciornele de
    maturare deschise."""
    try:
        n = max(0, min(int(zile_ramase), 365))
        bd, c = (brand or "").strip().upper(), (cauta or "").strip()
        cols, rows = _q(f"""
            SELECT l.lot_code AS lot, COALESCE(b.brand_code, '') AS brand, sm.internal_code AS cod,
                   sm.display_name AS aroma, l.remaining_qty AS litri, l.concentration_pct AS conc_pct,
                   {_ro("l.receipt_date")} AS pus_la, {_ro("l.matures_on")} AS gata_la,
                   ceil(EXTRACT(epoch FROM l.matures_on - now()) / 86400)::int AS zile_ramase,
                   COALESCE(sh.code, '') AS raft
            FROM inventory_lots l
            JOIN products p ON p.id = l.product_id AND p.product_type = 'PERFUME_BASE'
            LEFT JOIN brands b ON b.id = p.brand_id
            LEFT JOIN scent_masters sm ON sm.id = p.scent_master_id
            LEFT JOIN shelves sh ON sh.id = l.shelf_id
            WHERE l.remaining_qty > 0 AND l.matures_on IS NOT NULL
              AND l.matures_on <= now() + make_interval(days => %s)
              AND (%s OR l.matures_on > now())
              AND (%s = '' OR b.brand_code = %s)
              AND (%s = '' OR sm.internal_code = %s OR sm.display_name ILIKE %s OR l.lot_code ILIKE %s)
            ORDER BY l.matures_on ASC LIMIT 300""",
                        (n, bool(include_maturate), bd, bd, c, c, _like(c), _like(c)))
        _, s = _q("""
            SELECT count(*) FILTER (WHERE l.matures_on > now()), COALESCE(sum(l.remaining_qty) FILTER (WHERE l.matures_on > now()), 0),
                   count(*) FILTER (WHERE l.matures_on <= now()), COALESCE(sum(l.remaining_qty) FILTER (WHERE l.matures_on <= now()), 0),
                   (SELECT count(*) FROM maturation_operations WHERE status = 'DRAFT'),
                   (SELECT maturation_days FROM app_settings LIMIT 1)
            FROM inventory_lots l JOIN products p ON p.id = l.product_id AND p.product_type = 'PERFUME_BASE'
            WHERE l.remaining_qty > 0""")
        a, al, g, gl, dr, md = s[0]
        head = (f"Bază în fabrică: la maturat {a} loturi / {_v(al)} L · gata (neîmbuteliată) {g} loturi / {_v(gl)} L · "
                f"maturare țintă {md} zile · ciorne de maturare deschise {dr}\n"
                f"Filtru: gata în ≤ {n} zile{' + deja gata' if include_maturate else ''}: {len(rows)} loturi / "
                f"{_v(sum(float(r[4]) for r in rows))} L")
        return _tab(cols, rows, head)
    except Exception as e:
        return _err(e)


# ───────────────────────── CONSUM ─────────────────────────
@mcp.tool()
def consum_esente(de_la: str = "", pana_la: str = "", zile: int = 30, cauta: str = "", tip: str = "ESSENCE",
                  limita: int = 60) -> str:
    """CONSUMUL real de materie pe o perioadă, din mișcările de stoc postate (fără cele stornate): pe produs,
    defalcat pe maturare (MATURATION_ISSUE), producție/îmbuteliere (PRODUCTION_ISSUE) și consum direct
    (CONSUMPTION), plus ajustările minus separat (corecții de inventar, NU consum) și valoarea la cost.
    Perioada: de_la / pana_la = 'AAAA-LL-ZZ' (pana_la inclusă, ora României); dacă lipsesc = ultimele `zile`.
    tip = ESSENCE (implicit), ALCOHOL, PACKAGING, PERFUME_BASE. cauta = cod aromă / nume / cod produs."""
    try:
        tip = (tip or "ESSENCE").strip().upper()
        if tip not in TIPURI:
            return f"tip necunoscut: {tip}. Valori: {', '.join(TIPURI)}"
        for d in (de_la, pana_la):
            if d.strip() and not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d.strip()):
                return "Datele sunt AAAA-LL-ZZ (ex. 2026-10-01)."
        limita = max(1, min(int(limita), 500))
        if de_la.strip():
            lo = "((%s::date::timestamp AT TIME ZONE 'Europe/Bucharest') AT TIME ZONE 'UTC')"
            args = [de_la.strip()]
        else:
            lo = "(now() AT TIME ZONE 'UTC' - make_interval(days => %s))"
            args = [max(1, min(int(zile), 730))]
        if pana_la.strip():
            hi = "(((%s::date + 1)::timestamp AT TIME ZONE 'Europe/Bucharest') AT TIME ZONE 'UTC')"
            args.append(pana_la.strip())
        else:
            hi = "(now() AT TIME ZONE 'UTC' + interval '1 minute')"
        c = (cauta or "").strip()
        cols, rows = _q(f"""
            WITH m AS (
              SELECT m.* FROM inventory_movements m
              WHERE m.movement_status = 'POSTED' AND m.created_at >= {lo} AND m.created_at < {hi})
            SELECT COALESCE(sm.internal_code, '') AS cod, COALESCE(sm.display_name, p.name) AS nume, p.product_code AS produs,
                   p.base_uom::text AS um,
                   COALESCE(sum(-m.qty) FILTER (WHERE m.movement_type IN {CONSUM}), 0) AS consum_total,
                   COALESCE(sum(-m.qty) FILTER (WHERE m.movement_type = 'MATURATION_ISSUE'), 0) AS maturare,
                   COALESCE(sum(-m.qty) FILTER (WHERE m.movement_type = 'PRODUCTION_ISSUE'), 0) AS productie,
                   COALESCE(sum(-m.qty) FILTER (WHERE m.movement_type = 'CONSUMPTION'), 0) AS consum_direct,
                   COALESCE(sum(-m.qty) FILTER (WHERE m.movement_type = 'ADJUSTMENT_MINUS'), 0) AS ajustari_minus,
                   COALESCE(sum(-m.total_cost_snapshot) FILTER (WHERE m.movement_type IN {CONSUM}), 0) AS valoare_consum,
                   count(*) FILTER (WHERE m.movement_type IN {CONSUM}) AS miscari
            FROM m JOIN products p ON p.id = m.product_id
            LEFT JOIN scent_masters sm ON sm.id = p.scent_master_id
            WHERE p.product_type = %s
              AND (%s = '' OR sm.internal_code = %s OR p.product_code ILIKE %s OR p.name ILIKE %s OR sm.display_name ILIKE %s)
            GROUP BY sm.internal_code, sm.display_name, p.name, p.product_code, p.base_uom
            HAVING sum(-m.qty) FILTER (WHERE m.movement_type IN {CONSUM} OR m.movement_type = 'ADJUSTMENT_MINUS') <> 0
            ORDER BY 5 DESC""", tuple(args) + (tip, c, c, _like(c), _like(c), _like(c)))
        _, per = _q(f"SELECT {_ro(lo)}, {_ro(hi)}", tuple(args))
        tot = sum(float(r[4]) for r in rows)
        val = sum(float(r[9]) for r in rows)
        head = (f"Consum {tip} {per[0][0]} → {per[0][1]} (ora RO): {len(rows)} produse · total {tot:.2f} "
                f"(unitatea de bază) · valoare la cost {val:,.0f} (moneda de cost a loturilor)")
        more = f"\n… încă {len(rows) - limita} rânduri" if len(rows) > limita else ""
        return _tab(cols, rows[:limita], head) + more
    except Exception as e:
        return _err(e)


# ───────────────────────── COMENZI FURNIZOR ─────────────────────────
@mcp.tool()
def comenzi_furnizor(tip: str = "", status: str = "", cauta: str = "", po: str = "") -> str:
    """Comenzile la furnizor (PO) din Scentum: ESENTE sau AMBALAJE. Fără `po`: lista cu furnizor, status, linii,
    cantitate comandată / recepționată / rămasă, valoare în moneda PO-ului (câmpul total_cost_ron ține de fapt
    suma în `currency`), starea din TOM (număr, status, container, ETA).
    status gol = deschise (DRAFT, APPROVED, PARTIALLY_RECEIVED); sau unul anume / 'toate'. cauta = produs sau
    furnizor. Cu `po='PO-ESE-12'`: liniile comenzii (comandat, recepționat, rest, cost, starea liniei în TOM)."""
    try:
        if po.strip():
            nr = po.strip()
            cols, rows = _q(f"""
                SELECT po.number, po.type::text AS tip, po.status::text AS status, COALESCE(s.name, po.tom_supplier_name) AS furnizor,
                       po.title, po.currency, po.total_cost_ron, po.total_cost_usd, po.tom_number, po.tom_status,
                       po.tom_shipment_code AS container, {_ro("po.tom_shipment_eta")} AS eta, {_ro("po.order_date")} AS data
                FROM purchase_orders po LEFT JOIN suppliers s ON s.id = po.supplier_id
                WHERE po.number ILIKE %s OR po.tom_number ILIKE %s""", (nr, nr))
            if not rows:
                return f"PO {nr} nu există."
            head = _tab(cols, rows)
            cols, rows = _q("""
                SELECT i.line_no AS nr, i.product_code AS produs, left(i.product_title, 50) AS titlu,
                       i.quantity_ordered AS comandat, i.quantity_received AS receptionat,
                       GREATEST(i.quantity_ordered - COALESCE(i.quantity_received, 0), 0) AS rest,
                       i.unit_cost_ron, i.unit_cost_usd, i.tom_status, i.tom_shipped_qty AS tom_expediat,
                       i.tom_shipment_code AS container
                FROM purchase_order_items i JOIN purchase_orders po ON po.id = i.purchase_order_id
                WHERE po.number ILIKE %s OR po.tom_number ILIKE %s ORDER BY i.line_no""", (nr, nr))
            return head + "\n\n" + _tab(cols, rows, f"{len(rows)} linii")
        t = (tip or "").strip().upper()
        if t and t not in ("ESENTE", "AMBALAJE"):
            return "tip = ESENTE sau AMBALAJE (sau gol pentru ambele)."
        st = (status or "").strip().upper()
        sts = (["DRAFT", "APPROVED", "PARTIALLY_RECEIVED"] if st == "" else
               ["DRAFT", "APPROVED", "PARTIALLY_RECEIVED", "COMPLETED", "CANCELLED"] if st == "TOATE" else [st])
        c = (cauta or "").strip()
        cols, rows = _q(f"""
            SELECT po.number AS po, po.type::text AS tip, po.status::text AS status,
                   COALESCE(s.name, po.tom_supplier_name, '') AS furnizor, left(po.title, 35) AS titlu,
                   count(i.id) AS linii, COALESCE(sum(i.quantity_ordered), 0) AS comandat,
                   COALESCE(sum(i.quantity_received), 0) AS receptionat,
                   COALESCE(sum(GREATEST(i.quantity_ordered - COALESCE(i.quantity_received, 0), 0)), 0) AS rest,
                   COALESCE(po.total_cost_ron, po.total_cost_usd) AS valoare, po.currency AS moneda, COALESCE(po.tom_number, '') AS tom,
                   COALESCE(po.tom_status, '') AS tom_status, COALESCE(po.tom_shipment_code, '') AS container,
                   {_ro("po.tom_shipment_eta")} AS eta, {_ro("po.created_at")} AS creat
            FROM purchase_orders po
            LEFT JOIN suppliers s ON s.id = po.supplier_id
            LEFT JOIN purchase_order_items i ON i.purchase_order_id = po.id
            WHERE po.status::text = ANY(%s) AND (%s = '' OR po.type::text = %s)
              AND (%s = '' OR s.name ILIKE %s OR po.title ILIKE %s OR po.number ILIKE %s OR EXISTS (
                    SELECT 1 FROM purchase_order_items x WHERE x.purchase_order_id = po.id
                    AND (x.product_code ILIKE %s OR x.product_title ILIKE %s)))
            GROUP BY po.id, s.name ORDER BY po.created_at DESC LIMIT 100""",
                        (sts, t, t, c, _like(c), _like(c), _like(c), _like(c), _like(c)))
        return _tab(cols, rows, f"PO {t or 'ESENTE+AMBALAJE'} {','.join(sts)}: {len(rows)}")
    except Exception as e:
        return _err(e)


# ───────────────────────── SQL LIBER, PĂZIT ─────────────────────────
_INTERZIS = re.compile(
    r"\b(insert|update|delete|merge|upsert|create|alter|drop|truncate|grant|revoke|comment|copy|call|do|vacuum|"
    r"analyze|cluster|reindex|lock|listen|notify|unlisten|set|reset|refresh|prepare|execute|deallocate|discard|"
    r"into|begin|commit|rollback|savepoint|checkpoint|load|import)\b"
    r"|\b(pg_read_\w*|pg_ls_\w*|pg_stat_file|pg_sleep\w*|pg_terminate_backend|pg_cancel_backend|pg_reload_conf|"
    r"pg_advisory\w*|set_config|dblink\w*|lo_\w+|query_to_xml\w*|nextval|setval|currval)\s*\(",
    re.I)


@mcp.tool()
def scentum_sql(select: str, limita: int = 200) -> str:
    """SELECT liber pe Scentum, pentru ce n-au tool-urile de mai sus. Păzit strict: O SINGURĂ instrucțiune,
    care începe cu SELECT sau WITH, fără comentarii, fără cuvinte de scriere/DDL/SET/INTO și fără funcții de
    sistem (pg_read_file, pg_sleep, dblink, set_config…); rulează într-o tranzacție READ ONLY cu timeout 30s, și e
    învelită ca `SELECT * FROM (<select>) q LIMIT n` (limita ≤ 1000). Preferă tool-urile tipate când se potrivesc.
    Orele din DB sunt UTC. Tabele utile: products, inventory_lots, inventory_movements, scent_masters, recipes,
    recipe_components, deliveries, delivery_items, production_requirements(_items), maturation_operations/_lines,
    purchase_orders(_items), receptions(_items), shopify_variants, shopify_inventory_levels, brands, suppliers."""
    try:
        s = (select or "").strip()
        while s.endswith(";"):
            s = s[:-1].rstrip()
        if not s:
            return "Gol."
        if ";" in s:
            return "Refuzat: o singură instrucțiune (fără ';' la mijloc)."
        if "--" in s or "/*" in s:
            return "Refuzat: fără comentarii SQL."
        if not re.match(r"(?is)^\s*(select|with)\b", s):
            return "Refuzat: doar SELECT sau WITH … SELECT."
        fara_text = re.sub(r"'(?:[^']|'')*'", "''", s)
        if "$" in fara_text and re.search(r"\$\w*\$", fara_text):
            return "Refuzat: fără dollar-quoting."
        m = _INTERZIS.search(fara_text)
        if m:
            return f"Refuzat: „{m.group(0).strip()}” nu e permis într-un SELECT read-only."
        n = max(1, min(int(limita), 1000))
        # args=None: SQL-ul omului are '%' literal în LIKE/ILIKE — nu e placeholder.
        cols, rows = _q(f"SELECT * FROM (\n{s}\n) AS _q LIMIT {n}", None)
        return _tab(cols, rows, f"{len(rows)} rânduri" + (" (atins LIMIT)" if len(rows) == n else ""))
    except Exception as e:
        return _err(e)


if __name__ == "__main__":
    mcp.run()
