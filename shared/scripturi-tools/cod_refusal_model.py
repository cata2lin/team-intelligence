# /// script
# requires-python = ">=3.11"
# dependencies = ["lightgbm", "pandas", "numpy", "scikit-learn", "psycopg[binary]"]
# ///
"""
cod_refusal_model.py — model LightGBM de PREDICȚIE a refuzului COD pe date AWBprint.

Label: aggregated_status 'delivered' (0) vs {back_to_sender, refused, returning_to_sender,
unsuccessful_delivery, incorrect_address} (1). Split TEMPORAL (train pe trecut, test pe ultimele
săptămâni) = simulează predicția reală. Feature cheie: istoricul de refuz per client (telefon).
"""
import os, sys, argparse
import numpy as np
import pandas as pd
import psycopg
import lightgbm as lgb
from sklearn.metrics import roc_auc_score, average_precision_score

REFUSED = ("back_to_sender", "refused", "returning_to_sender", "unsuccessful_delivery", "incorrect_address")
COD_TOKENS = ("cod", "ramburs", "numerar", "livrare", "curier")  # payment_gateway → COD


def load(db, test_from):
    sql = f"""
        select id, order_number, store_uid, courier_name, currency, payment_gateway,
               total_price, subtotal_price, total_discounts,
               item_count, unique_sku_count, package_count, package_weight, transport_cost,
               frisbo_created_at,
               shipping_address->>'province'    as province,
               shipping_address->>'city'        as city,
               shipping_address->>'country_code' as ccode,
               shipping_address->>'zip'         as zip,
               shipping_address->>'phone'       as phone,
               shipping_address->>'last_name'   as last_name,
               length(shipping_address->>'address1') as addr1_len,
               aggregated_status
        from orders
        where aggregated_status in ('delivered','back_to_sender','refused',
              'returning_to_sender','unsuccessful_delivery','incorrect_address')
          and frisbo_created_at is not null
          -- DOAR RON (Orchestrator/owner, 22-oct-2026): statusurile internaționale din AWBprint sunt înghețate din
          -- 27-sep, deci etichetele intl (refuzat/livrat) mint. Până la etichetele din Order Hub, modelul învață doar RO.
          and coalesce(currency, RON) = RON
    """
    with psycopg.connect(db) as c:
        df = pd.read_sql(sql, c)
    print(f"încărcate {len(df):,} comenzi terminate")
    return df


