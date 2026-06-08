# /// script
# requires-python = ">=3.10"
# dependencies = ["requests>=2.31", "msal>=1.24", "psycopg2-binary>=2.9"]
# ///
"""Place UGC orders from Cristina's Comenzi sheet.

Per skills/placing-ugc-orders.md.

Usage:
    .venv/bin/python scripts/place_ugc_order.py <row_number>

`row_number` is 1-based as shown in Excel (so row 2 = first data row).
The script:
  1. Reads the row from the OneDrive workbook.
  2. Resolves the store from col A (`site`).
  3. Parses phone, address, products (col E).
  4. Finds-or-creates the customer.
  5. Creates a draft order (100% discount, 0 shipping) and completes it as PAID.
  6. Writes status back to H{row}/I{row} and paints the row green/red.

Refuses to overwrite a row that already has a status in H.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent))
from microsoft_auth import get_token  # noqa: E402

# ---- env: secrets come from the SharedClaude knowledge base, not a file ----
ROOT = Path(__file__).parent.parent
from kb_env import load_secrets_into_env  # noqa: E402
load_secrets_into_env()

# ---- workbook coordinates ----
DRIVE = "BAFD4BFC079B4528"
ITEM = "BAFD4BFC079B4528!s84358327961b47909300b70f8e9364e9"
MS_BASE = f"https://graph.microsoft.com/v1.0/drives/{DRIVE}/items/{ITEM}/workbook"

# ---- store map (site domain → Shopify store domain env key) ----
STORE_MAP = {
    "labnoir.ro": "SHOPIFY_ARONA_LABNOIR_DOMAIN",
    "esteban.ro": "SHOPIFY_ARONA_ESTEBAN_DOMAIN",
    "george-talent.ro": "SHOPIFY_ARONA_GT_DOMAIN",
    "nubra.ro": "SHOPIFY_ARONA_NUBRA_DOMAIN",
}
STORE_ADMIN_HANDLE = {
    "SHOPIFY_ARONA_LABNOIR_DOMAIN": "31k0py-bi",
    "SHOPIFY_ARONA_ESTEBAN_DOMAIN": "6f9e22-9d",
    "SHOPIFY_ARONA_GT_DOMAIN": "ix5bxc-hr",
    "SHOPIFY_ARONA_NUBRA_DOMAIN": "bmuwvv-jy",
}

# ---- per-store rules ----
# Each entry describes how that store's column-E references map to SKUs and
# what the "default cart" looks like when col E is empty.
#
# - sku_prefixes: list of SKU patterns to try when Cristina writes `nr.X`.
#                 First match wins. {n} is replaced with the number.
# - default_skus: hard-coded fillers when col E is silent / vague (in order).
#                 None = fall back to bestsellers.
# - extra_line:  optional dict {sku_pattern, qty} added on top of the 3
#                main perfumes. Used by Lab Noir to bundle a 100ml.
#                If sku_pattern == "random-100ml" → pick a random in-stock
#                100ml variant.
STORE_RULES = {
    "SHOPIFY_ARONA_LABNOIR_DOMAIN": {
        "sku_prefixes": ["{n}-50ml"],
        "default_skus": ["49-50ml", "47-50ml", "71-50ml"],
        "extra_line": {"sku_pattern": "random-100ml"},
    },
    "SHOPIFY_ARONA_GT_DOMAIN": {
        # Zeylin (`zn-`) line is sold via GT; Cristina's nr.X usually means Zeylin.
        "sku_prefixes": ["zn-{n}", "gt-{n}", "{n}"],
        "default_skus": None,  # use bestsellers
        "extra_line": None,
    },
    "SHOPIFY_ARONA_ESTEBAN_DOMAIN": {
        "sku_prefixes": ["{n}", "esteban-{n}"],
        "default_skus": None,
        "extra_line": None,
    },
    "SHOPIFY_ARONA_NUBRA_DOMAIN": {
        "sku_prefixes": ["{n}", "nubra-{n}"],
        "default_skus": None,
        "extra_line": None,
    },
}

RO_MONTHS = [
    "Ianuarie", "Februarie", "Martie", "Aprilie", "Mai", "Iunie",
    "Iulie", "August", "Septembrie", "Octombrie", "Noiembrie", "Decembrie",
]

# Normalize what Cristina writes in the `Județ` column to what Shopify
# stores. Shopify accepts most of these as-is, but București ↔ Bucharest is
# the common one. Diacritic-insensitive on the lookup side.
PROVINCE_NORMALIZE = {
    "bucuresti": "Bucharest",
    "bucurești": "Bucharest",
    "ilfov": "Ilfov",
}


# ---- MS Graph helpers ----
def ms_headers() -> dict:
    return {"Authorization": f"Bearer {get_token()}"}


def read_comenzi() -> list[list]:
    r = requests.get(
        f"{MS_BASE}/worksheets('Comenzi')/usedRange(valuesOnly=true)?$select=values",
        headers=ms_headers(),
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["values"]


def patch_status(row: int, status: str, msg: str, color: str) -> None:
    h = {**ms_headers(), "Content-Type": "application/json"}
    requests.patch(
        f"{MS_BASE}/worksheets('Comenzi')/range(address='K{row}:L{row}')",
        headers=h, json={"values": [[status, msg]]}, timeout=30,
    ).raise_for_status()
    requests.patch(
        f"{MS_BASE}/worksheets('Comenzi')/range(address='A{row}:N{row}')/format/fill",
        headers=h, json={"color": color}, timeout=30,
    ).raise_for_status()


# ---- Shopify helpers ----
class Shopify:
    def __init__(self, domain_env_key: str):
        domain = os.environ[domain_env_key]
        ver = os.environ["SHOPIFY_ARONA_API_VERSION"]
        token = requests.post(
            f"https://{domain}/admin/oauth/access_token",
            json={
                "client_id": os.environ["SHOPIFY_ARONA_CLIENT_ID"],
                "client_secret": os.environ["SHOPIFY_ARONA_CLIENT_SECRET"],
                "grant_type": "client_credentials",
            },
            timeout=15,
        ).json()["access_token"]
        self.domain = domain
        self.gql_url = f"https://{domain}/admin/api/{ver}/graphql.json"
        self.headers = {"X-Shopify-Access-Token": token, "Content-Type": "application/json"}
        self.admin_handle = STORE_ADMIN_HANDLE[domain_env_key]
        self.rules = STORE_RULES.get(domain_env_key, {
            "sku_prefixes": ["{n}"], "default_skus": None, "extra_line": None,
        })
        self.env_key = domain_env_key

    def gql(self, query: str, variables: dict | None = None) -> dict:
        r = requests.post(
            self.gql_url, headers=self.headers,
            json={"query": query, "variables": variables or {}}, timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def find_variant_by_sku(self, sku: str) -> dict | None:
        r = self.gql(
            """query($q:String!){productVariants(first:5,query:$q){edges{node{
                id sku title inventoryQuantity product{id title}}}}}""",
            {"q": f"sku:{sku}"},
        )
        edges = r["data"]["productVariants"]["edges"]
        # exact sku match preferred
        for e in edges:
            if (e["node"].get("sku") or "").lower() == sku.lower():
                return e["node"]
        return edges[0]["node"] if edges else None

    def search_product(self, term: str, first: int = 10) -> list[dict]:
        r = self.gql(
            """query($q:String!,$n:Int!){products(first:$n,query:$q){edges{node{
                id title
                variants(first:5){edges{node{id sku title inventoryQuantity}}}}}}}""",
            {"q": f"title:*{term}*", "n": first},
        )
        return [e["node"] for e in r["data"]["products"]["edges"]]

    def bestseller_variants(self, days: int = 30, top: int = 10) -> list[dict]:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        counter: Counter = Counter()
        meta: dict[str, dict] = {}
        cursor = None
        while True:
            r = self.gql(
                """query($c:String,$q:String!){orders(first:100,after:$c,query:$q){
                    pageInfo{hasNextPage endCursor}
                    edges{node{lineItems(first:50){edges{node{
                        quantity variant{id sku title inventoryQuantity
                            product{id title}}}}}}}}}""",
                {"c": cursor, "q": f"created_at:>={since} financial_status:paid"},
            )
            d = r["data"]["orders"]
            for e in d["edges"]:
                for li in e["node"]["lineItems"]["edges"]:
                    v = li["node"]["variant"]
                    if not v:
                        continue
                    counter[v["id"]] += li["node"]["quantity"]
                    meta[v["id"]] = {
                        "id": v["id"], "sku": v.get("sku") or "",
                        "title": v.get("title"),
                        "inventoryQuantity": v.get("inventoryQuantity"),
                        "product_id": v["product"]["id"],
                        "product_title": v["product"]["title"],
                    }
            if not d["pageInfo"]["hasNextPage"]:
                break
            cursor = d["pageInfo"]["endCursor"]
        # Aggregate to product level, pick representative variant
        prod_count: Counter = Counter()
        prod_vars: dict[str, list[dict]] = {}
        for vid, qty in counter.items():
            m = meta[vid]
            prod_count[m["product_id"]] += qty
            prod_vars.setdefault(m["product_id"], []).append(m)
        out = []
        for pid, qty in prod_count.most_common(top):
            variants = prod_vars[pid]
            chosen = next(
                (v for v in variants if "50" in (v["title"] or "").lower()),
                variants[0],
            )
            out.append({**chosen, "qty_sold": qty})
        return out

    def random_variant_by_sku_suffix(self, suffix: str, exclude_skus: list[str] | None = None) -> dict | None:
        """Pick a random in-stock variant whose SKU ends with `suffix`.

        Used for Lab Noir's bonus 100ml line. We page through all products
        once, collect every variant matching the suffix that is in stock,
        and pick uniformly at random.
        """
        import random
        exclude = set(exclude_skus or [])
        candidates: list[dict] = []
        cursor = None
        while True:
            r = self.gql(
                """query($c:String){products(first:50,after:$c){
                    pageInfo{hasNextPage endCursor}
                    edges{node{title
                        variants(first:10){edges{node{id sku title
                            inventoryQuantity product{id title}}}}}}}}""",
                {"c": cursor},
            )
            d = r["data"]["products"]
            for pe in d["edges"]:
                ptitle = pe["node"]["title"]
                for ve in pe["node"]["variants"]["edges"]:
                    v = ve["node"]
                    sku = v.get("sku") or ""
                    if not sku.endswith(suffix):
                        continue
                    if sku in exclude:
                        continue
                    if (v.get("inventoryQuantity") or 0) <= 0:
                        continue
                    candidates.append({**v, "product_title": ptitle})
            if not d["pageInfo"]["hasNextPage"]:
                break
            cursor = d["pageInfo"]["endCursor"]
        return random.choice(candidates) if candidates else None

    def upsert_customer(self, phone_e164: str, email: str, fn: str, ln: str) -> str:
        r = self.gql(
            'query($q:String!){customers(first:5,query:$q){edges{node{id email}}}}',
            {"q": f"phone:{phone_e164}"},
        )
        edges = r["data"]["customers"]["edges"]
        if edges:
            cid = edges[0]["node"]["id"]
            if edges[0]["node"]["email"] != email:
                self.gql(
                    "mutation($i:CustomerInput!){customerUpdate(input:$i){"
                    "userErrors{field message}}}",
                    {"i": {"id": cid, "email": email, "tags": ["UGC"]}},
                )
            return cid
        r = self.gql(
            "mutation($i:CustomerInput!){customerCreate(input:$i){"
            "customer{id} userErrors{field message}}}",
            {"i": {"firstName": fn, "lastName": ln, "phone": phone_e164,
                   "email": email, "tags": ["UGC"]}},
        )
        err = r["data"]["customerCreate"]["userErrors"]
        if err:
            raise RuntimeError(f"customerCreate: {err}")
        return r["data"]["customerCreate"]["customer"]["id"]

    def place_ugc_order(
        self,
        *,
        customer_id: str,
        ship_addr: dict,
        line_variant_ids: list[str],
        influencer_url: str,
    ) -> dict:
        draft_input = {
            "note": f"Comanda Influencer · {influencer_url}",
            "tags": ["Comanda Influencer", "influencer"],
            "customAttributes": [{"key": "influencer_url", "value": influencer_url}],
            "purchasingEntity": {"customerId": customer_id},
            "shippingAddress": ship_addr, "billingAddress": ship_addr,
            "useCustomerDefaultAddress": False,
            "lineItems": [{"variantId": vid, "quantity": 1} for vid in line_variant_ids],
            "appliedDiscount": {
                "value": 100, "valueType": "PERCENTAGE",
                "title": "Comanda Influencer", "description": "100% UGC",
            },
            "shippingLine": {
                "price": "0.00",
                "title": "Comanda Influencer — livrare gratuita",
            },
        }
        r = self.gql(
            "mutation($i:DraftOrderInput!){draftOrderCreate(input:$i){"
            "draftOrder{id name totalPrice} userErrors{field message}}}",
            {"i": draft_input},
        )
        err = r["data"]["draftOrderCreate"]["userErrors"]
        if err:
            raise RuntimeError(f"draftOrderCreate: {err}")
        do = r["data"]["draftOrderCreate"]["draftOrder"]
        r = self.gql(
            "mutation($id:ID!){draftOrderComplete(id:$id,paymentPending:false){"
            "draftOrder{order{id name totalPrice displayFinancialStatus}}"
            " userErrors{field message}}}",
            {"id": do["id"]},
        )
        err = r["data"]["draftOrderComplete"]["userErrors"]
        if err:
            raise RuntimeError(f"draftOrderComplete: {err}")
        return r["data"]["draftOrderComplete"]["draftOrder"]["order"]


# ---- product matching from col E ----
NUMBER_TOKEN = re.compile(r'\bnr\.?\s*(\d+)\b', re.I)
BARE_NUMBER = re.compile(r'^\s*(\d{1,4})\s*$')


def split_terms(arome: str) -> list[str]:
    """Split col E into terms by comma / 'și' / '&' (but keep '&' inside names)."""
    # naive: split on commas then on standalone ' și '
    parts: list[str] = []
    for chunk in arome.split(','):
        for sub in re.split(r'\s+și\s+', chunk, flags=re.I):
            sub = sub.strip(' .')
            if sub:
                parts.append(sub)
    return parts


def match_term_to_variant(term: str, sh: Shopify) -> tuple[dict | None, str]:
    """Return (variant or None, note). Note describes what we picked."""
    # 1. nr.X → SKU lookup. Use the store's configured prefix list.
    #    Also accepts a bare number (e.g. "12" or "  92 "), which Cristina
    #    sometimes writes without the "nr." prefix.
    m = NUMBER_TOKEN.search(term)
    if not m:
        m = BARE_NUMBER.match(term)
    if m:
        n = m.group(1)
        prefixes = sh.rules.get("sku_prefixes") or ["{n}"]
        for pat in prefixes:
            sku_try = pat.format(n=n)
            v = sh.find_variant_by_sku(sku_try)
            if v:
                return v, f"sku:{v['sku']}"
        return None, f"produs neidentificat: {term}"

    # 2. Named product → title search
    # Strip noise words
    cleaned = re.sub(r"\b(ceva de|și|de|la|the)\b", "", term, flags=re.I).strip()
    # Try the strongest token (longest word)
    tokens = [t for t in re.split(r'\s+', cleaned) if len(t) > 3]
    if not tokens:
        return None, f"produs neidentificat: {term}"
    # Try progressively shorter queries
    for q in (cleaned, *sorted(tokens, key=len, reverse=True)):
        prods = sh.search_product(q)
        if prods:
            # Pick first product, prefer 50ml variant in stock
            for p in prods:
                variants = [e["node"] for e in p["variants"]["edges"]]
                in_stock = [v for v in variants
                            if (v.get("inventoryQuantity") or 0) > 0]
                pool = in_stock or variants
                pick = next(
                    (v for v in pool if "50" in (v["title"] or "").lower()),
                    pool[0] if pool else None,
                )
                if pick:
                    note = f'"{q}" → {p["title"]} ({pick["sku"]})'
                    if not in_stock:
                        note += " [OOS]"
                    return {**pick, "product_title": p["title"]}, note
    return None, f"produs neidentificat: {term}"


# ---- main ----
def process_row(row_num: int, *, dry_run: bool = False) -> dict:
    rows = read_comenzi()
    if row_num < 2 or row_num > len(rows):
        raise ValueError(f"row {row_num} out of range (1..{len(rows)})")
    row = rows[row_num - 1]
    # New schema (post-2026-06-05 migration):
    # A Site, B Luna, C Nume, D Link, E Arome,
    # F Adresa, G Oraș, H Județ, I Cod poștal,
    # J Tel, K Status, L Mesaj, M Status colet, N Content
    site = row[0] if len(row) > 0 else ''
    nume = row[2] if len(row) > 2 else ''
    link = row[3] if len(row) > 3 else ''
    arome = row[4] if len(row) > 4 else ''
    addr_street = row[5] if len(row) > 5 else ''
    city = row[6] if len(row) > 6 else ''
    province = row[7] if len(row) > 7 else ''
    zipc = row[8] if len(row) > 8 else ''
    tel = row[9] if len(row) > 9 else ''
    status = row[10] if len(row) > 10 else ''
    if status:
        return {"row": row_num, "skipped": True, "reason": f"status already {status!r}"}

    # phone
    phone = "0" + str(int(tel)).zfill(9) if isinstance(tel, (int, float)) else str(tel)
    phone = phone.strip()
    if phone.startswith("+40"):
        phone = "0" + phone[3:]
    phone_e164 = "+40" + phone[1:]

    # name
    parts = nume.strip().split()
    fn = " ".join(parts[:-1]) if len(parts) > 1 else (parts[0] if parts else '')
    ln = parts[-1] if len(parts) > 1 else ""

    email = f"ugc+{phone}@arona.ro"

    # Address: pulled directly from F/G/H/I. Refuse rows missing required
    # fields; never invent missing data.
    addr_street = (addr_street or '').strip()
    city = (city or '').strip()
    province = (province or '').strip()
    province = PROVINCE_NORMALIZE.get(province.lower(), province)
    zipc = str(zipc or '').strip()
    if isinstance(zipc, str) and zipc.endswith('.0'):
        zipc = zipc[:-2]
    # Pad short zips that lost their leading zero somewhere.
    if zipc.isdigit() and len(zipc) < 6:
        zipc = zipc.zfill(6)

    missing = []
    if not addr_street:
        missing.append("strad\u0103 (F)")
    if not city:
        missing.append("ora\u0219 (G)")
    if not province:
        missing.append("jude\u021b (H)")
    if missing:
        return {
            "row": row_num,
            "error": f"address-incomplete: lips\u0103 {' + '.join(missing)}",
            "raw": {"F": addr_street, "G": city, "H": province, "I": zipc},
        }

    # Split address into address1/address2 if street contains apt details.
    address1 = addr_street
    address2: str | None = None
    if ',' in addr_street:
        head, _, tail = addr_street.partition(',')
        head_s, tail_s = head.strip(), tail.strip()
        if re.search(r'\b(bl\.?|sc\.?|et\.?|ap\.?|bloc|scara|etaj|sector|sec\.?|apartament|parter)\b', tail_s, re.I):
            address1 = head_s
            address2 = tail_s

    ship = {
        "address1": address1,
        "address2": address2,
        "city": city,
        "province": province,
        "zip": zipc or None,
        "countryCode": "RO",
        "firstName": fn,
        "lastName": ln,
        "phone": phone_e164,
    }

    # store
    site_key = site.strip().lower()
    if site_key not in STORE_MAP:
        raise RuntimeError(f"Unknown site {site!r}")
    sh = Shopify(STORE_MAP[site_key])

    # products
    terms = split_terms(arome)
    line_items: list[dict] = []  # list of variant nodes
    notes: list[str] = []

    for t in terms:
        v, note = match_term_to_variant(t, sh)
        notes.append(note)
        if v:
            line_items.append(v)

    # Substitute OOS items with bestsellers (or store defaults)
    bs_cache: list[dict] | None = None

    def get_substitutes() -> list[dict]:
        nonlocal bs_cache
        if bs_cache is not None:
            return bs_cache
        # Prefer store's configured defaults; otherwise bestsellers.
        default_skus = sh.rules.get("default_skus")
        if default_skus:
            bs_cache = []
            for s in default_skus:
                v = sh.find_variant_by_sku(s)
                if v:  # accept oversold (negative inventory) for fixed defaults
                    bs_cache.append(v)
            # Top up with bestsellers if defaults are short.
            if len(bs_cache) < 5:
                already = {x["id"] for x in bs_cache}
                for cand in sh.bestseller_variants(top=15):
                    if cand["id"] in already:
                        continue
                    if (cand.get("inventoryQuantity") or 0) <= 0:
                        continue
                    bs_cache.append(cand)
        else:
            bs_cache = sh.bestseller_variants(top=20)
        return bs_cache

    final: list[dict] = []
    for v in line_items:
        if (v.get("inventoryQuantity") or 0) <= 0:
            already = {x["id"] for x in final} | {x["id"] for x in line_items}
            sub = next(
                (c for c in get_substitutes()
                 if c["id"] not in already
                 and (c.get("inventoryQuantity") or 0) > 0),
                None,
            )
            if sub:
                notes.append(f"OOS {v.get('sku')} → {sub.get('sku')}")
                final.append(sub)
            else:
                notes.append(f"OOS {v.get('sku')} (no substitute)")
        else:
            final.append(v)

    # Top up to exactly 3 items.
    if len(final) < 3:
        already = {x["id"] for x in final}
        # When the store has fixed default SKUs, accept oversold items.
        allow_oversold = bool(sh.rules.get("default_skus"))
        for cand in get_substitutes():
            if cand["id"] in already:
                continue
            if not allow_oversold and (cand.get("inventoryQuantity") or 0) <= 0:
                continue
            final.append(cand)
            notes.append(f"top-up {cand.get('sku')}")
            if len(final) >= 3:
                break

    if len(final) > 3:
        final = final[:3]

    line_items = final

    if len(line_items) != 3:
        return {"row": row_num, "error": "could not assemble 3 line items",
                "notes": notes}

    # Per-store extra line (Lab Noir adds a random in-stock 100ml).
    extra = sh.rules.get("extra_line")
    if extra:
        if extra["sku_pattern"] == "random-100ml":
            existing_skus = [v.get("sku") or "" for v in line_items]
            bonus = sh.random_variant_by_sku_suffix("-100ml", exclude_skus=existing_skus)
            if bonus:
                line_items.append(bonus)
                notes.append(f"100ml bonus: {bonus.get('sku')}")
            else:
                notes.append("100ml bonus: NONE in stock")

    plan = {
        "row": row_num, "site": site, "name": f"{fn} {ln}", "phone": phone_e164,
        "email": email, "ship": ship,
        "line_items": [{"sku": v.get("sku"),
                        "title": v.get("product_title") or v.get("title"),
                        "variant_title": v.get("title"),
                        "inv": v.get("inventoryQuantity")}
                       for v in line_items],
        "notes": notes, "link": link,
    }
    if dry_run:
        return {"plan": plan}

    # Place
    cid = sh.upsert_customer(phone_e164, email, fn, ln)
    order = sh.place_ugc_order(
        customer_id=cid, ship_addr=ship,
        line_variant_ids=[v["id"] for v in line_items],
        influencer_url=link,
    )
    if order["totalPrice"] not in ("0.00", "0", 0, 0.0):
        raise RuntimeError(f"unexpected total {order['totalPrice']!r}")
    if order["displayFinancialStatus"] != "PAID":
        raise RuntimeError(f"unexpected status {order['displayFinancialStatus']!r}")

    today = datetime.now()
    date_label = f"{today.day} {RO_MONTHS[today.month - 1]} {today.year}"
    skus = ", ".join(v.get("sku") or "" for v in line_items)
    msg = f"{order['name']} · {skus} · {date_label}"
    if any("[OOS]" in n or "top-up" in n or "→" in n for n in notes):
        msg += "  [" + "; ".join(notes) + "]"
    patch_status(row_num, "Trimis", msg, "#C6EFCE")

    return {
        "row": row_num, "order": order["name"], "skus": skus, "notes": notes,
        "admin": f"https://admin.shopify.com/store/{sh.admin_handle}/orders/"
                 f"{order['id'].split('/')[-1]}",
    }


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: place_ugc_order.py <row|all> [--dry-run]")
        sys.exit(2)
    arg = sys.argv[1]
    dry = "--dry-run" in sys.argv
    if arg == "all":
        rows = read_comenzi()
        # Status is now in column K (index 10). Skip rows where K is set.
        targets = [i + 1 for i, r in enumerate(rows[1:], start=1)
                   if len(r) <= 10 or not r[10]]
    else:
        targets = [int(arg)]

    for row in targets:
        try:
            result = process_row(row, dry_run=dry)
        except Exception as e:
            result = {"row": row, "exception": str(e)}
            if not dry:
                try:
                    patch_status(row, "Eroare", f"shopify: {e}"[:200], "#FFC7CE")
                except Exception as e2:
                    result["patch_failed"] = str(e2)
        else:
            # Errors returned (not raised) — write Eroare to the sheet.
            if not dry and isinstance(result, dict) and "error" in result:
                err_msg = result["error"]
                if "raw" in result:
                    err_msg += f" — input: {result['raw']!r} — cere clarificare de la Cristina"
                try:
                    patch_status(row, "Eroare", err_msg[:255], "#FFC7CE")
                    result["patched"] = True
                except Exception as e2:
                    result["patch_failed"] = str(e2)
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    main()
