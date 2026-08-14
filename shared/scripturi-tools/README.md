# Scripturi VPS — tool-uri pe care le folosim (mirror version-controlled)

Tool-uri **standalone** (rulabile, au `__main__`) din aplicația Scripturi de pe VPS
(`/root/Scripturi`), aduse aici ca **mirror byte-identic** ca să le aibă toată echipa în git.
**NU** e aplicația web (routes/models/dashboard — alea rămân pe VPS); doar scripturile pe care le rulăm.

| Script | Ce face |
|---|---|
| `sync_raport_zilnic.py` | Backfill `daily_perf` + `profit_marketing_override` din sheet-ul „CPA și financiar". **Fără arg** = tab istoric „Raport Zilnic 2" (zile complete, până ieri) → cron `0 4`. **`--today`** = tab „Raport azi" (ziua curentă, refresh ~5 min) → upsert rândul de AZI în daily_perf + refresh override-ul lunii curente → cron `*/10 6-23`. **De ce contează:** marketingul din engine-ul de P&L vine din `profit_marketing_override` (= acest sheet, sursă PRIMARĂ; `cache.product_ad_spend` e doar fallback) — deci când tokenul Meta expiră și cache-ul Meta devine 0/stale, P&L-ul pe brand rămâne corect (citește sheet-ul), iar `--today` asigură că include și ziua curentă. |
| `wms_ad_spend_sync.py` | **Marketing per-SKU TOKEN-INDEPENDENT** (calea care NU depinde de OAuth-ul Meta). Trage spend-ul FB+TikTok per-campanie din sheet-ul WMS (`12L1KlG4...`, tab-uri „WMS Facebook 3"/„WMS Tiktok", conector direct din ad-platforme) → `profitability.db.wms_ad_spend` (acumulează istoric, sheet-ul ține doar ziua curentă). Trage și maparea (Nomenclator FB/TT + Product Group) + construiește **suplimentul** (`wms_nomen_extra` + `wms_product_group_extra` — conturile simple lipsă din Nomenclator: Nubra/Bonhaus/Esteban 3/Reflexino→Magdeal etc., + SKU→grup din comenzi). **Cron `*/30`.** Consumat de `metrics-cache/wms_marketing.py` → `profit_by_sku.py`: per-SKU marketing din WMS de la **cutover 2026-06-19** (moartea tokenului Meta), cache pt istoric (<cutover, neatins), Google mereu din cache. USD→RON din `fx_rates`. |
| `sync_barcodes.py` | Sincronizează barcode-urile din toate magazinele Shopify. |
| `sheets_labels.py` | Generează label-uri + barcode-uri din Google Sheets (per rând). |
| `shopify_image_manager.py` | Redenumire + compresie + alt-text poze Shopify (moduri: `rename` etc.). |
| `shopify_tag_orders_parallel.py` | Tag comenzi Shopify în paralel (workers + throttling, GraphQL `tagsAdd`). |
| `sku_to_url.py` | Mapare SKU → URL produs (folosește `core.stores`, fallback CSV). |
| `upload_shopify_img.py` | Upload imagini în Shopify (folosește `core.stores`, fallback CSV). |
| `sku_box_map_build.py` (+`.sh`) | Construiește **map-ul central SKU→nr colete** (`data/sku_box_map.json`) din metafield-urile `custom.nr_cutii`/`nr_produse` de pe cele 9 magazine deals (consensus per SKU, exclude ≤0). Folosit ca **fallback** de `xconnector.order_parcel_count` → setezi metafield-ul o dată pe orice magazin, cronul de AWB îl aplică pe TOATE (ca `sku_station`/DEPOZIT_SKU_RULES la depozite). **Cron `30 6 * * *`** (map-only). `--fill` completează și metafield-ul `nr_produse` pe magazinele unde lipsește (vizibilitate; one-time). |

## Reguli (ca să NU divergă de VPS)
- **Editezi AICI (git)**, apoi deployezi: `scp shared/scripturi-tools/<x>.py $VPS:/root/Scripturi/<x>.py`.
- **Drift check**: `ssh $VPS 'cat /root/Scripturi/<x>.py' | diff - shared/scripturi-tools/<x>.py` → trebuie gol.
- Secrete: niciunul hardcodat (citesc din env/`core.stores`/secrete) — verificat. **Nu pune secrete aici.**

## Ce a RĂMAS pe VPS (nu sunt „scripturi", sunt aplicația)
Module importate de FastAPI (fără `__main__`): `serial_refuser.py`, `shipment.py`, `validation_service.py`,
+ rutele/modelele app-ului + `test_*.py` (scratch). Astea NU se urcă (vezi engine-ul de profit în
`gigi/skills/metrics-cache/engine/` — singura excepție de „cod de app" versionat, fiindcă e P&L-ul canonic).

## gads_upload_conversions.py — server-side Enhanced Conversions (Data Manager API)
Trimite conversii server-side la Google Ads din comenzile AWBprint (email hash SHA-256), via `datamanager.googleapis.com/v1/events:ingest` (ConversionUploadService e deprecat pt integrări noi). Auth: `DATAMANAGER_REFRESH_TOKEN` + `YOUTUBE_OAUTH_CLIENT_ID/SECRET` (scope `datamanager`). Mod `delivered` (venit real) / `placed`. Idempotent (SQLite), dry-run by default, `--validate-only`. OMITE `--login-customer` dacă ai acces direct pe operating account. Cron VPS: `40 */3 * * *` pe Grandia (conv observation-only 7666059809). Vezi memoria [[grandia-takeover-google]].

## sku_box_map_build.py — nr colete central pe SKU (pt cronul de AWB)
Rezolvă „setezi nr colete pe UN magazin, dar produsul fan-out e pe multe": construiește un **map SKU→nr_cutii**
(`/root/Scripturi/data/sku_box_map.json`) din metafield-urile setate pe cele 9 magazine deals (ofertelezilei,
audusp-rf, bonhaus, covoareauto-ro, oriceredus, ux1x6n-n2, vthuzq-7j, 63e901-2f, 16w7xv-0w), consensus per SKU
(`nr_cutii` preferat, altfel `nr_produse`; exclude valorile ≤0 = zgomot). `xconnector.order_parcel_count` îl citește
prin `_sku_box_map_get(sku)` ca **fallback** când produsul local n-are metafield → **setezi o dată, merge pe toate**.
Mecanica e identică cu `sku_station()`/`DEPOZIT_SKU_RULES` (rută pe SKU), dar pentru nr colete.

**Deploy:** `scp shared/scripturi-tools/sku_box_map_build.{py,sh} $VPS:/root/Scripturi/`.
**Cron (pe crontab VPS, nu în git):** `30 6 * * * /usr/bin/flock -n /tmp/sku_box_map.lock /root/Scripturi/sku_box_map_build.sh >> /root/Scripturi/logs/sku_box_map.log 2>&1` (map-only; `--fill` doar manual, pt propagarea metafield-urilor).

**Regula de colete în `order_parcel_count`** (magazine split pe stații): densitate setată (`nr_cutii`/map) → `box×qty`
(ex așternut `0.5`=2/colet, geanta/broscuțe `0.05`=20/colet, covor `1`=1/buc); **fără densitate → contribuie 0 = se
COMBINĂ** (stația primește 1 colet baseline via `max(1,·)`), NU 1 colet/bucată — ca să nu se umfle coletele pe
articolele mici (regula owner 14-aug-2026, înlocuiește qty-driven-ul de pe 13-aug). Voluminos (`box>1`) → +colete.
