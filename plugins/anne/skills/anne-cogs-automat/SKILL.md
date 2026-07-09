---
name: anne-cogs-automat
description: Adaugare COGS automat pentru produse HA pe cele 4 magazine deals (ofertelezilei, reduceribune, casaofertelor/bonhaus, magdeal). Cauta TOM Real COGS + TOM Shipping Cost in spreadsheet-ul TOM, aplica formula (cogs+ship) x USD x 1.10 x 1.21 si seteaza costul pe Shopify. Foloseste cand vrei sa setezi sau actualizezi COGS pe produse HA, cand adaugi produse noi in catalog, cand vrei sa stii ce produse HA nu au COGS setat. Triggere: "seteaza cogs la HA", "adauga cost la produse", "lipseste cogs", "pune cogs automat", "calculeaza costul HA", "ce produse HA nu au cogs", "update cogs deals".
argument-hint: "--skus HA-0001 HA-0002 ... [--stores OFER RED BON MAG] [--apply] [--scan] [--usd 4.55]"
---

# ha-cogs-update — Adaugare COGS automat HA

> Autor: **Anne**. Disponibil pentru toata echipa prin plugin-ul `anne`.

Seteaza **Cost per item** (COGS) pe cele 4 magazine deals HA in Shopify, pornind de la
valorile TOM Real COGS + TOM Shipping Cost din spreadsheet-ul echipei.

**Formula:** `(TOM Real COGS $ + TOM Shipping Cost $) × USD × 1.10 × 1.21`
- `× USD` — conversie USD → RON (default 4.55, configurabil cu `--usd`)
- `× 1.10` — adauga 10% marja
- `× 1.21` — adauga TVA 21%

**Magazine:** OFER (ofertelezilei) · RED (audusp-rf/reduceribune) · BON (bonhaus/casaofertelor) · MAG (covoareauto-ro/magdeal)

---

## Rulare

```bash
# Cauta + calculeaza + arata ce s-ar seta (DRY RUN — sigur)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ha_cogs_update.py" --skus HA-0001 HA-0002 HA-0003

# Aplica efectiv pe Shopify
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ha_cogs_update.py" --skus HA-0001 HA-0002 --apply

# Gaseste toate produsele HA active FARA COGS (fara tag test)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ha_cogs_update.py" --scan

# Scan + aplica direct pe ce gaseste in TOM
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ha_cogs_update.py" --scan --apply

# Schimba cursul USD (default 4.55)
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ha_cogs_update.py" --skus HA-0001 --usd 4.60 --apply
```

## Optiuni

| Flag | Default | Descriere |
|------|---------|-----------|
| `--skus SKU [SKU ...]` | — | Lista de SKU-uri de procesat |
| `--stores STORE [...]` | toate 4 | Magazine: OFER RED BON MAG (ex: --stores MAG BON) |
| `--apply` | off | Aplica efectiv pe Shopify (fara = dry run) |
| `--scan` | off | Gaseste toate HA-urile fara COGS inainte de a procesa |
| `--usd FLOAT` | 4.55 | Cursul USD → RON folosit in formula |
| `--sheet ID` | ID TOM default | Spreadsheet ID alternativ |

## Configurare (o singura data)

Credentialele vin din KB:
```bash
# Magazinele deals (deja configurate in SHOPIFY_STORES_CSV)
kb.py secret-get SHOPIFY_STORES_CSV

# Spreadsheet TOM (deja configurat in TOM_SPREADSHEET_ID)
kb.py secret-get TOM_SPREADSHEET_ID
```

Google Sheets: OAuth Desktop la `~/.config/gcp/sheets-token.json` (acelasi token ca `core:export-to-google-sheet`).

## Output (dry run)

```
[DRY RUN] 3 SKU-uri de procesat

Cautare in TOM spreadsheet...
  HA-0001: TOM COGS $2.50 + SHIP $0.10 → 18.17 lei  [WINNER_WORK]
  HA-0002: TOM COGS $1.00 + SHIP $0.05 → 7.57 lei   [TO BE VERIFIED_WORK]
  HA-0999: negasit in TOM

Rezultat dry run:
  HA-0001: 18.17 lei | OFER: ar seta | RED: ar seta | BON: ar seta | MAG: ar seta
  HA-0002: 7.57 lei  | OFER: ar seta | RED: ar seta | BON: ar seta | MAG: ar seta
  HA-0999: negasit in TOM — skip

Ruleaza cu --apply ca sa aplici.
```

## Spreadsheet TOM

ID: `10eSCKItlCHMl8S5A2YGjBZBZwRe506HH0ETpgR7BV7A`

Cauta in **toate tab-urile** (nu doar primul). Coloane detectate automat dupa header:
- SKU → coloana care contine "sku"
- COGS → coloana care contine "real" + "cogs"
- SHIP → coloana care contine "shipping" + "cost"

Valorile pot fi `$1,30` sau `1.30` — parseaza ambele formate.

## Capcane

1. **Valori cu virgula** (`$1,30`) — scriptul normalizeaza automat la `.`
2. **SKU-uri cu sufix** (HA-0189-2, HA-1203-black) — pot lipsi din TOM; userul decide manual
3. **NOT FOUND pe OFER/RED/BON** — produsul exista doar pe MAG (nu e listat pe celelalte magazine)
4. **Tag test** — la `--scan`, produsele cu tag `test` pe Magdeal sunt excluse automat
5. **Cursul USD** — actualizeaza `--usd` cand cursul se schimba semnificativ (>0.10 lei)
