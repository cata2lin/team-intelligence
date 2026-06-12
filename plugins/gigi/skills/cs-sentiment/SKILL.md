---
name: cs-sentiment
description: Per-ticket SENTIMENT scoring for Customer Service tickets (negative / neutral / positive + intensity), across all channels and multilingual (RO + CZ/PL/BG). Recycles the complaint/praise dictionaries (cs-comment-intelligence) plus frustration/escalation signals (cs-quality-audit) to score each real CS ticket; escalation (ANPC/legal) = intensity 3, frustration ("nu raspunde nimeni", "al treilea email") = 2, complaint keyword = 1. Sorts the angriest customers first for triage, shows sentiment distribution per store and per agent, and the monthly sentiment trend. Reads richpanel_tickets.db (read-only). Use for "sentiment pe tichete", "cei mai furiosi clienti", "ticket sentiment", "sentiment per magazin", "clienti negativi de tratat urgent", "trend sentiment", "negative tickets triage". Read-only.
---

# cs-sentiment — scor de sentiment per tichet CS

Răspunde la „cine e cel mai nemulțumit, cât de rău, și cum evoluează" — pe toate canalele, multilingv.

## Cum rulezi
```bash
uv run cs_sentiment.py summary                 # distribuție negativ/neutru/pozitiv per magazin
uv run cs_sentiment.py negative --open         # cele mai negative tichete DESCHISE, sortate după intensitate (triaj)
uv run cs_sentiment.py negative --store Grandia
uv run cs_sentiment.py trend                   # trend sentiment pe luni
uv run cs_sentiment.py negative --json         # pt automatizare / alertă
uv run cs_sentiment.py llm --open --limit 100  # pas LLM: scor precis pe CS real (prinde reclamațiile CALME)
```

## Rule-based vs LLM
- **Rule-based** (summary/negative/trend) — instant, gratis, multilingv. Bun pt triaj rapid. Limită: ratează reclamațiile calme fără cuvinte-cheie (ex. „ați trimis alt produs").
- **LLM** (`llm`, ANTHROPIC_API_KEY→Claude / OPENAI_API_KEY→gpt, model `SENT_MODEL`) — scor precis (negativ/neutru/pozitiv + intensitate 0-3 + motiv), **prinde nemulțumirile factuale**. Cost mărginit de `--limit` (default 100). Folosește-l pe CS real / deschise pentru triaj de calitate.

## Cum scorează
- **Intensitate** (cumulativă): escaladare ANPC/juridic = **3** 🚨, frustrare („nu răspunde nimeni", „al treilea email") = **2** ⚠️, reclamație (dicționar) = **1**.
- **Sentiment:** intensitate>0 → `negativ`; altfel laudă → `pozitiv`; restul `neutru`.
- **Multilingv:** RO + CZ/PL/BG (dicționarele din `cs-comment-intelligence`, cu normalizare diacritice cehă + `praise_kill_guard`).
- Exclude zgomotul (comentarii sociale, spam, recenzii, saluturi, formulare).

## De refolosit cu
- `cs-quality-audit` (frustrare/lent) · `customer-identity` (cine e clientul negativ) · `cs-draft-reply` (răspuns la cei furioși).

## Note
- Sentimentul ≠ „e o problemă": un WISMO informativ e neutru; doar reclamațiile/frustrarea scorează negativ.
- Necesită `resolved_store` populat (rulează `richpanel_link.py`) pt defalcarea pe magazin; altfel cad pe „?".