def _oh_stare_pe_luni(months):
    """{order_number: stare} din OH /api/cs/livrabilitate pentru lunile cerute. Gol daca OH pica."""
    import os, json, subprocess, urllib.request
    tok = os.environ.get("OH_CS_SB_TOKEN") or ""
    if not tok:
        try:
            tok = subprocess.run(["/root/.local/bin/uv", "run",
                "/root/Scripturi/team-intelligence/plugins/core/scripts/kb.py", "secret-get", "OH_CS_SB_TOKEN"],
                capture_output=True, text=True, timeout=60).stdout.strip()
        except Exception:
            tok = ""
    out = {}
    baza = os.environ.get("OH_CS_BASE", "https://orderhub.arona.ro").rstrip("/")
    for m in months:
        req = urllib.request.Request(baza + "/api/cs/livrabilitate?luna=" + m,
            headers={"Authorization": "Bearer " + tok, "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                d = json.loads(r.read().decode("utf-8"))
        except Exception:
            continue
        ci = {c: i for i, c in enumerate(d.get("coloane") or [])}
        for row in d.get("randuri") or []:
            out[str(row[ci["comanda"]]).strip()] = row[ci["stare"]]
    return out


def _relabel_oh(df):
    """Label din Order Hub (colete): Refuzata=1, Livrata=0; pastreaza DOAR comenzile rezolvate in OH.
    Semnatura S1 — comutat de profit_settings.status_sursa='oh'. Fail-safe: apelantul prinde exceptia."""
    import pandas as _pd
    months = sorted({str(x)[:7] for x in df["frisbo_created_at"].dropna().astype(str)})
    oh = _oh_stare_pe_luni(months)
    if not oh:
        raise RuntimeError("OH livrabilitate gol")
    on = df["order_number"].fillna("").astype(str).str.strip()
    stare = on.map(oh)
    keep = stare.isin(["Livrata", "Refuzata"])
    before, after = len(df), int(keep.sum())
    df = df.loc[keep].copy()
    df["label"] = (stare.loc[keep] == "Refuzata").astype(int)
    print(f"OH relabel: {after:,}/{before:,} comenzi rezolvate in OH (refuz {df['label'].mean()*100:.1f}%)")
    return df


def feats(df):
    df = df.sort_values("frisbo_created_at").reset_index(drop=True)
    df["label"] = df["aggregated_status"].isin(REFUSED).astype(int)
    try:
        import sqlite3 as _sq
        _cx = _sq.connect("/root/Scripturi/data/profitability.db")
        _ss = (_cx.execute("SELECT value FROM profit_settings WHERE key='status_sursa'").fetchone() or ["awbprint"])[0]
        _cx.close()
    except Exception:
        _ss = "awbprint"
    if str(_ss).lower() == "oh":
        try:
            df = _relabel_oh(df)
        except Exception as _e:
            print("OH relabel esuat (%s) — ramas pe AWBprint" % str(_e)[:90])
    pg = df["payment_gateway"].fillna("").str.lower()
    df["is_cod"] = pg.apply(lambda s: int(any(t in s for t in COD_TOKENS))).astype(int)
    # phone normalizat = ultimele 9 cifre (identitate client)
    df["phone_norm"] = df["phone"].fillna("").str.replace(r"\D", "", regex=True).str[-9:]
    df.loc[df["phone_norm"].str.len() < 9, "phone_norm"] = ""
    # NB (verificat 31-iul-2026): phone_valid (phonenumbers) + addr_has_number/complete (libpostal)
    # NU cresc AUC (0.686→0.687) — zip/city + istoricul clientului + addr1_len prind deja semnalul. Excluse.
    # calitate nume/adresă
    ln = df["last_name"].fillna("").str.strip()
    df["name_bad"] = ((ln.str.len() < 2) | ln.isin(["-", ".", "_"])).astype(int)
    df["addr1_len"] = df["addr1_len"].fillna(0)
    # numeric
    for col in ["total_price","subtotal_price","total_discounts","item_count",
                "unique_sku_count","package_count","package_weight","transport_cost"]:
        df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    df["discount_ratio"] = df["total_discounts"] / df["total_price"].clip(lower=1)
    df["log_price"] = np.log1p(df["total_price"].clip(lower=0))
    # timp
    ts = pd.to_datetime(df["frisbo_created_at"])
    df["dow"] = ts.dt.dayofweek
    df["hour"] = ts.dt.hour
    df["month"] = ts.dt.month
    # === ISTORIC CLIENT (expanding, shift → fără leakage) ===
    g = df.groupby("phone_norm", sort=False)
    prior_orders = g.cumcount()                                   # câte comenzi ÎNAINTE
    ref_cum = g["label"].cumsum() - df["label"]                   # câte refuzuri ÎNAINTE
    df["cust_prior_orders"] = prior_orders
    df["cust_prior_refused"] = ref_cum
    df["cust_prior_refuse_rate"] = (ref_cum / prior_orders.clip(lower=1)).fillna(0)
    # clienții fără telefon valid → istoric 0 (neutru)
    noid = df["phone_norm"] == ""
    df.loc[noid, ["cust_prior_orders","cust_prior_refused","cust_prior_refuse_rate"]] = 0
    return df


CAT = ["store_uid","courier_name","currency","ccode","province","city","zip"]
# NB: transport_cost/package_count/package_weight EXCLUSE — sunt post-fulfillment/leaky
# (transport_cost include turul de retur → scurge label-ul; nu le ai la predicție, înainte de expediere)
NUM = ["is_cod","total_price","subtotal_price","total_discounts","discount_ratio","log_price",
       "item_count","unique_sku_count",
       "addr1_len","name_bad","dow","hour","month",
       "cust_prior_orders","cust_prior_refused","cust_prior_refuse_rate"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test-from", default="2026-06-15")
    ap.add_argument("--out", default="data/cod_refusal_model.txt")
    a = ap.parse_args()

    db = os.environ["DB"]
    df = feats(load(db, a.test_from))
    print(f"rată refuz globală: {df['label'].mean()*100:.1f}%  ·  COD: {df['is_cod'].mean()*100:.1f}% din comenzi")

    for col in CAT:
        df[col] = df[col].astype("category")

    cut = pd.Timestamp(a.test_from)
    tr = df[pd.to_datetime(df["frisbo_created_at"]) < cut]
    te = df[pd.to_datetime(df["frisbo_created_at"]) >= cut]
    print(f"train: {len(tr):,} (refuz {tr['label'].mean()*100:.1f}%)  ·  test: {len(te):,} (refuz {te['label'].mean()*100:.1f}%)")

    Xtr, ytr = tr[CAT+NUM], tr["label"]
    Xte, yte = te[CAT+NUM], te["label"]
    dtr = lgb.Dataset(Xtr, label=ytr, categorical_feature=CAT, free_raw_data=False)
    params = dict(objective="binary", metric="auc", learning_rate=0.05, num_leaves=64,
                  min_child_samples=200, feature_fraction=0.8, bagging_fraction=0.8,
                  bagging_freq=1, verbose=-1)
    model = lgb.train(params, dtr, num_boost_round=400)

    p = model.predict(Xte)
    auc = roc_auc_score(yte, p); ap_ = average_precision_score(yte, p)
    print(f"\n=== EVALUARE (holdout temporal) ===")
    print(f"AUC = {auc:.3f}  ·  PR-AUC = {ap_:.3f}  ·  baseline refuz = {yte.mean()*100:.1f}%")

    # valoare operațională: dacă CS confirmă top-K% cei mai riscanți, ce % din refuzuri prinde
    te2 = te.copy(); te2["p"] = p
    te2 = te2.sort_values("p", ascending=False).reset_index(drop=True)
    tot_ref = te2["label"].sum()
    print(f"\n=== Coadă de confirmare (test, {len(te2):,} comenzi, {int(tot_ref):,} refuzuri) ===")
    print("top-K% riscante | comenzi de sunat | refuzuri prinse (recall) | precizie în coadă | lift")
    for k in (0.05, 0.10, 0.20, 0.30):
        n = int(len(te2)*k)
        head = te2.head(n)
        caught = head["label"].sum()
        recall = caught/tot_ref*100
        prec = head["label"].mean()*100
        lift = (head["label"].mean())/(te2["label"].mean())
        print(f"  {int(k*100):>3}%          | {n:>7,}         | {recall:>5.1f}% ({int(caught):,})        | {prec:>4.1f}%          | {lift:.1f}x")

    imp = pd.DataFrame({"f": model.feature_name(), "imp": model.feature_importance(importance_type="gain")})
    imp = imp.sort_values("imp", ascending=False).head(15)
    print("\n=== Top 15 feature-uri (gain) ===")
    for _, r in imp.iterrows():
        print(f"  {r['f']:<24} {r['imp']:>12,.0f}")

    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    model.save_model(a.out)
    print(f"\nmodel salvat: {a.out}")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception: pass
    main()
