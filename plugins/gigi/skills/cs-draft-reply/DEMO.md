# CS Auto-Draft — rezumat pentru echipă

**Ce e:** un flow care parcurge coada deschisă din Richpanel și, pentru fiecare tichet care
așteaptă răspuns de la noi, pregătește răspunsul CS — în vocea agenților reali, urmând
procedurile reale ARONA, cu tot contextul clientului pus cap la cap, în limba clientului.

**Principiul de siguranță: DRAFT + PROPUNE-APROBĂ.** Nimic nu pleacă singur la client.
- Răspunsurile către client = **DRAFT** (omul verifică și trimite din Richpanel).
- Acțiunile reale (modificare/anulare comandă, hide/mesaj privat FB, închidere spam) = **doar cu aprobare**, niciodată nesupravegheat.

## Pipeline per tichet
1. **Triaj LLM** — problemă, **produs**, categorie, **limbă**, severitate, spam? (regex doar ca fallback).
2. **SPAM / notificări automate** (Meta, judge.me, newsletter, boți — pe email/DM/comentariu) → **excluse din draft**; cu `--close-spam` se **închid (CLOSED) + tag spam**.
3. **Context 360** — identitate cross-platform, **toate** comenzile, **unde a mai scris**, sentiment, **brand + produs**.
4. **Proceduri + voce ÎNVĂȚATE din tichete reale** (cs-procedures) → draftul urmează procedura reală și sună ca agenții.
5. **Limba clientului** — răspunde în limba în care a scris (RO/CZ/PL/BG/EN). Brandurile Bonhaus CZ/PL/BG → cehă/poloneză/bulgară; dacă scrie în engleză → engleză.
6. **Escaladare** (ANPC/juridic, refund promis, client supărat/repetat, VIP) → nu auto-răspunde; public scurt + rutare internă (HIGH + tag + notă-brief „de sunat") ca CS să preia ușor.
7. **Comentarii FB/IG:** spam → hide; reclamație → **public scurt politicos (plural) + DM detaliat**; presale → **public scurt + DM cu detalii**.
8. **Acțiuni pe comandă** (modify/cancel/swap/resend) — propune+aprobă, doar pre-fulfillment, pe comanda referită clar.

## Calitate (auto-verificare)
- **Audit RO + gramatică, multilingv** (`grammar_audit.py`): verifică fiecare răspuns în limba lui + dacă a răspuns în limba clientului. Ultima rulare: **0 greșeli**.
- **2 review-uri adversariale → 13 buguri** găsite și reparate; cod hardenat.

## Demo (comenzi)
```bash
# vede coada + ce ar face (dry-run, NU scrie nimic)
uv run cs_auto_draft.py --limit 15

# audit de limbă + gramatică peste drafturi
uv run cs_auto_draft.py --limit 20 --json 2>/dev/null | grep @@JSON@@ | sed 's/^@@JSON@@//' > /tmp/d.json
uv run grammar_audit.py --file /tmp/d.json

# salvează drafturile + rutează escaladările + (opțional) închide spam  — NU trimite, NU aplică acțiuni
uv run cs_auto_draft.py --create-draft --close-spam

# aplică o acțiune/hide/DM propus la un tichet (cu aprobare umană)
uv run cs_auto_draft.py --approve <nr> --agent <Nume>

# alegi ce acțiuni sunt active (restul rămân doar draft)
uv run cs_auto_draft.py --actions modify,cancel
```

## Stare
- Rulează pe **OpenAI gpt-4o** (română bună); comută automat pe Claude când adăugăm cheia Anthropic.
- **Validat live:** dry-run complet pe toate tipurile; limba CZ confirmată pe tichet real; audit 0 greșeli.
- **De validat live (cu omul de față):** aplicarea reală a acțiunilor + hide/mesaj-privat FB (necesită token Meta de pagină cu scope moderare/mesagerie) + închiderea spam.

## De decis cu echipa
- Pe ce branduri/magazine pornim întâi; cine aprobă/expediază drafturile.
- presale public: răspuns public + privat (acum) — ok pe toate brandurile?
