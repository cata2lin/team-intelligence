"""Regula de atribuire a canalului de marketing (folosită de analiză și de câmpul din Order Hub)."""
from canal_marketing import canal_din_comanda as C


def _v(**k):
    return {"customerJourneySummary": {"lastVisit": k}}


def test_click_id_bate_utm():
    assert C(_v(landingPage="https://x.ro/p?ttclid=abc&utm_source=facebook",
                utmParameters={"source": "facebook"})) == ("tiktok", "click_id:ttclid:last")
    assert C(_v(landingPage="/products/x?fbclid=1")) == ("meta", "click_id:fbclid:last")
    assert C(_v(landingPage="/?gbraid=1")) == ("google", "click_id:gbraid:last")


def test_utm_apoi_referrer():
    assert C(_v(utmParameters={"source": "ig"})) == ("meta", "utm:last")
    assert C(_v(utmParameters={"source": "TikTok"})) == ("tiktok", "utm:last")
    assert C(_v(utmParameters={"source": "newsletter"})) == ("altul", "utm:last")
    assert C(_v(referrerUrl="https://l.facebook.com/l.php?u=x")) == ("meta", "referrer:last")
    assert C(_v(referrerUrl="https://www.google.ro/")) == ("google", "referrer:last")


def test_releasit_atribute():
    o = {"customAttributes": [{"key": "utm_source", "value": "tiktok"}]}
    assert C(o) == ("tiktok", "releasit:utm")
    o = {"customAttributes": [{"key": "fbclid", "value": "x"}]}
    assert C(o) == ("meta", "releasit:fbclid")


def test_direct_si_necunoscut():
    assert C(_v(landingPage="/products/x")) == ("direct", "vizita_fara_sursa")
    assert C({}) == ("necunoscut", "fara_date")
    assert C({"customerJourneySummary": None}) == ("necunoscut", "fara_date")


def test_releasit_cheile_cu_spatiu():
    o = {"customAttributes": [{"key": "UTM source", "value": "facebook"}, {"key": "Telefon", "value": "07"}],
         "customerJourneySummary": {"firstVisit": None, "lastVisit": None}}
    assert C(o) == ("meta", "releasit:utm")


def test_marketplace_are_canalul_lui():
    from canal_marketing import canal_marketplace, CANALE, NECUNOSCUT_INCHIS
    assert canal_marketplace("eMAG") == ("emag", "marketplace")
    assert canal_marketplace("trendyol") == ("trendyol", "marketplace")
    assert canal_marketplace("shopify") is None and canal_marketplace(None) is None
    assert NECUNOSCUT_INCHIS in CANALE and "cel" in CANALE


def test_portal_are_canalul_lui():
    from canal_marketing import CANALE, PORTAL
    assert PORTAL in CANALE
