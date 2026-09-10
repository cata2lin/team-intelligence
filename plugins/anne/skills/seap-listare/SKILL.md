---
name: seap-listare
description: Fisa de catalog SEAP / SICAP (cumparari directe, institutii publice) pentru orice produs ARONA, gata de copiat in "Adauga reper de catalog" — ia produsul din Shopify dupa SKU, transportul REAL DPD din AWBprint si calculeaza pretul unitar FARA TVA cu transport inclus (pret site/1.21 + transport/1.21, rotunjit in sus), plus descriere curata, GTIN, link, conditii de livrare/plata cu contul de Trezorerie. Use pentru "listeaza in SEAP", "fisa SICAP", "pret fara TVA cu transport pentru SEAP", "cat pun in catalogul SEAP", "reper de catalog", "cumparare directa".
---

# seap-listare

> Autor: **Anne**. Disponibil pentru toata echipa prin plugin-ul `anne`.

ARONA SRL (CUI 37247302, J51/151/2017) vinde institutiilor publice prin catalogul SEAP/SICAP
(cumparari directe). Skill-ul scoate **fisa completa a unui reper de catalog** dintr-un SKU,
ca sa o copiezi camp cu camp in formularul SICAP. Nu scrie in SICAP (necesita certificat digital
+ cont personal) — omul apasa "Adauga reper".

## Rulare

```bash
uv run "${CLAUDE_PLUGIN_ROOT}/scripts/seap_fisa.py" --sku GD-BR-1313741 --cpv "38434560-9 Analizoare chimice"
```

| Flag | Default | Descriere |
|---|---|---|
| `--sku` | (obligatoriu) | SKU-ul din Shopify |
| `--prefix` | `GRAN` | magazinul (prefix din `SHOPIFY_STORES_CSV`: GRAN, MAG, OFER, EST, GT…) |
| `--price` | pret Shopify | pret site **cu TVA**, daca vrei sa-l suprascrii |
| `--transport` | median real AWBprint | transport **cu TVA** per colet; fallback 13,01 lei daca nu-s comenzi |
| `--cpv` | gol | codul CPV (vezi lista de mai jos) |
| `--garantie` | 24 | luni de garantie scrise in descriere |
| `--round` | `leu` | rotunjire in sus la leu intreg sau la ban |
| `--out fisa.txt` | — | salveaza fisa |
| `--img` | off | descarca poza principala ca JPG sub 1 MB (limita SICAP) |
| `--json` | off | output masina |

## Cum se calculeaza pretul (regula Annei, sep-2026)

```
pret unitar SICAP (fara TVA) = pret site cu TVA / 1.21  +  transport DPD cu TVA / 1.21
```

- Transportul = **median `orders.transport_cost`** din AWBprint pe comenzile DPD livrate cu SKU-ul
  respectiv (ultimele 120 zile, 1 colet). Colet standard DPD ≈ 13,01 lei cu TVA = **10,75 fara TVA**.
- Pretul in SICAP e **per bucata**, deci transportul se multiplica cu cantitatea comandata. SICAP nu
  are transport fix per comanda. Anne a ales varianta acoperita integral (+10,75/buc) — la 1 bucata
  nu pierzi nimic, la mai multe esti pe plus. Alternativa (+5/buc, compromis) NU e cea folosita.
- `nr_cutii` (metafield custom) = fractiune de cutie master → scriptul arata bucati/cutie (0.05 = 20 buc).
- Exemplu: GD-BR-1313741 tester apa 7 in 1: 139,90/1,21 = 115,62 + 10,75 = 126,37 → **127,00**.

## Campurile SICAP si ce pui

| Camp SICAP | Sursa |
|---|---|
| Numar referinta | SKU |
| Denumire | titlul Shopify (scurteaza-l manual daca e prea lung; ce cauta institutia in primele 60 caractere) |
| Cod GTIN | barcode-ul variantei Shopify (gol daca nu e) |
| Site de prezentare | URL-ul produsului pe magazin |
| Cod CPV | din lista de mai jos sau cauta cu lupa in SICAP |
| Descriere | descrierea Shopify curatata de HTML + "Produs nou, sigilat. Garantie N luni." Scoate formulari vagi ("baterii inclus sau nu") — institutia vrea raspuns clar |
| Pret unitar RON (fara TVA) | calculul de mai sus |
| Unitate de masura | bucata |
| Stare stoc | In stoc (scriptul avertizeaza daca produsul e inactiv) |
| Conditii de livrare | DPD 24-48h, Romania, transport inclus |
| Conditii de plata | OP in **Trezorerie RO19TREZ2015069XXX009130** (cont 50.69), 30 zile de la factura, factura la livrare |
| Imagini | max 1024 KB / imagine → `--img` |

Fiecare reper publicat consuma o **pozitie de catalog** preplatita (dashboard SICAP → "Pozitii de catalog";
se cumpara din "Plateste"). Pentru multe produse: "Descarca template - import catalog" + upload Excel.

## Coduri CPV uzuale pentru produsele noastre

| Produs | CPV |
|---|---|
| tester apa / pH / TDS | 38434560-9 Analizoare chimice (sau 38410000-2 Instrumente de masurare) |
| covorase auto | 34300000-0 Piese si accesorii pentru vehicule (34390000-7 Accesorii tractoare/34310000-3 motoare = NU) |
| covoare / mochete | 39531000-3 Covoare |
| pijamale / textile | 18300000-2 Articole de imbracaminte |
| genti / rucsacuri | 18930000-7 Saci si pungi / 18931000-4 Genti de voiaj |
| parfumuri | 33711000-4 Parfumuri si produse de toaleta |
| electrocasnice mici | 39710000-2 Aparate electrocasnice |
| unelte gradina | 44511000-5 Unelte de mana / 16160000-4 Diverse echipamente de gradinarit |
| mobilier gradina | 39142000-9 Mobilier de gradina |
| jucarii | 37520000-9 Jucarii |

Cauta in SICAP dupa denumire cand nu esti sigur; codul se poate schimba ulterior din "Modifica".

## Contul de Trezorerie (fix, sep-2026)

- **50.69 incasari institutii publice**: `RO19TREZ2015069XXX009130` — asta se pune pe facturi si in SICAP.
- 50.70 subventii: `RO10TREZ2015070XXX007011`. 50.86 = sume indisponibilizate, NU se foloseste.
- Contact Trezoreria Calarasi: manuela.gheorghe.cl@anaf.ro.

## Secrete necesare (din KB, nu se printeaza)

`SHOPIFY_STORES_CSV`, `DATABASE_URL_AWBPRINT`.
