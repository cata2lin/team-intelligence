# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27"]
# ///
"""emag_api.py — read-only client for the eMAG tracker (https://emg.arona.ro) HTTP API.

Finds products in the whole eMAG catalogue, pulls price / rating / review history, competitors and
sellers, performance scores (PPS), movers and the taxonomy — as a table (humans), JSON (agents) or CSV.

    uv run emag_api.py search --q "covor baie" --limit 20
    uv run emag_api.py product --pnk DVB77NBBM
    uv run emag_api.py movers --kind price_drops --window 7d --min-reviews 20
    uv run emag_api.py ranking --level category --q monitoare --tier star --format json

Auth: the bearer token in the environment variable EMAG_UI_API_TOKEN (team vault, same name).
Never prints the token. Base URL: EMAG_TRACKER_URL (default https://emg.arona.ro).
Every call is a GET; the API is read-only and needs no confirmation.
"""

from __future__ import annotations

import argparse
import csv
import io
import json
import os
import re
import sys
from collections.abc import Callable
from typing import Any

import httpx

DEFAULT_URL = "https://emg.arona.ro"
TOKEN_ENV = "EMAG_UI_API_TOKEN"
URL_ENV = "EMAG_TRACKER_URL"
TIMEOUT_S = 90.0
VAULT_HINT = (
    f"{TOKEN_ENV} is not set. Fetch it from the team vault into the environment "
    f"(core:fetch-secret / `uv run kb.py secret-get {TOKEN_ENV}`), never paste it in chat."
)

# Endpoints whose CSV the server renders itself (`;` delimited, UTF-8 BOM, Excel RO friendly).
SERVER_CSV = {"search", "movers", "ranking", "tracked"}
ISO_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}")


def _die(message: str, code: int = 1) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(code)


class Api:
    def __init__(self, base_url: str, token: str) -> None:
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=TIMEOUT_S,
            follow_redirects=False,
        )

    def get(self, path: str, params: dict[str, Any] | None = None, *, optional: bool = False) -> Any:
        clean = {k: v for k, v in (params or {}).items() if v is not None and v != "" and v is not False}
        try:
            response = self.client.get(path, params=clean)
        except httpx.HTTPError as exc:
            _die(f"cannot reach {self.client.base_url}{path}: {exc}")
        if response.status_code == 401:
            _die("401 from the API: the bearer token is missing or wrong (check the vault value)")
        if response.status_code in (302, 303):
            _die("the server redirected to the login page: this path is not under /api/ or the token failed")
        if response.status_code == 404 and optional:
            return None
        if response.status_code >= 400:
            try:
                detail = response.json().get("detail", response.text)
            except ValueError:
                detail = response.text[:300]
            _die(f"{response.status_code} on {path}: {detail}")
        if "text/csv" in response.headers.get("content-type", ""):
            return response.text
        return response.json()


# ----------------------------------------------------------------------------------------------
# actions: each returns (payload, table_columns, table_rows) — payload is what --format json prints
# ----------------------------------------------------------------------------------------------

Columns = list[tuple[str, str]]  # (key, label)
Result = tuple[Any, Columns, list[dict[str, Any]]]


def _paging(args: argparse.Namespace) -> dict[str, Any]:
    return {"page": args.page, "page_size": args.limit}


def _listing(api: Api, path: str, params: dict[str, Any], columns: Columns, args: argparse.Namespace) -> Result:
    if args.format == "csv":
        return api.get(path, {**params, "format": "csv"}), columns, []
    data = api.get(path, params)
    items = data if isinstance(data, list) else data.get("items", [])
    return data, columns, items


CATALOG_COLUMNS: Columns = [
    ("pnk", "PNK"),
    ("name", "Produs"),
    ("price", "Pret"),
    ("old_price", "Pret vechi"),
    ("discount_pct", "Disc%"),
    ("rating", "Rating"),
    ("reviews_count", "Review-uri"),
    ("availability_code", "Disponibilitate"),
    ("status", "Status"),
    ("tier", "Cadenta"),
    ("cat_tier", "Scor catalog"),
    ("category_trail", "Categorie"),
    ("last_checked_at", "Verificat"),
]


