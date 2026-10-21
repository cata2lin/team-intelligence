"""`KB_GUARD_ASK=off` scoate DOAR intrebarile (ASK). DENY rămâne intreg.

Rulare: `python3 -m pytest test_kb_hook_guard_ask_off.py -q`, din folderul in care stau cele doua fisiere.

De ce exista testul: intrerupatorul atinge o garda de permisiuni. Daca ar sari si peste DENY — din graba sau
dintr-o refactorizare de maine — ar deschide exact comenzile blocate dinadins (DDL distructiv, DELETE si
UPDATE fara WHERE, dump de baza). Fiecare caz de mai jos a fost blocat cel putin o data in practica.

Testul rulează hook-ul ca proces separat, cu `HOME` si `CLAUDE_PLUGIN_ROOT` mutate in folderul lui: asa
verificam DOAR regulile built-in, fara sa depindem de ce reguli de echipa sunt sincronizate pe masina.
"""
import json
import os
import subprocess
import sys

AICI = os.path.dirname(os.path.abspath(__file__))
HOOK = os.path.join(AICI, "kb_hook_guard.py")

# comenzile periculoase se compun din bucati: fisierul ăsta e citit de oameni si de hook-uri, iar un literal
# intreg in sursa ar declansa singur gardianul la fiecare `grep` sau `cat` dintr-o comanda bash
RM_RF = "rm -" + "rf / --no-preserve-root"
DROP_DB = 'psql -c "' + "drop " + 'database order_hub"'
TRUNC = 'psql -c "' + "TRUNCATE " + 'TABLE orders"'
DD = "dd " + "if=/dev/zero of=/dev/sda"
UPDATE_OK = 'psql -c "UPDATE orders SET x=1 WHERE id=2"'
INSERT = 'psql -c "INSERT INTO t VALUES (1)"'
ALTER = 'psql -c "ALTER TABLE t ADD COLUMN c int"'
PUSH = "git push origin main"


def _ruleaza(cmd, env_extra=None):
    env = dict(os.environ)
    env.pop("KB_GUARD_ASK", None)
    env.update(env_extra or {})
    env["HOME"] = AICI
    env["CLAUDE_PLUGIN_ROOT"] = AICI
    p = subprocess.run([sys.executable, HOOK], input=json.dumps({"tool_input": {"command": cmd}}),
                       capture_output=True, text=True, env=env)
    assert p.returncode == 0, p.stderr
    if not p.stdout.strip():
        return None
    return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"]


def test_implicit_ask_ul_cere_aprobare():
    for cmd in (PUSH, UPDATE_OK, INSERT, ALTER):
        assert _ruleaza(cmd) == "ask", cmd


def test_cu_ask_off_nu_mai_cere_nimic_din_ask():
    off = {"KB_GUARD_ASK": "off"}
    for cmd in (PUSH, UPDATE_OK, INSERT, ALTER):
        assert _ruleaza(cmd, off) is None, cmd


def test_deny_ul_rămâne_si_cu_ask_off():
    """Partea care conteaza: intrerupatorul NU are voie sa atinga blocajele."""
    off = {"KB_GUARD_ASK": "off"}
    for cmd in (RM_RF, DROP_DB, TRUNC, DD):
        assert _ruleaza(cmd, off) == "deny", cmd
        assert _ruleaza(cmd) == "deny", cmd


def test_valori_neintelese_lasa_ask_ul_pornit():
    """Doar „off" opreste intrebarile; orice altceva (gol, „0", „false", o greseala de scriere) le lasa."""
    for val in ("", "0", "false", "no", "ON", "oFFF"):
        assert _ruleaza(PUSH, {"KB_GUARD_ASK": val}) == "ask", val
    for val in ("off", "OFF", " off "):
        assert _ruleaza(PUSH, {"KB_GUARD_ASK": val}) is None, val


def test_o_comanda_curata_trece_oricum():
    assert _ruleaza("ls -la") is None
    assert _ruleaza("ls -la", {"KB_GUARD_ASK": "off"}) is None
