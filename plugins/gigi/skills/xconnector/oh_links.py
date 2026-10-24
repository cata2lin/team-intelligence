# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""`links` din Order Hub: status + etichete + linkuri pentru O comandă, după numărul ei SAU după un AWB.

DE CE: xConnector e dezinstalat din 19-oct-2026 (cheile API dau 401), iar statusul de livrare din AWBprint e înghețat
pe internațional din 27-sep; baza AWBprint nu mai are voie să fie citită de pe laptopuri. Sursa de adevăr pentru starea
unei comenzi e Order Hub: GET /api/cs/comanda (fișa, doar citire, fără date personale).

După AWB: GET /api/cs/comanda-dupa-awb — aceeași fișă plus `gasit_ca`: vie | colet (cod de colet DPD de 14 cifre →
AWB-ul părinte) | anulata | retur (AWB-ul de retur) | istoric (etichetă veche din era AWBprint). Textul spune când AWB-ul
căutat NU e eticheta vie, ca agentul să nu trateze o etichetă anulată / veche drept cea curentă.

Cheia (doar citire, `cs.citire`): env OH_TEAM_READ_TOKEN → OH_PROFIT_CITIRE_TOKEN → OH_CS_SB_TOKEN → KB OH_TEAM_READ_TOKEN.
Fără cheie sau fără răspuns de la Order Hub = eroare clară, nu o cifră din altă sursă.

  uv run oh_links.py --order GT123 [--shop <domeniu|nume>] [--json]
  uv run oh_links.py --awb <cod> [--json]
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://orderhub.arona.ro"
UI_COMENZI = BASE + "/app/orders"
KB = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "core", "scripts", "kb.py")
ENV_CHEI = ("OH_TEAM_READ_TOKEN", "OH_PROFIT_CITIRE_TOKEN", "OH_CS_SB_TOKEN")
GASIT_CA = {
    "vie": None,
    "colet": "cod de COLET (14 cifre) al AWB-ului părinte",
    "anulata": "o etichetă ANULATĂ a comenzii, nu eticheta vie",
    "retur": "AWB-ul de RETUR al comenzii",
    "istoric": "o etichetă VECHE (era AWBprint), nu eticheta vie",
}

TRACKING = {
    "dpd": "https://tracking.dpd.ro/?shipmentNumber=%s",
    "sameday": "https://sameday.ro/#awb=%s",
}


class EroareOH(RuntimeError):
    pass


def token():
    for k in ENV_CHEI:
        t = (os.environ.get(k) or "").strip()
        if t:
            return t
    try:
        t = subprocess.run(["uv", "run", KB, "secret-get", "OH_TEAM_READ_TOKEN"], capture_output=True, text=True,
                           stdin=subprocess.DEVNULL, timeout=60).stdout.strip()
    except Exception:
        t = ""
    if not t:
        raise EroareOH("lipsește cheia Order Hub (env %s sau KB OH_TEAM_READ_TOKEN)" % " / ".join(ENV_CHEI))
    return t


class _FaraRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):   # un redirect ar duce antetul Authorization la altă adresă
        return None


def _get(cale, params, cheie, http=None):
    url = BASE + cale + "?" + urllib.parse.urlencode(params)
    if http is not None:
        return http(url, cheie)
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + cheie, "Accept": "application/json"})
    try:
        with urllib.request.build_opener(_FaraRedirect).open(req, timeout=60) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:   # rețea: mesajul nu conține cheia
        raise EroareOH("Order Hub nu răspunde (%s)" % type(e).__name__)


def fisa(comanda, magazin="", cheie=None, http=None):
    """Fișa din Order Hub (dict). EroareOH pe orice altceva decât 200 cu `ok`."""
    p = {"comanda": comanda.strip().lstrip("#").strip()}
    if magazin:
        p["magazin"] = magazin
    return _fisa("/api/cs/comanda", p, cheie, http)


def fisa_dupa_awb(awb, cheie=None, http=None):
    """Fișa comenzii după AWB (vie / colet / anulată / retur / istoric). EroareOH ca la `fisa`."""
    return _fisa("/api/cs/comanda-dupa-awb", {"awb": str(awb).strip()}, cheie, http)


