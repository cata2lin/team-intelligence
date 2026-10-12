/**
 *  RAPORT ZILNIC AZI – versiune îmbunătățită
 *  Fix: Google clicks/impressions foloseau col C (Tiktok) din Mapping -> acum col E (Google).
 *  + inline Mapping (FB regex / Shopify / Google), Shopify revenue 1×FILTER, AOV ieri mărginit.
 *  Magdeal/Ofertele Zilei: citesc din 'Facebook Ads azi'/'Shopify azi' ca toate brandurile.
 *    Tab-urile dedicate 'Facebook azi Magdeal'/'Shopify azi Magdeal' (add-on) rămâneau în urmă
 *    cu o zi -> 0 la FB/Shopify. Conturile Esteban 3 (Ofertele Zilei) / Reflexino (Magdeal) și
 *    magazinele MagDeal.ro / Ofertele-Zilei.ro sunt în sheet-urile principale, nepartajate
 *    (Esteban = ^(ESTEBAN|ESTEBAN2)$ NU prinde Esteban 3) -> fără dublă-numărare. I,J,K,L = 0.
 */
function adaugaRandZilnicAzi() {
  const ss = SpreadsheetApp.openById('1IVg0fI-_Rm7IptmOl3BmGrqtyyzn3auf0ZPuftr9vQo');
  const sheet = ss.getSheetByName('Raport azi');
  const cursSheet = ss.getSheetByName('Curs valutar');
  if (!sheet || !cursSheet) return;

  const BRANDS = [
    'Apreciat','Carpetto',
    'Esteban','George Talent','Magdeal','Gento',
    'Nocturna','Nocturna Lux',
    'Reduceri bune','Rossi Nails','Bonhaus CZ','Bonhaus RO','Bonhaus PL','Bonhaus BG','Ofertele Zilei','Orice Redus','Nubra','Lab Noir','Grandia','Duppo BG','Duppo CZ','Duppo HU','Duppo MD','Artevita','De la Bucsa'
  ];

  /* Duppo EU = UN magazin (base EUR) servind 3 piete (BG/CZ/HU) cu monede diferite.
     Sursa cu shipping country (col J) = tabul add-on 'Shopify [azi] Magdeal'. Venitul (F:I) e in
     moneda LOCALA -> ×curs brand; COGS (col E) e in EUR baza -> ×curs Duppo BG (EUR) mereu. */
  const DUPPO_SPLIT = {
    'Duppo BG': { store: 'Duppo BG', country: 'Bulgaria' },
    'Duppo CZ': { store: 'Duppo BG', country: 'Czech Republic' },
    'Duppo HU': { store: 'Duppo BG', country: 'Hungary' }
  };

  /* Curs valutar + flag-uri */
  const cursVal = {}, exclFB = {}, incTik = {}, incShop = {};
  const ronTTaccounts = [];   // conturi TikTok RON (nu se ×curs) — config PER-CONT din Curs valutar
  cursSheet.getDataRange().getValues().slice(1).forEach(r => {
    const a = String(r[0] || '').trim(); if (!a) return;
    const mTT = a.match(/^tiktok\s*:\s*(.+)$/i);   // rand "TikTok: <cont>" cu Moneda(B)=RON
    if (mTT) { if (String(r[1] || '').trim().toUpperCase() === 'RON') ronTTaccounts.push(mTT[1].trim()); return; }
    const b = a;
    if (r[2] !== '') cursVal[b] = String(r[2]).replace('.', ',');
    if (String(r[3] || '').toUpperCase() === 'DA') exclFB[b] = true;
    if (String(r[4] || '').toUpperCase() === 'DA') incTik[b] = true;
    if (String(r[5] || '').toUpperCase() === 'DA') incShop[b] = true;
  });
  const ronTTregex = '"^(' + ronTTaccounts.map(function (x) { return x.toUpperCase().replace(/[^A-Z0-9]/g, ''); }).join('|') + ')$"';
  const USD = cursVal['Facebook USD'] || '1';

  /* Mapping o dată -> literali (B fb / D shopify / E google) */
  const mapData = ss.getSheetByName('Mapping').getDataRange().getValues();
  const MAP = {};
  for (let i = 1; i < mapData.length; i++) {
    const b = String(mapData[i][0] || '').trim(); if (!b) continue;
    MAP[b] = { fb: String(mapData[i][1] || ''), shop: String(mapData[i][3] || ''), google: String(mapData[i][4] || '') };
  }
  const qLit = s => '"' + String(s).replace(/"/g, '""') + '"';
  const fbRegexLit = fb => '"^(' + String(fb).toUpperCase().replace(/[^A-Z0-9,]/g, '').replace(/,/g, '|') + ')$"';

  /* Curăță + inserează rândurile de azi */
  if (sheet.getLastRow() > 1) sheet.deleteRows(2, sheet.getLastRow() - 1);
  sheet.insertRowsAfter(1, BRANDS.length);

  const rexList = (row, colLetter) => {            // păstrat pt Tiktok (col C)
    const idx = colLetter === 'B' ? 2 : 3;
    return '"^(" & REGEXREPLACE(REGEXREPLACE( UPPER( VLOOKUP($B' + row + ';Mapping!$A:$' + colLetter + ';' + idx + ';FALSE) ); "[^A-Z0-9,]" ; "" ); "," ; "|" ) & ")$"';
  };
  const sh = name => "'" + String(name).replace(/'/g, "''") + "'";
  const RZ2 = "'Raport Zilnic 2'";
  const today = new Date(); today.setHours(0, 0, 0, 0);

  const values = [], formulas = [];

  BRANDS.forEach((brand, i) => {
    const R = i + 2;
    values.push([today, brand]);
    const cv = (cursVal[brand] && incShop[brand]) ? '*' + cursVal[brand] : '';

    const FB = sh('Facebook Ads azi');
    const SHOP = sh('Shopify azi');

    const mp = MAP[brand], inl = !!mp;
    const fbReLit  = inl ? fbRegexLit(mp.fb) : rexList(R, 'B');
    const shopName = inl ? qLit(mp.shop)     : ('VLOOKUP($B' + R + ';Mapping!A:D;4;FALSE)');
    const googleNm = (inl && mp.google !== '') ? qLit(mp.google) : ('VLOOKUP($B' + R + ';Mapping!$A:$F;5;FALSE)');

    const tkRe  = rexList(R, 'C');
    const tkKey = 'VLOOKUP($B' + R + ';Mapping!$A:$F;6;FALSE)';
    const accFB = 'REGEXREPLACE( UPPER(' + FB + '!B:B) ; "[^A-Z0-9]" ; "" )';
    const accTK = 'REGEXREPLACE( UPPER(\'Tiktok Ads azi camp\'!B:B) ; "[^A-Z0-9]" ; "" )';

    /* FACEBOOK (inline regex) */
    const fbBase = c => 'IFERROR(SUM(FILTER(' + FB + '!' + c + ':' + c + ';(' + FB + '!A:A=$A' + R + ')*REGEXMATCH(' + accFB + ';' + fbReLit + ')));0)';
    const fbSpend = '=' + (exclFB[brand] ? fbBase('C') : '(' + fbBase('C') + ')*' + USD);
    const fbClk = '=' + fbBase('E');
    const fbImp = '=' + fbBase('F');

    /* TIKTOK (neschimbat) */
    const tkFilter =
      '(\'Tiktok Ads azi camp\'!A:A=$A' + R + ')*' +
      'IF(LEN(VLOOKUP($B' + R + ';Mapping!$A:$C;3;FALSE));' +
        'REGEXMATCH(' + accTK + ';' + tkRe + ');' +
        'IF(LEN(' + tkKey + ');TRUE;FALSE))*' +
      'IF(LEN(IFERROR(VLOOKUP($B' + R + ';Mapping!$A:$G;7;FALSE);""))>0;' +
        'IF(REGEXMATCH(' + accTK + ';"^(" & REGEXREPLACE(REGEXREPLACE( UPPER( IFERROR(VLOOKUP($B' + R + ';Mapping!$A:$G;7;FALSE);"") ) ; "[^A-Z0-9,]" ; "" ); "," ; "|" ) & ")$");' +
          'IF(LEN(' + tkKey + ');REGEXMATCH( UPPER(\'Tiktok Ads azi camp\'!C:C) ; UPPER(' + tkKey + ') );TRUE);TRUE);' +
        'IF(LEN(' + tkKey + ');REGEXMATCH( UPPER(\'Tiktok Ads azi camp\'!C:C) ; UPPER(' + tkKey + ') );TRUE))';
    /* Mixed-currency: conturile TikTok RON (ex Nocturna Europa) NU se ×curs chiar dacă brandul e incTik=DA */
    const tkRonAcc = 'REGEXMATCH(' + accTK + ';' + ronTTregex + ')';
    const tkSpend = '=' + (incTik[brand]
      ? 'IFERROR(SUM(FILTER(\'Tiktok Ads azi camp\'!D:D;(' + tkFilter + ')*(1-' + tkRonAcc + ')));0)*' + USD +
        '+IFERROR(SUM(FILTER(\'Tiktok Ads azi camp\'!D:D;(' + tkFilter + ')*' + tkRonAcc + '));0)'
      : 'IFERROR(SUM(FILTER(\'Tiktok Ads azi camp\'!D:D;' + tkFilter + '));0)');
    const tkConv = '=IFERROR(SUM(FILTER(\'Tiktok Ads azi camp\'!E:E;' + tkFilter + '));0)';
    const tkImp  = '=IFERROR(SUM(FILTER(\'Tiktok Ads azi camp\'!F:F;' + tkFilter + '));0)';

    /* SHOPIFY (shopName inline + revenue consolidat F:I) */
    /* Duppo EU: sursa cu shipping country (col J) = 'Shopify azi Magdeal'; filtru pe tara;
       venit SI COGS ×curs Duppo BG: tabul raporteaza in moneda magazinului (EUR), nu in moneda locala
       (verificat 11-oct: CZ 3 comenzi = 72,25, HU 2 = 57,48 -> EUR; ×CZK/HUF dadea 15,8 / 0,8 RON). */
    const dsp = DUPPO_SPLIT[brand];
    const SHP = dsp ? sh('Shopify azi Magdeal') : SHOP;
    const shpNm = dsp ? '"duppo*"' : shopName;
    const shpNmF = dsp ? 'REGEXMATCH(' + SHP + '!B:B;"(?i)^duppo")' : '(' + SHP + '!B:B=' + shpNm + ')';
    const ctry  = dsp ? ';' + SHP + '!J:J;' + qLit(dsp.country) : '';
    const ctryF = dsp ? '*(' + SHP + '!J:J=' + qLit(dsp.country) + ')' : '';
    const cvCgs = dsp ? ((cursVal['Duppo BG'] && incShop[brand]) ? '*' + cursVal['Duppo BG'] : '') : cv;
    const shpCmd = '=IFERROR(SUMIFS(' + SHP + '!C:C;' + SHP + '!B:B;' + shpNm + ';' + SHP + '!A:A;$A' + R + ctry + ');0)';
    const shpV   = '=IFERROR(SUM(FILTER(' + SHP + '!F:I;(' + SHP + '!A:A=$A' + R + ')*' + shpNmF + ctryF + '))' + (dsp ? cvCgs : cv) + ';0)';
    const shpCgs = '=IFERROR(SUMIFS(' + SHP + '!E:E;' + SHP + '!B:B;' + shpNm + ';' + SHP + '!A:A;$A' + R + ctry + ')' + cvCgs + ';0)';

    const trans = '=IF($B' + R + '="Belasil";24*$E' + R + ';12,2*$E' + R + ')';
    const aov = '=IFERROR($F' + R + '/($C' + R + '+$D' + R + '+$S' + R + ');0)';
    const cpa = '=IFERROR(($C' + R + '+$D' + R + '+$S' + R + ')/$E' + R + ';0)';

    /* GOOGLE (FIX: toate pe contul Google, col E) */
    const gCost = '=IFERROR(SUMIFS(\'Google Ads azi\'!C:C;\'Google Ads azi\'!B:B;' + googleNm + ';\'Google Ads azi\'!A:A;$A' + R + ')' + cv + ';0)';
    const gClk  = '=IFERROR(SUMIFS(\'Google Ads azi\'!E:E;\'Google Ads azi\'!B:B;' + googleNm + ';\'Google Ads azi\'!A:A;$A' + R + ');0)';
    const gImp  = '=IFERROR(SUMIFS(\'Google Ads azi\'!F:F;\'Google Ads azi\'!B:B;' + googleNm + ';\'Google Ads azi\'!A:A;$A' + R + ');0)';

    /* AOV ieri = medie mobila 7 zile din Zilnic2 (SUMA venit / SUMA comenzi pe [azi-7, ieri]).
       Fix: nu mai trage col W (care era deja "AOV ieri" -> off-by-one, lua AOV de acum 2 zile)
       si nu mai foloseste o singura zi volatila (Grandia) care umfla guard-ul din Looker. */
    const aovIeri = '=IFERROR(' +
      'SUMIFS(' + RZ2 + '!$F$2:$F;' + RZ2 + '!$B$2:$B;$B' + R + ';' + RZ2 + '!$A$2:$A;">="&($A' + R + '-7);' + RZ2 + '!$A$2:$A;"<"&$A' + R + ')/' +
      'SUMIFS(' + RZ2 + '!$E$2:$E;' + RZ2 + '!$B$2:$B;$B' + R + ';' + RZ2 + '!$A$2:$A;">="&($A' + R + '-7);' + RZ2 + '!$A$2:$A;"<"&$A' + R + ')' +
    ';0)';

    formulas.push([
      fbSpend, tkSpend, shpCmd, shpV, shpCgs,
      trans, '0', '0', '0', '0',
      aov, cpa, fbClk, tkConv, fbImp, tkImp,
      gCost, gClk, gImp, aovIeri
    ]);
  });

  sheet.getRange(2, 1, values.length, 2).setValues(values);
  sheet.getRange(2, 3, formulas.length, 20).setFormulas(formulas);
}

