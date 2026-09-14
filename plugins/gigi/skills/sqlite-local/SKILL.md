---
name: sqlite-local
description: Inventar + acces READ-ONLY la bazele SQLite locale din Scripturi/data/ (richpanel_tickets, marketing, etransport, product_analytics, daily_perf, test_products…). Ce ține fiecare, cum le interoghezi în siguranță cu sqlite3 CLI / helper-ul dbq.py (deschide read-only, fallback immutable pe WAL, refuză non-SELECT, sare peste bazele cu secrete), și capcanele (profit NU din SQL brut; profitability.db local e stale — cea vie e pe VPS via MCP sqlite-profitability). Folosește pentru „ce baze SQLite avem local", „interoghează richpanel_tickets / marketing / etransport", „istoricul tichetelor din SQLite", „spend FB/TikTok raw", „coduri tarifare/HS e-Transport", „sqlite3 pe bazele noastre", „backup/schema unei baze .db".
category: data-analytics
version: 1.0.0
---

# sqlite-local — bazele SQLite locale (read-only)

În `Scripturi/data/` sunt mai multe baze SQLite construite de tool-urile echipei. Nu-s în niciun
MCP (doar `profitability.db` are unul). Skill-ul le inventariază și le interoghează **read-only** cu
un helper (`dbq.py`) sau direct `sqlite3`.

## When to use
- „Ce baze SQLite avem local?" / „ce ține baza X?"
- „Interoghează istoricul tichetelor" (richpanel), „spend FB/TikTok raw" (marketing), „coduri HS /
  tarifare" (etransport), „vânzări/COGS analytics" (product_analytics).
- NU pt: profit canonic (→ engine `profit_by_sku`/`profit_core`); `profitability.db` VIE (→ MCP
  `sqlite-profitability`, pe VPS); Richpanel LIVE (→ `gigi:cs-tickets`); construirea exportului
  Richpanel (→ `gigi:richpanel-export`).

## Bazele (local, `data/`) — inventar
| Bază | Mărime | Ce ține (tabele-cheie) |
|---|---|---|
| **richpanel_tickets.db** | 384M · WAL | `tickets` (223k), `customer_identity` (223k), `pull_log`. Construită de `gigi:richpanel-export`. |
| **marketing.db** | 22M | `marketing_facebook_raw` (74k), `marketing_tiktok_raw` (11k), `product_group_mappings` (1,6k), `marketing_mapping_rules`, `marketing_sync_logs`. Spend per-ad + reguli de mapare (feed WMS). |
| **etransport.db** | 14M · WAL | `hs_codes_catalog` (32k), `tariff_codes` (9,7k), `etransport_product_history`, `etransport_documents`, `carrier_history`. Subsistemul e-Transport ANAF/SmartBill. |
| **product_analytics.db** | 6M | `analytics_sales` (50k), `delivered_not_paid` (139), `cogs_real`/`july_cogs_real`, `incasari_verification`. (multe tabele goale) |
| **daily_perf.db** · **test_products.db** | mici | daily_perf (gol); tp_* (doar settings) |
| **profitability.db** | 128K · **GOALĂ** | ⚠️ copie **stale/goală** local. Cea VIE (333M) e pe **VPS**, prin MCP `sqlite-profitability`. |
| **shopify_tokens.db** | 12K | 🔒 **SECRETE** (tokenuri OAuth) — NU interoga/dumpa. |
| orders/shopify_orders/trendyol_orders/users.db | 0B | goale (placeholdere) |

## Steps
```bash
cd plugins/gigi/skills/sqlite-local/scripts
uv run dbq.py list                              # bazele + mărime + nr tabele
uv run dbq.py tables --db richpanel_tickets     # tabelele + nr rânduri
uv run dbq.py query  --db marketing --sql "select count(*) from marketing_tiktok_raw" [--limit N]
```
`--db` = nume scurt (`marketing`) sau cale. `--dir` schimbă folderul (implicit `./data` sau
`~/Downloads/Scripturi/data`). `query` acceptă DOAR SELECT/WITH/PRAGMA/EXPLAIN.

Direct cu `sqlite3` (vine cu macOS):
```bash
sqlite3 -readonly data/marketing.db ".mode column" "select * from marketing_mapping_rules limit 5"
# WAL (richpanel/etransport) refuză -readonly → immutable=1:
sqlite3 "file:data/richpanel_tickets.db?immutable=1" "select count(*) from tickets"
```

## Notes
- **Deschide MEREU read-only.** `-readonly` / `?mode=ro`. Bazele **WAL** (richpanel_tickets,
  etransport) dau `unable to open (14)` la read-only clasic → folosește **`?immutable=1`** (sigur
  fiindcă-s copii locale statice; NU pe o bază care se scrie concurent — ai citi inconsistent).
- **🔒 `shopify_tokens.db` = secrete** — `dbq.py` refuză interogarea; nici manual nu o dumpa.
- ⚠️ **Profit NU din SQL brut.** `profitability.db` (și local, și pe VPS) trece prin engine
  (`profit_core`: TVA, monedă, doar-livrate). SQL direct = footgun. Pt profit → `profit_by_sku` /
  `gigi:multi-brand-pnl`. Vezi [[profit-data-sources-truth]].
- **`profitability.db` local ≈ goală/stale** — cea autoritativă e pe VPS `/root/Scripturi/data/`,
  citită prin MCP **`sqlite-profitability`** (read-only, SSH) + `ro_sqlite_mcp.py`. Vezi [[team-mcp-servers]].
- **Mentenanță utilă** cu `sqlite3`: `.backup out.db` (backup consistent la cald — ca `backup_profitdb.py`),
  `PRAGMA integrity_check`, `VACUUM`, `.dump`, `.mode csv`+`.output` (export). NU rula scriere/VACUUM pe baze vii.
- Related: [[gigi:richpanel-export]], [[team-mcp-servers]], [[profit-data-sources-truth]], [[no-db-reset]].
