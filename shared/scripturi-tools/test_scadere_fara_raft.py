"""scadere_fara_raft.calculeaza: doar masterele fără raft, fără parfumuri, fără MD / Duppo / Artevita."""
import scadere_fara_raft as S

LISTARI = {("rossi nails", "r203-020"): "m-rossi", ("rossi nails", "ha-0001"): "m-ha",
           ("maison d'esteban", "30"): "m-parf", ("rossi nails", "nub-01"): "m-nub"}
SKUS = {"m-rossi": {"r203-020"}, "m-ha": {"ha-0001", "ha-0001-2"}, "m-parf": {"30"}, "m-nub": {"nub-01"}}


def test_doar_fara_raft_si_fara_parfum():
    plecate = {"ROSSI1": ("ROSSI Nails", "shipped", [("r203-020", 2), ("ha-0001", 1), ("nub-01", 1), ("x", 1)]),
               "EST1": ("Maison d'Esteban", "delivered", [("30", 1)]),
               "DUP1": ("Duppo BG", "shipped", [("r203-020", 5)])}
    el, cnt, sarite = S.calculeaza(plecate, LISTARI, SKUS, in_scaner={"ha-0001-2"})
    assert el == [("ROSSI1", "r203-020", "m-rossi", 2, "ROSSI Nails", "shipped")]
    assert cnt["pe_raft"] == 1 and cnt["magazin_exclus"] == 2
    assert sarite["parfum"] == 1 and sarite["fara_master"] == 1


def test_kitul_rossi_se_desface_in_componente():
    listari = {("rossi nails", c): "m-" + c for c, _ in S.KITURI["kitpolygel-3"]}
    listari[("rossi nails", "r203-basecoat")] = "m-base"
    skus = {m: {s} for (_, s), m in listari.items()}
    plecate = {"R2": ("ROSSI Nails", "shipped", [("kitpolygel-3", 2), ("r196-1", 1)])}
    el, cnt, _ = S.calculeaza(plecate, listari, skus, in_scaner=set())
    got = {sku: q for _, sku, _, q, _, _ in el}
    assert got["r196-1"] == 3 and got["lampa-6w"] == 2 and "r196-top" not in got and "kitpolygel-3" not in got
    assert cnt["kit_desfacut"] == 2


def test_marketplace_doar_sku_cu_un_singur_master():
    listari = {("rossi nails", "r203-020"): "m1", ("trendyol", "r203-020"): "m1",
               ("grandia", "x"): "m2", ("bonhaus", "x"): "m3"}
    skus = {"m1": {"r203-020"}, "m2": {"x"}, "m3": {"x"}}
    plecate = {"MKT:trendyol:2026-10-25": ("marketplace:trendyol", "vandut", [("r203-020", 4), ("x", 1)])}
    el, _, sarite = S.calculeaza(plecate, listari, skus, in_scaner=set())
    assert [(s, m, q) for _, s, m, q, _, _ in el] == [("r203-020", "m1", 4)] and sarite["fara_master"] == 1
