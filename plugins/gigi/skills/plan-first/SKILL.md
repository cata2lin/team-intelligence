---
name: plan-first
description: Transformă un design aprobat într-un PLAN scris, pe pași mici, verificabili, salvat ca FIȘIER — apoi îl execuți un pas pe rând, cu puncte de control, oprindu-te la primul blocaj în loc să ghicești. Planul-ca-fișier = memorie externă: subagenții/tu îl citesc din disc în loc să țină tot în context (economie de tokeni) și munca e reluabilă după compactare. Anti-rework + anti-„am făcut jumătate greșit". Use pentru „fă un plan", „împarte în pași", „cum execut asta", „plan de implementare", „task mare/multi-pas", „vreau să pot relua dacă se întrerupe", sau după ce ai un design din [[brainstorming]].
argument-hint: "design aprobat → plan pe pași ca fișier → execuție 1-pas-pe-rând cu checkpoints"
---

# plan-first
> Author: **Gigi**. Plan scris ca fișier, execuție checkpointată. **Nu ghici — oprește-te și întreabă.**

## Când
Task de execuție cu ≥3 pași sau care atinge date/sisteme reale (mutații, migrări, deploy, un skill nou,
un raport multi-sursă). Dacă e trivial (1-2 pași reversibili), sari peste — nu birocratiza.

## Cum
1. **Scrie planul într-un FIȘIER** (`scratchpad/plan-<task>.md` sau în repo dacă e durabil), nu doar în
   chat. De ce fișier: (a) supraviețuiește compactării/întreruperii, (b) un subagent îl citește din disc
   în loc să-i torni tot contextul (mai puțini tokeni), (c) e o listă bifabilă.
2. **Pași mici, fiecare verificabil independent** — fiecare pas are: ce faci, ce fișiere/comenzi,
   și **cum verifici că a mers** (comanda/observația concretă). „Suficient de clar cât un coleg fără
   context să-l execute."
3. **Execută UN pas pe rând**, bifează, verifică înainte să treci mai departe. La mutații reale:
   dry-run întâi (vezi [[scentum-erp-cli]]/[[scripts-app-cli]] — `--apply` doar după ce vezi previzualizarea).
4. **Oprește-te la primul blocaj / necunoscută și întreabă** — nu inventa o valoare, nu sări peste.
   Un pas eșuat nu contaminează restul.
5. La final: verifică rezultatul end-to-end (dovadă, nu presupunere) înainte de „gata".

## Formatul planului
```
# Plan: <task>   (design: <link/rezumat>)
- [ ] 1. <acțiune> → fișiere/cmd: <...> → verific: <cum știu că a mers>
- [ ] 2. ...
- [ ] N. Verificare finală end-to-end: <comanda + ce aștept>
Rollback: <cum revin dacă stric ceva>   Backup: <unde>
```

## Note
- **Memorie externă = economie de tokeni**: planul + rezultatele intermediare stau pe disc; nu reciti
  fișiere pe care le-ai citit deja, nu repeta conținut în chat.
- Pentru fan-out (pași independenți) → dă-i pe subagenți paraleli, fiecare cu brief-ul lui din plan.
- Cuplat cu [[brainstorming]] (înainte: designul) și cu verificarea (`/verify`) + [[debug-systematic]]
  (când un pas pică). Extras din obra/superpowers (writing-plans + executing-plans), unificat.
