---
name: ha-refuz-trend
description: Analizeaza evolutia ratei de refuz HA pe ferestre de timp (ultimele 7 zile, 8-30 zile, 31-90 zile, 91+ zile) si genereaza raport Excel sau HTML in browser. Foloseste cand vrei sa vezi daca rata de refuz s-a imbunatatit sau inrautatit in timp, sa compari perioade, sau sa prezinti analiza intr-un spreadsheet sau browser.
argument-hint: "excel | html | trend [--min-orders N] [--top N]"
---

# ha-refuz-trend

> Autor: **Anne**. Disponibil pentru toata echipa prin plugin-ul `anne`.

Analizeaza evolutia ratei de refuz per produs HA pe 4 ferestre de timp, detecteaza produsele cu imbunatatiri sau inrautatiri semnificative (≥5pp), si exporta rezultatele in Excel sau HTML.

## Formula

**Refuz% = Intorse / (Livrate + Intorse) × 100**

Incluse in "Intorse": orice `status_category = 'Refuzata'` — refuz la usa + retur fizic. Comenzile `Anulate` si `In curs de livrare` sunt excluse.

## Ferestre de timp

| Perioada | Interval |
|----------|----------|
| Ultimele 7 zile | `now - 7 zile` |
| 8–30 zile in urma | `now - 30 zile` → `now - 7 zile` |
| 31–90 zile in urma | `now - 90 zile` → `now - 30 zile` |
| 91+ zile in urma | tot ce e mai vechi |

## Scripturi

### 1. Raport HTML in browser (recomandat)

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ha_refuz_html.py"
```

Deschide automat browserul cu:
- Tabel agregat pe perioade (cu culori verde→rosu dupa refuz%)
- Tabel per produs cu trend per SKU
- Carduri cu produse imbunatatite / inrautatite (≥5pp)

### 2. Export Excel

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ha_refuz_excel.py" --out raport.xlsx
```

Genereaza un `.xlsx` cu 3 sheet-uri:
- **Rezumat agregat** — evolutie globala pe perioade
- **Per produs** — toate SKU-urile cu color scale pe Refuz% si coloana Trend
- **Schimbari semnificative** — produse cu modificare ≥5pp (31-90 zile vs 8-30 zile)

Optiuni Excel:

| Flag | Default | Descriere |
|------|---------|-----------|
| `--out PATH` | `ha_refuz_retur_trend.xlsx` | Fisier de output |
| `--min-orders N` | 30 | Minim comenzi totale pentru a include un produs |

### 3. Output terminal

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ha_refuz_trend.py" [--top N] [--min-orders N] [--sort refuz]
```

Afiseaza in terminal tabelul pe perioade + sectiunea de schimbari semnificative.

## Configurare (o singura data)

```bash
kb.py secret-set PROFIT_SSH_HOST 84.46.242.181
kb.py secret-set PROFIT_SSH_USER root
kb.py secret-set PROFIT_SSH_PASS <parola>
```

## Interpretare trend

- **vv -Xpp** (verde) — rata a scazut: imbunatatire
- **^^ +Xpp** (rosu) — rata a crescut: inrautatire
- **-> stabil** — variatie < 0.5pp

**Atentie:** Perioada "Ultimele 7 zile" are volum mic — nu trage concluzii din ea. Comparatia cea mai fiabila este **31-90 zile vs 8-30 zile**.
