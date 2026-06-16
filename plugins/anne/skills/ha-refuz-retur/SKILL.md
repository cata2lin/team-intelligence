---
name: ha-refuz-retur
description: Calculeaza rata de refuz per produs HA — colete trimise care s-au intors (refuzate la usa sau retur fizic). Afiseaza top produse sortate dupa refuz%, cu numarul de livrate, intorse si anulate. Foloseste cand vrei o privire rapida in terminal asupra ratei de refuz HA.
---

# ha-refuz-retur

> Autor: **Anne**. Disponibil pentru toata echipa prin plugin-ul `anne`.

Calculeaza pentru fiecare produs HA (SKU `HA-XXXX`) rata de refuz = colete care s-au intors / colete trimise.

## Definitie

| Metric | Formula | Ce include |
|--------|---------|------------|
| Refuz% | `intorse / (livrate + intorse) × 100` | Orice `status_category = 'Refuzata'`: refuz la usa + retur fizic dupa primire |

**Numitor = colete trimise efectiv** (Livrate + Intorse). Comenzile Anulate sunt excluse — nu au plecat niciodata.

Comenzile `In curs de livrare` sunt excluse automat (nu au status final).

## Rulare

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/ha_refuz_retur.py"
```

### Optiuni

```bash
# Top 10, minim 50 comenzi trimise
uv run ha_refuz_retur.py --top 10 --min-orders 50

# Toate produsele (inclusiv bundle-uri), salvat in JSON
uv run ha_refuz_retur.py --bundles --save output.json

# Default: top 20, min 30 comenzi
uv run ha_refuz_retur.py
```

| Flag | Default | Descriere |
|------|---------|-----------|
| `--top N` | 20 | Numarul de produse afisate |
| `--min-orders N` | 30 | Minim comenzi trimise (livrate + intorse) |
| `--bundles` | off | Include SKU-uri compuse (cu `;`) |
| `--save PATH` | — | Salveaza JSON complet |

## Configurare initiala (o singura data)

```bash
kb.py secret-set PROFIT_SSH_HOST 84.46.242.181
kb.py secret-set PROFIT_SSH_USER root
kb.py secret-set PROFIT_SSH_PASS <parola>
```

## Interpretare

- **Refuz% > 35%** — problema severa. Verifica pretul, pozele, descrierea.
- **Refuz% 20–35%** — zona de atentie, monitorizeaza.
- **Refuz% < 15%** — produs sanatos, candidat de scalat.

Cauze frecvente de refuz mare:
1. Pret perceput ca prea mare la livrare (COD shock)
2. Descriere/poze care supraestimeaza produsul
3. Timp de livrare prea lung — clientul uita comanda

## Scripturi conexe

Pentru analiza temporala (evolutie in timp) si export Excel/HTML, vezi skill-ul `anne:ha-refuz-trend`.
