---
name: shipping-rates
description: "Read, COMPARE and SET shipping/delivery rates on ARONA Shopify stores (Shopify delivery profiles) — see each store's RO + international rates side by side, and apply the canonical COD Romania pattern (flat fee below a threshold + FREE over it). Use for 'ce transport are magazinul X', 'compara ratele de transport', 'pune 20 lei + gratuit peste 150', 'set shipping rate', 'transport gratuit necondiționat' (bug fix), 'aliniaza transportul cu GT/Esteban', 'delivery profile', 'shipping zones'. Read-only by default; writes are dry-run unless --apply. Companion to gigi:shopify-stores."
argument-hint: "read --all | read --brand LABNOIR | set-ro --brand X --flat 20 --free-over 150 [--apply]"
---

# shipping-rates — transport pe magazinele ARONA (delivery profiles)

Citește / compară / setează ratele de transport prin **Shopify delivery profiles** (unde trăiesc ratele moderne — NU în `shipping_zones.json` legacy, care returnează zonele fără rate).

```bash
uv run scripts/shipping_rates.py read --all                 # toate brandurile ARONA-app, comparativ
uv run scripts/shipping_rates.py read --brand LABNOIR       # un magazin
# seteaza pattern-ul canonic COD RO (rata plata sub prag + GRATUIT peste):
uv run scripts/shipping_rates.py set-ro --brand LABNOIR --flat 20 --free-over 150            # DRY-RUN
uv run scripts/shipping_rates.py set-ro --brand LABNOIR --flat 20 --free-over 150 --apply    # scrie
uv run scripts/shipping_rates.py set-ro --brand X --flat 19 --free-over 150 --name "Livrare prin DPD" --apply
```

## Pattern-ul canonic COD RO (ce face `set-ro`)
Pe zona **Domestic**: **șterge** metodele existente și creează DOUĂ:
1. `<name>` = `--flat` lei, condiție `TOTAL_PRICE 0 .. (free_over − 0.01)` → transport plătit sub prag.
2. `<name>` = `0` lei, condiție `TOTAL_PRICE ≥ free_over` → **gratuit** peste prag.
Prag curat: la `free_over − 0.01` = plată, la `free_over` = gratuit → **fără gap** (ca GT: `≤149`/`≥150` lasă gaura 149.01–149.99 fără rată) și **fără dublă-afișare** (ca Esteban: `≤150`/`≥150` arată ambele la exact 150). International rămâne **neatins**.

## Referință ARONA (iul-2026) — pattern-ul standard = **20 lei + gratuit ≥150**
- **GT** 20 (0–149) + free ≥150 · intl 92 · nume „Livrare prin DPD"
- **Esteban** 20 (0–150) + free ≥150 · intl 90 · „Livrare prin DPD"
- **Nubra** 20 (0–150) + free ≥150 · intl 82 · „Curier rapid"
- **Lab Noir** 20 (0–149.99) + free ≥150 · intl 87 · „Livrare prin DPD" *(reparat 03-iul — avea transport GRATUIT NECONDIȚIONAT = livra gratis la toți)*
> ⚠️ **Capcană des întâlnită:** un magazin nou pornește cu o singură rată „Standard = 0" pe Domestic → **transport gratis la toată lumea** (pierdere). Verifică cu `read --all`; repară cu `set-ro`.

## Auth / note
- Merge pe magazinele de pe **app-ul OAuth ARONA** (`SHOPIFY_ARONA_<BRAND>_DOMAIN` + `CLIENT_ID/SECRET` din KB): ESTEBAN, GT, NUBRA, LABNOIR (adaugă brandul în `ARONA_BRANDS` dacă apar altele). App-ul are scope `read/write_shipping`.
- Dry-run by default; scrie doar cu `--apply`. `deliveryProfileUpdate`: `methodDefinitionsToDelete` e la **nivel TOP** în `DeliveryProfileInput` (NU în zonă — greșeală tipică), iar create-urile în `zonesToUpdate`.
- Sume în moneda magazinului (RON pt cele RO). Companion: `gigi:shopify-stores`.