def act_search(api: Api, args: argparse.Namespace) -> Result:
    params = {
        "q": args.q,
        "department_id": args.department,
        "category_id": args.category,
        "tier": args.tier,
        "status": args.status,
        "changed_since": args.changed_since,
        "min_reviews": args.min_reviews,
        "sort": args.sort,
        **_paging(args),
    }
    return _listing(api, "/api/catalog", params, CATALOG_COLUMNS, args)


def act_product(api: Api, args: argparse.Namespace) -> Result:
    pnk = _require_pnk(args)
    detail = api.get(f"/api/catalog/{pnk}", {"days": args.days})
    deep = api.get(f"/api/products/{pnk}", {"range": args.range}, optional=True)
    item = detail["item"]
    timeline = detail.get("timeline") or {}
    facts: list[tuple[str, Any]] = [
        ("PNK", item["pnk"]),
        ("Produs", item["name"]),
        ("Brand", item.get("brand_name")),
        ("Categorie", item.get("category_trail")),
        ("Pret", _money(item.get("price"))),
        ("Pret vechi", _money(item.get("old_price"))),
        ("Reducere %", item.get("discount_pct")),
        ("Rating", item.get("rating")),
        ("Review-uri", item.get("reviews_count")),
        ("Disponibilitate", item.get("availability_code")),
        ("Status", item.get("status")),
        ("Vanzator (ultima citire)", _seller(item)),
        ("Genius", item.get("is_genius")),
        ("Badge-uri", ", ".join(item.get("badges") or []) or None),
        ("Cadenta (cat de des verificam)", f"{item.get('tier')} ({item.get('tier_reason') or '-'})"),
        ("Scor catalog (toate produsele)", f"{_cell(item.get('cat_pps'))} / {_cell(item.get('cat_tier'))}"),
        (
            "Scor profund PPS / tier",
            f"{_cell(item.get('pps'))} / {_cell(item.get('score_tier'))}"
            if item.get("pps") is not None
            else "nescorat in profunzime (doar produsele urmarite cu snapshot primesc PPS)",
        ),
        ("Loc in categorie (review-uri)", detail.get("category_rank")),
        ("Urmarit in profunzime", detail.get("tracked")),
        ("Prima aparitie", item.get("first_seen_at")),
        ("Ultima verificare", item.get("last_checked_at")),
        ("Ultima schimbare", item.get("last_change_at")),
        ("Verificari / schimbari", f"{item.get('check_count')} / {item.get('change_count')}"),
        (
            "Fereastra",
            f"ultimele {timeline.get('days')} zile: {(timeline.get('counts') or {}).get('checks', 0)} verificari, "
            f"{(timeline.get('counts') or {}).get('changed', 0)} cu schimbari, "
            f"{(timeline.get('counts') or {}).get('changes', 0)} schimbari observate",
        ),
        ("eMAG", item.get("emag_url")),
        ("Tracker", f"{api.client.base_url}{item.get('href')}"),
    ]
    for change in (timeline.get("changes") or [])[:12]:
        facts.append(
            (
                f"  {_cell(change['observed_at'])} {change['field_label']}",
                f"{_cell(change.get('old_value'))} -> {_cell(change.get('new_value'))} (sursa: {change.get('source')})",
            )
        )
    if deep and deep.get("snapshot"):
        snapshot = deep["snapshot"]
        profile = deep.get("profile") or {}
        facts += [
            ("Snapshot profund la", snapshot.get("observed_at")),
            ("  pret buy-box / initial / lowest 30d", f"{_money(snapshot.get('price'))} / {_money(snapshot.get('price_initial'))} / {_money(snapshot.get('lowest_30d'))}"),
            ("  vanzator buy-box", snapshot.get("main_vendor_name")),
            ("  oferte / concurenti / min concurent", f"{snapshot.get('offers_count')} / {snapshot.get('competitors_count')} / {_money(snapshot.get('min_competitor_price'))}"),
            ("  stoc estimat (<50) / max qty", f"{snapshot.get('stock_estimate')} / {snapshot.get('max_qty')}"),
            ("  review-uri / intrebari", f"{snapshot.get('reviews_count')} / {snapshot.get('questions_count')}"),
            ("  livrare gratuita / pickup / 6h", f"{_cell(snapshot.get('has_free_delivery'))} / {_cell(snapshot.get('has_pickup'))} / {_cell(snapshot.get('has_courier_6h'))}"),
            ("  profil complet la", _cell(profile.get("last_profile_at"))),
            ("  specificatii (grupuri)", len(profile.get("specs") or [])),
        ]
    facts += [
        ("Legenda", "Cadenta = ritmul de verificare (hot zilnic, warm la 3 zile, cold/dormant rar), NU performanta"),
        ("", "Scor catalog = cat_pps 0-100 pe tot catalogul (elite/strong/average/weak); PPS = scor profund fata de produsele-pereche (star/strong/average/weak/poor/watch)"),
        ("", "Pret vechi = pretul taiat de pe eMAG; sursa listing = cardul din categorie (fara vanzator), pdp = pagina produsului, sitemap = doar status"),
        ("", "Categorie / Disponibilitate in istoric = codurile eMAG (id categorie, id disponibilitate: 3 = in stoc); starea curenta e mai sus, in clar"),
    ]
    payload = {"catalog": detail, "deep": deep}
    rows = [{"camp": k, "valoare": v} for k, v in facts]
    return payload, [("camp", "Camp"), ("valoare", "Valoare")], rows


