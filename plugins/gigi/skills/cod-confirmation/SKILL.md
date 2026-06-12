---
name: cod-confirmation
description: Refusal PREVENTION for Customer Service — the pre-shipping confirmation queue of risky COD orders (still unshipped / status Netrimisa), ranked by 4 data-driven risk signals: (1) customer who REFUSED a delivery before, (2) order contains a HIGH-REFUSAL PRODUCT (computed live from history — products that refuse >30% and >1.6× the store average, e.g. the HA-* deals items at 40-56%), (3) IMPULSE band (single item, 50-100 RON → refuses ~22%), (4) HIGH-VALUE order. Confirm by phone/SMS BEFORE paying for transport → cut the refusal at the source (the ~272k RON/month refusal leak). Includes a ready-to-send confirmation message per market. Use for "which COD orders to confirm before shipping", "risky orders", "confirmare comenzi inainte de livrare", "prevent refusals", "high-risk COD", "produse care se refuza", "clienti care au mai refuzat". Read-only.
---

# CS — Confirmare pre-livrare COD (prevenție refuz)

Recuperarea (cs-refused-recovery) prinde banii DUPĂ ce s-a refuzat. Asta îi oprește ÎNAINTE: confirmi comenzile riscante înainte să cheltui transportul.

## Cum rulezi
```bash
uv run cod_confirmation.py --days 5                          # neexpediate (Netrimisa) din 5 zile, risc-prioritizate
uv run cod_confirmation.py --brand Grandia --min-value 200   # un brand, prag valoare
uv run cod_confirmation.py --days 5 --draft                  # + mesajul de confirmare per comandă/limbă
uv run cod_confirmation.py --days 3 --json                   # pt automatizare (dialer/SMS)
```

## Cum funcționează
- Neexpediate = `profit_orders.status_category='Netrimisa'` (încă neplecate), recente, din `data/profitability.db` (VPS).
- **4 semnale de risc** (prioritate în această ordine):
  1. **REFUZAT ÎNAINTE** — telefonul apare pe o comandă `Refuzata` în ultimele 90 zile.
  2. **PRODUS RISC** — comanda conține un produs cu refuz mare, calculat LIVE din istoric (refuz >30% și >1.6× media magazinului; ex. HA-0431 Seif 56%, HA-0126 Covor Persan ~50%).
  3. **IMPULS 50-100** — 1 singur produs, 50-100 lei (banda care refuză ~22% vs 16% media).
  4. **VALOARE MARE** — valoare ≥ prag (`--min-value`).
- Contact + valoare din `metrics.orders`. Mesaj de confirmare RO/CZ/PL/BG. Read-only.

## Idee de extins
- Risc pe județ (rată refuz mare per `shippingProvince`); excludere clienți deja confirmați; integrare directă cu dialer/WhatsApp.