def _fisa(cale, p, cheie, http):
    cod, corp = _get(cale, p, cheie or token(), http)
    try:
        d = json.loads(corp)
    except Exception:
        raise EroareOH("Order Hub: răspuns necitit (HTTP %s)" % cod)
    if cod == 200 and isinstance(d, dict) and d.get("ok"):
        return d
    det = d.get("detail") if isinstance(d, dict) else None
    mesaj = (det.get("mesaj") if isinstance(det, dict) else det) or (d.get("mesaj") if isinstance(d, dict) else "")
    cand = det.get("candidati") if isinstance(det, dict) else None
    if cand:
        mesaj = "%s · %s" % (mesaj, ", ".join("%s (%s)" % (c.get("comanda"), c.get("magazin")) for c in cand))
    raise EroareOH("Order Hub HTTP %s: %s" % (cod, mesaj or "fără mesaj"))


def link_tracking(curier, awb):
    if not awb:
        return None
    for k, tip in TRACKING.items():
        if k in (curier or "").lower():
            return tip % urllib.parse.quote(str(awb))
    return None


def formateaza(d):
    st, mg = d.get("stare") or {}, d.get("magazin") or {}
    vii = d.get("etichete") or []
    awbs = ", ".join(e.get("awb") or "?" for e in vii)
    out = ["  %s (%s · %s)%s" % (d.get("comanda"), mg.get("nume"), mg.get("domeniu"),
                                 (" · AWB %s" % awbs) if awbs else " · fără AWB")]
    if d.get("awb_cautat"):
        cum = d.get("gasit_ca") or "?"
        nota = GASIT_CA.get(cum, "găsită ca «%s»" % cum)
        out.append("  Căutat:     AWB %s → %s" % (d["awb_cautat"], ("eticheta VIE" if nota is None else
                                                                   "⚠ %s; starea de mai jos e a comenzii" % nota)))
    stare = "ANULATĂ" if st.get("anulata") else (st.get("livrare") or "—")
    out.append("  Status:     %s · procesare=%s · adresă=%s · stadiu=%s · colete=%s%s" % (
        stare, st.get("procesare") or "?", st.get("adresa") or "?", st.get("stadiu") or "?", st.get("colete"),
        " · arhivată" if st.get("arhivata") else ""))
    for e in vii:
        out.append("  Etichetă:   %s · %s · %s (%s)%s" % (e.get("awb"), e.get("curier") or "?", e.get("status") or "?",
                                                         e.get("ultim_status") or "—",
                                                         " · printată" if e.get("printata") else ""))
    h = d.get("hold") or {}
    if h.get("pe_hold"):
        out.append("  Hold:       DA (%s)" % (h.get("clasa") or "necitit"))
    t = d.get("tichet")
    if t and t.get("stare") != "solved":
        out.append("  Tichet CS:  #%s %s (%s)" % (t.get("id"), t.get("motiv") or "", t.get("stare")))
    out.append("  Order Hub:  %s" % UI_COMENZI)
    if mg.get("domeniu"):
        out.append("  Shopify:    https://%s/admin/orders?query=%s" % (mg["domeniu"], urllib.parse.quote(d.get("comanda") or "")))
    for e in vii:
        u = link_tracking(e.get("curier"), e.get("awb"))
        out.append("  Tracking:   %s" % (u or "%s: vezi awb_track (status live)" % e.get("awb")))
    if not vii:
        out.append("  Tracking:   — (fără AWB)")
    out.append("  → acțiuni pe comandă: gigi:order-hub · profil client: gigi:cs-360 customer · tichete: gigi:cs-tickets")
    return "\n".join(out)


def ruleaza(order="", awb="", shop="", ca_json=False, http=None):
    """(cod_iesire, text)."""
    if not order and not awb:
        return 2, "Dă --order GT123 sau --awb <cod>."
    try:
        d = fisa(order, shop, http=http) if order else fisa_dupa_awb(awb, http=http)
    except EroareOH as e:
        return 1, "EROARE: %s" % e
    return 0, (json.dumps(d, ensure_ascii=False, indent=1) if ca_json else formateaza(d))


def main(argv=None):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--order", default=""); ap.add_argument("--awb", default="")
    ap.add_argument("--shop", default=""); ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    cod, text = ruleaza(a.order, a.awb, a.shop, a.json)
    print(text)
    return cod


if __name__ == "__main__":
    sys.exit(main())
