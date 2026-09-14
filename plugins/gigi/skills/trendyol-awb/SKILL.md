---
name: trendyol-awb
description: "Download the Trendyol shipping labels (AWB) for orders ready to ship, as ONE merged PDF pulled to your Mac — the CLI version of the dashboard's 'Descarcă AWB Trendyol' button. Trendyol is separate from xConnector/AWBprint: its orders + labels live in the scripts.arona.ro web app (api/trendyol_awb.py, SQLite data/trendyol_orders.db). This runs the exact download logic IN-PROCESS on the VPS (bypassing the dashboard login), merges the labels with SKU overlay grouped by brand, marks them downloaded (out of the print queue), and SFTPs the PDF to ~/Downloads. Triggers: 'descarcă AWB-urile de Trendyol', 'download Trendyol labels', 'etichete Trendyol', 'Trendyol shipping labels', 'ce comenzi Trendyol am de expediat', 'print Trendyol orders', 'AWB Trendyol'."
argument-hint: "list  |  download [--dir ~/Downloads]"
---

# trendyol-awb — descarcă etichetele AWB Trendyol

Trendyol e **separat** de xConnector/AWBprint (`gigi:xconnector print-batch` NU-l acoperă). Comenzile +
etichetele lui trăiesc în app-ul web `scripts.arona.ro` (`api/trendyol_awb.py`, SQLite `data/trendyol_orders.db`).
În dashboard e butonul **„Descarcă AWB Trendyol"** → ruta `/api/trendyol-awb/download`. Skill-ul face EXACT asta,
din CLI: rulează logica **in-proces pe VPS** (import `api.trendyol_awb`), deci nu-i trebuie login-ul dashboard-ului.

## Comenzi

```bash
uv run scripts/trendyol_awb.py list                    # ce comenzi sunt de descărcat (fără efect)
uv run scripts/trendyol_awb.py download                # descarcă + îmbină PDF + trage în ~/Downloads
uv run scripts/trendyol_awb.py download --dir /alt/loc # alt folder destinație
```

## Ce face `download`
1. **Sync live** din Trendyol (`_sync_open_orders`) → filtrează comenzile trecute deja pe Shipped/Delivered.
2. Ia etichetele pt comenzile **`Picking`/`Created`/`Invoiced`** nedescărcate (`cargoTrackingNumber` → label URL).
3. Le **îmbină într-un singur PDF** (`Trendyol <dd.mm.yyyy HH-MM>.pdf`), cu overlay SKU + grupare pe brand.
4. **Marchează `downloaded=1`** în `trendyol_orders.db` (ies din coada de print — ca butonul).
5. Copiază PDF-ul pe VPS în `/tmp` și-l **trage prin SFTP** în `~/Downloads` (sau `--dir`).

## Capcane / de știut
- ⚠️ **`download` marchează comenzile downloaded** → ies din coadă. Fă-l DOAR când chiar descarci pt print
  (ca `gigi:xconnector print-batch --apply`). `list` e inofensiv (nu atinge nimic).
- Auth: dashboard-ul cere login (JWT `JWT_SECRET_KEY`) → de-asta rulăm **in-proces** (import direct), nu prin HTTP.
- Creds Trendyol (`TRENDYOL_TOKEN/SELLER_ID/…`) le are deja app-ul din `/root/Scripturi/.env` — nu le injectăm noi.
- Statusuri Trendyol: `Created→Picking→Invoiced→Shipped→Delivered` (+ `Cancelled/Returned/UnDelivered/UnPacked`).
  Doare cele **deschise** (Picking/Created/Invoiced) au etichete de descărcat.
- Un `cargoTrackingNumber` = un barcode CODE128; pt DPD eticheta fizică folosește `cargoSenderNumber` (AWB 812…).

Related: `gigi:xconnector` (print-batch pt restul magazinelor), `gigi:tom` (PO-uri), [[trendyol-awb-download]].
