# /// script
# requires-python = ">=3.11"
# dependencies = ["lightgbm", "pandas", "numpy", "psycopg[binary]"]
# ///
"""
cod_refusal_score.py — scorează comenzile COD ÎN AȘTEPTARE (încă neexpediate) cu modelul
LightGBM antrenat → coadă de confirmare pentru CS (sună top-risc înainte de pickup).
Feature-urile = identice cu antrenarea; istoricul clientului (per telefon) din TOATE comenzile.
"""
import os, sys, argparse
import numpy as np, pandas as pd, psycopg, lightgbm as lgb

REFUSED = ("back_to_sender","refused","returning_to_sender","unsuccessful_delivery","incorrect_address")
COD_TOKENS = ("cod","ramburs","numerar","livrare","curier")
PENDING = ("waiting_for_courier","fulfilled","not_fulfilled","awaiting_shipment_generation_initialization","on_hold")
CAT = ["store_uid","courier_name","currency","ccode","province","city","zip"]
NUM = ["is_cod","total_price","subtotal_price","total_discounts","discount_ratio","log_price",
       "item_count","unique_sku_count","addr1_len","name_bad","dow","hour","month",
       "cust_prior_orders","cust_prior_refused","cust_prior_refuse_rate"]

SEL = """id, order_number, store_uid, courier_name, currency, payment_gateway,
    total_price, subtotal_price, total_discounts, item_count, unique_sku_count, frisbo_created_at,
    shipping_address->>'province' as province, shipping_address->>'city' as city,
    shipping_address->>'country_code' as ccode, shipping_address->>'zip' as zip,
    shipping_address->>'phone' as phone, shipping_address->>'name' as cname,
    shipping_address->>'last_name' as last_name,
    length(shipping_address->>'address1') as addr1_len, aggregated_status"""


def phone_norm(s):
    return s.fillna("").str.replace(r"\D","",regex=True).str[-9:]


def base_feats(df):
    pg = df["payment_gateway"].fillna("").str.lower()
    df["is_cod"] = pg.apply(lambda s:int(any(t in s for t in COD_TOKENS)))
    ln = df["last_name"].fillna("").str.strip()
    df["name_bad"] = ((ln.str.len()<2)|ln.isin(["-",".","_"])).astype(int)
    df["addr1_len"] = pd.to_numeric(df["addr1_len"],errors="coerce").fillna(0)
    for c in ["total_price","subtotal_price","total_discounts","item_count","unique_sku_count"]:
        df[c] = pd.to_numeric(df[c],errors="coerce").fillna(0.0)
    df["discount_ratio"] = df["total_discounts"]/df["total_price"].clip(lower=1)
    df["log_price"] = np.log1p(df["total_price"].clip(lower=0))
    ts = pd.to_datetime(df["frisbo_created_at"])
    df["dow"],df["hour"],df["month"] = ts.dt.dayofweek, ts.dt.hour, ts.dt.month
    df["phone_norm"] = phone_norm(df["phone"]); df.loc[df["phone_norm"].str.len()<9,"phone_norm"]=""
    return df


def _oh_tok():
    import subprocess
    for k in ("OH_PROFIT_CITIRE_TOKEN", "OH_CS_SB_TOKEN"):
        if os.environ.get(k):
            return os.environ[k]
    return subprocess.run(
        ["/root/.local/bin/uv", "run", "team-intelligence/plugins/core/scripts/kb.py", "secret-get", "OH_PROFIT_CITIRE_TOKEN"],
        capture_output=True, text=True).stdout.strip()


