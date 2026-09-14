#!/usr/bin/env bash
# wa.sh — puntea WhatsApp, o singura usa de intrare.
#
# DE CE EXISTA: puntea suporta UN SINGUR socket pe `auth`. Orice al doilea proces care deschide
# sesiune o evacueaza pe prima (`CLOSED sc=440`) si mesajele cad cu „Connection Closed". S-a
# intamplat in productie: patru bucle pornite simultan + listener-ul = nimic nu pleca.
# Regula: NIMENI nu deschide socket propriu. Totul trece pe aici, care stie sa verifice intai
# daca exista deja o sesiune si sa se aseze la coada in loc sa concureze.
#
#   wa.sh status                     ce sesiune e vie si pe ce grupuri asculta
#   wa.sh find <text>                cauta grupuri dupa nume
#   wa.sh join <text|jid>            intra pe un grup (fara sa repornesti nimic)
#   wa.sh leave <text|jid>           iesi de pe un grup
#   wa.sh listen [minute]            porneste ascultarea (0 = fara termen)
#   wa.sh stop                       opreste ascultarea
#   wa.sh say <grup> [--raw] <mesaj> trimite un mesaj (marcat cu robotel; --raw = nemarcat)
#   wa.sh doc <text|jid> <pdf> [cap] trimite un PDF (nu-l retrimite daca a plecat deja)
#   wa.sh read [n]                   ultimele n mesaje (cu #numar, ca sa le poti tinti)
#   wa.sh react <#nr> <emoji>        pune reactie (emoji gol = scoate reactia)
#   wa.sh edit  <#nr> <text nou>     editeaza (doar mesajele noastre, ~15 min)
#   wa.sh del   <#nr>                sterge pt toata lumea (ale noastre; ale altora doar ca admin)
#   wa.sh reply <#nr> <text>         raspunde CITAND mesajul ala
#   wa.sh fwd   <#nr> <grup|numar>   trimite mai departe mesajul
#   wa.sh get   <#nr> [folder]       descarca media din mesaj (poza/video/voice/document)
#   wa.sh poll  <grup> "Intrebare" op1 op2 ...   sondaj
#   wa.sh seen  <#nr>                marcheaza mesajul citit
#   wa.sh typing <grup> [on|off]     indicatorul "scrie..."
#   wa.sh members <grup>             cati membri are
#   wa.sh add|remove|promote|demote <grup> <numar> --apply
#   wa.sh subject <grup> "nume nou" --apply        (⚠️ cer --apply: schimba un grup real)
#
# Grup sau conversatie PRIVATA — la fel: `wa.sh say 0746661159 "salut"`, `wa.sh join 0746661159`.
set -uo pipefail
DIR=/root/wa-bridge
cd "$DIR"
PIDF=$DIR/multi_bot.pid
GRUPFILE=$DIR/groups.json
CACHE=$DIR/groups_cache.json
# Marcajul care spune ca vorbeste botul, nu ownerul. Se pune la INCEPUT, nu la final: o semnatura
# la coada se citeste ultima, adica dupa ce grupul deja a crezut ca e ownerul. Schimba-l aici.
MARCAJ="${WA_MARCAJ:-"🤖 "}"

# Verific si IDENTITATEA procesului, nu doar ca numarul exista: `kill -9` nu lasa procesul sa-si
# stearga pid-ul, iar un PID poate fi reciclat de altcineva. Fara asta, `listen` refuza sa porneasca
# zicand „deja ruleaza" despre un proces pe care tocmai l-am omorat (masurat).
viu() {
  local p
  p=$(cat "$PIDF" 2>/dev/null) || return 1
  [ -n "$p" ] || return 1
  kill -0 "$p" 2>/dev/null || return 1
  tr '\0' ' ' < /proc/$p/cmdline 2>/dev/null | grep -q multi_bot.js
}

