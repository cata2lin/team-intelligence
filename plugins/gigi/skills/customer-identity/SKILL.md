---
name: customer-identity
description: Unified CROSS-PLATFORM customer identity — links a Shopify customer to their Richpanel conversations across Email, Facebook, Instagram and Messenger, and back. Give ANY starting point (email, phone, Shopify order number, or a Richpanel conversation number) and get ONE merged profile: who they are (name, all emails/phones, city, social handles), what they bought (every order across all stores with delivery/refusal status + products + LTV), and all their support tickets across every channel, plus auto-flags (serial COD refuser, LTV, open complaints). Bridges three sources: Richpanel's own CDP (get_customer_by_email_or_phone — already carries orderIds + store + LTV), metrics.orders (email/phone↔order↔name), and profit_orders (deliverability + profit + products). For social MESSAGE threads (Messenger/IG DM) it regex-extracts the email/phone the customer typed in the conversation (CS asks for it) and links that to Shopify. Use for "who is this customer", "cine e clientul", "leagă tichetul de comandă", "cross-platform customer", "identitate client", "client 360 cu tichete", "match Richpanel to Shopify", "is this a returning customer / serial refuser". Read-only — writes nothing.
---

# customer-identity — identitate unificată cross-platform (Shopify ↔ Richpanel)

Răspunde la „cine e clientul ăsta și ce istoric are", indiferent pe ce canal a apărut.

## Cum rulezi
```bash
uv run customer_identity.py --email client@gmail.com
uv run customer_identity.py --phone 0760383019
uv run customer_identity.py --order EST185476        # din nr comandă
uv run customer_identity.py --conv 249890            # din nr conversație Richpanel (extrage email/tel din textul mesajelor)
uv run customer_identity.py --email x@y.ro --json     # ieșire structurată
```

## Ce întoarce (un singur profil)
- **Identitate:** nume, toate emailurile, toate telefoanele, oraș, handle-uri sociale, magazinele.
- **Comenzi:** fiecare comandă (toate magazinele) + livrabilitate (Livrată/Refuzată/Netrimisa), produse, LTV. Cross-check cu LTV-ul Richpanel.
- **Tichete:** toate conversațiile pe toate canalele (Email/FB/IG/Messenger), câte deschise.
- **Flaguri:** 🚩 REFUZNIC SERIAL (≥2 refuzuri sau ≥50% rată refuz → ofertă DOAR pe card, nu COD).

## Punțile (cum leagă)
1. **Richpanel CDP** (`get_customer_by_email_or_phone`) — profilul lor e deja un CDP: `orderIds` + magazin (`appClientId`) + LTV + telefoane + social. Punte primară.
2. **metrics.orders** (Postgres) — email/telefon ↔ `name` comandă ↔ nume client + total.
3. **profit_orders** (SQLite pe VPS, via SSH) — `order_name` → livrabilitate + profit + produse (SKU).

## Limite (sincer — din structura datelor)
- **Email** (27% din tichete) → punte directă. ✅
- **Mesaje sociale** (Messenger/IG DM, ~12%) → email/telefon e în **corpul** conversației (CS îl cere) → extras cu regex. ✅ când e prezent.
- **Comentarii FB/IG la reclame** (60%) → doar ID page-scoped anonimizat + pagina. **Individul NU se poate identifica** (privacy Facebook — nici numele nu vine), doar magazinul. ❌→🏬

## Necesită
- `RICHPANEL_MCP_TOKEN`, `DATABASE_URL_METRICS` (din KB / `kb.py secret-get`).
- Acces SSH la VPS-ul Scripturi pentru `profit_orders` (livrabilitate/profit).

Read-only peste tot. Vezi și `gigi:cs-customer-360` (doar Shopify) și `gigi:richpanel-export` (istoricul tichetelor).
