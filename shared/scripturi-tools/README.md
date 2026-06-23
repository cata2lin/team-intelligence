# Scripturi VPS — tool-uri pe care le folosim (mirror version-controlled)

Tool-uri **standalone** (rulabile, au `__main__`) din aplicația Scripturi de pe VPS
(`/root/Scripturi`), aduse aici ca **mirror byte-identic** ca să le aibă toată echipa în git.
**NU** e aplicația web (routes/models/dashboard — alea rămân pe VPS); doar scripturile pe care le rulăm.

| Script | Ce face |
|---|---|
| `sync_raport_zilnic.py` | Backfill `daily_perf` + `profit_marketing_override` din sheet-ul „CPA și financiar". **Fără arg** = tab istoric „Raport Zilnic 2" (zile complete, până ieri) → cron `0 4`. **`--today`** = tab „Raport azi" (ziua curentă, refresh ~5 min) → upsert rândul de AZI în daily_perf + refresh override-ul lunii curente → cron `*/10 6-23`. **De ce contează:** marketingul din engine-ul de P&L vine din `profit_marketing_override` (= acest sheet, sursă PRIMARĂ; `cache.product_ad_spend` e doar fallback) — deci când tokenul Meta expiră și cache-ul Meta devine 0/stale, P&L-ul pe brand rămâne corect (citește sheet-ul), iar `--today` asigură că include și ziua curentă. |
| `sync_barcodes.py` | Sincronizează barcode-urile din toate magazinele Shopify. |
| `sheets_labels.py` | Generează label-uri + barcode-uri din Google Sheets (per rând). |
| `shopify_image_manager.py` | Redenumire + compresie + alt-text poze Shopify (moduri: `rename` etc.). |
| `shopify_tag_orders_parallel.py` | Tag comenzi Shopify în paralel (workers + throttling, GraphQL `tagsAdd`). |
| `sku_to_url.py` | Mapare SKU → URL produs (folosește `core.stores`, fallback CSV). |
| `upload_shopify_img.py` | Upload imagini în Shopify (folosește `core.stores`, fallback CSV). |

## Reguli (ca să NU divergă de VPS)
- **Editezi AICI (git)**, apoi deployezi: `scp shared/scripturi-tools/<x>.py $VPS:/root/Scripturi/<x>.py`.
- **Drift check**: `ssh $VPS 'cat /root/Scripturi/<x>.py' | diff - shared/scripturi-tools/<x>.py` → trebuie gol.
- Secrete: niciunul hardcodat (citesc din env/`core.stores`/secrete) — verificat. **Nu pune secrete aici.**

## Ce a RĂMAS pe VPS (nu sunt „scripturi", sunt aplicația)
Module importate de FastAPI (fără `__main__`): `serial_refuser.py`, `shipment.py`, `validation_service.py`,
+ rutele/modelele app-ului + `test_*.py` (scratch). Astea NU se urcă (vezi engine-ul de profit în
`gigi/skills/metrics-cache/engine/` — singura excepție de „cod de app" versionat, fiindcă e P&L-ul canonic).
