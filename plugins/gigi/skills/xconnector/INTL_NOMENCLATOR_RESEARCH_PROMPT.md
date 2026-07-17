# Deep Research Brief — National Address Nomenclators for Deterministic Address Correction (CZ / PL / BG)

> Paste this whole document into ChatGPT Deep Research **and** Gemini Deep Research (run it in both, compare).
> Written in English on purpose: CZ/PL/BG government open-data docs retrieve far better in English than in RO.

---

## 1. Who we are & what we're doing

We run cash-on-delivery (COD) e-commerce across Central/Eastern Europe. At checkout, customers type their **own** shipping address, and a large share are malformed: wrong or missing street, wrong postal code, a garbled/abbreviated city, or the **county/region written in the city field**. Before a courier waybill (AWB) can be printed, the address must be valid. Today a human agent fixes each one by hand.

We are replacing that with a **deterministic, offline address nomenclator + corrector, one per country** — a local PostgreSQL gazetteer of every real `(locality, street, house-number-range, postal-code)` plus a rules engine that **validates and auto-corrects** a messy address with **no paid geocoding API**. We only fall back to a paid geocoder (HERE) when the nomenclator genuinely can't decide. Goal: turn "address rejected → human CS" into "address auto-corrected → AWB printed automatically."

**We have already built and shipped this for Romania and Czechia.** We now need the same for **Poland** and **Bulgaria**, and a sanity-check on our Czech approach.

---

## 2. What we already built — the proven template

### 🇷🇴 Romania (RO) — LIVE
- Table `romania_addresses` (~55k street-level keys). Each row: county (*județ*), locality, street (with type: *strada / bulevardul / aleea / calea*…), postal code, and **the house-number range that postal code covers, including odd/even parity**.
- **Key property we discovered:** in big RO cities a single street carries **several** postal codes split by house-number ranges and parity (e.g. odd 1–27 → one code, even 2–30 → another). So we pick the postal code by **house number + parity**, never by street name alone. → RO is **ZIP-driven at street+number granularity**.
- Correction rules: (1) **never overwrite a real customer street** — if the customer's street exists in the nomenclator, keep it verbatim; (2) if the postal code is valid, derive/confirm county+locality from it (inverse lookup ZIP → owner); (3) fix systematic confusions — e.g. many customers write county **"Ilfov"** for **Bucharest** addresses; we detect and correct that **via the postal code**; (4) if street is missing/garbage AND the locality has exactly one street → fill it; if ambiguous → geocoder/CS.
- Result: the majority of malformed RO addresses auto-correct; only true unknowns escalate.

### 🇨🇿 Czechia (CZ) — LIVE
- Source: **RÚIAN** (ČÚZK). Monthly national CSV, licence **CC-BY 4.0** (commercial OK). URL pattern `https://vdp.cuzk.gov.cz/vymenny_format/csv/YYYYMMDD_OB_ADR_csv.zip` — cp1250 encoding, `;`-delimited, ~6250 per-municipality CSVs, ~3M address points. Fields: *obec* (municipality), district, *část obce*, *ulice* (street), *číslo popisné* + *číslo orientační* (two house numbers), **PSČ** (postal code), coordinates.
- We aggregated it to `cz_addresses` = 102,509 keys `(obec, district, cast_obce, ulice, psc)` + number range + count, ~2,677 distinct PSČ.
- **Key property we discovered:** CZ is **LOCALITY-DRIVEN — the inverse of RO.** ~74% of municipalities map to exactly **one** PSČ, so we derive `locality → PSČ`. A PSČ is **coarse** (covers many streets). So the corrector confirms `(locality + PSČ)` is a real deliverable pair, fixes a garbled city **from** its PSČ, and derives a missing PSČ from the locality. House number is required.
- Result: ~86% of previously-unresolvable CZ addresses now auto-correct.

**The critical lesson:** every country has a different **granularity model** (ZIP-driven vs locality-driven), and getting that model right is what makes the corrector safe. That is the #1 thing we need you to determine for PL and BG.

---

## 3. What we want you to research (the deliverable)

For **🇵🇱 Poland (PL)** and **🇧🇬 Bulgaria (BG)** — and a sanity-check on **🇨🇿 Czechia** — produce, **per country**:

1. **Best authoritative, commercially-usable, street-level address dataset.** Prefer the official national register. Give: exact name, maintainer/agency, **exact working download URL**, file format, encoding, delimiter, update cadence, **licence (must allow commercial use)**, and approximate row count.

2. **Exact field schema.** Does the dataset contain: locality, street, house number, **postal code**, admin codes (PL: TERYT / TERC / SIMC / ULIC; BG: EKATTE), coordinates? **Explicitly flag any field that is MISSING or documented as low-quality.** (For PL specifically: does the official PRG address-points file actually carry the postal code, and how good is it?)

3. **Postal-code granularity model — the single most important question.** Is the country **ZIP-driven like RO** (postal code = street + house-number-range, *number-inclusive*, so you must pick the code by house number + parity) or **locality-driven like CZ** (postal code coarse; derive it from the locality)? Give 2–3 concrete real examples proving the answer.

4. **Parsing / build recipe.** How to go from the raw file to a normalized `(locality, street, number-range, postcode)` table **without heavyweight GIS tooling** — we bulk-load PostgreSQL via `COPY`. If the official file is huge or awkward (e.g. a 20 GB GML), name the **best lighter alternative** (e.g. OpenAddresses CSV, a prebuilt extract, an API) and its trade-offs (freshness, licence, completeness).

5. **Correction rules that fit the country's model.** Street-type prefixes & abbreviations (PL: *ul. / al. / pl. / os.*; BG: *ул. / бул. / ж.к. / кв.*), diacritic/transliteration normalization (BG is Cyrillic — is Latin transliteration common in the wild?), the most common customer-entry errors, and how to reconcile city ↔ postal code.

6. **Bulgaria specifically — the weak link.** Is there ANY official **street + house-number** dataset (e.g. **ГРАО / GRAO** National Registry, or the postal operator)? Or is **OpenStreetMap** (Geofabrik extract, ODbL) the only realistic street-level source? What exactly does **EKATTE** provide (we believe: localities only, no streets)? Lay out how to build a usable BG validator (at least locality + street + postal) from what actually exists, and state its **accuracy ceiling** honestly.

### Required output format
- One section per country (PL, BG, + CZ sanity-check).
- A **comparison table** with columns: `dataset | download URL | licence | ~rows | has postcode? | has house number? | granularity model (ZIP-driven vs locality-driven) | recommended build path`.
- End with a **concrete recommendation**: which single dataset to use for PL and which for BG, and why — optimizing for (a) legal commercial use, (b) postal-code + house-number completeness, (c) ease of loading into Postgres without GIS.

Cite every source with a working URL. Where sources disagree (especially on licence or on whether a field exists), say so and give the more authoritative one.
