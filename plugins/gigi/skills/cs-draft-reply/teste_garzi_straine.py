# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Teste de REGRESIE pentru gărzile anti-halucinare pe PIEȚELE STRĂINE (bg/cz/sk/pl/hu/hr).

Rulează OFFLINE, pe propoziții SINTETICE (niciun dat real de client, niciun număr de comandă).
Acoperă exact clasele găsite lipsă la măsurarea pe corpus HELD-OUT (98 de site-uri publice de pe
cele șase piețe, niciunul citat în comentariile din `cs_auto_draft.py`):

  1. PREȚ cu 4+ cifre fără separator de mii („1399 Kč", „1990 Ft", „1599 zł") — clasa cea mai
     numeroasă de prețuri reale, ratată complet înainte;
  2. STATUS la diateza ACTIVĂ (persoana I plural și a III-a) — gărzile prindeau doar pasivul;
  3. STATUS la trecut/copulă cu cuvinte între auxiliar și participiu, plus formele NEGATE;
  4. TERMENE în ore scrise lipit („24h") sau fără prepoziția „od".

Și, la fel de important, ce NU trebuie să prindă: program de lucru, sloganuri fără obiect,
politica de livrare/retur în română.

  uv run teste_garzi_straine.py
"""
import importlib.util, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
MOTOR = os.path.join(HERE, "cs_auto_draft.py")


def incarca():
    spec = importlib.util.spec_from_file_location("motor_garzi", MOTOR)
    m = importlib.util.module_from_spec(spec)
    sys.modules["motor_garzi"] = m
    spec.loader.exec_module(m)
    return m


def prins(m, text, clasa):
    hits = m.hallu_hits(text, ctx="", inc_blk="")
    if clasa == "pret":
        return any(h.startswith("preț inventat") for h in hits)
    return any(not h.startswith(("preț inventat", "telefon inventat", "dimensiune inventată"))
               for h in hits)


TREBUIE_PRINSE = [
    ("pret", "Cena je 1399 Kč."),
    ("pret", "A szállítási díj 1990 Ft."),
    ("pret", "Koszt dostawy to 1599 zł."),
    ("pret", "Стойността е 120 лева."),
    ("pret", "Cijena je 10 kuna."),
    ("pret", "Suma este BGN 10 000."),
    ("pret", "Doprava stojí 109.- Kč."),
    ("pret", "Doprava zadarmo nad 70 €¹."),
    ("status", "Изпратихме поръчката ви вчера."),
    ("status", "Куриерът ще достави поръчката утре."),
    ("status", "Поръчките бяха изпратени."),
    ("status", "Objednávku jsme odeslali."),
    ("status", "Zboží vám odešleme ještě dnes."),
    ("status", "Objednávka byla již odeslána."),
    ("status", "Tovar vám odošleme zajtra."),
    ("status", "Vaša objednávka nebola odoslaná."),
    ("status", "Wyślemy zamówienie jutro."),
    ("status", "Przekazaliśmy przesyłkę kurierowi."),
    ("status", "Zamówienie nie zostało jeszcze wysłane."),
    ("status", "Paket šaljemo danas."),
    ("status", "Naručene artikle dostavljamo danas."),
    ("status", "A megrendelést holnap kiszállítjuk."),
    ("ore", "Paket stiže unutar 24h."),
    ("ore", "Pošiljku šaljemo u roku 24 sata."),
]

NU_TREBUIE_PRINSE = [
    ("status", "Radno vrijeme je od 9 do 20 sati."),
    ("ore", "Ügyfélszolgálat hétfőtől péntekig 8-18 óráig."),
    ("ore", "Narudžbe zaprimljene nakon 15:00 sati obrađujemo ujutro."),
    ("status", "Изпращаме бързо и сигурно."),
    ("status", "Мога ли да анулирам направена поръчка?"),
    ("status", "Wysyłamy szybko i bezpiecznie."),
    ("status", "Zboží je skladem."),
    ("pret", "Doprava zdarma."),
    ("status", "Livrarea standard se face în 1-3 zile lucrătoare."),
    ("status", "Rambursarea se face în 14 zile de la returnarea coletului."),
]


def main():
    m = incarca()
    rateaza, fals = [], []
    for clasa, text in TREBUIE_PRINSE:
        if not prins(m, text, clasa):
            rateaza.append((clasa, text))
    for clasa, text in NU_TREBUIE_PRINSE:
        if prins(m, text, clasa):
            fals.append((clasa, text))
    total = len(TREBUIE_PRINSE) + len(NU_TREBUIE_PRINSE)
    for clasa, text in rateaza:
        print("❌ RATAT   [%s] %s" % (clasa, text))
    for clasa, text in fals:
        print("❌ FALS-POZITIV [%s] %s" % (clasa, text))
    trecute = total - len(rateaza) - len(fals)
    print("\n%d/%d teste trecute" % (trecute, total))
    return 1 if (rateaza or fals) else 0


if __name__ == "__main__":
    sys.exit(main())
