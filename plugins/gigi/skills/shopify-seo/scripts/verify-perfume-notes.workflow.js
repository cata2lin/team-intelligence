export const meta = {
  name: 'verify-perfume-notes',
  description: 'Verify each perfume\'s note metafields (top/heart/base, gender, family) against the REAL original perfume and propose corrections',
  phases: [{ title: 'Verify', detail: 'agents check batches against real fragrance notes' }],
}

const SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    checked: { type: 'number' },
    fixes: {
      type: 'array',
      items: {
        type: 'object',
        additionalProperties: false,
        properties: {
          handle: { type: 'string' },
          original: { type: 'string' },
          reason: { type: 'string' },
          varf: { type: 'string' },
          note_inima: { type: 'string' },
          note_baza: { type: 'string' },
          sex: { type: 'string', description: 'exact: Femei | Barbati | Unisex' },
          note_parfum: { type: 'string', description: 'comma list of olfactory families' },
          mom_zi: { type: 'string', description: 'Zi | Noapte | Zi, Noapte' },
        },
        required: ['handle', 'reason'],
      },
    },
  },
  required: ['checked', 'fixes'],
}

const STORES = [['nubra', 151], ['esteban', 153]]
const SIZE = 13
const batches = []
for (const [store, count] of STORES) {
  for (let s = 0; s < count; s += SIZE) batches.push({ store, start: s, end: Math.min(s + SIZE, count) })
}
log(`${batches.length} loturi de verificat (lot=${SIZE})`)

const prompt = (b) => `Ești expert în parfumerie. Verifici acuratețea notelor olfactive stocate vs parfumul ORIGINAL real.

Citește fișierul JSON /tmp/verify_${b.store}.json (listă de produse cu {handle,title,inspired_by,varf,note_inima,note_baza,note_parfum,sex,mom_zi}). Procesează DOAR elementele cu index ${b.start}..${b.end - 1} inclusiv.

Pentru fiecare produs care are inspired_by (ex: "Black Opium by Yves Saint Laurent"):
1. Identifică parfumul ORIGINAL din inspired_by.
2. Reamintește-ți piramida olfactivă REALĂ, documentată, a originalului: note de VÂRF (top), de INIMĂ (heart), de BAZĂ (base), genul (Femei/Barbati/Unisex), familia olfactivă, momentul (Zi/Noapte).
3. Compară cu valorile stocate (varf=vârf, note_inima=inimă, note_baza=bază, sex=gen, note_parfum=familie, mom_zi=moment).
4. Dacă valorile stocate sunt GREȘITE (diferă semnificativ de realitate) ȘI ești SIGUR pe notele reale → adaugă în "fixes" un obiect cu handle + reason (scurt) + DOAR câmpurile care trebuie corectate, cu valori CORECTE în română.

REGULI STRICTE:
- Fii CONSERVATOR: corectează DOAR dacă ești sigur pe notele reale documentate ale parfumului. Dacă e obscur/nesigur → NU-l include (lasă cum e).
- Stil note: listă cu virgulă, română, litere mici, ex: "piper roz, pără, flori de portocal". (varf/note_inima/note_baza = text simplu, nu JSON)
- sex EXACT: "Femei" / "Barbati" / "Unisex" (fără diacritice). note_parfum = familii separate prin virgulă din vocabularul: Oriental, Floral, Lemnos, Dulce, Fresh, Fructat, Aromatic, Gourmand, Condimentat, Acvatic, Vanilat, Cypre, Aldehidic. mom_zi: "Zi" / "Noapte" / "Zi, Noapte".
- Include în "fixes" DOAR câmpurile efectiv greșite (omite-le pe cele corecte). Nu inventa note.
- NU aplica nimic în Shopify; doar returnează structura.

Returnează {checked: <câte ai verificat>, fixes: [...]}.`

const results = await parallel(batches.map((b) => () =>
  agent(prompt(b), { label: `verify:${b.store}:${b.start}`, phase: 'Verify', schema: SCHEMA })
))

const ok = results.filter(Boolean)
const allFixes = ok.flatMap((r) => (r.fixes || []).map((f) => ({ ...f })))
const checked = ok.reduce((a, r) => a + (r.checked || 0), 0)
log(`verificate ${checked} · corecturi propuse ${allFixes.length}`)
return { checked, fixesCount: allFixes.length, fixes: allFixes }