def act_timeline(api: Api, args: argparse.Namespace) -> Result:
    pnk = _require_pnk(args)
    data = api.get(f"/api/catalog/{pnk}/timeline", {"days": args.days})
    columns: Columns = [
        ("observed_at", "Observat"),
        ("field_label", "Camp"),
        ("old_value", "Vechi"),
        ("new_value", "Nou"),
        ("source", "Sursa"),
    ]
    return data, columns, data.get("changes", [])


def act_history(api: Api, args: argparse.Namespace) -> Result:
    pnk = _require_pnk(args)
    data = api.get(f"/api/products/{pnk}/history", {"range": args.range})
    columns: Columns = [
        ("ts", "Moment"),
        ("main_price", "Pret buy-box"),
        ("min_competitor_price", "Min concurent"),
        ("lowest_30d", "Lowest 30d"),
        ("availability_code", "Disponibilitate"),
        ("stock_estimate", "Stoc est."),
        ("main_vendor_id", "Vendor id"),
    ]
    return data, columns, data.get("points", [])


def _seller(item: dict[str, Any]) -> str | None:
    """The catalogue's effective seller with the date it was read: `vendor_source` deep = the newest
    product-page snapshot, catalog = the catalogue's own product-page read (stopped 2026-09-16)."""
    name = item.get("vendor_name")
    as_of = str(item.get("vendor_as_of") or "")[:10]
    if not name:
        return f"necunoscut (pagina citita {as_of}, fara vanzator)" if as_of else None
    source = {"deep": "snapshot profunzime", "catalog": "citire catalog"}.get(item.get("vendor_source") or "")
    when = f"citit {as_of}" if as_of else "data necunoscuta"
    stale = " - MAI VECHI DE 7 ZILE, poate nu mai e actual" if item.get("vendor_stale") else ""
    return f"{name} ({when}{', ' + source if source else ''}){stale}"


def act_competitors(api: Api, args: argparse.Namespace) -> Result:
    pnk = _require_pnk(args)
    data = api.get(f"/api/products/{pnk}/competitors")
    columns: Columns = [
        ("vendor_name", "Vanzator"),
        ("price", "Pret"),
        ("delta_vs_main_pct", "vs buy-box %"),
        ("availability_code", "Disponibilitate"),
        ("stock_estimate", "Stoc est."),
        ("shipping_cost", "Transport"),
        ("vendor_rating", "Rating vanzator"),
        ("is_main", "Buy-box"),
        ("is_fbe", "FBE"),
        ("is_ours", "Al nostru"),
    ]
    return data, columns, data


