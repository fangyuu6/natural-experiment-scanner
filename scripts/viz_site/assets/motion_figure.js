/* Home motion figure: three acts on one canvas, drawn as a pure function of time.
   Act 1, Screening: policy texts stream through a bar that narrows at each screening step. Nothing is placed on a map: the
   dots stand for texts and sit in the bar, not on states.
   Act 2, Estimates: the states of the nurse licensure compact turn on in the years they joined (verified years of
   curated_adoption.json, the shapes of the Home map card); a schematic estimate; the re-estimated papers fill a grid by result
   and move into the strip.
   Act 3, Explore: the camera pulls back to one small adoption map per state policy, in five bands by the year the first state
   adopted; a descriptive clock (years after the first state) and the median number of states with the policy 5 years after
   the first; the wall ends beside the findings, which are DOM text.
   No numbers are made up here: counts, years and findings come from the JSON the page carries (#mf-cfg, #mf-data).
   The page language is fixed by the server: the script carries no wording of its own. */
(function () {
  'use strict';
  var root = document.getElementById('motion-figure');
  if (!root) return;
  var canvas = root.querySelector('.mf-canvas');
  if (!canvas || !canvas.getContext) return;
  var ctx = canvas.getContext('2d');
  if (!ctx) return;
  var stage = root.querySelector('.mf-stage');
  var cfgEl = root.querySelector('#mf-cfg'), dataEl = root.querySelector('#mf-data');
  if (!cfgEl || !dataEl || !stage) return;
  var CFG, D;
  try { CFG = JSON.parse(cfgEl.textContent); D = JSON.parse(dataEl.textContent); } catch (e) { return; }
  var S = CFG.s, TAU = Math.PI * 2;
  var LANE = !!CFG.lane, TALL_BELOW = CFG.tallBelow || 900, AUTOPLAY = CFG.autoplay || 0.6, STILL = CFG.poster || 1.5;
  root.classList.add('mf-js');

  /* ------------------------------------------------------------------ helpers */
  function clamp01(x) { return x < 0 ? 0 : (x > 1 ? 1 : x); }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function smooth(x) { x = clamp01(x); return x * x * (3 - 2 * x); }
  function easeOut(x) { x = clamp01(x); var u = 1 - x; return 1 - u * u * u; }
  function easeIn(x) { x = clamp01(x); return x * x * x; }
  function easeInOut(x) { x = clamp01(x); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; }
  function easeOutBack(x) { x = clamp01(x); var c1 = 1.2, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); }
  function ramp(t, a, b) { return clamp01((t - a) / (b - a)); }
  function rng(seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }
  function shuffle(arr, r) {
    for (var i = arr.length - 1; i > 0; i--) { var j = Math.floor(r() * (i + 1)); var t = arr[i]; arr[i] = arr[j]; arr[j] = t; }
    return arr;
  }
  function fmt(n) {
    var s = String(Math.round(n)), out = '';
    for (var i = 0; i < s.length; i++) { if (i && (s.length - i) % 3 === 0) out += ','; out += s.charAt(i); }
    return out;
  }
  function fill(str, vars) {
    return String(str).replace(/\{(\w+)\}/g, function (m, k) { return vars && vars[k] != null ? vars[k] : m; });
  }
  function parseColor(str) {
    str = String(str || '').trim();
    var m;
    if ((m = /^#([0-9a-f]{3})$/i.exec(str))) { var h = m[1]; return [parseInt(h[0] + h[0], 16), parseInt(h[1] + h[1], 16), parseInt(h[2] + h[2], 16)]; }
    if ((m = /^#([0-9a-f]{6})/i.exec(str))) { var g = m[1]; return [parseInt(g.slice(0, 2), 16), parseInt(g.slice(2, 4), 16), parseInt(g.slice(4, 6), 16)]; }
    if ((m = /^rgba?\(([^)]+)\)/i.exec(str))) { var p = m[1].split(/[,\s/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2]]; }
    return null;
  }
  function rgba(c, a) { return 'rgba(' + (c[0] | 0) + ',' + (c[1] | 0) + ',' + (c[2] | 0) + ',' + (Math.round(clamp01(a) * 1000) / 1000) + ')'; }
  var qcache = {};
  function rgbaQ(c, a) {                       /* alpha in 1/20 steps: few distinct strings, few batches */
    var q = Math.round(clamp01(a) * 20), k = (c[0] | 0) + ',' + (c[1] | 0) + ',' + (c[2] | 0) + ',' + q;
    return qcache[k] || (qcache[k] = rgba(c, q / 20));
  }
  function mix(a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; }

  /* ------------------------------------------------------------------ theme (site tokens, light only) */
  var th = null, FONT = 'sans-serif';
  function readTheme() {
    var cs = window.getComputedStyle(root);
    function tok(name, fallback) { return parseColor(cs.getPropertyValue(name)) || parseColor(fallback); }
    FONT = cs.fontFamily || FONT;
    th = {
      bg: tok('--mf-bg', '#f9fafb'), ink: tok('--nx-ink', '#111827'), text: tok('--nx-text', '#4b5563'), muted: tok('--nx-muted', '#6b7280'),
      hair: tok('--nx-line', '#e5e7eb'), hair2: tok('--nx-line-2', '#d1d5db'), land: tok('--nx-land', '#eceef2'),
      accent: tok('--nx-accent', '#1d4ed8'), slate: tok('--nx-slate', '#8793a6'),
      b: [tok('--nx-b1', '#a8c5f9'), tok('--nx-b2', '#7ba4f0'), tok('--nx-b3', '#5283e0'), tok('--nx-b4', '#3263c3'), tok('--nx-b5', '#1e4798')],
      res: [tok('--mf-match', '#2166ac'), tok('--mf-dir', '#9a6700'), tok('--mf-nc', '#64748b'), tok('--mf-os', '#a23b72')],
      amber: tok('--mf-amber', '#d97706')
    };
    th.dark = tok('--nx-dark', '#1f2937');
    /* years after the first state: dark = the first state, lighter = later; grey, so that blue means only 'later is darker' (act 2) */
    th.cls = [0.9, 0.68, 0.45, 0.22, 0].map(function (u) { return mix(th.dark, th.hair2, u); });
    th.veil = mix(th.land, th.bg, 0.6);
  }
  function setFont(size, weight) { ctx.font = (weight || 400) + ' ' + size + 'px ' + FONT; }
  function textWidth(s, size, weight) { setFont(size, weight); return ctx.measureText(s).width; }
  /* o: size, weight, color, alpha, align, base */
  function text(s, x, y, o) {
    setFont(o.size || 12, o.weight);
    ctx.textAlign = o.align || 'left'; ctx.textBaseline = o.base || 'alphabetic';
    ctx.fillStyle = rgba(o.color, o.alpha == null ? 1 : o.alpha);
    ctx.fillText(s, x, y);
    if (root.mfLog) root.mfLog.push({ s: s, x: x, y: y, size: o.size || 12, align: o.align || 'left', a: o.alpha == null ? 1 : o.alpha, w: ctx.measureText(s).width });   /* checking aid */
  }
  /* digits on a fixed advance so a counting number never jitters */
  function tabular(s, x, y, o) {
    var size = o.size || 14, adv = size * 0.6, narrow = size * 0.3, w = 0, i, ch, widths = [];
    setFont(size, o.weight);
    ctx.textBaseline = o.base || 'alphabetic'; ctx.textAlign = 'left';
    ctx.fillStyle = rgba(o.color, o.alpha == null ? 1 : o.alpha);
    for (i = 0; i < s.length; i++) {
      ch = s.charAt(i);
      var a = (ch >= '0' && ch <= '9') ? adv : ((ch === ',' || ch === '.') ? narrow : (ch === ' ' ? size * 0.3 : ctx.measureText(ch).width));
      widths.push(a); w += a;
    }
    var px = o.align === 'right' ? x - w : (o.align === 'center' ? x - w / 2 : x);
    for (i = 0; i < s.length; i++) {
      ch = s.charAt(i);
      if (ch !== ' ') {
        var cw = ctx.measureText(ch).width, isDigit = ch >= '0' && ch <= '9';
        ctx.fillText(ch, isDigit ? px + (widths[i] - cw) / 2 : px, y);
      }
      px += widths[i];
    }
    if (root.mfLog) root.mfLog.push({ s: s, x: o.align === 'right' ? x - w : (o.align === 'center' ? x - w / 2 : x), y: y, size: size, align: 'left', a: o.alpha == null ? 1 : o.alpha, w: w });
    return w;
  }
  function tabularWidth(s, size, weight) {
    setFont(size, weight);
    var w = 0;
    for (var i = 0; i < s.length; i++) { var ch = s.charAt(i); w += (ch >= '0' && ch <= '9') ? size * 0.6 : ((ch === ',' || ch === '.') ? size * 0.3 : (ch === ' ' ? size * 0.3 : ctx.measureText(ch).width)); }
    return w;
  }
  function fitFirst(list, maxW, size, weight) {      /* first wording of a list (longest first) that fits */
    for (var q = 0; q < list.length; q++) if (textWidth(list[q], size, weight) <= maxW) return list[q];
    return list[list.length - 1];
  }

  /* ------------------------------------------------------------------ data */
  var M = D.map, nS = M.codes.length;
  var PF = M.d.map(function (d) { return new Path2D(d); });       /* the Home card's shapes */
  var PS = M.ds.map(function (d) { return new Path2D(d); });      /* simplified for the small maps */
  var allSimp = new Path2D();
  PS.forEach(function (p) { try { allSimp.addPath(p); } catch (e) { /* drawn state by state below */ } });
  var nT = D.tiles.n, PK = D.tiles.packed, BANDS = D.tiles.bands, SRC = D.tiles.src, OPEN = D.tiles.open;
  var tOff = new Int32Array(nT + 1), adS = [], adD = [], bandOf = new Uint8Array(nT), i0, k0, acc0, b0;
  for (i0 = 0; i0 < nT; i0++) {
    tOff[i0] = adS.length;
    for (k0 = 1; k0 < PK[i0].length; k0 += 2) { adS.push(PK[i0][k0]); adD.push(PK[i0][k0 + 1]); }
  }
  tOff[nT] = adS.length;
  for (i0 = 0, b0 = 0, acc0 = BANDS[0]; i0 < nT; i0++) { while (i0 >= acc0) { b0++; acc0 += BANDS[b0]; } bandOf[i0] = b0; }
  var CLK = D.clock.k, obsT = new Uint8Array(nT), MED = D.clock.bands.map(function (b) { return b.median; });
  for (i0 = 0; i0 < nT; i0++) obsT[i0] = PK[i0][0] + CLK <= D.clock.obsEnd[SRC.charAt(i0)] ? 1 : 0;
  var nurseYear = new Int16Array(nS);
  D.nurse.a.forEach(function (a) { nurseYear[a[0]] = a[1]; });
  var Y0 = D.nurse.y0, Y1 = D.nurse.y1, NURSE_N = D.nurse.n, NB = D.nurse.bins, nurseBin = new Uint8Array(nS);
  var BC = D.nurse.binColors.map(parseColor);                  /* the card's colours of its year bands */
  for (i0 = 0; i0 < nS; i0++) {
    if (!nurseYear[i0]) continue;
    for (k0 = 0; k0 < NB.length; k0++) if (nurseYear[i0] >= NB[k0][0] && nurseYear[i0] <= NB[k0][1]) nurseBin[i0] = k0;
  }
  var CN = CFG.counts, GATES = D.counts.gates, TEXTS = D.counts.texts, CANDS = D.counts.cands;
  var NFIND = CFG.findings.length;
  /* years after the first state -> colour class (0 = darkest) */
  function classOfD(d) { return d === 0 ? 4 : (d <= 2 ? 3 : (d <= 5 ? 2 : (d <= 9 ? 1 : 0))); }
  function classRGB(q) { return th.cls[q]; }

  /* ------------------------------------------------------------------ timeline (seconds) */
  var T = {
    in0: 0.4, in1: 3.4, trav: 2.0,                     /* texts enter the bar, and cross it in `trav` */
    pick0: 5.5, pick1: 6.8,                            /* the bar leaves; the design of act 2 appears */
    wave0: 7.0, wave1: 10.0,                           /* the nurse-compact states join */
    step: 0.45, fstep: 0.13
  };
  T.est0 = T.wave1 + 0.3; T.est1 = T.est0 + 0.8;                     /* the schematic lane (ESTIMATE_LANE) */
  T.grid0 = LANE ? T.est1 : T.wave1 + 0.5;                           /* the papers leave the lane, or the nurse map */
  T.cls0 = T.grid0 + 1.4; T.cls1 = T.cls0 + 0.6; T.dep0 = T.cls1 + 0.5; T.dep1 = T.dep0 + 0.8;
  T.pb0 = T.dep1; T.pb1 = T.pb0 + 2.2;                               /* camera pulls back to the wall */
  T.c0 = T.pb1 + 0.6; T.cc0 = T.c0 + 0.6;                            /* descriptive clock on the wall */
  T.c1 = T.cc0 + CLK * T.step + 1.55;
  T.fin0 = T.c1 + 1.0; T.fin1 = T.fin0 + 0.9;                        /* narrow screens: labels leave, then the wall shrinks */
  T.find0 = T.fin1 - 0.1;
  T.end = T.find0 + T.fstep * NFIND + 1.6;
  T.poster = T.end;                                                  /* the last frame (reduced motion) */
  T.still = STILL;                                                   /* the still before play */
  T.med0 = T.cc0 + CLK * T.step;
  var GRID_FLY = 0.8;

  /* ------------------------------------------------------------------ layout */
  var L = null, dpr = 1, W = 0, H = 0;
  var stripEl = root.querySelector('.mf-strip'), findEl = root.querySelector('.mf-find');
  var cells = [].slice.call(root.querySelectorAll('.mf-cell'));
  var findItems = [].slice.call(root.querySelectorAll('.mf-find li'));
  var CV = { ox: 0, oy: 0, sc: 1, w: 0, h: 0 };
  var sparks = null, atlas = null, layers = null, atlasState = null, grid = null;

  function findColumns() {                /* tall layout: two columns from 480 px of figure width, one below (a label never splits across two lines of a cell) */
    findEl.classList.toggle('mf-two', L.portrait && W >= 480);
    findEl.classList.toggle('mf-one', L.portrait && W < 480);
  }
  function findingsHeight(w) {            /* natural height of the findings box at width w (narrow screens) */
    findEl.classList.remove('mf-tight');
    findColumns();
    findEl.style.width = Math.round(w) + 'px'; findEl.style.height = 'auto';
    return Math.ceil(findEl.scrollHeight);
  }
  function relRect(el) {
    var a = el.getBoundingClientRect(), b = stage.getBoundingClientRect();
    return { x: a.left - b.left, y: a.top - b.top, w: a.width, h: a.height };
  }

  function layout() {
    var portrait = root.classList.contains('mf-tall'), pad = portrait ? 14 : (W >= 900 ? 24 : 18), topRow = 46;
    var sr = relRect(stripEl);
    var areaTop = topRow + 2, areaBottom = sr.y - 12;
    var R, C;
    if (portrait) {
      var ah = areaBottom - areaTop, gridRows = Math.ceil(CN.papers / 9);
      /* below the map: the grid of papers (and the lane when it is on); under 330 px the four legend lines go under the grid */
      var needC = (LANE ? 62 : 8) + gridRows * 17 - 4 + (W < 330 ? 20 + 4 * 18 : 0) + 10, rh = Math.max(150, ah - needC);
      R = { x: pad, y: areaTop, w: W - 2 * pad, h: rh };
      C = { x: pad, y: R.y + R.h + 6, w: W - 2 * pad, h: areaBottom - (R.y + R.h + 6) };
    } else {
      var rightW = Math.max(190, Math.min(390, Math.round(W * 0.31))), gap = 26;
      R = { x: pad, y: areaTop, w: W - 2 * pad - rightW - gap, h: areaBottom - areaTop };
      C = { x: R.x + R.w + gap, y: areaTop, w: rightW, h: R.h };
    }
    L = { w: W, h: H, portrait: portrait, pad: pad, R: R, C: C, strip: sr, topRow: topRow, areaBottom: areaBottom };
    L.mapRect = { x: R.x, y: R.y, w: R.w, h: R.h - (W < 330 ? 54 : 34) };      /* under the map: the year, and the legend (three lines under 330 px) */
    var mf = Math.min(L.mapRect.w / 975, L.mapRect.h / 610);
    L.map = { sc: mf, ox: L.mapRect.x + (L.mapRect.w - 975 * mf) / 2, oy: L.mapRect.y + (L.mapRect.h - 610 * mf) / 2, w: 975 * mf, h: 610 * mf };
    L.Rbig = portrait ? { x: pad, y: areaTop, w: W - 2 * pad, h: areaBottom - areaTop } : R;
    layoutStream(); layoutGrid(); layoutWall();
    if (portrait) {                                   /* the findings take the lower part; the wall shrinks above them */
      var Fh = findingsHeight(W - 2 * pad), top = 40, availH;        /* the thumbnail sits under the wall's caption */
      L.findBox = { x: pad, y: areaBottom - Fh, w: W - 2 * pad, h: Fh };
      availH = L.findBox.y - 10 - top;
      L.fin = { s: Math.max(0.3, Math.min(1, availH / L.wall.h)), ax: L.wall.x + L.wall.w / 2, ay: L.wall.y, ty: top };
    } else { L.findBox = C; L.fin = { s: 1, ax: 0, ay: 0, ty: 0 }; }
    var F = L.tile(OPEN);
    L.focus = F; L.K0 = L.map.sc / F.s;
    L.mapC = { x: L.map.ox + L.map.w / 2, y: L.map.oy + L.map.h / 2 };
    buildSparks();
    atlas = null; layers = null; atlasState = null;
  }

  /* act 1: the bar. Six segments (the texts, then what each of the five steps keeps), thickness ~ sqrt(count) as on the Screening page */
  function layoutStream() {
    var R = L.Rbig, maxT = Math.min(R.h * 0.34, L.portrait ? 84 : 150), cy = R.y + R.h * 0.5 - 6;
    var counts = [TEXTS].concat(GATES), sq = counts.map(function (n) { return Math.sqrt(n / TEXTS); });
    L.a1 = { x0: R.x + 4, x1: (L.portrait ? R.x + R.w : L.w - L.pad) - 4, cy: cy, maxT: maxT, counts: counts, sq: sq };
  }
  function thickAt(u) {                     /* thickness at fraction u of the bar: steps at the five boundaries */
    var A = L.a1, v = A.sq[0];
    for (var i = 1; i < 6; i++) v += (A.sq[i] - A.sq[i - 1]) * smooth((u - (i / 6 - 0.012)) / 0.024);
    return v * A.maxT;
  }
  function xAt(u) { return L.a1.x0 + (L.a1.x1 - L.a1.x0) * u; }

  function buildSparks() {
    var N = 1304, rg = rng(424242), q, order = [];
    for (q = 0; q < N; q++) order.push(q);
    shuffle(order, rg);
    var kept = GATES.map(function (g) { return Math.round(N * g / TEXTS); });      /* kept after step 1..5 */
    var sp = { n: N, te: new Float32Array(N), v: new Float32Array(N), fate: new Uint8Array(N), fall: new Float32Array(N), drift: new Float32Array(N), life: new Float32Array(N), cl: new Float32Array(N), sy: new Float32Array(N) };
    var surv = [];
    for (q = 0; q < N; q++) {
      var o = order[q], f;
      if (q < kept[4]) f = 6; else if (q < kept[3]) f = 5; else if (q < kept[2]) f = 4; else if (q < kept[1]) f = 3; else if (q < kept[0]) f = 2; else f = 1;
      sp.fate[o] = f;
      sp.te[o] = T.in0 + (T.in1 - T.in0) * Math.pow(rg(), 0.95);
      sp.v[o] = rg() - 0.5;
      sp.fall[o] = 22 + rg() * 46; sp.drift[o] = 10 + rg() * 28; sp.life[o] = 0.55 + rg() * 0.5;
      sp.cl[o] = rg(); sp.sy[o] = rg() - 0.5;
      if (f === 6) surv.push(o);
    }
    surv.sort(function (a, b) { return sp.te[a] - sp.te[b]; });
    sp.surv = surv;
    sparks = sp;
  }

  /* act 2, right column: the estimate lane and the grid of papers */
  function layoutGrid() {
    var C = L.C, n = CN.papers, cols, rows, cell, gap;
    if (L.portrait) { cols = 9; cell = 13; gap = 4; } else { cols = 9; cell = Math.max(11, Math.min(18, Math.floor((C.w - 8 * 5) / 9))); gap = 5; }
    rows = Math.ceil(n / cols);
    var gw = cols * cell + (cols - 1) * gap, gh = rows * cell + (rows - 1) * gap;
    var gx0, gy0, laneY, laneX0, laneX1;
    laneX0 = C.x + 4; laneX1 = C.x + C.w - 4;
    if (L.portrait) { laneY = C.y + 36; gx0 = C.x + 6; gy0 = LANE ? laneY + 26 : C.y + 6; }
    else { laneY = C.y + 40; gx0 = C.x + 4; gy0 = LANE ? laneY + 40 : C.y + 8; }
    var g = { cols: cols, rows: rows, cell: cell, gap: gap, gx0: gx0, gy0: gy0, gw: gw, gh: gh, laneY: laneY, laneX0: laneX0, laneX1: laneX1,
      mu: laneX0 + (laneX1 - laneX0) * 0.56, whisk: (laneX1 - laneX0) * 0.27,
      x: new Float32Array(n), y: new Float32Array(n), dly: new Float32Array(n), arc: new Float32Array(n), cls: new Uint8Array(n), tx: 0, ty: 0 };
    var rg = rng(5150), q, k = 0;
    for (q = 0; q < n; q++) {
      g.x[q] = gx0 + (q % cols) * (cell + gap) + cell / 2;
      g.y[q] = gy0 + Math.floor(q / cols) * (cell + gap) + cell / 2;
      g.dly[q] = q * 0.02 + rg() * 0.1;
      g.arc[q] = 18 + rg() * 30;
    }
    [CN.match, CN.dir, CN.nc, CN.os].forEach(function (c, cls) { for (var z = 0; z < c; z++) g.cls[k++] = cls; });
    var tgEl = cntEl || (cells[1] && cells[1].querySelector('.mf-lab'));                          /* the papers fly into the strip's number, or its level name */
    var tg = tgEl ? relRect(tgEl) : { x: L.strip.x + 20, y: L.strip.y + 10, w: 60, h: 18 };
    g.tx = tg.x + tg.w / 2; g.ty = tg.y + tg.h / 2;
    grid = g;
  }

  /* act 3: the wall. Five bands by the year the first state adopted, a label row above each band, the key under it */
  function layoutWall() {
    var R = L.Rbig, lh = 18, gp = 6, asp = 0.64, best = null, C, b;
    var keyH = 7 + KEY_LH * Math.max(keyLayout(false).rows, keyLayout(true).rows);
    var avail = R.h - keyH - 5 * lh - 4 * gp;
    for (C = 10; C <= 110; C++) {
      var rows = 0;
      for (b = 0; b < 5; b++) rows += Math.ceil(BANDS[b] / C);
      var tw = Math.min(R.w / C, avail / (rows * asp));
      if (!best || tw > best.tw + 1e-6) best = { C: C, rows: rows, tw: tw };
    }
    var tw2 = best.tw, th2 = tw2 * asp, Cc = best.C, rowsTotal = best.rows;
    var totalH = rowsTotal * th2 + 5 * lh + 4 * gp, x0 = L.portrait ? R.x + (R.w - Cc * tw2) / 2 : R.x, y = R.y + Math.max(0, (R.h - keyH - totalH) / 2);
    var bandBox = [], start = 0;
    for (b = 0; b < 5; b++) {
      var rowsB = Math.ceil(BANDS[b] / Cc);
      bandBox.push({ x: x0, y: y + lh, w: Cc * tw2, h: rowsB * th2, start: start, n: BANDS[b], ly: y });
      y += lh + rowsB * th2 + gp; start += BANDS[b];
    }
    L.C2 = Cc; L.tw = tw2; L.th = th2; L.lh = lh; L.keyH = keyH; L.bandBox = bandBox;
    var first = bandBox[0], last = bandBox[4];
    L.wall = { x: Math.floor(x0 - 2), y: Math.floor(first.ly - 2), w: Math.ceil(Cc * tw2 + 4), h: Math.ceil(last.y + last.h - first.ly + 4) };
    L.keyY = R.y + R.h - 6;
    var cache = {};
    L.tile = function (ti) {
      if (cache[ti]) return cache[ti];
      var bx = bandBox[bandOf[ti]], j = ti - bx.start, col = j % Cc, row = Math.floor(j / Cc);
      var cx0 = bx.x + col * tw2, cy0 = bx.y + row * th2, inset = Math.max(0.5, tw2 * 0.05);
      var s = Math.min((tw2 - 2 * inset) / 975, (th2 - 2 * inset) / 610);
      var o = { x: cx0, y: cy0, w: tw2, h: th2, s: s, mx: cx0 + (tw2 - 975 * s) / 2, my: cy0 + (th2 - 610 * s) / 2 };
      cache[ti] = o; return o;
    };
  }

  /* The wall as one bitmap (drawn in slices across frames while acts 1 and 2 play), and the wall on the clock as layers:
     land and the first states (layer 0), then the states of each later year. */
  function newCanvas(w, h) { var c = document.createElement('canvas'); c.width = Math.max(1, w); c.height = Math.max(1, h); return c; }
  function paintTile(c, ti) {
    c.fillStyle = rgba(th.land, 1);
    if (allSimp) c.fill(allSimp);
    for (var q = tOff[ti]; q < tOff[ti + 1]; q++) { c.fillStyle = rgba(classRGB(classOfD(adD[q])), 1); c.fill(PS[adS[q]]); }
  }
  function initAtlas() {
    var Wl = L.wall, res = Math.min(4, dpr * 2), lr = dpr, d;
    atlasState = { next: 0, res: res, lr: lr, done: false };
    atlas = newCanvas(Math.round(Wl.w * res), Math.round(Wl.h * res));
    layers = [];
    for (d = 0; d <= CLK; d++) layers.push(newCanvas(Math.round(Wl.w * lr), Math.round(Wl.h * lr)));
  }
  function buildAtlas(budgetMs) {          /* returns true when finished */
    if (!atlas) initAtlas();
    var st = atlasState, Wl = L.wall, c1 = atlas.getContext('2d'), t0 = performance.now();
    var cs = layers.map(function (l) { return l.getContext('2d'); });
    while (st.next < nT) {
      var ti = st.next++, o = L.tile(ti), z, d;
      c1.setTransform(st.res * o.s, 0, 0, st.res * o.s, st.res * (o.mx - Wl.x), st.res * (o.my - Wl.y));
      paintTile(c1, ti);
      var a0 = st.lr * o.s, e0 = st.lr * (o.mx - Wl.x), f0 = st.lr * (o.my - Wl.y);
      cs[0].setTransform(a0, 0, 0, a0, e0, f0);
      cs[0].fillStyle = rgba(obsT[ti] ? th.land : th.veil, 1); cs[0].fill(allSimp);
      if (obsT[ti]) {
        for (z = tOff[ti]; z < tOff[ti + 1]; z++) {
          d = adD[z];
          if (d > CLK) continue;
          cs[d].setTransform(a0, 0, 0, a0, e0, f0); cs[d].fillStyle = rgba(classRGB(classOfD(d)), 1); cs[d].fill(PS[adS[z]]);
        }
      }
      if (budgetMs && performance.now() - t0 > budgetMs) break;
    }
    if (st.next >= nT) {
      c1.setTransform(1, 0, 0, 1, 0, 0);
      cs.forEach(function (c) { c.setTransform(1, 0, 0, 1, 0, 0); });
      st.done = true;
    }
    return st.done;
  }

  /* ------------------------------------------------------------------ camera */
  var cam = { k: 1, tx: 0, ty: 0 };
  function updateCamera(t) {
    var e = easeInOut(ramp(t, T.pb0, T.pb1)), F = L.focus, kk = Math.exp(lerp(Math.log(L.K0), 0, e));
    var u = (L.K0 - kk) / (L.K0 - 1);
    var fcx = F.mx + 975 * F.s / 2, fcy = F.my + 610 * F.s / 2;
    var px = lerp(L.mapC.x, fcx, u), py = lerp(L.mapC.y, fcy, u);
    cam.k = kk; cam.tx = px - fcx * kk; cam.ty = py - fcy * kk;
    CV.sc = kk * F.s; CV.ox = F.mx * kk + cam.tx; CV.oy = F.my * kk + cam.ty; CV.w = 975 * CV.sc; CV.h = 610 * CV.sc;
  }

  /* ------------------------------------------------------------------ captions */
  function wrapLines(str, maxW, size, weight, maxLines) {   /* at most maxLines lines: break at spaces, or anywhere in Chinese */
    var cjk = /[\u3400-\u9fff]/.test(str), tokens = cjk ? str.split('') : str.split(' '), lines = [], cur = '', i, next;
    for (i = 0; i < tokens.length; i++) {
      next = cur ? cur + (cjk ? '' : ' ') + tokens[i] : tokens[i];
      if (cur && textWidth(next, size, weight) > maxW) { lines.push(cur); cur = cjk ? tokens[i].replace(/^ /, '') : tokens[i]; } else cur = next;
    }
    lines.push(cur);
    return lines.length <= maxLines ? lines : null;
  }
  function fitCaption(list, maxW, size) {      /* the full wording on one line; else the longest that fits two lines at 13 px; else the shortest, smaller (never below 12) */
    var q, ls;
    if (textWidth(list[0], size, 600) <= maxW) return { lines: [list[0]], size: size };
    for (q = 0; q < list.length; q++) { ls = wrapLines(list[q], maxW, 13, 600, 2); if (ls) return { lines: ls, size: 13 }; }
    var last = list[list.length - 1], w = textWidth(last, size, 600);
    return { lines: [last], size: Math.max(12, Math.floor(size * maxW / w * 10) / 10) };
  }
  /* a text fades in over [a, b] and out over [c, d]; the next text on the same line starts after d: two texts are never on one line */
  function seq(t, a, b, c, d) { return smooth((t - a) / (b - a)) * (1 - smooth((t - c) / (d - c))); }
  function caption(t) {
    var maxW = L.w - 2 * L.pad - (L.portrait ? 92 : 104), size = L.portrait ? 14 : 15, a, main, sub;
    var list = [                                                         /* in from, in to, out from, out to, wording, small text */
      [T.pick0 + 0.7, T.pick0 + 1.1, T.pb0 - 0.5, T.pb0 - 0.15, S.nurse, S.signed],
      [T.pb0, T.pb0 + 0.4, T.c0 - 0.5, T.c0 - 0.1, S.wallCap, null],
      [T.c0, T.c0 + 0.4, T.c1, T.c1 + 0.3, S.clockCap, null],
      [T.c1 + 0.4, T.c1 + 0.8, 1e9, 1e9 + 1, S.wallCap, null]            /* the wall keeps its name in the last frame */
    ];
    for (var q = 0; q < list.length; q++) {
      a = seq(t, list[q][0], list[q][1], list[q][2], list[q][3]);
      if (a <= 0.01) continue;
      main = list[q][4]; sub = list[q][5];
      var f = fitCaption(Array.isArray(main) ? main : [main], maxW, size);
      var below = sub && f.lines.length === 1 && textWidth(f.lines[0], f.size, 600) + 12 + textWidth(sub, 13, 400) >= maxW;     /* no room beside the title: under it */
      for (var li = 0; li < f.lines.length; li++) text(f.lines[li], L.pad + 2, f.lines.length === 1 && !below ? 28 : 20 + li * 17, { size: f.size, weight: 600, color: th.ink, alpha: a });
      if (sub && f.lines.length === 1) {
        if (below) text(sub, L.pad + 2, 37, { size: 13, color: th.muted, alpha: a });
        else text(sub, L.pad + 2 + textWidth(f.lines[0], f.size, 600) + 12, 28, { size: 13, color: th.muted, alpha: a });
      }
    }
  }

  /* ------------------------------------------------------------------ act 1: texts through the bar */
  function drawAct1(ctx2, t) {
    if (t > T.pick1 + 1.0) return;
    var A = L.a1, sp = sparks, q, i;
    var fade = 1 - smooth((t - (T.pick0 - 0.1)) / 0.9), appear = smooth(t / 0.7);
    var base = fade * appear;
    if (base > 0.003) {
      /* the bar */
      var steps = 96, xs = [], ts = [];
      for (i = 0; i <= steps; i++) { var u = i / steps; xs.push(xAt(u)); ts.push(thickAt(u)); }
      ctx.beginPath();
      for (i = 0; i <= steps; i++) { if (i) ctx.lineTo(xs[i], A.cy - ts[i] / 2); else ctx.moveTo(xs[i], A.cy - ts[i] / 2); }
      for (i = steps; i >= 0; i--) ctx.lineTo(xs[i], A.cy + ts[i] / 2);
      ctx.closePath();
      ctx.fillStyle = rgba(th.slate, 0.12 * base); ctx.fill();
      ctx.strokeStyle = rgba(th.slate, 0.35 * base); ctx.lineWidth = 1; ctx.stroke();
      /* counters over the two ends */
      var f = Math.pow(ramp(t, T.in0, T.in1 + T.trav * 0.5), 1 / 0.95), ent = TEXTS * f, arrived = 0;
      for (i = 0; i < sp.surv.length; i++) if (t >= sp.te[sp.surv[i]] + T.trav) arrived++;
      var cand = CANDS * arrived / sp.surv.length, big = L.portrait ? 20 : 24, ty = A.cy - A.maxT / 2 - (L.portrait ? 46 : 50);
      tabular(fmt(ent), A.x0, ty, { size: big, weight: 500, color: th.ink, alpha: base });
      text(S.texts, A.x0, ty + 18, { size: 12, color: th.muted, alpha: base });
      text(fill(S.dot, { n: fmt(Math.round(TEXTS / sp.n)) }), A.x0, ty + 34, { size: 12, color: th.muted, alpha: base * 0.85 });
      tabular(fmt(cand), A.x1, ty, { size: big, weight: 600, color: th.accent, alpha: base, align: 'right' });
      text(S.cands, A.x1, ty + 18, { size: 12, color: th.muted, alpha: base, align: 'right' });
    }
    /* particles: text stand-ins inside the bar. Those a step removes drop out of it; survivors turn blue */
    var dots = {}, keys = [], r0 = L.portrait ? 1.2 : 1.5;
    function add(style, x, y, r) { var a = dots[style]; if (!a) { a = dots[style] = []; keys.push(style); } a.push(x, y, r); }
    for (q = 0; q < sp.n; q++) {
      var tt = t - sp.te[q];
      if (tt < 0) continue;
      var uu = tt / T.trav, fate = sp.fate[q], uExit = fate === 6 ? 2 : fate / 6;
      var uo = Math.min(uu, uExit > 1 ? 1 : uExit), x, y, al = 0.85 * fade, col = th.slate;
      var thick = thickAt(Math.min(uo, 1));
      if (fate === 6) {
        var uc = Math.min(uu, 1);
        x = xAt(uc); y = A.cy + sp.v[q] * thickAt(uc) * 0.86;
        if (uu >= 1) {                       /* the cluster at the end */
          var kq = sp.surv.indexOf(q);
          x = A.x1 - 6 - (kq % 7) * 5.2; y = A.cy + ((Math.floor(kq / 7) - 1) * 6.2);
        }
        col = mix(th.slate, th.accent, smooth((uu - 0.78) / 0.2));
        add(rgbaQ(col, al), x, y, r0 + (uu >= 1 ? 0.5 : 0) + (uu > 0.8 ? 0.3 : 0));
      } else {
        var xe = xAt(uExit), ye = A.cy + sp.v[q] * thickAt(uExit - 0.001) * 0.86;
        if (uu < uExit) { x = xAt(uu); y = A.cy + sp.v[q] * thickAt(uu) * 0.86; add(rgbaQ(col, al), x, y, r0); }
        else {
          var tau = (uu - uExit) * T.trav / sp.life[q];
          if (tau >= 1) continue;
          x = xe + sp.drift[q] * easeOut(tau); y = ye + sp.fall[q] * easeIn(tau * 0.9 + 0.1 * tau);
          add(rgbaQ(col, al * (1 - tau)), x, y, r0);
        }
      }
    }
    for (var kk = 0; kk < keys.length; kk++) {
      var arr = dots[keys[kk]]; ctx.fillStyle = keys[kk]; ctx.beginPath();
      for (i = 0; i < arr.length; i += 3) { ctx.moveTo(arr[i] + arr[i + 2], arr[i + 1]); ctx.arc(arr[i], arr[i + 1], arr[i + 2], 0, TAU); }
      ctx.fill();
    }
  }

  /* ------------------------------------------------------------------ act 2: the nurse-compact map */
  var flip = null;
  function buildFlip() {
    flip = new Float32Array(nS);
    var rr = rng(11);
    for (var q = 0; q < nS; q++) { var y = nurseYear[q]; flip[q] = y ? T.wave0 + ((y - Y0) / (Y1 - Y0 + 1)) * (T.wave1 - T.wave0) + rr() * 0.16 : 1e9; }
  }
  function drawMap(ctx2, t) {
    var mapIn = smooth((t - (T.pick0 + 0.4)) / 0.8);
    if (mapIn <= 0.003) return;
    var mapA = (1 - smooth((t - (T.pb1 - 0.25)) / 0.4)) * mapIn;
    if (mapA <= 0.003) return;
    var m = CV, q, morph = smooth((t - (T.pb0 + 0.1)) / 0.9);
    if (t < T.pb0) {            /* before the pull-back the camera is at rest: the map fills the rectangle */
      m.sc = L.map.sc; m.ox = L.map.ox; m.oy = L.map.oy; m.w = L.map.w; m.h = L.map.h;
    }
    ctx.save();
    ctx.beginPath(); ctx.rect(L.Rbig.x - 2, L.Rbig.y - 2, L.Rbig.w + 4, L.Rbig.h + 4); ctx.clip();
    ctx.globalAlpha *= mapA;
    ctx.translate(m.ox, m.oy); ctx.scale(m.sc, m.sc);
    var grow = 0.94 + 0.06 * mapIn;
    if (t < T.pick1 + 0.5) { ctx.translate(975 / 2 * (1 - grow), 610 / 2 * (1 - grow)); ctx.scale(grow, grow); }
    for (q = 0; q < nS; q++) {
      var colr = th.land, fl = flip[q];
      if (t >= fl - 0.05) {
        var f = smooth((t - fl) / 0.7);
        colr = mix(th.land, BC[nurseBin[q]], f);
      }
      if (morph > 0 && nurseYear[q]) colr = mix(colr, classRGB(classOfD(nurseYear[q] - Y0)), morph);
      ctx.fillStyle = rgba(colr, 1); ctx.fill(PF[q]);
    }
    var bordA = 1 - smooth((t - (T.pb0 + 0.2)) / 0.9);
    if (bordA > 0.01 && m.sc > 0.12) {
      ctx.lineWidth = 0.9 / m.sc; ctx.strokeStyle = rgba(th.hair, 0.95 * bordA);
      for (q = 0; q < nS; q++) ctx.stroke(PF[q]);
    }
    ctx.restore();
    /* a flash on each state at the moment it joins */
    if (t >= T.wave0 - 0.1 && t <= T.wave1 + 1.4 && t < T.pb0) {
      ctx.save();
      ctx.beginPath(); ctx.rect(L.Rbig.x - 2, L.Rbig.y - 2, L.Rbig.w + 4, L.Rbig.h + 4); ctx.clip();
      ctx.translate(m.ox, m.oy); ctx.scale(m.sc, m.sc);
      for (q = 0; q < nS; q++) {
        var dt = t - flip[q];
        if (dt < 0 || dt > 0.7) continue;
        var pp = dt / 0.7;
        ctx.lineWidth = (0.8 + 1.1 * (1 - pp)) / m.sc; ctx.strokeStyle = rgba(th.amber, 0.6 * (1 - pp)); ctx.lineJoin = 'round';
        ctx.stroke(PF[q]);
      }
      ctx.restore();
    }
  }

  function drawYear(ctx2, t) {
    if (t < T.wave0 - 0.3 || t > T.pb0) return;
    var a = smooth((t - (T.wave0 - 0.3)) / 0.5) * (1 - smooth((t - (T.est0 + 0.2)) / 0.6));
    var keyA = smooth((t - (T.wave0 - 0.3)) / 0.5) * (1 - smooth((t - (T.pb0 - 0.55)) / 0.4));      /* gone before the pull-back starts */
    var wf = clamp01((t - T.wave0) / (T.wave1 - T.wave0)), year = Math.min(Y1, Y0 + Math.floor(wf * (Y1 - Y0 + 1))), tr = 0, q;
    for (q = 0; q < nS; q++) if (t >= flip[q]) tr++;
    var y = L.R.y + L.R.h - 6, x = L.R.x + 2, size = L.portrait ? 26 : 32, size2 = 12, sw = 10;
    var xr = L.R.x + L.R.w - 2;
    /* the Home card's year bins, as a ramp at the right: full labels when they fit, else the first year of each bin */
    var full = D.nurse.binLabels, starts = NB.map(function (b) { return String(b[0]); }), ends = starts.map(function (s, i) { return i === 0 || i === starts.length - 1 ? s : ''; });
    var modes = [full, starts, ends], lab = ends, kw = 0, showCount = false, m;
    function width(list) { var w = 0; for (var i = 0; i < list.length; i++) w += sw + 4 + textWidth(list[i], size2, 400) + 10; return w - 10; }
    var yearW = tabularWidth(String(Y1), size, 300), cnt = fill(S.ofStates, { a: NURSE_N, n: NURSE_N }), cntW = textWidth(cnt, 13, 400);
    var room = L.R.w - 4 - yearW - 14;
    for (m = 0; m < modes.length; m++) { if (width(modes[m]) <= room) { lab = modes[m]; showCount = width(lab) + 24 + cntW <= room; break; } }
    kw = width(lab);
    if (L.w < 330) {                              /* narrow: the card's full legend in three left-aligned lines under the map, the year at the right */
      var y3 = L.R.y + L.R.h - 6, lh = 18;
      if (a > 0.01) {
        tabular(String(year), xr, y3 - lh, { size: size, weight: 300, color: th.ink, alpha: a, align: 'right' });
        text(fill(S.ofStates, { a: tr, n: NURSE_N }), xr, y3, { size: 13, color: th.muted, alpha: a, align: 'right' });
      }
      if (keyA > 0.01) for (q = 0; q < full.length; q++) {
        var yl = y3 - (full.length - 1 - q) * lh;
        ctx.fillStyle = rgba(BC[q], keyA); ctx.fillRect(x, yl - sw + 1, sw, sw);
        text(full[q], x + sw + 4, yl, { size: size2, color: th.muted, alpha: keyA });
      }
      return;
    }
    if (a > 0.01) {
      var w = tabular(String(year), x, y, { size: size, weight: 300, color: th.ink, alpha: a });
      if (showCount) text(fill(S.ofStates, { a: tr, n: NURSE_N }), x + w + 12, y, { size: 13, color: th.muted, alpha: a });
    }
    if (keyA > 0.01) {
      var kx = xr - kw;
      for (q = 0; q < lab.length; q++) {
        ctx.fillStyle = rgba(BC[q], keyA); ctx.fillRect(kx, y - sw + 1, sw, sw);
        if (lab[q]) text(lab[q], kx + sw + 4, y, { size: size2, color: th.muted, alpha: keyA });
        kx += sw + 4 + textWidth(lab[q], size2, 400) + 10;
      }
    }
  }

  function drawEstimate(ctx2, t) {
    if (t < (LANE ? T.est0 - 0.2 : T.grid0 - 0.1) || t > T.dep1 + 0.6) return;
    var g = grid, n = CN.papers, kk;
    var rp = LANE ? ramp(t, T.est0, T.est1) : 0, laneA = LANE ? smooth((t - (T.est0 - 0.1)) / 0.5) * (1 - smooth((t - (T.grid0 + 1.6)) / 0.5)) : 0;
    /* the estimate leaves the map for the lane (ESTIMATE_LANE only) */
    if (rp > 0 && rp < 1) {
      var sx = L.map.ox + L.map.w, sy = L.map.oy + L.map.h * 0.5, ex = g.mu, ey = g.laneY, e = easeInOut(rp);
      ctx.strokeStyle = rgba(th.accent, 0.35 * (1 - rp)); ctx.lineWidth = 1.2; ctx.beginPath();
      for (var q = 0; q <= 24 * e; q++) {
        var tt = q / 24, u = 1 - tt, px = u * u * sx + 2 * u * tt * ((sx + ex) / 2) + tt * tt * ex, py = u * u * sy + 2 * u * tt * (Math.min(sy, ey) - 30) + tt * tt * ey;
        if (q) ctx.lineTo(px, py); else ctx.moveTo(px, py);
      }
      ctx.stroke();
      var u2 = 1 - e, hx = u2 * u2 * sx + 2 * u2 * e * ((sx + ex) / 2) + e * e * ex, hy = u2 * u2 * sy + 2 * u2 * e * (Math.min(sy, ey) - 30) + e * e * ey;
      ctx.fillStyle = rgba(th.accent, 0.95); ctx.beginPath(); ctx.arc(hx, hy, 3.4, 0, TAU); ctx.fill();
    }
    if (laneA > 0.01) {
      ctx.strokeStyle = rgba(th.hair2, laneA); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(g.laneX0, g.laneY); ctx.lineTo(g.laneX1, g.laneY); ctx.stroke();
      var half = g.whisk * easeOut(ramp(t, T.est1 - 0.4, T.est1 + 0.5));
      ctx.strokeStyle = rgba(th.ink, 0.85 * laneA); ctx.lineWidth = 1.6; ctx.beginPath();
      ctx.moveTo(g.mu - half, g.laneY); ctx.lineTo(g.mu + half, g.laneY);
      ctx.moveTo(g.mu - half, g.laneY - 6); ctx.lineTo(g.mu - half, g.laneY + 6);
      ctx.moveTo(g.mu + half, g.laneY - 6); ctx.lineTo(g.mu + half, g.laneY + 6);
      ctx.stroke();
      ctx.fillStyle = rgba(th.accent, laneA); ctx.beginPath(); ctx.arc(g.mu, g.laneY, Math.max(0, (L.portrait ? 5.5 : 7) * easeOutBack(ramp(t, T.est1 - 0.6, T.est1))), 0, TAU); ctx.fill();
      text(S.estLab, g.laneX0, g.laneY - 16, { size: 12, weight: 500, color: th.muted, alpha: laneA });
    }
    /* the papers: from the lane, or from the east edge of the nurse map (over the sea, on no state), into the grid, coloured by result, then into the strip */
    var cellR = g.cell / 2 - 0.5, depP = ramp(t, T.dep0, T.dep1), ox = LANE ? g.mu : L.map.ox + L.map.w, oy = LANE ? g.laneY : L.map.oy + L.map.h * 0.5;
    for (kk = 0; kk < n; kk++) {
      var t0 = T.grid0 + g.dly[kk], p = ramp(t, t0, t0 + GRID_FLY);
      if (p <= 0) continue;
      var e2 = easeInOut(p), u3 = 1 - e2;
      var x = u3 * u3 * ox + 2 * u3 * e2 * ((ox + g.x[kk]) / 2) + e2 * e2 * g.x[kk];
      var y = u3 * u3 * oy + 2 * u3 * e2 * (Math.min(oy, g.y[kk]) - g.arc[kk]) + e2 * e2 * g.y[kk];
      var rr = p < 0.7 ? 2.6 : lerp(2.6, cellR, easeOut((p - 0.7) / 0.3));
      if (p >= 1) rr = cellR * (1 + 0.12 * Math.sin(clamp01(ramp(t, t0 + GRID_FLY, t0 + GRID_FLY + 0.3)) * Math.PI));
      var cl = g.cls[kk], cp = smooth((t - (T.cls0 + kk / n * (T.cls1 - T.cls0))) / 0.35);
      var colr = mix(th.accent, th.res[cl], cp), alpha = 0.98 * smooth(p * 3);
      if (depP > 0) {
        var dk = easeInOut(ramp(t, T.dep0 + kk * 0.008, T.dep1 + kk * 0.008 - 0.3)), dx = g.tx, dy = g.ty;
        var u4 = 1 - dk;
        var x2 = u4 * u4 * x + 2 * u4 * dk * ((x + dx) / 2) + dk * dk * dx, y2 = u4 * u4 * y + 2 * u4 * dk * (Math.max(y, dy) - 20) + dk * dk * dy;
        x = x2; y = y2; rr = lerp(rr, 2.2, dk);
        if (dk >= 1) alpha = 0;
      }
      if (alpha <= 0.01) continue;
      ctx.fillStyle = rgba(colr, alpha); ctx.beginPath(); marker(cp > 0.5 ? cl : 0, x, y, rr); ctx.fill();
    }
    /* legend: the four results with their counts (the site's labels) */
    /* the legend stays clear until the last paper has landed in the grid (no circle crosses its text), then fades in; it is gone as the papers depart */
    var landed = 0; for (kk = 0; kk < n; kk++) if (g.dly[kk] > landed) landed = g.dly[kk];
    var legendA = smooth((t - (T.grid0 + landed + GRID_FLY)) / 0.4) * (1 - smooth((t - T.dep0) / 0.3));
    if (legendA > 0.01) {
      var items = [CN.match, CN.dir, CN.nc, CN.os], lx, ly, lh2 = L.portrait ? 18 : 22;
      if (L.portrait && L.w >= 330) { lx = g.gx0 + g.gw + 16; ly = g.gy0 + 14; }          /* beside the grid; under 330 px, under it, left-aligned */
      else { lx = g.gx0; ly = g.gy0 + g.gh + (L.portrait ? 20 : 28); }
      for (kk = 0; kk < 4; kk++) {
        ctx.fillStyle = rgba(th.res[kk], legendA); ctx.beginPath(); marker(kk, lx + 5, ly + kk * lh2 - 4, 4.4); ctx.fill();
        var nw = tabular(String(items[kk]), lx + 16, ly + kk * lh2, { size: 13, weight: 600, color: th.ink, alpha: legendA });
        text(CFG.labels[kk], lx + 16 + nw + 6, ly + kk * lh2, { size: 12, color: th.text, alpha: legendA });
      }
    }
  }
  function marker(cls, x, y, r) {            /* circle, diamond and square, as in the Estimates plot */
    if (cls === 0) { ctx.moveTo(x + r, y); ctx.arc(x, y, r, 0, TAU); }
    else if (cls === 1) { var d = r * 1.28; ctx.moveTo(x, y - d); ctx.lineTo(x + d, y); ctx.lineTo(x, y + d); ctx.lineTo(x - d, y); ctx.closePath(); }
    else if (cls === 2) { var s = r * 0.92; ctx.rect(x - s, y - s, s * 2, s * 2); }
    else { var h = r * 1.2; ctx.moveTo(x, y - h); ctx.lineTo(x + h, y + h * 0.8); ctx.lineTo(x - h, y + h * 0.8); ctx.closePath(); }
  }

  /* ------------------------------------------------------------------ act 3: the wall */
  var VECTOR_K = 3.2;
  function finP(t) { return easeInOut(ramp(t, T.fin0, T.fin1)); }
  function finApply(t) {                          /* narrow screens: the wall shrinks about its top centre */
    var s = lerp(1, L.fin.s, finP(t));
    if (s !== 1) { var ty = lerp(L.fin.ay, L.fin.ty, finP(t)); ctx.translate(L.fin.ax, ty); ctx.scale(s, s); ctx.translate(-L.fin.ax, -L.fin.ay); }
  }
  /* the labels do not shrink: on a narrow screen they are gone (opacity 0) before the wall starts to move */
  function finFade(t) { return L.fin.s < 1 ? 1 - smooth((t - (T.fin0 - 0.55)) / 0.5) : 1; }
  function drawWall(ctx2, t) {
    if (t < T.pb0) return;
    var a = t >= T.pb1 ? 1 : smooth((L.K0 - cam.k) / (L.K0 - 6));
    if (a <= 0.003) return;
    var Wl = L.wall, kk = cam.k, ti;
    ctx.save();
    ctx.beginPath(); ctx.rect(L.Rbig.x - 2, L.Rbig.y - 2, L.Rbig.w + 4, L.Rbig.h + 4); ctx.clip();
    finApply(t);
    ctx.translate(cam.tx, cam.ty); ctx.scale(kk, kk);
    ctx.globalAlpha *= a;
    if (kk <= VECTOR_K && atlasState && atlasState.done) ctx.drawImage(atlas, Wl.x, Wl.y, Wl.w, Wl.h);
    else {
      var x0 = -cam.tx / kk, x1 = (L.w - cam.tx) / kk, y0 = -cam.ty / kk, y1 = (L.h - cam.ty) / kk;
      for (ti = 0; ti < nT; ti++) {
        if (ti === OPEN && t < T.pb1) continue;
        var o = L.tile(ti);
        if (o.x + o.w < x0 || o.x > x1 || o.y + o.h < y0 || o.y > y1) continue;
        ctx.save(); ctx.translate(o.mx, o.my); ctx.scale(o.s, o.s); paintTile(ctx, ti); ctx.restore();
      }
    }
    ctx.restore();
    drawClock(t);
  }
  function clockK(t) { return Math.max(0, Math.min(CLK + 0.999, (t - T.cc0) / T.step)); }
  function clockAmt(d, k) { var yr = Math.floor(k); return d === 0 || d < yr ? 1 : (d === yr ? smooth((k - yr) / 0.4) : 0); }
  function clockA(t) { return smooth((t - T.c0) / 0.5) * (1 - smooth((t - T.c1) / 0.6)); }
  function medA(t) { return smooth((t - T.med0) / 0.5) * (1 - smooth((t - T.c1) / 0.6)); }
  function drawClock(t) {
    var a = clockA(t);
    if (a <= 0.003 || !atlasState || !atlasState.done) return;
    var Wl = L.wall, k = clockK(t), d;
    ctx.save();
    ctx.beginPath(); ctx.rect(L.Rbig.x - 2, L.Rbig.y - 2, L.Rbig.w + 4, L.Rbig.h + 4); ctx.clip();
    finApply(t);
    ctx.globalAlpha = a; ctx.drawImage(layers[0], Wl.x, Wl.y, Wl.w, Wl.h);
    for (d = 1; d <= CLK; d++) {
      var am = clockAmt(d, k);
      if (am <= 0.003) break;
      ctx.globalAlpha = a * am; ctx.drawImage(layers[d], Wl.x, Wl.y, Wl.w, Wl.h);
    }
    ctx.restore();
  }

  function drawBandLabels(t) {
    var a = smooth((t - (T.pb1 - 0.3)) / 0.6) * finFade(t);
    if (a <= 0.003) return;
    var ma = medA(t), mMax = Math.max.apply(null, MED), size = 12;
    for (var q = 0; q < 5; q++) {
      var B = L.bandBox[q];
      text(S.bands[q], B.x, B.ly + 13, { size: size, weight: 500, color: th.muted, alpha: a });
      if (ma > 0.003) {
        var lab = fill(S.med, { m: MED[q] }), xr = B.x + B.w, bw = (L.portrait ? 34 : 56) * MED[q] / mMax, lw = textWidth(lab, size, 600);
        text(lab, xr, B.ly + 13, { size: size, weight: 600, color: th.ink, alpha: ma * a, align: 'right' });
        ctx.fillStyle = rgba(th.accent, ma * a); ctx.fillRect(xr - lw - 8 - bw, B.ly + 5, bw, 5);
      }
    }
  }

  function swatch(rgb, x, y, sw, a) {
    ctx.fillStyle = rgba(rgb, a); ctx.fillRect(x, y - sw + 1, sw, sw);
    ctx.strokeStyle = rgba(th.hair2, a); ctx.lineWidth = 1; ctx.strokeRect(x + 0.5, y - sw + 1.5, sw - 1, sw - 1);
  }
  /* the key under the wall: years after the first state; during the clock, the clock and what it shows */
  function drawKey(t) {
    var lf = finFade(t);
    var pre = smooth((t - (T.pb0 + 0.2)) / 0.5) * (1 - smooth((t - (T.c0 - 0.5)) / 0.4));       /* the all-years key, until the clock begins */
    var clock = seq(t, T.c0, T.c0 + 0.4, T.c1, T.c1 + 0.3);                                       /* the clock's key */
    var post = L.fin.s < 1 ? 0 : smooth((t - (T.c1 + 0.4)) / 0.4);                                /* the all-years key again (not on the thumbnail) */
    if (pre + post > 0.003) keyRow((pre + post) * lf, false, 0);
    if (clock > 0.003) keyRow(clock * lf, true, clockK(t));
  }
  var KEY_LABELS = ['0', '1\u20132', '3\u20135', '6\u20139', '10+'], KEY_SIZE = 12, KEY_SW = 10, KEY_GL = 4, KEY_GI = 10, KEY_NS = 15, KEY_LH = 17;
  /* The key is a flow of items (the title, then one swatch with its label each) that wraps to as many rows as the width needs. */
  function keyLayout(clock) {
    var x0 = L.Rbig.x, maxX = L.Rbig.x + L.Rbig.w - 2, items = [], placed = [], row = 0, x = x0, q, nCls = clock ? 3 : 5, it, i;
    var h = S.clockHead;
    items.push({ k: 'head', w: clock ? textWidth(h[0], KEY_SIZE, 500) + 4 + tabularWidth('0', KEY_NS, 700) + 4 + textWidth(h[1], KEY_SIZE, 500) : textWidth(S.key, KEY_SIZE, 500) });
    for (q = 0; q < nCls; q++) items.push({ k: 'cls', q: q, w: KEY_SW + KEY_GL + textWidth(KEY_LABELS[q], KEY_SIZE, 400) });
    items.push({ k: 'none', w: KEY_SW + KEY_GL + textWidth(S.none, KEY_SIZE, 400) });
    if (clock) items.push({ k: 'unobs', w: KEY_SW + KEY_GL + textWidth(S.unobs, KEY_SIZE, 400) });
    for (i = 0; i < items.length; i++) {
      it = items[i];
      if (i > 0 && x > x0 && x + it.w > maxX) { row++; x = x0; }
      placed.push({ k: it.k, q: it.q, x: x, row: row, w: it.w });
      x += it.w + (i === 0 ? 14 : KEY_GI);
    }
    return { rows: row + 1, placed: placed };
  }
  function keyRow(a, clock, k) {
    var lay = keyLayout(clock), h = S.clockHead, i, p, y;
    for (i = 0; i < lay.placed.length; i++) {
      p = lay.placed[i]; y = L.keyY - (lay.rows - 1 - p.row) * KEY_LH;
      if (p.k === 'head') {
        if (clock) {
          var x = p.x;
          text(h[0], x, y, { size: KEY_SIZE, weight: 500, color: th.text, alpha: a }); x += textWidth(h[0], KEY_SIZE, 500) + 4;
          tabular(String(Math.floor(k)), x, y + 1, { size: KEY_NS, weight: 700, color: th.ink, alpha: a }); x += tabularWidth('0', KEY_NS, 700) + 4;
          text(h[1], x, y, { size: KEY_SIZE, weight: 500, color: th.text, alpha: a });
        } else text(S.key, p.x, y, { size: KEY_SIZE, weight: 500, color: th.text, alpha: a });
      } else if (p.k === 'cls') {
        ctx.fillStyle = rgba(classRGB(4 - p.q), a); ctx.fillRect(p.x, y - KEY_SW + 1, KEY_SW, KEY_SW);
        text(KEY_LABELS[p.q], p.x + KEY_SW + KEY_GL, y, { size: KEY_SIZE, color: th.muted, alpha: a });
      } else {
        swatch(p.k === 'none' ? th.land : th.veil, p.x, y, KEY_SW, a);
        text(p.k === 'none' ? S.none : S.unobs, p.x + KEY_SW + KEY_GL, y, { size: KEY_SIZE, color: th.muted, alpha: a });
      }
    }
  }

  /* ------------------------------------------------------------------ frame */
  function drawFrame(t) {
    if (!L) return;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.globalAlpha = 1; ctx.fillStyle = rgba(th.bg, 1); ctx.fillRect(0, 0, W, H);
    if (!flip) buildFlip();
    updateCamera(t);
    drawWall(ctx, t);
    drawMap(ctx, t);
    drawAct1(ctx, t);
    drawYear(ctx, t);
    drawEstimate(ctx, t);
    drawBandLabels(t);
    drawKey(t);
    caption(t);
    ctx.globalAlpha = 1;
  }

  /* DOM: the strip numbers, the progress hairlines, the findings (a pure function of t, written only on change) */
  function cls(el, name, on) { if (el && el.classList.contains(name) !== on) el.classList.toggle(name, on); }
  var cntEl = root.querySelector('.mf-count');
  function syncDom(t) {
    var p1 = ramp(t, 0.2, T.pick0), p2 = ramp(t, T.wave0 - 0.4, T.dep1), p3 = ramp(t, T.pb0, T.find0);
    var ps = [p1, p2, p3], on1 = t >= T.pick0 - 0.4, on2 = t >= T.dep0 + 0.2, on3 = t >= T.pb1;
    for (var i = 0; i < 3; i++) { cells[i].style.setProperty('--p', (Math.round(ps[i] * 1000) / 10) + '%'); cls(cells[i], 'is-live', ps[i] > 0 && ps[i] < 1); cls(cells[i], 'is-done', ps[i] >= 1); }
    cls(cells[0], 'is-on', on1); cls(cells[1], 'is-on', on2); cls(cells[2], 'is-on', on3);
    if (cntEl) {                            /* the papers count up as they reach the strip */
      var landed = 0, g = grid;
      for (var kk = 0; kk < CN.papers; kk++) if (t >= T.dep0 + kk * 0.008 + (T.dep1 - T.dep0 - 0.3) * 0.96) landed++;
      if (t >= T.dep1 + 0.1) landed = CN.papers;
      var s = String(landed);
      if (cntEl.textContent !== s) cntEl.textContent = s;
      cls(cells[1], 'is-full', landed === CN.papers);
    }
    /* findings: together, in a short stagger */
    var fOn = t >= T.find0 - 0.05;
    cls(findEl, 'is-on', fOn);
    for (var q = 0; q < findItems.length; q++) cls(findItems[q], 'is-on', t >= T.find0 + q * T.fstep);
  }

  function placeFindings() {
    if (!findEl) return;
    var C = L.findBox;
    findEl.style.left = Math.round(C.x) + 'px'; findEl.style.top = Math.round(C.y) + 'px';
    findEl.style.width = Math.round(C.w) + 'px'; findEl.style.height = Math.round(C.h) + 'px';
    findColumns();
    findEl.classList.remove('mf-tight');
    if (!L.portrait && findEl.scrollHeight > findEl.clientHeight + 1) findEl.classList.add('mf-tight');   /* a short box: closer rows */
  }

  /* ------------------------------------------------------------------ stage: size, observers, play state */
  var t = 0, onPoster = false, ratio = 0, playing = false, visible = false, started = false, raf = 0, last = 0, userOverride = false, built = false, sized = false;
  var reduceMQ = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : { matches: false };
  var btnPlay = root.querySelector('[data-act="toggle"]'), btnReplay = root.querySelector('[data-act="replay"]');
  function reduced() { return reduceMQ.matches && !userOverride; }
  function need() { return Math.min(AUTOPLAY, 0.9 * window.innerHeight / Math.max(1, H)); }     /* share in view that starts play */

  function syncTall() {                    /* the tall layout belongs to figures narrower than TALL_BELOW px, whatever the window */
    var tall = root.getBoundingClientRect().width < TALL_BELOW;
    if (root.classList.contains('mf-tall') !== tall) { root.classList.toggle('mf-tall', tall); return true; }
    return false;
  }
  function measure() {
    var flipped = syncTall(), w = stage.clientWidth, h = stage.clientHeight, d = Math.min(window.devicePixelRatio || 1, 2);
    if (w < 60 || h < 60) return false;
    if (!flipped && w === W && h === H && d === dpr && built) return false;
    W = w; H = h; dpr = d;
    canvas.width = Math.round(W * dpr); canvas.height = Math.round(H * dpr);
    return true;
  }
  function build() {
    readTheme(); layout(); placeFindings(); built = true;
    if (reduced()) buildAtlas(0);
    else if (atlasState === null && started) schedule(idleBuild);
  }
  function render() {
    if (!built) return;
    if (atlasState === null && t >= T.pb0 - 3) buildAtlas(0);           /* safety: never reach the wall without it */
    try { drawFrame(t); } catch (e) { root.mfError = String(e && e.message); playing = false; t = T.end; }
    syncDom(t);
  }
  function syncUi() {
    if (btnPlay) {
      btnPlay.setAttribute('aria-pressed', playing ? 'false' : 'true');
      btnPlay.classList.toggle('is-paused', !playing);
    }
  }
  function refresh() {
    var resized = measure();
    if (!built || resized) {
      if (W < 60) return;
      build(); render(); syncUi();
      if (!started && ratio >= need()) { started = true; autoplay(); return; }
    }
    update();
  }
  function update() {
    var want = playing && visible && !document.hidden && built;
    if (want && !raf) { last = 0; raf = requestAnimationFrame(frame); }
    else if (!want && raf) { cancelAnimationFrame(raf); raf = 0; }
  }
  function frame(now) {
    raf = 0;
    if (!playing || !visible || document.hidden) return;
    if (!last) last = now;
    var dt = Math.min(0.05, Math.max(0, (now - last) / 1000));
    last = now; t += dt;
    if (t >= T.end) { t = T.end; playing = false; render(); syncUi(); return; }
    if (!(atlasState && atlasState.done)) { if (t >= T.pb0 - 1.2) buildAtlas(0); else if (t > 0.3) buildAtlas(3); }
    render();
    raf = requestAnimationFrame(frame);
  }
  function autoplay() {
    if (reduced()) { playing = false; t = T.poster; render(); syncUi(); return; }
    playing = true; if (!onPoster) t = 0; onPoster = false; syncUi(); update();      /* from the still, when it is on screen */
  }
  function toggle() {
    if (playing) { playing = false; syncUi(); update(); return; }
    if (reduceMQ.matches) userOverride = true;
    if (t >= T.end - 0.01) t = 0;
    onPoster = false; playing = true; started = true; syncUi(); update();
  }
  function replay() {
    if (reduceMQ.matches) userOverride = true;
    started = true; onPoster = false; t = 0; playing = true; last = 0;
    render(); syncUi(); update();
  }
  if (btnPlay) btnPlay.addEventListener('click', toggle);
  if (btnReplay) btnReplay.addEventListener('click', replay);

  var delay = 0;
  function queue(ms) { window.clearTimeout(delay); delay = window.setTimeout(refresh, ms); }
  if (window.ResizeObserver) new ResizeObserver(function () { queue(80); }).observe(stage);
  window.addEventListener('resize', function () { queue(80); });
  window.addEventListener('orientationchange', function () { queue(200); });
  if (reduceMQ.addEventListener) reduceMQ.addEventListener('change', function () {
    if (reduceMQ.matches) { userOverride = false; playing = false; t = T.poster; if (built) { buildAtlas(0); render(); } }
    else if (visible && !started) { started = true; autoplay(); }
    syncUi(); update();
  });
  document.addEventListener('visibilitychange', update);
  if (window.IntersectionObserver) {
    new IntersectionObserver(function (entries) {
      for (var i = 0; i < entries.length; i++) {
        var en = entries[i];
        ratio = en.intersectionRatio; visible = en.isIntersecting && ratio >= 0.1;
        if (!started && built && ratio >= need()) { started = true; autoplay(); return; }
      }
      update();
    }, { threshold: [0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1] }).observe(stage);
  } else { visible = true; }

  /* the first frame: the start (or, with reduced motion, the last frame). The wall bitmap is built in slices while the
     first two acts play, and in idle time before that. */
  function idleBuild() {
    if (!built || (atlasState && atlasState.done)) return;
    buildAtlas(6);
    if (!(atlasState && atlasState.done)) schedule(idleBuild);
  }
  function schedule(fn) {
    if (window.requestIdleCallback) window.requestIdleCallback(fn, { timeout: 400 }); else window.setTimeout(fn, 60);
  }
  t = reduced() ? T.poster : T.still; onPoster = !reduced();
  measure(); build();
  if (reduced()) { started = true; playing = false; buildAtlas(0); }
  else schedule(idleBuild);
  render(); syncUi();
  /* a hook for checking: root.mfSeek(seconds) shows that moment and pauses; root.mfInfo() reports the state */
  root.mfSeek = function (s) { started = true; onPoster = false; playing = false; t = Math.max(0, Math.min(T.end, +s || 0)); if (!atlasState || !atlasState.done) buildAtlas(0); render(); syncUi(); update(); };
  root.mfInfo = function () { return { t: t, playing: playing, visible: visible, started: started, W: W, H: H, dpr: dpr, T: T, portrait: L && L.portrait, wall: L && L.wall, tile: L && [L.tw, L.th, L.C2], atlas: !!(atlasState && atlasState.done), cam: cam, CV: CV, R: L && L.R, F: L && L.focus, K0: L && L.K0, mapC: L && L.mapC }; };
})();
