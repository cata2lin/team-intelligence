# /// script
# requires-python = ">=3.9"
# dependencies = ["requests>=2.31"]
# ///
"""
Domain authority benchmark via Open PageRank (free) — a backlink/authority PROXY.
Compares our stores against competitors on Open PageRank score (0-10) + global rank.

Key from KB secret OPENPAGERANK_API_KEY (or env). NOTE: this gives an authority
SIGNAL, not a referring-domains list — a real backlink graph needs DataForSEO/Ahrefs
(paid). Open PageRank coverage on .ro is partial; treat as directional.

Usage:
    KB=~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py
    export OPENPAGERANK_API_KEY="$(uv run "$KB" secret-get OPENPAGERANK_API_KEY)"
    uv run authority.py --domains esteban.ro,george-talent.ro,nubra.ro
    uv run authority.py --ours --vs notino.ro,sephora.ro     # our stores vs competitors
"""
import argparse, os, subprocess, sys
import requests

OURS = ["esteban.ro", "george-talent.ro", "nubra.ro", "grandia.ro", "belasil.ro"]

def _key():
    k = os.environ.get("OPENPAGERANK_API_KEY")
    if k: return k
    for c in [os.environ.get("KB_PY"),
              os.path.expanduser("~/.claude/plugins/marketplaces/team-intelligence/plugins/core/scripts/kb.py"),
              os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py")]:
        if c and os.path.exists(c):
            try:
                return subprocess.run(["uv", "run", os.path.abspath(c), "secret-get", "OPENPAGERANK_API_KEY"],
                                      capture_output=True, text=True, timeout=60).stdout.strip()
            except Exception:
                pass
    sys.exit("No OPENPAGERANK_API_KEY (set it or kb.py secret-get OPENPAGERANK_API_KEY).")

def fetch(domains, key):
    out = {}
    for i in range(0, len(domains), 100):
        batch = domains[i:i+100]
        params = [("domains[]", d) for d in batch]
        r = requests.get("https://openpagerank.com/api/v1.0/getPageRank",
                         headers={"API-OPR": key}, params=params, timeout=30)
        if r.status_code != 200:
            sys.exit(f"Open PageRank error {r.status_code}: {r.text[:200]}")
        for row in r.json().get("response", []):
            out[row.get("domain")] = row
    return out

def main():
    ap = argparse.ArgumentParser(description="Domain authority via Open PageRank.")
    ap.add_argument("--domains", help="comma-separated domains")
    ap.add_argument("--ours", action="store_true", help="include our 5 stores")
    ap.add_argument("--vs", help="competitor domains, comma-separated")
    args = ap.parse_args()

    domains = []
    if args.ours or not args.domains:
        domains += OURS
    if args.domains:
        domains += [d.strip() for d in args.domains.split(",") if d.strip()]
    if args.vs:
        domains += [d.strip() for d in args.vs.split(",") if d.strip()]
    domains = list(dict.fromkeys(domains))  # dedupe, keep order

    data = fetch(domains, _key())
    rows = []
    for d in domains:
        row = data.get(d, {})
        opr = row.get("page_rank_decimal")
        rank = row.get("rank")
        rows.append((opr if isinstance(opr, (int, float)) else -1, d, opr, rank))
    rows.sort(reverse=True)
    print(f"\nDomain authority (Open PageRank 0-10) — {len(domains)} domenii")
    print(f"  {'OPR':>5}  {'global rank':>13}  domain")
    for _, d, opr, rank in rows:
        opr_s = f"{opr:.2f}" if isinstance(opr, (int, float)) else "n/a"
        rank_s = f"{int(rank):,}" if str(rank).isdigit() else (rank or "n/a")
        mark = "  ← al nostru" if d in OURS else ""
        print(f"  {opr_s:>5}  {rank_s:>13}  {d}{mark}")
    print("\n  (semnal de autoritate, nu listă de backlinks; lista reală = DataForSEO/Ahrefs, plătit)")

if __name__ == "__main__":
    main()
