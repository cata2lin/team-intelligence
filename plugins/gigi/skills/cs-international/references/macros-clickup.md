# Macro-uri CS multilingve (copie din ClickUp)

> Sursa: documentul ClickUp „Pachet Macros & FAQ — Customer Service" (`2kyqg8j1-3895`, pagina `2kyqg8j1-1315`), Secțiunea B,
> copiat pe 19-oct-2026. **Sursa de adevăr rămâne ClickUp**: dacă cineva schimbă macro-ul acolo, copia de aici rămâne în urmă.
> Recitire: `GET https://api.clickup.com/api/v3/workspaces/<ws>/docs/2kyqg8j1-3895/pages?content_format=text/md` cu `CLICKUP_API_TOKEN` (doar citire).
>
> ⚠️ Există DOAR CZ, PL, BG, EN. **Nu există HU, SK, GR, RU** (pentru ele vezi formulările validate din `piete.md`).
> ⚠️ Toate presupun DPD + ramburs + 1–3 zile. Verifică pe piață (`politici.json`): Duppo are și plată cu cardul, MD merge cu FAN Moldova.
> ⚠️ `Retur-formular-*` trimite `{link_retur}` — **doar Grandia are formular de retur**. Pe Duppo/Bonhaus returul se cere pe e-mail
>   (vezi `piete.md`), deci NU folosi `Retur-formular` cu un link inventat; folosește doar fraza cu IBAN.
> ⚠️ `Retur-formular-*` promite banii în 14 zile „de la primirea coletului"; pe MD termenul legal curge de la notificare (vezi politica MD).
> ⚠️ `WISMO-no-data-*` conține „vă vom anunța imediat" (promisiune de revenire): OK doar dacă chiar răspunde un om pe piața aceea.

### Cehă (CZ)

**WISMO-tracking-CZ** — _WISMO_

```css
Dobrý den, {client},

děkujeme za Vaši zprávu. Vaši objednávku {comanda} jsme již předali přepravci DPD. Aktuální stav zásilky můžete sledovat zde:

https://tracking.dpd.ro?shipmentNumber={awb}

Balík bude doručen kurýrem DPD během 1–3 pracovních dnů. Pokud byste cokoli potřeboval/a, jsme Vám rádi k dispozici.

S pozdravem,
tým {magazin}
```

**WISMO-no-data-CZ** — _WISMO_

```css
Dobrý den, {client},

děkujeme, že jste se na nás obrátil/a. Rádi pro Vás stav objednávky ihned zkontrolujeme. Abychom ji mohli v systému dohledat, mohl/a byste nám prosím poslat Vaše jméno a příjmení a telefonní číslo, případně číslo objednávky?

Jakmile tyto údaje obdržíme, dáme Vám okamžitě vědět, kde se Vaše zásilka nachází.

S pozdravem,
tým {magazin}
```

**Retur-formular-CZ** — _Retur_

```css
Dobrý den, {client},

děkujeme za Vaši zprávu. Vrácení objednávky {comanda} pro Vás rádi vyřídíme. Stačí vyplnit jednoduchý formulář pro vrácení zde:

{link_retur}

Jakmile se balík vrátí k nám, odešleme Vám částku zpět nejpozději do 14 dnů od jeho doručení. Pokud by se Vám formulář nezobrazil nebo hlásil chybu, pošlete nám prosím číslo účtu (IBAN) a jméno majitele účtu a my vrácení vyřídíme ručně.

S pozdravem,
tým {magazin}
```

**Anulare-confirmata-CZ** — _Anulare_

```css
Dobrý den, {client},

děkujeme, že jste nás informoval/a. Potvrzujeme, že Vaše objednávka {comanda} byla zrušena a nebude Vám účtována ani odeslána.

Mrzí nás, že to tentokrát nevyšlo, a budeme se těšit, až Vás budeme moci obsloužit příště.

S pozdravem,
tým {magazin}
```

**Produs-defect-CZ** — _Produs defect_

```css
Dobrý den, {client},

je nám velmi líto, že produkt z objednávky {comanda} dorazil poškozený. Rádi to pro Vás co nejrychleji vyřešíme.

Mohl/a byste nám prosím poslat fotografii poškozeného produktu? Na základě fotografie Vám obratem zajistíme výměnu nebo nové zaslání, abyste vše obdržel/a v pořádku.

Děkujeme za pochopení a omlouváme se za vzniklé nepříjemnosti.

S pozdravem,
tým {magazin}
```

**Presale-livrare-CZ** — _Presale_

