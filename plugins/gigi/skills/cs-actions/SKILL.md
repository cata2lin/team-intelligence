---
name: cs-actions
description: Operațiunile CS de tip ACȚIUNE pe orice magazin ARONA, declanșate de agent din chat — anulează comandă, plasează comandă nouă COD, swap/înlocuire, resend (retrimitere gratis), modifică adresa, factură. Găsește comanda după NUMĂR, NUME client sau TELEFON (din DB intern metrics.orders, nu din Shopify unde PII e blocat). Adresa pt swap/resend din xConnector (GT) / Frisbo (restul) / din chat. Taghează cu agentul CS (Raluca/Oana/Andra/Anna/OanaO) + tag-ul operațiunii. DRY-RUN implicit, scrie doar cu --apply. Use pt „anuleaza comanda lui Ion Pop", „fa o comanda noua pt clientul 0750...", „swap pe alta marime", „retrimite gratis produsul spart", „schimba adresa la comanda X", „cancel/place/swap order".
---

# /cs-actions

Operațiunile CS care **fac ceva** (scriu în Shopify), pe care un agent le cere din chat. Tu (Claude) rezolvi
clientul→comandă, confirmi scurt, apoi execuți. Toate magazinele ARONA (tokenii `write_orders` din `SHOPIFY_STORES_CSV`).

## Pasul 0 — cine e agentul (o dată pe sesiune)
Taghează fiecare comandă cu agentul CS. Stabilește-l **o dată**: din userul de Claude (`$EMPLOYEE_HANDLE`/`$USER`)
dacă se mapează la un agent, altfel întreabă o dată „cu ce agent CS lucrez? (Raluca/Oana/Andra/Anna/OanaO)".
Apoi pune `--agent <Nume>` (sau exportă `CS_AGENT=<Nume>`) la fiecare acțiune. Nu mai întreba după.

## Pasul 1 — găsește comanda (NUMĂR, NUME sau TELEFON)
Agentul are adesea numele/telefonul, nu numărul. **Rezolvă din DB-ul intern** (Shopify blochează căutarea după
client — PII). Interoghează `metrics.orders` (MCP `postgres-metrics`):
```sql
SELECT o.name, o."shippingName", o."financialStatus"::text fin, o."fulfillmentStatus"::text ful,
       o."cancelledAt", s."shopifyDomain"
FROM orders o JOIN shopify_stores s ON s."brandId"=o."brandId"
WHERE o."shippingPhone" LIKE '%<ultimele 9 cifre>%' OR o.phone LIKE '%<...>%'
   OR o."shippingName" ILIKE '%<nume>%'
ORDER BY o."shopifyCreatedAt" DESC LIMIT 10;
```
Mapează `shopifyDomain → prefix` (din `stores.csv`: ex `n12w89-yy…→GRAN`, `6f9e22-9d…→EST`) și dă-l ca `--store`
(numele comenzii poate avea alt prefix: `GRAND17148` e magazinul `GRAN`). Mai mulți candidați → confirmă cu agentul.

## Pasul 1.5 — produs pe mai multe magazine (ex. HA), fără --store → alegi TU
Produsele **HA-** (deals) sunt același titlu pe magazinele de oferte, fiecare cu SKU propriu:
**RO** = Casa ofertelor (`BON`=bonhaus.myshopify.com) · Reduceri bune (`RED`) · Ofertele zilei (`OFER`) · Magdeal ·
**BG** = Bonhaus (`BONBG`=ux1x6n-n2) · **CZ** = Bonhaus.cz (`CZ`). Nu te baza pe nume — **alege după STOC real**.
Dacă agentul dă produsul fără magazin, rezolvă din `metrics`:
```sql
SELECT s."shopifyDomain", li.sku, MAX(v."inventoryQuantity") AS stoc
FROM order_line_items li JOIN orders o ON o.id=li."orderId"
  JOIN shopify_stores s ON s."brandId"=o."brandId"
  JOIN variants v ON v.sku=li.sku AND v."brandId"=o."brandId"
WHERE li.title ILIKE '%<produs>%' GROUP BY 1,2 ORDER BY stoc DESC;
```
Filtrează după **PIAȚA clientului** (prefix telefon: `+40`→RO, `+359`→BG, `+420`→CZ, `+48`→PL):
RO → magazine RO (RED/BON/GT/EST/GRAN/…) · BG → BG/BONBG · CZ → CZ · PL → PL. Alege magazinul **cu cel mai mult
stoc** din piața clientului și folosește **SKU-ul ACELUI magazin** la `--items`. Două candidate RO cu stoc → confirmă scurt.

