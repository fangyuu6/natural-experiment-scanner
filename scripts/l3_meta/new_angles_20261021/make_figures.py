#!/usr/bin/env python3
"""T232: SVG figures for the four questions (stdlib only; matplotlib is not installed in .venv_did). EXPLORATORY, no site text.
Reads data/l3_meta/new_angles_20261021/{RESULTS_Q1,Q2,Q3,Q5}.json and the csv files; writes fig_q1.svg .. fig_q5.svg next to them."""
import csv, json, sys, math
from pathlib import Path
sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "data/l3_meta/new_angles_20261021"
INK, MUTE, GRID = "#111827", "#6b7280", "#e5e7eb"
BLUE, AMBER, GREY, LIGHT, RED = "#1d4ed8", "#b45309", "#9ca3af", "#d1d5db", "#b91c1c"
FONT = "font-family=\"system-ui,-apple-system,Segoe UI,Helvetica,Arial,sans-serif\""


def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;")


class Svg:
    def __init__(self, w, h):
        self.w, self.h, self.p = w, h, []

    def add(self, s):
        self.p.append(s)

    def text(self, x, y, t, size=12, fill=INK, anchor="start", weight="normal"):
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{fill}" text-anchor="{anchor}" font-weight="{weight}" {FONT}>{esc(t)}</text>')

    def line(self, x1, y1, x2, y2, stroke=GRID, w=1, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{w}"{d}/>')

    def rect(self, x, y, w, h, fill, opacity=1.0, stroke="none"):
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{max(w, 0):.1f}" height="{max(h, 0):.1f}" fill="{fill}" fill-opacity="{opacity}" stroke="{stroke}"/>')

    def circle(self, x, y, r, fill, opacity=1.0, stroke="none"):
        self.add(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="{r}" fill="{fill}" fill-opacity="{opacity}" stroke="{stroke}"/>')

    def poly(self, pts, stroke, w=2, fill="none"):
        self.add(f'<polyline points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in pts)}" fill="{fill}" stroke="{stroke}" stroke-width="{w}" stroke-linejoin="round"/>')

    def save(self, path):
        body = "\n".join(self.p)
        Path(path).write_text(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}">\n<rect width="100%" height="100%" fill="#ffffff"/>\n{body}\n</svg>\n')


def scale(lo, hi, a, b):
    return lambda v: a + (v - lo) / (hi - lo) * (b - a)


def title(s, t1, t2):
    s.text(24, 28, t1, 16, INK, weight="600")
    s.text(24, 48, t2, 12, MUTE)


def fig_q1():
    r = json.load(open(OUT / "RESULTS_Q1.json"))["result"]
    rows = [x for x in csv.DictReader(open(OUT / "q1_design_level.csv")) if x.get("D")]
    D = sorted(float(x["D"]) for x in rows)
    nul = [float(x["share"]) for x in csv.DictReader(open(OUT / "q1_null_draws.csv"))]
    s = Svg(940, 470)
    p = r["primary"]
    title(s, "Pioneers vs copycats: does the order of adoption matter?",
          f"{p['n']} designs (law x outcome), years 0 to 3 after adoption; late minus early group in noise units. EXPLORATORY.")
    # left: sorted dots
    x0, x1, y0, y1 = 70, 520, 90, 380
    xs = scale(0, len(D) - 1, x0, x1); ys = scale(-4, 4, y1, y0)
    for v in (-4, -2, 0, 2, 4):
        s.line(x0, ys(v), x1, ys(v), GRID if v else MUTE, 1)
        s.text(x0 - 8, ys(v) + 4, f"{v:+d}" if v else "0", 11, MUTE, "end")
    for i, v in enumerate(D):
        vv = max(-4, min(4, v))
        s.circle(xs(i), ys(vv), 4, BLUE if v > 0 else AMBER, 0.85)
    s.text(x0, y1 + 28, "each dot is one design, sorted (values beyond +-4 drawn at the edge)", 11, MUTE)
    s.text(x1, ys(3.7), "late did better", 11, BLUE, "end"); s.text(x1, ys(-3.5), "early did better", 11, AMBER, "end")
    s.text(x0 - 55, (y0 + y1) / 2, "", 11)
    # right: null histogram of share
    h0, h1, hy0, hy1 = 600, 910, 90, 380
    bins = [i / 40 for i in range(8, 33)]
    counts = [0] * (len(bins) - 1)
    for v in nul:
        for k in range(len(bins) - 1):
            if bins[k] <= v < bins[k + 1] or (k == len(bins) - 2 and v == bins[-1]):
                counts[k] += 1; break
    mx = max(counts)
    hx = scale(0.2, 0.8, h0, h1); hy = scale(0, mx, hy1, hy0)
    lo, hi = p["null_share_band"]
    s.rect(hx(lo), hy0, hx(hi) - hx(lo), hy1 - hy0, LIGHT, 0.35)
    for k, c in enumerate(counts):
        s.rect(hx(bins[k]) + 1, hy(c), hx(bins[k + 1]) - hx(bins[k]) - 2, hy1 - hy(c), GREY, 0.9)
    s.line(h0, hy1, h1, hy1, MUTE, 1)
    for v in (0.2, 0.35, 0.5, 0.65, 0.8):
        s.text(hx(v), hy1 + 16, f"{int(round(v * 100))}%", 11, MUTE, "middle")
    s.line(hx(p["share_late_better"]), hy0, hx(p["share_late_better"]), hy1, BLUE, 3)
    s.text(hx(p["share_late_better"]) + 6, hy0 + 14, f"real: {p['share_late_better']*100:.0f}%", 12, BLUE, weight="600")
    s.text(h0, y1 + 28, "share of designs where the late group did better", 11, MUTE)
    s.text(h0, 84, "random cohort order, 5,000 shuffles (shaded = 95%)", 11, MUTE)
    s.text(24, 450, f"Median difference {p['median_D']:+.2f} noise units (shuffle band {p['null_median_band'][0]:+.2f} to {p['null_median_band'][1]:+.2f}): both inside, so order carries no detectable information.", 12, INK)
    s.save(OUT / "fig_q1.svg")


def fig_q2():
    r = json.load(open(OUT / "RESULTS_Q2.json"))["result"]
    prof = list(csv.DictReader(open(OUT / "q2_profile_vs_placebo.csv")))
    s = Svg(940, 500)
    title(s, "After adoption: fade, hold or grow?",
          f"{r['n_eligible']} designs followed for six years. Early = years 0 to 2, late = years 3 to 5, in noise units. EXPLORATORY.")
    doms = ["health", "crime", "labor", "other", "public_finance", "environment", "education"]
    cols = {"fade": AMBER, "hold": GREY, "grow": BLUE, "unclear": LIGHT}
    names = {"fade": "fades", "hold": "holds", "grow": "grows", "unclear": "no effect beyond noise"}
    x0, x1 = 150, 520
    y = 100
    mx = max(v["n"] for v in r["by_domain_counts"].values())
    sc = scale(0, mx, 0, x1 - x0)
    s.text(24, 82, "Designs by domain", 12, MUTE)
    for u in doms:
        v = r["by_domain_counts"].get(u)
        if not v:
            continue
        s.text(x0 - 10, y + 15, f"{u.replace('_', ' ')} ({v['n']})", 12, INK, "end")
        x = x0
        for l in ("grow", "hold", "fade", "unclear"):
            w = sc(v[l])
            if w > 0:
                s.rect(x, y, w, 22, cols[l])
                if v[l] >= 2:
                    s.text(x + w / 2, y + 15, str(v[l]), 11, "#ffffff" if l in ("grow", "hold", "fade") else INK, "middle")
            x += w
        y += 34
    lx = x0
    for l in ("grow", "hold", "fade", "unclear"):
        s.rect(lx, y + 8, 12, 12, cols[l]); s.text(lx + 17, y + 18, names[l], 11, MUTE); lx += 14 + len(names[l]) * 6.4 + 20
    oc = r["overall_counts"]
    s.text(24, 440, f"Overall: {oc['grow']} grow, {oc['hold']} hold, {oc['fade']} fade, {oc['unclear']} with no effect beyond noise in either window.", 12, INK)
    s.text(24, 458, "Health designs grow (5 of 18) more often than label shuffling gives (p = .006, 10 comparisons) and more than random timing gives; other domains do not.", 12, INK)
    # right: profile vs placebo
    px0, px1, py0, py1 = 600, 910, 100, 330
    es = [int(x["event_time"]) for x in prof if x["real_median"] and x["placebo_p95"] not in ("", "nan")]
    pe = [x for x in prof if x["placebo_p95"] not in ("", "nan", None) and x["placebo_p95"].lower() != "nan"]
    xs = scale(0, 8, px0, px1); ys = scale(0, 2, py1, py0)
    for v in (0, 0.5, 1, 1.5, 2):
        s.line(px0, ys(v), px1, ys(v), GRID if v else MUTE, 1); s.text(px0 - 6, ys(v) + 4, f"{v:g}", 11, MUTE, "end")
    for e in range(0, 9, 2):
        s.text(xs(e), py1 + 16, str(e), 11, MUTE, "middle")
    s.text((px0 + px1) / 2, py1 + 34, "years since adoption", 11, MUTE, "middle")
    up = [(xs(int(x["event_time"])), ys(float(x["placebo_p95"]))) for x in pe]
    dn = [(xs(int(x["event_time"])), ys(float(x["placebo_p05"]))) for x in pe]
    s.add(f'<polygon points="{" ".join(f"{a:.1f},{b:.1f}" for a, b in up + dn[::-1])}" fill="{LIGHT}" fill-opacity="0.6"/>')
    s.poly([(xs(int(x["event_time"])), ys(float(x["real_median"]))) for x in prof], BLUE, 2.5)
    for x in prof:
        s.circle(xs(int(x["event_time"])), ys(float(x["real_median"])), 3.5, BLUE)
    s.text(px0, 84, "Median effect by year (29 designs)", 12, MUTE)
    s.text(xs(0.2), ys(1.85), "real", 12, BLUE, weight="600"); s.text(xs(5.3), ys(0.45), "random timing", 11, MUTE)
    s.text(px0, py1 + 56, "Grey band: 5th to 95th percentile when timing is randomised (too few cells after year 5).", 11, MUTE)
    s.text(px0, py1 + 72, "Builds for about four years; years 6 to 8 rest on 21 to 26 designs.", 11, MUTE)
    s.save(OUT / "fig_q2.svg")


def fig_q3():
    r = json.load(open(OUT / "RESULTS_Q3.json"))["result"]
    fam = list(csv.DictReader(open(OUT / "q3_by_outcome_family.csv")))
    rs = r["registered_slope"]
    s = Svg(940, 470)
    title(s, "Need and gain: does the change after a law follow the starting gap?",
          f"{rs['n_designs']} designs, {rs['n_series']} outcome series, {rs['n_cells']:,} design-by-series cells. Slope of the effect on the adopters' starting gap. EXPLORATORY.")
    # left: bars real vs placebo for lag 1 and lag 5
    x0, x1, y0, y1 = 70, 440, 100, 330
    ys = scale(-0.5, 0.5, y1, y0)
    for v in (-0.4, -0.2, 0, 0.2, 0.4):
        s.line(x0, ys(v), x1, ys(v), GRID if v else MUTE, 1); s.text(x0 - 6, ys(v) + 4, f"{v:+.1f}" if v else "0", 11, MUTE, "end")
    items = [("gap one year\nbefore adoption", rs["beta"], rs["placebo_slope_mean"], rs["placebo_slope_sd"]),
             ("gap six years\nbefore adoption", rs["lag5"]["beta"], rs["lag5"]["placebo_mean"], None)]
    for k, (lab, real, pm, psd) in enumerate(items):
        cx = x0 + 90 + k * 170
        s.rect(cx - 38, min(ys(0), ys(pm)), 32, abs(ys(pm) - ys(0)), GREY)
        s.rect(cx + 6, min(ys(0), ys(real)), 32, abs(ys(real) - ys(0)), BLUE)
        s.text(cx - 22, ys(pm) + (16 if pm < 0 else -6), f"{pm:+.2f}", 11, MUTE, "middle"); s.text(cx + 22, ys(real) + (-6 if real > 0 else 16), f"{real:+.2f}", 11, BLUE, "middle", "600")
        for i, t in enumerate(lab.split("\n")):
            s.text(cx, y1 + 18 + 14 * i, t, 11, MUTE, "middle")
    s.rect(x0 + 10, 70, 11, 11, GREY); s.text(x0 + 26, 80, "random-label slope (mechanical)", 11, MUTE)
    s.rect(x0 + 230, 70, 11, 11, BLUE); s.text(x0 + 246, 80, "real", 11, BLUE)
    s.text(24, 392, f"Real minus random-label: {rs['excess']:+.2f} ({rs['excess_in_null_sd']:.1f} random-label SDs). Dropping any one policy family keeps it", 12, INK)
    s.text(24, 410, f"between {r['leave_one_policy_family_out']['excess_sd_min']:.1f} and {r['leave_one_policy_family_out']['excess_sd_max']:.1f} SDs. Earlier run on 28 designs: {r['earlier_run_r4']['excess_in_null_sd']:.1f} SDs.", 12, INK)
    # right: by outcome family
    fx0, fx1, fy = 700, 910, 100
    xs = scale(-2, 5, fx0, fx1)
    s.text(600, 84, "Excess by outcome family (random-label SDs)", 12, MUTE)
    fam = sorted(fam, key=lambda x: -float(x["excess_in_null_sd"]))
    for v in (-2, 0, 2, 4):
        s.line(xs(v), fy - 6, xs(v), fy + 21 * len(fam), GRID if v else MUTE, 1); s.text(xs(v), fy + 21 * len(fam) + 14, f"{v:g}", 11, MUTE, "middle")
    s.line(xs(2), fy - 6, xs(2), fy + 21 * len(fam), AMBER, 1, "4 3")
    for i, f in enumerate(fam):
        yy = fy + 21 * i + 10
        e = float(f["excess_in_null_sd"])
        s.text(fx0 - 10, yy + 4, f["outcome_family"].replace("_", " "), 11, INK, "end")
        s.circle(xs(max(-2, min(5, e))), yy, 5, BLUE if e >= 2 else GREY)
    s.save(OUT / "fig_q3.svg")


def fig_q5():
    r = json.load(open(OUT / "RESULTS_Q5.json"))["result"]
    s = Svg(940, 400)
    title(s, "Party and ideology at adoption versus delivery, inside each design",
          f"{r['n_designs_with_state_map_and_ge3_cohorts']} designs with state lists. Difference in effect (noise units) between the compared groups of adopting cohorts. EXPLORATORY.")
    rowsd = [("Unified Democratic vs unified Republican", r["cut_A"]["primary"]), ("More liberal half vs less liberal half", r["cut_B"]["primary"]),
             ("Farther vs closer to the first adopters' ideology", r["cut_C"]["primary"])]
    x0, x1 = 360, 900
    xs = scale(-2, 2, x0, x1)
    for v in (-2, -1, 0, 1, 2):
        s.line(xs(v), 80, xs(v), 300, GRID if v else MUTE, 1); s.text(xs(v), 316, f"{v:+d}" if v else "0", 11, MUTE, "middle")
    for i, (lab, d) in enumerate(rowsd):
        y = 120 + i * 62
        s.text(24, y + 4, lab, 12, INK)
        s.text(24, y + 20, f"{d['n_designs']} designs; {d['n_cohorts_class_plus']} vs {d['n_cohorts_class_minus']} cohorts", 11, MUTE)
        lo, hi = d["null_band"]
        s.rect(xs(lo), y - 12, xs(hi) - xs(lo), 24, LIGHT, 0.7)
        v = max(-2, min(2, d["pooled_diff"]))
        s.circle(xs(v), y, 6, BLUE if d["outside_band"] else INK)
        s.text(xs(v), y - 18, f"{d['pooled_diff']:+.2f}", 11, INK, "middle", "600")
    s.text(x0, 346, "grey = band of 2,000 shuffles of group labels inside each design (95%)", 11, MUTE)
    s.text(24, 380, "All three dots sit inside their bands: no detectable link between who governed, or state ideology, and delivery.", 12, INK)
    s.save(OUT / "fig_q5.svg")


if __name__ == "__main__":
    fig_q1(); fig_q2(); fig_q3(); fig_q5()
    print("figures written")