def act_score(api: Api, args: argparse.Namespace) -> Result:
    pnk = _require_pnk(args)
    data = api.get(f"/api/products/{pnk}/score", {"day": args.day})
    inputs = data.get("inputs") or {}
    peer = data.get("peer_group_name") or inputs.get("category_name") or "-"
    rows = [
        {"camp": "PPS / tier (ziua)", "valoare": f"{data.get('pps')} / {data.get('tier')} ({data.get('day')})"},
        {"camp": "Grup de referinta", "valoare": f"{peer} ({data.get('peer_level') or '-'}, {data.get('peer_size') or '-'} produse)"},
        {"camp": "Cale taxonomie", "valoare": " > ".join(p["name"] for p in data.get("path") or []) or "-"},
        {"camp": "Flags", "valoare": "; ".join(h.get("hint") or h.get("flag") for h in data.get("flag_hints") or []) or ", ".join(data.get("flags") or []) or "-"},
    ]
    for component in data.get("components", []):
        score = component.get("score")
        if score is None:
            score = inputs.get(component["key"])
        reasons = "; ".join(component.get("reasons") or [])
        rows.append(
            {
                "camp": f"  {component['label']} (pondere {component['weight']})",
                "valoare": f"{_cell(score)}" + (f" -> {reasons}" if reasons else ""),
            }
        )
    for reason in data.get("reasons", []):
        rows.append({"camp": "  motiv", "valoare": reason})
    for key in ("reviews_total", "reviews_30d", "reviews_prev_30d", "rating_adj", "offers_count_now", "competitors_now", "in_stock_share_30d", "sell_through_30d", "pps_percentile"):
        if key in inputs:
            rows.append({"camp": f"  input {key}", "valoare": _cell(inputs.get(key))})
    return data, [("camp", "Camp"), ("valoare", "Valoare")], rows


def act_reviews(api: Api, args: argparse.Namespace) -> Result:
    pnk = _require_pnk(args)
    data = api.get(f"/api/products/{pnk}/reviews")
    rows = [{"stele": bar["stars"], "numar": bar["count"], "procent": bar["share_pct"]} for bar in data.get("bars", [])]
    rows.insert(0, {"stele": "total", "numar": data.get("count"), "procent": f"rating {data.get('rating')}"})
    return data, [("stele", "Stele"), ("numar", "Numar"), ("procent", "%")], rows


def act_questions(api: Api, args: argparse.Namespace) -> Result:
    pnk = _require_pnk(args)
    data = api.get(f"/api/products/{pnk}/questions")
    columns: Columns = [
        ("published_at", "Data"),
        ("content", "Intrebare"),
        ("answers_count", "Raspunsuri"),
        ("has_official_answer", "Oficial"),
    ]
    return data, columns, data


def act_tracked(api: Api, args: argparse.Namespace) -> Result:
    params = {"q": args.q, "sort": args.sort, **_paging(args)}
    columns: Columns = [
        ("pnk", "PNK"),
        ("name", "Produs"),
        ("price", "Pret buy-box"),
        ("main_vendor_name", "Vanzator"),
        ("min_competitor_price", "Min concurent"),
        ("competitors_count", "Concurenti"),
        ("availability_code", "Disponibilitate"),
        ("stock_estimate", "Stoc est."),
        ("rating", "Rating"),
        ("reviews_count", "Review-uri"),
        ("observed_at", "Snapshot"),
    ]
    return _listing(api, "/api/products", params, columns, args)


def act_movers(api: Api, args: argparse.Namespace) -> Result:
    params = {
        "kind": args.kind,
        "window": args.window,
        "q": args.q,
        "department": args.department,
        "category": args.category,
        "brand": args.brand,
        "tier": args.tier.split(",") if args.tier else None,
        "min_reviews": args.min_reviews,
        "min_price": args.min_price,
        "max_price": args.max_price,
        "min_price_change_pct": args.min_price_change_pct,
        "max_price_change_pct": args.max_price_change_pct,
        "min_reviews_gained": args.min_reviews_gained,
        "sort": args.sort,
        **_paging(args),
    }
    columns: Columns = [
        ("pnk", "PNK"),
        ("name", "Produs"),
        ("reviews_gained", "Review-uri +"),
        ("reviews_velocity", "Review-uri/zi"),
        ("price_change_pct", "Pret %"),
        ("rating_delta", "Rating +/-"),
        ("events", "Schimbari"),
        ("price", "Pret"),
        ("reviews_count", "Review-uri"),
        ("score", "Scor"),
        ("category_trail", "Categorie"),
    ]
    return _listing(api, "/api/movers", params, columns, args)


def act_ranking(api: Api, args: argparse.Namespace) -> Result:
    params = {
        "day": args.day,
        "level": args.level,
        "node_id": args.node,
        "facet": args.facet,
        "facet_value": args.facet_value,
        "tier": args.tier,
        "flag": args.flag,
        "lens": args.lens,
        "q": args.q,
        "sort": args.sort,
        **_paging(args),
    }
    columns: Columns = [
        ("pnk", "PNK"),
        ("name", "Produs"),
        ("tier", "Tier"),
        ("pps", "PPS"),
        ("demand", "Cerere"),
        ("satisfaction", "Satisfactie"),
        ("market", "Piata"),
        ("availability", "Disponib."),
        ("momentum", "Momentum"),
        ("reviews_30d", "Review 30d"),
        ("rating", "Rating"),
        ("price", "Pret"),
        ("offers_count", "Oferte"),
        ("estimated_units_30d", "Unit. est. 30d"),
        ("category_name", "Categorie"),
    ]
    return _listing(api, "/api/ranking", params, columns, args)


