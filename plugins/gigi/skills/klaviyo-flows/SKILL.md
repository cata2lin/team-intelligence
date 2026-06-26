---
name: klaviyo-flows
description: >-
  CONSTRUIEȘTE flow-urile email/SMS lipsă pe un brand Klaviyo — auditează gap-ul
  față de cele 10 flow-uri standard de ecommerce, generează conținutul RO în
  vocea brandului și CREEAZĂ template-urile de email via API. Complementul de
  EXECUȚIE al gigi:klaviyo (care doar auditează). Use când "construiește flow-uri
  klaviyo", "email automation lipsă", "welcome/abandoned cart/winback", "ce flow-uri
  ne lipsesc". Chei KLAVIYO_<BRAND>_PRIVATE_KEY în KB (avem ESTEBAN, GT).
tools: Read, Bash
---

# gigi:klaviyo-flows — construiește flow-urile lipsă

`gigi:klaviyo` **auditează** golurile; acest skill le **umple**: generează conținutul RO
+ creează template-urile via API, ca să nu mai lași bani pe masă pe email.

```
uv run scripts/klaviyo_flows.py --brand GT --audit                  # ce flow-uri standard lipsesc
uv run scripts/klaviyo_flows.py --brand GT --build welcome --apply  # generează RO + creează template-urile
uv run scripts/klaviyo_flows.py --brand GT --build all --apply      # toate flow-urile lipsă
```

## Cele 10 flow-uri standard (acoperite)
| Flow | Trigger | # emailuri |
|---|---|---|
| Welcome Series | Added to List (signup) | 3 |
| Abandoned Cart | Checkout Started | 3 |
| Browse Abandonment | Viewed Product | 2 |
| Post-Purchase | Placed Order | 3 (incl. cerere review) |
| Winback | metric no-purchase 60d | 2 |
| Back in Stock | Back in Stock | 1 |
| Birthday | date property | 1 |
| Sunset / Re-engagement | disengaged 120d | 1 |

## ⚠️ Realitatea API-ului
Klaviyo creează **fiabil**: template-uri, segmente, liste (le facem). Crearea unui **flow COMPLET**
(graf trigger→delay→email) via API e complexă/limitată → skill-ul livrează **template-urile + spec-ul**
(trigger + întârzieri + secvență), iar cablarea finală a flow-ului = ~2 min/flow în UI (Flows → Create →
trigger → adaugă emailurile cu template-urile). Conținutul + template-urile (partea grea) sunt gata.

## Voce per brand
- **Esteban** = lux accesibil (elegant, aspirațional, cald). **GT** = influencer energy (direct, "miroase
scump dar nu e scump"). COD-aware peste tot ("plată ramburs, livrare rapidă"). Treci textul prin
`gigi:ai-scrub` înainte de live. Coduri promo (WELCOME10/CART10/COMEBACK15/BDAY20) — verifică-le în Shopify.

Legături: [[marketing-toolkit]] (gigi:klaviyo audit + chei). Read-only by default (`--audit`); `--apply` creează template-uri.
