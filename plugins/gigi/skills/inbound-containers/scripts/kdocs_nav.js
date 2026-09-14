/* kdocs_nav.js — helper pentru citit fisierul de containere din KDocs/WPS (share link view-only).
 *
 * CUM SE FOLOSESTE: lipesti functiile astea prin chrome-devtools MCP `evaluate_script`
 * (nu se ruleaza cu node — e cod de pagina). Vezi SKILL.md pentru fluxul complet.
 *
 * DE CE E NEVOIE: pe un share link view-only NU merge nici copy, nici download, nici Ctrl+F,
 * iar valorile celulelor NU sunt in JS — traiesc intr-un core WASM:
 *   range.Text / .Value2 / getValue2() / queryRangeValues()  =>  toate `undefined`.
 * Singurul mod fiabil de citire = ACTIVEZI foaia + dai ZOOM + faci SCREENSHOT si citesti vizual.
 * Ce EXISTA in JS: lista de foi, activate(), loadSheetData(), sheetView.setZoom().
 */

// 1) Ce containere exista (fiecare foaie = un container, ex "#43")
window.__sheets = () =>
  window.APP.workbook._worksheets._sheets.map(s => { try { return s.getName(); } catch (e) { return '?'; } });

// 2) Du-te pe un container, la un zoom dat. zoom 25 = scanare rapida (vezi ~18 randuri,
//    recunosti produsul dupa POZA); zoom 90 = citesti SKU/cantitati.
window.__go = async (name, zoom) => {
  const s = window.APP.workbook._worksheets._sheets.find(x => x.getName() === name);
  if (!s) return 'missing ' + name;
  s.activate();
  if (s.loadSheetData) { try { await s.loadSheetData(); } catch (e) {} }
  await new Promise(r => setTimeout(r, 900));           // fara asta, foaia e inca goala
  const sv = s.getSheetView ? s.getSheetView() : s._getSheetView();
  try { sv.setZoom(zoom || 25); } catch (e) {}
  await new Promise(r => setTimeout(r, 500));
  return 'ok ' + name;
};

// 3) Scroll. onScrollToCellLeftTop()/setScrollPos() NU functioneaza pe view-only, nici Ctrl+Home/End.
//    Singurul care merge: eveniment `wheel` pe canvas-ul cel mai mare.
//    dir = 1 (jos) / -1 (sus). La zoom 25, 1 tick ~= 6-8 randuri; la zoom 90, ~= 2 randuri.
window.__wheel = async (n, dir) => {
  const cvs = [...document.querySelectorAll('canvas')]
    .sort((a, b) => (b.width * b.height) - (a.width * a.height))[0];
  const r = cvs.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  for (let i = 0; i < n; i++) {
    cvs.dispatchEvent(new WheelEvent('wheel', {
      deltaY: (dir || 1) * 300, clientX: x, clientY: y, bubbles: true, cancelable: true
    }));
    await new Promise(r2 => setTimeout(r2, 250));
  }
  return 'wheel' + n;
};