def act_taxonomy(api: Api, args: argparse.Namespace) -> Result:
    data = api.get("/api/taxonomy", {"root_node_id": args.node, "depth": args.depth, "day": args.day})
    rows: list[dict[str, Any]] = []

    def walk(nodes: list[dict[str, Any]], depth: int) -> None:
        for node in nodes:
            rows.append(
                {
                    "node_id": node["node_id"],
                    "level": node["level"],
                    "name": ("  " * depth) + node["name"],
                    "product_count": node.get("product_count"),
                    "avg_pps": node.get("avg_pps"),
                    "tier_counts": json.dumps(node.get("tier_counts") or {}, ensure_ascii=False),
                }
            )
            walk(node.get("children") or [], depth + 1)

    walk(data, 0)
    columns: Columns = [
        ("node_id", "Nod"),
        ("level", "Nivel"),
        ("name", "Nume"),
        ("product_count", "Produse scorate"),
        ("avg_pps", "PPS mediu"),
        ("tier_counts", "Tiers"),
    ]
    return data, columns, rows


def act_taxonomy_node(api: Api, args: argparse.Namespace) -> Result:
    if args.node is None:
        _die("--node <id> is required (find ids with `taxonomy`)", 2)
    data = api.get(f"/api/taxonomy/{args.node}", {"day": args.day})
    rows = [
        {"camp": "Nod", "valoare": f"{data['node_id']} {data['level']} {data['name']} (eMAG id {data.get('emag_id')})"},
        {"camp": "Cale", "valoare": " > ".join(p["name"] for p in data.get("path", []))},
        {"camp": "Produse scorate / PPS mediu", "valoare": f"{data.get('product_count')} / {data.get('avg_pps')}"},
        {"camp": "Tiers", "valoare": json.dumps(data.get("tier_counts") or {}, ensure_ascii=False)},
        {"camp": "Copii", "valoare": ", ".join(f"{c['name']} (#{c['node_id']})" for c in data.get("children", [])) or "-"},
    ]
    for facet in data.get("facets", []):
        values = ", ".join(f"{v['value']} ({v['count']})" for v in facet.get("values", [])[:8])
        rows.append({"camp": f"  facet {facet['name']}", "valoare": values})
    return data, [("camp", "Camp"), ("valoare", "Valoare")], rows


def act_categories(api: Api, args: argparse.Namespace) -> Result:
    params = {"sort": args.sort, "department_id": args.department, "overdue_only": args.overdue_only, **_paging(args)}
    columns: Columns = [
        ("category_id", "Id"),
        ("name", "Categorie"),
        ("department_name", "Departament"),
        ("total_products", "Produse eMAG"),
        ("products", "In catalog"),
        ("hot_pages", "Pagini hot"),
        ("last_top_sweep_at", "Ultimul sweep top"),
        ("last_full_sweep_at", "Ultimul sweep complet"),
        ("overdue", "Intarziat"),
    ]
    return _listing(api, "/api/catalog/categories", params, columns, args)


def act_sellers(api: Api, args: argparse.Namespace) -> Result:
    data = api.get("/api/sellers")
    needle = (args.q or "").lower()
    rows = [s for s in data if not needle or needle in (s.get("name") or "").lower()]
    key = args.sort or "-offers_tracked"
    reverse = key.startswith("-")
    field = key.lstrip("-")
    rows.sort(key=lambda s: (s.get(field) is None, s.get(field) or 0), reverse=reverse)
    rows = rows[: args.limit]
    columns: Columns = [
        ("vendor_id", "Id"),
        ("name", "Vanzator"),
        ("vendor_type", "Tip (1 eMAG / 3 marketplace)"),
        ("offers_tracked", "Oferte urmarite"),
        ("buy_button_share_pct", "Buy-box %"),
        ("last_rating", "Rating"),
        ("last_positive_share", "Pozitiv %"),
        ("is_fde_eligible", "FBE"),
        ("last_seen_at", "Vazut ultima data"),
    ]
    return rows, columns, rows