def oh_stari(luni):
    """{comandă: stare OH} pe lunile cerute (`/api/cs/livrabilitate`: starea din COLETE)."""
    import json, urllib.request
    tok, out = _oh_tok(), {}
    for luna in sorted(luni):
        req = urllib.request.Request("https://orderhub.arona.ro/api/cs/livrabilitate?luna=" + luna,
                                     headers={"Authorization": "Bearer " + tok, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=300) as r:
            d = json.loads(r.read().decode("utf-8"))
        ci = d["coloane"].index("comanda"); cs = d["coloane"].index("stare")
        out.update({x[ci]: x[cs] for x in d["randuri"]})
    return out


def oh_neplecate():
    import datetime as dt
    azi = dt.date.today()
    luni = {azi.strftime("%Y-%m"), (azi.replace(day=1) - dt.timedelta(days=1)).strftime("%Y-%m")}
    return {o for o, st in oh_stari(luni).items() if st == "Netrimisa"}


LUNI_ISTORIC_OH = 18


def istoric_oh(comenzi):
    """Istoricul pe telefon cu STAREA din Order Hub (S1, 24-oct-2026). `comenzi` = (order_number, phone, status AWBprint)
    pentru toate comenzile terminate sau nu; unde OH are comanda, starea lui bate AWBprint (Refuzata = refuz,
    Livrata = livrat, restul = încă nerezolvat, deci nu intră). Comenzile pe care OH nu le are (mai vechi decât
    istoricul OH) rămân pe AWBprint. Întoarce (hist, câte comenzi și-au schimbat eticheta)."""
    import datetime as dt
    azi = dt.date.today().replace(day=1)
    luni, z = set(), azi
    for _ in range(LUNI_ISTORIC_OH):
        luni.add(z.strftime("%Y-%m"))
        z = (z - dt.timedelta(days=1)).replace(day=1)
    st = oh_stari(luni)
    vechi = comenzi["aggregated_status"].isin(REFUSED).astype(int)
    oh = comenzi["order_number"].map(st)
    ref = np.where(oh == "Refuzata", 1, np.where(oh == "Livrata", 0, np.where(oh.isna(), vechi, -1)))
    terminat_vechi = comenzi["aggregated_status"].isin(("delivered",) + REFUSED)
    rezolvat = np.where(oh.isna(), terminat_vechi, ref >= 0)
    h = comenzi.assign(r=ref, n=1)[rezolvat]
    h = h.assign(r=h["r"].clip(lower=0))
    schimbate = int(((ref >= 0) & oh.notna() & (ref != vechi)).sum())
    return h[["phone", "n", "r"]], schimbate


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="data/cod_refusal_model.txt")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--out", default="data/cod_refusal_queue.csv")
    ap.add_argument("--doar-oh", action="store_true",
                    help="scorează DOAR comenzile pe care Order Hub le dă neplecate și neanulate (stare Netrimisa)")
    ap.add_argument("--istoric-oh", action="store_true",
                    help="istoricul de refuz pe telefon cu starea din Order Hub (S1), AWBprint doar unde OH n-are comanda")
    a = ap.parse_args()
    db = os.environ["DB"]
    model = lgb.Booster(model_file=a.model)

    with psycopg.connect(db) as c:
        if a.istoric_oh:
            comenzi = pd.read_sql("""select order_number, shipping_address->>'phone' as phone, aggregated_status
                from orders where shipping_address->>'phone' is not null""", c)
        else:
            # istoric per telefon din TOATE comenzile terminate
            hist = pd.read_sql(f"""select shipping_address->>'phone' as phone,
                count(*) n, sum(case when aggregated_status in {REFUSED} then 1 else 0 end) r
                from orders where aggregated_status in ('delivered',{str(REFUSED)[1:-1]})
                and shipping_address->>'phone' is not null group by 1""", c)
        pend = pd.read_sql(f"select {SEL} from orders where aggregated_status in {PENDING} and frisbo_created_at is not null", c)

    if a.doar_oh:
        # COADA DIN ORDER HUB (Orchestrator/owner, 22-oct-2026): AWBprint ține „în așteptare" comenzi plecate de mult
        # (5.247 erau deja la curier, 2.884 fără AWB vechi de ~247 de zile, 3.713 nici nu există în OH) — din 13.304
        # rămâneau ~800 reale. Lista de scorat = comenzile neplecate și neanulate din OH, luna curentă + precedenta;
        # trăsăturile rămân cele din AWBprint (identice cu antrenarea).
        vii = oh_neplecate()
        inainte = len(pend)
        pend = pend[pend["order_number"].isin(vii)]
        print(f"filtru Order Hub: {inainte:,} „în așteptare\" în AWBprint → {len(pend):,} neplecate în OH ({len(vii):,} în OH)")
    if pend.empty:
        print("Nicio comandă în așteptare de scorat."); return
    if a.istoric_oh:
        hist, schimbate = istoric_oh(comenzi)
        print(f"istoric pe telefon din Order Hub: {schimbate:,} comenzi și-au schimbat eticheta față de AWBprint")
    hist["phone_norm"] = phone_norm(hist["phone"])
    hist = hist[hist["phone_norm"].str.len()==9].groupby("phone_norm")[["n","r"]].sum()

    df = base_feats(pend)
    j = df.join(hist, on="phone_norm")
    df["cust_prior_orders"] = j["n"].fillna(0)
    df["cust_prior_refused"] = j["r"].fillna(0)
    df["cust_prior_refuse_rate"] = (df["cust_prior_refused"]/df["cust_prior_orders"].clip(lower=1)).fillna(0)
    for col in CAT: df[col] = df[col].astype("category")

    df["risk"] = model.predict(df[CAT+NUM])
    cod = df[df["is_cod"]==1].sort_values("risk",ascending=False)
    print(f"comenzi în așteptare: {len(df):,}  ·  din care COD: {len(cod):,}")
    print(f"risc mediu COD: {cod['risk'].mean()*100:.1f}%  ·  în top-20% risc: prag {cod['risk'].quantile(0.8)*100:.0f}%")

    show = cod.head(a.top)[["order_number","store_uid","cname","phone","city","province","total_price","currency","cust_prior_refused","risk"]]
    print(f"\n=== TOP {a.top} RISC (de sunat înainte de expediere) ===")
    for _,r in show.iterrows():
        print(f"  {r['risk']*100:>4.0f}%  {r['order_number']:<12} {str(r['cname'])[:22]:<22} {str(r['city'])[:16]:<16} "
              f"{r['total_price']:>7.0f}{r['currency']}  refuzuri_anterior={int(r['cust_prior_refused'])}")

    os.makedirs(os.path.dirname(a.out),exist_ok=True)
    cod[["order_number","store_uid","cname","phone","city","province","zip","total_price","currency",
         "cust_prior_orders","cust_prior_refused","risk"]].to_csv(a.out,index=False)
    print(f"\ncoadă completă salvată: {a.out} ({len(cod):,} comenzi COD)")


if __name__ == "__main__":
    try: sys.stdout.reconfigure(encoding="utf-8",errors="replace")
    except Exception: pass
    main()
