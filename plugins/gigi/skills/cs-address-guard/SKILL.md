---
name: cs-address-guard
description: Refusal PREVENTION for Customer Service — the pre-shipping queue of still-unshipped COD orders (status Netrimisa) that have a BROKEN shipping address, so CS can phone-confirm the correct address BEFORE the warehouse ever pays to print an AWB. Attacks the refusal leak at its root cause — roughly 13% of Romanian orders arrive with no house/street number, and a bad address means a lost parcel = a refusal/return that costs transport twice (out + back). For each unshipped order it flags WHY the address is bad — no digit at all in shippingAddress1 (missing street number), address shorter than 10 chars, missing postal/ZIP code, or an un-callable phone (digit count outside 9–12) — with a plain-text reason, and hands the CS team a ready-to-send confirmation message per market (rich Romanian; short Czech/Polish/Bulgarian by shippingCountry). Outputs a per-store summary (how many flagged out of total, estimated transport cost avoided) and a detailed per-store view with order_name, phone, raw address, reason and the draft message. Modes: default summary across all stores, --store for one brand detailed, --reasons to keep only one problem type, --days window, --json for a dialer/SMS/Sheet. Use for "which orders have a bad address", "confirm address before shipping", "missing street number", "prevent COD refusals from bad addresses", "comenzi cu adresa gresita de confirmat inainte de AWB", "lipsa numar strada", "adresa incompleta", "confirmare adresa pre-livrare". Read-only — never writes to Postgres, Shopify or Richpanel.
---

# CS — Gardian de adresă (prevenție refuz din adresă proastă)

`cod-confirmation` confirmă comenzile riscante după client/valoare. Asta atacă o cauză precisă a refuzului: **adresa defectă**. ~13% din comenzile RO ajung fără număr de stradă → coletul se rătăcește → refuz/retur plătit dublu (transport dus + întors). Aici prinzi adresa proastă cât comanda e încă **Netrimisa** (neexpediată) și o confirmi telefonic ÎNAINTE ca depozitul să facă AWB.

## Cum rulezi
```bash
uv run cs_address_guard.py                          # sumar: câte flag-uite/magazin + cost evitat
uv run cs_address_guard.py --store Esteban          # un magazin, detaliat + mesaj de confirmare per comandă
uv run cs_address_guard.py --reasons no_number      # doar lipsă număr de stradă (sau short|no_zip|bad_phone)
uv run cs_address_guard.py --days 7 --limit 30      # fereastră 7 zile, max 30 rânduri/magazin
uv run cs_address_guard.py --json                   # pt automatizare (dialer/SMS/Google Sheet)
```

## Cum funcționează
- **Sursă comenzi:** SSH → `data/profitability.db` (`profit_orders`) pe VPS — `order_name`, `prefix`, `revenue` WHERE `status_category='Netrimisa'`, din ultimele `--days` (default 14).
- **Adresa:** join pe `metrics.orders.name = profit_orders.order_name` (format identic, ex `EST184096`) → `shippingAddress1/2`, `shippingZip`, `shippingPhone`, `shippingName`, `shippingCity`, `shippingProvince`, `shippingCountry`, `email`.
- **FLAG adresă proastă** (fiecare cu motiv text):
  - `no_number` — **nicio cifră** în `shippingAddress1` (lipsă număr de stradă);
  - `short` — `len(shippingAddress1.strip()) < 10` (adresă prea scurtă);
  - `no_zip` — `shippingZip` lipsă/gol;
  - `bad_phone` — telefon invalid (după strip non-cifre, nr de cifre nu e între 9 și 12; RO=11, CZ=12 trec).
- **Mesaj de confirmare** gata făcut per piață: RO bogat (cere stradă + NUMĂR + bloc/scară/ap + cod poștal), CZ/PL/BG scurt, ales după `shippingCountry` (fallback pe prefix).
- **Sumar:** câte flag-uite din câte netrimise analizate + estimare cost transport evitat (dus-întors per piață).

READ-ONLY total: nu scrie nimic în Postgres/Shopify/Richpanel; fără secrete în output.

## Acoperire
Se evaluează doar comenzile care au rând în `metrics.orders`. Câteva branduri nu sunt sincronizate acolo (ex. Ofertele Zilei, Magdeal, Bonhaus PL/BG, parțial Reduceri bune) — acelea apar în nota de „netrimise fără rând în metrics" și nu pot fi verificate aici până nu intră în warehouse. Restul (Esteban, GT, Nubra, Grandia, Bonhaus RO/CZ, Carpetto, Gento ș.a.) se acoperă integral.

## Idee de extins
- Validare ZIP cu nomenclatorul poștal RO (potrivire localitate↔cod); excludere comenzi deja confirmate; push direct în dialer/WhatsApp sau într-un Google Sheet pentru CS.
