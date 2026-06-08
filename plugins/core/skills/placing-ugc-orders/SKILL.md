---
name: placing-ugc-orders
description: Place free-gift UGC/influencer orders in Shopify from Cristina's OneDrive 'Comenzi' sheet: resolve the store (GT / Esteban / Nubra / Lab Noir), match products, build a 100%-discount draft order, complete it after Iulian confirms, and write status back to the sheet. Use when processing influencer/UGC orders.
---

> Author: **Arona core**. Ported from assistant v2.
>
> **Team setup:** run the helper with `uv run "${CLAUDE_PLUGIN_ROOT}/scripts/place_ugc_order.py" <args>`. Secrets (`SHOPIFY_ARONA_*`) load automatically from the DB secret store (via `kb_env`). OneDrive auth = `microsoft_auth.get_token()` (interactive MS login, cached at `~/.config/microsoft/`). Reference docs are in the repo: `shared/integrations/microsoft-graph.md` and `shared/integrations/shopify-arona-assistant.md`. The inline `scripts/…` / `integrations/…` paths below are from the original layout — the real scripts live in this plugin's `scripts/`.

# Skill: Placing UGC orders (Cristina → Shopify)

Cristina (UGC manager) collects orders from influencers. They land in the
**`Comenzi`** sheet of the OneDrive workbook
[`Influenceri Esteban- Belasil.xlsx`](https://1drv.ms/x/c/bafd4bfc079b4528/IQAngzWEG5aQR5MAtw-Ok2TpAdRtXZ4s-9D4C4EoDvdYbAI).
The agent processes each row, places a free-gift order in the right Shopify
store, and writes status back to the sheet.

## Ground rules

- **Process one row at a time during validation phase.** In production,
  process all rows where `Status` (col K) is blank.
- **Always confirm with Iulian before each `draftOrderComplete`** (the call
  that turns the draft into a real order). Read-only / product-search /
  draft-create can be auto-run; the final completion step is gated.
- Never delete or reorder rows. Only update columns K/L and optionally cell
  fills.
- When you encounter a new exception and figure out the resolution, append
  to the [`## Lessons`](#lessons) section at the bottom of this file.

## Source workbook

| Field | Value |
|---|---|
| Share URL | `https://1drv.ms/x/c/bafd4bfc079b4528/IQAngzWEG5aQR5MAtw-Ok2TpAdRtXZ4s-9D4C4EoDvdYbAI?e=8OIznj` |
| driveId | `BAFD4BFC079B4528` |
| itemId | `BAFD4BFC079B4528!s84358327961b47909300b70f8e9364e9` |
| Worksheet | `Comenzi` |
| Auth | [`scripts/microsoft_auth.py`](../scripts/microsoft_auth.py) (`get_token()`) |

See [`integrations/microsoft-graph.md`](../integrations/microsoft-graph.md)
for endpoint patterns. Open a workbook session at start
(`POST {base}/createSession {"persistChanges": true}`), reuse the
`workbook-session-id` header for all reads/writes, close at end.

## Sheet schema (`Comenzi`)

Migrated 5 Iunie 2026: address is now split across 4 columns. Cristina
fills `Adresa` (street + bloc/scara/etaj/ap only) and the structured
`Oraș` / `Județ` / `Cod poștal` fields separately.

| Col | Header | Type | Notes |
|---|---|---|---|
| A | Site | text | one of: `george-talent.ro`, `esteban.ro`, `nubra.ro`, `labnoir.ro` |
| B | Luna | text | e.g. `Iunie 2026`. Label only. |
| C | Nume | text | influencer's full name (often trailing space) |
| D | Link Profil | url | TikTok / IG / etc. |
| E | Arome | text | requested products (free-form — see "Product matching") |
| F | Adresa | text | street + bloc/scara/etaj/ap only. **No city / county / zip here.** |
| G | Oraș | text | locality (e.g. `București`, `Ploiești`, `Pantelimon`) |
| H | Județ | text | județ name (e.g. `Prahova`, `Ilfov`). Use `Bucharest` for București (script normalizes `bucurești` → `Bucharest` automatically). |
| I | Cod poștal | text | 6-digit RO postal code. Optional in source data; script falls back to county-center ZIP. **Format the column as Text** so leading zeros (`077001`) survive. |
| J | Tel | int | 9 digits; the leading `0` is stripped by Excel — **prepend `0` always** |
| K | Status | text | agent writes `Trimis` or `Eroare` |
| L | Mesaj status | text | agent writes details (see "Status messages") |
| M | Status colet | text | (downstream / fulfillment) |
| N | Content | text | (downstream / fulfillment) |

Row 1 = header. Data starts row 2. Last row = `usedRange.rowCount`.

**Schema migration helper**: `scripts/migrate_comenzi_address_columns.py`
is a one-off that splits the legacy single `Adresa` column into the new
F/G/H/I structure. It is idempotent (aborts if G1 already says `Oraș`).
Keep it around as documentation of the migration; do not re-run.

## Site → Shopify store mapping

| Site value | myshopify domain | Public domain | Admin URL |
|---|---|---|---|
| `george-talent.ro` | `ix5bxc-hr.myshopify.com` | georgetalent.ro | <https://admin.shopify.com/store/ix5bxc-hr> |
| `esteban.ro` | `6f9e22-9d.myshopify.com` | esteban.ro | <https://admin.shopify.com/store/6f9e22-9d> |
| `nubra.ro` | `bmuwvv-jy.myshopify.com` | nubra.ro | <https://admin.shopify.com/store/bmuwvv-jy> |
| `labnoir.ro` | `31k0py-bi.myshopify.com` | labnoir.ro | <https://admin.shopify.com/store/31k0py-bi> |

All RON, `Europe/Bucharest`. Verified store names: `GT Parfumuri by George
Talent`, `Maison d'Esteban`, `Nubra`, `Lab Noir`.

## Auth — Shopify (ARONA Assistant custom app)

The **ARONA Assistant** custom app is installed on **all 4 stores**. Single
client_id / client_secret across all four; only the domain differs. Mint a
fresh access token per (store, run) via `client_credentials`; cache in
memory for the run.

Env vars in `secrets/credentials.env`:

```
SHOPIFY_ARONA_CLIENT_ID
SHOPIFY_ARONA_CLIENT_SECRET
SHOPIFY_ARONA_API_VERSION=2026-04
SHOPIFY_ARONA_LABNOIR_DOMAIN=31k0py-bi.myshopify.com
SHOPIFY_ARONA_ESTEBAN_DOMAIN=6f9e22-9d.myshopify.com
SHOPIFY_ARONA_GT_DOMAIN=ix5bxc-hr.myshopify.com
SHOPIFY_ARONA_NUBRA_DOMAIN=bmuwvv-jy.myshopify.com
```

Verified 2026-06-03: token mint + `{ shop }` query OK on all 4. See
[`integrations/shopify-arona-assistant.md`](../integrations/shopify-arona-assistant.md).

## Two-phase workflow (recommended for batch runs)

To minimize token usage when processing many rows at once:

1. **Phase 1 — dry run.** `place_ugc_order.py all --dry-run` resolves
   every blank-K row: parses fields, matches products against Shopify,
   computes substitutes / top-ups / 100ml bonus, but **does not place
   orders or write to the sheet**. Pipe through a one-line summarizer
   (group blocks by `{`/`}` depth, emit one line per row) so the agent
   sees only the verdict per row, not the full JSON plan.
2. **Review** the summary with the user. Confirm any auto-substitutions
   (`OOS X → Y`), unusual SKU prefixes (e.g. `zn-93` vs `gt-93` for GT),
   and address fallback decisions.
3. **Phase 2 — placement.** `place_ugc_order.py all` (no dry-run) loops
   through the same set, this time minting tokens, calling
   `draftOrderCreate` + `draftOrderComplete`, and patching the sheet.
   Stops on hard exceptions (still writes `Eroare` rows individually).

The per-row JSON output of phase 2 contains `order`, `skus`, `notes`,
and an `admin` URL — keep it for audit.

## Per-row workflow

1. **Skip** if col K is `Trimis` or `Eroare` — only re-process if user asks.
2. **Resolve store** from col A → use cached Shopify token for that domain.
3. **Parse phone** (col J):
   - If int → `phone = "0" + str(int(J)).zfill(9)` (strip 0 lost in Excel).
   - If string starting with `+40` or `0` → keep digits, normalize to
     `0XXXXXXXXX`.
   - If <9 or >10 digits → still send order, error message added to col L,
     fulfillment will fix.
   `ugc+0756329387@arona.ro`). Used as Shopify customer key.
   - **Why not a fake subdomain like `ugc.arona.ro`?** Shopify validates
     the email domain has MX records on `draftOrderCreate` (but NOT on
     `customerCreate` — asymmetric). A fake subdomain creates the
     customer fine, then blows up the draft with `Email contains an
     invalid domain name`. Use the real `arona.ro` with plus-addressing.
5. **Parse address** (col F) → `(address1, address2|None, city, province,
   zip, country='RO')`. **Best-effort**; AI agent infers / corrects missing
   parts (Q6=B). See "Address parsing".
6. **Match products** (col E) → list of `{ variantId, sku, title, price }`.
   See "Product matching". If a piece is unresolved → propose a substitute,
   ask user, then continue. If user rejects all options → mark `Eroare`.
7. **Find or create Customer**:
   - Search by `phone` (E.164 `+40XXXXXXXXX`).
   - If none, search by `email` (placeholder above).
   - If none, `customerCreate` with `firstName`/`lastName`/`phone`/`email`
     and `tags: ["UGC"]`.
   - Tag the customer `UGC`. (Single tag, no per-brand customer tag.)
8. **Build a draft order** (`draftOrderCreate`):
   - `customerId` = matched/created customer (Q3=A).
   - `email` = placeholder.
   - `shippingAddress` + `billingAddress` = parsed address with `phone`.
   - `lineItems`: each at full price, qty 1, **default 50ml variant** if
     product has size variants (Q3=50ml). If product has no variants, use
     the default variant.
   - `appliedDiscount`: order-level 100% (Q1=B):
     ```
     { value: 100, valueType: PERCENTAGE, title: "Comanda Influencer" }
     ```
   - `shippingLine`: manual, `price: "0.00"`, title `Comanda Influencer —
     livrare gratuita` (Q2=B2). This zeroes shipping.
   - `tags`: `["Comanda Influencer"]` (single tag, Q5).
   - `note`: `Comanda Influencer · <col D — profile URL>`
   - `customAttributes`: `[{ key: "influencer_url", value: <col D> }]`
     (Q5).
   - `useCustomerDefaultAddress: false`.
9. **Show plan to Iulian**: store, customer (matched/new), shipping
   address, line items + per-line price, expected total = 0.00. Ask before
   completing.
10. On user OK → `draftOrderComplete(id, paymentPending: false)`. Total is
    0.00, Shopify auto-marks the order **paid** (verify
    `displayFinancialStatus == "PAID"`).
11. **Write back to row** `i`:
    - `K` = `Trimis`
    - `L` = plain text `<OrderName> · <SKU1>, <SKU2>, ... · <DD RO-month YYYY>`
      e.g. `GT1234 · GT012, GT045, GT008 · 3 Iunie 2026` (Q9=A).
    - Optional: paint A:N light green.
12. On unrecoverable error → `K` = `Eroare`, `L` = short reason. Paint red.

Never proceed silently past an error — always log row index + reason.

## Address — structured columns (post-migration)

Address is now four separate columns (F-I). The `place_ugc_order.py`
script reads them directly:

```python
ship = {
    "address1": <street part of row["Adresa"]>,
    "address2": <bloc/scara/etaj/ap part of row["Adresa"]>,  # split on first comma if it has apt markers
    "city":     row["Oraș"],
    "province": row["Județ"]   # normalized: 'București' → 'Bucharest'
    "zip":      row["Cod poștal"] or county-fallback ZIP,
    "countryCode": "RO",
}
```

If F, G, or H is empty, the row is marked `Eroare` with
`address-incomplete: lipsă ...` and skipped — we never invent missing
fields. The legacy free-form parser (`scripts/ro_address.py`) is no
longer consulted at order-placement time; it is kept only for the
migration script.

Province normalization (in `place_ugc_order.PROVINCE_NORMALIZE`):
- `bucurești` / `bucuresti` → `Bucharest`
- everything else passes through with the case Cristina wrote.

ZIP fallback: when `Cod poștal` is blank, the script uses the
county-center ZIP from `scripts/ro_address.ZIP_FALLBACK` (e.g.
Prahova → `100001`, Ilfov → `077001`, Bucharest sector defaults →
`0X0001` only when the F-column has a `sector N` token). DPD will
correct to street level on label print.

## Product matching

Column E ranges from explicit names to numeric SKUs to vague hints.

**HARD RULE — every order must have exactly 3 line items.** No more, no
less. If column E lists fewer than 3 → top up from brand bestsellers
(showing the picks first). If it lists more than 3 → ask Iulian which 3.
If column E is silent / vague (e.g. `Top 3 cele mai vandute`) → 3
bestsellers. Mixed (1 named + 1 vague) → resolve named first, fill the
rest from bestsellers / category to reach 3.

1. **Number tokens** (`nr.76`, `Nr.71`, `nr 50`, **or a bare number
   like `76` / `12, 92, 3`**) → match by SKU. Cristina sometimes writes
   bare numbers without the `nr.` prefix; the matcher accepts both via
   `NUMBER_TOKEN` and `BARE_NUMBER` in `place_ugc_order.py`. Don't guess
   prefixes per-store — the `STORE_RULES[..].sku_prefixes` list owns it.
   (Q2: prefix not needed.)

2. **Named product** (`Leather & Tobacco`, `Devotion D&G`,
   `Burberry Goddess`, `J'adore Interdit`) →
   `products(first:5, query:"title:*<term>*")`. Pick the highest-relevance
   match. If product has size variants, use **50ml**; if no 50ml variant
   exists, use the default variant and note in col L (Q3).

3. **Vague phrase** (`Top 3 cele mai vandute`, `ceva de vară`):
   - Compute bestsellers per brand: **last 30 days, by quantity sold**
     (Q8=A). Cache the list per (brand, run).
   - Query: `orders(query: "created_at:>=<30d>", first: 250)` → aggregate
     `lineItems.variant.product.id` counts → top N variants.
   - Or use a curated Shopify collection if one exists (e.g. `bestsellers`
     handle).
   - Propose top 3 to Iulian; he confirms / overrides.
   - For "ceva de vară" / similar fragrance hints: filter by tag
     `fresh`/`citrus`/`aquatic` if exists, else fall back to bestsellers.

4. **OOS handling** (Q4a=iii): if the matched variant has `inventoryQuantity
   <= 0`:
   - Search for a similar product (same fragrance family / similar title /
     bestseller fallback).
   - Propose substitute to Iulian; on approval, use it and add a note in
     col L `(înlocuit <orig> → <subst>)`.

5. **Unknown** → propose 3 nearest matches; on rejection, mark `Eroare` with
   `produs neidentificat: <term>`.

A single column-E cell can mix styles
(`Leather& Tobacco și ceva de vară` = 1 named + 1 to-recommend). Resolve
each comma-separated piece independently. If any piece can't be resolved
*and* the user rejects substitutes, the whole row fails.

Always record both `variantId` (for the mutation) and `sku` (for col L).

## Draft order — exact GraphQL shape

```graphql
mutation CreateUGCDraft($input: DraftOrderInput!) {
  draftOrderCreate(input: $input) {
    draftOrder {
      id
      name
      totalPrice
      lineItems(first: 20) { edges { node { sku title } } }
    }
    userErrors { field message }
  }
}
```

```python
input = {
  "email": f"ugc+{phone}@arona.ro",
  "note": f"Comanda Influencer · {profile_url}",
  "tags": ["Comanda Influencer", "influencer"],
  "customAttributes": [{"key": "influencer_url", "value": profile_url}],
  "purchasingEntity": {"customerId": customer_gid},
  "shippingAddress": addr_dict_with_phone,
  "billingAddress":  addr_dict_with_phone,
  "useCustomerDefaultAddress": False,
  "lineItems": [
    {"variantId": "gid://shopify/ProductVariant/<id>", "quantity": 1}
    for v in chosen_variants
  ],
  "appliedDiscount": {
    "value": 100, "valueType": "PERCENTAGE",
    "title": "Comanda Influencer",
    "description": "100% UGC"
  },
  "shippingLine": {
    "price": "0.00",
    "title": "Comanda Influencer — livrare gratuita"
  }
}
```

Then complete:

```graphql
mutation { draftOrderComplete(id: "<gid>", paymentPending: false) {
  draftOrder { order { id name displayFinancialStatus totalPrice } }
  userErrors { field message }
}}
```

Verify `order.totalPrice == "0.00"` and `displayFinancialStatus == "PAID"`.

## Status messages (col L)

**HARD RULE — never silently skip a row.** Every processed row gets a
status in H (`Trimis` or `Eroare`) and a clear message in I. If we
can't place the order, we still mark the row so Cristina sees it
needs attention. Don't leave H blank for "we'll come back to it".

- **Success**: `<OrderName> · <SKU1>, <SKU2>, ... · <DD RO-month YYYY>`
  - Example: `GT1234 · GT012, GT045, GT008 · 3 Iunie 2026`
  - If any auto-substitution / top-up happened, append
    `[OOS <orig> → <sub>; top-up <sku>]` to the message.
  - RO months: Ianuarie, Februarie, Martie, Aprilie, Mai, Iunie, Iulie,
    August, Septembrie, Octombrie, Noiembrie, Decembrie.
- **Error vocabulary** (always start with one of these prefixes so
  Cristina / fulfillment can grep):
  - `address-incomplete: lipsă <oraș|județ|stradă>` — address parser
    couldn't find the listed field. Append the raw input + actionable
    suggestion: `— input: '<raw>' — cere clarificare de la Cristina`.
  - `address-parse: <text>` — parsed but flagged something suspicious
    (still attempted).
  - `produs neidentificat: <term>` — no match, no substitute accepted.
  - `phone-invalid: <value>` — not 9 or 10 digits.
  - `shopify: <userErrors[0].message>` — Shopify rejected the mutation.
  - `mai multe variante: <term>` — needs Iulian's pick (only when
    auto-pick is unsafe).
  - `stoc 0: <SKU> (substitute respins)` — OOS and substitute rejected.

