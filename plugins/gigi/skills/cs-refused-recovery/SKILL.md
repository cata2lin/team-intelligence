---
name: cs-refused-recovery
description: Customer-Service revenue recovery — the queue of REFUSED / failed-delivery COD orders that can still be won back. Pulls recently refused parcels (deliverability status_category=Refuzata) joined to the customer's name/phone/city/value from metrics, ranks by order value, and drafts a ready-to-send win-back message per market (re-deliver / pay-by-card -10%). Each refused parcel already cost ad spend + transport, so recovering even 15-20% is real money. Use for "refused orders to recover", "win back refused COD", "comenzi refuzate de recontactat", "failed delivery follow-up", "recuperare colete refuzate", or building a daily CS callback list. Read-only.
---

# CS — Recuperare comenzi refuzate (win-back)

~9.000 colete refuzate/lună la COD = **bani deja cheltuiți (reclamă + transport) pe cale să se piardă**. Acest skill transformă CS-ul într-un canal de venit: scoate coada de recuperat, cu contactul clientului, și mesajul gata de trimis.

## Cum rulezi
```bash
uv run cs_refused_recovery.py --days 14                       # toate refuzatele din 14 zile, sortate pe valoare
uv run cs_refused_recovery.py --brand Esteban --days 7        # doar un brand, fereastră 7 zile
uv run cs_refused_recovery.py --days 14 --min-value 100       # doar comenzi >=100 lei (prioritate)
uv run cs_refused_recovery.py --days 7 --draft                # + mesajul de win-back per comandă/limbă
uv run cs_refused_recovery.py --days 14 --json                # pt automatizare (export către dialer/SMS)
```

## Cum funcționează
- Refuzate: `profit_orders.status_category='Refuzata'` din `data/profitability.db` (VPS, prin SSH), filtrate pe `created_at` recent.
- Contact client: join pe `order_name` cu `metrics.orders` (shippingName, shippingPhone, city, country, totalPrice).
- Sortare pe valoarea comenzii (cele mari întâi). Mesaj de win-back per limbă (RO default; EN pt piețele non-RO — de extins BG/CZ/PL/HU).
- Read-only (doar SELECT). Trimiterea efectivă (SMS/WhatsApp/apel) se face din uneltele voastre — aici e coada + textul.

## Idei de extins
- Mesaje native BG/CZ/PL/HU; link de plată card per comandă; marcaj „recontactat / acceptat" (necesită un store de stare); excludere clienți „refuznici seriali" (vezi cs-customer-360).
