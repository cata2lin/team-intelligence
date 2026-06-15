# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31", "beautifulsoup4>=4.12"]
# ///
"""
GEO/AEO readiness scorer — how likely a page is to be CITED by AI engines
(ChatGPT, Perplexity, Google AI Overviews) and to win featured snippets.

Pure heuristics, offline, no API keys. Romanian-aware. Signals (evidence-based,
ported from open GEO skills): self-contained answer passages (~130-170 words),
front-loaded answers, question-style headings, stats-with-source, freshness,
entity/sameAs presence, structured data, and AI-crawler access in robots.txt.

NOTE: the GEO correlation figures floating around (3x, 0.737, 44%) are vendor
marketing — use this score as a relative tuning heuristic, not a guarantee.

Usage:
    uv run geo.py score  --url https://esteban.ro/collections/dama
    uv run geo.py robots --url https://esteban.ro          # AI-crawler access audit
"""
import argparse, json, re, sys
import requests
from bs4 import BeautifulSoup

UA = {"User-Agent": "Mozilla/5.0 (compatible; AronaGEO/1.0)"}
RO_Q = ("ce ", "cum ", "de ce", "care ", "când", "cand", "unde", "cât", "cat ", "cine", "ce-")
EN_Q = ("what", "how", "why", "which", "when", "where", "who", "is ", "are ", "best")
DEF_PAT = re.compile(r"\b(este|sunt|înseamnă|inseamna|reprezintă|reprezinta|se referă|is|are|means)\b", re.I)
AI_BOTS = ["GPTBot", "OAI-SearchBot", "ChatGPT-User", "ClaudeBot", "anthropic-ai", "PerplexityBot",
           "CCBot", "Bytespider", "cohere-ai", "Google-Extended", "GoogleOther", "Applebot-Extended",
           "FacebookBot", "Amazonbot"]

def fetch(url):
    r = requests.get(url, headers=UA, timeout=30)
    r.raise_for_status()
    return r.text

def words(s): return len(re.findall(r"\w+", s or ""))

def is_question(t):
    tl = (t or "").strip().lower()
    return tl.endswith("?") or tl.startswith(RO_Q) or tl.startswith(EN_Q)

def jsonld(soup):
    out = []
    for tag in soup.find_all("script", {"type": "application/ld+json"}):
        try:
            d = json.loads(tag.string or "{}")
            out += d if isinstance(d, list) else [d]
        except Exception:
            pass
    return out

