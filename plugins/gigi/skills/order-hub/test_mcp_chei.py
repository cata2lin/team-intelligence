"""Ce unelte expune MCP-ul `arona-orderhub`, în funcție de CHEIA pe care o are mașina.

De ce (21-oct-2026): MCP-ul ajunge pe mașinile echipei. Cine are doar cheia de citire (`cs.citire`) nu trebuie să
vadă `oh_proba` / `oh_executa` — nu ca refuz după apăsare (403 de la Order Hub), ci deloc.

⚠️ Gardul e o CONVENȚIE, nu o barieră: `kb.py secret-get` nu filtrează secretele pe om, deci orice mașină cu
bootstrap-ul de KB poate lua cheia de scriere. De aceea testul verifică ORDINEA de alegere a cheii, nu o
imposibilitate — bariera reală ar fi scoaterea cheii comune din seif.

Rulare: `uv run --with "mcp>=1.2,<2" test_mcp_chei.py`
"""
import asyncio
import importlib.util
import os
import sys

AICI = os.path.dirname(os.path.abspath(__file__))
_n = [0]


def _incarca(env, seif):
    """Încarcă modulul cu env-ul dat și cu seiful fals `seif` ({cheie: valoare})."""
    for k in ("OH_CS_SB_TOKEN", "OH_TEAM_READ_TOKEN"):
        os.environ.pop(k, None)
    os.environ.update(env)
    _n[0] += 1
    spec = importlib.util.spec_from_file_location("mcp_srv_%d" % _n[0], os.path.join(AICI, "mcp_server.py"))
    mod = importlib.util.module_from_spec(spec)
    mod.__dict__["_SEIF_FALS"] = seif
    # `_kb` și `whoami` se înlocuiesc ÎNAINTE de execuția modulului nu se poate (modulul le definește), deci
    # punem un hook: executăm modulul cu subprocess.run înlocuit
    import subprocess as sp

    class _R:
        def __init__(self, out):
            self.stdout, self.stderr, self.returncode = out, "", 0

    def _run(cmd, **kw):
        if "whoami" in cmd:
            return _R("employee: %s | id: 9 | host: test" % seif.get("_handle", "anne"))
        if "secret-get" in cmd:
            return _R(seif.get(cmd[-1], ""))
        return _R("")
    vechi = sp.run
    sp.run = _run
    try:
        spec.loader.exec_module(mod)
    finally:
        sp.run = vechi
    return mod


def _unelte(mod):
    return sorted(x.name for x in asyncio.run(mod.mcp.list_tools()))


SCRIERE = {"oh_proba", "oh_executa"}
CITIRE = {"oh_comanda", "oh_actiuni", "oh_operatie", "oh_rata_livrare", "oh_neplecate_pe_sku"}


def test_env_cu_cheia_cs_da_scrierea():
    m = _incarca({"OH_CS_SB_TOKEN": "ohsvc.cs-sb." + "x" * 40}, {})
    assert m.POATE_SCRIE is True
    assert SCRIERE <= set(_unelte(m))


def test_cheia_omului_din_seif_da_scrierea_si_e_preferata_celei_comune():
    m = _incarca({}, {"_handle": "anne", "OH_CS_TOKEN_ANNE": "ohsvc.anne." + "a" * 40,
                      "OH_CS_SB_TOKEN": "ohsvc.cs-sb." + "x" * 40})
    assert m.POATE_SCRIE is True
    assert m._TOKEN.startswith("ohsvc.anne."), "cheia OMULUI bate cheia comună (istoricul comenzii)"


def test_fara_cheia_omului_cade_pe_cea_comuna():
    m = _incarca({}, {"_handle": "andreea", "OH_CS_SB_TOKEN": "ohsvc.cs-sb." + "x" * 40})
    assert m.POATE_SCRIE is True and m._TOKEN.startswith("ohsvc.cs-sb.")


def test_doar_cheia_de_citire_ascunde_uneltele_de_scriere():
    m = _incarca({}, {"_handle": "oana", "OH_TEAM_READ_TOKEN": "ohsvc.echipa-citire." + "z" * 40})
    assert m.POATE_SCRIE is False
    u = set(_unelte(m))
    assert not (SCRIERE & u), "scrierea NU are voie să apară: %s" % sorted(SCRIERE & u)
    assert CITIRE <= u, "citirile trebuie să rămână toate"


def test_fara_nicio_cheie_serverul_tot_porneste_cu_citirile():
    """Fără cheie, uneltele de citire există și spun singure eroarea la apel — nu crăpăm la pornire."""
    m = _incarca({}, {"_handle": "nimeni"})
    assert m.POATE_SCRIE is False and CITIRE <= set(_unelte(m))


if __name__ == "__main__":
    for nume, f in sorted(globals().items()):
        if nume.startswith("test_"):
            f()
            print("ok", nume)
    print("TOATE TRECUTE")