When a row errors, **never invent missing data** to push the order
through. Do `patch_status(row, "Eroare", msg, "#FFC7CE")` and move on.

## Coloring rows

After writing K/L:

```python
fill = {'Trimis': '#C6EFCE', 'Eroare': '#FFC7CE'}[status]
patch(f"{base}/worksheets('Comenzi')/range(address='A{row}:N{row}')/format/fill",
      json={'color': fill})
```

## Performance

- **Workbook session** for the whole run.
- **Shopify token cache**: mint once per store, reuse across rows.
- **Product / customer lookup cache** keyed by (store, term/phone) within
  the run.
- **Bestsellers cache**: compute once per store on first vague-term row.

## Don't

- Don't run `draftOrderComplete` without explicit Iulian approval for that
  row.
- Don't auto-pick from bestsellers without showing the picks first.
- Don't send the order without `phone` on `shippingAddress`.
- Don't reprocess a row already marked `Trimis` / `Eroare`.
- Don't set `paymentPending: true` — total is 0, must be marked paid.
- Always tag the order with both `Comanda Influencer` and `influencer`.
- Don't add per-brand or per-month tags. Cristina's `Luna` column is just
  a label, not a tag.

## Workflow summary

```text
1. Open OneDrive workbook session.
2. Read Comenzi!usedRange.
3. Pick first row where H is blank.
   a. Resolve store, mint Shopify token (cached).
   b. Parse phone, address, name.
   c. Generate placeholder email.
   d. Match each product term → variant IDs + SKUs.
       OOS → propose substitute (ask).
       Vague → use bestseller cache, propose top 3 (ask).
   e. Find/create Customer (search by phone, then email; tag UGC).
   f. Build draft order; show plan, ask.
4. On approval → draftOrderComplete; verify totalPrice=0 and PAID.
5. Write H/I + light green fill. On error: Eroare + reason + light red.
6. Stop (validation phase) OR move to next blank row (production).
7. Close workbook session. Print summary table.
```

