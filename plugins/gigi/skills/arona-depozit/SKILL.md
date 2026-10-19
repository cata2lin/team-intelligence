---
name: gigi:arona-depozit
description: "MCP-ul arona-depozit — operațiile de DEPOZIT prin tool-uri tipate: coada de print pe stații din Order Hub (Printing), istoricul printurilor și RESETUL unui print eșuat (gated, parolă de reset de la om), rafturile (ce e pe raft, unde e produsul, rafturi goale, marfă fără raft, jurnalul unui SKU), culegerea (live, productivitate pe om, comandă culeasă?, loturi, etichete printate și NECULESE = risc de colet-fantomă, „NU GĂSESC”), anomalii de scanare și utilizatorii aplicației de scanare (aprobare, stații, drepturi — gated). Use when: „câte etichete am de printat pe Uzina 2”, „resetează printul”, „pune eticheta înapoi la printat”, „ce e pe raftul A1”, „unde e HA-0364”, „rafturi goale la Bartolomeu”, „cine a cules cât azi”, „productivitatea pe săptămână”, „s-a cules comanda X”, „ce n-a fost cules”, „nu găsește AWB-ul”, „aprobă contul de scaner”, „pune-l pe stația Parfumuri”, „dă-i drept de stoc”."
category: fulfillment-logistics
version: 1.0.0
---

# arona-depozit — depozitul prin MCP

Server: `plugins/gigi/skills/arona-depozit/mcp_server.py` (user-scope `arona-depozit`). Strat subțire peste
`depozit.py`, care cheamă rutele EXISTENTE și testate:

| Sursă | Cum | Pentru |
|---|---|---|
| **Order Hub** `/api/print/*` | acces local OH (`/standalone/login`, `ORDERHUB_LOCAL_PASSWORD` din KB), `scope=all` | coada pe stații, istoric, reset |
| **Aplicația de scanare** `scripts.arona.ro/api/*` | contul de serviciu awb-svc (`SCAN_SVC_USER/PASSWORD`) | rafturi, culegere, loturi, „NU GĂSESC”, utilizatori |
| **Bazele scanerului**, direct | `scripts/depozit_sql.py` trimis pe ssh, sqlite `mode=ro` | doar ce n-are endpoint: neculese, anomalii, jurnalul unui SKU |

Niciun tool nu citește AWBprint (vezi [[nu-folosi-awbprint-pentru-comenzi]]).

## Tool-uri
| Tool | Ce răspunde | Scrie? |
|---|---|---|
| `print_coada(statie, zile, facute)` | câte etichete are de printat fiecare stație, pe magazin și categorie (= ecranul Printing) | nu |
| `print_istoric(statie, cauta)` | printurile (id, oră UTC, stație, loturi, resetări); `cauta` = comandă/AWB; `statie="WhatsApp"` = loturile trimise de OH pe grup | nu |
| `print_reset(print_id \| awb, motiv, parola, apply)` | printul / eticheta înapoi în coadă (ruta OH, cu audit și loturile scanerului anulate) | **da, gated** |
| `scanner_utilizatori(statie, cauta, doar_neaprobati)` | conturile scanerului, stații, drepturi, conturi DUBLE | nu |
| `scanner_aproba(user_id, rol, apply)` | approved=1 + rol | **da, gated** |
| `scanner_aloca_statie(user_id, statii, apply)` | stațiile omului (validate pe cele reale; gol = toate) | **da, gated** |
| `scanner_acces(user_id, capabilitati, apply)` | `scanner: stock,locations`; păstrează restul permisiunilor | **da, gated** |
| `raft_continut(raft, statie)` | ce e pe raft + ultimele mișcări | nu |
| `unde_e_produsul(sku)` | pe ce rafturi/stații e un SKU + ultimele mișcări | nu |
| `rafturi_statie(statie)` | rafturi goale, buc pe categorii, marfă încă pe „Fără raft” | nu |
| `produs_istoric(sku, zile)` | jurnalul SKU-ului pe rafturi (pick/add/transfer/…; cu sinonime) | nu |
| `picking_live()` | culegerea de azi: pe om, pe stație, loturi neterminate | nu |
| `picking_raport(de_la, pana_la \| luna, statie, om)` | productivitate pe om/zi | nu |
| `picking_comanda(cod)` | s-a cules comanda/AWB-ul? cine, când, în ce lot | nu |
| `lot_continut(lot)` | comenzile unui lot de print | nu |
| `picking_neculese(ore, zile, statie)` | printate și NECULESE de peste N ore, încă vii și nepreluate de curier | nu |
| `scanari_anomalii(zile)` | coduri negăsite, AWB cules de 2 oameni, unpicked, culegeri fără comandă | nu |
| `lipsa_marfa(zile, sku)` | semnalările „NU GĂSESC” | nu |