## Pasul 2 — execută (DRY-RUN întâi, apoi --apply)
```
cs_actions.py cancel  --order GRAND17148 --store GRAN [--reason customer|inventory|declined|fraud|other] [--refund] [--no-restock]
cs_actions.py place   --store GT --name "Ion Pop" --phone 0750... --address "Str X 1" --city Ploiesti --zip 100294 --items "SKU:2;termen:1"
cs_actions.py swap    --from-order EST188351 --items "GD-BR-6660:1"        # copiază adresa + tag swap
cs_actions.py resend  --from-order GT44004  --items "SKU:1"               # retrimitere GRATIS (100% discount) + tag resend
cs_actions.py modify  --order EST188351 --store EST [--address "Str Noua 9" --city Cluj --zip 400001] \
                      [--add "SKU:1"] [--remove "termen"] [--set "termen:3"]    # adresă ȘI/SAU produse (orderEdit)
cs_actions.py invoice --order GT44004                                     # factură fiscală SmartBill
```
- **Fără `--apply` = DRY-RUN** (arată exact ce ar face). Arată sumarul agentului, confirmă, apoi rulează cu `--apply`.
- **COD**: `place/swap/resend` creează comandă neplătită (`paymentPending`) → se expediază, plătește la livrare.
- **Produse**: `--items "termen:cantitate;..."` (termen = SKU exact sau titlu). Ambiguu → scriptul cere SKU-ul.
- **modify** schimbă adresa (REST, pre-fulfillment) și/sau produsele (`--add`/`--remove`/`--set` prin orderEdit→commit).
- **Adresa la swap/resend**: xConnector (GT) / **Frisbo** (restul — org per magazin, mapat în `FRISBO_BY_PREFIX`). Fallback: `--address --city --zip`.
- **invoice**: SmartBill per magazin — necesită KB `SMARTBILL_STORES`=`[{prefix,email,token,cif,series}]`. ⚠ emite document fiscal REAL — testează pe o comandă întâi.
- **Taguri**: agentul mereu; `swap`/`resend`/`garantie`/`anulat-cs`/`adresa-modificata` după caz.

## Auth (nimic nu se printează)
- Shopify write per magazin: `SHOPIFY_STORES_CSV` (KB) — toate 20 au `write_orders`.
- Adresă swap/resend: `XCONNECTOR_SHOPS` (GT) / `FRISBO_ORG_TOKENS` (restul) — KB.
- Client→comandă: DB `metrics.orders` prin MCP `postgres-metrics` (read-only).

## Siguranță
DRY-RUN implicit; **confirmă cu agentul înainte de `--apply`** (sunt acțiuni cu bani/stare). `cancel` are
`notifyCustomer:false` (nu spamează clientul). Pentru COD verifică riscul de refuz (vezi gigi:cod-confirmation)
înainte de comenzi mari. Nu citește/scrie PII din Shopify — adresele vin din xConnector/Frisbo/chat.

## Operațiuni acoperite (din analiza a 223k tichete)
anulare (1.7%) · comandă nouă (0.3%) · swap (0.2%) · resend produs spart · modificare comandă (0.4%) · factură (0.3%).
Pereche cu răspunsurile (WISMO/retur/presale): gigi:cs-draft-reply, gigi:awb-track, gigi:cs-stock-answer.
