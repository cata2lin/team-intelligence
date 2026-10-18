/**
 * fb-comment-webhook — moderare + reply real-time FB pentru brandurile ARONA. Înlocuiește Reply Zen.
 * Webhook Page `feed` → clasifică (reguli Reply Zen + scenariile CS oficiale) → ASCUNDE răul (live) +
 * REPLY PUBLIC cu template-urile oficiale CS (draft-cu-aprobare prima săpt., apoi live). Plângerile → CS.
 *
 * Lecții CS (feedback colege, 30-iun): răspuns SCURT + formal („dumneavoastră") + trimite la SITE (self-service) +
 * upsell (recenzie/recomandă). NU răspunsuri lungi care deschid conversație („spune-mi ce arome, îți descriu eu").
 * DM (private_replies) = NU merge pe comentarii IG (eroare 100/33) → TODO via IG Messaging API; momentan reply PUBLIC.
 *
 * KV: REVIEW. Secrets: FB_SYSTEM_TOKEN, FB_APP_SECRET, FB_VERIFY_TOKEN. Var: REPLY_MODE = draft|live.
 * Rute: GET / (verify) · POST / (events) · GET /review · GET /act?id=&do=approve|reject
 */
const GRAPH="https://graph.facebook.com/v20.0";

// page id -> site (din câmpul website al paginii; completate manual cele lipsă)
const SITE={"775068272350568":"https://magdeal.ro","676105508924341":"https://george-talent.ro","629666993566339":"https://grandia.ro","680369271815957":"https://bonhaus.bg","628544790345906":"https://gentipromo.ro","621560724373069":"https://carpetto.ro","651700798017858":"https://casaofertelor.ro","575484808985734":"https://covoria.ro","522811567592063":"https://gento.ro","506398435900401":"https://produsebisericesti.ro","421367954403103":"https://apreciat.ro","132189989971450":"https://stemma.ro","115983611500696":"https://manscout.ro","103675612509107":"https://bonhaus.ro","104553898590313":"https://www.uneltepotrivite.ro","582569158278392":"https://nubra.ro","582681401604162":"https://ofertelezilei.ro"};
const siteOf=pid=>SITE[pid]||"site-ul nostru";


// ---------- captare BRUTA (D1) ----------
// Se scrie ÎNAINTE de clasificare/hide, deliberat. Motivul, măsurat: pe Instagram 81% din
// comentarii dispar de pe platformă înainte să apuci să le citești prin Graph (polling = 13%
// recall), iar ce dispare e SELECTIV — rămân "Recomand", se șterg "mi-am luat țeapă". Dacă
// scriem după moderare, oglinda moștenește exact aceeași orbire.
// INSERT OR IGNORE: un re-livrat de Meta (retry) nu suprascrie ce am prins prima dată.
async function rawSave(env, obj, entryId, field, v, body){
 if(!env.RAW) return;
 try{
  // Doua forme COMPLET diferite pe acelasi webhook:
  //  changes[]   -> comentarii: {comment_id, from:{id,name|username}, message|text, created_time, verb}
  //  messaging[] -> DM-uri:     {sender:{id}, recipient:{id}, timestamp,
  //                              message:{mid,text,attachments} | read:{mid} | delivery:{mids}
  //                              | reaction:{mid,emoji} | postback:{...}}
  // Prima versiune trata doar prima forma => DM-urile intrau cu autor si text GOALE
  // (payloadul se pastra in `raw`, deci nu s-a pierdut nimic, dar coloanele erau inutile).
  const isMsg = field === "messaging";
  let id, fromId, fromName, text, ts, verb, parent;
  if(isMsg){
   const m = v.message || {};
   verb = v.message ? "message" : v.read ? "read" : v.delivery ? "delivery"
        : v.reaction ? "reaction" : v.postback ? "postback" : "other";
   id = m.mid || (v.read&&v.read.mid) || (v.reaction&&v.reaction.mid)
        || (v.delivery&&(v.delivery.mids||[])[0]) || `${entryId}_${v.timestamp||Date.now()}_${verb}`;
   fromId = (v.sender||{}).id || "";
   fromName = (v.sender||{}).username || (v.sender||{}).name || "";
   // atasamentele nu au text; notam tipul ca sa nu para mesaj gol
   const att = (m.attachments||[]).map(a=>a.type).filter(Boolean).join(",");
   text = m.text || (att ? `[attachment:${att}]` : "") || (v.reaction&&v.reaction.emoji) || "";
   ts = Number(v.timestamp||0)||0;
   parent = (v.recipient||{}).id || "";
  } else {
   const from = v.from || {};
   verb = String(v.verb||"");
   id = v.comment_id || v.id || `${entryId}_${Date.now()}`;
   fromId = from.id || "";
   fromName = from.name || from.username || "";
   text = v.message ?? v.text ?? "";
   ts = Number(v.created_time||0)||0;
   parent = v.parent_id || v.post_id || v.media_id || (v.media&&v.media.id) || "";
  }
  await env.RAW.prepare(
    `INSERT OR IGNORE INTO raw_event
     (id,object,field,entry_id,parent_id,from_id,from_name,text,created_at,received_at,verb,raw)
     VALUES (?,?,?,?,?,?,?,?,?,?,?,?)`)
   .bind(String(id), obj||"", field||"", String(entryId||""), String(parent||""),
         String(fromId), String(fromName), String(text),
         ts, Date.now(), verb, String(body).slice(0,20000))
   .run();
 }catch(e){ console.log("rawSave err", String(e).slice(0,120)); }
}


