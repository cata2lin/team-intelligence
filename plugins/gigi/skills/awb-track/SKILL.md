---
name: awb-track
description: Live multi-courier AWB status tracker — paste one or many AWB numbers and get the current status across DPD, Sameday, Econt, Packeta and Dragon Star (DSC), with auto-detection of which courier an AWB belongs to. Returns delivered / in-transit / returned / refused per parcel and flags problem shipments. Use for "track this AWB", "status colet", "unde e coletul", "is this delivered", "check these tracking numbers", "bulk AWB status", "ce colete sunt returnate". Read-only.
---

# AWB tracker live (DPD / Sameday / Econt / Packeta / Dragon Star)

Tracking live multi-curier. Lipești unul sau mai multe AWB-uri și primești statusul curent normalizat (livrat / în tranzit / returnat / refuzat), cu auto-detecția curierului după pattern-ul AWB-ului. Reutilizează logica testată din aplicația AWB (awb.arona.ro / Scripturi `bulk_tracker.py`).

## Cum rulezi
```bash
uv run awb_track.py --awb 81298289998,81299189040        # mai multe AWB-uri, auto-detect curier
uv run awb_track.py --awb-file awbs.txt                    # un AWB pe linie
uv run awb_track.py --awb 12345 --courier dpd              # forțează curierul
uv run awb_track.py --awb ... --problems                   # doar coletele cu probleme (returnat/refuzat)
uv run awb_track.py --awb ... --json                       # output JSON
```

## Cum funcționează
- Curier auto-detectat din pattern-ul AWB (DPD `8...`, Packeta `Z...`, Sameday `1O...`, Econt `10...`, **Dragon Star `9xxxxxxx` 8 cifre**), apoi se interoghează sursa live: DPD `api.dpd.ro/v1/track`, Sameday `api.sameday.ro`, Econt `ee.econt.com`, Packeta, **Dragon Star `dragonstarcurier.ro/tracking-awb` (status server-side, fără credentiale)**.
- Credențiale din KB (`DPD_RO_USERNAME/PASSWORD`, `DPD_JG_*`, `COURIER_CREDS_JSON` cu sameday/econt/packeta). Dragon Star NU cere credentiale. Statusurile brute se mapează la categorii: livrat / in_tranzit / returnat / refuzat / generat.
- Read-only (doar tracking).

## Limitări
- Rate-limit per curier — pentru loturi mari se face throttling. DPD + Sameday sunt cele mai bine acoperite; Econt/Packeta în funcție de credențialele disponibile.
- **Dragon Star (DSC)** = curier nou, doar pe Grandia (connector xConnector 24257). NU se sincronizează în AWBprint/Frisbo (`shipment_status` rămâne null) — de-aceea acest tracker live e singura sursă de status real pentru DSC. AWB-urile sunt 8 cifre (`94xxxxxx`).
