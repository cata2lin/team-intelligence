---
name: gads-audit
description: >-
  Auditor MULTI-CONT Google Ads — face sweep pe TOATE conturile din MCC și
  flaghează leak-urile recurente (limbă greșită/Catalan, igienă conversii,
  COD-form netrackuit, câștigători capați, drainere, UTM lipsă, tCPA cold-start
  blocat). Read-only by default; fix sigur auto pentru limbă. Use când vrei
  "audit google ads", "verifică toate conturile", "ce e stricat pe conturi",
  "de ce e CPA mare", "găsește risipa", "audit cont nou". Complementul de SWEEP
  al gigi:google-ads-mcc (operează) / gigi:campaign-structure (un cont) /
  gigi:ads-anomalies (anomalii temporale).
tools: Read, Bash, Grep
---

# gigi:gads-audit — auditor multi-cont Google Ads

Rulează o baterie de verificări pe **toate** conturile din MCC dintr-o singură
comandă și scoate un raport cu flag-uri (severitate + acțiune). Prinde automat
exact bug-urile pe care le tot găseam manual (catalană, YT-subs-ca-conversii,
COD netrackuit, drainere). Construit după ce un sweep manual a descoperit că
**Gento/GT/Nubra/Carpetto + Ofertele** rulau pe limba **catalană** (reach ≈0) și
mai multe PMax-uri erau drainere sub breakeven.

## Rulare
```
uv run scripts/gads_audit.py --all                 # toate conturile, RAPORT (read-only)
uv run scripts/gads_audit.py --customer <CID>      # un singur cont
uv run scripts/gads_audit.py --all --fix-language  # AUTO-FIX sigur: +limba corectă / −Catalan
```
Auth = `google_ads_connections` din metrics (ca tot toolkit-ul google-ads). Read-only
by default; singurul auto-fix e limba (clar corect, reversibil).

## Verificări
| Cod | Ce prinde | Acțiune |
|---|---|---|
| **LANG** | limbă greșită — Catalan(1038) FĂRĂ limba contului, sau lipsă RO(1032)/CZ(1021)… (mapat din monedă) | `--fix-language` (+limba corectă, −Catalan) |
| **CONV** | igienă conversii — primary care NU e PURCHASE (YouTube subs/views, Calls from ads, micro-conv) + 2+ PURCHASE primary (de-dup) | manual: scoate din primary (capcană: `YOUTUBE_HOSTED` = ne-mutabile via API) |
| **CODGAP** | spend dar ~0 conversii PURCHASE 30z = **COD form netrackuit** (pattern Carpetto/Ofertele/CZ) | `gigi:google-ads-mcc` → `cod_tracking.py` |
| **CAPPED** | câștigător capat — `budget_lost_impression_share`>15% & ROAS>3 → bani lăsați pe masă | ridică bugetul |
| **DRAIN** | drainer — spend>200 & ROAS<2 (sau 0 conv) → arde bani | taie/strânge tROAS/pauză |
| **UTM** | `customer.final_url_suffix` gol (atribuire warehouse/Shopify ruptă) | setează UTM (vezi google-ads-mcc playbook) |

## Reguli
- **Read-only by default.** Doar `--fix-language` scrie (sigur). Restul = raport → decizi/aplici cu `gigi:google-ads-mcc`.
- **Grandia = agenția lor** — auditează (observă) dar **NU interveni** fără cerere explicită; e contul lor.
- Mapare monedă→limbă/geo în `CUR_LANG` (RON→RO 1032/2642, CZK→CZ 1021/2203, …) — extinde la nevoie.
- Capcană limbă: **1038 = Catalană, NU română** (RO = 1032). Bug istoric în playbook-ul de lansare — vezi [[cz-rossi-google-ads-launch]] / memoria de igienă conversii.

Legături: [[gads-conversion-hygiene]], [[google-ads-launch-playbook]], [[cz-rossi-google-ads-launch]].
