---
name: data-slice
description: "Query/filter LARGE data files (CSV, JSON, Parquet, Excel) with SQL locally via DuckDB, so you feed the LLM only the ROWS/COLUMNS that matter instead of the whole file — huge token/credit saver on big exports. Reads the file directly (no import), returns a compact Markdown table (or CSV). Use whenever you need a number/subset out of a big CSV/Excel/JSON export, want to aggregate/group/filter before analysis, or just to see a file's schema + a preview cheaply. Triggers: 'filtreaza CSV-ul', 'ce e in fisierul asta de date', 'group by / suma pe', 'query this Excel/CSV/JSON', 'doar randurile cu', 'schema fisierului', 'top N din export', 'slice this data', 'aggregate before feeding'."
argument-hint: "<file> [--sql \"SELECT ... FROM t WHERE ...\"] [--limit 50] [--cols a,b] [--csv]"
---

# data-slice — fișiere de date mari → doar ce contează (SQL local)

Wrapper peste **DuckDB**: interoghează CSV / JSON / Parquet / Excel **direct** (fără import), cu SQL, și întoarce doar rândurile/coloanele relevante ca tabel Markdown. În loc să dai LLM-ului un CSV de 50k rânduri, dai rezultatul unei interogări. Tabelul se numește **`t`** în SQL.

```bash
uv run scripts/data_slice.py export.csv                                   # SCHEMA + primele randuri (recon ieftin)
uv run scripts/data_slice.py export.csv --sql "SELECT brand, SUM(profit) FROM t GROUP BY 1 ORDER BY 2 DESC"
uv run scripts/data_slice.py comenzi.xlsx --sql "SELECT * FROM t WHERE status='refuzat'" --limit 100
uv run scripts/data_slice.py data.json --cols "id,nume,total" --limit 30  # doar niste coloane
uv run scripts/data_slice.py mare.parquet --sql "SELECT COUNT(*), AVG(pret) FROM t"
uv run scripts/data_slice.py export.csv --sql "SELECT * FROM t WHERE ..." --csv   # iesire CSV in loc de Markdown
```

## Când îl folosești (regula de aur)
- **ÎNAINTE** de a „citi" un CSV/Excel/JSON mare → rulează o interogare (filtru/agregare/top-N) și dă LLM-ului DOAR rezultatul. Diferența pe fișiere de zeci de mii de rânduri e enormă.
- **Recon ieftin**: fără `--sql` → schema (coloane + tipuri) + un preview, ca să știi ce ai înainte să interoghezi.
- Agregări (SUM/GROUP BY/JOIN între fișiere), dedup, filtre pe dată/status — toate local, instant, zero tokeni până la rezultat.

## Note
- Dependență inline `duckdb`; `uv run`, fără setup. Citește CSV/JSON/Parquet nativ; Excel via extensia `excel` (auto-INSTALL/LOAD).
- `--limit` implicit 50 (ca să nu torni mii de rânduri); pune `--limit` mai mare sau include `LIMIT` în `--sql` pentru control total.
- Poți face JOIN între fișiere: dă un fișier ca `t`, apoi în `--sql` referă alt fișier cu `read_csv_auto('alt.csv')`.
- Companion: `gigi:markitdown` (docs), `gigi:core:query-postgres` (baze live). Acesta = fișiere de date locale.
