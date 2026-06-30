---
name: cod-reconcile
description: "Reconcile COD orders against the COURIER's live truth (DPD), not the unreliable AWBprint status — find Shopify orders that are FULFILLED but still PENDING (COD shipped, not collected), verify each on DPD, then CANCEL the returned ones (keeping them FULFILLED) and MARK-AS-PAID the delivered-but-uncaptured ones. Use for 'anuleaza refuzatele', 'curata comenzile COD blocate', 'cancel returned/refused orders', 'reconcile COD', 'fulfilled dar neplatit', 'comenzi expediate neincasate', 'mark paid delivered COD'. Companion to gigi:xconnector + gigi:cs-refused-recovery + gigi:deliverability-monitor."
argument-hint: "[--apply] [--shops EST,MAG] [--before YYYY-MM-DD] [--limit N]"
---

# cod-reconcile — reconciliere COD pe adevărul curierului (DPD)

Curăță comenzile COD „blocate" pe baza **statusului LIVE de la curier (DPD)**, NU pe `aggregated_status` din AWBprint — care **minte în ambele sensuri** (vezi memoria [[awbprint-status-unreliable]]): arată livrate ca `waiting_for_courier`, și ascunde refuzuri reale sub `incorrect_address`/`unsuccessful_delivery`/`customer_pickup`/chiar `delivered`.

**Semnalul SIGUR (Shopify-native):** comandă **FULFILLED + plată PENDING + veche** = COD expediat dar neîncasat → ori **returnat**, ori **livrat-dar-neîncasat** (capture ratat). Verific fiecare pe DPD și:
- **DPD returnat** (Back to Sender / Return to Sender / refused) → `orderCancel` **DIRECT** (refund=false, restock=false, notify=true) + tag `anulata`. **NU ating fulfillment-ul** → comanda rămâne **FULFILLED** (= dovada că a plecat). **NICIODATĂ `fulfillmentCancel`** pe comenzi expediate (l-ar face CANCELLED+UNFULFILLED, ireversibil — vezi lecția).
- **DPD livrat** (Delivered) → `orderMarkAsPaid` (COD încasat la ușă, corectează venitul în analytics).
- **în-tranzit / fără răspuns DPD / non-DPD** (Packeta/Sameday) → **LAS** (nu ating; nu pot verifica → conservator).

```bash
uv run scripts/cod_reconcile.py                          # DRY-RUN, toate magazinele (comenzi < azi-14 zile)
uv run scripts/cod_reconcile.py --apply                  # execută
uv run scripts/cod_reconcile.py --apply --shops EST,GT   # doar anumite magazine (prefix)
uv run scripts/cod_reconcile.py --before 2026-06-16      # alt prag de vechime
uv run scripts/cod_reconcile.py --limit 50               # max N candidați / magazin (test)
```

## Reguli de aur (de ce e construit așa)
1. **NU te baza pe AWBprint `aggregated_status`** pt „a plecat / refuzat / livrat" — verifică **LIVE pe curier**. Refuzul real (`back_to_sender`) E de încredere, dar restul statusurilor ascund refuzuri.
2. **Refuzat ≠ neplecat.** O comandă refuzată A PLECAT → la anulare se păstrează FULFILLED. `orderCancel` direct MERGE pe comenzi fulfilled+pending și le lasă fulfilled. Doar comenzile **care chiar n-au plecat** (AWB făcut, niciodată preluat) primesc unfulfilled — și alea sunt o categorie diferită.
3. `orderCancel` eșuează cu „Cannot cancel an order that has outstanding fulfillments" pe **PAID/REFUNDED** + fulfilled → pe alea **NU forța** fulfillmentCancel; sar și le loghez (decizie separată, posibil refund).
4. Dry-run by default. `--before` default = azi-14 zile (ca să NU atingă comenzi încă legitim în tranzit).

## Dependențe / rulare
- `uv run` (PEP723: `pg8000`, `pypdf`). Importă `load_shopify_tokens` + `dpd_track_sync` din `gigi:xconnector` (cale relativă). Credențiale DPD din env/KB (`DPD_RO_USERNAME/PASSWORD`), tokenuri Shopify din `SHOPIFY_ADMIN_TOKENS` + `stores.csv`.
- Forța pe VPS (autonom, volume mari): wrapper care exportă env-ul cu `sed` din `.env.xconnector` (NU `source` — JSON-ul sparge sourcing-ul). Vezi [[xconnector-bulk-invoicing]].