// ---------- clasificator: întoarce {act, scn} ----------
// normalizare: scoate diacriticele + lowercase (ca să prindă „încercat/să/țepari" indiferent de scriere)
const norm=s=>(s||"").normalize("NFD").replace(/[̀-ͯ]/g,"").replace(/ș|ş/gi,"s").replace(/ț|ţ/gi,"t").toLowerCase();
const R={
 spam:/https?:\/\/|wa\.me|whatsapp|t\.me|bit\.ly|telegram|\b0\d{2}[\s.]?\d{3}[\s.]?\d{3}\b/,
 comp:/\b(temu|aliexpress|\bali\b|shein|viled[ya]|al+egro|empik\w*|ka?ufland|ceneo|\bolx\b|lidl|biedronka|rossmann|heureka|alza|emag|technopolis|bazar\.bg)\b/,
 curr:/\b(k[cs]|zl)\b|лева|\bbgn\b/,
 // plângere reală (problemă cu o comandă) — BATE lead-ul
 complaint:/nu am primit|n-?am primit|nu a ajuns|nu mi-?au? ajuns|mi-?au trimis|am primit (alt|gresit|3|2|alta|defect|stricat|rupt|spart|altceva)|lipsa|lipsesc|gresit|defect|stricat|rupt|spart|nu functioneaza|retur|inapoi banii|vreau banii|comanda gresit|nu merge site|nu raspunde|incercat sa iau legatura|am incercat sa|oribil|groaznic|nu a venit|nu mi se raspunde/,
 accuse:/\b(tepar|escroc|hoti?|minciun|inselat|fake|teapa|jaf|fals)\w*/,
 price:/\bpret\b|ce pret|cat cost|ce cost/,
 howorder:/cum (comand|se comand|pot comanda|fac comanda)|de unde (comand|cumpar)|unde comand/,
 delivery:/cate zile|in cat timp|cand ajunge|timp(ul)? de livrare|cand primesc/,
 details:/ce (culori|dimensiun|marim|material)|disponibil|pe stoc|in stoc|mai aveti|ce variante|ce modele/,
 scent:/ce miros|cum miroase|a ce miroase|ce arom|nu stiu ce (parfum|aleg)|ce parfum (sa aleg|recomand)/,
 lead:/vreau|doresc|as dori|ma intereseaz|comand si eu|il vreau|o vreau/,
 perfumePartial:/seaman|nu persist|nu tine|nu rezist|miroase frumos dar|nu e ca originalul|nu se simte/,
 placed:/am (dat|plasat|facut) comand|tocmai am comandat|azi am comandat|abia am comandat|revin cu (recenzi|parer)/,
 positive:/recomand|super|frumos|excelent|multumesc|multumit|perfect|imi place|bravo|minunat|calitate buna|nota 10|\btop\b/,
};
function allCaps(m){const l=[...m].filter(c=>/[a-zăâîșțA-ZĂÂÎȘȚ]/.test(c));return l.length>=12 && l.filter(c=>c===c.toUpperCase()).length/l.length>0.85;}
function classify(raw){
 const m=norm((raw||"").trim()); if(m.length<2) return {act:"keep"};
 if(R.spam.test(m)) return {act:"hide",scn:"spam"};
 if((R.comp.test(m)||R.curr.test(m)) && !R.price.test(m) && !R.howorder.test(m)) return {act:"hide",scn:"competitie"};
 if(R.complaint.test(m)) return {act:"route",scn:"problema"};          // plângere reală → CS (NU hide)
 if(R.accuse.test(m)) return {act:"hide",scn:"acuzatie"};              // acuzație vagă → hide
 if(allCaps(raw)) return {act:"hide",scn:"caps"};
 if(R.price.test(m)) return {act:"reply",scn:"pret"};
 if(R.howorder.test(m)) return {act:"reply",scn:"cumcomand"};
 if(R.delivery.test(m)) return {act:"reply",scn:"livrare"};
 if(R.scent.test(m)) return {act:"reply",scn:"alegere"};
 if(R.details.test(m)) return {act:"reply",scn:"detalii"};
 if(R.perfumePartial.test(m)) return {act:"reply",scn:"partial"};
 if(R.placed.test(m)) return {act:"reply",scn:"plasat"};
 if(R.lead.test(m)) return {act:"reply",scn:"pret"};                   // intenție de cumpărare → preț+site
 if(R.positive.test(m)) return {act:"reply",scn:"pozitiv"};
 return {act:"keep"};
}
// ---------- template-uri oficiale CS (scurte, formale, link la site) ----------
function tmpl(scn,site){const S=site; return ({
 pret:`Bună ziua! 😊 Prețul actual și oferta sunt disponibile aici: ${S}`,
 cumcomand:`Bună ziua! 😊 Comanda se plasează accesând ${S} — apăsați «Adaugă în coș», completați datele și finalizați. Plata se face ramburs, la livrare.`,
 livrare:`Bună ziua! 😊 În mod normal, comenzile sunt livrate în 1–3 zile lucrătoare, în funcție de localitate și curier.`,
 detalii:`Bună ziua! 😊 Toate detaliile (dimensiuni, culori, materiale, mod de utilizare) le găsiți pe pagina produsului: ${S}. Dacă mai aveți întrebări, vă răspundem cu drag!`,
 alegere:`Bună ziua! 😊 Pentru alegerea potrivită, vă recomandăm să consultați descrierea fiecărui parfum pe ${S} — veți găsi notele de vârf, de mijloc și de bază, concentrația și stilul. Vă stăm la dispoziție!`,
 partial:`Vă mulțumim pentru feedback! Mirosul este asemănător originalului, însă concentrația diferă, iar percepția ține și de pH-ul fiecărei persoane. Vă mulțumim pentru încredere! ❤️`,
 plasat:`Vă mulțumim pentru comandă și pentru încrederea acordată! ❤️ Suntem convinși că vă veți bucura de produse. După ce primiți comanda, ne-ar face mare plăcere să reveniți cu o recenzie. Vă așteptăm cu drag și la următoarea comandă!`,
 pozitiv:`Vă mulțumim din suflet! ❤️ Ne bucurăm că sunteți mulțumit(ă) și vă așteptăm cu drag și la următoarea comandă!`,
 problema:`Ne pare rău pentru situația întâmpinată. Vă rugăm să ne transmiteți în privat numărul comenzii și o scurtă descriere, ca să verificăm și să revenim rapid cu o soluție. 🙏`,
}[scn]||"");}

