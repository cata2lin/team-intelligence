/* ===== HELPER: gaseste tab dupa gid (getSheetById NU exista in Apps Script) ===== */
function getSheetByGid(ss, gid) {
  return ss.getSheets().filter(function (s) { return s.getSheetId() === gid; })[0] || null;
}

function adaugaRandZilnic2() {
  /* =================== CONFIG =================== */
  const ssId = '1IVg0fI-_Rm7IptmOl3BmGrqtyyzn3auf0ZPuftr9vQo';

  // Tab-ul din linkul tau:
  // https://docs.google.com/spreadsheets/d/.../edit?gid=1025107216
  const dataGid = 1025107216;

  const rateSh = 'Curs valutar';

  const BRANDS = [
    'Esteban',
    'Rossi Nails',
    'Nocturna',
    'Nocturna Lux',
    'Apreciat',
    'Gento',
    'Carpetto',
    'Reduceri bune',
    'George Talent',
    'Magdeal',
    'Bonhaus CZ',
    'Bonhaus RO',
    'Bonhaus PL',
    'Bonhaus BG',
    'Ofertele Zilei',
    'Orice Redus',
    'Nubra',
    'Lab Noir',
    'Grandia',
    'Duppo BG',
    'Duppo CZ',
    'Duppo HU',
    'Duppo MD',
    'Artevita',
    'De la Bucsa'
  ];
  /* ============================================== */

  /* Duppo EU = UN magazin (base EUR) servind 3 piete (BG/CZ/HU) cu monede diferite.
     Sursa cu shipping country (col J) = tabul add-on 'Shopify [azi] Magdeal'. Venitul (F:I) e in
     moneda LOCALA -> ×curs brand; COGS (col E) e in EUR baza -> ×curs Duppo BG (EUR) mereu. */
  const DUPPO_SPLIT = {
    'Duppo BG': { store: 'Duppo BG', country: 'Bulgaria' },
    'Duppo CZ': { store: 'Duppo BG', country: 'Czech Republic' },
    'Duppo HU': { store: 'Duppo BG', country: 'Hungary' }
  };

  const ss = SpreadsheetApp.openById(ssId);
  const sheet = getSheetByGid(ss, dataGid);
  const curs = ss.getSheetByName(rateSh);

  if (!sheet) {
    throw new Error('Nu gasesc tab-ul Raport Zilnic 2 cu gid ' + dataGid);
  }

  if (!curs) {
    throw new Error('Nu gasesc sheet-ul "' + rateSh + '".');
  }

  /* === Citim setarile din Curs valutar ===
     A Brand
     B Moneda
     C Curs
     D Exclude FB USD
     E Include Tiktok USD
     F Include Shopify
  */
  const cursVal = {};
  const exclFB = {};
  const incTik = {};
  const incShop = {};
  const ronTTaccounts = [];   // conturi TikTok facturate in RON (nu se ×curs) — config PER-CONT din Curs valutar

  const cursData = curs.getDataRange().getValues();

  for (let i = 1; i < cursData.length; i++) {
    const row = cursData[i];
    const a = String(row[0] || '').trim();

    /* Config per-cont TikTok: rand "TikTok: <nume cont>" cu Moneda (col B) = RON -> spend-ul de pe
       contul ala NU se ×curs, chiar daca brandul are incTik=DA (mixed-currency, ex Nocturna Europa). */
    const mTT = a.match(/^tiktok\s*:\s*(.+)$/i);
    if (mTT) {
      if (String(row[1] || '').trim().toUpperCase() === 'RON') ronTTaccounts.push(mTT[1].trim());
      continue;
    }

    const brand = a;
    if (!brand) continue;

    if (row[2] !== '') {
      cursVal[brand] = String(row[2]).replace('.', ',');
    }

    if (String(row[3] || '').trim().toUpperCase() === 'DA') {
      exclFB[brand] = true;
    }

    if (String(row[4] || '').trim().toUpperCase() === 'DA') {
      incTik[brand] = true;
    }

    if (String(row[5] || '').trim().toUpperCase() === 'DA') {
      incShop[brand] = true;
    }
  }
  const ronTTregex = '"^(' + ronTTaccounts.map(function (x) { return x.toUpperCase().replace(/[^A-Z0-9]/g, ''); }).join('|') + ')$"';

  const USD = cursVal['Facebook USD'] || '1';

  /* === Citim Mapping o data (config static) -> literali in loc de VLOOKUP === */
  const mapData = ss.getSheetByName('Mapping').getDataRange().getValues();
  const MAP = {};
  for (let i = 1; i < mapData.length; i++) {
    const b = String(mapData[i][0] || '').trim();
    if (!b) continue;
    MAP[b] = { fb: String(mapData[i][1] || ''), shop: String(mapData[i][3] || ''), google: String(mapData[i][4] || '') };
  }

  /* === Verificam sheet-urile sursa === */
  const sourceSheets = [
    'Facebook Ads',
    'Facebook Magdeal',
    'Tiktok Ads',
    'Shopify',
    'Shopify Magdeal',
    'Google Ads',
    'Mapping'
  ];

  const sourceLastRows = {};

  for (let i = 0; i < sourceSheets.length; i++) {
    const name = sourceSheets[i];
    const s = ss.getSheetByName(name);

    if (!s) {
      throw new Error('Nu gasesc sheet-ul sursa: "' + name + '". Verifica numele tab-ului.');
    }

    sourceLastRows[name] = Math.max(s.getLastRow(), 2);
  }

  /* === Data pentru randurile noi === */
  let lastRow = sheet.getLastRow();

  if (lastRow < 2) {
    lastRow = 1;
  }

  const lastDateVal = sheet.getRange(lastRow, 1).getValue();

  let newDate;

  if (lastDateVal instanceof Date && !isNaN(lastDateVal.getTime())) {
    newDate = new Date(lastDateVal);
    newDate.setDate(newDate.getDate() + 1);
  } else {
    newDate = new Date();
  }

  const tomorrow = new Date(newDate);
  tomorrow.setDate(tomorrow.getDate() + 1);

  /* === Helpers === */
  function sh(name) {
    return "'" + String(name).replace(/'/g, "''") + "'";
  }

  function rng(sheetName, colLetter) {
    return sh(sheetName) + '!$' + colLetter + '$2:$' + colLetter;   // open-ended: prinde datele care intra in cursul zilei
  }

  function rexList(rowNumber, colLetter, idx) {
    return '"^(" & REGEXREPLACE(' +
      'REGEXREPLACE(' +
        'UPPER(VLOOKUP($B' + rowNumber + ';Mapping!$A:$' + colLetter + ';' + idx + ';FALSE));' +
        '"[^A-Z0-9,]";' +
        '""' +
      ');' +
      '",";' +
      '"|"' +
    ') & ")$"';
  }

  function safeFormula(formula) {
    return formula;
  }

  function rng2(sheetName, c1, c2) {
    return sh(sheetName) + '!$' + c1 + '$2:$' + c2;   // open-ended F:I
  }
  function qLit(s) { return '"' + String(s).replace(/"/g, '""') + '"'; }
  function fbRegexLit(fb) {
    return '"^(' + String(fb).toUpperCase().replace(/[^A-Z0-9,]/g, '').replace(/,/g, '|') + ')$"';
  }

  /* === Inseram randurile toate odata === */
  const n = BRANDS.length;

  sheet.insertRowsAfter(lastRow, n);

  const startRow = lastRow + 1;

  const valuesA = [];
  const valuesB = [];
  const valuesV = [];

  const formulasCtoH = [];
  const valuesItoL = [];
  const formulasMtoN = [];
  const formulasOtoR = [];
  const formulasStoU = [];
  const formulasW = [];

  for (let i = 0; i < n; i++) {
    const brand = BRANDS[i];
    const row = startRow + i;

    valuesA.push([newDate]);
    valuesB.push([brand]);
    valuesV.push([tomorrow]);

    valuesItoL.push([0, 0, 0, 0]);

    const isMagdealOverride =
      brand === 'Magdeal' ||
      brand === 'Mag Deal' ||
      brand === 'Ofertele Zilei';

    const fbSheetName = isMagdealOverride ? 'Facebook Magdeal' : 'Facebook Ads';
    const shopSheetName = isMagdealOverride ? 'Shopify Magdeal' : 'Shopify';

    const mp = MAP[brand];
    const inl = !!mp;
    const fbReLit  = inl ? fbRegexLit(mp.fb) : rexList(row, 'B', 2);
    const shopName = inl ? qLit(mp.shop)     : ('VLOOKUP($B' + row + ';Mapping!A:D;4;FALSE)');
    const googleNm = (inl && mp.google !== '') ? qLit(mp.google) : ('VLOOKUP($B' + row + ';Mapping!$A:$F;5;FALSE)');
    const tkRe = rexList(row, 'C', 3);

    const accFB =
      'REGEXREPLACE(UPPER(' + rng(fbSheetName, 'B') + ');"[^A-Z0-9]";"")';

    const accTK =
      'REGEXREPLACE(UPPER(' + rng('Tiktok Ads', 'B') + ');"[^A-Z0-9]";"")';

    const tkKey =
      'VLOOKUP($B' + row + ';Mapping!$A:$F;6;FALSE)';

    const tkOptionalAccount =
      'IFERROR(VLOOKUP($B' + row + ';Mapping!$A:$G;7;FALSE);"")';

    const tkOptionalAccountClean =
      'REGEXREPLACE(UPPER(' + tkOptionalAccount + ');"[^A-Z0-9]";"")';

    /* ===================== FACEBOOK ===================== */
    function fbUniqueSum(colLetter) {
      return (
        'IFERROR(' +
          'SUM(' +
            'INDEX(' +
              'UNIQUE(' +
                'FILTER({' +
                  accFB + '\\' + rng(fbSheetName, colLetter) +
                '};' +
                  '(' + rng(fbSheetName, 'A') + '=$A' + row + ')*' +
                  'REGEXMATCH(' + accFB + ';' + fbReLit + ')' +
                ')' +
              ');;2' +
            ')' +
          ')' +
        ';0)'
      );
    }

    const fbCostBase = fbUniqueSum('C');

    const fbCost = exclFB[brand]
      ? '=' + fbCostBase
      : '=(' + fbCostBase + ')*' + USD;

    const fbClicks = '=' + fbUniqueSum('E');
    const fbImpressions = '=' + fbUniqueSum('F');

    /* ===================== TIKTOK ===================== */
    const tkFilter =
      '(' + rng('Tiktok Ads', 'A') + '=$A' + row + ')*' +

      'IF(' +
        'LEN(VLOOKUP($B' + row + ';Mapping!$A:$C;3;FALSE));' +
        'REGEXMATCH(' + accTK + ';' + tkRe + ');' +
        'IF(LEN(' + tkKey + ');TRUE;FALSE)' +
      ')*' +

      'IF(' +
        'LEN(' + tkOptionalAccount + ')>0;' +

        'IF(' +
          'REGEXMATCH(' + accTK + ';"^(" & REGEXREPLACE(REGEXREPLACE(UPPER(' + tkOptionalAccount + ');"[^A-Z0-9,]";"");",";"|") & ")$");' +
          'IF(' +
            'LEN(' + tkKey + ');' +
            'REGEXMATCH(UPPER(' + rng('Tiktok Ads', 'C') + ');UPPER(' + tkKey + '));' +
            'TRUE' +
          ');' +
          'TRUE' +
        ');' +

        'IF(' +
          'LEN(' + tkKey + ');' +
          'REGEXMATCH(UPPER(' + rng('Tiktok Ads', 'C') + ');UPPER(' + tkKey + '));' +
          'TRUE' +
        ')' +
      ')';

    /* Mixed-currency: conturi TikTok facturate în RON (ex Nocturna Europa) NU se ×curs, chiar dacă
       brandul are incTik=DA (pt contul lui USD). Altfel 561 RON × 4,58 = 2576 fals la Casa Ofertelor. */
    const tkRonAcc = 'REGEXMATCH(' + accTK + ';' + ronTTregex + ')';
    const tkColD = rng('Tiktok Ads', 'D');
    const tkSpend = incTik[brand]
      ? '=IFERROR(SUM(FILTER(' + tkColD + ';(' + tkFilter + ')*(1-' + tkRonAcc + ')));0)*' + USD +
        '+IFERROR(SUM(FILTER(' + tkColD + ';(' + tkFilter + ')*' + tkRonAcc + '));0)'
      : '=IFERROR(SUM(FILTER(' + tkColD + ';' + tkFilter + '));0)';

    const tkClicks =
      '=IFERROR(SUM(FILTER(' + rng('Tiktok Ads', 'F') + ';' + tkFilter + '));0)';

    const tkImpressions =
      '=IFERROR(SUM(FILTER(' + rng('Tiktok Ads', 'G') + ';' + tkFilter + '));0)';

    /* ===================== SHOPIFY ===================== */
    const shopCurrencyConversion =
      cursVal[brand] && incShop[brand]
        ? '*' + cursVal[brand]
        : '';

    /* Duppo EU: sursa cu shipping country (col J) = 'Shopify Magdeal'; filtru pe tara;
       venit SI COGS ×curs Duppo BG: tabul raporteaza in moneda magazinului (EUR), nu in moneda locala
       (verificat 11-oct: ×CZK/HUF dadea venit de ~5% / ~0,2% din real). */
    const dsp = DUPPO_SPLIT[brand];
    const shpSheet = dsp ? 'Shopify Magdeal' : shopSheetName;
    const shpName2 = dsp ? '"duppo*"' : shopName;
    const shpFilterB = dsp
      ? 'REGEXMATCH(' + rng(shpSheet, 'B') + ';"(?i)^duppo")'
      : '(' + rng(shpSheet, 'B') + '=' + shpName2 + ')';
    const ctrySum = dsp ? ';' + rng(shpSheet, 'J') + ';' + qLit(dsp.country) : '';
    const ctryFil = dsp ? '*(' + rng(shpSheet, 'J') + '=' + qLit(dsp.country) + ')' : '';
    const cogsConv = dsp
      ? ((cursVal['Duppo BG'] && incShop[brand]) ? '*' + cursVal['Duppo BG'] : '')
      : shopCurrencyConversion;

    const shopOrders =
      '=IFERROR(' +
        'SUMIFS(' +
          rng(shpSheet, 'C') + ';' +
          rng(shpSheet, 'B') + ';' + shpName2 + ';' +
          rng(shpSheet, 'A') + ';$A' + row + ctrySum +
        ')' +
      ';0)';

    const shopRevenue =
      '=IFERROR(SUM(FILTER(' + rng2(shpSheet, 'F', 'I') + ';' +
        '(' + rng(shpSheet, 'A') + '=$A' + row + ')*' + shpFilterB + ctryFil + '))' +
        (dsp ? cogsConv : shopCurrencyConversion) + ';0)';

    const shopProfit =
      '=IFERROR(' +
        'SUMIFS(' +
          rng(shpSheet, 'E') + ';' +
          rng(shpSheet, 'B') + ';' + shpName2 + ';' +
          rng(shpSheet, 'A') + ';$A' + row + ctrySum +
        ')' + cogsConv +
      ';0)';

    /* ===================== COST FIX ===================== */
    const fixedCost =
      '=IF($B' + row + '="Belasil";24*$E' + row + ';12,2*$E' + row + ')';

    /* ===================== ROAS / CPA ===================== */
    const roas =
      '=IFERROR($F' + row + '/($C' + row + '+$D' + row + '+$S' + row + ');0)';

    const cpa =
      '=IFERROR(($C' + row + '+$D' + row + '+$S' + row + ')/$E' + row + ';0)';

    /* ===================== GOOGLE ADS ===================== */
    const googleCost =
      '=IFERROR(' +
        'SUMIFS(' +
          rng('Google Ads', 'C') + ';' +
          rng('Google Ads', 'B') + ';' + googleNm + ';' +
          rng('Google Ads', 'A') + ';$A' + row +
        ')' + shopCurrencyConversion +
      ';0)';

    const googleClicks =
      '=IFERROR(' +
        'SUMIFS(' +
          rng('Google Ads', 'E') + ';' +
          rng('Google Ads', 'B') + ';' + googleNm + ';' +
          rng('Google Ads', 'A') + ';$A' + row +
        ')' +
      ';0)';

    const googleImpressions =
      '=IFERROR(' +
        'SUMIFS(' +
          rng('Google Ads', 'F') + ';' +
          rng('Google Ads', 'B') + ';' + googleNm + ';' +
          rng('Google Ads', 'A') + ';$A' + row +
        ')' +
      ';0)';

    /* ===================== AOV IERI (medie mobila 7 zile) ===================== */
    /* Fix: single-day AOV era volatil (Grandia) -> guard-ul din Looker sarea.
       Acum = SUMA venit / SUMA comenzi pe ultimele 7 zile [A-7, A-1], per brand. */
    const aovYesterday =
      '=IFERROR(' +
        'SUMIFS($F$2:$F;$B$2:$B;B' + row + ';$A$2:$A;">="&(A' + row + '-7);$A$2:$A;"<"&A' + row + ')/' +
        'SUMIFS($E$2:$E;$B$2:$B;B' + row + ';$A$2:$A;">="&(A' + row + '-7);$A$2:$A;"<"&A' + row + ')' +
      ';0)';

    formulasCtoH.push([
      safeFormula(fbCost),
      safeFormula(tkSpend),
      safeFormula(shopOrders),
      safeFormula(shopRevenue),
      safeFormula(shopProfit),
      safeFormula(fixedCost)
    ]);

    formulasMtoN.push([
      safeFormula(roas),
      safeFormula(cpa)
    ]);

    formulasOtoR.push([
      safeFormula(fbClicks),
      safeFormula(tkClicks),
      safeFormula(fbImpressions),
      safeFormula(tkImpressions)
    ]);

    formulasStoU.push([
      safeFormula(googleCost),
      safeFormula(googleClicks),
      safeFormula(googleImpressions)
    ]);

    formulasW.push([
      safeFormula(aovYesterday)
    ]);
  }

  /* === Scriem in batch === */
  sheet.getRange(startRow, 1, n, 1).setValues(valuesA);             // A Data
  sheet.getRange(startRow, 2, n, 1).setValues(valuesB);             // B Brand

  sheet.getRange(startRow, 3, n, 6).setFormulas(formulasCtoH);      // C:H
  sheet.getRange(startRow, 9, n, 4).setValues(valuesItoL);          // I:L

  sheet.getRange(startRow, 13, n, 2).setFormulas(formulasMtoN);     // M:N
  sheet.getRange(startRow, 15, n, 4).setFormulas(formulasOtoR);     // O:R
  sheet.getRange(startRow, 19, n, 3).setFormulas(formulasStoU);     // S:U

  sheet.getRange(startRow, 22, n, 1).setValues(valuesV);            // V
  sheet.getRange(startRow, 23, n, 1).setFormulas(formulasW);        // W

  congeleazaRanduriVechi();

  Logger.log('Am adaugat ' + n + ' randuri in "' + sheet.getName() + '", de la randul ' + startRow + '.');
}

/* ===== Inghetam formulele vechi -> valori (pastreaza foaia rapida) ===== */
function congeleazaRanduriVechi() {
  const ssId = '1IVg0fI-_Rm7IptmOl3BmGrqtyyzn3auf0ZPuftr9vQo';
  const dataGid = 1025107216;
  const ZILE_LIVE = 3;

  const ss = SpreadsheetApp.openById(ssId);
  const sheet = getSheetByGid(ss, dataGid);
  if (!sheet) throw new Error('Nu gasesc Raport Zilnic 2');

  const lastRow = sheet.getLastRow();
  if (lastRow < 2) return;

  const azi = new Date(); azi.setHours(0, 0, 0, 0);
  const prag = new Date(azi); prag.setDate(prag.getDate() - ZILE_LIVE);

  const dates = sheet.getRange(2, 1, lastRow - 1, 1).getValues();
  let freezeUpTo = 0;
  for (let i = 0; i < dates.length; i++) {
    const d = dates[i][0];
    if (d instanceof Date && d < prag) freezeUpTo = i + 2;
  }
  if (freezeUpTo < 2) { Logger.log('Nimic de inghetat.'); return; }

  const numRows = freezeUpTo - 1;
  const blocks = [[3, 6], [13, 9], [23, 1]];   // C:H, M:U, W
  blocks.forEach(function (b) {
    const r = sheet.getRange(2, b[0], numRows, b[1]);
    r.setValues(r.getValues());
  });
  Logger.log('Inghetat ' + numRows + ' randuri: formule -> valori.');
}

