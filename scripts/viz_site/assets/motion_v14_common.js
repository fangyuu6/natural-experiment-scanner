/* Shared helpers for the three motion prototypes. DOM-free: scenes are pure functions of time. */
(function (G) {
  'use strict';
  var NS = G.NESM = G.NESM || {};
  var TAU = Math.PI * 2;
  NS.TAU = TAU;

  /* ------------------------------------------------------------------ math */
  var clamp = function (x, a, b) { return x < a ? a : (x > b ? b : x); };
  var clamp01 = function (x) { return x < 0 ? 0 : (x > 1 ? 1 : x); };
  var lerp = function (a, b, t) { return a + (b - a) * t; };
  var smooth = function (x) { x = clamp01(x); return x * x * (3 - 2 * x); };
  var smoother = function (x) { x = clamp01(x); return x * x * x * (x * (x * 6 - 15) + 10); };
  var easeOut = function (x) { x = clamp01(x); var u = 1 - x; return 1 - u * u * u; };
  var easeIn = function (x) { x = clamp01(x); return x * x * x; };
  var easeInOut = function (x) { x = clamp01(x); return x < 0.5 ? 4 * x * x * x : 1 - Math.pow(-2 * x + 2, 3) / 2; };
  var easeOutBack = function (x) { x = clamp01(x); var c1 = 1.2, c3 = c1 + 1; return 1 + c3 * Math.pow(x - 1, 3) + c1 * Math.pow(x - 1, 2); };
  /* progress of t inside [a,b] as 0..1 */
  var ramp = function (t, a, b) { return clamp01((t - a) / (b - a)); };
  /* 1 inside [a,b], with soft edges of width e */
  var window01 = function (t, a, b, e) { return smooth((t - a) / e) * (1 - smooth((t - b) / e)); };
  NS.clamp = clamp; NS.clamp01 = clamp01; NS.lerp = lerp; NS.smooth = smooth; NS.smoother = smoother;
  NS.easeOut = easeOut; NS.easeIn = easeIn; NS.easeInOut = easeInOut; NS.easeOutBack = easeOutBack;
  NS.ramp = ramp; NS.window01 = window01;

  NS.rng = function (seed) {
    var a = seed >>> 0;
    return function () {
      a = (a + 0x6D2B79F5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  };
  NS.gauss = function (r) { // Box-Muller from a uniform rng
    var u = 0, v = 0;
    while (u === 0) u = r();
    v = r();
    return Math.sqrt(-2 * Math.log(u)) * Math.cos(TAU * v);
  };
  NS.shuffle = function (arr, r) {
    for (var i = arr.length - 1; i > 0; i--) { var j = Math.floor(r() * (i + 1)); var t = arr[i]; arr[i] = arr[j]; arr[j] = t; }
    return arr;
  };

  /* ---------------------------------------------------------------- format */
  NS.fmt = function (n) {
    var s = String(Math.round(n)), out = '';
    for (var i = 0; i < s.length; i++) { if (i && (s.length - i) % 3 === 0) out += ','; out += s.charAt(i); }
    return out;
  };

  /* ---------------------------------------------------------------- colour */
  var parseColor = function (str) {
    str = String(str).trim();
    var m;
    if ((m = /^#([0-9a-f]{3})$/i.exec(str))) { var h = m[1]; return [parseInt(h[0] + h[0], 16), parseInt(h[1] + h[1], 16), parseInt(h[2] + h[2], 16)]; }
    if ((m = /^#([0-9a-f]{6})/i.exec(str))) { var g = m[1]; return [parseInt(g.slice(0, 2), 16), parseInt(g.slice(2, 4), 16), parseInt(g.slice(4, 6), 16)]; }
    if ((m = /^rgba?\(([^)]+)\)/i.exec(str))) { var p = m[1].split(/[,\s/]+/).filter(Boolean).map(Number); return [p[0], p[1], p[2]]; }
    return null;
  };
  NS.parseColor = parseColor;
  var rgba = function (c, a) { return 'rgba(' + (c[0] | 0) + ',' + (c[1] | 0) + ',' + (c[2] | 0) + ',' + (Math.round(clamp01(a) * 1000) / 1000) + ')'; };
  NS.rgba = rgba;
  var qcache = {};
  /* quantised alpha (1/20 steps) so batched groups stay few and strings are reused */
  NS.rgbaQ = function (c, a) {
    var q = Math.round(clamp01(a) * 20);
    var k = (c[0] | 0) + ',' + (c[1] | 0) + ',' + (c[2] | 0) + ',' + q;
    var v = qcache[k];
    if (!v) { v = rgba(c, q / 20); qcache[k] = v; }
    return v;
  };
  NS.mix = function (a, b, t) { return [a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t, a[2] + (b[2] - a[2]) * t]; };

  var hex = function (s) { return parseColor(s); };
  NS.themes = {
    light: {
      dark: false,
      bg: hex('#ffffff'), ink: hex('#111827'), text: hex('#4b5563'), muted: hex('#6b7280'), faint: hex('#9ca3af'),
      hair: hex('#e5e7eb'), land: hex('#eceef2'), accent: hex('#1d4ed8'), accent2: hex('#7ba4f0'),
      b1: hex('#a8c5f9'), b2: hex('#7ba4f0'), b3: hex('#5283e0'), b4: hex('#3263c3'), b5: hex('#1e4798'),
      amber: hex('#d97706'), match: hex('#2166ac'), dir: hex('#9a6700'), nc: hex('#64748b'), os: hex('#a23b72'), slate: hex('#8793a6')
    },
    dark: {
      dark: true,
      bg: hex('#0e1320'), ink: hex('#e8ecf4'), text: hex('#b9c2d3'), muted: hex('#8d98ad'), faint: hex('#5f6b82'),
      hair: hex('#263043'), land: hex('#1a2336'), accent: hex('#6f9bff'), accent2: hex('#9bbaff'),
      b1: hex('#31508f'), b2: hex('#4a72c8'), b3: hex('#6b92f0'), b4: hex('#93b2ff'), b5: hex('#bdd0ff'),
      amber: hex('#fbbf24'), match: hex('#5aa0e6'), dir: hex('#e0a62d'), nc: hex('#94a3b8'), os: hex('#e07bb0'), slate: hex('#6f7b90')
    }
  };
  var CSS_TOKENS = ['bg', 'ink', 'text', 'muted', 'faint', 'hair', 'land', 'accent', 'accent2', 'b1', 'b2', 'b3', 'b4', 'b5', 'amber', 'match', 'dir', 'nc', 'os', 'slate'];
  /* Browser only: read the palette from CSS custom properties (--v-bg, --v-ink, ...) on `el`. */
  NS.readTheme = function (el) {
    var cs = G.getComputedStyle(el);
    var out = { dark: false };
    var bgc = parseColor(cs.getPropertyValue('--v-bg') || '');
    if (bgc) out.dark = (bgc[0] * 0.299 + bgc[1] * 0.587 + bgc[2] * 0.114) < 128;
    var fallback = out.dark ? NS.themes.dark : NS.themes.light;
    for (var i = 0; i < CSS_TOKENS.length; i++) {
      var k = CSS_TOKENS[i], v = parseColor(cs.getPropertyValue('--v-' + k) || '');
      out[k] = v || fallback[k];
    }
    return out;
  };

  /* ------------------------------------------------------------------ text */
  var FONT = '-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,"Helvetica Neue",Arial,sans-serif';
  NS.FONT = FONT;
  NS.setFont = function (ctx, size, weight) { ctx.font = (weight || 400) + ' ' + size + 'px ' + FONT; };
  /* o: size, weight, color [r,g,b], alpha, align, base */
  NS.text = function (ctx, s, x, y, o) {
    o = o || {};
    ctx.font = (o.weight || 400) + ' ' + (o.size || 12) + 'px ' + FONT;
    ctx.textAlign = o.align || 'left';
    ctx.textBaseline = o.base || 'alphabetic';
    if (o.halo) {
      ctx.save();
      ctx.lineJoin = 'round'; ctx.lineWidth = 4; ctx.strokeStyle = rgba(o.halo, 0.9 * (o.alpha == null ? 1 : o.alpha));
      ctx.strokeText(s, x, y);
      ctx.restore();
    }
    ctx.fillStyle = rgba(o.color || [0, 0, 0], o.alpha == null ? 1 : o.alpha);
    ctx.fillText(s, x, y);
  };
  /* Tabular numbers: draw each glyph on a fixed advance so counting digits never jitter. */
  NS.tabular = function (ctx, s, x, y, o) {
    o = o || {};
    var size = o.size || 14;
    ctx.font = (o.weight || 400) + ' ' + size + 'px ' + FONT;
    ctx.textBaseline = o.base || 'alphabetic';
    ctx.fillStyle = rgba(o.color || [0, 0, 0], o.alpha == null ? 1 : o.alpha);
    ctx.textAlign = 'left';
    var adv = size * 0.60, narrow = size * 0.30, w = 0, i, ch, widths = [];
    for (i = 0; i < s.length; i++) {
      ch = s.charAt(i);
      var a = (ch >= '0' && ch <= '9') ? adv : (ch === ',' || ch === '.') ? narrow : (ch === ' ' ? size * 0.3 : (ctx.measureText(ch).width));
      widths.push(a); w += a;
    }
    var x0 = o.align === 'right' ? x - w : (o.align === 'center' ? x - w / 2 : x);
    var pass = o.halo ? 2 : 1;
    for (var ps = 0; ps < pass; ps++) {
      var px = x0;
      if (o.halo && ps === 0) { ctx.save(); ctx.lineJoin = 'round'; ctx.lineWidth = 4; ctx.strokeStyle = rgba(o.halo, 0.9 * (o.alpha == null ? 1 : o.alpha)); }
      for (i = 0; i < s.length; i++) {
        ch = s.charAt(i);
        if (ch !== ' ') {
          var cw = ctx.measureText(ch).width;
          var isDigit = ch >= '0' && ch <= '9';
          var gx = isDigit ? px + (widths[i] - cw) / 2 : px;
          if (o.halo && ps === 0) ctx.strokeText(ch, gx, y); else ctx.fillText(ch, gx, y);
        }
        px += widths[i];
      }
      if (o.halo && ps === 0) ctx.restore();
    }
    return w;
  };
  NS.tabularWidth = function (ctx, s, size, weight) {
    ctx.font = (weight || 400) + ' ' + size + 'px ' + FONT;
    var adv = size * 0.60, narrow = size * 0.30, w = 0;
    for (var i = 0; i < s.length; i++) {
      var ch = s.charAt(i);
      w += (ch >= '0' && ch <= '9') ? adv : (ch === ',' || ch === '.') ? narrow : (ch === ' ' ? size * 0.3 : ctx.measureText(ch).width);
    }
    return w;
  };
  /* font size (<= size) at which `s` fits maxW */
  NS.fitSize = function (ctx, s, size, maxW, weight, tab) {
    var w = tab ? NS.tabularWidth(ctx, s, size, weight) : NS.textWidth(ctx, s, size, weight);
    return w <= maxW ? size : Math.max(9, size * maxW / w);
  };
  NS.textWidth = function (ctx, s, size, weight) { ctx.font = (weight || 400) + ' ' + size + 'px ' + FONT; return ctx.measureText(s).width; };

  /* -------------------------------------------------------------- dot batch */
  /* Collects circles per fill style and flushes one path per style. */
  NS.DotBatch = function () { this.g = {}; this.keys = []; };
  NS.DotBatch.prototype.add = function (style, x, y, r) {
    var a = this.g[style];
    if (!a) { a = this.g[style] = []; this.keys.push(style); }
    a.push(x, y, r);
  };
  NS.DotBatch.prototype.flush = function (ctx) {
    for (var k = 0; k < this.keys.length; k++) {
      var style = this.keys[k], a = this.g[style];
      if (!a.length) continue;
      ctx.fillStyle = style;
      ctx.beginPath();
      for (var i = 0; i < a.length; i += 3) { ctx.moveTo(a[i] + a[i + 2], a[i + 1]); ctx.arc(a[i], a[i + 1], a[i + 2], 0, TAU); }
      ctx.fill();
      a.length = 0;
    }
  };

  /* ---------------------------------------------------------- result classes */
  /* The re-estimated papers in the site's order: number matches, same direction, not comparable, opposite sign. */
  NS.resultClasses = function (counts) {
    var out = [], i;
    for (i = 0; i < counts.match; i++) out.push(0);
    for (i = 0; i < counts.dir; i++) out.push(1);
    for (i = 0; i < counts.nc; i++) out.push(2);
    for (i = 0; i < (counts.os || 0); i++) out.push(3);
    return out;
  };
  var RES_KEYS = ['match', 'dir', 'nc', 'os'];
  NS.resultColor = function (theme, cls) { return theme[RES_KEYS[cls]]; };
  /* Marker per class, as on the site's Estimates plot: circle, diamond, square. */
  NS.marker = function (ctx, cls, x, y, r) {
    if (cls === 0) { ctx.moveTo(x + r, y); ctx.arc(x, y, r, 0, TAU); }
    else if (cls === 1) { var d = r * 1.28; ctx.moveTo(x, y - d); ctx.lineTo(x + d, y); ctx.lineTo(x, y + d); ctx.lineTo(x - d, y); ctx.closePath(); }
    else if (cls === 2) { var s = r * 0.92; ctx.rect(x - s, y - s, s * 2, s * 2); }
    else { var h = r * 1.25; ctx.moveTo(x, y - h); ctx.lineTo(x + h, y + h * 0.85); ctx.lineTo(x - h, y + h * 0.85); ctx.closePath(); }
  };

  /* ---------------------------------------------------------------- the map */
  /* Builds Path2D objects (or recorded paths in tests) for each state from the data. */
  NS.buildMap = function (D) {
    var m = D.map, states = [];
    for (var i = 0; i < m.codes.length; i++) {
      var p = new G.Path2D();
      var rings = m.rings[i];
      for (var r = 0; r < rings.length; r++) {
        var a = rings[r];
        p.moveTo(a[0], a[1]);
        for (var k = 2; k < a.length; k += 2) p.lineTo(a[k], a[k + 1]);
        p.closePath();
      }
      states.push({ code: m.codes[i], cx: m.cent[i][0], cy: m.cent[i][1], area: m.area[i], path: p, rings: rings });
    }
    return states;
  };
  /* Unpack the flat dot arrays: x,y,state triples. */
  NS.unpackDots = function (flat) {
    var n = flat.length / 3, x = new Float32Array(n), y = new Float32Array(n), s = new Uint8Array(n);
    for (var i = 0; i < n; i++) { x[i] = flat[i * 3]; y[i] = flat[i * 3 + 1]; s[i] = flat[i * 3 + 2]; }
    return { n: n, x: x, y: y, s: s };
  };
  /* Layout of the map frame (975x610) inside a rectangle, centred. */
  NS.fitMap = function (x, y, w, h) {
    var sc = Math.min(w / 975, h / 610);
    return { sc: sc, ox: x + (w - 975 * sc) / 2, oy: y + (h - 610 * sc) / 2, w: 975 * sc, h: 610 * sc };
  };

  /* ------------------------------------------------------------ layout flag */
  NS.isPortrait = function (w, h, opts) {
    if (opts && typeof opts.portrait === 'boolean') return opts.portrait;
    return w < 560 || h > w * 0.95;
  };
})(typeof window !== 'undefined' ? window : globalThis);