// mapuri: fb page id -> token; ig account id -> {token, pageId}
// Harta pagina->token. ⚠️ Se chema la FIECARE eveniment pe /me/accounts (cel mai scump endpoint
// ca si cota). La trafic mare app-ul se satura, `j.data` lipsea, harta iesea GOALA si fiecare
// comentariu era sarit tacut (`if(!pt)continue`) — cu raspuns 200 EVENT_RECEIVED. Doua reparatii:
// (1) cache 5 min => ~300x mai putine apeluri;
// (2) la eroare NU mai producem harta goala: folosim ultima harta buna, iar daca n-avem niciuna
//     aruncam => Worker-ul da 500 si Meta REIA livrarea, in loc sa piardem comentariul.
let _mapsCache=null,_mapsAt=0;
const MAPS_TTL=300000;
async function maps(t){
 const now=Date.now();
 if(_mapsCache && (now-_mapsAt)<MAPS_TTL) return _mapsCache;
 const r=await fetch(`${GRAPH}/me/accounts?fields=id,access_token,instagram_business_account&limit=100&access_token=${t}`);
 const j=await r.json();
 if(!Array.isArray(j.data)){
  const why=JSON.stringify(j.error||j).slice(0,200);
  if(_mapsCache){console.log("maps: eroare, folosesc cache-ul vechi:",why);return _mapsCache;}
  throw new Error("maps indisponibil (fara cache): "+why);
 }
 const fb={},ig={};
 j.data.forEach(p=>{fb[p.id]=p.access_token;if(p.instagram_business_account)ig[p.instagram_business_account.id]={token:p.access_token,pageId:p.id};});
 _mapsCache={fb,ig};_mapsAt=now;
 console.log("maps: reincarcat,",j.data.length,"pagini");
 return _mapsCache;
}
async function pageTokens(t){return (await maps(t)).fb;}
// FB
async function hide(id,pt){return fetch(`${GRAPH}/${id}?is_hidden=true&access_token=${pt}`,{method:"POST"});}
async function reply(id,msg,pt){return fetch(`${GRAPH}/${id}/comments`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({message:msg,access_token:pt})});}
// IG
async function hideIG(id,pt){return fetch(`${GRAPH}/${id}?hide=true&access_token=${pt}`,{method:"POST"});}
async function replyIG(id,msg,pt){return fetch(`${GRAPH}/${id}/replies`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({message:msg,access_token:pt})});}
// DM IG (Instagram Login token din KV) — răspuns privat la comentariu
async function dmIG(igTok,commentId,msg){const r=await fetch(`https://graph.instagram.com/me/messages?access_token=${igTok}`,{method:"POST",headers:{"content-type":"application/json"},body:JSON.stringify({recipient:{comment_id:commentId},message:{text:msg}})});return r.ok;}
async function verifySig(req,body,sec){const sig=req.headers.get("x-hub-signature-256");if(!sig)return false;const k=await crypto.subtle.importKey("raw",new TextEncoder().encode(sec),{name:"HMAC",hash:"SHA-256"},false,["sign"]);const mac=await crypto.subtle.sign("HMAC",k,new TextEncoder().encode(body));return "sha256="+[...new Uint8Array(mac)].map(b=>b.toString(16).padStart(2,"0")).join("")===sig;}

