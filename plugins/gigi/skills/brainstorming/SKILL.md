---
name: brainstorming
description: Rafinează o idee brută într-un design aprobat ÎNAINTE de a construi/scrie cod/lansa — prin întrebări una-câte-una (socratic), nu monolog. Scoate la iveală ipotezele nespuse, alternativele și capcanele cât sunt ieftine (în chat), nu scumpe (după ce ai construit). Se termină cu un mini design-doc pe care userul îl aprobă. Anti-rework: cel mai mare consumator de tokeni/timp e munca greșită refăcută. Use pentru „hai să ne gândim la X", „vreau să fac Y, cum abordez", „design înainte de cod", „ce skill/feature/campanie construim", „nu sunt sigur cum să structurez", „brainstorming", sau ORICE task nou ambiguu înainte de a te apuca.
argument-hint: "descrie ideea brută → întrebări 1-câte-1 → design-doc aprobat"
---

# brainstorming
> Author: **Gigi**. Design-înainte-de-construit. Regula: **nu te apuca de treabă până designul nu e aprobat.**

## Când
Orice task nou care nu e trivial și nu e 100% specificat: un skill nou, un feature, un raport, o
campanie, o schimbare de pipeline, o migrare. Dacă te-ai prins că „încep și văd pe parcurs" — stop, fă asta.

## Cum (regulile de aur)
1. **O întrebare pe rând.** Nu turna 10 întrebări; pui UNA, aștepți răspunsul, apoi următoarea. Fiecare
   întrebare pleacă din răspunsul anterior. (Un perete de întrebări = userul răspunde la jumătate.)
2. **Sapă după ce NU s-a spus** — scopul real, constrângerile (buget/timp/date/permisiuni), cazul de
   eșec, cine folosește rezultatul, ce înseamnă „gata". Contradicțiile ies acum, ieftin.
3. **Propune 2-3 alternative** cu compromisuri, nu doar prima idee. Recomandă una, spune de ce.
4. **Nu scrie cod / nu construi în timpul brainstorming-ului.** Doar clarifici și proiectezi.
5. **Ieși cu un DESIGN-DOC scurt** (vezi mai jos) și cere aprobarea explicită înainte de a trece la
   [[plan-first]] sau la construit.

## Design-doc (formatul de ieșire)
```
## <Titlu>
Problema:        <ce rezolvăm, în 1-2 fraze — și de ce acum>
Utilizator/scop: <cine folosește + ce înseamnă succes, măsurabil>
Abordarea aleasă: <varianta recomandată> — pentru că <motiv>
Alternative respinse: <A (de ce nu), B (de ce nu)>
Constrângeri:    <buget/timp/date/permisiuni/securitate — ce e non-negociabil>
Riscuri & necunoscute: <ce poate strica, ce nu știm încă>
Out of scope:    <ce NU facem acum>
```

## Note
- Pentru munca de ecom/ops (nu doar cod): „ipotezele nespuse" = pe ce date te bazezi (AWBprint vs
  metrics vs Shopify — vezi [[profit-data-sources-truth]]), ce magazine/branduri, ce monedă/TVA.
- La final, dacă e task de execuție → **[[plan-first]]** (transformă designul în plan checkpointat).
- Nu confunda cu [[deep-research]] (ăla adună surse externe; ăsta clarifică intern ce construim).
- Extras din metodologia obra/superpowers (brainstorming), adaptat la echipa Arona.
