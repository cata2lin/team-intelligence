# -*- coding: utf-8 -*-
import json, datetime, pathlib, re, html as _html
DIR=pathlib.Path("/root/Scripturi/data/briefing"); ST=DIR/"alerts_state.json"; MD=DIR/"alerts.md"
def _key(s): return re.sub(r"\d+","#",(s or "")).strip()[:80]
def _txt(body):
    if not isinstance(body,str): body=""
    t=_html.unescape(re.sub(r"<[^>]+>"," ",body))
    ls=[l.strip() for l in re.split(r"[\n\r]+",t) if l.strip()]
    return " ".join(ls)[:200]
def scrie(subiect, corp=""):
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        today=datetime.date.today().isoformat(); now=datetime.datetime.now().strftime("%H:%M")
        try: st=json.loads(ST.read_text(encoding="utf-8"))
        except Exception: st={}
        if st.get("_date")!=today: st={"_date":today}
        k=_key(subiect); body=_txt(corp)
        if k in st:
            st[k]["last"]=now; st[k]["n"]=st[k].get("n",1)+1; st[k]["subiect"]=subiect
            if body: st[k]["corp"]=body
        else:
            st[k]={"first":now,"last":now,"n":1,"subiect":subiect,"corp":body}
        ST.write_text(json.dumps(st,ensure_ascii=False), encoding="utf-8")
        items=sorted((v for kk,v in st.items() if kk!="_date"), key=lambda v: v["last"], reverse=True)
        out=[]
        for v in items:
            rep=(" (x%d, ultima %s)"%(v["n"],v["last"])) if v.get("n",1)>1 else (" (%s)"%v["last"])
            ln="- **%s**%s"%(v["subiect"],rep)
            if v.get("corp"): ln+=" - "+v["corp"]
            out.append(ln)
        MD.write_text(("\n".join(out)+"\n") if out else "", encoding="utf-8")
    except Exception:
        pass