def act_seller(api: Api, args: argparse.Namespace) -> Result:
    if args.vendor is None:
        _die("--vendor <id> is required (find ids with `sellers --q nume`)", 2)
    data = api.get(f"/api/sellers/{args.vendor}")
    kpis = data.get("kpis") or {}
    vendor = data.get("vendor") or {}
    offers = sorted(data.get("offers", []), key=lambda o: (o.get("is_main") is not True, o.get("price") or 0))
    payload = {**data, "offers": offers[: args.limit]}
    rows = [
        {"camp": "Vanzator", "valoare": f"{vendor.get('name')} (#{vendor.get('vendor_id')}, tip {vendor.get('vendor_type')})"},
        {"camp": "Oferte urmarite / observatii", "valoare": f"{kpis.get('offers_tracked')} / {kpis.get('observations')}"},
        {"camp": "Buy-box % / in stoc %", "valoare": f"{kpis.get('buy_button_share_pct')} / {kpis.get('in_stock_share_pct')}"},
        {"camp": "Index pret mediu / transport mediu", "valoare": f"{kpis.get('avg_price_index')} / {_money(kpis.get('avg_shipping_cost'))}"},
        {"camp": "Rating / pozitiv % / trend", "valoare": f"{kpis.get('rating_latest')} / {kpis.get('positive_share_latest')} / {kpis.get('rating_trend')}"},
        {"camp": "Fereastra (zile)", "valoare": data.get("window_days")},
    ]
    for offer in offers[: args.limit]:
        rows.append(
            {
                "camp": f"  {offer['pnk']} {'[buy-box]' if offer.get('is_main') else ''}",
                "valoare": f"{_money(offer.get('price'))} vs buy-box {_money(offer.get('main_price'))} ({offer.get('delta_vs_main')}) {offer.get('availability_code')} - {(offer.get('name') or '')[:70]}",
            }
        )
    return payload, [("camp", "Camp"), ("valoare", "Valoare")], rows


def act_summary(api: Api, args: argparse.Namespace) -> Result:
    catalog = api.get("/api/catalog/summary")
    deep = api.get("/api/summary")
    health = api.get("/api/health")
    rows = [
        {"camp": "Stare", "valoare": f"{health.get('status')} (db {health.get('db', {}).get('latency_ms')} ms, ultimul snapshot {health.get('last_snapshot_at')})"},
        {"camp": "Catalog: produse / active", "valoare": f"{catalog.get('total')} / {catalog.get('active')}"},
        {"camp": "Catalog: cadenta", "valoare": json.dumps(catalog.get("tiers"), ensure_ascii=False)},
        {"camp": "Catalog: statusuri", "valoare": json.dumps(catalog.get("statuses"), ensure_ascii=False)},
        {"camp": "Verificari azi / 7 zile", "valoare": f"{catalog.get('checks_today')} / {catalog.get('checks_7d')}"},
        {"camp": "Schimbari azi / 7 zile", "valoare": f"{catalog.get('changes_today')} / {catalog.get('changes_7d')}"},
        {"camp": "Acoperire 7 zile %", "valoare": catalog.get("coverage_pct")},
        {"camp": "Ultimul sweep", "valoare": f"{catalog.get('last_sweep_kind')} la {catalog.get('last_sweep_at')} (in urma: {catalog.get('sweep_behind')})"},
        {"camp": "Urmarite in profunzime / cu snapshot", "valoare": f"{deep.get('tracked_active')} / {deep.get('with_snapshot')}"},
        {"camp": "Vanzatori cunoscuti", "valoare": deep.get("vendors")},
        {"camp": "Buy-box al nostru (fereastra)", "valoare": f"{deep.get('buy_button_ours')} din {deep.get('with_snapshot')} ({deep.get('window_days')} zile)"},
    ]
    return {"catalog": catalog, "deep": deep, "health": health}, [("camp", "Camp"), ("valoare", "Valoare")], rows


def act_sweeps(api: Api, args: argparse.Namespace) -> Result:
    data = api.get("/api/catalog/sweeps", {"limit": args.limit})
    columns: Columns = [
        ("sweep_id", "Id"),
        ("kind", "Tip"),
        ("started_at", "Inceput"),
        ("duration_s", "Durata s"),
        ("pages", "Pagini"),
        ("products_seen", "Produse vazute"),
        ("changed", "Schimbate"),
        ("new", "Noi"),
        ("errors", "Erori"),
    ]
    return data, columns, data.get("items", [])


