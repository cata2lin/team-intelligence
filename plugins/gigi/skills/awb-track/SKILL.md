---
name: awb-track
description: Live multi-courier AWB status tracker — paste one or many AWB numbers and get the current status across DPD, Sameday, Econt and Packeta, with auto-detection of which courier an AWB belongs to. Returns delivered / in-transit / returned / refused per parcel and flags problem shipments. Use for "track this AWB", "status colet", "unde e coletul", "is this delivered", "check these tracking numbers", "bulk AWB status", "ce colete sunt returnate". Read-only.
---

# AWB tracker live (DPD / Sameday / Econt / Packeta)

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
- Curier auto-detectat din pattern-ul AWB (DPD numeric, Packeta `Z...`, Econt `ee...`, etc.), apoi se interoghează API-ul live: DPD `api.dpd.ro/v1/track`, Sameday `api.sameday.ro`, Econt `ee.econt.com`, Packeta.
- Credențiale din KB (`DPD_RO_USERNAME/PASSWORD`, `DPD_JG_*`, `COURIER_CREDS_JSON` cu sameday/econt/packeta). Statusurile brute se mapează la categorii: livrat / in_tranzit / returnat / refuzat.
- Read-only (doar tracking).

## Limitări
- Rate-limit per curier — pentru loturi mari se face throttling. DPD + Sameday sunt cele mai bine acoperite; Econt/Packeta în funcție de credențialele disponibile.