export default {
 async fetch(req,env){
  const url=new URL(req.url);
  if(req.method==="GET"&&url.pathname==="/"){
   if(url.searchParams.get("hub.mode")==="subscribe"&&url.searchParams.get("hub.verify_token")===env.FB_VERIFY_TOKEN)
    return new Response(url.searchParams.get("hub.challenge"),{status:200});
   return new Response("forbidden",{status:403});
  }
  // --- pagini pt App Review (eligibilitate app) ---
  if(url.pathname==="/privacy"){return new Response(`<!doctype html><html lang="ro"><meta charset="utf-8"><title>Politică de confidențialitate</title><body style="font-family:system-ui;max-width:760px;margin:40px auto;padding:0 16px;line-height:1.6">
<h1>Politică de confidențialitate</h1><p>Această aplicație („Api export") este folosită intern de grupul ARONA pentru a modera și a răspunde la comentariile de pe paginile proprii de Facebook și Instagram. Aplicația citește textul comentariilor publice pentru a ascunde spam/conținut abuziv și pentru a trimite răspunsuri publice utile. <strong>Nu stocăm date personale</strong> — textul comentariilor este procesat tranzitoriu, în memorie, fără a fi salvat. Nu vindem și nu partajăm date cu terți.</p>
<h2>Contact</h2><p>Pentru întrebări sau ștergerea datelor: gheorghe.beschea@overheat.agency</p>
<h2>Data deletion</h2><p>Nu reținem date de utilizator; pentru confirmarea ștergerii vezi ${url.origin}/data-deletion.</p></body></html>`,{headers:{"content-type":"text/html; charset=utf-8"}});}
  if(url.pathname==="/terms"){return new Response(`<!doctype html><html lang="ro"><meta charset="utf-8"><title>Termeni și condiții</title><body style="font-family:system-ui;max-width:760px;margin:40px auto;padding:0 16px;line-height:1.6">
<h1>Termeni și condiții</h1><p>Aplicația „Api export" este un instrument intern al grupului ARONA, folosit exclusiv pentru moderarea și gestionarea comentariilor de pe paginile și conturile proprii de Facebook și Instagram. Accesul este limitat la personalul autorizat ARONA.</p>
<h2>Utilizare</h2><p>Aplicația ascunde comentariile de tip spam/abuziv și trimite răspunsuri publice utile, conform politicilor Meta Platform Terms și Developer Policies. Nu este destinată utilizării de către terți.</p>
<h2>Date</h2><p>Nu stocăm date personale; textul comentariilor este procesat tranzitoriu. Vezi <a href="${url.origin}/privacy">Politica de confidențialitate</a>.</p>
<h2>Contact</h2><p>gheorghe.beschea@overheat.agency</p></body></html>`,{headers:{"content-type":"text/html; charset=utf-8"}});}
  if(url.pathname==="/data-deletion"){
   const code="ARONA-"+(url.searchParams.get("id")||"nostore");
   return Response.json({url:`${url.origin}/data-deletion?status=${code}`,confirmation_code:code});
  }
  // Instagram Business Login redirect — schimbă code→token, salvează în KV (ig:{uid})
  if(url.pathname==="/ig-callback"){
   const code=url.searchParams.get("code");if(!code)return new Response("missing code",{status:400});
   const redirect=`${url.origin}/ig-callback`;
   const form=new URLSearchParams({client_id:env.IG_APP_ID,client_secret:env.IG_APP_SECRET,grant_type:"authorization_code",redirect_uri:redirect,code});
   const r1=await (await fetch("https://api.instagram.com/oauth/access_token",{method:"POST",headers:{"content-type":"application/x-www-form-urlencoded"},body:form})).json();
   if(r1.error_type||r1.error_message)return new Response("oauth err: "+JSON.stringify(r1),{status:400});
   const short=r1.access_token,uid=r1.user_id;
   const r2=await (await fetch(`https://graph.instagram.com/access_token?grant_type=ig_exchange_token&client_secret=${env.IG_APP_SECRET}&access_token=${short}`)).json();
   const long=r2.access_token||short;
   const me=await (await fetch(`https://graph.instagram.com/me?fields=username,user_id&access_token=${long}`)).json();
   await env.REVIEW.put("ig:"+(me.user_id||uid),JSON.stringify({token:long,username:me.username,uid:me.user_id||uid}));
   return new Response(`<html><meta charset="utf-8"><body style="font-family:system-ui;text-align:center;margin-top:80px"><h2>✅ Cont Instagram conectat: @${me.username||"?"}</h2><p>Token salvat. Poți închide fereastra.</p></body></html>`,{headers:{"content-type":"text/html; charset=utf-8"}});
  }
  if(url.pathname==="/review"){const l=await env.REVIEW.list({prefix:"p:"});const o=[];for(const k of l.keys)o.push(JSON.parse(await env.REVIEW.get(k.name)));return Response.json(o);}
  if(url.pathname==="/act"){const id=url.searchParams.get("id"),d=url.searchParams.get("do");const raw=await env.REVIEW.get("p:"+id);if(!raw)return new Response("404",{status:404});const a=JSON.parse(raw);if(d==="approve"){const pt=(await maps(env.FB_SYSTEM_TOKEN)).fb[a.pageId];if(a.platform==="ig")await replyIG(a.commentId,a.msg,pt);else await reply(a.commentId,a.msg,pt);}await env.REVIEW.delete("p:"+id);return new Response("ok:"+d);}
  if(req.method==="POST"){
   const body=await req.text();
   if(!(await verifySig(req,body,env.FB_APP_SECRET))&&!(env.IG_APP_SECRET&&await verifySig(req,body,env.IG_APP_SECRET)))return new Response("bad sig",{status:401});
   const data=JSON.parse(body);
   // Fara linia asta, logurile nu spun CE a livrat Meta: corpul cererii nu apare in Workers Logs,
   // deci "instagram vs page" ramanea deductie din user-agent, iar livrarile reale nu se puteau
   // separa de butonul "Test" din App Dashboard (acelasi UA). entry.id = pagina / contul IG real;
   // payloadul de test are un entry.id care NU e printre id-urile noastre.
   try{
    const ents=data.entry||[];
    console.log("wh", data.object, ents.length,
      ents.map(e=>e.id).join(","),
      ents.flatMap(e=>(e.changes||[]).map(c=>c.field)).join(","),
      ents.flatMap(e=>(e.messaging||[]).map(()=>"messaging")).join(","));
   }catch(_){}
   // ⚠️ CAPTAREA BRUTA SE FACE PRIMA, inaintea oricarui filtru. Prima versiune o pusese DUPA
   // `if(!pt)continue` / `if(!acc)continue` — adica exact evenimentele de pe pagini care NU sunt in
   // harta (token lipsa, cont IG nelegat) se pierdeau tacut, fix cazul pentru care exista captarea.
   // Dovedit: un eveniment IG de test n-a ajuns in D1, desi cel FB a ajuns.
   for(const e of data.entry||[]){
    for(const ch of e.changes||[]){
     await rawSave(env, data.object, e.id, ch.field, ch.value||{}, body);
    }
    for(const m of e.messaging||[]){
     await rawSave(env, data.object, e.id, "messaging", m, body);
    }
   }
   const {fb,ig}=await maps(env.FB_SYSTEM_TOKEN);
   const live=(env.REPLY_MODE||"draft")==="live";
   for(const e of data.entry||[]){
    if(data.object==="instagram"){                                   // --- IG comments ---
     const acc=ig[e.id];if(!acc)continue;const pt=acc.token,site=siteOf(acc.pageId);
     const dmRaw=await env.REVIEW.get("ig:"+e.id);const dmTok=dmRaw?JSON.parse(dmRaw).token:null;
     for(const ch of e.changes||[]){
      if(ch.field!=="comments")continue;const v=ch.value||{};const cid=v.id;if(!cid)continue;
      if(v.from&&String(v.from.id)===String(e.id))continue;
      const k=classify(v.text);
      if(k.act==="hide"){await hideIG(cid,pt);continue;}
      if(k.act==="keep")continue;
      const msg=tmpl(k.scn,site);if(!msg)continue;
      const buy=["pret","cumcomand","detalii","alegere"].includes(k.scn)&&dmTok;  // intenție cumpărare + avem token DM
      if(live){
       if(buy){await dmIG(dmTok,cid,msg);await replyIG(cid,"Bună ziua! 😊 V-am trimis un mesaj privat cu toate detaliile 💬",pt);}
       else await replyIG(cid,msg,pt);
      } else await env.REVIEW.put("p:ig_"+cid,JSON.stringify({id:"ig_"+cid,platform:"ig",channel:buy?"dm+reply":"reply",commentId:cid,pageId:acc.pageId,act:k.act,scn:k.scn,msg,orig:v.text,ts:v.created_time}));
     }
    } else {                                                         // --- FB page feed ---
     const pid=e.id,pt=fb[pid];if(!pt)continue;
     for(const ch of e.changes||[]){
      if(ch.field!=="feed")continue;const v=ch.value||{};
      if(v.item!=="comment"||v.verb!=="add")continue;
      if(v.from&&String(v.from.id)===String(pid))continue;
      const cid=v.comment_id;const k=classify(v.message);
      if(k.act==="hide"){await hide(cid,pt);continue;}
      if(k.act==="keep")continue;
      const msg=tmpl(k.scn,siteOf(pid));if(!msg)continue;
      if(live)await reply(cid,msg,pt);
      else await env.REVIEW.put("p:"+cid,JSON.stringify({id:cid,platform:"fb",commentId:cid,pageId:pid,act:k.act,scn:k.scn,msg,orig:v.message,ts:v.created_time}));
     }
    }
   }
   return new Response("EVENT_RECEIVED",{status:200});
  }
  return new Response("ok");
 }
};
