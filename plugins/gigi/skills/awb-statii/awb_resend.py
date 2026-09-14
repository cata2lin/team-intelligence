"""Retrimite lotul de azi CURATAT: doar coletele neplecate, cate un PDF pe statie."""
import sys, os
sys.path.insert(0, "/root/Scripturi")
import awb_fast as A, awb_zilnic as Z, awb_station as S, importlib.util as il
sp = il.spec_from_file_location("pq", "/root/Scripturi/team-intelligence/plugins/gigi/skills/print-queue/print_queue.py")
PQ = il.module_from_spec(sp); sp.loader.exec_module(PQ)

ORD = sys.argv[1].split(",")
A.prefetch(ORD)
info = []
for od in ORD:
    sh, xc, o, doc, trk, n = A.resolve(od)
    if not o:
        print("  %s negasit" % od); continue
    sku, _ = PQ.primary(o)
    dep = PQ.category(sku) in PQ.DEPOZIT_CATS or PQ._is_lavete(sku)
    info.append((od, trk, "BARTOLOMEU" if dep else "UZINA2"))

stare = Z.stare_curier([t for _o, t, _s in info])
lot = {"BARTOLOMEU": [], "UZINA2": []}
plecate = []
for od, trk, st_ in info:
    s = stare.get(str(trk).split("-")[0]) if trk else None
    if s in ("in_transit", "delivered", "returned", "refused"):
        plecate.append("%s(%s)" % (od, s)); continue
    lot[st_].append(od)
print("plecate, NU le retrimit: %d — %s" % (len(plecate), ", ".join(plecate)))
for k, v in lot.items():
    print("%-11s %d — %s" % (k, len(v), ", ".join(v)))
if os.environ.get("NO_SEND"):
    sys.exit(0)
for k, v in lot.items():
    if v:
        S.build_and_send(k, v)
