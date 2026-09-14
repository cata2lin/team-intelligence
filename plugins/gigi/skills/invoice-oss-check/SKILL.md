---
name: invoice-oss-check
description: "Audit that SmartBill invoices apply the CORRECT destination-country OSS VAT rate — catch intl orders (PL/BG/CZ/HU/SK) wrongly invoiced at the RO 21% rate instead of their country's rate. Samples recent invoices (from the xConnector invoicing log or given numbers), downloads each PDF from SmartBill, extracts the VAT rate + country, and compares to the OSS table (RO 21, CZ 21, BG 20, PL 23, HU 27, SK 23, GR 24, HR 25). Rate-limit-aware (SmartBill throttles PDF downloads hard → spaces requests + backs off on 403). Use to verify the weekly xConnector invoicing cron and the Trendyol invoicing are fiscally correct on OSS. Triggers: 'verifică cota TVA pe facturi', 'OSS corect pe facturi', 'facturile intl au cota țării?', 'audit TVA facturi', 'check invoice VAT rate', 'sunt facturate corect PL/BG/CZ'."
argument-hint: "--from-log [--per-prefix N] [--gap S] | --prefix PL,CZ,BONBG --per-prefix 3 | --numbers 583051,565789"
---

# invoice-oss-check — auditează cota OSS pe facturi

**Riscul fiscal:** o comandă intl (Bonhaus PL/BG/CZ, etc.) facturată din greșeală la cota **RO 21%** în loc de cota
țării de destinație (PL 23%, BG 20%, HU 27%…) = TVA greșit declarat prin OSS. Skill-ul verifică empiric,
citind PDF-ul real al facturii din SmartBill.

## Ce face
1. Ia un eșantion de facturi: `--from-log` (parsează `xc_invoice.log`, „PREFIX### → ARONA NUM", grupat pe prefix),
   sau `--numbers N1,N2`, sau `--prefix PL,CZ`.
2. Descarcă PDF-ul (`GET /invoice/pdf`), extrage **cota TVA** (`\d+%`) + **țara** (Poland/Bulgaria/Czech…).
3. Compară cu tabelul **OSS**: RO 21, CZ 21, BG 20, PL 23, HU 27, SK 23, GR 24, HR 25, DE 19.
4. Raportează per prefix/țară + listează 🔴 MISMATCH (țară X cu cotă ≠ cota ei).

## Rulare (pe VPS — are logul + creds SmartBill din KB)
```bash
ssh <vps> 'cd /root/Scripturi; export KB_DATABASE_URL=... ; export SMARTBILL_EMAIL=$(kb...); export SMARTBILL_TOKEN=$(kb...)
  .venv/bin/python invoice_oss_check.py --from-log --per-prefix 2 --gap 12'
# țintit pe intl:
  .venv/bin/python invoice_oss_check.py --prefix PL,CZ,BONBG --per-prefix 3 --gap 14
```

## ⚠️ Capcane
- **SmartBill rate-limitează PDF-urile AGRESIV** ("Ai depasit limita maxima de...") → folosește `--gap 12-15`s +
  eșantion mic. Scriptul face backoff 25s pe 403. NU trage zeci de PDF-uri rapid.
- Țara se detectează din textul PDF; magazinele RO pot să nu scrie „Romania" → afișate ca „?" cu cotă 21 (OK RO).
- Verificat 28-iul: **PL → 23%** (corect), magazine RO → 21% (corect). Cronul `xc_invoice.sh` aplică OSS corect.

Related: `gigi:invoice-audit` · `gigi:trendyol-invoicing` · [[trendyol-invoicing-flow]] · [[smartbill-transport-nefacturat]]
