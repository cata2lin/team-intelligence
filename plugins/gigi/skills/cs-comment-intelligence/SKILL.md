---
name: cs-comment-intelligence
description: Turns the ~12,000 Facebook/Instagram AD COMMENTS (the biggest, mostly-ignored slice of Richpanel) into action. Classifies every ad comment as buy-intent LEAD ("cum comand?", "pret?", "vreau si eu" = lost sales), public COMPLAINT (negative on a live ad → tanks CTR, raises CPA, hurts reputation), TESTIMONIAL (reusable social proof), question or noise — and groups them BY STORE using the FB page→store map. Surfaces actionable lists: open leads to recover, public complaints to moderate, praise to reuse. Reads the local richpanel_tickets.db (from gigi:richpanel-export + richpanel_link.py). Use for "comentarii la reclame", "lead-uri din comentarii", "reclamatii publice pe reclame", "ad comments", "social comment moderation", "ce spun oamenii la reclame", "testimoniale de refolosit", "comentarii negative facebook". Read-only — never replies or drafts.
---

# cs-comment-intelligence — lead-uri + reclamații + testimoniale din comentariile la reclame

Cele ~12.000 comentarii la reclame FB/IG nu-s zgomot. Pe 30 zile am găsit: **~1.350 lead-uri** de cumpărare nerăspunse (vânzări pierdute), **~1.000 reclamații publice** pe reclame live (CTR↓ CPA↑ reputație) și **~1.380 testimoniale** (social proof).

## Cum rulezi
```bash
uv run cs_comment_intelligence.py summary                    # tablou per magazin (lead/reclamație/testimonial/întrebare)
uv run cs_comment_intelligence.py leads --open               # intenții de cumpărare ÎNCĂ DESCHISE (de recuperat acum)
uv run cs_comment_intelligence.py leads --store Esteban      # lead-urile unui magazin
uv run cs_comment_intelligence.py complaints --store Grandia # reclamații publice de moderat
uv run cs_comment_intelligence.py praise --store Nubra       # testimoniale de refolosit
uv run cs_comment_intelligence.py leads --store GT --json    # pt automatizări
```

## Ce face
- Clasifică fiecare comentariu (reguli RO, tunate pe exemple reale): `lead` / `reclamatie` / `testimonial` / `intrebare` / `neutru` / `zgomot`.
- Grupează pe magazin din `resolved_store` (maparea pagină FB→magazin, vezi memoria `fb-page-store-map`).
- Prioritate: reclamație > lead > testimonial > întrebare (negativul e cel mai urgent).

## De refolosit cu
- `gigi:customer-identity` — cine e cel care a comentat (dacă a și comandat).
- Pt răspuns: rămâne **manual / draft** (regula draft-only Richpanel). Acest skill doar identifică, nu răspunde.

## Limite
- Regulile sunt în română → comentariile **Bonhaus CZ/PL/BG** ies sub-clasificate (lead/reclamație ≈0). De adăugat dicționare cehă/poloneză/bulgară într-o versiune viitoare.
- Necesită ca `richpanel_link.py` să fi rulat (pt `resolved_store`). Fără el cade pe coloana `store` (mai goală).
