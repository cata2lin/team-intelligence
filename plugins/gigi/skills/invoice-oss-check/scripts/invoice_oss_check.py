# /// script
# requires-python = ">=3.10"
# dependencies = ["pypdf>=4.0"]
# ///
"""invoice_oss_check.py — auditează dacă facturile SmartBill aplică cota OSS corectă pe ȚARA de destinație.

Riscul fiscal: o comandă intl (PL/BG/CZ/HU/SK) facturată din greșeală la cota RO (21%) în loc de cota țării
(PL 23%, BG 20%…) = TVA greșit declarat. Skill-ul ia un eșantion de facturi (din logul xConnector sau numere
date), descarcă PDF-ul din SmartBill, extrage cota TVA + țara și o compară cu tabelul OSS. Rate-limit-aware
(SmartBill limitează PDF-urile agresiv → spacing între cereri).

  invoice_oss_check.py --from-log [--per-prefix 2] [--gap 5]      # eșantion din xc_invoice.log, grupat pe prefix
  invoice_oss_check.py --numbers 583051,565789 [--series ARONA]   # facturi anume
  invoice_oss_check.py --prefix PL,CZ,BONBG --per-prefix 3        # doar anumite prefixe

Env: SMARTBILL_EMAIL/TOKEN, SMARTBILL_CIF (implicit RO37247302). Rulează pe VPS (are logul + creds din KB via wrapper).
"""
import argparse, base64, collections, glob, io, os, re, sys, time, urllib.request, urllib.error

BASE = "https://ws.smartbill.ro/SBORO/api"
CIF = os.environ.get("SMARTBILL_CIF", "RO37247302")
LOG_GLOB = "/root/Scripturi/logs/xc_invoice.log*"
# cotele OSS pe țară (RO standard 21% din aug-2025)
OSS = {"RO": 21, "CZ": 21, "BG": 20, "PL": 23, "HU": 27, "SK": 23, "GR": 24, "HR": 25, "DE": 19}
COUNTRY_WORDS = {"Bulgaria": "BG", "Czech": "CZ", "Cehia": "CZ", "Ceska": "CZ", "Poland": "PL", "Polska": "PL",
                 "Hungary": "HU", "Ungaria": "HU", "Slovak": "SK", "Slovacia": "SK", "Romania": "RO", "România": "RO",
                 "Germany": "DE", "Greece": "GR", "Croatia": "HR"}


def _auth():
    em, tk = os.environ.get("SMARTBILL_EMAIL"), os.environ.get("SMARTBILL_TOKEN")
    if not (em and tk):
        sys.exit("lipsesc SMARTBILL_EMAIL/TOKEN din env")
    return "Basic " + base64.b64encode(f"{em}:{tk}".encode()).decode()


def fetch_pdf(series, num, auth, retries=3, backoff=25):
    u = f"{BASE}/invoice/pdf?cif={CIF}&seriesname={series}&number={num}"
    for i in range(retries):
        try:
            return urllib.request.urlopen(urllib.request.Request(u, headers={"authorization": auth}), timeout=40).read()
        except urllib.error.HTTPError as e:
            if e.code == 403 and i < retries - 1:   # rate limit
                time.sleep(backoff); continue
            return None
    return None


def pdf_info(pdf):
    from pypdf import PdfReader
    t = "\n".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
    rates = sorted(set(int(x) for x in re.findall(r"(\d{1,2})\s*%", t)))
    country = next((cc for w, cc in COUNTRY_WORDS.items() if w.lower() in t.lower()), None)
    return rates, country


def samples_from_log(prefixes=None, per=2):
    pat = re.compile(r"([A-Z]{2,6})\d+\s*(?:→|->)\s*ARONA\s*(\d+)")
    by = collections.defaultdict(list)
    for f in glob.glob(LOG_GLOB):
        for ln in open(f, errors="replace"):
            m = pat.search(ln)
            if m and (not prefixes or m.group(1) in prefixes):
                if m.group(2) not in by[m.group(1)]:
                    by[m.group(1)].append(m.group(2))
    out = []
    for pref, nums in by.items():
        out += [(pref, n) for n in nums[:per]]
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--from-log", action="store_true")
    p.add_argument("--numbers"); p.add_argument("--prefix"); p.add_argument("--series", default="ARONA")
    p.add_argument("--per-prefix", type=int, default=2); p.add_argument("--gap", type=float, default=5)
    a = p.parse_args()
    auth = _auth()
    if a.numbers:
        samples = [("MANUAL", n.strip()) for n in a.numbers.split(",") if n.strip()]
    else:
        prefixes = set(a.prefix.split(",")) if a.prefix else None
        samples = samples_from_log(prefixes, a.per_prefix)
    if not samples:
        sys.exit("niciun eșantion (verifică logul / prefixele)")
    print(f"Verific {len(samples)} facturi (gap {a.gap}s, seria {a.series})…\n")
    res = collections.defaultdict(lambda: collections.Counter())
    problems = []
    for pref, num in samples:
        pdf = fetch_pdf(a.series, num, auth)
        if not pdf:
            print(f"  {pref} {num}: fetch eșuat (rate-limit persistent)"); time.sleep(a.gap); continue
        rates, ctry = pdf_info(pdf)
        exp = OSS.get(ctry) if ctry else None
        verdict = "OK" if (exp and exp in rates) else ("? (țară nedetectată)" if not ctry else "⚠️ MISMATCH")
        if verdict.startswith("⚠️"): problems.append((pref, num, ctry, exp, rates))
        print(f"  {pref:<8} {num}: țară={ctry or '?'} cote={rates} așteptat={exp} → {verdict}")
        res[f"{pref}/{ctry}"][tuple(rates)] += 1
        time.sleep(a.gap)
    print("\n── REZUMAT ──")
    for k, c in sorted(res.items()):
        print(f"  {k:<16} {dict(c)}")
    if problems:
        print("\n🔴 PROBLEME OSS (cotă greșită pe țară):")
        for pref, num, ctry, exp, rates in problems:
            print(f"  {pref} {num}: {ctry} ar trebui {exp}%, dar are {rates}")
    else:
        print("\n✅ Nicio problemă OSS detectată în eșantion.")


if __name__ == "__main__":
    main()
