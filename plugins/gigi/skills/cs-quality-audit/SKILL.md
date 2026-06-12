---
name: cs-quality-audit
description: Systematic "where did we answer poorly" audit over the whole Richpanel CS history — the data-driven version of the CS documentation's bad-answer section. Scans every real CS ticket and auto-flags problems on hard signals: LENT (slow first response = first_response_time − created_at), FRICȚIUNE (high comment_count = many round-trips, not solved first time), FRUSTRARE (customer says "al treilea email", "nu raspunde nimeni", "v-am tot scris"), ESCALADARE (ANPC / consumer-protection / legal threats), VECHI-DESCHIS (OPEN too long). Groups by STORE and by AGENT (resolves assignee_id → real name) with per-agent volume, % slow, median response hours, frustration & escalation counts. Reads richpanel_tickets.db (gigi:richpanel-export). Use for "audit calitate CS", "unde s-a raspuns prost", "tichete cu frustrare", "raspuns lent CS", "ANPC / escaladari", "performanta agenti CS pe calitate", "stale open tickets", "quality report customer service". Read-only.
---

# cs-quality-audit — unde s-a răspuns prost (automat, pe tot istoricul)

În loc de 3 exemple în documentație, marchează automat toate tichetele-problemă pe semnale reale.

## Cum rulezi
```bash
uv run cs_quality_audit.py summary               # tablou: per agent (vol/lent%/median h/frustrări) + per magazin
uv run cs_quality_audit.py frustrated            # FRUSTRARE + ESCALADARE (cele mai grave) — de citit primele
uv run cs_quality_audit.py slow --hours 24       # primă reacție peste N ore (sortat descrescător)
uv run cs_quality_audit.py stale --days 7        # OPEN mai vechi de N zile
uv run cs_quality_audit.py friction --min 6      # tichete cu multe mesaje (nerezolvate din prima)
uv run cs_quality_audit.py frustrated --agent Cristina   # filtrare pe agent / --store / --json
```

## Semnale (flaguri)
- **LENT** `first_response_time − created_at > prag` (default 24h)
- **FRICȚIUNE** `comment_count ≥ N` (multe round-trip-uri)
- **FRUSTRARE** clientul semnalează că nu primește răspuns / a scris de mai multe ori
- **ESCALADARE** ANPC / protecția consumatorului / „dau în judecată" (urgent!)
- **VECHI-DESCHIS** OPEN de prea mult timp

## Ce arată (exemplu real, 30 zile)
27% din tichetele CS reale au cel puțin un flag. Median primă reacție foarte diferit între agenți (ex. piața CZ ~120h). Top magazine-problemă: Esteban, Grandia, Nubra.

## Note
- Exclude comentariile la reclame + spam + recenzii (alea le ia `gigi:cs-comment-intelligence`).
- `first_response_time` vine din raw-ul Richpanel; unde lipsește, LENT nu se aplică (dar FRUSTRARE/FRICȚIUNE/VECHI da).
- Anti-pattern-ul „șablon repetat fără rezolvare" (cazul Belasil din documentație) apare aici ca FRUSTRARE+FRICȚIUNE. Pt confirmare, citește conversația cu `gigi:richpanel-export` / get_conversation.
- Harta agent_id→nume e în skill (6 agenți); actualizeaz-o dacă se schimbă echipa.
