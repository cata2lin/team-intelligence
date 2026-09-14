const { default: makeWASocket, useMultiFileAuthState, fetchLatestBaileysVersion } = require('baileys')
const pino = require('pino'); const fs = require('fs')
const AUTH='/root/wa-bridge/auth'
const DIR='/root/wa-bridge'
// argv[2] = "jid1=NumeA,jid2=NumeB"   argv[3] = minute
const SPEC = (process.argv[2]||'').split(',').filter(Boolean).map(s=>{
  const [jid,name] = s.split('='); return {jid, name: name||jid.slice(0,8)}
})
const JIDS = new Set(SPEC.map(s=>s.jid))
const NAME = Object.fromEntries(SPEC.map(s=>[s.jid, s.name]))
const MINUTES = parseInt(process.argv[3]||'30',10)   // 0 = fara termen, ruleaza pana il opresti
const INBOX=DIR+'/multi_in.jsonl', OUTDIR=DIR+'/multi_out', RAWDIR=DIR+'/multi_raw'
if(!fs.existsSync(OUTDIR)) fs.mkdirSync(OUTDIR,{recursive:true})
fs.writeFileSync(DIR+'/multi_bot.pid', String(process.pid))
process.on('exit', ()=>{ try{ fs.unlinkSync(DIR+'/multi_bot.pid') }catch(e){} })
const DEADLINE = MINUTES>0 ? Date.now() + MINUTES*60*1000 : Infinity

// Grupurile ascultate se tin intr-un FISIER, nu doar in argumente: ca sa intri pe un grup nou nu
// mai repornesti procesul (ceea ce ar rupe sesiunea si ar pierde grupurile curente) — scrii in
// `groups.json` si listener-ul il reciteste singur. `wa.sh join/leave` face exact asta.
const GRUPFILE = DIR+'/groups.json'
function incarcaGrupuri(){
  let din_fisier = []
  try{ din_fisier = JSON.parse(fs.readFileSync(GRUPFILE,'utf8')) }catch(e){}
  const toate = [...SPEC, ...(Array.isArray(din_fisier)?din_fisier:[])].filter(g=>g&&g.jid)
  const noi = []
  for(const g of toate){
    if(!JIDS.has(g.jid)){ JIDS.add(g.jid); noi.push(g.name||g.jid) }   // mutez SETUL pe loc:
    NAME[g.jid] = g.name || NAME[g.jid] || g.jid                        // closure-ul shouldIgnoreJid
  }                                                                     // vede modificarea imediat
  const vrute = new Set(toate.map(g=>g.jid))
  for(const j of [...JIDS]) if(!vrute.has(j)){ JIDS.delete(j); noi.push('-'+(NAME[j]||j)) }
  if(noi.length) console.log('GRUPURI: '+[...JIDS].map(j=>NAME[j]).join(' + '))
}
incarcaGrupuri()
setInterval(incarcaGrupuri, 5000)
const MY_SENT = new Set()
// Fiecare mesaj primeste un NUMAR (#12) = randul lui din multi_in.jsonl. Cu el se poate da react,
// edit sau delete din CLI, fara sa pui mana pe id-uri de WhatsApp. Reactia/editarea/stergerea au
// nevoie de CHEIA mesajului (remoteJid+id+participant), care inainte nu se salva deloc.
let SEQ = 0
try{ SEQ = (fs.readFileSync(INBOX,'utf8').match(/\n/g)||[]).length }catch(e){}
function noteaza(rec){ SEQ++; rec.n = SEQ; fs.appendFileSync(INBOX, JSON.stringify(rec)+'\n'); return SEQ }
let reconnects=0, ignored=0, kept=0
const logger = pino({level:'warn'}, pino.destination({dest:DIR+'/multi_bot.warn.log', sync:false}))
const log = m => { try{fs.appendFileSync(DIR+'/multi_bot.log', new Date().toISOString()+' '+m+'\n')}catch(e){} }

