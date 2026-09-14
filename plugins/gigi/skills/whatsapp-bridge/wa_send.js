/* wa_send.js — trimite un mesaj sau un PDF pe WhatsApp.
 *
 * Trei lucruri reparate (4-sep-2026), toate masurate in productie:
 *
 * 1. UN SINGUR SOCKET pe `auth`. Daca `multi_bot.js` ruleaza, scriptul asta NU mai deschide
 *    o a doua sesiune — pune treaba in outbox si o lasa pe cea vie s-o trimita. Doua sesiuni
 *    pe aceleasi credentiale se dau afara reciproc (`CLOSED sc=440`), iar mesajele cad cu
 *    „Connection Closed". Masurat: 4 reconectari in 50s si 2 mesaje pierdute.
 * 2. REINCEARCA. Inainte iesea la prima eroare (`process.exit(2)`), desi cele mai multe esecuri
 *    sunt tranzitorii (fereastra de reconectare).
 * 3. SERIALIZARE intre procese. Daca nu exista listener, mai multe apeluri simultane (mai multi
 *    agenti, sau o bucla pornita de doua ori) ar deschide fiecare cate o sesiune si s-ar da afara
 *    reciproc. Acum se iau la rand pe un lock cu detectie de lock mort — al doilea ASTEAPTA, nu
 *    concureaza. Regula pe scurt: **nimeni nu deschide socket propriu, toata lumea trece pe aici.**
 * 4. NU RETRIMITE un PDF deja trimis. Evidenta in `sent_docs.log`, cheia = jid|nume|marime.
 *    `--force` trece peste.
 *
 * Contractul de iesire e neschimbat — `SENT doc -> <jid>` — fiindca `awb_zilnic.py` si
 * `awb_station.py` cauta exact sirul asta ca sa stie ca a plecat.
 */
const { default: makeWASocket, useMultiFileAuthState, fetchLatestBaileysVersion } = require('baileys')
const pino = require('pino'); const fs = require('fs'); const path = require('path')

const DIR = '/root/wa-bridge'
const AUTH = DIR + '/auth'
const OUTDIR = DIR + '/multi_out'
const SENTLOG = DIR + '/sent_docs.log'
const ACKS = DIR + '/outbox_acks.log'
const PIDF = DIR + '/multi_bot.pid'
const LOCK = DIR + '/.send.lock'

const argv = process.argv.slice(2).filter(a => a !== '--force')
const FORCE = process.argv.includes('--force')
const jid = argv[0], mode = argv[1], payload = argv[2], caption = argv[3] || ''

if (!jid || !mode || !payload) {
  console.log('USAGE: wa_send.js <jid> <text|doc> <mesaj|cale.pdf> [caption] [--force]')
  process.exit(64)
}

function docKey(j, p) {
  let size = 0
  try { size = fs.statSync(p).size } catch (e) { }
  return j + '|' + path.basename(p) + '|' + size
}
function alreadySent(key) {
  try {
    return fs.readFileSync(SENTLOG, 'utf8').split('\n').some(l => l.split('\t')[0] === key)
  } catch (e) { return false }
}
function markSent(key) {
  try { fs.appendFileSync(SENTLOG, key + '\t' + new Date().toISOString() + '\n') } catch (e) { }
}
function listenerAlive() {
  try {
    const pid = parseInt(fs.readFileSync(PIDF, 'utf8').trim(), 10)
    if (!pid) return false
    process.kill(pid, 0)          // nu omoara: doar testeaza ca exista
    return true
  } catch (e) { return false }
}

// ── garda de duplicat, inainte de orice ─────────────────────────────────────
let key = null
if (mode === 'doc') {
  if (!fs.existsSync(payload)) { console.log('NO_FILE ' + payload); process.exit(66) }
  key = docKey(jid, payload)
  if (!FORCE && alreadySent(key)) {
    console.log('DEJA_TRIMIS ' + path.basename(payload) + ' -> ' + jid)
    process.exit(0)
  }
}