def cmd_score(args):
    html = fetch(args.url)
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "nav", "footer", "header"]):
        if t.name in ("script", "style", "noscript"): t.decompose()
    h2s = [h.get_text(" ", strip=True) for h in soup.find_all(["h2", "h3"])]
    paras = [p.get_text(" ", strip=True) for p in soup.find_all("p") if words(p.get_text()) >= 8]
    body = soup.get_text(" ", strip=True)
    total_w = words(body)
    ld = jsonld(soup)
    types = {(d.get("@type") if isinstance(d, dict) else "") for d in ld}
    types = {t for t in types if t}

    sig = {}
    # 1. question headings
    qh = sum(1 for h in h2s if is_question(h))
    sig["question_headings"] = (min(100, round(100 * qh / max(1, len(h2s)))), f"{qh}/{len(h2s)} headinguri sunt întrebări")
    # 2. self-contained answer passages (130-170 words ideal; 60-220 acceptable)
    good = sum(1 for p in paras if 60 <= words(p) <= 220)
    ideal = sum(1 for p in paras if 130 <= words(p) <= 170)
    sig["citable_passages"] = (min(100, round(100 * good / max(1, len(paras)))), f"{good}/{len(paras)} paragrafe au lungime de pasaj citabil ({ideal} în zona ideală 130-170 cuvinte)")
    # 3. front-loaded answer (a definition/answer in the first ~120 words)
    lead = " ".join(body.split()[:120])
    sig["front_loaded"] = (100 if DEF_PAT.search(lead) else 0, "răspuns/definiție în primele ~120 cuvinte" if DEF_PAT.search(lead) else "primele 120 cuvinte nu conțin un răspuns direct")
    # 4. stats with source-ish signal (numbers + outbound links/citations)
    nums = len(re.findall(r"\b\d{1,4}([.,]\d+)?\s?(%|lei|ron|ore|h|ml|zile|ani)?", body, re.I))
    links = len(soup.find_all("a", href=True))
    sig["evidence_density"] = (min(100, nums * 2), f"{nums} valori numerice/cifre detectate (statistici cu sursă cresc citabilitatea)")
    # 5. freshness
    dm = ""
    for d in ld:
        if isinstance(d, dict) and (d.get("dateModified") or d.get("datePublished")):
            dm = d.get("dateModified") or d.get("datePublished"); break
    sig["freshness"] = (100 if dm else 0, f"dateModified/datePublished = {dm}" if dm else "lipsește data (dateModified) — prospețimea e un semnal de citabilitate")
    # 6. entity / sameAs
    same = any(isinstance(d, dict) and d.get("sameAs") for d in ld)
    sig["entity_sameAs"] = (100 if same else 0, "Organization sameAs prezent" if same else "lipsește sameAs (profiluri sociale/Wikidata) în schema Organization")
    # 7. structured data useful for AEO
    aeo_types = {"FAQPage", "Product", "Article", "BlogPosting", "BreadcrumbList", "HowTo"}
    have = types & aeo_types
    sig["structured_data"] = (min(100, len(have) * 34), f"schema: {', '.join(sorted(have)) or 'niciuna utilă AEO'}")
    # 8. AI-crawler access
    allowed = _ai_access(args.url)
    sig["ai_crawler_access"] = (round(100 * allowed / len(AI_BOTS)), f"{allowed}/{len(AI_BOTS)} boți AI permiși în robots.txt")

    weights = {"question_headings":0.12,"citable_passages":0.20,"front_loaded":0.15,"evidence_density":0.12,
               "freshness":0.10,"entity_sameAs":0.10,"structured_data":0.13,"ai_crawler_access":0.08}
    score = round(sum(sig[k][0]*weights[k] for k in weights))
    print(f"\nGEO/AEO Readiness — {args.url}\n{'='*64}")
    print(f"  SCOR: {score}/100   ({total_w} cuvinte, {len(h2s)} subtitluri, {len(paras)} paragrafe)\n")
    for k in weights:
        s, note = sig[k]
        bar = "█"*round(s/10) + "·"*(10-round(s/10))
        print(f"  {k:<20} {s:>3}/100 {bar}  {note}")
    print(f"\n  Fix-uri prioritare (RO, parfumuri/produse):")
    fixes = []
    if sig["citable_passages"][0] < 50: fixes.append("Rescrie răspunsurile ca pasaje auto-suficiente de ~130-170 cuvinte (un H2-întrebare → un paragraf-răspuns complet).")
    if sig["question_headings"][0] < 40: fixes.append("Transformă subtitlurile în întrebări reale ('Ce parfum...', 'Cum alegi...').")
    if not sig["front_loaded"][0]: fixes.append("Pune răspunsul/definiția în primele 1-2 fraze (front-loading).")
    if not sig["freshness"][0]: fixes.append("Adaugă dateModified în schema + dată vizibilă 'Actualizat'.")
    if not sig["entity_sameAs"][0]: fixes.append("Adaugă sameAs (IG/FB/TikTok/Wikidata) în schema Organization.")
    if sig["structured_data"][0] < 60: fixes.append("Adaugă FAQPage + Product/Article JSON-LD.")
    if sig["ai_crawler_access"][0] < 80: fixes.append("Deblochează boții AI în robots.txt (GPTBot/ClaudeBot/PerplexityBot...).")
    for f in fixes or ["(pagina e solidă pe semnalele AEO de bază)"]:
        print(f"    - {f}")

def _ai_access(url):
    m = re.match(r"(https?://[^/]+)", url)
    if not m: return 0
    try:
        txt = requests.get(m.group(1) + "/robots.txt", headers=UA, timeout=20).text
    except Exception:
        return len(AI_BOTS)  # no robots = allowed
    blocked = 0
    low = txt.lower()
    # crude: a bot is "blocked" if it has a UA block with Disallow: /
    for bot in AI_BOTS:
        bl = bot.lower()
        if bl in low:
            seg = low.split(bl, 1)[1][:300]
            if re.search(r"disallow:\s*/\s*(\n|$)", seg):
                blocked += 1
    return len(AI_BOTS) - blocked

def cmd_robots(args):
    m = re.match(r"(https?://[^/]+)", args.url)
    base = m.group(1) if m else args.url
    try:
        txt = requests.get(base + "/robots.txt", headers=UA, timeout=20).text
    except Exception as e:
        print(f"robots.txt inaccesibil: {e}"); return
    low = txt.lower()
    print(f"\nAI-crawler access — {base}/robots.txt\n{'='*52}")
    for bot in AI_BOTS:
        bl = bot.lower()
        state = "✅ permis"
        if bl in low:
            seg = low.split(bl, 1)[1][:300]
            if re.search(r"disallow:\s*/\s*(\n|$)", seg): state = "⛔ BLOCAT (Disallow: /)"
        print(f"  {bot:<22} {state}")
    print("\n  (boții AI permiși = magazinul poate fi citat de ChatGPT/Perplexity/AI Overviews)")

def main():
    ap = argparse.ArgumentParser(description="GEO/AEO readiness scorer (offline, RO-aware).")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("score", help="GEO/AEO readiness score for a URL"); s.add_argument("--url", required=True); s.set_defaults(fn=cmd_score)
    r = sub.add_parser("robots", help="AI-crawler access audit"); r.add_argument("--url", required=True); r.set_defaults(fn=cmd_robots)
    args = ap.parse_args(); args.fn(args)

if __name__ == "__main__":
    main()