```css
Dobrý den, {client},

děkujeme za Váš zájem. Produkt máme skladem a připravený k odeslání. Doručení probíhá kurýrem DPD během 1–3 pracovních dnů a k dispozici je i platba na dobírku (při převzetí).

Stačí dokončit objednávku a my se postaráme o rychlé odeslání. Pokud byste měl/a jakýkoli dotaz, jsme Vám rádi k dispozici.

S pozdravem,
tým {magazin}
```

### Poloneză (PL)

**WISMO-tracking-PL** — _WISMO_

```cpp
Dzień dobry, {client},

dziękujemy za wiadomość. Twoje zamówienie {comanda} zostało już przekazane firmie kurierskiej DPD. Aktualny status przesyłki możesz śledzić tutaj:

https://tracking.dpd.ro?shipmentNumber={awb}

Paczka zostanie dostarczona przez kuriera DPD w ciągu 1–3 dni roboczych. W razie jakichkolwiek pytań chętnie pomożemy.

Z pozdrowieniami,
zespół {magazin}
```

**WISMO-no-data-PL** — _WISMO_

```erlang
Dzień dobry, {client},

dziękujemy za kontakt. Chętnie od razu sprawdzimy status Twojego zamówienia. Aby odnaleźć je w naszym systemie, prosimy o podanie imienia i nazwiska oraz numeru telefonu lub numeru zamówienia.

Gdy tylko otrzymamy te dane, niezwłocznie poinformujemy Cię, gdzie znajduje się Twoja przesyłka.

Z pozdrowieniami,
zespół {magazin}
```

**Retur-formular-PL** — _Retur_

```css
Dzień dobry, {client},

dziękujemy za wiadomość. Chętnie pomożemy w zwrocie zamówienia {comanda}. Wystarczy wypełnić prosty formularz zwrotu tutaj:

{link_retur}

Po otrzymaniu przez nas paczki zwrócimy należną kwotę najpóźniej w ciągu 14 dni od jej dostarczenia. Jeśli formularz nie wyświetli się poprawnie lub pojawi się błąd, prosimy przesłać numer rachunku (IBAN) oraz imię i nazwisko właściciela konta, a my zrealizujemy zwrot ręcznie.

Z pozdrowieniami,
zespół {magazin}
```

**Anulare-confirmata-PL** — _Anulare_

```css
Dzień dobry, {client},

dziękujemy za informację. Potwierdzamy, że zamówienie {comanda} zostało anulowane — nie zostanie wysłane ani obciążone żadną opłatą.

Przykro nam, że tym razem nie doszło do finalizacji, i mamy nadzieję, że będziemy mogli Cię obsłużyć przy następnej okazji.

Z pozdrowieniami,
zespół {magazin}
```

**Produs-defect-PL** — _Produs defect_

```css
Dzień dobry, {client},

bardzo nam przykro, że produkt z zamówienia {comanda} dotarł uszkodzony. Chętnie rozwiążemy to dla Ciebie jak najszybciej.

Czy mógłbyś/mogłabyś przesłać nam zdjęcie uszkodzonego produktu? Na jego podstawie od razu zorganizujemy wymianę lub ponowną wysyłkę, aby wszystko dotarło do Ciebie w idealnym stanie.

Dziękujemy za wyrozumiałość i przepraszamy za niedogodności.

Z pozdrowieniami,
zespół {magazin}
```

**Presale-livrare-PL** — _Presale_

```css
Dzień dobry, {client},

dziękujemy za zainteresowanie. Produkt jest dostępny w magazynie i gotowy do wysyłki. Dostawa realizowana jest kurierem DPD w ciągu 1–3 dni roboczych, a płatność za pobraniem (przy odbiorze) jest dostępna.

Wystarczy złożyć zamówienie, a my zadbamy o szybką wysyłkę. W razie jakichkolwiek pytań jesteśmy do Twojej dyspozycji.

Z pozdrowieniami,
zespół {magazin}
```

### Bulgară (BG)

**WISMO-tracking-BG** — _WISMO_

```cpp
Здравейте, {client},

благодарим за съобщението. Вашата поръчка {comanda} вече е предадена на куриера DPD. Можете да проследите актуалния статус на пратката тук:

https://tracking.dpd.ro?shipmentNumber={awb}

Пратката ще бъде доставена от куриер на DPD в рамките на 1–3 работни дни. Ако имате нужда от нещо, с удоволствие ще Ви съдействаме.

С уважение,
екипът на {magazin}
```

**WISMO-no-data-BG** — _WISMO_