ACTIONS: dict[str, tuple[Callable[[Api, argparse.Namespace], Result], str]] = {
    "summary": (act_summary, "state of the tracker: catalogue size, cadence tiers, freshness, deep tracking"),
    "search": (act_search, "find products in the whole catalogue (name / PNK / brand; filters)"),
    "product": (act_product, "one product: listing facts + change history (+ deep snapshot when tracked)"),
    "timeline": (act_timeline, "every observed change of a product (price, rating, reviews, stock, status)"),
    "history": (act_history, "deep price history of a tracked product (buy-box vs min competitor)"),
    "competitors": (act_competitors, "every seller on a tracked product at the last deep snapshot"),
    "score": (act_score, "performance score (PPS) with its six components and reasons"),
    "reviews": (act_reviews, "rating distribution of a tracked product"),
    "questions": (act_questions, "customer questions on a tracked product"),
    "tracked": (act_tracked, "the deep-tracked list with the latest snapshot per product"),
    "movers": (act_movers, "what moved in a window: rising, price_drops, back_in_stock, tier_ups, all"),
    "ranking": (act_ranking, "scored ranking by taxonomy level / node / facet / tier / flag"),
    "taxonomy": (act_taxonomy, "the eMAG taxonomy tree with scored-product counts"),
    "taxonomy-node": (act_taxonomy_node, "one taxonomy node: path, children, facets"),
    "categories": (act_categories, "the 1 809 sweep categories and their sweep state"),
    "sellers": (act_sellers, "every seller seen, filter by name, sort by offers / buy-box share"),
    "seller": (act_seller, "one seller: KPIs, offers vs buy-box, price changes"),
    "sweeps": (act_sweeps, "recent catalogue sweeps"),
}


# ----------------------------------------------------------------------------------------------
# rendering
# ----------------------------------------------------------------------------------------------


def _money(value: Any) -> str | None:
    if value is None:
        return None
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return str(value)


