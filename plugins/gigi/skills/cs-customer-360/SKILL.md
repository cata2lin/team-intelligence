---
name: cs-customer-360
description: A full 360° view of a customer for Customer Service. Paste a phone / email / name and get all their orders across every Arona store, lifetime value (delivered), delivered-vs-refused counts, refusal rate, the brands they bought from, and an automatic "SERIAL REFUSER" flag (recommend card-only, not COD) so the team stops losing money on chronic refusers. Use for "who is this customer", "customer history", "is this a serial refuser", "client history", "istoric client", "câte comenzi a refuzat", "should we ship COD to this person". Read-only.
---

# CS — Customer 360 (+ flag refuznic serial)

Context complet pe client într-o comandă: tot ce a cumpărat (din toate magazinele), LTV, de câte ori a refuzat, și un flag automat pentru clienții care refuză cronic (de pus pe card, nu COD).

## Cum rulezi
```bash
uv run cs_customer_360.py --phone 0748620192
uv run cs_customer_360.py --email client@gmail.com
uv run cs_customer_360.py --name "Varga Rebeka"
```

## Ce arată
- Toate comenzile (din `metrics.orders`, pe toate brandurile) + valoare + dată + status financiar.
- **Livrabilitate per comandă** din `profit_orders` (VPS): livrate vs refuzate → rată refuz + LTV (doar din livrate).
- 🚩 **REFUZNIC SERIAL** când a refuzat ≥2 comenzi (sau ≥50% rată refuz pe ≥2 comenzi) → recomandare „doar plată card".
- Read-only.

## De extins
- Retururi (RMA Grandia) și recenzii (Judge.me) per client; scor de risc; integrare cu cs-refused-recovery (exclude refuznicii din win-back COD, oferă-le doar card).