// ── calea 1: listener-ul e viu -> pune in outbox, el trimite ────────────────
async function prinListener() {
  const id = 'send_' + process.pid + '_' + Math.random().toString(36).slice(2, 8)
  const job = { id, jid, kind: mode === 'doc' ? 'doc' : 'text', caption }
  if (mode === 'doc') job.path = payload; else job.text = payload
  try { fs.mkdirSync(OUTDIR, { recursive: true }) } catch (e) { }
  fs.writeFileSync(OUTDIR + '/' + id + '.json', JSON.stringify(job))

  const pana = Date.now() + 180000
  while (Date.now() < pana) {
    await new Promise(r => setTimeout(r, 2000))
    let acks = ''
    try { acks = fs.readFileSync(ACKS, 'utf8') } catch (e) { }
    const linia = acks.split('\n').find(l => l.startsWith(id + '\t'))
    if (linia) {
      const [, stare, detaliu] = linia.split('\t')
      if (stare === 'SENT') { if (key) markSent(key); console.log('SENT ' + mode + ' -> ' + jid); return 0 }
      console.log('SEND_ERR ' + (detaliu || 'esuat in outbox')); return 2
    }
    if (!listenerAlive()) {       // a murit intre timp: iau eu treaba inapoi
      try { fs.unlinkSync(OUTDIR + '/' + id + '.json') } catch (e) { }
      console.log('LISTENER_MORT trimit direct')
      return null
    }
  }
  try { fs.unlinkSync(OUTDIR + '/' + id + '.json') } catch (e) { }
  console.log('SEND_ERR listener-ul nu a confirmat in 180s')
  return 2
}

// ── lock intre procese, ca doua trimiteri directe sa nu se dea afara ────────
function pidViu(pid) { try { process.kill(pid, 0); return true } catch (e) { return false } }

async function iaLock(timeoutMs) {
  const pana = Date.now() + timeoutMs
  while (Date.now() < pana) {
    try {
      const fd = fs.openSync(LOCK, 'wx')      // atomic: pica daca exista deja
      fs.writeSync(fd, String(process.pid)); fs.closeSync(fd)
      return true
    } catch (e) {
      let detinator = 0
      try { detinator = parseInt(fs.readFileSync(LOCK, 'utf8').trim(), 10) } catch (e2) { }
      if (!detinator || !pidViu(detinator)) {  // lock ramas de la un proces mort
        try { fs.unlinkSync(LOCK) } catch (e2) { }
        continue
      }
      await new Promise(r => setTimeout(r, 2000))
    }
  }
  return false
}
function dalockul() { try { fs.unlinkSync(LOCK) } catch (e) { } }

// ── calea 2: nimeni nu tine sesiunea -> deschid eu una, dar pe rand ─────────
async function direct() {
  const { version } = await fetchLatestBaileysVersion()
  const { state, saveCreds } = await useMultiFileAuthState(AUTH)
  const sock = makeWASocket({
    version, auth: state, logger: pino({ level: 'silent' }),
    markOnlineOnConnect: false, browser: ['Arona Bridge', 'Chrome', '1.0']
  })
  sock.ev.on('creds.update', saveCreds)

  return await new Promise((resolve) => {
    let incercari = 0, gata = false
    sock.ev.on('connection.update', async (u) => {
      if (u.connection === 'open' && !gata) {
        for (incercari = 1; incercari <= 3; incercari++) {
          try {
            if (mode === 'doc') {
              await sock.sendMessage(jid, {
                document: fs.readFileSync(payload), mimetype: 'application/pdf',
                fileName: path.basename(payload), caption
              })
            } else {
              await sock.sendMessage(jid, { text: payload })
            }
            gata = true
            if (key) markSent(key)
            console.log('SENT ' + mode + ' -> ' + jid)
            setTimeout(() => resolve(0), 3500)
            return
          } catch (e) {
            console.log('incercarea ' + incercari + ' a picat: ' + e.message)
            await new Promise(r => setTimeout(r, 3000 * incercari))
          }
        }
        console.log('SEND_ERR dupa 3 incercari')
        resolve(2)
      }
      if (u.connection === 'close') {
        const sc = u.lastDisconnect?.error?.output?.statusCode
        if (sc === 401) { console.log('LOGGED_OUT'); resolve(3) }
        // sc=440 = alta sesiune ne-a luat locul; nu insistam, iesim curat
        if (sc === 440 && !gata) { console.log('SEND_ERR conflict de sesiune (sc=440)'); resolve(2) }
      }
    })
  })
}

; (async () => {
  let cod = null
  if (listenerAlive()) cod = await prinListener()
  if (cod === null) {
    if (!await iaLock(240000)) { console.log('SEND_ERR alta trimitere tine sesiunea de 4 min'); process.exit(2) }
    try { cod = await direct() } finally { dalockul() }
  }
  process.exit(cod)
})().catch(e => { console.log('FATAL ' + e.message); process.exit(9) })