function textOf(m){
  const mm=m.message||{}
  return mm.conversation || mm.extendedTextMessage?.text || mm.imageMessage?.caption
      || mm.videoMessage?.caption || mm.documentMessage?.caption
      || (mm.audioMessage?'[voice]':'') || (mm.imageMessage?'[imagine]':'')
      || (mm.stickerMessage?'[sticker]':'')
      || (mm.reactionMessage?('[reactie '+(mm.reactionMessage.text||'')+']'):'')
      || descrie(mm)
}

// Ce NU stiam sa citim ajungea text GOL — mesajul aparea in log ca o linie fara continut si eu
// raspundeam pe langa, fara sa stiu macar ca mi-a scapat ceva. Acum orice tip necunoscut isi
// spune numele, ca sa se vada ca A VENIT ceva si ce fel. (masurat 4-sep: doua mesaje invizibile)
function descrie(mm){
  const NUME = {
    documentMessage:'[document]', audioMessage:'[voice]', locationMessage:'[locatie]',
    liveLocationMessage:'[locatie live]', contactMessage:'[contact]',
    contactsArrayMessage:'[contacte]', pollCreationMessage:'[sondaj]',
    pollCreationMessageV3:'[sondaj]', pollUpdateMessage:'[vot sondaj]',
    viewOnceMessage:'[vizionare unica]', viewOnceMessageV2:'[vizionare unica]',
    ephemeralMessage:'[mesaj efemer]', protocolMessage:'[sistem]',
    editedMessage:'[editat]', ptvMessage:'[video mesaj]',
    groupInviteMessage:'[invitatie grup]', productMessage:'[produs]',
  }
  for(const k of Object.keys(mm||{})){
    if(NUME[k]) return NUME[k]
  }
  const chei = Object.keys(mm||{}).filter(k=>k!=='messageContextInfo')
  return chei.length ? '['+chei[0]+']' : ''
}

