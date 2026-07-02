---
name: dpd-awb
description: Creează un AWB DPD între DOUĂ adrese ORICARE (expeditor + destinatar liberi), NElegat de o comandă Shopify — pt ridicare de la un terț, retururi, expedieri one-off. Direct prin API-ul DPD (api.dpd.ro) pe contul ARONA. Rezolvă adresele (oraș+stradă din nomenclatorul DPD), calculează prețul, creează AWB-ul și descarcă eticheta PDF. Dry-run by default; creează real doar cu --apply. Use pt „fă-mi un AWB DPD de la X la Y", „AWB de ridicare de la <furnizor/instituție>", „trimite un colet de la adresa A la adresa B", „AWB one-off / retur cu DPD". NU e pt comenzi Shopify (alea → gigi:xconnector awb-make) și NU e doar tracking (ăla → gigi:awb-track).
---

# AWB DPD cu expeditor + destinatar liberi

Creează un AWB DPD între orice două adrese, prin API-ul DPD direct (`api.dpd.ro`), pe contul ARONA. Pentru cazurile pe care `gigi:xconnector` NU le acoperă (acela face AWB doar pt o **comandă Shopify**, cu magazinul ca expeditor).

## Când folosești ce
- **Comandă Shopify** (magazin → client) → `gigi:xconnector awb-make`.
- **Doar urmărire** AWB → `gigi:awb-track`.
- **Expeditor/destinatar arbitrari** (ridicare de la un terț, retur, one-off) → **ăsta**.

## Cum rulezi
```bash
# DRY-RUN (rezolvă adresele + calculează prețul, NU creează):
uv run scripts/dpd_awb.py \
  --from-name "CRSP Iasi" --from-phone 0232410399 --from-city Iasi --from-street "Victor Babes" --from-no 14 --from-zip 700465 \
  --to-name "ARONA SRL" --to-contact "Gheorghe Beschea" --to-phone 0746661159 --to-city Brasov --to-street Bazaltului --to-no 11 \
  --content Documente --weight 0.5

# CREARE reală + etichetă PDF în ~/Downloads/AWB_<nr>.pdf:
uv run scripts/dpd_awb.py ...aceleași... --apply
```
Flag-uri: `--parcels N` (colete, def 1) · `--package ENVELOPE|BOX|PALLET|OTHER` · `--cod SUMA` (ramburs) · `--from/--to-private` (persoană fizică, default firmă) · `--from/--to-email` · `--ref` · `--account dpd-ro|dpd-jg|dpd-px` · `--out <folder>`.

## Cum funcționează
- **Credențiale** din KB `COURIER_CREDS_JSON` → `dpd_creds.dpd-ro` (aceleași ca `gigi:awb-track`). Cont implicit `dpd-ro`.
- **Adrese**: rezolvă orașul (`/location/site/` după cod poștal, altfel nume) + strada (`/location/street/`) în nomenclatorul DPD → `siteId`/`streetId`. RO folosește nomenclator, deci strada trebuie să existe (dacă nu, dă eroare — verifică denumirea).
- **Serviciu**: DPD STANDARD (id 2505), livrare domestică.
- **Plătitor**: implicit **contul ARONA** (clientId luat automat din `/client/`, ca `THIRD_PARTY`). Asta e cheia: DPD cere ca „**clientul tău să fie plătitor sau expeditor**" — când NICIUNA din adrese nu e a noastră (ex ridicare de la un furnizor), setăm contul nostru ca plătitor-terț. Override: `--payer sender|recipient|third`.
- **Dry-run by default**: calculează prețul + validează adresele. `--apply` face POST-ul real (creează AWB, programează ridicarea, generează eticheta).

## Capcane (descoperite empiric)
- **Schema diferă între endpoint-uri**: `/calculate/` vrea `addressLocation` + `serviceIds` (array); `/shipment/` vrea `address` + `serviceId` (singular). Tool-ul le tratează automat.
- **clientId pe destinatar FORȚEAZĂ adresa înregistrată** a clientului (nu poți da și `addressLocation` alături) → NU pune clientId pe părți dacă vrei o adresă liberă; plata se face prin `payment.thirdPartyClientId`.
- **`privatePerson` interzis** când dai clientId pe o parte.
- Preț exemplu (plic documente Iași→Brașov, ambele la domiciliu): **13,87 RON** cu TVA (include surtaxă ridicare + livrare la adresă + combustibil).
- Etichetă via `/print/` (PDF A6). Tracking: `https://tracking.dpd.ro/?shipmentNumber=<AWB>`.

## Limitări
- Doar **DPD** (nu Sameday/Packeta). Doar **domestic RO** (serviciu 2505); pt internațional/alt serviciu, extinde `--service`.
- Scrie bani/stare (creează expediere fizică + cost) → **--apply doar cu confirmare**.