## Lessons

> Format: `<DD Mon YYYY> · <brand> · <pattern observed> · <resolution>`.
> Append as you learn. Keep entries short.

- 3 Iunie 2026 · GT · `draftOrderCreate` rejected `<phone>@ugc.arona.ro`
  with `Email contains an invalid domain name`, even though
  `customerCreate` had accepted the same email. Shopify only checks
  MX on draft creation. Switched placeholder to `ugc+<phone>@arona.ro`
  (real domain, plus-addressing). For pre-existing customers carrying
  the broken email, run `customerUpdate` to migrate before draft.
- 3 Iunie 2026 · GT · Order tag set is `Comanda Influencer` + `influencer`
  (added second tag at Iulian's request — both always go together).
- 3 Iunie 2026 · GT · Shopify rejects RO orders without a `zip`. Cristina
  often omits it. Built `scripts/ro_address.py` with county-center ZIP
  fallback (`Brașov` → `500001`, sector 3 → `030001`, etc.). DPD will
  correct to street-level on label print.
- 3 Iunie 2026 · GT · HARD RULE: every UGC order is 3 line items. Top
  up from brand bestsellers when column E names fewer than 3.
- 3 Iunie 2026 · GT · The GT Shopify store also sells the **Zeylin** (`zn-`)
  line. Cristina's `nr.X` references the Zeylin numbers FIRST. SKU search
  order: `zn-N`, then `gt-N`, then bare `N`. Do NOT assume `gt-` for GT.
- 3 Iunie 2026 · GT · OOS auto-substitution policy: when a column-E pick
  is OOS, replace with the next in-stock GT bestseller (last-30d, by qty)
  not already in the cart. Always log `OOS <orig-sku> → <sub-sku>` to
  col L so fulfillment sees the swap. Do NOT silently drop the line.
- 3 Iunie 2026 · GT · Address with no city/judet (Cristina sometimes only
  writes a street + apartment) cannot be auto-placed \u2014 STOP and ask.
  `parse_address` returns `city=''` / `province=''` in that case; the
  caller MUST refuse rather than guess.
- 3 Iunie 2026 · `scripts/place_ugc_order.py` is the canonical placement
  script. Use it as the entry point; it owns all the rules above.
  - `place_ugc_order.py <row>` — process one row.
  - `place_ugc_order.py all` — process every blank-H row.
  - Add `--dry-run` to show the plan without placing or writing back.
- 3 Iunie 2026 · GT · Row with no city/județ → `H="Eroare"`,
  `I="address-incomplete: lipsă oraș + județ — input: '<raw>' — cere
  clarificare de la Cristina"`, paint red. Never silently skip.
- 3 Iunie 2026 · LabNoir · SKU pattern is `<N>-50ml` for the 50ml variant
  and `<N>-100ml` for the 100ml variant of the same product. Cristina's
  `nr.X` token therefore maps to `<X>-50ml`. Wired into
  `STORE_RULES["SHOPIFY_ARONA_LABNOIR_DOMAIN"].sku_prefixes = ["{n}-50ml"]`.
- 3 Iunie 2026 · LabNoir · Default fillers when col E is empty / vague /
  unmatched: `49-50ml`, `47-50ml`, `71-50ml` in that order. Wired into
  `STORE_RULES.SHOPIFY_ARONA_LABNOIR_DOMAIN.default_skus`. These are
  accepted **even when oversold** (negative `inventoryQuantity`) — Shopify
  still allows the draft, and these are explicitly Cristina-approved.
- 3 Iunie 2026 · LabNoir · Every LabNoir UGC order ALSO gets a bonus
  4th line item: a random in-stock 100ml variant (any product, picked
  uniformly at random from `*-100ml` SKUs with `inventoryQuantity > 0`,
  excluding SKUs already in the cart). So Lab Noir orders are
  **3×50ml + 1×100ml**. Wired into
  `STORE_RULES.SHOPIFY_ARONA_LABNOIR_DOMAIN.extra_line = {"sku_pattern": "random-100ml"}`.
- 3 Iunie 2026 · Per-store behavior lives in the `STORE_RULES` dict at the
  top of `place_ugc_order.py`. Each store entry has `sku_prefixes` (the
  list of `{n}` patterns to try when matching `nr.X`), `default_skus`
  (fallback when col E is empty or all unmatched), and `extra_line`
  (a bonus 4th line item, e.g. LabNoir's random 100ml). When adding a new
  store, add an entry here rather than hardcoding logic in `process_row`.
- 3 Iunie 2026 · `Comenzi` sheet has been extended to 11 columns
  (`Status colet`, `Content` after I). The script reads only the first 9.
- 5 Iunie 2026 · **Address columns split.** Replaced single free-form
  `Adresa` with `Adresa` (street + bloc/scara/etaj/ap), `Oraș`, `Județ`,
  `Cod poștal`. Status moved from H/I to K/L. New column count: 14. The
  legacy `parse_address(raw)` is no longer called at placement time —
  retained only for the one-off
  `scripts/migrate_comenzi_address_columns.py`. Diff in
  `place_ugc_order.process_row`: read F/G/H/I/J directly; status
  read/write moved to K/L; row coloring now spans `A:N`.
- 5 Iunie 2026 · **Format col I (Cod poștal) as Text in Excel.**
  Otherwise leading-zero ZIPs (`077001`, `030001`) are stored as
  numbers and Excel strips the leading zero. The migration script
  applies `numberFormat = "@"` to the column; Cristina shouldn't ever
  see it switch back, but if she does, re-apply.
- 5 Iunie 2026 · **Bare-number SKU tokens.** Cristina sometimes writes
  col E as `12, 92, 3` (no `nr.` prefix). `match_term_to_variant` now
  accepts both `nr.X` (`NUMBER_TOKEN`) and bare `^\d{1,4}$`
  (`BARE_NUMBER`) per term, then tries the store's `sku_prefixes` list.
- 5 Iunie 2026 · **Two-phase batch flow** (token-efficient): run
  `place_ugc_order.py all --dry-run` first, summarize one row per line,
  let Iulian review, then run `place_ugc_order.py all` to actually
  place. The dry-run does all the Shopify reads (variant lookups,
  bestsellers, 100ml random pick) but does NOT mint draftOrderCreate;
  phase 2 redoes lookups but caches Shopify token + bestsellers per
  store within a single run.
- 5 Iunie 2026 · GT · `gt-95` and `gt-100` are both OOS as of today's
  batch; auto-substituted to `gt-30` (next in-stock GT bestseller) per
  the standing OOS policy. Both shown in col L as `OOS gt-X → gt-30`.