# nume -> jid, din CATALOG (scris de listener) ca sa nu deschidem un al doilea socket.
# Caut si in `groups.json`: acolo stau ETICHETELE cu care ai intrat pe grup ("Operational + CS"),
# care pot fi mai scurte decat numele real de pe WhatsApp ("Operational + Customer Service") —
# fara asta, exact numele pe care il vezi in `status` nu se putea folosi in `say`. (masurat)
rezolva() {
  local q="$1"
  case "$q" in *@g.us|*@s.whatsapp.net) echo "$q"; return 0;; esac
  # Numar de telefon -> conversatie PRIVATA. Restul lantului (join/say/doc/react/read) merge deja
  # pe orice jid, lipsea doar conversia. 0746... -> 40746...@s.whatsapp.net
  local cifre; cifre=$(printf '%s' "$q" | tr -cd '0-9')
  if [ ${#cifre} -ge 9 ]; then
    case "$cifre" in
      40*) echo "${cifre}@s.whatsapp.net"; return 0;;
      0*)  echo "4${cifre}@s.whatsapp.net"; return 0;;
      *)   [ ${#cifre} -ge 11 ] && { echo "${cifre}@s.whatsapp.net"; return 0; };;
    esac
  fi
  python3 - "$q" <<'PY'
import json,sys,unicodedata
def n(s): return unicodedata.normalize("NFKD",(s or "").lower()).encode("ascii","ignore").decode()
q=n(sys.argv[1])
def incarca(f):
    try: return json.load(open(f))
    except Exception: return []
surse = incarca("/root/wa-bridge/groups.json") + incarca("/root/wa-bridge/groups_cache.json")
hit, vazut = [], set()
for g in surse:
    if not g.get("jid") or g["jid"] in vazut: continue
    if q in n(g.get("name")): hit.append(g); vazut.add(g["jid"])
if len(hit)==1: print(hit[0]["jid"])
elif not hit: pass
else:
    print("AMBIGUU, precizeaza:", file=sys.stderr)
    for g in hit[:10]: print("   %s  %s" % (g["jid"], g.get("name")), file=sys.stderr)
PY
}

case "${1:-}" in
status)
  if viu; then
    echo "SESIUNE VIE (PID $(cat $PIDF))"
    echo "asculta: $(python3 -c "import json;print(', '.join(g['name'] for g in json.load(open('$GRUPFILE'))))" 2>/dev/null || echo '(doar ce s-a dat la pornire)')"
    [ -f "$CACHE" ] && echo "catalog: $(python3 -c "import json;print(len(json.load(open('$CACHE'))))" 2>/dev/null) grupuri"
  else
    echo "NICIO SESIUNE — poti trimite direct sau porni ascultarea cu: wa.sh listen 60"
  fi ;;

find)
  q="${2:?dai un text de cautat}"
  if [ -f "$CACHE" ]; then
    python3 - "$q" <<'PY'
import json,sys,unicodedata
def n(s): return unicodedata.normalize("NFKD",(s or "").lower()).encode("ascii","ignore").decode()
q=n(sys.argv[1]); gs=json.load(open("/root/wa-bridge/groups_cache.json"))
hit=[g for g in gs if q in n(g.get("name"))]
print("gasite %d din %d grupuri (din catalog)" % (len(hit), len(gs)))
for g in hit[:20]: print("  [%s]  %s  — %s membri" % (g["jid"], g.get("name"), g.get("membri","?")))
PY
  else
    viu && { echo "listener viu dar fara catalog inca; mai asteapta 5s"; exit 1; }
    timeout 90 node wa_find.js "$q"
  fi ;;

join|leave)
  actiune="$1"; q="${2:?dai numele grupului sau jid-ul}"
  jid="$(rezolva "$q")" || true
  [ -z "$jid" ] && { echo "nu gasesc grupul '$q' (ruleaza: wa.sh find $q)"; exit 1; }
  nume="$(python3 -c "
import json
try: gs=json.load(open('$CACHE'))
except Exception: gs=[]
print(next((g['name'] for g in gs if g['jid']=='$jid'), '$jid'))" 2>/dev/null)"
  python3 - "$actiune" "$jid" "$nume" <<'PY'
import json,sys
act,jid,nume=sys.argv[1],sys.argv[2],sys.argv[3]
f="/root/wa-bridge/groups.json"
try: gs=json.load(open(f))
except Exception: gs=[]
gs=[g for g in gs if g.get("jid")!=jid]
if act=="join": gs.append({"jid":jid,"name":nume})
json.dump(gs,open(f,"w"),ensure_ascii=False,indent=1)
print(("intrat pe " if act=="join" else "iesit de pe ")+nume)
print("grupuri acum: "+(", ".join(g["name"] for g in gs) or "(niciunul)"))
PY
  viu && echo "listener-ul reciteste singur in ~5s (fara repornire)" || echo "ATENTIE: nu ruleaza niciun listener — porneste-l cu: wa.sh listen 60" ;;

listen)
  min="${2:-60}"
  if viu; then echo "deja ruleaza (PID $(cat $PIDF)) — foloseste 'wa.sh join' ca sa adaugi un grup"; exit 0; fi
  rm -f "$PIDF"            # pid ramas de la un proces mort
  spec="$(python3 -c "
import json
try: gs=json.load(open('$GRUPFILE'))
except Exception: gs=[]
print(','.join('%s=%s'%(g['jid'],g['name']) for g in gs))" 2>/dev/null)"
  setsid flock -n $DIR/.bot.lock node multi_bot.js "$spec" "$min" </dev/null >>$DIR/multi_bot.out 2>&1 &
  disown; sleep 12
  viu && echo "pornit: $(tail -2 $DIR/multi_bot.out | head -1)" || { echo "N-A PORNIT:"; tail -5 $DIR/multi_bot.out; exit 1; } ;;

