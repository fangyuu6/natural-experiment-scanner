/* Browser glue for the Home figure: sizes the canvas, plays the scene once when about a third of it is in view, holds the last frame,
   pauses while the page is hidden or the figure is off screen, and shows the last frame at once under reduced motion. */
(function (G) {
  'use strict';
  var NS = G.NESM;
  function mq(query) { return G.matchMedia ? G.matchMedia(query) : { matches: false, addEventListener: function () {}, addListener: function () {} }; }
  function onChange(m, fn) { if (m.addEventListener) m.addEventListener('change', fn); else if (m.addListener) m.addListener(fn); }
  var reduceMQ = mq('(prefers-reduced-motion: reduce)');
  var narrowMQ = mq('(max-width: 719px)');

  function Stage(cfg) {
    this.root = cfg.root; this.frame = cfg.frame; this.canvas = cfg.canvas; this.btnPlay = cfg.btnPlay; this.btnReplay = cfg.btnReplay; this.scene = cfg.scene;
    this.labels = cfg.labels || {};
    this.ctx = this.canvas.getContext('2d');
    this.t = 0; this.playing = false; this.visible = false; this.built = false; this.started = false; this.userOverride = false; this.firstRun = true;
    this.raf = 0; this.last = 0; this.w = 0; this.h = 0; this.dpr = 1; this.maxDpr = 2; this.frames = 0; this.slow = 0; this.refreshQueued = false;
    var self = this;
    this.tick = function (now) { self.onFrame(now); };
    this.bind();
  }

  Stage.prototype.bind = function () {
    var self = this;
    if (this.btnPlay) this.btnPlay.addEventListener('click', function () { self.toggle(); });
    if (this.btnReplay) this.btnReplay.addEventListener('click', function () { self.replay(); });
    if (G.ResizeObserver) { this.ro = new G.ResizeObserver(function () { self.queueRefresh(90); }); this.ro.observe(this.frame); }
    if (G.IntersectionObserver) {
      this.io = new G.IntersectionObserver(function (entries) {
        for (var i = 0; i < entries.length; i++) self.visible = entries[i].isIntersecting;
        if (self.visible && !self.started && self.built) { self.started = true; self.autoplay(); }
        else self.updateRun();
      }, { threshold: [0, 0.1, 0.35] });
      this.io.observe(this.frame);
    } else this.visible = true;
    G.document.addEventListener('visibilitychange', function () { self.updateRun(); });
    onChange(reduceMQ, function () { self.onMotionPref(); });
    onChange(narrowMQ, function () { self.w = 0; self.queueRefresh(); });
    G.addEventListener('resize', function () { self.queueRefresh(90); });
  };

  Stage.prototype.reduced = function () { return reduceMQ.matches && !this.userOverride; };

  Stage.prototype.measure = function () {
    var w = this.frame.clientWidth, h = this.frame.clientHeight, dpr = Math.min(G.devicePixelRatio || 1, this.maxDpr);
    if (w < 60 || h < 60) return false;
    if (w === this.w && h === this.h && dpr === this.dpr && this.built) return false;
    this.w = w; this.h = h; this.dpr = dpr;
    this.canvas.width = Math.round(w * dpr); this.canvas.height = Math.round(h * dpr);
    return true;
  };

  Stage.prototype.build = function () {
    this.theme = NS.readTheme(this.root);
    this.scene.init(this.w, this.h, this.theme, {
      dpr: this.dpr, mobile: this.w < 560, portrait: this.w < 720 || this.h > this.w * 0.95,
      createCanvas: function (pw, ph) { var c = G.document.createElement('canvas'); c.width = pw; c.height = ph; return c; }
    });
    this.built = true;
  };

  Stage.prototype.render = function () {
    if (!this.built) return;
    this.ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    try { this.scene.draw(this.ctx, this.t); }
    catch (e) { this.playing = false; if (G.console && !this.reported) { this.reported = true; G.console.error('motion scene stopped: ' + (e && e.message)); } }
  };

  Stage.prototype.queueRefresh = function (delay) {
    var self = this;
    if (delay) {
      if (this.delayTimer) G.clearTimeout(this.delayTimer);
      this.delayTimer = G.setTimeout(function () { self.delayTimer = 0; self.queueRefresh(); }, delay);
      return;
    }
    if (this.refreshQueued) return;
    this.refreshQueued = true;
    G.requestAnimationFrame(function () { self.refreshQueued = false; self.refresh(); });
  };

  Stage.prototype.refresh = function () {
    var resized = this.measure();
    if (!this.built || resized) {
      if (this.w < 60) return;
      this.build();
      if (this.firstRun) this.t = this.reduced() ? this.scene.poster : this.scene.still;
      this.render();
      this.syncUi();
      if (this.visible && !this.started) { this.started = true; this.autoplay(); return; }
    }
    this.updateRun();
  };

  Stage.prototype.updateRun = function () {
    var want = this.playing && this.visible && !G.document.hidden && this.built;
    if (want && !this.raf) { this.last = 0; this.raf = G.requestAnimationFrame(this.tick); }
    else if (!want && this.raf) { G.cancelAnimationFrame(this.raf); this.raf = 0; }
  };

  Stage.prototype.onFrame = function (now) {
    this.raf = 0;
    if (!this.playing || !this.visible || G.document.hidden) return;
    if (!this.last) this.last = now;
    var dt = Math.min(0.05, Math.max(0, (now - this.last) / 1000));
    this.last = now;
    this.t += dt;
    var done = this.t >= this.scene.poster;
    if (done) this.t = this.scene.poster;
    var s0 = G.performance ? G.performance.now() : 0;
    this.render();
    if (done) { this.playing = false; this.syncUi(); return; }
    if (G.performance) {                       /* one-shot quality guard: slow frames on a dense screen drop to 1x */
      var cost = G.performance.now() - s0;
      this.frames++;
      if (cost > 14) this.slow++;
      if (this.frames >= 90) {
        if (this.slow > 45 && this.dpr > 1) { this.maxDpr = 1; this.w = 0; this.queueRefresh(); }
        this.frames = 0; this.slow = 0;
      }
    }
    this.raf = G.requestAnimationFrame(this.tick);
  };

  Stage.prototype.syncUi = function () {
    if (this.btnPlay) {
      var paused = !this.playing;
      this.btnPlay.classList.toggle('is-paused', paused);
      this.btnPlay.setAttribute('aria-pressed', paused ? 'true' : 'false');
      var name = paused ? this.labels.play : this.labels.pause;
      if (name) { this.btnPlay.setAttribute('aria-label', name); this.btnPlay.setAttribute('title', name); }
    }
  };

  Stage.prototype.autoplay = function () {
    if (this.reduced()) { this.playing = false; this.t = this.scene.poster; this.render(); this.syncUi(); return; }
    this.playing = true;
    if (this.firstRun) { this.t = this.scene.start; this.firstRun = false; }
    this.syncUi(); this.updateRun();
  };

  Stage.prototype.toggle = function () {
    if (this.playing) { this.playing = false; this.syncUi(); this.updateRun(); return; }
    if (reduceMQ.matches) this.userOverride = true;
    if (this.t >= this.scene.poster) this.t = this.scene.start;
    this.playing = true; this.firstRun = false; this.started = true;
    this.syncUi(); this.updateRun();
  };

  Stage.prototype.replay = function () {
    if (reduceMQ.matches) this.userOverride = true;
    this.firstRun = false; this.started = true; this.t = this.scene.start; this.playing = true;
    this.render(); this.syncUi(); this.updateRun();
  };

  Stage.prototype.onMotionPref = function () {
    if (reduceMQ.matches) { this.userOverride = false; this.playing = false; this.t = this.scene.poster; this.render(); }
    else if (this.visible) { this.autoplay(); }
    this.syncUi(); this.updateRun();
  };

  NS.Stage = Stage;

  /* Page wiring: the figure element with .mf-stage > canvas, [data-act=toggle], [data-act=replay]; the page data and labels as JSON. */
  NS.mount = function (fig, D, labels) {
    var scene = NS.sceneC12(D);
    var st = new Stage({ root: fig, frame: fig.querySelector('.mf-stage'), canvas: fig.querySelector('canvas'),
      btnPlay: fig.querySelector('[data-act="toggle"]'), btnReplay: fig.querySelector('[data-act="replay"]'), scene: scene, labels: labels });
    (NS.stages = NS.stages || []).push(st);      /* checking aid: NESM.stages[0].t = 12; .render() holds a frame */
    st.refresh();
    return st;
  };
})(typeof window !== 'undefined' ? window : globalThis);