## Reguli
- **Proba întâi, apoi OK-ul omului, apoi `apply=true`.** Fără `apply` nimic nu se scrie (testat în `test_depozit.py`).
- **`print_reset`**: parola e PAROLA DE RESET PRINT a organizației, pe care o dă omul (nu e în KB, nu o reține, nu o
  scrie în chat-ul altcuiva). Resetează doar etichete care NU au ieșit pe hârtie — altfel se printează de două ori.
  Un AWB reprintat între timp rămâne al printului nou (OH răspunde 409).
- **Printul propriu-zis** se face din Order Hub → Printing, nu de aici. `depozit:print-queue` (xConnector) e doar
  rezervă, iar descărcarea unei etichete în xConnector o scoate din coada TUTUROR stațiilor — nu o atinge.
- **Fluxul lot → stații → grupul WhatsApp „AWB” e în Order Hub.** MCP-ul nu construiește și nu trimite PDF-uri pe grup;
  starea lui se vede în `print_istoric(statie="WhatsApp")`. Pe grup: doar documentul ([[wa-grup-doar-documentul]]).
- **„Neculese”** vin din oglinda OH a scanerului (~10 min): înainte de orice acțiune pe comandă → `arona-orderhub oh_comanda`.
- Stocul în stock-sync / Shopify, pozițiile negative, recepția și mutările între stații: **`arona-stock`**
  (`stoc_produs`, `stoc_statie`, `receptie_container`, `mutare_stoc`). Nu le dublăm aici.
- Un raft pus pe 0 se propagă în stock-sync în ~5 min (zero_auto) — de aceea MCP-ul NU expune scrieri pe rafturi.

## Capcane măsurate (19-oct-2026)
- `picking_neculese`: prima variantă număra 107; 68 erau etichete ANULATE în OH (oh_awbs nu le mai are, dar lotul de
  pe scaner a rămas — ex. comandă anulată pe 13-oct cu AWB „void”). Filtrul corect: eticheta încă în `oh_awbs`,
  status gol/`processed`, comanda neanulată → 39, verificate pe fișa OH („Netrimisă (Alertă)”, „Shipment data received”).
- `lot.culese` din `picking-live` poate rămâne 0 deși comenzile s-au cules (alt lot cu aceleași etichete:
  „Tot printul” + loturile pe SKU). Pentru o comandă anume, `picking_comanda`.
- `/api/product-picks` citește `pick_items`, oprit în sep-2026 — istoricul viu e `shelf_events` (`produs_istoric`).
- Login-ul scanerului cere `email_verified=1`; aprobarea NU confirmă emailul. Conturi duble de signup = de obicei un
  cont neconfirmat abandonat + unul bun.
- Fiecare apel deschide o sesiune pe contul de serviciu (scanerul scrie un eveniment `login`); tokenurile se țin în
  memoria serverului MCP, nu pe disc.

## TODO (în afara MCP-ului; necesită modificări în aplicații)
- **Comasarea a două conturi de scaner**: nu există endpoint; istoricul (scan_events, shelf_events, pick_rest) stă pe
  `username`. Trebuie o rută de admin în `api/auth.py` care mută istoricul și șterge contul dublu.
- **Confirmarea emailului de către admin** (`email_verified=1`) — lipsește din `POST /api/auth/users/{id}/approve`.
- **Resetul unui LOT** (`/api/print/loturi/{lot_id}/reset`) cere id-ul intern al lotului, pe care `/api/print/logs`
  nu-l întoarce (doar codul LOT-…). Order Hub ar trebui să expună `id` în `loturi[]`.
- **`picking_lista` pe traseu de rafturi** pentru coada curentă: aplicația o face pe lot/val (wave), fără endpoint de
  citire per stație; de construit în scanner, nu re-implementat aici.

## Testare
```bash
uv run -q --with pytest --with paramiko python -m pytest -q test_depozit.py      # gărzile, fără rețea
uv run -q depozit.py print_coada '{"statie":"Uzina"}'                            # citire live din CLI
```
Related: `gigi:arona-mcp`, `gigi:order-hub`, `gigi:arona-stock`, `gigi:awb-scanner`, `depozit:print-queue`.
