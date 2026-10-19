---
name: gigi:arona-marketplace
description: "MCP-ul arona-marketplace — eMAG RO/BG/HU, Trendyol RO/BG/GR, Okazii, Pepita, OLX, CEL operate PRIN Order Hub, ca tool-uri tipate: a intrat comanda eMAG/Trendyol/Okazii, are AWB, e în coada de print; starea unui SKU pe fiecare marketplace (ofertă, preț, stoc, documentație); catalogul Trendyol live (Product v2) cu capcana onSale; split pachete Trendyol; etichete Trendyol de descărcat; prețuri/concurență eMAG din tracker; conținutul feed-ului Okazii; prețul țintă după regula profit net 20%. Scrieri gated: activare/legare oferte eMAG, listare eMAG, adăugare în feed Okazii, preț Trendyol (preview + confirm). Use: „a venit comanda de pe eMAG”, „e listat HA-1657 pe Trendyol”, „de ce nu e live pe eMAG”, „ce preț are concurența pe eMAG”, „pune pe Okazii”, „ce e în feed-ul Okazii”, „schimbă prețul pe Trendyol”, „preț marketplace cu profit 20%”."
category: marketplace-integrations
version: 1.0.0
---

# arona-marketplace — marketplace-urile prin Order Hub, ca MCP

Server: `mcp_server.py` (FastMCP, `uv run`). Strat SUBȚIRE peste scripturile testate; **nu schimbă codul Order Hub**.
Comenzile marketplace intră în OH ca orice comandă: sursa de adevăr e **OH**, nu AWBprint.

Register (o dată, user-scope):
`claude mcp add --scope user arona-marketplace -- uv run <abs>/plugins/gigi/skills/arona-marketplace/mcp_server.py`

## Tool-uri
| Tool | Ce face | Sursă |
|---|---|---|
| `mkt_conexiuni` | conexiunile (1 eMAG RO, 2 BG, 3 HU, 4 OLX, 5 Trendyol, 6 CEL, 7 Pepita, 8 Okazii) + listări pe stare + ultimul import/eroare | OH DB, READ ONLY |
| `mkt_comenzi` | comenzile marketplace: AWB, curier, status colet, `coada_print`, factură, SKU-uri (fără date personale) | OH DB |
| `mkt_listare` | un SKU/barcode pe toate conexiunile OH + varianta Trendyol RO live | OH DB + Trendyol v2 |
| `trendyol_produse` | catalog Trendyol live (RO/BG/GR): sumar onSale/stoc 0/blacklist + variante filtrate | `trendyol_stock_push.ty_produse` (VPS) |
| `trendyol_awb_list` | etichetele Trendyol de descărcat (nu atinge nimic) | `trendyol-awb/scripts/trendyol_awb.py list` |
| `trendyol_split` | pachete multi-bucată de spart (dry-run; `apply` = execută) | `trendyol_split.sh` (VPS) |
| `emag_tracker` | orice acțiune a tracker-ului eMAG (search/product/competitors/movers/ranking…) | `scripts/emag_api.py` (copie a `catalin:emag-tracker`, doar GET) |
| `emag_concurenta` | PNK-ul ofertei noastre (din OH) → fișa + concurenți; dacă PNK-ul nu e în tracker, produse asemănătoare după titlu | OH + tracker |
| `okazii_feed` | ce vede Okazii (8) / Pepita (7) în feed: articole, preț, cantitate (stock-sync, plafon 20) | funcțiile OH `services/marketplace/feed`, READ ONLY |
| `emag_pret_proba` | ce preț ar trimite sincronizarea OH (prețul magazinului) vs eMAG — nu scrie | `marketplace_oferte.py --pret` |
| `mkt_pret_tinta` | prețul țintă după regula ownerului: profit NET 20% | calcul local (aceeași formulă ca `pepita_listeaza.pret_tinta`) |
| `emag_oferte` ✎ | activare/dezactivare oferte eMAG; PROBĂ implicit; masă = `acord_owner=true` | `marketplace_oferte.py` |
| `mkt_leaga_oferte` ✎ | leagă ofertele existente de variantele Shopify (scrie doar în OH) | `marketplace_leaga_oferte.py` |
| `emag_listeaza` ✎ | listare produse noi pe eMAG din plan CSV; PROBĂ implicit | `marketplace_listeaza.py` |
| `okazii_adauga` ✎ | SKU-uri în feed-ul Okazii; PROBĂ implicit | `okazii_listeaza.py` |
| `trendyol_pret` ✎ | preț Trendyol: preview → `apply=true` + `confirm` exact din preview; blocat sub ținta 20% | `ty_push` + `ty_batch` (VPS) |

✎ = scriere. Fără `apply=true` nu scrie nimic. Scripturile OH rulează în containerul `orderhub-web` (probă = sesiune READ ONLY).

## Reguli de business (din memorie, aplicate în tool-uri)
- **Prețul = decizia ownerului.** Regula aprobată (15-oct): `(COGS + 15) / (1 − comision − 0,03 − 0,10 − 0,20) × 1,21` la ,99; eMAG 17%, Trendyol 18%. eMAG BG/HU: fără cei 10% reclame, iar prețul BG/HU **oglindește RO** (se schimbă din RO). Ținta > 2× prețul actual = COGS suspect → la owner.
- **Capcana onSale Trendyol:** stoc 0 ⇒ `onSale=false` — NU e inactiv. Stocul îl ține **stock-sync**; bootstrap-ul `trendyol_stock_push.sh --apply` e OPRIT (18-oct) și nu e expus. `trendyol_pret` retrimite cantitatea neschimbată (Trendyol cere preț+cantitate împreună).
- Endpoint-ul Trendyol vechi `/products` (v1) dă **426** din 13-oct → doar v2 `/products/approved` (nextPageToken).
- eMAG: activarea în masă / ștergeri / prețuri doar cu acordul ownerului; EAN-uri doar din `ean_alocari` (prefixe 5941240/5941237).
- Okazii: prețul implicit = prețul magazinului (ales de owner 16-oct); `formula` cere comisionul explicit.

## Capcane
- `date_marketplace.pret/stoc` din `mkt_listare` sunt citite la legare (`citit_la`), nu live.
- Un deploy OH repornește `orderhub-web` → „container is not running”: reîncearcă peste un minut.
- PNK-urile ofertelor noastre noi lipsesc des din catalogul tracker-ului → `emag_concurenta` cade pe căutare după titlu.

## TODO (nu sunt tool-uri încă — lipsește un script testat / o schimbare OH)
- Preț eMAG per ofertă (OH n-are script de `offer/save` pe preț; `pret_sync` e oprit) — nevoie de script OH.
- Stoc manual pe marketplace — intenționat absent (stock-sync e singurul scriitor).
- `marketplace_listeaza.py --verifica` (actualizează stări în OH) — de expus după ce e clar că e idempotent.
- OLX (`olx_listeaza.py`), CEL (API blocat, 405), Trendyol listare produse noi (`stemma_shopify_to_trendyol.py` e pe Stemma), promoții Trendyol (token panel scurt).

Related: `core:marketplace-ops` · `gigi:order-hub` · `gigi:trendyol-awb` · `catalin:emag-tracker` · `gigi:arona-mcp`.
