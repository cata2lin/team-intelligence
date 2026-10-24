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