```erlang
Здравейте, {client},

благодарим, че се свързахте с нас. С удоволствие ще проверим статуса на поръчката Ви веднага. За да я открием в системата, бихте ли ни изпратили Вашите име и фамилия и телефонен номер или номера на поръчката?

Веднага щом получим тези данни, незабавно ще Ви уведомим къде се намира пратката Ви.

С уважение,
екипът на {magazin}
```

**Retur-formular-BG** — _Retur_

```css
Здравейте, {client},

благодарим за съобщението. С удоволствие ще обработим връщането на поръчка {comanda}. Достатъчно е да попълните този кратък формуляр за връщане:

{link_retur}

След като пратката се върне при нас, ще възстановим сумата най-късно до 14 дни от получаването ѝ. Ако формулярът не се зарежда или дава грешка, моля изпратете ни банкова сметка (IBAN) и името на титуляря, и ще обработим връщането ръчно.

С уважение,
екипът на {magazin}
```

**Anulare-confirmata-BG** — _Anulare_

```erlang
Здравейте, {client},

благодарим, че ни уведомихте. Потвърждаваме, че поръчка {comanda} е анулирана — тя няма да бъде изпратена, нито таксувана.

Съжаляваме, че този път не се получи, и ще се радваме да Ви бъдем полезни при следваща поръчка.

С уважение,
екипът на {magazin}
```

**Produs-defect-BG** — _Produs defect_

```erlang
Здравейте, {client},

много съжаляваме, че продуктът от поръчка {comanda} е пристигнал повреден. С удоволствие ще решим това за Вас възможно най-бързо.

Бихте ли ни изпратили снимка на повредения продукт? Въз основа на снимката веднага ще организираме замяна или повторно изпращане, за да получите всичко в перфектно състояние.

Благодарим за разбирането и се извиняваме за причиненото неудобство.

С уважение,
екипът на {magazin}
```

**Presale-livrare-BG** — _Presale_

```erlang
Здравейте, {client},

благодарим за интереса. Продуктът е в наличност и готов за изпращане. Доставката се извършва с куриер DPD в рамките на 1–3 работни дни, като е възможно и плащане с наложен платеж (при получаване).

Достатъчно е да завършите поръчката и ние ще се погрижим за бързото изпращане. Ако имате въпроси, на Ваше разположение сме.

С уважение,
екипът на {magazin}
```

### Engleză (EN)

**WISMO-tracking-EN** — _WISMO_

```rust
Hello {client},

Thank you for your message. Your order {comanda} has already been handed over to our courier DPD. You can track the current status of your parcel here:

https://tracking.dpd.ro?shipmentNumber={awb}

Your parcel will be delivered by a DPD courier within 1–3 working days. If there's anything else we can help with, we're happy to assist.

Kind regards,
The {magazin} Team
```

**WISMO-no-data-EN** — _WISMO_

```rust
Hello {client},

Thank you for reaching out. We'd be glad to check the status of your order right away. To locate it in our system, could you please send us your full name and phone number, or your order number?

As soon as we have these details, we'll let you know exactly where your parcel is.

Kind regards,
The {magazin} Team
```

**Retur-formular-EN** — _Retur_

```rust
Hello {client},

Thank you for your message. We'd be happy to process the return of order {comanda} for you. Simply fill in this short return form:

{link_retur}

Once the parcel reaches us, we'll refund the amount within a maximum of 14 days from its arrival. If the form doesn't load or returns an error, please send us your bank account number (IBAN) and the account holder's name, and we'll process the refund manually.

Kind regards,
The {magazin} Team
```

**Anulare-confirmata-EN** — _Anulare_

```dpr
Hello {client},

Thank you for letting us know. We confirm that your order {comanda} has been cancelled — it will not be shipped or charged.

We're sorry it didn't work out this time, and we look forward to serving you on your next order.

Kind regards,
The {magazin} Team
```

**Produs-defect-EN** — _Produs defect_

```rust
Hello {client},

We're very sorry to hear that the product from order {comanda} arrived damaged. We'd be glad to sort this out for you as quickly as possible.

Could you please send us a photo of the damaged product? Based on the photo, we'll arrange a replacement or a re-shipment right away, so that everything reaches you in perfect condition.

Thank you for your understanding, and we apologise for the inconvenience.

Kind regards,
The {magazin} Team
```

**Presale-livrare-EN** — _Presale_

```dpr
Hello {client},

Thank you for your interest. The product is in stock and ready to ship. Delivery is handled by DPD courier within 1–3 working days, and cash on delivery (payment on receipt) is also available.

All you need to do is place your order, and we'll take care of the fast shipping. If you have any questions, we're here to help.

Kind regards,
The {magazin} Team
```

* * *

