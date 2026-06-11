---
name: cod-confirmation
description: Refusal PREVENTION for Customer Service — the pre-shipping confirmation queue of risky COD orders (still unshipped / status Netrimisa), ranked by risk: customer who has REFUSED a delivery before, or a HIGH-VALUE order. Confirm by phone/SMS BEFORE paying for transport → cut the refusal at the source (the other half of the ~272k RON/month refusal leak — recovery catches it after, this stops it before). Includes a ready-to-send confirmation message per market. Use for "which COD orders to confirm before shipping", "risky orders", "confirmare comenzi inainte de livrare", "prevent refusals", "high-risk COD", "clienti care au mai refuzat". Read-only.
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
- **Risc:** clientul a mai refuzat (telefonul apare pe o comandă `Refuzata` în ultimele 90 zile) → marcat „REFUZAT ÎNAINTE" (prioritate maximă); sau valoare ≥ prag → „VALOARE MARE".
- Contact + valoare din `metrics.orders`. Mesaj de confirmare RO/CZ/PL/BG. Read-only.

## Idee de extins
- Risc pe județ (rată refuz mare per `shippingProvince`); excludere clienți deja confirmați; integrare directă cu dialer/WhatsApp.
