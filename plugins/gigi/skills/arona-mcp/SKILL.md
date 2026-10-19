---
name: gigi:arona-mcp
description: "Harta MCP-urilor ARONA + REGULA de a rula prin MCP (mai puțini tokeni) + cum EXTINZI un MCP când apare o capabilitate nouă. Use when: 'rulează prin MCP', 'ce tool MCP folosesc', 'adaugă tool la MCP', 'extinde MCP-ul', 'fac un tool nou de Google Ads/CS/profit', 'arona-ads/arona-fulfillment/arona-profit'. Triggers: mcp, tool nou, extindere mcp, adaugă la mcp, rulează eficient."
category: dev-tools
version: 1.0.0
---

# MCP-urile ARONA — rulează prin ele, extinde-le când apare ceva nou

Avem servere MCP care înfășoară CLI-ul testat al echipei în **tool-uri tipate**. Rulează operațiile prin ELE,
nu prin `uv run <script> --flags` ad-hoc.

## ⚡ REGULA #1 — preferă tool-ul MCP, nu CLI-ul ad-hoc
Când există un tool MCP pentru ce vrei, **folosește-l**. E mai eficient și **consumă mult mai puțini tokeni**:
schema e deja încărcată (nu mai citești docstring-uri/`--help`), parametrii sunt validați, nu mai reconstruiești
flag-uri, iar output-ul e curat. Cazi pe CLI (`uv run …`) DOAR pt ce n-are încă tool MCP (și atunci → vezi Regula #2).

## 🗺️ Harta serverelor (tool-uri = ce chemi direct)
| Server (user-scope, „✔ Connected") | Fișier | Tool-uri |
|---|---|---|
| **arona-ads** (paid media + storefront) | `gigi/skills/google-ads-mcc/mcp_server.py` | `gads_accounts`, `gads_query`, `gads_portfolio`, `gads_profit_verdict`, `gads_ngram`, `gads_search_terms`, `gads_negative_cleaner`, `gads_change_history`, `weekly_insights`, `spend_pacing`, `merchant_feed_health`, `merchant_performance`, `gads_set_budget/set_tcpa/set_status/add_negatives/add_keywords` (gated), `shopify_stores`, `shopify_graphql`, `shopify_feed_gaps`, `meta_report/list/set_budget`, `tiktok_report/list/set_budget` |
| **arona-fulfillment** (CS + livrare) | `gigi/skills/xconnector/mcp_server.py` | `cs_customer`, `cs_wismo`, `cs_conversation`, `xc_links`, `xc_summary`, `xc_address_issues`, `xc_not_downloaded`, `xc_order_cancel`, `xc_awb_make`, `xc_awb_void`, `xc_awb_regen`, `xc_inv_make` (acțiunile = dry-run) |
| **arona-profit** (P&L + livrabilitate) | `gigi/skills/multi-brand-pnl/mcp_server.py` | `pnl`, `pnl_today`, `fulfillment`, `breakeven` |
| **arona-catalog** (produse + inventar) | `gigi/skills/product-sales/mcp_server.py` | `product_sales`, `stock_alerts`, `returns_rma`, `reviews` |
| **arona-stock** (stock-sync + depozite + rafturi) | `gigi/skills/arona-stock/mcp_server.py` | `stoc_produs`, `stoc_statie`, `stoc_erori`, `magazin_ok`, `stoc_blocat`, `ponderi_stoc` (gated), `stoc_bucket_vs_shopify`, `reorder_plan`, `receptie_container` (gated), `mutare_stoc` (gated) |
| **arona-social** (social organic) | `gigi/skills/social-post/mcp_server.py` | `social_listen`, `social_post_list`, `social_post` (gated), `competitor_ads` |
| **arona-cs-inbox** (inbox Richpanel) | `gigi/skills/cs-draft-reply/mcp_server.py` | `cs_draft` (gated), `richpanel_triage` (gated), `richpanel_janitor` (gated), `cs_sentiment`, `cs_sla` |
| **arona-tom** (WMS / aprovizionare) | `gigi/skills/tom/mcp_server.py` | `tom_pos`, `tom_po_get`, `tom_shipments`, `tom_ghost`, `tom_events`, `tom_product` |
| **arona-studio** (creative/content) | `gigi/skills/image-gen/mcp_server.py` | `image_gen` (⚠️ generează, cost), `ai_scrub`, `youtube_channel` |
| **arona-orderhub** (Order Hub = SURSA DE ADEVĂR pt comenzi/statusuri — NU AWBprint) | `gigi/skills/order-hub/mcp_server.py` | `oh_comanda`, `oh_actiuni`, `oh_operatie`, `oh_rata_livrare`, `oh_proba` (nu scrie), `oh_executa` (gated: confirmare din probă + OK-ul omului) |
| **arona-marketplace** (eMAG RO/BG/HU · Trendyol RO/BG/GR · Okazii · Pepita — prin Order Hub) | `gigi/skills/arona-marketplace/mcp_server.py` | `mkt_conexiuni`, `mkt_comenzi`, `mkt_listare`, `trendyol_produse`, `trendyol_awb_list`, `trendyol_split` (dry-run), `emag_tracker`, `emag_concurenta`, `okazii_feed`, `emag_pret_proba`, `mkt_pret_tinta`, `emag_oferte`/`mkt_leaga_oferte`/`emag_listeaza`/`okazii_adauga` (PROBĂ fără apply), `trendyol_pret` (preview + apply + confirm, regula profit 20%) |
| **arona-kb** (baza de cunoștințe SharedClaude — READ ONLY, fără secrete) | `gigi/skills/arona-kb/mcp_server.py` | `kb_utilizare_skilluri`, `kb_comparatie`, `kb_goluri`, `kb_activitate`, `kb_resurse` |
| **arona-depozit** (depozit: print pe stații din OH, rafturi, culegere, scanări, utilizatorii scanerului) | `gigi/skills/arona-depozit/mcp_server.py` | `print_coada`, `print_istoric`, `print_reset` (gated: parola de reset de la om), `raft_continut`, `unde_e_produsul`, `rafturi_statie`, `produs_istoric`, `picking_live`, `picking_raport`, `picking_comanda`, `lot_continut`, `picking_neculese`, `scanari_anomalii`, `lipsa_marfa`, `scanner_utilizatori`, `scanner_aproba`/`scanner_aloca_statie`/`scanner_acces` (gated) |
> **SEO/analytics/ClickUp/Richpanel/postgres = MCP-uri OFICIALE deja existente** (GA4, Search Console, dataforseo, clickup, richpanel, postgres-*) — nu le dublăm.
> **12 servere arona · ~108 tool-uri.** Domenii rămase eventual: fiscal (SmartBill e deja în arona-fulfillment `xc_inv_make`; e-Transport = VPS), content-generation (articole = flux interactiv, nu MCP-natural).

**Convenții comune:** read-only by default; **mutațiile Google Ads/Meta/TikTok = DRY-RUN dacă nu pui `apply=true`**;
**mutațiile Shopify cer `confirm_mutation=true`** (Shopify n-are dry-run); acțiunile pe comandă și AWB (`xc_order_cancel`, `xc_awb_void`, `xc_awb_regen`, `xc_awb_make`) întreabă întâi Order Hub, care hotărăște pe orice comandă pe care o cunoaște.
Credențiale din KB (self-provisioning la pornire, nu se printează). Toate = wrapper SUBȚIRE peste scripturile testate.

## 🔧 REGULA #2 — când apare o capabilitate nouă, ADAUG-O la MCP-ul potrivit
Ai făcut un tool nou (script) care se potrivește unui domeniu → **nu-l lăsa doar CLI**, adaugă-l ca tool în serverul potrivit:
1. **Alege serverul** pe domeniu: ads/merchant/shopify/meta/tiktok → `arona-ads`; CS/xConnector/AWB → `arona-fulfillment`;
   P&L/livrabilitate/breakeven → `arona-profit`; comenzi/statusuri/acțiuni Order Hub → `arona-orderhub`; print/rafturi/culegere/scaner → `arona-depozit`. (Domeniu nou fără server → creează unul nou pe același tipar.)
2. **Adaugă un `@mcp.tool()`** în `mcp_server.py`-ul acelui skill — wrapper subțire (subprocess pe scriptul testat via `_run`/`_env`):
   ```python
   @mcp.tool()
   def nume_tool(param: str, apply: bool = False) -> str:
       \"\"\"Ce face, pe scurt. DRY-RUN dacă apply=false.\"\"\"
       return _run(SCRIPT, ["subcmd", param] + (["--apply"] if apply else []))
   ```
   Reguli: read-only implicit; scrierile gated cu `apply`/`confirm`; docstring clar (devine descrierea tool-ului);
   `_env()` scoate `VIRTUAL_ENV` (uv nested); NU duplica logica — cheamă scriptul existent.
3. **Testează**: `py_compile` + smoke (listează tool-urile) + o chemare live pe un read.
4. **Se re-încarcă singur** (înregistrarea e pe calea fișierului) — la următoarea sesiune tool-ul e disponibil. Server NOU → `claude mcp add --scope user <nume> -- uv run <abs path>/mcp_server.py`.
5. **Publică skill-ul** în SB (`second-brain-skill`). ⚠️ **max 40 fișiere/skill** — exclude one-off-urile la publish. ⚠️ Re-verifică persistența (sync de plugin poate reverti — vezi [[marketplace-plugin-edits-can-revert]]).

## Note
- MCP-urile rulează LOCAL (stdio); pentru cron/headless pot lipsi — atunci cazi pe CLI-ul de dedesubt.
- Detalii per-domeniu: `gigi:google-ads-mcc` (§0), `gigi:xconnector`, `gigi:multi-brand-pnl`, `gigi:cs-360`, `gigi:merchant-center-feed`.
- Vezi [[arona-ads-mcp-server]] (istoricul + capcanele: uv-nested, limita 40 fișiere).
