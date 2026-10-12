# Scripturile din „CPA și financiar”

Copii ale celor două proiecte Apps Script legate de sheet-ul „CPA și financiar”. Sunt aici ca o schimbare să aibă istoric și să se poată compara cu ce rulează.

| Fișier | Proiect | Scrie în |
|---|---|---|
| `raport-azi.gs` | „Raport azi” (`1ttVcW2s…`), `adaugaRandZilnicAzi` | tab-ul „Raport azi” (ziua curentă) |
| `daily-gross-profit.gs` | „Daily Gross profit” (`11sZUIg…`), `adaugaRandZilnic2` | tab-ul „Raport Zilnic 2” (ieri) |

Fiecare script are **propria** listă `BRANDS`. Un brand se adaugă sau se scoate **în ambele**.

## Înainte de orice push
Pornește de la codul care rulează **acum**, nu de la copia de aici:

```bash
uv run scripts/gas_deploy.py get --script-id <ID> --out acum.json
```

Pe 11 oct, un push făcut dintr-o copie mai veche a readus în raport patru branduri scoase cu câteva ore înainte. Dacă diferă de ce e aici, pune întâi în git ce rulează, apoi modifici.

Un brand scos rămâne în tab-urile „Mapping” și „Curs valutar”. Dacă revine, e de ajuns să fie adăugat înapoi în `BRANDS`.
