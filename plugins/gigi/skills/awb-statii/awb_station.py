"""Etichete AWB grupate pe STATIE -> un PDF per statie, trimis pe grupul AWB.
Reutilizeaza masinaria din awb_fast.py (resolve/merge), doar numele fisierului difera."""
import sys, os, time, urllib.request, urllib.parse, subprocess
sys.path.insert(0, "/root/Scripturi")
import awb_fast as A

def build_and_send(eticheta, orders):
    A.prefetch(orders)
    bucati, sarite = [], []
    for od in orders:
        sh, xc, o, doc, trk, n = A.resolve(od)
        if not o or not doc:
            sarite.append(od); continue
        url = doc.get("url") or doc.get("awbPdfUrl") or (
            A.X.XBASE + "/api/document/shipping-label?connectorId=%s&trackingNumber=%s"
            % (doc.get("connectorId"), urllib.parse.quote(str(trk or ""))))
        try:
            with urllib.request.urlopen(url, timeout=90) as r: pdf = r.read()
        except Exception as e:
            sarite.append("%s(dl:%s)" % (od, str(e)[:25])); continue
        if not pdf.startswith(b"%PDF-"):
            sarite.append("%s(nu-e-pdf)" % od); continue
        bucati.append((od.upper(), pdf))
    if not bucati:
        print("%s: nimic de trimis" % eticheta); return
    nume = "%s-%d-AWB-%s.pdf" % (eticheta, len(bucati), time.strftime("%d.%m"))
    path = os.path.join(A.WA_DIR, nume)
    open(path, "wb").write(A._merge_pdfs(bucati) if len(bucati) > 1 else bucati[0][1])
    if os.environ.get("NO_SEND"):
        print("%s -> CONSTRUIT, netrimis  (%d etichete%s)  %s" % (
            nume, len(bucati), ", sarite: " + ", ".join(sarite) if sarite else "", path))
        return
    r = subprocess.run(["/usr/bin/node", "wa_send.js", A.WA_GROUP, "doc", path, ""],
                       cwd=A.WA_DIR, capture_output=True, text=True, timeout=300)
    out = (r.stdout or "") + (r.stderr or "")
    print("%s -> %s  (%d etichete%s)" % (
        nume, "TRIMIS" if "SENT " in out else "ESUAT " + out.strip()[-70:],
        len(bucati), ", sarite: " + ", ".join(sarite) if sarite else ""))

if __name__ == "__main__":
    build_and_send(sys.argv[1], sys.argv[2].split(","))