stop)
  viu || { echo "nu rula nimic"; exit 0; }
  P=$(cat $PIDF); kill -9 "$P" 2>/dev/null
  for i in 1 2 3 4 5; do viu || break; sleep 1; done
  rm -f "$PIDF"; echo "oprit PID $P" ;;

say)
  # Puntea scrie de pe contul firmei, deci mesajele botului arata EXACT ca ale ownerului si grupul
  # nu poate deosebi cine vorbeste (owner, 4-sep: "nu mai scriu nimic ca crede lumea ca sunt tot eu").
  # De aceea semnez implicit. --raw trimite nesemnat, pentru mesaje operationale care trebuie sa
  # para ale firmei (notificari, AWB-uri).
  q="${2:?grup}"; shift 2
  pre="$MARCAJ"
  case "${1:-}" in --raw) pre=""; shift;; esac
  msg="$*"
  jid="$(rezolva "$q")" || true
  [ -z "$jid" ] && { echo "nu gasesc grupul '$q'"; exit 1; }
  node wa_send.js "$jid" text "$pre$msg" ;;

doc)
  q="${2:?grup}"; f="${3:?cale pdf}"; cap="${4:-}"
  jid="$(rezolva "$q")" || true
  [ -z "$jid" ] && { echo "nu gasesc grupul '$q'"; exit 1; }
  node wa_send.js "$jid" doc "$f" "$cap" ;;

read)
  n="${2:-20}"; tail -n "$n" $DIR/multi_in.jsonl 2>/dev/null | python3 -c "
import sys,json
for l in sys.stdin:
    try: r=json.loads(l)
    except Exception: continue
    print('#%-5s [%s] %s: %s' % (r.get('n','?'), r.get('grup'), r.get('from'), (r.get('text') or '').replace('\n',' / ')[:200]))" ;;

poll)
  q="${2:?grup}"; intrebare="${3:?intrebarea}"; shift 3
  [ $# -lt 2 ] && { echo "dai cel putin 2 optiuni"; exit 1; }
  jid="$(rezolva "$q")" || true; [ -z "$jid" ] && { echo "nu gasesc '$q'"; exit 1; }
  python3 - "$jid" "$intrebare" "$@" <<'PY'
import json,sys,os,random
jid,intrebare,optiuni=sys.argv[1],sys.argv[2],sys.argv[3:]
job={"id":"cli_%d_%s"%(os.getpid(),random.randrange(10**6)),"kind":"poll","jid":jid,
     "name":intrebare,"values":optiuni}
json.dump(job,open("/root/wa-bridge/multi_out/%s.json"%job["id"],"w"))
print("sondaj: %s (%d optiuni)"%(intrebare,len(optiuni)))
PY
  ;;

typing)
  q="${2:?grup}"; st="${3:-composing}"
  case "$st" in off|stop|nu) st="paused";; on|da) st="composing";; esac
  jid="$(rezolva "$q")" || true; [ -z "$jid" ] && { echo "nu gasesc '$q'"; exit 1; }
  python3 - "$jid" "$st" <<'PY'
import json,sys,os,random
job={"id":"cli_%d_%s"%(os.getpid(),random.randrange(10**6)),"kind":"typing",
     "jid":sys.argv[1],"state":sys.argv[2]}
json.dump(job,open("/root/wa-bridge/multi_out/%s.json"%job["id"],"w"))
print("indicator: %s"%sys.argv[2])
PY
  ;;

seen)
  nr="${2:?dai numarul mesajului}"
  python3 - "$nr" <<'PY'
import json,sys,os,random
nr=sys.argv[1].lstrip("#"); rec=None
with open("/root/wa-bridge/multi_in.jsonl",encoding="utf-8") as f:
    for l in f:
        try: r=json.loads(l)
        except Exception: continue
        if str(r.get("n"))==nr: rec=r
if not rec or not (rec.get("key") or {}).get("id"): print("nu gasesc #%s"%nr); sys.exit(1)
job={"id":"cli_%d_%s"%(os.getpid(),random.randrange(10**6)),"kind":"seen","key":rec["key"],"n":nr}
json.dump(job,open("/root/wa-bridge/multi_out/%s.json"%job["id"],"w"))
print("marcat citit #%s"%nr)
PY
  ;;

members)
  q="${2:?grup}"; jid="$(rezolva "$q")" || true
  [ -z "$jid" ] && { echo "nu gasesc '$q'"; exit 1; }
  python3 -c "
import json
gs=json.load(open('$CACHE'))
g=next((x for x in gs if x['jid']=='$jid'), None)
print('%s — %s membri' % (g['name'], g.get('membri','?')) if g else 'necunoscut')" ;;