async function start(){
  const { version } = await fetchLatestBaileysVersion()
  const { state, saveCreds } = await useMultiFileAuthState(AUTH)
  const sock = makeWASocket({
    version, auth:state, logger,
    markOnlineOnConnect:false, syncFullHistory:false,
    shouldIgnoreJid: (jid) => {
      if(!jid) return false
      if(JIDS.has(jid)){ kept++; return false }
      ignored++; return true
    },
    browser:['Arona Bridge','Chrome','1.0']
  })
  sock.ev.on('creds.update', saveCreds)

  sock.ev.on('messages.upsert', (ev)=>{
    for(const m of ev.messages||[]){
      try{
        const chat=m.key?.remoteJid
        if(!JIDS.has(chat)) continue
        if(m.key?.fromMe && MY_SENT.has(m.key?.id)) continue
        const r={ t:new Date().toISOString(), grup:NAME[chat], type:ev.type,
                  from: m.key?.fromMe ? 'GIGI (telefon)'
                        : (m.pushName || (m.key?.participant||'').split('@')[0] || '?'),
                  text: textOf(m),
                  key: { remoteJid:chat, fromMe:!!m.key?.fromMe, id:m.key?.id,
                         participant:m.key?.participant||undefined } }
        const n = noteaza(r)
        // Mesajul INTREG, pe disc. Cheia ajunge pt react/edit/delete, dar forward, raspuns-citat
        // si descarcarea de media au nevoie de obiectul complet. Il tin separat, ca sa nu umflu
        // multi_in.jsonl (care e citit la fiecare `read`).
        try{ fs.mkdirSync(RAWDIR,{recursive:true}); fs.writeFileSync(RAWDIR+'/'+n+'.json', JSON.stringify(m)) }catch(e){}
        console.log(`[${r.grup}] #${n} ${r.from}: ${(r.text||'').replace(/\n/g,' / ').slice(0,250)}`)
      }catch(e){ log('upsert '+e.message) }
    }
  })

  sock.ev.on('connection.update',(u)=>{
    if(u.connection==='open'){
      reconnects=0            // s-a conectat: bugetul se reia de la zero
      log('connected'); console.log('CONNECTED pe: '+[...JIDS].map(j=>NAME[j]).join(' + '))
      // Public catalogul de grupuri, ca `wa.sh join` sa poata rezolva un nume FARA sa deschida
      // un al doilea socket (ar evicta exact sesiunea asta — vezi sc=440).
      sock.groupFetchAllParticipating().then(gs=>{
        const lista = Object.values(gs||{}).map(g=>({jid:g.id, name:g.subject, membri:(g.participants||[]).length}))
        try{ fs.writeFileSync(DIR+'/groups_cache.json', JSON.stringify(lista,null,1)) }catch(e){}
        console.log('CATALOG: '+lista.length+' grupuri')
      }).catch(e=>log('catalog '+e.message))
    }
    if(u.connection==='close'){
      const sc=u.lastDisconnect?.error?.output?.statusCode
      log('closed sc='+sc); console.log('CLOSED sc='+sc)
      if(sc===401){ console.log('LOGGED_OUT'); process.exit(3) }
      // Bugetul de reconectari era pe TOATA VIATA procesului (5). Pentru un listener permanent
      // asta inseamna ca dupa a 5-a reconectare — oricat de departate in timp — se oprea de tot.
      // Acum contorul se reseteaza la fiecare conectare reusita, iar fara termen nu se preda.
      const nelimitat = !isFinite(DEADLINE)
      if(Date.now()<DEADLINE && (nelimitat || reconnects<5)){
        reconnects++
        setTimeout(()=>start().catch(e=>log(e.message)), Math.min(5000*reconnects, 30000))
      }
      else { console.log('STOP'); process.exit(0) }
    }
  })

  setInterval(()=>console.log(`FILTRU ignorate=${ignored} pastrate=${kept}`),300000)

  // outbox. Doua formate:
  //   `.txt`  = PRIMUL RAND jid, restul mesajul (formatul vechi, ramane valabil)
  //   `.json` = {id, jid, kind:'text'|'doc', text?, path?, caption?} — scris de wa_send.js
  //
  // Trei reparatii (4-sep-2026):
  // 1. Fisierul se sterge DOAR dupa ce mesajul a plecat. Inainte se stergea INAINTE de trimitere,
  //    deci un esec il pierdea definitiv — masurat: doua mesaje pierdute pe „Connection Closed".
  // 2. Trimit catre ORICE jid. Filtrul de grupuri spune ce CITESC, nu unde am voie sa scriu;
  //    inainte, orice mesaj pentru alt grup era aruncat cu „SKIP (jid necunoscut)".
  // 3. Accept si documente (PDF), cu garda de duplicat pe jid|nume|marime.
  const pathmod = require('path')
  const ACKS = DIR+'/outbox_acks.log', SENTLOG = DIR+'/sent_docs.log', FAILDIR = OUTDIR+'/esuate'
  const INCERCARI = {}, MAX_INCERCARI = 20
  const ack = (id,stare,det)=>{ if(id){ try{ fs.appendFileSync(ACKS,[id,stare,det||''].join('\t')+'\n') }catch(e){} } }
  // Orice fisier pleca marcat `application/pdf`, deci o poza ajungea in chat ca document ilizibil.
  // Acum tipul se ia din extensie, si imaginile/filmele/audio pleaca in formatul lor firesc.
  const MIME = { '.pdf':'application/pdf', '.jpg':'image/jpeg', '.jpeg':'image/jpeg', '.png':'image/png',
                 '.webp':'image/webp', '.gif':'image/gif', '.mp4':'video/mp4', '.mov':'video/quicktime',
                 '.ogg':'audio/ogg; codecs=opus', '.mp3':'audio/mpeg', '.m4a':'audio/mp4',
                 '.xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                 '.csv':'text/csv', '.txt':'text/plain', '.zip':'application/zip' }
  function continutFisier(cale, caption){
    const ext = pathmod.extname(cale).toLowerCase()
    const mimetype = MIME[ext] || 'application/octet-stream'
    const buf = fs.readFileSync(cale)
    if(mimetype.startsWith('image/')) return { image:buf, mimetype, caption }
    if(mimetype.startsWith('video/')) return { video:buf, mimetype, caption }
    if(mimetype.startsWith('audio/')) return { audio:buf, mimetype }
    return { document:buf, mimetype, fileName:pathmod.basename(cale), caption }
  }

  const docKey = (j,pp)=>{ let sz=0; try{ sz=fs.statSync(pp).size }catch(e){}; return j+'|'+pathmod.basename(pp)+'|'+sz }
  const dejaTrimis = (k)=>{ try{ return fs.readFileSync(SENTLOG,'utf8').split('\n').some(l=>l.split('\t')[0]===k) }catch(e){ return false } }

  const pump=setInterval(async ()=>{
    if(Date.now()>DEADLINE){ clearInterval(pump); console.log('DEADLINE'); setTimeout(()=>process.exit(0),1500); return }
    let files=[]
    try{ files=fs.readdirSync(OUTDIR).filter(f=>f.endsWith('.txt')||f.endsWith('.json')).sort() }catch(e){ return }
    for(const f of files){
      const p=OUTDIR+'/'+f
      let job=null
      try{
        const raw=fs.readFileSync(p,'utf8')
        if(f.endsWith('.json')) job=JSON.parse(raw)
        else{
          const nl=raw.indexOf('\n'); if(nl<0){ fs.unlinkSync(p); continue }
          job={ jid:raw.slice(0,nl).trim(), kind:'text', text:raw.slice(nl+1) }
        }
      }catch(e){ console.log('OUTBOX_INVALID '+f+' '+e.message); try{fs.unlinkSync(p)}catch(e2){}; continue }
      const peNumar = job && (job.kind==='reply'||job.kind==='forward'||job.kind==='download')
      if(peNumar && !fs.existsSync(RAWDIR+'/'+job.n+'.json')){
        console.log('FARA_BRUT #'+job.n+' (mesaj de dinainte de upgrade)')
        ack(job.id,'FAILED','mesajul nu e salvat integral'); try{fs.unlinkSync(p)}catch(e){}; continue }
      const peCheie = job && (job.kind==='react'||job.kind==='edit'||job.kind==='delete'||job.kind==='seen')
      if(peCheie && !(job.key && job.key.id)){
        console.log('FARA_CHEIE '+f); ack(job.id,'FAILED','mesaj tinta necunoscut'); try{fs.unlinkSync(p)}catch(e){}; continue }
      if(!peCheie && !peNumar && (!job||!job.jid)){ try{fs.unlinkSync(p)}catch(e){}; continue }
      const faraText = ['doc','poll','typing','gadmin','gsubject','gdesc']
      if(!peCheie && !peNumar && !faraText.includes(job.kind) && !(job.text||'').trim()){ try{fs.unlinkSync(p)}catch(e){}; continue }

      if(job.kind==='doc'){
        if(!job.path||!fs.existsSync(job.path)){
          console.log('NO_FILE '+job.path); ack(job.id,'FAILED','fisier lipsa'); try{fs.unlinkSync(p)}catch(e){}; continue }
        if(dejaTrimis(docKey(job.jid,job.path))){
          console.log('DEJA_TRIMIS '+pathmod.basename(job.path)); ack(job.id,'SENT','duplicat, sarit')
          try{fs.unlinkSync(p)}catch(e){}; continue }
      }

      try{
        let sent
        if(job.kind==='react'){
          // reactie: text gol = SCOATE reactia
          await sock.sendMessage(job.key.remoteJid, { react:{ text: job.emoji||'', key: job.key } })
          try{ fs.unlinkSync(p) }catch(e){}
          delete INCERCARI[f]; ack(job.id,'SENT','')
          console.log('REACT '+(job.emoji||'(scos)')+' pe #'+(job.n||'?')); await new Promise(r=>setTimeout(r,800)); continue
        }
        if(job.kind==='delete'){
          // sterge pt toata lumea. Merge pe mesajele NOASTRE; pe ale altora doar daca suntem admin.
          await sock.sendMessage(job.key.remoteJid, { delete: job.key })
          try{ fs.unlinkSync(p) }catch(e){}
          delete INCERCARI[f]; ack(job.id,'SENT','')
          console.log('DELETE #'+(job.n||'?')); await new Promise(r=>setTimeout(r,800)); continue
        }
        if(job.kind==='edit'){
          // editarea merge DOAR pe mesajele proprii, si doar in fereastra permisa de WhatsApp (~15 min)
          await sock.sendMessage(job.key.remoteJid, { text: job.text, edit: job.key })
          try{ fs.unlinkSync(p) }catch(e){}
          delete INCERCARI[f]; ack(job.id,'SENT','')
          console.log('EDIT #'+(job.n||'?')); await new Promise(r=>setTimeout(r,800)); continue
        }
        if(job.kind==='poll'){
          sent=await sock.sendMessage(job.jid,{ poll:{ name:job.name, values:job.values,
                                                       selectableCount: job.multi ? job.values.length : 1 } })
          if(sent?.key?.id) MY_SENT.add(sent.key.id)
          try{ fs.unlinkSync(p) }catch(e){}
          delete INCERCARI[f]; ack(job.id,'SENT','')
          console.log('POLL "'+job.name+'" ('+job.values.length+' optiuni) -> '+(NAME[job.jid]||job.jid))
          await new Promise(r=>setTimeout(r,1200)); continue
        }
        if(job.kind==='seen'){
          await sock.readMessages([job.key])
          try{ fs.unlinkSync(p) }catch(e){}
          delete INCERCARI[f]; ack(job.id,'SENT','')
          console.log('SEEN #'+(job.n||'?')); await new Promise(r=>setTimeout(r,500)); continue
        }
        if(job.kind==='typing'){
          await sock.sendPresenceUpdate(job.state||'composing', job.jid)
          try{ fs.unlinkSync(p) }catch(e){}
          delete INCERCARI[f]; ack(job.id,'SENT','')
          console.log('TYPING '+(job.state||'composing')+' -> '+(NAME[job.jid]||job.jid))
          await new Promise(r=>setTimeout(r,300)); continue
        }
        if(job.kind==='gadmin' || job.kind==='gsubject' || job.kind==='gdesc'){
          // Actiuni de ADMIN pe grup. Scot/adaug oameni dintr-un grup real, deci se cer explicit
          // cu --apply din CLI; aici doar executam ce a fost deja confirmat.
          if(job.kind==='gadmin'){
            const rez = await sock.groupParticipantsUpdate(job.jid, job.numere, job.op)
            console.log('GADMIN '+job.op+' '+JSON.stringify(rez).slice(0,200))
          } else if(job.kind==='gsubject'){
            await sock.groupUpdateSubject(job.jid, job.subject); console.log('GSUBJECT -> '+job.subject)
          } else {
            await sock.groupUpdateDescription(job.jid, job.desc); console.log('GDESC actualizat')
          }
          try{ fs.unlinkSync(p) }catch(e){}
          delete INCERCARI[f]; ack(job.id,'SENT','')
          await new Promise(r=>setTimeout(r,1200)); continue
        }
        if(job.kind==='reply' || job.kind==='forward' || job.kind==='download'){
          const brut = JSON.parse(fs.readFileSync(RAWDIR+'/'+job.n+'.json','utf8'))
          if(job.kind==='reply'){
            const sent2=await sock.sendMessage(job.jid,{text:job.text},{quoted:brut})
            if(sent2?.key?.id) MY_SENT.add(sent2.key.id)
            console.log('REPLY la #'+job.n)
          } else if(job.kind==='forward'){
            const sent2=await sock.sendMessage(job.jid,{forward:brut})
            if(sent2?.key?.id) MY_SENT.add(sent2.key.id)
            console.log('FORWARD #'+job.n+' -> '+(NAME[job.jid]||job.jid))
          } else {
            const { downloadMediaMessage } = require('baileys')
            const buf = await downloadMediaMessage(brut, 'buffer', {})
            const dest = job.dest || (DIR+'/media')
            fs.mkdirSync(dest,{recursive:true})
            const mm = brut.message||{}
            const ext = mm.imageMessage?'.jpg' : mm.videoMessage?'.mp4' : mm.audioMessage?'.ogg'
                      : mm.stickerMessage?'.webp'
                      : (mm.documentMessage?.fileName ? pathmod.extname(mm.documentMessage.fileName) : '.bin')
            const cale = dest+'/'+job.n+ext
            fs.writeFileSync(cale, buf)
            console.log('MEDIA #'+job.n+' -> '+cale+' ('+buf.length+' octeti)')
            ack(job.id,'SENT',cale); try{ fs.unlinkSync(p) }catch(e){}; delete INCERCARI[f]
            await new Promise(r=>setTimeout(r,800)); continue
          }
          try{ fs.unlinkSync(p) }catch(e){}
          delete INCERCARI[f]; ack(job.id,'SENT','')
          await new Promise(r=>setTimeout(r,1200)); continue
        }
        if(job.kind==='doc'){
          sent=await sock.sendMessage(job.jid, continutFisier(job.path, job.caption||''))
          try{ fs.appendFileSync(SENTLOG, docKey(job.jid,job.path)+'\t'+new Date().toISOString()+'\n') }catch(e){}
        } else {
          sent=await sock.sendMessage(job.jid,{text:job.text})
        }
        if(sent?.key?.id) MY_SENT.add(sent.key.id)
        try{ fs.unlinkSync(p) }catch(e){}
        delete INCERCARI[f]; ack(job.id,'SENT','')
        // imi notez si mesajele MELE, altfel nu am cum sa le editez sau sa le sterg mai tarziu
        let n='?'
        if(sent?.key?.id){
          n = noteaza({ t:new Date().toISOString(), grup:NAME[job.jid]||job.jid, type:'trimis',
                        from:'BOT', text: job.kind==='doc' ? ('[fisier] '+pathmod.basename(job.path)) : job.text,
                        key:{ remoteJid:job.jid, fromMe:true, id:sent.key.id } })
          // Salvez brutul SI pentru mesajele mele. Fara asta, `reply`/`fwd` pe ce am scris eu
          // esuau cu FARA_BRUT — raw-ul se scria doar la RECEPTIE. (masurat 4-sep, pe #388)
          try{ fs.mkdirSync(RAWDIR,{recursive:true}); fs.writeFileSync(RAWDIR+'/'+n+'.json', JSON.stringify(sent)) }catch(e){}
        }
        console.log('SENT '+f+' #'+n+' -> '+(NAME[job.jid]||job.jid))
      }catch(e){
        INCERCARI[f]=(INCERCARI[f]||0)+1
        console.log('SEND_ERR '+f+' (incercarea '+INCERCARI[f]+'/'+MAX_INCERCARI+') '+e.message)
        if(INCERCARI[f]>=MAX_INCERCARI){
          try{ fs.mkdirSync(FAILDIR,{recursive:true}); fs.renameSync(p,FAILDIR+'/'+f) }catch(e2){}
          ack(job.id,'FAILED',e.message); delete INCERCARI[f]
          console.log('ESUAT DEFINITIV '+f+' -> multi_out/esuate/  (nu s-a pierdut, e acolo)')
        }
      }
      await new Promise(r=>setTimeout(r,1500))
    }
  },2000)
}
start().catch(e=>{ console.log('FATAL '+e.message); process.exit(9) })