def _cell(value: Any, width: int = 70) -> str:
    if value is None:
        return "-"
    if isinstance(value, bool):
        return "da" if value else "nu"
    if isinstance(value, float):
        text = f"{value:.2f}".rstrip("0").rstrip(".")
    elif isinstance(value, (list, dict)):
        text = json.dumps(value, ensure_ascii=False)
    elif isinstance(value, str) and ISO_TIMESTAMP.match(value):
        text = value[:16].replace("T", " ")  # 2026-08-27T12:22:09.169371Z -> 2026-08-27 12:22 (UTC)
    else:
        text = str(value)
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def render_table(columns: Columns, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "0 rezultate"
    keys = [k for k, _ in columns]
    labels = [label for _, label in columns]
    table = [[_cell(row.get(k)) for k in keys] for row in rows]
    widths = [max(len(labels[i]), *(len(r[i]) for r in table)) for i in range(len(keys))]
    line = "  ".join(labels[i].ljust(widths[i]) for i in range(len(keys)))
    out = [line, "  ".join("-" * w for w in widths)]
    out += ["  ".join(r[i].ljust(widths[i]) for i in range(len(keys))) for r in table]
    return "\n".join(out)


def render_csv(columns: Columns, rows: list[dict[str, Any]]) -> str:
    buffer = io.StringIO()
    writer = csv.writer(buffer, delimiter=";", lineterminator="\n")
    writer.writerow([label for _, label in columns])
    for row in rows:
        writer.writerow([_cell(row.get(k), width=10_000) for k, _ in columns])
    return "﻿" + buffer.getvalue()


def _require_pnk(args: argparse.Namespace) -> str:
    if not args.pnk:
        _die("--pnk <PNK> is required (find it with `search --q ...`)", 2)
    return args.pnk.strip().upper()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only client for the eMAG tracker API (emg.arona.ro).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="actions:\n" + "\n".join(f"  {name:14s} {doc}" for name, (_, doc) in ACTIONS.items()),
    )
    parser.add_argument("action_positional", nargs="?", metavar="action", help="one of the actions below")
    parser.add_argument("--action", dest="action", help="the action (alternative to the positional)")
    parser.add_argument("--q", help="text filter on the product NAME / PNK / brand (substring), not a category: for a category use --category (eMAG id from search/categories) or --node (taxonomy id) [search, tracked, movers, ranking, sellers]")
    parser.add_argument("--pnk", help="product key, e.g. DVB77NBBM")
    parser.add_argument("--kind", default="rising", help="movers: rising | price_drops | back_in_stock | tier_ups | all")
    parser.add_argument("--window", default="7d", help="movers: 7d | 14d | 30d")
    parser.add_argument("--days", type=int, default=90, help="product / timeline: how many days back (1..730)")
    parser.add_argument("--range", default="30d", help="history / product deep: 7d | 30d | 90d | all")
    parser.add_argument("--limit", type=int, default=25, help="rows per page (1..1000)")
    parser.add_argument("--page", type=int, default=1, help="page number")
    parser.add_argument("--sort", help="sort key, '-' prefix = descending; the API lists the valid keys on a wrong one")
    parser.add_argument("--tier", help="search: hot|warm|cold|dormant|new; ranking: star|strong|average|weak|poor|watch; movers: elite,strong,average,weak")
    parser.add_argument("--status", help="search: active | unavailable | delisted | unknown")
    parser.add_argument("--department", type=int, help="eMAG department id (search, movers, categories)")
    parser.add_argument("--category", type=int, help="eMAG category id (search, movers)")
    parser.add_argument("--brand", help="movers: brand name")
    parser.add_argument("--min-reviews", dest="min_reviews", type=int, help="search / movers: at least N reviews")
    parser.add_argument("--changed-since", dest="changed_since", help="search: 1d | 7d | 30d | 90d | YYYY-MM-DD")
    parser.add_argument("--min-price", dest="min_price", type=float, help="movers: price floor (RON)")
    parser.add_argument("--max-price", dest="max_price", type=float, help="movers: price ceiling (RON)")
    parser.add_argument("--min-price-change-pct", dest="min_price_change_pct", type=float, help="movers: e.g. -50")
    parser.add_argument("--max-price-change-pct", dest="max_price_change_pct", type=float, help="movers: e.g. -10")
    parser.add_argument("--min-reviews-gained", dest="min_reviews_gained", type=int, help="movers: reviews gained in the window")
    parser.add_argument("--node", type=int, help="taxonomy node id (ranking, taxonomy, taxonomy-node)")
    parser.add_argument("--level", help="ranking: department | subdepartment | category | supercategory | brand_category")
    parser.add_argument("--lens", default="sell", help="ranking: sell (what sells) | source (what to source)")
    parser.add_argument("--flag", help="ranking: too_new | avoid_quality | unavailable | delisted | suspicious_reviews | promo_dependent | single_seller")
    parser.add_argument("--facet", help="ranking: facet name, e.g. 'Tip SIM'")
    parser.add_argument("--facet-value", dest="facet_value", help="ranking: facet value, e.g. 'Dual SIM'")
    parser.add_argument("--day", help="ranking / score / taxonomy: score day YYYY-MM-DD (default latest)")
    parser.add_argument("--depth", type=int, default=2, help="taxonomy: tree depth (1..6)")
    parser.add_argument("--vendor", type=int, help="seller: vendor id")
    parser.add_argument("--overdue-only", dest="overdue_only", action="store_true", help="categories: only overdue sweeps")
    parser.add_argument("--format", choices=("table", "json", "csv"), default="table", help="output format")
    return parser


def main(argv: list[str] | None = None) -> None:
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    action = args.action or args.action_positional
    if not action:
        build_parser().print_help()
        sys.exit(2)
    if action not in ACTIONS:
        _die(f"unknown action {action!r}; expected one of {', '.join(ACTIONS)}", 2)
    token = os.environ.get(TOKEN_ENV, "").strip()
    if not token:
        _die(VAULT_HINT, 2)
    api = Api(os.environ.get(URL_ENV, DEFAULT_URL), token)
    payload, columns, rows = ACTIONS[action][0](api, args)
    if args.format == "json":
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.format == "csv":
        print(payload if isinstance(payload, str) else render_csv(columns, rows), end="")
    else:
        print(render_table(columns, rows))
        if isinstance(payload, dict) and "total" in payload:
            print(f"\n{payload['total']} rezultate, pagina {payload.get('page')}/{payload.get('pages')}")


if __name__ == "__main__":
    main()
