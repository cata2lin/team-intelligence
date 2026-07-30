#!/usr/bin/env python3
"""📕 RULEBOOK — UN SINGUR LOC pentru dicționarele de corecție adrese RO (ce EXTINDEM des).
Importat de `xconnector.py` (preclean) și `address_nomenclator.py` (matching). Ca să adaugi o regulă nouă
(o abreviere de oraș, o stradă cu inițiale, un nume clasic, un sufix de declinare) → o pui AICI, într-un singur loc.

Reguli-cheie separate de dicționare (rămân în cod, dar documentate aici pt orientare):
  · deglue spațiere/typo (nr330, 5sc2, sect, multi-puncte, ı, Bl/bvd→Bulevardul, 1dec) → xconnector `_street_deglue`/`_expand_street_abbrev`
  · bloc/scară/ap din stradă → address2 → xconnector `_pull_block_details`  (owner: „le lași în A2 pt curier")
  · landmark în fața străzii → address2 → xconnector `_pull_artery_prefix`
  · localitate scrisă înainte de stradă → `loc_hint` (xconnector) → nomenclator
  · rural (sat cu zip unic) / fuzzy localitate (_lev1/_locmatch/_foldloc) / substring-gazetteer (deglue localitate lipită)
    / declinare stradă (_destem) / i↔â (_foldi/_foldloc) → address_nomenclator
  · fallback: HERE fără-tip (owner „scoți prefixul de stradă") + istoric comandă LIVRATĂ → xconnector gate
"""

# ── Abrevieri de ORAȘ → nume oficial (adaugă orașe/indicative aici). Cheia = folded (fără diacritice, lowercase). ──
CITY_ABBREV = {
    "dr tr severin": "Drobeta-Turnu Severin", "dr t severin": "Drobeta-Turnu Severin",
    "drobeta tr severin": "Drobeta-Turnu Severin", "tr severin": "Drobeta-Turnu Severin",
    "m ciuc": "Miercurea Ciuc", "mc ciuc": "Miercurea Ciuc", "mercurea ciuc": "Miercurea Ciuc",
    "tg mures": "Târgu Mureș", "tg jiu": "Târgu Jiu", "tg neamt": "Târgu Neamț", "tg secuiesc": "Târgu Secuiesc",
    "tg ocna": "Târgu Ocna", "tg frumos": "Târgu Frumos", "tg lapus": "Târgu Lăpuș",
    "tg carbunesti": "Târgu Cărbunești", "tg bujor": "Târgu Bujor",
    "rm valcea": "Râmnicu Vâlcea", "rm vilcea": "Râmnicu Vâlcea", "rm vl": "Râmnicu Vâlcea", "rmvl": "Râmnicu Vâlcea", "rm sarat": "Râmnicu Sărat",
    "sf gheorghe": "Sfântu Gheorghe", "p neamt": "Piatra Neamț", "b mare": "Baia Mare",
    "c de arges": "Curtea de Argeș", "c-lung": "Câmpulung", "c lung": "Câmpulung",
    "od secuiesc": "Odorheiu Secuiesc", "odorhei": "Odorheiu Secuiesc",
    # nume UNGUREȘTI de localități (Ținutul Secuiesc + Transilvania) → oficial ROMÂNESC (fold scoate á→a, é→e, ő→o, ű→u, ö→o, ü→u).
    "szekelyudvarhely": "Odorheiu Secuiesc", "marosvasarhely": "Târgu Mureș", "csikszereda": "Miercurea Ciuc",
    "sepsiszentgyorgy": "Sfântu Gheorghe", "kezdivasarhely": "Târgu Secuiesc", "gyergyoszentmiklos": "Gheorgheni",
    "szekelykeresztur": "Cristuru Secuiesc", "kolozsvar": "Cluj-Napoca", "nagyvarad": "Oradea",
    "szatmarnemeti": "Satu Mare", "nagyszeben": "Sibiu", "brasso": "Brașov", "segesvar": "Sighișoara",
    "gyulafehervar": "Alba Iulia", "beszterce": "Bistrița", "nagybanya": "Baia Mare", "szentegyhaza": "Vlăhița",
    "barot": "Baraolt", "kovaszna": "Covasna", "szovata": "Sovata", "tusnadfurdo": "Băile Tușnad",
    "balanbanya": "Bălan", "parajd": "Praid", "korond": "Corund", "marosheviz": "Toplița",
    "gyergyoditro": "Ditrău", "temesvar": "Timișoara",
    # indicative auto folosite ca oraș — doar cele NEAMBIGUE spre reședință
    "cta": "Constanța", "ct": "Constanța", "tgv": "Târgoviște", "rmv": "Râmnicu Vâlcea", "sm": "Satu Mare",
    "buc": "București", "buc.": "București",
}

# ── Abrevieri de CUVÂNT în stradă (whole-word, neambigue). ──
ST_ABBREV = {"ctin": "Constantin", "c-tin": "Constantin", "gral": "General", "g-ral": "General",
             "cpt": "Căpitan", "serg": "Sergent", "acad": "Academician"}

# ── Străzi cu INIȚIALĂ de prenume (forma degluată, spațiu-separată) → nume complet. Cheie = 'inițiale nume' folded. ──
STREET_INITIAL = {
    "n titulescu": "Nicolae Titulescu", "c brancoveanu": "Constantin Brâncoveanu", "al lapusneanu": "Alexandru Lăpușneanu",
    "n balcescu": "Nicolae Bălcescu", "n iorga": "Nicolae Iorga", "n grigorescu": "Nicolae Grigorescu",
    "m eminescu": "Mihai Eminescu", "i creanga": "Ion Creangă", "g cosbuc": "George Coșbuc", "g enescu": "George Enescu",
    "v alecsandri": "Vasile Alecsandri", "a vlaicu": "Aurel Vlaicu", "t vladimirescu": "Tudor Vladimirescu",
    "m kogalniceanu": "Mihail Kogălniceanu", "c coposu": "Corneliu Coposu", "b delavrancea": "Barbu Delavrancea",
    "a i cuza": "Alexandru Ioan Cuza", "i c bratianu": "Ion C. Brătianu", "a saguna": "Andrei Șaguna",
}

# ── Străzi clasice cu INIȚIALE cu punct (A.I.Cuza). Cheie = folded fără punct/spații. ──
STREET_FULL = {
    "aicuza": "Alexandru Ioan Cuza", "icbratianu": "Ion C. Brătianu", "cibratianu": "Ion C. Brătianu",
    "ilcaragiale": "Ion Luca Caragiale",
    "nbalcescu": "Nicolae Bălcescu", "ghdoja": "Gheorghe Doja", "ghlazar": "Gheorghe Lazăr",
    "mkogalniceanu": "Mihail Kogălniceanu", "avlaicu": "Aurel Vlaicu", "cbrancusi": "Constantin Brâncuși",
    "genescu": "George Enescu", "bphasdeu": "Bogdan Petriceicu Hasdeu", "gcosbuc": "George Coșbuc",
    "vparvan": "Vasile Pârvan", "aivlaicu": "Aurel Vlaicu", "ghbaritiu": "George Barițiu",
}

# ── Sufixe de DECLINARE/articol RO pt matching stradă (cel mai lung întâi). Sebesi≡Sebeșului, Gara≡Gării. ──
DECL_SUF = ("urilor", "ilor", "elor", "ului", "lui", "lor", "ale", "uri", "ii", "ei", "ul", "u", "a", "e", "i")