add|remove|promote|demote|subject)
  # ⚠️ ACTIUNI DE ADMIN pe un grup real: scot/adaug oameni, schimb numele grupului. Nu se executa
  # din greseala — cer --apply explicit, ca la restul uneltelor din casa.
  act="$1"; q="${2:?grup}"; val="${3:?numar sau nume nou}"
  jid="$(rezolva "$q")" || true; [ -z "$jid" ] && { echo "nu gasesc '$q'"; exit 1; }
  case " $* " in *" --apply "*) ;; *) echo "DRY-RUN: as face '$act' pe $q cu '$val'. Adauga --apply ca sa se intample."; exit 0;; esac
  python3 - "$act" "$jid" "$val" <<'PY'
import json,sys,os,random,re
act,jid,val=sys.argv[1],sys.argv[2],sys.argv[3]
job={"id":"cli_%d_%s"%(os.getpid(),random.randrange(10**6)),"jid":jid}
if act=="subject": job.update(kind="gsubject", subject=val)
else:
    cif=re.sub(r"\D","",val)
    if cif.startswith("0"): cif="4"+cif
    job.update(kind="gadmin", op=act, numere=[cif+"@s.whatsapp.net"])
json.dump(job,open("/root/wa-bridge/multi_out/%s.json"%job["id"],"w"))
print("%s trimis"%act)
PY
  ;;

reply|fwd|get)
  # Actiuni care au nevoie de mesajul INTREG (salvat in multi_raw/), nu doar de cheie.
  act="$1"; nr="${2:?dai numarul mesajului}"; shift 2; rest="$*"
  tinta=""
  case "$act" in fwd) tinta="$(rezolva "$rest")"; [ -z "$tinta" ] && { echo "nu gasesc destinatia '$rest'"; exit 1; };; esac
  python3 - "$act" "$nr" "$rest" "$tinta" <<'PY'
import json,sys,os,random
act,nr,rest,tinta=sys.argv[1],sys.argv[2],sys.argv[3],sys.argv[4]
nr=nr.lstrip("#")
rec=None
with open("/root/wa-bridge/multi_in.jsonl",encoding="utf-8") as f:
    for l in f:
        try: r=json.loads(l)
        except Exception: continue
        if str(r.get("n"))==nr: rec=r
if not rec: print("nu gasesc mesajul #%s"%nr); sys.exit(1)
kind={"reply":"reply","fwd":"forward","get":"download"}[act]
job={"id":"cli_%d_%s"%(os.getpid(),random.randrange(10**6)),"kind":kind,"n":nr}
if kind=="reply":    job["jid"]=(rec.get("key") or {}).get("remoteJid"); job["text"]=rest
if kind=="forward":  job["jid"]=tinta
if kind=="download" and rest: job["dest"]=rest
json.dump(job,open("/root/wa-bridge/multi_out/%s.json"%job["id"],"w"))
print("%s pentru #%s"%(kind,nr))
PY
  ;;

react|edit|del)
  # Tinta e NUMARUL mesajului din `wa.sh read` (#12). Cheia lui se scoate din multi_in.jsonl.
  # ⚠️ edit merge DOAR pe mesajele noastre si doar ~15 min; delete pt toata lumea merge pe ale
  # noastre, pe ale altora doar daca botul e admin in grup.
  act="$1"; nr="${2:?dai numarul mesajului, vezi 'wa.sh read'}"; shift 2; rest="$*"
  python3 - "$act" "$nr" "$rest" <<'PY'
import json,sys,os,random
act,nr,rest=sys.argv[1],sys.argv[2],sys.argv[3]
nr=nr.lstrip("#")
gasit=None
with open("/root/wa-bridge/multi_in.jsonl",encoding="utf-8") as f:
    for l in f:
        try: r=json.loads(l)
        except Exception: continue
        if str(r.get("n"))==nr: gasit=r
if not gasit or not (gasit.get("key") or {}).get("id"):
    print("nu gasesc mesajul #%s (are cheie doar ce a intrat DUPA upgrade)"%nr); sys.exit(1)
kind={"react":"react","edit":"edit","del":"delete"}[act]
job={"id":"cli_%d_%s"%(os.getpid(),random.randrange(10**6)),"kind":kind,"key":gasit["key"],"n":nr}
if kind=="react": job["emoji"]=rest
if kind=="edit":  job["text"]=rest
json.dump(job,open("/root/wa-bridge/multi_out/%s.json"%job["id"],"w"))
print("%s trimis pentru #%s (%s de la %s)"%(kind,nr,(gasit.get("text") or "")[:40],gasit.get("from")))
PY
  ;;

*) sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//' ;;
esac
