/* Concept C, fourteenth round: the opening of the seventh round, then the wall of state-policy maps with two examples.
   Act 1 (Screening): dots standing for policy texts land on one map of the United States; 21 stay lit as candidates.
   Act 2 (Estimates): a design is shown (the nurse licensure compact), the states of the nurse licensure compact turn on in the years they
   joined, an estimate rises and the results fill a grid that moves into the Estimates counter.
   L3: the camera pulls back from the nurse-compact map. It is one tile of a wall of 732 state-policy maps (SPID v1.2 and the
   8 verified policies), in five bands by the year the first state adopted, every tile on one key: years after
   the first state. Both examples then run on one clock, years after the first state, from 0 to 5. A small example: the maps
   of the four laws whose studies measure traffic deaths lift out of the wall; each fills in the states that had adopted by
   each year, and counts those not yet adopting. A broad example: the whole wall runs on the same clock, and the median number
   of states within 5 years appears beside each band, larger in every later band. The last frame holds the whole wall, with
   the medians. Pure function of time. */
(function (G) {
  'use strict';
  var NS = G.NESM;
  var TAU = NS.TAU, clamp01 = NS.clamp01, lerp = NS.lerp, smooth = NS.smooth;
  var easeOut = NS.easeOut, easeInOut = NS.easeInOut, easeOutBack = NS.easeOutBack, ramp = NS.ramp, fmt = NS.fmt;

  function zhSp(w) { return /[\w.]$/.test(w) ? w + ' ' : w; }   // a space between a Latin ending and the Chinese that follows

  /* Every string on the canvas. A list holds wordings from longest to shortest; the first that fits is used. */
  var STR = {
    /*EN:START*/en: {
      cap1: ['Screening: policy texts land on the country', 'Policy texts land on the country'],
      cap2: ['A research design, for example', 'An example design'],
      cap3: ['Nurse licensure compact: states join in different years', 'Nurse compact: states join over the years', 'Nurse compact: states join'],
      cap4: ['Treated and not-yet-treated states'],
      cap5: ['An estimate rises from the wave'],
      texts: 'US policy texts', dot: function (n) { return '1 dot ≈ ' + n + ' texts'; }, cands: 'candidates',
      rules: ['pass 5 screening rules', 'pass 5 rules'],
      treatedT: 'States treated', treated: 'Treated', notYet: 'Not yet treated',
      ofStates: function (a, n) { return a + ' of ' + n + ' states'; },
      estLab: 'Estimate and interval',
      resLab: ['number matches', 'same direction', 'not comparable'],
      confirmed: ['confirmed by a second coder', 'confirmed by 2nd coder', 'confirmed'],
      l1: ['Screening'], l2: ['Estimates'], l3: ['Explore'],
      l2sub: ['papers re-estimated', 'papers'],
      l3sub: ['state policies, one map each', 'policies'],
      l3src: ['Published adoption data + 8 verified policies', 'Adoption data + 8 verified', 'Adoption data'],
      wallT: ['Explore · patterns across policies', 'Explore'],
      wallS: function (n) { return ['The nurse-compact map is one of ' + n + ' state-policy maps', 'One of ' + n + ' state-policy maps']; },
      smallT: ['One outcome (traffic deaths), four laws: how fast each spread', 'Four traffic-safety laws: how fast each spread', 'How fast four laws spread', 'Four laws spread'],
      smallS: function (hi, lo, n) { var nm = STR.en.trafficLc; return ['Year 5 after the first state: ' + nm[hi] + ' in ' + n[hi] + ' states, ' + nm[lo] + ' in ' + n[lo] + '; the states not yet adopting are the comparison group of each study', 'Year 5: ' + nm[hi] + ' in ' + n[hi] + ' states, ' + nm[lo] + ' in ' + n[lo], 'Year 5: ' + STR.en.trafficSh[hi] + ' in ' + n[hi] + ' states, ' + STR.en.trafficSh[lo] + ' in ' + n[lo], 'Year 5: ' + n[hi] + ' vs ' + n[lo] + ' states']; },
      traffic: ['Primary seat belt', 'Seat belt mandate', 'Bicycle helmet', '.08 drunk driving'],
      trafficLc: ['primary seat belt laws', 'seat belt mandates', 'bicycle helmet laws', '.08 drunk-driving laws'],
      trafficSh: ['primary belt laws', 'belt mandates', 'bike helmets', '.08 BAC'],
      lawLab: function (nm, y) { return [nm + ' · from ' + y, nm + ' ' + y]; },
      counts: function (a, b) { return a + ' adopted · ' + b + ' not yet'; },
      clock: [['Year', 'after the first state'], ['Year', 'after first'], ['Year', '']],
      clkNone: ['not yet adopted (comparison)', 'not yet adopted', 'not yet'],
      unobs: ['under 5 years observed', '< 5 yrs observed', '< 5 yrs'],
      broadT: ['A broad pattern: newer state policies spread faster', 'Newer state policies spread faster', 'Newer policies spread faster', 'Spreading faster'],
      broadS: function (m) { return ['States adopting within 5 years of the first, median per band: from ' + m[0] + ' before 1900 to ' + m[4] + ' from 2000', 'States within 5 years (median): ' + m[0] + ' → ' + m[4], 'Median: ' + m[0] + ' → ' + m[4]]; },
      medKey: ['states adopting within 5 years of the first (median)', 'states within 5 yrs (median)'],
      medLab: function (m) { return 'median ' + m + ' in 5 yrs'; },
      endS: ['States × policies × years: one block of data, read many ways', 'One block of data, read many ways'],
      bands: ['before 1900', '1900–49', '1950–79', '1980–99', '2000 on'],
      key: ['Years after the first state', 'Years after first state', 'Yrs after 1st state', 'After 1st'],
      none: 'none'
    },
    /*ZH:START*/zh: {
      cap1: ['筛查：政策文本落到全国各州', '政策文本落到各州'],
      cap2: ['以一个研究设计为例', '研究设计示例'],
      cap3: ['护士执照协定：各州在不同年份加入', '护士协定：各州先后加入'],
      cap4: ['已处理的州与尚未处理的州'],
      cap5: ['从这一波加入中得到一个估计', '得到一个估计'],
      texts: '份美国政策文本', dot: function (n) { return '1 个点 ≈ ' + n + ' 份文本'; }, cands: '个候选',
      rules: ['通过 5 道筛查规则', '通过 5 道规则'],
      treatedT: '已处理的州', treated: '已处理', notYet: '尚未处理',
      ofStates: function (a, n) { return a + ' / ' + n + ' 州'; },
      estLab: '估计值与区间',
      resLab: ['数字一致', '方向一致', '不可比'],
      confirmed: ['经第二位编码者复核', '经复核'],
      l1: ['筛查'], l2: ['估计'], l3: ['探索'],
      l2sub: ['篇已发表论文已重估', '篇已重估'],
      l3sub: ['项州政策，每项一张地图', '项州政策'],
      l3src: ['公开采纳数据 + 8 项已核实政策', '采纳数据 + 8 项已核实', '采纳数据'],
      wallT: ['探索 · 跨政策规律', '探索'],
      wallS: function (n) { return ['护士协定这张图，是 ' + n + ' 张州政策地图中的一张', n + ' 张州政策地图中的一张']; },
      smallT: ['同一个结果（交通死亡），四项法律：各自传得多快', '交通死亡的四项法律：各自传得多快', '四项法律传得多快'],
      smallS: function (hi, lo, n) { var nm = STR.zh.trafficLc.map(zhSp); return ['首个州之后第 5 年：' + nm[hi] + '已有 ' + n[hi] + ' 州，' + nm[lo] + '只有 ' + n[lo] + ' 州；尚未采纳的州，就是研究用来比较的对照组', '第 5 年：' + nm[hi] + '已有 ' + n[hi] + ' 州，' + nm[lo] + '只有 ' + n[lo] + ' 州', '第 5 年：' + n[hi] + ' 州对 ' + n[lo] + ' 州']; },
      traffic: ['主执法安全带', '强制安全带', '自行车头盔', '酒驾 0.08'],
      trafficLc: ['主执法安全带', '强制安全带', '自行车头盔', '酒驾 0.08'],
      lawLab: function (nm, y) { return [nm + '（' + y + ' 年起）', nm + ' ' + y]; },
      counts: function (a, b) { return '已采纳 ' + a + ' · 尚未采纳 ' + b; },
      clock: [['首个州之后第', '年'], ['第', '年']],
      clkNone: ['尚未采纳（可作对照）', '尚未采纳', '尚未'],
      unobs: ['观察不足 5 年', '不足 5 年'],
      broadT: ['大的规律：新的州政策传得越来越快', '新的州政策传得越来越快'],
      broadS: function (m) { return ['首个州之后 5 年内采纳的州数，每带中位数：从 1900 年前的 ' + m[0] + ' 个到 2000 年后的 ' + m[4] + ' 个', '5 年内采纳的州数（中位）：' + m[0] + ' → ' + m[4], '中位数：' + m[0] + ' → ' + m[4]]; },
      medKey: ['首个州之后 5 年内采纳的州数（中位）', '5 年内采纳州数（中位）'],
      medLab: function (m) { return '5 年内中位 ' + m + ' 州'; },
      endS: ['州 × 政策 × 年份：同一块数据，很多种读法', '同一块数据，很多种读法'],
      bands: ['1900 年前', '1900–49', '1950–79', '1980–99', '2000 年后'],
      key: ['比首个州晚几年', '晚几年'],
      none: '无'
    }
  };

  function classOfD(d) { return d === 0 ? 4 : (d <= 2 ? 3 : (d <= 5 ? 2 : (d <= 9 ? 1 : 0))); }

  NS.sceneC12 = function (D) {
    var lang = D.lang === 'zh' ? 'zh' : 'en', S = STR[lang];
    var C = D.counts, M = D.map;
    if (D.labels) { STR.en.resLab = D.labels; STR.zh.resLab = D.labels; }
    var states = NS.buildMap(D);
    var nS = M.codes.length, CA = M.codes.indexOf('CA');
    var dotsM = NS.unpackDots(M.dotsM);
    var classes = NS.resultClasses(C);
    var nurse = D.policies.filter(function (p) { return p.id === 'nurse_licensure_compact'; })[0];
    var nurseYear = new Int16Array(nS);
    nurse.a.forEach(function (a) { nurseYear[a[0]] = a[1]; });
    var Y0 = nurse.y0, Y1 = nurse.y1;
    var NP = C.papers, N1 = 1304, GRID_FLY = 0.8;                                // seconds a result takes to fly into the grid

    /* simplified state shapes for the small maps of the wall */
    var simp = [], allSimp = new G.Path2D(), allFull = new G.Path2D(), si, r, k, i;
    for (si = 0; si < nS; si++) {
      var p = new G.Path2D(), rings = M.ringsS[si];
      for (r = 0; r < rings.length; r++) {
        var ar = rings[r]; p.moveTo(ar[0], ar[1]);
        for (k = 2; k < ar.length; k += 2) p.lineTo(ar[k], ar[k + 1]);
        p.closePath();
      }
      simp.push(p); allSimp.addPath(p); allFull.addPath(states[si].path);
    }

    /* the tiles of the wall */
    var nT = D.tiles.n, PK = D.tiles.packed, BANDS = D.tiles.bands;
    var tOff = new Int32Array(nT + 1), adS = [], adD = [], bandOf = new Uint8Array(nT), b, acc;
    for (i = 0; i < nT; i++) {
      tOff[i] = adS.length;
      for (k = 1; k < PK[i].length; k += 2) { adS.push(PK[i][k]); adD.push(PK[i][k + 1]); }
    }
    tOff[nT] = adS.length;
    for (i = 0, b = 0, acc = BANDS[0]; i < nT; i++) { while (i >= acc) { b++; acc += BANDS[b]; } bandOf[i] = b; }
    /* the clock of both examples, years after the first state. A policy's states within CLK years of the first count only
       where that window is observed (SPID runs to 2017, the 8 verified policies to 2025); the others are grey. */
    var CLK = D.clock.k, SRC = D.tiles.src, obsT = new Uint8Array(nT), MED = [], within = [[], [], [], [], []], b2, nUnobs = 0;
    for (i = 0; i < nT; i++) {
      obsT[i] = PK[i][0] + CLK <= D.clock.obsEnd[SRC[i]] ? 1 : 0;
      if (!obsT[i]) { nUnobs++; continue; }
      var nw = 0;
      for (k = tOff[i]; k < tOff[i + 1]; k++) if (adD[k] <= CLK) nw++;
      within[bandOf[i]].push(nw);
    }
    for (b2 = 0; b2 < 5; b2++) {
      var wv = within[b2].sort(function (u, v) { return u - v; }), nv = wv.length;
      var md = nv % 2 ? wv[(nv - 1) / 2] : (wv[nv / 2 - 1] + wv[nv / 2]) / 2;
      if (md !== D.clock.bands[b2].median || nv !== D.clock.bands[b2].n) throw new Error('clock medians differ from the data build');
      MED.push(md);
    }
    if (nUnobs !== D.clock.unobserved) throw new Error('unobserved tiles differ from the data build');
    /* the small example: the four laws whose published studies measure traffic deaths; states adopted by each year of the clock
       (SPID maps: the 50 states, no DC) */
    var TRAFFIC = D.traffic.tiles, DC = M.codes.indexOf('DC'), nStates = nS - 1, cumS = [];
    TRAFFIC.forEach(function (ti) {
      var cu = [];
      for (var kk = 0; kk <= CLK; kk++) {
        var n0 = 0;
        for (var z = tOff[ti]; z < tOff[ti + 1]; z++) { if (adS[z] === DC) throw new Error('DC on a traffic map'); if (adD[z] <= kk) n0++; }
        cu.push(n0);
      }
      cumS.push(cu);
    });
    var CUM5 = cumS.map(function (cu) { return cu[CLK]; }), hiQ = 0, loQ = 0;
    for (i = 1; i < TRAFFIC.length; i++) { if (CUM5[i] > CUM5[hiQ]) hiQ = i; if (CUM5[i] < CUM5[loQ]) loQ = i; }
    var OPEN = D.open.tile, OPEN_Y0 = PK[OPEN][0];
    if (OPEN_Y0 !== Y0) throw new Error('the nurse-compact tile and the second act disagree on the first year');


    /* ---------------------------------------------------------- timelines */
    /* Seconds. Acts 1 and 2 are those of the seventh round, tighter (L1 about 4.6 s from the start, L2 about 6.8 s), and end
       with the results leaving into the Estimates chip.
       Then the camera pulls back from the nurse-compact map to the wall (pb0..pb1). The small example lifts four maps out of
       the wall (s0), runs the clock from sc0, one year a step, holds and goes back (s1). The broad example turns the wall to
       the clock (g0), runs it from gc0, shows the medians and turns back to all years (g2..g3). The last frame holds (poster). */
    var T = {
      land0: 0.5, land1: 3.6, ho0: 3.8, ho1: 4.6,
      wave0: 4.8, wave1: 7.6, rise0: 8.2, rise1: 8.9,
      grid0: 9.0, grid1: 10.2, cls0: 10.3, cls1: 10.8, dep0: 11.0, dep1: 11.6, start: 0, seek: 9.5,
      pb0: 11.5, pb1: 13.6, s0: 14.2, sc0: 15.1, step: 0.5, s1: 18.6, g0: 19.5, gc0: 20.2, g2: 24.0, g3: 24.6, poster: 25.6, fade0: 27.0, end: 27.8
    };

    var L = null, th = null, sparks = null, wave = null, gridC = null, styles = null, atlas = null, layers = null;
    var CV = { ox: 0, oy: 0, sc: 1, w: 0, h: 0 };
    var createCanvasQ = null;

    /* ------------------------------------------------------------- layout */
    function init(w, h, theme, opts) {
      th = theme;
      createCanvasQ = (opts && opts.createCanvas) || null;
      var portrait = NS.isPortrait(w, h, opts);
      var pad = portrait ? 14 : 24, railH = portrait ? 56 : 60;
      var railY = h - railH - (portrait ? 8 : 12), top = portrait ? 10 : 16, areaH = railY - top - 10;
      L = { w: w, h: h, portrait: portrait, pad: pad, railY: railY, railH: railH, top: top, areaH: areaH, dpr: Math.min(2, (opts && opts.dpr) || 1) };
      var capH = 28;
      if (portrait) {
        var mw = w - 2 * pad;
        L.mapRect = { x: pad, y: top + capH, w: mw, h: Math.min(areaH * 0.52, mw * 0.64) };
        var py = L.mapRect.y + L.mapRect.h + 10;
        L.panel = { x: pad, y: py, w: w - 2 * pad, h: railY - 8 - py };
      } else {
        var pw = Math.min(196, w * 0.205);
        L.pw = pw;
        L.mapRect = { x: pad + pw + 14, y: top + capH, w: w - 2 * (pad + pw + 14), h: areaH - capH - 18 };
        L.left = { x: pad, y: top + capH, w: pw, h: areaH - capH };
        L.right = { x: w - pad - pw, y: top + capH, w: pw, h: areaH - capH };
      }
      L.map = NS.fitMap(L.mapRect.x, L.mapRect.y, L.mapRect.w, L.mapRect.h);
      L.sc = L.map.sc;
      CV.ox = L.map.ox; CV.oy = L.map.oy; CV.sc = L.map.sc; CV.w = L.map.w; CV.h = L.map.h;
      buildSparks(); buildWave(); buildGrid(); buildChips();
      /* the wall: between a two-line caption and the key above the rail */
      L.keyY = railY - (portrait ? 12 : 14);
      var ay = top + (portrait ? 46 : 48);
      L.area = { x: pad, y: ay, w: w - 2 * pad, h: L.keyY - 18 - ay };
      layoutWall();
      var F = L.tile(OPEN);
      L.focus = F; L.K0 = L.map.sc / F.s; L.mapC = { x: L.map.ox + L.map.w / 2, y: L.map.oy + L.map.h / 2 };
      layoutSmall();
      prepStyles();
      buildAtlases();
    }

    function buildSparks() {
      var rg = NS.rng(424242), q, sc = L.sc, m = L.map;
      var order = []; for (q = 0; q < N1; q++) order.push(q);
      NS.shuffle(order, rg);
      var kept = C.gates.map(function (g) { return Math.round(N1 * g / C.texts); });
      var sp = { x: new Float32Array(N1), y: new Float32Array(N1), t: new Float32Array(N1), fh: new Float32Array(N1), life: new Float32Array(N1), surv: new Uint8Array(N1), fall: new Float32Array(N1) };
      var survIdx = [];
      for (q = 0; q < N1; q++) {
        var kk = Math.floor(rg() * dotsM.n);
        sp.x[q] = m.ox + (dotsM.x[kk] + (rg() - 0.5) * 9) * sc;
        sp.y[q] = m.oy + (dotsM.y[kk] + (rg() - 0.5) * 9) * sc;
        sp.t[q] = T.land0 + (T.land1 - T.land0) * Math.pow(rg(), 0.95);
        sp.fh[q] = (60 + rg() * 120) * (L.portrait ? 0.8 : 1);
        sp.fall[q] = 0.34 + rg() * 0.3;
        sp.life[q] = 0.5 + rg() * 1.1;
      }
      for (q = 0; q < kept[4]; q++) { sp.surv[order[q]] = 1; survIdx.push(order[q]); }
      survIdx.sort(function (a, b2) { return sp.t[a] - sp.t[b2]; });
      sparks = sp; sparks.survIdx = survIdx; sparks.chosen = survIdx[Math.floor(survIdx.length / 2)];
    }

    function buildWave() {
      var flip = new Float32Array(nS), rr = NS.rng(11), q;
      for (q = 0; q < nS; q++) {
        var y = nurseYear[q];
        flip[q] = y ? T.wave0 + ((y - Y0) / (Y1 - Y0 + 1)) * (T.wave1 - T.wave0) + rr() * 0.18 : 1e9;
      }
      wave = { flip: flip };
      stairsArr = null;
    }

    function buildGrid() {
      var R = L.portrait ? L.panel : L.right;
      var cols = 9, rows = Math.ceil(NP / cols), gap = L.portrait ? 3 : 6, cell = Math.max(8, Math.min(L.portrait ? 11 : 16, Math.floor((R.w - 8 * gap - 8) / cols)));
      var gw = cols * cell + (cols - 1) * gap, gh = rows * cell + (rows - 1) * gap;
      var gx0, gy0, laneY, laneX0, laneX1;
      if (L.portrait) {
        gx0 = R.x + R.w - gw - 6; gy0 = R.y + 52;
        laneX0 = R.x + R.w - 170; laneX1 = R.x + R.w - 4; laneY = R.y + 26;
      } else {
        gx0 = R.x + (R.w - gw) / 2; gy0 = R.y + 86;
        laneX0 = R.x + 6; laneX1 = R.x + R.w - 6; laneY = R.y + 36;
      }
      gridC = { cols: cols, rows: rows, cell: cell, gap: gap, gx0: gx0, gy0: gy0, gw: gw, gh: gh, laneY: laneY, laneX0: laneX0, laneX1: laneX1, mu: laneX0 + (laneX1 - laneX0) * 0.56, whisk: (laneX1 - laneX0) * 0.27,
        x: new Float32Array(NP), y: new Float32Array(NP), dly: new Float32Array(NP), src: new Int16Array(NP), arc: new Float32Array(NP) };
      var rg = NS.rng(5150), q;
      for (q = 0; q < NP; q++) {
        gridC.x[q] = gx0 + (q % cols) * (cell + gap) + cell / 2;
        gridC.y[q] = gy0 + Math.floor(q / cols) * (cell + gap) + cell / 2;
        gridC.dly[q] = q * (0.9 / NP) + rg() * 0.12;
        gridC.src[q] = Math.floor(rg() * nS);
        gridC.arc[q] = 20 + rg() * 40;
      }
    }

    function buildChips() {
      var gap = L.portrait ? 6 : 14, cw = (L.w - 2 * L.pad - 2 * gap) / 3;
      L.chips = [];
      for (var q = 0; q < 3; q++) L.chips.push({ x: L.pad + q * (cw + gap), y: L.railY, w: cw, h: L.railH });
      var c2 = L.chips[1];
      L.compact = L.portrait || c2.w < 250;
      gridC.tx = new Float32Array(NP); gridC.ty = new Float32Array(NP);
      if (L.compact) {
        for (var k2 = 0; k2 < NP; k2++) { gridC.tx[k2] = c2.x + (k2 + 0.5) / NP * c2.w; gridC.ty[k2] = c2.y + 49; }
      } else {
        var px = c2.x + c2.w - 18 * 7 - 12, py = c2.y + 16;
        for (var k3 = 0; k3 < NP; k3++) { gridC.tx[k3] = px + (k3 % 18) * 7 + 3; gridC.ty[k3] = py + Math.floor(k3 / 18) * 8 + 3; }
      }
    }

    /* the wall: five bands by the year the first state adopted, one small map per policy */
    function layoutWall() {
      var A = L.area, portrait = L.portrait;
      var labW = portrait ? 0 : Math.min(96, A.w * 0.12), gap = portrait ? 14 : 7, asp = 0.66, best = null, Cc, bb;
      for (Cc = 10; Cc <= 90; Cc++) {
        var rows = 0; for (bb = 0; bb < 5; bb++) rows += Math.ceil(BANDS[bb] / Cc);
        var extra = portrait ? 5 * gap : 4 * gap;
        var tw = Math.min((A.w - labW) / Cc, (A.h - extra) / (rows * asp));
        if (!best || tw > best.tw + 1e-6) best = { C: Cc, rows: rows, tw: tw };
      }
      var tw2 = best.tw, th2 = tw2 * asp; Cc = best.C;
      var totalW = labW + Cc * tw2, totalH = best.rows * th2 + (portrait ? 5 * gap : 4 * gap);
      var x0 = A.x + (A.w - totalW) / 2 + labW, y = A.y + (A.h - totalH) / 2;
      var bandBox = [], start = 0;
      for (bb = 0; bb < 5; bb++) {
        if (portrait) y += gap;
        var rowsB = Math.ceil(BANDS[bb] / Cc);
        bandBox.push({ x: x0, y: y, w: Cc * tw2, h: rowsB * th2, start: start, n: BANDS[bb] });
        y += rowsB * th2 + (portrait ? 0 : gap);
        start += BANDS[bb];
      }
      L.C = Cc; L.tw = tw2; L.th = th2; L.labW = labW; L.gap = gap; L.bandBox = bandBox;
      var first = bandBox[0], last = bandBox[4], top0 = portrait ? first.y - gap : first.y;
      L.wall = { x: Math.floor(x0 - 2), y: Math.floor(top0 - 2), w: Math.ceil(Cc * tw2 + 4), h: Math.ceil(last.y + last.h - top0 + 4) };
      var cache = {};
      L.tile = function (ti) {
        if (cache[ti]) return cache[ti];
        var bx = bandBox[bandOf[ti]], j = ti - bx.start, col = j % Cc, row = Math.floor(j / Cc);
        var cx0 = bx.x + col * tw2, cy0 = bx.y + row * th2, inset = Math.max(0.6, tw2 * 0.05);
        var s = Math.min((tw2 - 2 * inset) / 975, (th2 - 2 * inset) / 610);
        var o = { x: cx0, y: cy0, w: tw2, h: th2, s: s, mx: cx0 + (tw2 - 975 * s) / 2, my: cy0 + (th2 - 610 * s) / 2 };
        cache[ti] = o; return o;
      };
    }

    function classRGB(q) { var c = th; return [c.b1, c.b2, c.b3, c.b4, c.b5][q]; }
    function prepStyles() { styles = { land: NS.rgba(th.land, 1), cls: [0, 1, 2, 3, 4].map(function (q) { return NS.rgba(classRGB(q), 1); }) }; }

    /* The finished wall as one bitmap, drawn once per size and theme at 2x the screen density (the camera lands from about 3x;
       closer than that the tiles on screen are drawn as vectors). For the broad example, the wall on the clock as layers at the
       screen density: land and the first states (layer 0), then the states of each later year (layers 1 to CLK). */
    function buildAtlases() {
      atlas = null; layers = null;
      if (!createCanvasQ) return;
      var W = L.wall, res = L.res = Math.min(4, L.dpr * 2), ti, z, d, o;
      atlas = createCanvasQ(Math.max(1, Math.round(W.w * res)), Math.max(1, Math.round(W.h * res)));
      var c1 = atlas.getContext('2d');
      for (ti = 0; ti < nT; ti++) {
        o = L.tile(ti);
        c1.setTransform(res * o.s, 0, 0, res * o.s, res * (o.mx - W.x), res * (o.my - W.y));
        paintTile(c1, ti);
      }
      c1.setTransform(1, 0, 0, 1, 0, 0);
      var lr = L.dpr, cs = [], veil = NS.rgba(veilRGB(), 1);
      layers = [];
      for (d = 0; d <= CLK; d++) {
        layers.push(createCanvasQ(Math.max(1, Math.round(W.w * lr)), Math.max(1, Math.round(W.h * lr))));
        cs.push(layers[d].getContext('2d'));
      }
      for (ti = 0; ti < nT; ti++) {
        o = L.tile(ti);
        var a0 = lr * o.s, e0 = lr * (o.mx - W.x), f0 = lr * (o.my - W.y);
        cs[0].setTransform(a0, 0, 0, a0, e0, f0);
        cs[0].fillStyle = obsT[ti] ? styles.land : veil; cs[0].fill(allSimp);
        if (!obsT[ti]) continue;
        for (z = tOff[ti]; z < tOff[ti + 1]; z++) {
          d = adD[z];
          if (d > CLK) continue;
          cs[d].setTransform(a0, 0, 0, a0, e0, f0); cs[d].fillStyle = styles.cls[classOfD(d)]; cs[d].fill(simp[adS[z]]);
        }
      }
      for (d = 0; d <= CLK; d++) cs[d].setTransform(1, 0, 0, 1, 0, 0);
    }
    function veilRGB() { return NS.mix(th.land, th.bg, 0.6); }
    /* one tile in map coordinates (the caller sets the transform) */
    function paintTile(c, ti) {
      c.fillStyle = styles.land; c.fill(allSimp);
      for (var q = tOff[ti]; q < tOff[ti + 1]; q++) { c.fillStyle = styles.cls[classOfD(adD[q])]; c.fill(simp[adS[q]]); }
    }

    /* ------------------------------------------------------------- helpers */
    var tmp = [0, 0];
    function quad(x0, y0, qx, qy, x1, y1, t, out) {
      var u = 1 - t;
      out[0] = u * u * x0 + 2 * u * t * qx + t * t * x1;
      out[1] = u * u * y0 + 2 * u * t * qy + t * t * y1;
    }
    function tintFor(y, c) { return y <= 2019 ? c.b1 : (y <= 2023 ? c.b2 : c.b3); }
    var batch = new NS.DotBatch();
    function cx(q) { return CV.ox + states[q].cx * CV.sc; }
    function cy(q) { return CV.oy + states[q].cy * CV.sc; }
    function nurseIdx(code) { return M.codes.indexOf(code); }
    function win(t, a, b2, e) { return smooth((t - a) / e) * (1 - smooth((t - b2) / e)); }
    function fit(ctx, options, maxW, size, weight) {
      for (var q = 0; q < options.length; q++) if (NS.textWidth(ctx, options[q], size, weight) <= maxW) return options[q];
      return options[options.length - 1];
    }
    function fitWord(ctx, options, x0, size, weight, keepLast) {   // first wording that fits between x0 and the right margin
      for (var q = 0; q < options.length; q++) if (x0 + NS.textWidth(ctx, options[q], size || 12, weight || 400) <= L.w - L.pad + 2) return options[q];
      return keepLast ? options[options.length - 1] : '';
    }

    /* camera: world = the final layout. Until pb0 it shows the nurse-compact tile at the size of the large map, exactly where
       the first two acts drew it; then it pulls back to the wall in one move, the focus travelling with the zoom. */
    var cam = { k: 1, tx: 0, ty: 0 };
    function updateCamera(t) {
      var e = easeInOut(ramp(t, T.pb0, T.pb1)), F = L.focus, kk = Math.exp(lerp(Math.log(L.K0), 0, e));
      var u = (L.K0 - kk) / (L.K0 - 1);
      var fcx = F.mx + 975 * F.s / 2, fcy = F.my + 610 * F.s / 2;
      var px = lerp(L.mapC.x, fcx, u), py = lerp(L.mapC.y, fcy, u);
      cam.k = kk; cam.tx = px - fcx * kk; cam.ty = py - fcy * kk;
      CV.sc = kk * F.s; CV.ox = F.mx * kk + cam.tx; CV.oy = F.my * kk + cam.ty; CV.w = 975 * CV.sc; CV.h = 610 * CV.sc;
    }

    /* ------------------------------------------------------------- the map */
    /* The nurse-compact map of the first two acts. As the camera pulls back its colours change to the wall's key
       (years after the first state), so on landing it is the same tile as in the wall. */
    function drawMap(ctx, t) {
      var c = th, m = CV, q;
      var mapA = 1 - smooth((t - (T.pb1 - 0.25)) / 0.4);              // the wall's bitmap holds this tile from here
      if (mapA <= 0.003) return;
      var pulseEnd = T.pb0 - 0.3;
      var morph = smooth((t - (T.pb0 + 0.1)) / 0.9);
      ctx.save();
      ctx.globalAlpha *= mapA;
      ctx.translate(m.ox, m.oy); ctx.scale(m.sc, m.sc);
      var breath = Math.sin(t * TAU / 1.5);
      for (q = 0; q < nS; q++) {
        var colr = c.land;
        var fl = wave.flip[q];
        if (t >= fl - 0.05) {
          var f = smooth((t - fl) / 0.7);
          var amt = 0.62 * f;
          if (t > T.wave1 + 0.2 && t < pulseEnd) amt += 0.07 * breath * f;
          colr = NS.mix(colr, tintFor(nurseYear[q], c), clamp01(amt));
        }
        if (morph > 0) colr = NS.mix(colr, nurseYear[q] ? classRGB(classOfD(nurseYear[q] - Y0)) : c.land, morph);
        ctx.fillStyle = NS.rgba(colr, 1);
        ctx.fill(states[q].path);
      }
      if (m.sc > 0.12) {
        ctx.lineWidth = 0.9 / m.sc; ctx.strokeStyle = NS.rgba(c.hair, 0.95 * (1 - morph));
        for (q = 0; q < nS; q++) ctx.stroke(states[q].path);
      }
      if (t > T.wave1 + 0.2 && t < pulseEnd) {
        var pa = smooth((t - T.wave1) / 0.6) * (1 - smooth((t - (pulseEnd - 0.6)) / 0.6));
        ctx.lineWidth = (1.0 + 0.9 * (0.5 - 0.5 * breath)) / m.sc;
        ctx.strokeStyle = NS.rgba(c.slate, 0.75 * pa);
        for (q = 0; q < nS; q++) if (t < wave.flip[q] || !nurseYear[q]) ctx.stroke(states[q].path);
      }
      ctx.restore();
    }

    function drawWaveFx(ctx, t) {
      if (t < T.wave0 - 0.1 || t > T.wave1 + 1.4) return;
      var c = th, q;
      for (q = 0; q < nS; q++) {
        var dt = t - wave.flip[q];
        if (dt < 0 || dt > 1.1) continue;
        var pp = dt / 1.1, e = easeOut(pp);
        ctx.save();
        ctx.translate(cx(q), cy(q)); ctx.scale(CV.sc * (1 + 0.28 * e), CV.sc * (1 + 0.28 * e));
        ctx.translate(-states[q].cx, -states[q].cy);
        ctx.lineWidth = 1.5 / (CV.sc * (1 + 0.28 * e)); ctx.strokeStyle = NS.rgba(c.amber, 0.85 * (1 - pp));
        ctx.stroke(states[q].path);
        ctx.restore();
      }
    }

    /* ---------------------------------------------------------- Act 1 sparks */
    function drawSparks(ctx, t) {
      if (t > T.ho1 + 0.8) return;
      var c = th, sp = sparks, q, kk;
      var slate = c.slate, accn = c.accent;
      var fadeAll = 1 - smooth((t - (T.ho0 - 0.2)) / 1.2);
      ctx.lineWidth = 1; ctx.lineCap = 'round';
      var lines = [];
      for (q = 0; q < N1; q++) {
        var tl = sp.t[q], fall = sp.fall[q], tt = t - (tl - fall);
        if (tt < 0) continue;
        if (tt < fall) {
          var pp = tt / fall, e = pp * pp;
          var y = sp.y[q] - sp.fh[q] * (1 - e), x = sp.x[q];
          var p2 = Math.max(0, pp - 0.08), y2 = sp.y[q] - sp.fh[q] * (1 - p2 * p2);
          lines.push(x, y2, y, NS.rgbaQ(slate, 0.55 * fadeAll));
          batch.add(NS.rgbaQ(slate, 0.8 * fadeAll), x, y, L.portrait ? 1.1 : 1.4);
        } else {
          var age = t - tl;
          if (sp.surv[q]) continue;
          var a = Math.exp(-age / sp.life[q]) * 0.8 * fadeAll;
          if (a > 0.02) batch.add(NS.rgbaQ(slate, a), sp.x[q], sp.y[q], L.portrait ? 1.2 : 1.5);
        }
      }
      batch.flush(ctx);
      var groups = {};
      for (kk = 0; kk < lines.length; kk += 4) { var key = lines[kk + 3]; (groups[key] = groups[key] || []).push(lines[kk], lines[kk + 1], lines[kk + 2]); }
      for (var key2 in groups) {
        var arr = groups[key2]; ctx.strokeStyle = key2; ctx.beginPath();
        for (kk = 0; kk < arr.length; kk += 3) { ctx.moveTo(arr[kk], arr[kk + 1]); ctx.lineTo(arr[kk], arr[kk + 2]); }
        ctx.stroke();
      }
      ctx.lineWidth = 1; ctx.beginPath();
      var any = false;
      for (q = 0; q < N1; q++) {
        var ag = t - sp.t[q];
        if (ag < 0 || ag > 0.45 || sp.surv[q]) continue;
        any = true;
        ctx.moveTo(sp.x[q] + 2 + 7 * ag / 0.45, sp.y[q]);
        ctx.arc(sp.x[q], sp.y[q], 2 + 7 * ag / 0.45, 0, TAU);
      }
      if (any) { ctx.strokeStyle = NS.rgba(slate, 0.3 * fadeAll); ctx.stroke(); }
      for (kk = 0; kk < sp.survIdx.length; kk++) {
        var s = sp.survIdx[kk];
        var tl2 = sp.t[s]; if (t < tl2) continue;
        var age2 = t - tl2, x2 = sp.x[s], y2b = sp.y[s];
        var sr = L.portrait ? 2.2 : 2.8, alpha = 1;
        if (t >= T.ho0) {
          var hp = easeInOut(ramp(t, T.ho0 + kk * 0.03, T.ho1 - 0.1 + kk * 0.01));
          if (s === sp.chosen) {
            var rp = ramp(t, T.ho0, T.ho1 + 0.3);
            ctx.strokeStyle = NS.rgba(accn, 0.4 * (1 - rp)); ctx.lineWidth = 1.4; ctx.beginPath();
            ctx.arc(x2, y2b, 6 + (L.map.w * 0.42) * easeOut(rp), 0, TAU); ctx.stroke();
            alpha = 1 - smooth((t - (T.ho1 + 0.1)) / 0.5);
          } else {
            var tx = L.chips[0].x + 26, ty = L.chips[0].y + 20;
            quad(x2, y2b, (x2 + tx) / 2, Math.max(y2b, ty) + 30, tx, ty, hp, tmp);
            x2 = tmp[0]; y2b = tmp[1]; alpha = 1 - smooth((hp - 0.75) / 0.25); sr *= 1 - 0.3 * hp;
          }
        }
        if (alpha > 0.01) {
          ctx.fillStyle = NS.rgba(accn, alpha * (0.5 + 0.5 * smooth(age2 / 0.3))); ctx.beginPath(); ctx.arc(x2, y2b, sr, 0, TAU); ctx.fill();
          if (t < T.ho0) {
            var ringA = (0.45 + 0.2 * Math.sin(t * 3 + kk)) * smooth(age2 / 0.4);
            ctx.strokeStyle = NS.rgba(accn, ringA); ctx.lineWidth = 1; ctx.beginPath(); ctx.arc(x2, y2b, 7, 0, TAU); ctx.stroke();
          }
        }
      }
    }

    /* ------------------------------------------------------ side panels, L1 */
    function drawPanelsL1(ctx, t) {
      var c = th;
      var a = smooth((t - 0.3) / 0.6) * (1 - smooth((t - (T.ho1 - 0.7)) / 0.6));
      if (a <= 0.01) return;
      var big = L.portrait ? 21 : 26;
      var lx, ly, rx, ry;
      var f = ramp(t, T.land0, T.land1 + 0.4);
      var ent = Math.round(C.texts * smooth(f));
      var arrived = 0, kk;
      for (kk = 0; kk < sparks.survIdx.length; kk++) if (t >= sparks.t[sparks.survIdx[kk]]) arrived++;
      var cand = Math.round(C.cands * arrived / sparks.survIdx.length);
      var dotTxt = S.dot(fmt(C.texts / N1));
      if (L.portrait) {
        lx = L.pad + 2; ly = L.mapRect.y + L.mapRect.h + 34;
        NS.tabular(ctx, fmt(ent), lx, ly, { size: big, weight: 500, color: c.ink, alpha: a });
        NS.text(ctx, S.texts, lx, ly + 18, { size: 12, color: c.muted, alpha: a });
        NS.text(ctx, dotTxt, lx, ly + 34, { size: 12, color: c.muted, alpha: a * 0.85 });
        rx = L.w - L.pad - 2; ry = ly;
        NS.tabular(ctx, fmt(cand), rx, ry, { size: big, weight: 600, color: c.accent, alpha: a, align: 'right' });
        NS.text(ctx, S.cands, rx, ry + 18, { size: 12, color: c.muted, alpha: a, align: 'right' });
        NS.text(ctx, S.rules[S.rules.length - 1], rx, ry + 34, { size: 12, color: c.muted, alpha: a * 0.85, align: 'right' });
      } else {
        lx = L.left.x + 2; ly = L.left.y + 36;
        NS.tabular(ctx, fmt(ent), lx, ly, { size: big, weight: 500, color: c.ink, alpha: a });
        NS.text(ctx, S.texts, lx, ly + 20, { size: 12, color: c.muted, alpha: a });
        NS.text(ctx, dotTxt, lx, ly + 36, { size: 12, color: c.muted, alpha: a * 0.85 });
        rx = L.right.x + L.right.w - 2; ry = ly;
        NS.tabular(ctx, fmt(cand), rx, ry, { size: big, weight: 600, color: c.accent, alpha: a, align: 'right' });
        NS.text(ctx, S.cands, rx, ry + 20, { size: 12, color: c.muted, alpha: a, align: 'right' });
        NS.text(ctx, S.rules[0], rx, ry + 36, { size: 12, color: c.muted, alpha: a * 0.85, align: 'right' });
      }
    }

    /* ------------------------------------------------ L2: chart, year, legend */
    var STEPS = 72, stairsArr = null;
    function stairsAt(kk) {
      if (!stairsArr) {
        stairsArr = [];
        for (var q = 0; q <= STEPS; q++) {
          var tt = T.wave0 + (q / STEPS) * (T.wave1 - T.wave0), n = 0;
          for (var z = 0; z < nS; z++) if (wave.flip[z] <= tt) n++;
          stairsArr.push(n);
        }
      }
      return stairsArr[kk];
    }
    function drawStairs(ctx, t) {
      var endT = T.pb0 + 0.3;
      if (t < T.wave0 - 0.3 || t > endT + 0.2) return;
      var c = th;
      var a = smooth((t - (T.wave0 - 0.25)) / 0.5) * (1 - smooth((t - (endT - 0.4)) / 0.6));
      if (a <= 0.01) return;
      var P = L.portrait ? { x: L.panel.x, y: L.panel.y, w: Math.min(150, L.panel.w * 0.42), h: L.panel.h } : L.left;
      var x0 = P.x + 6, x1 = P.x + P.w - 6;
      var yTop = P.y + (L.portrait ? 40 : 96), yBase = P.y + (L.portrait ? P.h - 30 : P.h * 0.60);
      var nmax = nurse.n, kk, px, py;
      NS.text(ctx, S.treatedT, P.x + 2, P.y + (L.portrait ? 14 : 18), { size: 12, weight: 500, color: c.muted, alpha: a });
      if (!L.portrait) {
        ctx.fillStyle = NS.rgba(c.b2, a); ctx.fillRect(P.x + 2, P.y + 34, 12, 12);
        NS.text(ctx, S.treated, P.x + 22, P.y + 44, { size: 12, color: c.text, alpha: a });
        ctx.strokeStyle = NS.rgba(c.slate, a); ctx.lineWidth = 1.4; ctx.strokeRect(P.x + 2.5, P.y + 54.5, 11, 11);
        NS.text(ctx, S.notYet, P.x + 22, P.y + 64, { size: 12, color: c.text, alpha: a });
      }
      ctx.strokeStyle = NS.rgba(c.hair, a); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(x0, yBase + 0.5); ctx.lineTo(x1, yBase + 0.5); ctx.stroke();
      ctx.save(); ctx.setLineDash([2, 3]); ctx.beginPath(); ctx.moveTo(x0, yTop + 0.5); ctx.lineTo(x1, yTop + 0.5); ctx.stroke(); ctx.restore();
      NS.text(ctx, String(nmax), x0, yTop - 5, { size: 12, color: c.muted, alpha: a });
      NS.text(ctx, String(Y0), x0, yBase + 17, { size: 12, color: c.muted, alpha: a });
      NS.text(ctx, String(Y1), x1, yBase + 17, { size: 12, color: c.muted, alpha: a, align: 'right' });
      var prog = clamp01((t - T.wave0) / (T.wave1 - T.wave0));
      var last = Math.floor(prog * STEPS);
      var fr = prog * STEPS - last, nowCount = stairsAt(Math.min(STEPS, last + 1));
      var hx = x0 + prog * (x1 - x0), hy = yBase - (lerp(stairsAt(last), nowCount, fr) / nmax) * (yBase - yTop);
      ctx.beginPath(); ctx.moveTo(x0, yBase);
      for (kk = 0; kk <= last; kk++) { px = x0 + (kk / STEPS) * (x1 - x0); py = yBase - (stairsAt(kk) / nmax) * (yBase - yTop); ctx.lineTo(px, py); }
      ctx.lineTo(hx, hy); ctx.lineTo(hx, yBase); ctx.closePath();
      ctx.fillStyle = NS.rgba(c.accent, 0.12 * a); ctx.fill();
      ctx.beginPath(); ctx.moveTo(x0, yBase);
      for (kk = 0; kk <= last; kk++) { px = x0 + (kk / STEPS) * (x1 - x0); py = yBase - (stairsAt(kk) / nmax) * (yBase - yTop); ctx.lineTo(px, py); }
      ctx.lineTo(hx, hy);
      ctx.strokeStyle = NS.rgba(c.accent, a); ctx.lineWidth = 1.8; ctx.stroke();
      ctx.fillStyle = NS.rgba(c.accent, a); ctx.beginPath(); ctx.arc(hx, hy, 3.4, 0, TAU); ctx.fill();
    }

    function drawYear(ctx, t) {
      if (t < T.wave0 - 0.3 || t > T.rise1 + 0.2) return;
      var c = th, m = CV;
      var a = smooth((t - (T.wave0 - 0.3)) / 0.5) * (1 - smooth((t - (T.rise0 + 0.3)) / 0.6));
      if (a <= 0.01) return;
      var wf = clamp01((t - T.wave0) / (T.wave1 - T.wave0));
      var year = Math.min(Y1, Y0 + Math.floor(wf * (Y1 - Y0 + 1)));
      var tr = 0, q; for (q = 0; q < nS; q++) if (t >= wave.flip[q]) tr++;
      if (L.portrait) {
        var x = m.ox + 975 * m.sc, y = m.oy + 556 * m.sc;
        NS.tabular(ctx, String(year), x, y, { size: 24, weight: 300, color: c.ink, alpha: a, align: 'right' });
        NS.text(ctx, S.ofStates(tr, nurse.n), x, y + 18, { size: 12, color: c.muted, alpha: a, align: 'right' });
      } else {
        var x2 = m.ox + m.w / 2, y2 = m.oy + m.h + 44;
        NS.tabular(ctx, String(year), x2 - 8, y2, { size: 34, weight: 300, color: c.ink, alpha: a, align: 'right' });
        NS.text(ctx, S.ofStates(tr, nurse.n), x2 + 4, y2 - 4, { size: 12, color: c.muted, alpha: a });
      }
    }

    /* ------------------------------------------- estimate stem, lane and grid */
    function drawEstimate(ctx, t) {
      var endT = T.dep1 + 0.5;
      if (t < T.rise0 - 0.2 || t > endT) return;
      var c = th, g = gridC, kk;
      var sx = cx(nurseIdx('ME')), sy = cy(nurseIdx('ME'));
      var rp = ramp(t, T.rise0, T.rise1);
      var ex = g.mu, ey = g.laneY;
      if (rp > 0 && rp < 1.0) {
        ctx.strokeStyle = NS.rgba(c.accent, 0.35 * (1 - smooth((t - T.rise1) / 0.5))); ctx.lineWidth = 1.2; ctx.beginPath();
        var steps = 24, q;
        for (q = 0; q <= steps * easeInOut(rp); q++) {
          quad(sx, sy, (sx + ex) / 2, Math.min(sy, ey) - 50, ex, ey, q / steps, tmp);
          if (q) ctx.lineTo(tmp[0], tmp[1]); else ctx.moveTo(tmp[0], tmp[1]);
        }
        ctx.stroke();
        quad(sx, sy, (sx + ex) / 2, Math.min(sy, ey) - 50, ex, ey, easeInOut(rp), tmp);
        ctx.fillStyle = NS.rgba(c.accent, 0.95); ctx.beginPath(); ctx.arc(tmp[0], tmp[1], 3.4, 0, TAU); ctx.fill();
      }
      var laneA = smooth((t - (T.rise1 - 0.6)) / 0.5) * (1 - smooth((t - (T.grid0 + 0.9)) / 0.5));
      if (laneA > 0.01) {
        ctx.strokeStyle = NS.rgba(c.faint, 0.6 * laneA); ctx.lineWidth = 1; ctx.beginPath(); ctx.moveTo(g.laneX0, g.laneY); ctx.lineTo(g.laneX1, g.laneY); ctx.stroke();
        var half = g.whisk * easeOut(ramp(t, T.rise1 - 0.4, T.rise1 + 0.5));
        ctx.strokeStyle = NS.rgba(c.ink, 0.85 * laneA); ctx.lineWidth = 1.6; ctx.beginPath();
        ctx.moveTo(g.mu - half, g.laneY); ctx.lineTo(g.mu + half, g.laneY);
        ctx.moveTo(g.mu - half, g.laneY - 6); ctx.lineTo(g.mu - half, g.laneY + 6);
        ctx.moveTo(g.mu + half, g.laneY - 6); ctx.lineTo(g.mu + half, g.laneY + 6);
        ctx.stroke();
        ctx.fillStyle = NS.rgba(c.accent, laneA); ctx.beginPath(); ctx.arc(g.mu, g.laneY, (L.portrait ? 5.5 : 7) * easeOutBack(ramp(t, T.rise1 - 0.6, T.rise1)), 0, TAU); ctx.fill();
        NS.text(ctx, S.estLab, g.laneX0, g.laneY - 16, { size: 12, weight: 500, color: c.muted, alpha: laneA });
      }
      var cellR = g.cell / 2 - 1;
      var depP = ramp(t, T.dep0, T.dep1);
      var legendA = smooth((t - (T.cls0 + 0.2)) / 0.6) * (1 - smooth((t - T.dep0) / 0.5));
      for (kk = 0; kk < NP; kk++) {
        var t0 = T.grid0 + g.dly[kk];
        var p = ramp(t, t0, t0 + GRID_FLY);
        if (p <= 0) continue;
        var s = g.src[kk];
        quad(cx(s), cy(s), (cx(s) + g.x[kk]) / 2, Math.min(cy(s), g.y[kk]) - g.arc[kk] - 20, g.x[kk], g.y[kk], easeInOut(p), tmp);
        var x = tmp[0], y = tmp[1];
        var rr = p < 0.7 ? 2.6 : lerp(2.6, cellR, easeOut((p - 0.7) / 0.3));
        if (p >= 1) rr = cellR * (1 + 0.14 * Math.sin(Math.min(1, ramp(t, t0 + GRID_FLY, t0 + GRID_FLY + 0.3)) * Math.PI));
        var cl = classes[kk];
        var cp = smooth((t - (T.cls0 + kk / NP * (T.cls1 - T.cls0))) / 0.35);
        var colr = NS.mix(c.accent, NS.resultColor(c, cl), cp);
        var ringOn = false;
        var ringA = 0.8;
        var alpha = 0.98 * smooth(p * 3);
        if (depP > 0) {
          var dk = easeInOut(ramp(t, T.dep0 + kk * 0.008, T.dep1 + kk * 0.008 - 0.3));
          quad(x, y, (x + g.tx[kk]) / 2, Math.max(y, g.ty[kk]) - 24, g.tx[kk], g.ty[kk], dk, tmp);
          x = tmp[0]; y = tmp[1]; rr = lerp(rr, L.portrait ? 1.4 : 2.7, dk);
          ringA = 0.8 * (1 - depP / 0.4);
        }
        if (depP >= 1 && t > T.dep1 + 0.1) alpha = 0;
        if (depP >= 0.4) ringOn = false;
        if (alpha <= 0.01) continue;
        ctx.fillStyle = NS.rgba(colr, alpha); ctx.beginPath(); NS.marker(ctx, cp > 0.5 ? cl : 0, x, y, rr); ctx.fill();
        if (ringOn) {
          ctx.strokeStyle = NS.rgba(c.ink, ringA); ctx.lineWidth = 1.2; ctx.beginPath(); ctx.arc(x, y, rr + 3.4, 0, TAU); ctx.stroke();
        }
      }
      if (legendA > 0.01) {
        var lx, ly, lh = L.portrait ? 0 : 20;
        var items = [C.match, C.dir, C.nc, C.os];
        if (L.portrait) {
          lx = g.gx0 - 4; ly = g.gy0 + g.gh + 16; var xx = lx;
          for (kk = 0; kk < 4; kk++) {
            ctx.fillStyle = NS.rgba(NS.resultColor(c, kk), legendA); ctx.beginPath(); NS.marker(ctx, kk, xx + 4, ly - 4, 3.6); ctx.fill();
            NS.tabular(ctx, String(items[kk]), xx + 12, ly, { size: 12, weight: 600, color: c.ink, alpha: legendA });
            xx += 36;
          }
        } else {
          lx = g.gx0 - 6; ly = g.gy0 + g.gh + 28;
          for (kk = 0; kk < 4; kk++) {
            ctx.fillStyle = NS.rgba(NS.resultColor(c, kk), legendA); ctx.beginPath(); NS.marker(ctx, kk, lx + 5, ly + kk * lh - 4, 4.2); ctx.fill();
            NS.tabular(ctx, String(items[kk]), lx + 16, ly + kk * lh, { size: 13, weight: 600, color: c.ink, alpha: legendA });
            NS.text(ctx, S.resLab[kk], lx + 38, ly + kk * lh, { size: 12, color: c.text, alpha: legendA });
          }
        }
      }
    }

    /* ============================================================ L3: the wall */
    var VECTOR_K = 3.2;                                              // closer than this, the tiles on screen are drawn as vectors
    /* one tile taken from the wall's bitmap and drawn at any place and size */
    function tileImg(ctx, ti, x, y, w, h) {
      var W = L.wall, o = L.tile(ti), res = L.res;
      ctx.drawImage(atlas, (o.x - W.x) * res, (o.y - W.y) * res, o.w * res, o.h * res, x, y, w, h);
    }
    function drawWall(ctx, t) {
      if (t < T.pb0) return;
      var a = t >= T.pb1 ? 1 : smooth((L.K0 - cam.k) / (L.K0 - 6)); // the neighbours come in as soon as the camera starts back
      if (a <= 0.003) return;
      var W = L.wall, kk = cam.k;
      ctx.save(); ctx.translate(cam.tx, cam.ty); ctx.scale(kk, kk);
      ctx.globalAlpha *= a;
      if (kk <= VECTOR_K && atlas) ctx.drawImage(atlas, W.x, W.y, W.w, W.h);
      else {
        var x0 = -cam.tx / kk, x1 = (L.w - cam.tx) / kk, y0 = -cam.ty / kk, y1 = (L.h - cam.ty) / kk;
        for (var ti = 0; ti < nT; ti++) {
          if (ti === OPEN) continue;                                   // drawn by drawMap
          var o = L.tile(ti);
          if (o.x + o.w < x0 || o.x > x1 || o.y + o.h < y0 || o.y > y1) continue;
          ctx.save(); ctx.translate(o.mx, o.my); ctx.scale(o.s, o.s); paintTile(ctx, ti); ctx.restore();
        }
      }
      ctx.restore();
      drawClockWall(ctx, t);
    }

    /* the pull-back reaches beyond the plot: the caption and rail margins stay clean */
    function drawMask(ctx, t) {
      if (t < T.pb0) return;
      var c = th, A = L.area;
      ctx.fillStyle = NS.rgba(c.bg, smooth((t - (T.pb0 + 0.15)) / 0.8));   // fades in while the large map shrinks
      ctx.fillRect(0, 0, L.w, A.y - 4);
      ctx.fillRect(0, A.y + A.h + 4, L.w, L.h - (A.y + A.h + 4));
    }

    /* ---- the clock of both examples: years after the first state, one step a year. A state that adopted d years after the
       first is shown once the clock reaches d; it comes in during the first part of its step. */
    function clockK(t, t0) { return Math.max(0, Math.min(CLK + 0.999, (t - t0) / T.step)); }
    function clockAmt(d, k) {
      var yr = Math.floor(k);
      return d === 0 || d < yr ? 1 : (d === yr ? smooth((k - yr) / 0.4) : 0);
    }

    /* ---- the small example: one outcome, traffic deaths. The maps of the four laws studied for it lift out of the wall and run
       on the clock; under each, the states adopted so far and those not yet (the comparison group of each study). */
    /* n maps in rows of perRow, centred in the wall area, with room for two lines of labels under each row */
    function layoutRow(n, perRow, maxW, gx, gy) {
      var A = L.area, slots = [], q, lab = L.portrait ? 32 : 40;
      var rowsN = Math.ceil(n / perRow), sw = Math.min(maxW, (A.w - (perRow - 1) * gx) / perRow), sh = sw * 0.66;
      var hMax = (A.h - (rowsN - 1) * gy - rowsN * lab) / rowsN;
      if (sh > hMax) { sh = hMax; sw = sh / 0.66; }
      var totalH = rowsN * (sh + lab) + (rowsN - 1) * gy, y0 = A.y + (A.h - totalH) / 2;
      for (q = 0; q < n; q++) {
        var row = Math.floor(q / perRow), inRow = Math.min(perRow, n - row * perRow), col = q - row * perRow;
        var rw = inRow * sw + (inRow - 1) * gx, rx = A.x + (A.w - rw) / 2;
        slots.push({ x: rx + col * (sw + gx), y: y0 + row * (sh + lab + gy), w: sw, h: sh });
      }
      return slots;
    }
    /* narrow phones (stage under 310 px): a wider gap between the two columns keeps the two law labels of a row apart */
    function layoutSmall() { L.slots = L.portrait ? layoutRow(TRAFFIC.length, 2, 220, L.w < 310 ? 24 : 12, 8) : layoutRow(TRAFFIC.length, 4, 250, 22, 0); }
    function liftP(t) { return t < T.s1 ? easeInOut(ramp(t, T.s0, T.s0 + 0.7)) : 1 - easeInOut(ramp(t, T.s1, T.s1 + 0.7)); }
    function smallClockA(t) { return smooth((t - (T.s0 + 0.5)) / 0.4) * (1 - smooth((t - T.s1) / 0.4)); }
    function smallOn(t) { return t >= T.s0 && t <= T.s1 + 0.75; }
    function drawSmallEx(ctx, t) {
      if (!smallOn(t)) return;
      var p = liftP(t), c = th, W = L.wall, q, z;
      if (p <= 0.003) return;
      ctx.fillStyle = NS.rgba(c.bg, 0.86 * p); ctx.fillRect(W.x - 4, W.y - 4, W.w + 8, W.h + 8);
      var k = clockK(t, T.sc0), yr = Math.floor(k), tp = smallClockA(t), ga = ctx.globalAlpha;
      for (q = 0; q < TRAFFIC.length; q++) {
        var ti = TRAFFIC[q], o = L.tile(ti), sl = L.slots[q];
        var x = lerp(o.x, sl.x, p), y = lerp(o.y, sl.y, p), w = lerp(o.w, sl.w, p), h = lerp(o.h, sl.h, p);
        var s = Math.min(w * 0.96 / 975, h * 0.96 / 610);
        ctx.save(); ctx.translate(x + (w - 975 * s) / 2, y + (h - 610 * s) / 2); ctx.scale(s, s);
        if (tp < 1) paintTile(ctx, ti);
        if (tp > 0) {                                                  // the wall's colours give way to the clock
          ctx.globalAlpha = ga * tp;
          ctx.fillStyle = styles.land; ctx.fill(allSimp);
          for (z = tOff[ti]; z < tOff[ti + 1]; z++) {
            var am = clockAmt(adD[z], k);
            if (am > 0.01) { ctx.fillStyle = NS.rgba(NS.mix(c.land, classRGB(classOfD(adD[z])), am), 1); ctx.fill(simp[adS[z]]); }
          }
          ctx.globalAlpha = ga;
        }
        if (s > 0.05) { ctx.lineWidth = 0.8 / s; ctx.strokeStyle = NS.rgba(c.bg, 0.9 * p); ctx.stroke(allSimp); }
        ctx.restore();
        var la = smooth((p - 0.65) / 0.35);
        if (la > 0.01) {
          var size = 12, cxl = sl.x + sl.w / 2, yl = sl.y + sl.h + (L.portrait ? 13 : 17);
          NS.text(ctx, fit(ctx, S.lawLab(S.traffic[q], PK[ti][0]), sl.w + 10, size, 600), cxl, yl, { size: size, weight: 600, color: c.ink, alpha: la, align: 'center' });
          var n = cumS[q][yr];
          NS.text(ctx, S.counts(n, nStates - n), cxl, yl + (L.portrait ? 14 : 17), { size: size, color: c.text, alpha: la * tp, align: 'center' });
        }
      }
    }

    /* ---- the broad example: the whole wall on the clock (the layers of buildAtlases), then back to all years. Once the clock
       reaches 5, the median number of states within 5 years shows beside each band and stays. */
    function bigClockA(t) { return smooth((t - T.g0) / 0.5) * (1 - smooth((t - T.g2) / 0.6)); }
    function medA(t) { return smooth((t - (T.gc0 + CLK * T.step)) / 0.5); }
    function drawClockWall(ctx, t) {
      var a = bigClockA(t);
      if (a <= 0.003 || !layers) return;
      var W = L.wall, ga = ctx.globalAlpha, k = clockK(t, T.gc0), d;
      ctx.globalAlpha = ga * a; ctx.drawImage(layers[0], W.x, W.y, W.w, W.h);
      for (d = 1; d <= CLK; d++) {
        var am = clockAmt(d, k);
        if (am <= 0.003) break;
        ctx.globalAlpha = ga * a * am; ctx.drawImage(layers[d], W.x, W.y, W.w, W.h);
      }
      ctx.globalAlpha = ga;
    }

    function drawBandLabels(ctx, t) {
      var a = smooth((t - (T.pb1 - 0.3)) / 0.6) * (1 - (smallOn(t) ? liftP(t) : 0));
      if (a <= 0.003) return;
      var c = th, q, ma = a * medA(t), mMax = Math.max.apply(null, MED);
      for (q = 0; q < 5; q++) {
        var B = L.bandBox[q];
        if (L.portrait) {
          NS.text(ctx, S.bands[q], B.x, B.y - 4, { size: 12, weight: 500, color: c.muted, alpha: a });
          if (ma > 0.003) {
            var lab = S.medLab(MED[q]), xr = B.x + B.w, bw = 40 * MED[q] / mMax;
            NS.text(ctx, lab, xr, B.y - 4, { size: 12, weight: 600, color: c.ink, alpha: ma, align: 'right' });
            ctx.fillStyle = NS.rgba(c.accent, ma); ctx.fillRect(xr - NS.textWidth(ctx, lab, 12, 600) - 6 - bw, B.y - 9, bw, 4);
          }
        } else {
          NS.text(ctx, S.bands[q], B.x - L.labW, B.y + 11, { size: 12, weight: 600, color: c.text, alpha: a });
          if (ma > 0.003) {
            var bw2 = (L.labW - 36) * MED[q] / mMax, x2 = B.x - L.labW, y2 = B.y + 27;
            ctx.fillStyle = NS.rgba(c.accent, ma); ctx.fillRect(x2, y2 - 6, bw2, 5);
            NS.text(ctx, String(MED[q]), x2 + bw2 + 5, y2, { size: 12, weight: 650, color: c.ink, alpha: ma });
          }
        }
      }
    }

    /* the key under the wall: years after the first state; during the examples, the clock and what it shows */
    function drawKey(ctx, t) {
      var base = smooth((t - (T.pb0 + 0.2)) / 0.6);
      if (base <= 0.003) return;
      var sa = smallOn(t) ? smallClockA(t) : 0, ba = bigClockA(t), ka = base * (1 - Math.max(sa, ba));
      if (ka > 0.003) drawYearsKey(ctx, ka, t > T.g0 && !L.portrait, medA(t));
      if (sa > 0.003) drawClockKey(ctx, base * sa, clockK(t, T.sc0), false);
      if (ba > 0.003) drawClockKey(ctx, base * ba, clockK(t, T.gc0), true);
    }
    function swatch(ctx, rgb, x, y, sw, a) {
      ctx.fillStyle = NS.rgba(rgb, a); ctx.fillRect(x, y - sw + 1, sw, sw);
      ctx.strokeStyle = NS.rgba(th.hair, a); ctx.lineWidth = 1; ctx.strokeRect(x + 0.5, y - sw + 1.5, sw - 1, sw - 1);
    }
    function drawYearsKey(ctx, a, withMed, mA) {
      var c = th, size = 12, sw = L.portrait ? 9 : 10, y = L.keyY, x = L.pad;
      var gl = L.portrait ? 3 : 4, gi = L.portrait ? 6 : 9;          // swatch-to-label and item gaps
      if (L.w < 340) { gl = 2; gi = 4; sw = 8; }                      // the narrowest phones: tighter key
      var labels = ['0', '1–2', '3–5', '6–9', '10+'], q, wl = 0, mk = '';
      for (q = 0; q < 5; q++) wl += sw + gl + NS.textWidth(ctx, labels[q], size, 400) + gi;
      wl += sw + gl + NS.textWidth(ctx, S.none, size, 400);
      if (withMed) {
        mk = fit(ctx, S.medKey, L.w - 2 * L.pad - wl - 60 - NS.textWidth(ctx, S.key[S.key.length - 1], size, 500), size, 400);
        wl += 22 + 23 + NS.textWidth(ctx, mk, size, 400);
      }
      var key = fit(ctx, S.key, L.w - 2 * L.pad - wl - 8, size, 500);
      NS.text(ctx, key, x, y, { size: size, weight: 500, color: c.text, alpha: a });
      x += NS.textWidth(ctx, key, size, 500) + 8;
      for (q = 4; q >= 0; q--) {
        ctx.fillStyle = NS.rgba(classRGB(q), a); ctx.fillRect(x, y - sw + 1, sw, sw);
        var lab = labels[4 - q];
        NS.text(ctx, lab, x + sw + gl, y, { size: size, color: c.muted, alpha: a });
        x += sw + gl + NS.textWidth(ctx, lab, size, 400) + gi;
      }
      swatch(ctx, c.land, x, y, sw, a);
      NS.text(ctx, S.none, x + sw + gl, y, { size: size, color: c.muted, alpha: a });
      if (withMed && mA > 0.003) {
        x += sw + gl + NS.textWidth(ctx, S.none, size, 400) + 22;
        ctx.fillStyle = NS.rgba(c.accent, a * mA); ctx.fillRect(x, y - 6, 18, 5);
        NS.text(ctx, mk, x + 23, y, { size: size, color: c.muted, alpha: a * mA });
      }
    }
    function drawClockKey(ctx, a, k, big) {
      var c = th, size = 12, ns = L.portrait ? 15 : 17, sw = L.portrait ? 9 : 10, y = L.keyY, x = L.pad, q, lv;
      var gl = L.portrait ? 3 : 4, gi = L.portrait ? 7 : 10, labels = ['0', '1–2', '3–5'], nW = NS.textWidth(ctx, '0', ns, 700);
      var fixed = 0, ck, nl, ul;
      for (q = 0; q < 3; q++) fixed += sw + gl + NS.textWidth(ctx, labels[q], size, 400) + gi;
      for (lv = 0; lv < 3; lv++) {                                    // the longest wordings that fit together
        ck = S.clock[Math.min(lv, S.clock.length - 1)]; nl = S.clkNone[Math.min(lv, S.clkNone.length - 1)]; ul = S.unobs[Math.min(lv, S.unobs.length - 1)];
        var wt = NS.textWidth(ctx, ck[0], size, 500) + nW + 8 + (ck[1] ? NS.textWidth(ctx, ck[1], size, 500) : 0) + 14 + fixed
          + sw + gl + NS.textWidth(ctx, nl, size, 400) + (big ? gi + sw + gl + NS.textWidth(ctx, ul, size, 400) : 0);
        if (wt <= L.w - 2 * L.pad) break;
      }
      NS.text(ctx, ck[0], x, y, { size: size, weight: 500, color: c.text, alpha: a }); x += NS.textWidth(ctx, ck[0], size, 500) + 4;
      NS.text(ctx, String(Math.floor(k)), x + nW / 2, y + 1, { size: ns, weight: 700, color: c.ink, alpha: a, align: 'center' }); x += nW + 4;
      if (ck[1]) { NS.text(ctx, ck[1], x, y, { size: size, weight: 500, color: c.text, alpha: a }); x += NS.textWidth(ctx, ck[1], size, 500); }
      x += 14;
      for (q = 0; q < 3; q++) {
        ctx.fillStyle = NS.rgba(classRGB(4 - q), a); ctx.fillRect(x, y - sw + 1, sw, sw);
        NS.text(ctx, labels[q], x + sw + gl, y, { size: size, color: c.muted, alpha: a });
        x += sw + gl + NS.textWidth(ctx, labels[q], size, 400) + gi;
      }
      swatch(ctx, c.land, x, y, sw, a);
      NS.text(ctx, nl, x + sw + gl, y, { size: size, color: c.muted, alpha: a });
      if (big) {
        x += sw + gl + NS.textWidth(ctx, nl, size, 400) + gi;
        swatch(ctx, veilRGB(), x, y, sw, a);
        NS.text(ctx, ul, x + sw + gl, y, { size: size, color: c.muted, alpha: a });
      }
    }

    /* ---------------------------------------------------------------- rail */
    function drawRail(ctx, t) {
      var c = th, ch, kk, kx;
      var acts = [[0, T.ho1], [T.wave0 - 0.4, T.dep1], [T.pb0, T.g3]];
      var names = [S.l1, S.l2, S.l3];
      for (kk = 0; kk < 3; kk++) {
        ch = L.chips[kk];
        var p = clamp01((t - acts[kk][0]) / (acts[kk][1] - acts[kk][0]));
        var started = t >= acts[kk][0], done = p >= 1, active = started && !done;
        ctx.strokeStyle = NS.rgba(c.hair, 1); ctx.lineWidth = 1.5; ctx.beginPath(); ctx.moveTo(ch.x, ch.y + 1); ctx.lineTo(ch.x + ch.w, ch.y + 1); ctx.stroke();
        if (started) { ctx.strokeStyle = NS.rgba(c.accent, active ? 1 : 0.9); ctx.lineWidth = 1.8; ctx.beginPath(); ctx.moveTo(ch.x, ch.y + 1); ctx.lineTo(ch.x + ch.w * easeOut(p), ch.y + 1); ctx.stroke(); }
        NS.text(ctx, fit(ctx, names[kk], ch.w, 12, 600), ch.x, ch.y + 18, { size: 12, weight: 600, color: active || done ? c.ink : c.muted, alpha: started ? 1 : 0.55 });
      }
      var vy = L.portrait ? 38 : 42, vs = L.portrait ? 12.5 : 15, cwid = L.chips[0].w;
      ch = L.chips[0];
      var t1 = smooth((t - T.ho0) / (T.ho1 - T.ho0));
      if (t < T.ho0 + 0.05) NS.text(ctx, '–', ch.x, ch.y + vy, { size: vs, color: c.faint });
      else {
        var val1 = fmt(C.texts) + ' → ' + fmt(Math.max(1, Math.round(C.cands * t1)));
        var fs1 = NS.fitSize(ctx, val1, vs, cwid - 2, 500, true), a1 = smooth((t - T.ho0) / 0.4);
        if (fs1 >= 12) NS.tabular(ctx, val1, ch.x, ch.y + vy, { size: fs1, weight: 500, color: c.ink, alpha: a1 });
        else {                                                       // a narrow chip: two lines, never below 12 px
          NS.tabular(ctx, fmt(C.texts) + ' →', ch.x, ch.y + vy - 4, { size: 12.5, weight: 500, color: c.ink, alpha: a1 });
          NS.tabular(ctx, fmt(Math.max(1, Math.round(C.cands * t1))), ch.x, ch.y + vy + 11, { size: 12.5, weight: 500, color: c.ink, alpha: a1 });
        }
      }
      ch = L.chips[1];
      var t2 = smooth((t - T.grid0) / 0.8);
      if (t < T.grid0) NS.text(ctx, '–', ch.x, ch.y + vy, { size: vs, color: c.faint });
      else {
        var landedN = 0; for (kk = 0; kk < NP; kk++) if (t >= T.grid0 + gridC.dly[kk] + GRID_FLY) landedN++;
        var wnum = NS.tabular(ctx, String(landedN), ch.x, ch.y + vy, { size: vs, weight: 500, color: c.ink, alpha: t2 });
        var room = L.compact ? ch.w - wnum - 6 : gridC.tx[0] - 8 - (ch.x + wnum + 5);
        NS.text(ctx, fit(ctx, S.l2sub, room, 12, 400), ch.x + wnum + 5, ch.y + vy, { size: 12, color: c.text, alpha: t2 });
      }
      var counts = [C.match, C.dir, C.nc, C.os];
      if (t >= T.dep0) {
        var ca = smooth((t - (T.dep1 - 0.2)) / 0.4);
        if (L.compact) {
          var bx0 = ch.x, bw = ch.w, by = ch.y + 49, acc2 = 0;
          if (ca > 0.01) for (kx = 0; kx < 4; kx++) { var segW = counts[kx] / NP * bw; ctx.fillStyle = NS.rgba(NS.resultColor(c, kx), ca); ctx.fillRect(bx0 + acc2, by - 2.5, Math.max(1, segW - 1.5), 5); acc2 += segW; }
        } else if (t >= T.dep1 - 0.1) {
          for (kk = 0; kk < NP; kk++) {
            var cl = classes[kk];
            ctx.fillStyle = NS.rgba(NS.resultColor(c, cl), ca); ctx.beginPath(); NS.marker(ctx, cl, gridC.tx[kk], gridC.ty[kk], 2.7); ctx.fill();
          }
          var xx = ch.x + ch.w - 4 * 32 - 2, cy0 = ch.y + 50;
          for (kx = 0; kx < 4; kx++) {
            ctx.fillStyle = NS.rgba(NS.resultColor(c, kx), ca); ctx.beginPath(); NS.marker(ctx, kx, xx + 4, cy0 - 4, 3.2); ctx.fill();
            NS.tabular(ctx, String(counts[kx]), xx + 11, cy0, { size: 12, weight: 600, color: c.ink, alpha: ca }); xx += 32;
          }
        }
      }
      ch = L.chips[2];
      var a3 = smooth((t - (T.pb0 + 1.0)) / 0.6);
      if (t < T.pb0 + 1.0) NS.text(ctx, '–', ch.x, ch.y + vy, { size: vs, color: c.faint });
      else {
        var w3 = NS.tabular(ctx, String(nT), ch.x, ch.y + vy, { size: vs, weight: 500, color: c.ink, alpha: a3 });
        NS.text(ctx, fit(ctx, S.l3sub, ch.w - w3 - 6, 12, 400), ch.x + w3 + 5, ch.y + vy, { size: 12, color: c.text, alpha: a3 });
        NS.text(ctx, fit(ctx, S.l3src, ch.w, 12, 400), ch.x, ch.y + 58, { size: 12, color: c.muted, alpha: a3 * 0.9 });
      }
    }

    /* a title too wide for the strip left of the two buttons breaks once, at a word, when both halves fit (narrow phones only) */
    function wrapTwo(ctx, s, maxW, size, weight) {
      if (NS.textWidth(ctx, s, size, weight) <= maxW) return null;
      var words = s.split(' '), k, a, b;
      for (k = words.length - 1; k > 0; k--) {
        a = words.slice(0, k).join(' ');
        if (NS.textWidth(ctx, a, size, weight) <= maxW) { b = words.slice(k).join(' '); return NS.textWidth(ctx, b, size, weight) <= maxW ? [a, b] : null; }
      }
      return null;
    }

    function drawCaptions(ctx, t) {
      var c = th, q, a, x = L.pad + 2, y1 = L.top + 12, maxW = L.w - 2 * L.pad - 4 - 80;          // the two 44 px buttons sit at the top right
      var caps = [
        [T.land0 - 0.2, T.ho0, S.cap1],
        [T.ho0, T.wave0 + 0.3, S.cap2],
        [T.wave0 + 0.3, T.wave1 + 0.4, S.cap3],
        [T.wave1 + 0.3, T.rise0 + 0.5, S.cap4],
        [T.rise0 + 0.5, T.dep0 + 0.2, S.cap5]
      ];
      for (q = 0; q < caps.length; q++) {
        a = smooth((t - caps[q][0]) / 0.35) * (1 - smooth((t - (caps[q][1] - 0.3)) / 0.3));
        if (a <= 0.01) continue;
        var one = fit(ctx, caps[q][2], maxW, 13, 500), two = L.portrait ? wrapTwo(ctx, one, maxW, 13, 500) : null;
        if (two) {
          NS.text(ctx, two[0], x, y1 - 5, { size: 13, weight: 500, color: c.ink, alpha: a * 0.92 });
          NS.text(ctx, two[1], x, y1 + 10, { size: 13, weight: 500, color: c.ink, alpha: a * 0.92 });
        } else NS.text(ctx, one, x, y1, { size: 13, weight: 500, color: c.ink, alpha: a * 0.92 });
      }
      /* L3: a title and one line under it */
      var ts = L.portrait ? 13.5 : 14.5, ss = L.portrait ? 12 : 12.5, ya = L.top + 13, yb = L.top + 31;
      var caps3 = [
        [T.pb0 - 0.1, T.s0, S.wallT, S.wallS(nT)],
        [T.s0, T.s1 + 0.7, S.smallT, S.smallS(hiQ, loQ, CUM5)],
        [T.g0, T.g3, S.broadT, S.broadS(MED)],
        [T.g3, 1e9, S.wallT, S.endS]
      ];
      for (q = 0; q < caps3.length; q++) {
        a = smooth((t - caps3[q][0]) / 0.3) * (1 - smooth((t - (caps3[q][1] - 0.25)) / 0.25));
        if (a <= 0.003) continue;
        NS.text(ctx, fit(ctx, caps3[q][2], maxW, ts, 650), x, ya, { size: ts, weight: 650, color: c.ink, alpha: a });
        var sub = caps3[q][3];
        if (sub) NS.text(ctx, fit(ctx, sub, maxW, ss, 400), x, yb, { size: ss, color: c.text, alpha: a });
      }
    }

    function draw(ctx, t) {
      if (!L) return;
      var w = L.w, h = L.h, c = th;
      ctx.globalAlpha = 1; ctx.fillStyle = NS.rgba(c.bg, 1); ctx.fillRect(0, 0, w, h);
      var gf = smooth(t / 0.6) * (1 - smooth((t - T.fade0) / (T.end - T.fade0)));
      if (gf <= 0.003) return;
      ctx.globalAlpha = gf;
      updateCamera(t);
      drawWall(ctx, t);
      drawMap(ctx, t);
      drawSmallEx(ctx, t);
      drawMask(ctx, t);
      drawSparks(ctx, t);
      drawWaveFx(ctx, t);
      drawPanelsL1(ctx, t);
      drawStairs(ctx, t);
      drawEstimate(ctx, t);
      drawBandLabels(ctx, t);
      drawKey(ctx, t);
      drawYear(ctx, t);
      drawRail(ctx, t);
      drawCaptions(ctx, t);
      ctx.globalAlpha = 1;
    }

    var api = {
      id: 'C', duration: T.end, poster: T.poster, still: 3.7, start: T.start, seek: T.seek, T: T,
      init: init, draw: draw, lang: lang,
            stats: { tiles: nT, bands: BANDS.slice(), clockMedians: MED.slice(), unobserved: nUnobs, traffic: TRAFFIC.length, trafficYear5: CUM5.slice(), nurseStates: nurse.n }
    };
    return api;
  };
})(typeof window !== 'undefined' ? window : globalThis);
