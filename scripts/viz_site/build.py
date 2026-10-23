#!/usr/bin/env python3
"""Render the bilingual visualization pages (stdlib only, Python 3.9+).

    python3 scripts/viz_site/build.py                      # every page, both languages
    python3 scripts/viz_site/build.py --lang zh            # one language
    python3 scripts/viz_site/build.py --page funnel        # one page
    python3 scripts/viz_site/build.py --check              # validate only, write nothing
    python3 scripts/viz_site/build.py --page worked_examples
                                                          # production examples; real batch-2 sidecars in scripts/did_samples/results/cases
    python3 scripts/viz_site/build.py --page worked_examples --check
                                                          # validate the same production examples, write nothing
    python3 scripts/viz_site/build.py --preview-home       # ONLY write deploy_output/_landing_options/final_{en,zh}.html
    python3 scripts/viz_site/build.py --inject-home        # ONLY re-render the EN story block inside deploy_output/policy_db.html
    python3 scripts/viz_site/build.py --output-dir /tmp/site-preview --inject-home
                                                          # update EN/ZH home stories in an isolated copy (no deploy_output writes)
    python3 scripts/viz_site/build.py --fixtures --output-dir /private/tmp/w7a-worked-examples
                                                          # invented examples, isolated HTML output
    python3 scripts/viz_site/build.py --case-fixtures /tmp/cases --output-dir /tmp/case-preview
                                                          # synthetic case sidecars, isolated HTML output

Input : scripts/viz_site/pages/<name>.html            page templates
        scripts/viz_site/partials/<name>.html         shared fragments
        scripts/viz_site/i18n/<name>.{en,zh}.json     flat {key: string} dictionaries
        deploy_output/<name>.json                     data, inlined at build time
Output: deploy_output/viz/<lang>/<name>.html          served at /viz/<lang>/<name>.html
        --output-dir changes the HTML root; data inputs remain in deploy_output.
        --inject-home with an isolated root copies and updates EN and ZH homepages there.
        Worked examples/nav/home entry require a PUBLISHABLE_EXAMPLE result and publishable primary runs.
        Fixture mode reads only scripts/did_samples/fixtures and cannot output inside deploy_output.

Placeholders (single pass; inserted text is never re-scanned)
    {{t:key}}          HTML-escaped dictionary string; keys ending _html are inserted raw
    {{t_json}}         whole merged dictionary as a JSON object for inline script:  const T = {{t_json}};
    {{data:name}}      minified JSON of deploy_output/<name>.json for inline script (ASCII only, </ escaped)
    {{partial:name}}   partials/<name>.html, rendered recursively with the same language
    {{css:name}}       scripts/viz_site/design/<name>.css inlined (CSS comments removed): the design tokens of the homepage story
                       ({{css:design_tokens}}), so the page carries one copy of the tokens and a token edit shows up in --check
    {{nav:name}}       attributes for a view-toggle link: class="vt-btn vt-active" aria-current="page"
                       when <name> is the page being built, else class="vt-btn vt-link"
    {{lang}} {{html_lang}}   en | zh-CN (for <html lang>)        {{lang_code}}  en | zh (for URLs)
    {{page}}           page name
    {{self_url}} {{other_url}}   root-absolute URL of this page / of the same page in the other language
    {{lang_switch}}    the EN/中 pill toggle (globe icon + two labels, the current language highlighted) linking to
                       {{other_url}}; used by partials/header.html, pages never need it directly.
                       Its title/aria-label contain CJK on purpose, so the EN CJK lint skips this exact markup.

    {{num:<expr>}} {{num:<expr>|<fmt>}}
                       a figure computed from deploy_output/<file>.json at build time (never typed into a template).
                       <expr> = numbers, data paths, + - * / ( ), and the functions below. A path starts with the JSON
                       file name: funnel_data.total | funnel_data.stages.5.n_out | funnel_data.stages[key=min_gate].n_out
                       ([field=value] picks the one list item whose field equals value; in a path an underscore also
                       matches a space in an object key: by_source_family.Federal_Regulations.n.5).
                       len(path)  count(path, field)  countnot(path, field)  -> numbers
                       Formats (as |fmt or wrapped around a sub-expression): int (default, 483,414) | k (483k, 7.8k; zh
                       uses 万 from 10,000 up) | pct0 pct1 (fractions -> 1.6%) | dec1 dec2 | raw (2016, no separator) |
                       text(path) (a string value). A missing file, path or selector fails the build.
                       fam(<text expr>) a policy family id shown as a readable name: the dictionary key map.family.<id> when it
                       exists, else the id with underscores turned into spaces and capitalised (property_tax_exemption ->
                       Property tax exemption). Only usable in {{tf:..}} slots, e.g. family=fam(text(adoption_data.families[family=x].family)).
    {{tf:key|slot=<expr>|slot=<expr>}}
                       dictionary string with {slot} fields filled from expressions (HTML-escaped); slots and arguments
                       must match exactly. Use it whenever a sentence carries figures, so word order stays per language.

    {{if:github}} ... {{endif}}
                       markup kept only when the top-level constant SHOW_GITHUB in this file is True (currently False):
                       one switch for every GitHub link / "MIT License" wording in header, footer and home_story.
    {{if:examples}} ... {{endif}}   present only when eligible worked examples exist.
    {{if:policy_exposure}} ... {{endif}}   present when the page and data exist, outside synthetic fixture mode.
    {{if:fixtures}} ... {{endif}}   local synthetic-preview label and noindex tag.
    {{worked_examples}}            summary, stopped notes and one section per shown policy.
    {{example:field}}              escaped result field or generated static SVG/table inside that card template.

Dictionaries merge as common < (each partial used, in order of first use) < page; the page wins.
Do not write {{something}} in page JavaScript (object literals inside template strings): put a space
between the braces, because any leftover {{word}} or {{word:key}} is reported as an unresolved placeholder.

Exit codes: 0 ok | 1 build errors (all listed) | 2 bad command line.
Fails on: unresolved placeholder, unknown data/partial/dictionary key, en/zh key sets that differ
(per file and per page), CJK in an EN value or in EN output (except the language-switch label),
'DEV FALLBACK' or a fetch() of a .json file in the output.
Warns on: Latin-only zh values longer than 25 characters.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from html import escape
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEPLOY = ROOT / "deploy_output"
PAGES_DIR = HERE / "pages"
PARTIALS_DIR = HERE / "partials"
I18N_DIR = HERE / "i18n"
OUT_DIR = DEPLOY / "viz"
CSS_DIR = ROOT / "scripts" / "viz_site" / "design"   # {{css:name}} -> scripts/viz_site/design/<name>.css (committed; the spec lives next to it)
CSS_COMMENT = re.compile(r"/\*.*?\*/", re.S)

# One-line switch for EVERY GitHub link and the "MIT License" wording that the partials render (header, footer, homepage
# story block). False = hidden everywhere (the repository may be private: the link would 404 for visitors).
# Flip to False to hide them all again; nothing else needs editing. Markup is fenced with {{if:github}} ... {{endif}}.
# V5 / owner decision 22: the public materials repository is not published yet.
# Keep private repository and license URLs out of public-facing page chrome.
SHOW_GITHUB = False

RESULTS_DIR = ROOT / "scripts/did_samples/results"
FIXTURES_DIR = ROOT / "scripts/did_samples/fixtures"
FIXTURE_MODE = False
CASE_FIXTURES = None
_examples_cache = None


def load_worked_examples(results_dir=None, *, fixtures=False):
    """Return presentation objects through the single result-schema adapter."""
    from scripts.did_samples.make_site_assets import adapt_worked_examples
    directory = Path(results_dir) if results_dir is not None else (FIXTURES_DIR if fixtures else RESULTS_DIR)
    return adapt_worked_examples(directory, fixtures=fixtures)["examples"]


def worked_example_selection():
    global _examples_cache
    if _examples_cache is None:
        from scripts.did_samples.make_site_assets import adapt_worked_examples
        directory = FIXTURES_DIR if FIXTURE_MODE else RESULTS_DIR
        from scripts.viz_site.make_case_assets import load_case_selection, merge_case_selection
        batch1 = adapt_worked_examples(directory, fixtures=FIXTURE_MODE)
        # Fixture builds never discover future real case sidecars.
        case_dir = CASE_FIXTURES if CASE_FIXTURES is not None else (FIXTURES_DIR / "cases" if FIXTURE_MODE else RESULTS_DIR / "cases")
        cases = load_case_selection(case_dir)
        if CASE_FIXTURES is not None and any(c["data_kind"] != "synthetic" for c in cases["cases"]):
            raise ValueError("--case-fixtures accepts synthetic sidecars only")
        _examples_cache = merge_case_selection(batch1, cases)
    return _examples_cache


def worked_examples():
    return worked_example_selection()["examples"]


def has_examples():
    return bool(worked_examples() or worked_example_selection().get("cases"))


def worked_fixture_mode():
    return FIXTURE_MODE or (CASE_FIXTURES is not None and bool(worked_example_selection().get("cases")))


DESIGN_DATA = HERE / "data/design_opportunity.json"


def has_design_opportunity():
    return not FIXTURE_MODE and (PAGES_DIR / "design_opportunity.html").is_file() and DESIGN_DATA.is_file()


def has_policy_exposure():
    # Synthetic example previews keep their existing navigation and never link to real exposure data.
    return not FIXTURE_MODE and CASE_FIXTURES is None and (PAGES_DIR / "policy_exposure.html").is_file() and (HERE / "data/policy_exposure.json").is_file()


def render_design_opportunity(ctx, errs):
    from scripts.viz_site.design_opportunity_adapter import render_design_opportunity as adapt
    try:
        payload = json.loads(DESIGN_DATA.read_text(encoding="utf-8"))
        if not has_examples():
            payload = dict(payload, run_evidence=[])
        return adapt(payload, ctx["lang"], ctx["strings"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errs.error("design opportunity: " + str(exc))
        return ""


def render_treatment_timing(ctx, errs):
    if worked_fixture_mode():
        return ""
    from scripts.viz_site.policy_exposure_adapter import render_treatment_timing as adapt
    try:
        payload = json.loads((HERE / "data/policy_exposure.json").read_text(encoding="utf-8"))
        strings = dict(load_dict("policy_exposure", ctx["lang"], errs) or {})
        strings.update(ctx["strings"])
        return adapt(payload, ctx["lang"], strings)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errs.error("Treatment timing [%s]: %s" % (ctx["lang"], exc))
        return ""


def render_map_sources(ctx, errs):
    from scripts.viz_site.design_opportunity_adapter import render_map_sources as adapt
    try:
        return adapt(json.loads(DESIGN_DATA.read_text(encoding="utf-8")), ctx["lang"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errs.error("Map sources [%s]: %s" % (ctx["lang"], exc))
        return ""


def render_policy_exposure(ctx, errs):
    from scripts.viz_site.policy_exposure_adapter import render_policy_exposure as adapt
    try:
        payload = json.loads((HERE / "data/policy_exposure.json").read_text(encoding="utf-8"))
        return adapt(payload, ctx["lang"], ctx["strings"])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errs.error("Policy exposure [%s]: %s" % (ctx["lang"], exc))
        return ""


def render_policy_exposure_records(ctx, errs):
    from scripts.viz_site.policy_exposure_adapter import render_policy_exposure_records as adapt
    try:
        payload = json.loads((HERE / "data/policy_exposure.json").read_text(encoding="utf-8"))
        strings = dict(load_dict("policy_exposure", ctx["lang"], errs) or {})
        strings.update(ctx["strings"])
        return adapt(payload, ctx["lang"], strings)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        errs.error("Policy exposure records [%s]: %s" % (ctx["lang"], exc))
        return ""


LANGS = ("en", "zh")
HTML_LANG = {"en": "en", "zh": "zh-CN"}
OTHER = {"en": "zh", "zh": "en"}
SWITCH_TITLE = "Switch language \u00b7 \u5207\u6362\u8bed\u8a00"


def chrome_features():
    return dict(github=SHOW_GITHUB, examples=has_examples(),
                design_opportunity=has_design_opportunity(), policy_exposure=has_policy_exposure())


def lang_switch_html(lang: str, page: str) -> str:
    from scripts.viz_site.shared_chrome import language_switch
    return language_switch(lang, page)


CJK = re.compile("[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\u3000-\u303f\uff00-\uffef]")
LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
NAME = r"[A-Za-z0-9_.\-]+"
SIMPLE = ("t_json", "lang", "html_lang", "lang_code", "page", "lang_switch", "other_url", "self_url", "worked_examples", "l2_additions", "l2_gates", "l2_noise", "example_mode", "design_opportunity", "policy_exposure", "policy_exposure_records", "treatment_timing", "map_sources", "v5_estimates", "v5_progress", "v5_screening", "v5_explore", "v5_home")
PLACEHOLDER = re.compile(
    r"\{\{(?:(t|data|partial|nav|css|example):(" + NAME + r")|(" + "|".join(SIMPLE) + r")"
    r"|(num):([^{}|]+?)(?:\|([a-z][a-z0-9]*))?"
    r"|(tf):(" + NAME + r")((?:\|[^{}|]*)*))\}\}")
LEFTOVER = re.compile(r"\{\{\s*[A-Za-z_][A-Za-z0-9_]*\s*(?::\s*" + NAME + r")?\s*\}\}")
LEFTOVER_CALC = re.compile(r"\{\{\s*(?:num|tf)\s*:[^{}]*\}\}")          # a malformed num/tf placeholder
SLOT = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")
COND = re.compile(r"\{\{if:(github|examples|fixtures|design_opportunity|policy_exposure)\}\}(.*?)\{\{endif\}\}", re.S)
PARTIAL_REF = re.compile(r"\{\{partial:(" + NAME + r")\}\}")
HTML_COMMENT = re.compile(r"<!--.*?-->[ \t]*\n?", re.S)
FETCH_JSON = re.compile(r"fetch\s*\(\s*[^)]*?\.json", re.S)


class Errors:
    def __init__(self):
        self.errors, self.warnings = [], []

    def error(self, msg):
        if msg not in self.errors:
            self.errors.append(msg)

    def warn(self, msg):
        if msg not in self.warnings:
            self.warnings.append(msg)


# --------------------------------------------------------------------------- dictionaries

_dict_cache: dict = {}


def load_dict(name: str, lang: str, errs: Errors):
    """Flat {key: str} from i18n/<name>.<lang>.json, or None when the file does not exist."""
    key = (name, lang)
    if key in _dict_cache:
        return _dict_cache[key]
    path = I18N_DIR / ("%s.%s.json" % (name, lang))
    result = None
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            errs.error("%s: invalid JSON (%s)" % (path.name, exc))
            data = {}
        if not isinstance(data, dict):
            errs.error("%s: top level must be a JSON object" % path.name)
            data = {}
        result = {}
        for k, v in data.items():
            if not isinstance(v, str):
                errs.error("%s: value of %r must be a string" % (path.name, k))
                continue
            result[k] = v
    _dict_cache[key] = result
    return result


def check_dict_pair(name: str, errs: Errors):
    """Both language files must exist (or neither) and carry identical key sets; lint the values."""
    en, zh = load_dict(name, "en", errs), load_dict(name, "zh", errs)
    if en is None and zh is None:
        return
    if en is None or zh is None:
        errs.error("i18n/%s: only the %s file exists" % (name, "zh" if en is None else "en"))
        return
    for k in sorted(set(en) - set(zh)):
        errs.error("i18n/%s: key %r missing in zh" % (name, k))
    for k in sorted(set(zh) - set(en)):
        errs.error("i18n/%s: key %r missing in en" % (name, k))
    for k, v in en.items():
        if CJK.search(v):
            errs.error("i18n/%s.en: CJK character in value of %r" % (name, k))
    for k, v in zh.items():
        if not CJK.search(v) and len(v) > 25 and LATIN_WORD.search(v):
            errs.warn("i18n/%s.zh: Latin-only value (%d chars) for %r" % (name, len(v), k))


# --------------------------------------------------------------------------- templates

def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig").replace("\r\n", "\n")


def partials_used(text: str, errs: Errors, seen=None, stack=()) -> list:
    """Partial names referenced by text, recursively, in order of first use."""
    seen = [] if seen is None else seen
    for m in PARTIAL_REF.finditer(text):
        name = m.group(1)
        if name in stack:
            errs.error("partial cycle: %s" % " -> ".join(stack + (name,)))
            continue
        path = PARTIALS_DIR / (name + ".html")
        if not path.is_file():
            continue                                   # reported during rendering
        if name not in seen:
            seen.append(name)
        partials_used(HTML_COMMENT.sub("", read_text(path)), errs, seen, stack + (name,))
    return seen


def json_for_script(obj, ascii_only: bool) -> str:
    s = json.dumps(obj, ensure_ascii=ascii_only, separators=(",", ":"))
    return (s.replace("</", "<\\/").replace("<!--", "<\\!--")
             .replace("\u2028", "\\u2028").replace("\u2029", "\\u2029"))


_data_cache: dict = {}


def load_data(name: str, errs: Errors):
    if name not in _data_cache:
        path = (HERE / "data" if name == "l2_counts" else DEPLOY) / (name + ".json")
        if not path.is_file():
            errs.error("data file missing: deploy_output/%s.json" % name)
            _data_cache[name] = None
        else:
            try:
                _data_cache[name] = json.loads(path.read_text(encoding="utf-8-sig"))
            except ValueError as exc:
                errs.error("deploy_output/%s.json: invalid JSON (%s)" % (name, exc))
                _data_cache[name] = None
    return _data_cache[name]


# --------------------------------------------------------------------------- computed figures ({{num:..}}, {{tf:..}})

class ExprError(Exception):
    pass


_SEL = r"(?:\[[A-Za-z_][A-Za-z0-9_]*=[A-Za-z0-9_\-]+\])?"
_EXPR_TOKEN = re.compile(
    r"\s*(?:(?P<num>\d+(?:\.\d+)?)"
    r"|(?P<path>[A-Za-z_][A-Za-z0-9_]*" + _SEL + r"(?:\.[A-Za-z0-9_]+" + _SEL + r")*)"
    r"|(?P<op>[-+*/(),]))")
_SEGMENT = re.compile(r"^([A-Za-z0-9_]+)(?:\[([A-Za-z_][A-Za-z0-9_]*)=([A-Za-z0-9_\-]+)\])?$")
FORMATS = ("int", "k", "pct0", "pct1", "dec1", "dec2", "raw")


def resolve_path(path: str, errs: Errors):
    """Value at 'file.key.0.key[field=value].key' inside deploy_output/<file>.json; ExprError when absent."""
    parts = path.split(".")
    cur = load_data(parts[0], errs)
    if cur is None:
        raise ExprError("data file deploy_output/%s.json is missing or invalid" % parts[0])
    walked = parts[0]
    for seg in parts[1:]:
        m = _SEGMENT.match(seg)
        if not m:
            raise ExprError("bad path segment %r in %s" % (seg, path))
        key, sel_field, sel_value = m.group(1), m.group(2), m.group(3)
        if isinstance(cur, dict) and key in cur:
            cur = cur[key]
        elif isinstance(cur, dict) and key.replace("_", " ") in cur:      # funnel_data.by_source_family.Federal_Regulations
            cur = cur[key.replace("_", " ")]
        elif isinstance(cur, list) and key.isdigit() and int(key) < len(cur):
            cur = cur[int(key)]
        else:
            raise ExprError("no %r under %s" % (key, walked))
        walked += "." + key
        if sel_field:
            if not isinstance(cur, list):
                raise ExprError("%s is not a list, cannot select [%s=%s]" % (walked, sel_field, sel_value))
            hits = [x for x in cur if isinstance(x, dict) and str(x.get(sel_field)) == sel_value]
            if len(hits) != 1:
                raise ExprError("%s[%s=%s] matches %d items (need exactly 1)" % (walked, sel_field, sel_value, len(hits)))
            cur = hits[0]
            walked += "[%s=%s]" % (sel_field, sel_value)
    return cur


def _tokenize(expr: str):
    toks, pos = [], 0
    while pos < len(expr):
        if not expr[pos:].strip():
            break
        m = _EXPR_TOKEN.match(expr, pos)
        if not m:
            raise ExprError("cannot read %r" % expr[pos:pos + 20].strip())
        pos = m.end()
        toks.append((m.lastgroup, m.group(m.lastgroup)))
    return toks


def _group_int(v: float) -> str:
    return "{:,}".format(int(round(v)))


def format_number(v, fmt: str, lang: str) -> str:
    if isinstance(v, str):
        return v
    if fmt == "int":
        return _group_int(v)
    if fmt == "raw":
        return str(int(v)) if float(v).is_integer() else repr(float(v))
    if fmt in ("pct0", "pct1"):
        return "%.*f%%" % (0 if fmt == "pct0" else 1, v * 100)
    if fmt in ("dec1", "dec2"):
        return "%.*f" % (1 if fmt == "dec1" else 2, v)
    if fmt == "k":
        if lang == "zh" and v >= 1e4:
            s = "%.1f" % (v / 1e4)
            return (s[:-2] if s.endswith(".0") else s) + "万"
        if lang == "zh" or v < 1e3:
            return _group_int(v)
        if v >= 1e5:
            return "%dk" % round(v / 1e3)
        s = "%.1f" % (v / 1e3)
        return (s[:-2] if s.endswith(".0") else s) + "k"
    raise ExprError("unknown format %r (use %s)" % (fmt, ", ".join(FORMATS)))


class _Expr:
    """expr := term (('+'|'-') term)* ; term := factor (('*'|'/') factor)* ;
    factor := NUMBER | '-' factor | '(' expr ')' | FUNC '(' args ')' | PATH. No eval(); values are int/float/str."""

    def __init__(self, text: str, lang: str, errs: Errors, strings=None):
        self.toks, self.i, self.lang, self.errs = _tokenize(text), 0, lang, errs
        self.strings = strings or {}

    def peek(self):
        return self.toks[self.i] if self.i < len(self.toks) else (None, None)

    def take(self, kind=None, val=None):
        k, v = self.peek()
        if k is None or (kind and k != kind) or (val and v != val):
            raise ExprError("unexpected %s" % ("end of expression" if k is None else repr(v)))
        self.i += 1
        return v

    def parse(self):
        v = self.expr()
        if self.i != len(self.toks):
            raise ExprError("unexpected %r" % self.peek()[1])
        return v

    def number(self, v):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise ExprError("value %r is not a number" % (v if not isinstance(v, (dict, list)) else type(v).__name__))
        return v

    def expr(self):
        v = self.term()
        while self.peek() in (("op", "+"), ("op", "-")):
            op = self.take()
            w = self.number(self.term())
            v = self.number(v) + w if op == "+" else self.number(v) - w
        return v

    def term(self):
        v = self.factor()
        while self.peek() in (("op", "*"), ("op", "/")):
            op = self.take()
            w = self.number(self.factor())
            if op == "/" and w == 0:
                raise ExprError("division by zero")
            v = self.number(v) * w if op == "*" else self.number(v) / w
        return v

    def factor(self):
        kind, val = self.peek()
        if kind == "num":
            self.i += 1
            return float(val) if "." in val else int(val)
        if kind == "op" and val == "-":
            self.i += 1
            return -self.number(self.factor())
        if kind == "op" and val == "(":
            self.i += 1
            v = self.expr()
            self.take("op", ")")
            return v
        if kind == "path":
            self.i += 1
            if self.peek() == ("op", "("):
                return self.call(val)
            return resolve_path(val, self.errs)
        raise ExprError("unexpected %s" % ("end of expression" if kind is None else repr(val)))

    def call(self, name: str):
        self.take("op", "(")
        if name == "example_count":
            self.take("op", ")")
            # One primary estimation case per registered policy, including stopped cases.
            return worked_example_selection()["prepared"]
        if name in ("len", "count", "countnot"):
            container = resolve_path(self.take("path"), self.errs)
            field = None
            if name != "len":
                self.take("op", ",")
                field = self.take("path")
            self.take("op", ")")
            items = list(container.values()) if isinstance(container, dict) else container
            if not isinstance(items, list):
                raise ExprError("%s() needs a list or an object" % name)
            if name == "len":
                return len(items)
            hit = sum(1 for x in items if isinstance(x, dict) and field in x and bool(x[field]))
            if not all(isinstance(x, dict) and field in x for x in items):
                raise ExprError("%s(): not every item has field %r" % (name, field))
            return hit if name == "count" else len(items) - hit
        if name == "text":
            v = resolve_path(self.take("path"), self.errs)
            self.take("op", ")")
            if not isinstance(v, str):
                raise ExprError("text() needs a string value")
            return v
        if name == "fam":
            v = self.expr()
            self.take("op", ")")
            if not isinstance(v, str) or not v:
                raise ExprError("fam() needs a non-empty text value (a family id)")
            readable = self.strings.get("map.family." + v)
            return readable if readable else v.replace("_", " ").capitalize()
        if name in FORMATS:
            v = self.number(self.expr())
            self.take("op", ")")
            return format_number(v, name, self.lang)
        raise ExprError("unknown function %s()" % name)


def compute(expr: str, fmt, lang: str, errs: Errors, strings=None) -> str:
    """Evaluate one expression and format it (raises ExprError). strings = the merged dictionary (for fam())."""
    v = _Expr(expr, lang, errs, strings).parse()
    if isinstance(v, bool) or isinstance(v, (dict, list)) or v is None:
        raise ExprError("the value is %s, not a number or text" % ("missing" if v is None else type(v).__name__))
    if isinstance(v, str):
        if fmt:
            raise ExprError("a text value takes no format")
        return v
    if fmt is None and not float(v).is_integer():
        raise ExprError("%r is not a whole number: add a format (%s)" % (v, ", ".join(FORMATS)))
    return format_number(v, fmt or "int", lang)


def render_l2_extension(name, lang):
    if worked_fixture_mode() or not worked_example_selection().get("cases"):
        return ""
    from scripts.viz_site import make_l2_rows
    return getattr(make_l2_rows, name)(lang)


def render_worked_examples(ctx, errs):
    from scripts.did_samples.make_site_assets import render_policy_fields, render_selection_fields, render_closing_fields
    template = HTML_COMMENT.sub("", read_text(PARTIALS_DIR / "worked_examples_body.html"))
    cards = []
    try:
        if worked_example_selection().get("cases"):
            html = render_case_page(worked_example_selection(), ctx, errs, template)
            if not worked_fixture_mode():
                # The unified Estimates table already contains these same outcomes.
                html = re.sub(r'<p class="we-selection">.*?</p>', '', html, count=1, flags=re.S)
                html = re.sub(r'<table class="we-summary we-case-summary">.*?</table>', '', html, count=1, flags=re.S)
                heading = 'Event studies' if ctx['lang'] == 'en' else '事件研究'
                summary = 'View 10 event studies' if ctx['lang'] == 'en' else '查看 10 项事件研究'
                html = '<h2 id="event-studies">' + heading + '</h2><details class="we-event-studies"><summary>' + summary + '</summary>' + html + '</details>'
            return html
        cards.append(render_selection_fields(worked_example_selection(), lang=ctx["lang"], strings=ctx["strings"]))
        for example in worked_examples():
            card = render_policy_fields(example, lang=ctx["lang"], strings=ctx["strings"])
            cards.append(render(template, dict(ctx, example=card), errs, "partials/worked_examples_body.html"))
        cards.append(render_closing_fields(worked_example_selection(), lang=ctx["lang"], strings=ctx["strings"]))
    except (ValueError, KeyError, TypeError) as exc:
        errs.error("worked examples: %s" % exc)
    return "\n".join(cards)


def lean_event_svg(svg):
    """One responsive canvas: fixed-size labels/marks, stretched plot geometry.

    The existing renderer validates the evidence and chooses segments.
    Only pixel coordinates and repeated styling change in this projection.
    A symbol scales bands horizontally while circles and type keep their size.
    """
    import xml.etree.ElementTree as ET

    def pixel(value):
        return format(round(float(value), 1), '.1f').rstrip('0').rstrip('.') or '0'

    root = ET.fromstring(svg)
    for node in root.iter():
        node.tag = node.tag.rsplit('}', 1)[-1]
    width = float(root.attrib['width'])
    left, span = 48, width - 60
    root.attrib.update(width='100%', height='222')
    root.attrib.pop('viewBox', None)
    root.attrib.pop('preserveAspectRatio', None)
    root.attrib.pop('xmlns', None)
    root.set('xmlns', 'http://www.w3.org/2000/svg')
    geometry_id = root.attrib['id'] + '-geometry'
    defs = ET.Element('defs')
    geometry = ET.SubElement(defs, 'symbol', id=geometry_id,
        viewBox=f'48 0 {pixel(span)} 222', preserveAspectRatio='none', overflow='visible')
    points, labels, errorbars = [], [], {}

    def responsive_x(value):
        fraction = (float(value) - left) / span
        # Percent precision preserves subpixel x positions at every viewport.
        return format(fraction * 100, '.3f').rstrip('0').rstrip('.'), pixel(left - 60 * fraction)

    def styles(node):
        classes = node.attrib.get('class', '').split()
        color = node.attrib.pop('stroke', None)
        fill = node.attrib.pop('fill', None)
        if color in ('#8793a6', '#1d4ed8') or fill in ('#8793a6', '#1d4ed8'):
            classes.append('we-pre' if '#8793a6' in (color, fill) else 'we-post')
        if node.tag == 'line' and color == '#e5e7eb':
            classes.append('we-grid')
        if fill == '#ffffff':
            classes.append('we-hollow')
        if node.tag == 'text' and fill == '#4b5563':
            classes.append('we-muted')
        for key in ('stroke-width', 'fill-opacity', 'stroke-dasharray'):
            node.attrib.pop(key, None)
        if node.tag in ('line', 'path', 'polyline'):
            node.set('vector-effect', 'non-scaling-stroke')
        if classes:
            node.set('class', ' '.join(classes))
        for key in ('x', 'y', 'x1', 'x2', 'y1', 'y2', 'cx', 'cy', 'r'):
            if key in node.attrib:
                node.set(key, pixel(node.attrib[key]))
        if 'points' in node.attrib:
            node.set('points', re.sub(r'-?\d+(?:\.\d+)?', lambda m: pixel(m[0]), node.attrib['points']))
        if 'd' in node.attrib:
            node.set('d', re.sub(r'-?\d+(?:\.\d+)?', lambda m: pixel(m[0]), node.attrib['d']))

    group = root.find('g')
    # Label multiples of the step, including adoption year 0. Two static label
    # sets retain readable gaps on phones without changing any plot geometry.
    times = [int(n.attrib['data-event-time']) for n in group.findall('g')
             if 'data-event-time' in n.attrib]
    if times:
        first, last = min(times), max(times)
        for node in list(group):
            if node.tag == 'text' and node.attrib.get('y') == '194':
                group.remove(node)

        def ticks(max_intervals):
            needed = max(1, math.ceil((last - first) / max_intervals))
            power = 10 ** math.floor(math.log10(needed))
            step = next(f * power for f in (1, 2, 5, 10) if f * power >= needed)
            return set(range(math.ceil(first / step) * step, last + 1, step))

        wide, narrow = ticks(7), {0}
        # At 320px the canvas has 297.6px, with a 237.6px plotting span.
        # Reserve 28px between label edges using a conservative glyph width.
        def phone_x(time):
            return (time - first) / (last - first) * 237.6 if last != first else 118.8

        for time in sorted(wide - narrow, key=lambda t: (abs(t), t)):
            if all(abs(phone_x(time) - phone_x(other)) >=
                   28 + (len(str(time)) + len(str(other))) * 8.5 / 2 for other in narrow):
                narrow.add(time)
        for time in sorted(wide | narrow):
            classes = ['we-x-tick']
            if time in wide:
                classes.append('we-x-wide')
            if time in narrow:
                classes.append('we-x-narrow')
            xx = left + (time - first) / (last - first) * span if last != first else left + span / 2
            ET.SubElement(group, 'text', {'x': str(xx), 'y': '194',
                'text-anchor': 'middle', 'class': ' '.join(classes)}).text = str(time).replace('-', '−')
    for node in list(group):
        if node.tag == 'text':
            if float(node.attrib['x']) > 42:
                percent, offset = responsive_x(node.attrib['x'])
                node.set('x', percent + '%')
                node.set('dx', offset)
            # y and fixed x positions are pixel coordinates; percentage x stays exact.
            for key in ('y', 'dx'):
                if key in node.attrib:
                    node.set(key, pixel(node.attrib[key]))
            fill = node.attrib.pop('fill', None)
            if fill == '#4b5563':
                node.set('class', (node.attrib.get('class', '') + ' we-muted').strip())
            labels.append(node)
        elif node.tag == 'g':
            for mark in list(node):
                if mark.tag == 'circle':
                    percent, offset = responsive_x(mark.attrib['cx'])
                    styles(mark)
                    mark.attrib.pop('cx')
                    mark.set('style', f'cx:calc({percent}% + {offset}px)')
                    points.append(mark)
                else:
                    # Bars/caps and any recorded partial-period diamond keep
                    # their original geometry in the scalable symbol.
                    styles(mark)
                    if mark.tag == 'line':
                        color = mark.attrib['class']
                        errorbars.setdefault(color, []).append(
                            'M{0[x1]} {0[y1]}H{0[x2]}' .format(mark.attrib)
                            if mark.attrib['y1'] == mark.attrib['y2'] else
                            'M{0[x1]} {0[y1]}V{0[y2]}'.format(mark.attrib))
                    else:
                        geometry.append(mark)
        else:
            styles(node)
            geometry.append(node)
    for color, commands in errorbars.items():
        ET.SubElement(geometry, 'path', {'class': 'we-errorbars ' + color, 'd': ''.join(commands),
                                      'vector-effect': 'non-scaling-stroke'})
    root.remove(group)
    background = root.find('rect')
    if background is not None:
        root.remove(background)
    canvas = ET.Element('svg', {'xmlns': 'http://www.w3.org/2000/svg',
        'class': 'we-chart-geometry', 'aria-hidden': 'true', 'height': '222'})
    canvas.append(defs)
    ET.SubElement(canvas, 'use', href='#' + geometry_id, width='100%', height='100%')
    root.extend(points)
    root.extend(labels)
    # Strip renderer indentation, retaining title/description text verbatim.
    for tree in (canvas, root):
        for node in tree.iter():
            if node.tag not in ('title', 'desc', 'text'):
                node.text = None
            node.tail = None
    return ''.join(ET.tostring(tree, encoding='unicode', short_empty_elements=False)
                   for tree in (canvas, root))


def lean_policy_fields(example, *, lang, strings):
    """Use batch-1 copy/evidence unchanged; retain one canvas per outcome."""
    from scripts.did_samples import make_site_assets as legacy
    from types import FunctionType
    from scripts.viz_site.make_l2_rows import number
    # Isolate presentation overrides: never mutate the frozen module or evidence.
    display = dict(vars(legacy))
    for name, value in list(display.items()):
        if isinstance(value, FunctionType) and value.__module__ == legacy.__name__:
            clone = FunctionType(value.__code__, display, value.__name__, value.__defaults__, value.__closure__)
            clone.__kwdefaults__ = value.__kwdefaults__
            display[name] = clone
    display['_display_value'] = lambda value, scale: 100 * value if scale == 'proportion' else value
    display['_effect_number'] = lambda value, signed=False: number(value)
    card_strings = dict(strings)
    if example['runs'][0]['unit']['en'] == 'log difference':
        card_strings['lean.figure.unit.pp'] = 'log difference' if lang == 'en' else '对数差'
    card = display['render_policy_fields'](example, lang=lang, strings=card_strings)

    def panel(match):
        content = match[0]
        svgs = re.findall(r'<svg\b.*?</svg>', content, re.S)
        chosen = next(svg for svg in svgs if 'data-native-width="368"' in svg)
        chosen = chosen.replace(' data-native-width="368"', '')
        first = True

        def replace_svg(_):
            nonlocal first
            if first:
                first = False
                return '<div class="we-chart">' + lean_event_svg(chosen) + '</div>'
            return ''

        return re.sub(r'<svg\b.*?</svg>', replace_svg, content, flags=re.S)

    card['runs'] = re.sub(r'<div class="we-plot">.*?</div>', panel, card['runs'], flags=re.S)
    return card


def batch1_native_display(example):
    """Undo only the legacy log-to-percent projection on an isolated display copy."""
    from copy import deepcopy
    example = deepcopy(example)
    for run in example['runs']:
        if run['unit']['en'] != '%':
            continue
        native = lambda value: math.log1p(value / 100)
        run['estimate'] = native(run['estimate'])
        run['interval'] = [native(v) for v in run['interval']]
        for key in ('target', 'tolerance'):
            if run.get(key) is not None:
                run[key] = native(run[key])
        if run.get('historical') and run['historical'].get('target') is not None:
            run['historical']['target'] = native(run['historical']['target'])
        for point in run.get('events', []):
            point['att'] = native(point['att'])
            if point.get('simultaneous95'):
                point['simultaneous95'] = [native(v) for v in point['simultaneous95']]
        run['unit'] = dict(en='log difference', zh='对数差')
    return example


def case_display_scale(unit, lang="en"):
    """Display-only unit conversion; sidecar evidence retains its original scale."""
    name = unit["en"]
    if name in ("log points", "log units", "log difference"):
        return 1, 3, "log difference" if lang == "en" else "对数差"
    if name == "proportion":
        return 100, 3, "pp" if lang == "en" else "百分点"
    if name in ("percentage points", "pp"):
        return 1, 2, "pp" if lang == "en" else "百分点"
    return 1, 3, unit[lang]


def case_display_number(value, decimals=3):
    """Fixed precision per outcome, including a consistently rounded interval."""
    value = round(float(value), decimals)
    if value == 0:
        value = 0.0
    return format(value, f".{decimals}f").replace("-", "−")


def case_page_typography(html, lang):
    """Localize prose only, without changing SVG coordinates or legacy output."""
    def prose(part):
        part = re.sub(r"(?<![\d.])-([0-9])", r"−\1", part)
        if lang == "zh":
            part = part.replace("[", "［").replace("]", "］")
            part = re.sub(r"(?<=[\u3400-\u9fff])[:;]", lambda m: "：" if m[0] == ":" else "；", part)
            part = re.sub(r"(?<=[，。；：）]) +", "", part)
            part = re.sub(r"(?<=[\u3400-\u9fff])(?=\d)", " ", part)
            part = re.sub(r"(?<=\d)(?=[\u3400-\u9fff])", " ", part)
            part = part.replace("—", "–").replace("日历年", "年")
            # Keep a count beside its classifier, and keep quarter labels whole.
            part = re.sub(r"(\d+) +(第\d+季度|个|项|组|年|季度|财年|岁|支)", lambda m: m[1] + "\u00a0" + m[2], part)
            part = re.sub(r"［([^］]+)］", lambda m: "［" + m[1].replace(", ", "，").replace(",", "，") + "］", part)
            part = re.sub(r'[ \u00a0]*［', '［', part)
            part = re.sub(r'］[ \u00a0]*', '］', part)
            part = re.sub(r'([。；])[ \u00a0]*·[ \u00a0]*', r'\1', part)
            # Explicitly bind semicolons to the preceding character;
            # keep-all plus emergency wrapping otherwise permits a line-start ;.
            part = re.sub(r'(?<!\u2060)(；)', '\u2060' + r'\1', part)
        else:
            # Keep separators beside their preceding words at every width.
            part = part.replace(' · ', '\u00a0· ')
        return part
    html = ''.join(part if part.startswith('<') else prose(part)
                   for part in re.split(r'(<[^>]+>)', html))
    html = re.sub(r'<wbr>(?=[，。；：］）])', '', html)
    if lang == 'en':
        html = re.sub(r'\b(?:January|February|March|April|May|June|July|August|September|October|November|December) \d{1,2}, \d{4}\b',
                      lambda m: '<span class="we-nowrap">' + m[0] + '</span>', html)
        html = re.sub(r'\d{4} Q[1-4]–\d{4} Q[1-4]',
                      lambda m: '<span class="we-nowrap">' + m[0] + '</span>', html)
    if lang == 'zh':
        html = re.sub(r'(</span>)[ \u00a0]+(?=<span class="we-(?:nowrap|num)">［)', r'\1', html)
        html = re.sub(r'(］</span>)[ \u00a0]+', r'\1', html)
        html = re.sub(r'\d{4}[ \u00a0]年[ \u00a0]*第[ \u00a0]*\d+[ \u00a0]季度',
                      lambda m: '<span class="we-nowrap">' + m[0] + '</span>', html)
        # Bind full prose dates without inserting HTML spans into SVG text or attributes.
        html = ''.join(part if part.startswith('<') else re.sub(
            r'\d{4}[ \u00a0]年[ \u00a0]*\d{1,2}[ \u00a0]月[ \u00a0]*\d{1,2}[ \u00a0]日',
            lambda m: '<span class="we-nowrap">' + m[0] + '</span>', part)
            for part in re.split(r'(<svg\b.*?</svg>|<[^>]+>)', html, flags=re.S))
    # Dates and year spans never split at a hyphen or a range separator.
    html = re.sub(r'(?<![\w])(?:\d{4}-\d{2}-\d{2}|\d+–\d+(?:[ \u00a0]岁)?|\d{4}-\d{2,4})(?!\d)',
                  lambda m: '<span class="we-nowrap">' + m[0] + '</span>', html)
    return html


def render_case_page(selection, ctx, errs, template):
    """Optional WP4 presentation renderer; legacy services remain public wrappers.

    Extension contract: render_case_page(selection, ctx, errs, template, *, services).
    Inputs are isolated copies. Base rendering remains available without recursion.
    """
    from copy import deepcopy
    import importlib
    module_name = "scripts.viz_site.worked_examples_polish"
    try:
        extension = importlib.import_module(module_name)
    except ModuleNotFoundError as exc:
        if exc.name != module_name:
            raise
        return _render_case_page(selection, ctx, errs, template)
    renderer = getattr(extension, "render_case_page", None)
    if renderer is None:
        return _render_case_page(selection, ctx, errs, template)
    services = dict(render_case_page=_render_case_page, lean_event_svg=lean_event_svg,
                    case_page_typography=case_page_typography, render=render,
                    case_display_scale=case_display_scale, case_display_number=case_display_number)
    return renderer(deepcopy(selection), deepcopy(ctx), errs, template, services=services)


def _render_case_page(selection, ctx, errs, template):
    """Flat combined page; retain all selected results, with compact display copy."""
    from scripts.did_samples.make_site_assets import (
        render_event_study, _effect_readout, _page_text, _localized)
    lang, strings = ctx["lang"], ctx["strings"]
    from scripts.viz_site.make_l2_rows import number as display_number

    def text(key, **values):
        value = escape(strings["cases." + key].format(**values))
        if lang == 'zh' and key.startswith('coverage.'):
            value = re.sub(r'（名义 \d+%）', lambda m: nowrap(m[0]), value)
        return value

    def words(pair):
        value = pair[lang]
        if lang == 'zh':
            value = value.replace('私营NAICS', '私营 NAICS').replace('纳入NM和VT', '纳入 NM 和 VT')
        value = escape(value)
        return value.replace('对数', nowrap('对数')) if lang == 'zh' else value

    def nowrap(value):
        return '<span class="we-nowrap">' + value + '</span>'

    def scalar_parts(row):
        factor, decimals, unit = case_display_scale(row["unit"], lang)
        # Preserve a small signed bound when normal display precision erases it.
        quantities = [row["estimate"], *row["interval"]]
        while any(v != 0 and round(v * factor, decimals) == 0 for v in quantities) and decimals < 6:
            decimals += 1
        from scripts.viz_site.make_l2_rows import number as display_number
        estimate = display_number(row["estimate"] * factor)
        lo, hi = (display_number(v * factor) for v in row["interval"])
        interval = f'[{lo}, {hi}]' if lang == "en" else f'［{lo}，{hi}］'
        return estimate, interval, escape(unit)

    def scalar(row):
        estimate, interval, unit = scalar_parts(row)
        return nowrap(estimate + ' ' + interval + ' ' + unit)

    def summary_scalar(row):
        estimate, interval, unit = scalar_parts(row)
        suffix = nowrap(' ' + unit) if lang == 'en' and unit == 'log difference' else ' ' + unit
        return nowrap(estimate + ' ' + interval) + suffix

    def batch1_scalar(run):
        from scripts.viz_site.make_l2_rows import number
        unit = run['unit'][lang].replace('percentage points', 'pp')
        interval = ('[' if lang == 'en' else '［') + (', ' if lang == 'en' else '，').join(
            number(v) for v in run['interval']) + (']' if lang == 'en' else '］')
        return nowrap(number(run['estimate']) + ' ' + interval + ' ' + escape(unit))

    def sentence(value):
        value = re.sub(r'(?:<wbr>)+$', '', value.strip())
        return value if value.endswith(('.', '。')) else value + ('.' if lang == 'en' else '。')

    def comparison_words(pair):
        """Round the registered published number in its declared comparison unit."""
        source = pair[lang]
        if pair['en'] == 'Context: published estimate 14.344 percent.':
            source = ('Published estimate 14.3%.' if lang == 'en' else '已发表估计为 14.3%。')
        elif pair['en'] == 'Context: published estimate -0.0084 proportion.':
            source = ('Published estimate −0.84 pp.' if lang == 'en' else '已发表估计为 −0.84 百分点。')
        elif pair['en'] == 'Context only: published study concerns a different estimand; no comparable point estimate.':
            source = strings['cases.literature_context']
        source = re.sub(r'^(?:Context: |背景参照：)', '', source)
        if lang == 'en' and source.startswith('published estimate'):
            source = source[0].upper() + source[1:]
        return escape(source.replace('log points', 'log difference').replace('对数点', '对数差').replace('对数单位', '对数差'))

    def line(label, body, cls="we-design"):
        return '<p class="' + cls + '">' + text(label) + ('：' if lang == 'zh' else ': ') + body + '</p>'

    def unique(values):
        return ('；' if lang == 'zh' else ' · ').join(dict.fromkeys(values))

    def stopped_copy(reason, computed, multiple=False):
        label = text('computed_cs_estimates' if multiple else 'computed_cs_estimate')
        if lang == 'zh':
            computed = computed.replace('名义', nowrap('名义'))
        return sentence(reason) + ' ' + label + ('：' if lang == 'zh' else ': ') + sentence(computed)

    def case_design(case):
        design = case["design"]
        kind = design.get("time_basis", {}).get("kind", "calendar_year")
        period_kind = "quarter" if kind in ("quarter", "legacy_quarterly") else "fiscal" if kind == "fiscal_year" else "annual"
        first, last = design["first_period"][lang], design["last_period"][lang]
        if kind == 'fiscal_year':
            first, last = (re.sub(r'\s*[（(].*$', '', value) for value in (first, last))
        body = text("design_line_complete" if "primary_comparison_jurisdictions" in design else "design_line", treated=design["treated_jurisdictions"], groups=design["adoption_groups"],
                    never=design.get("primary_comparison_jurisdictions", design["never_treated"]), periods=design["periods"],
                    period_kind=strings["cases.period_kind." + period_kind],
                    first=first, last=last)
        if lang == 'zh' and kind == 'fiscal_year':
            body = body.replace('财年 全窗口', '财年全窗口')
        if design.get('reference_first_period'):
            # The hire window is named once in the specific window sentence.
            body = text('design_hire_counts', treated=design['treated_jurisdictions'], groups=design['adoption_groups'],
                        never=design['primary_comparison_jurisdictions']) + ' ' + text('hire_reference_window',
                        periods=design['periods'], first=first, last=last,
                        reference_first=design['reference_first_period'][lang], reference_last=design['reference_last_period'][lang])
        if design.get('full_exposure_min_days') is not None:
            body += ' ' + text('full_exposure_days', days=design['full_exposure_min_days'])
        return body

    def summary_row(identifier, policy, outcomes, estimates, stopped=False):
        outcomes, estimates = outcomes.split('<br>'), estimates.split('<br>')
        if len(estimates) == 1 and len(outcomes) > 1:
            estimates *= len(outcomes)
        if len(outcomes) != len(estimates):
            raise ValueError('Summary outcome/estimate count mismatch: ' + identifier)
        if lang == 'zh':
            # Keep the suffixes that formerly left 法 / 数 alone on a line.
            policy = policy.replace('许可法', nowrap('许可法'))
            outcomes = [re.sub(r'(?<!>)对数', lambda _: nowrap('对数'), outcome) for outcome in outcomes]
        cls = ' class="we-stopped-row"' if stopped else ''
        status = ' · ' + text('status.stopped') if stopped else ''
        records = []
        for index, (outcome, estimate) in enumerate(zip(outcomes, estimates)):
            heading = ('<th scope="rowgroup" rowspan="' + str(len(outcomes)) + '"><a href="#' +
                       escape(identifier, quote=True) + '-heading">' + policy + status + '</a></th>') if index == 0 else ''
            records.append('<tr' + cls + '>' + heading + '<td data-label="' + text("outcome") + '">' +
                           outcome + '</td><td data-label="' + text("estimate") + '">' + estimate + '</td></tr>')
        return '<tbody data-policy="' + escape(identifier, quote=True) + '">' + ''.join(records) + '</tbody>'

    rows, sections, stopped_rows, stopped_sections = [], [], [], []
    for example in selection["examples"] + selection["omitted"]:
        shown = example in selection["examples"]
        example = batch1_native_display(example)
        runs = [r for r in example["runs"] if r["headline"]] if shown else example["runs"][:1]
        policy = _page_text(_localized(example["policy"], lang), lang)
        estimates = '<br>'.join(batch1_scalar(r) for r in runs)
        row = summary_row(example["id"], policy, '<br>'.join(words(r["outcome"]) for r in runs),
                          estimates, not shown)
        (rows if shown else stopped_rows).append(row)
        if not shown:
            reason = text("castle_reason") if example["registered_id"] == "castle_doctrine" else words(example["stop_reason"])
            stopped_sections.append('<aside class="we-stopped" id="' + escape(example["id"], quote=True) + '-heading"><p>' +
                            policy + ('：' if lang == 'zh' else ': ') + text("status.stopped") + ' · ' +
                            sentence(reason) + ' ' + text('castle_estimate', estimate='{ESTIMATE}').replace('{ESTIMATE}', estimates) + '</p></aside>')
            continue
        # The combined page states each sample window once, in Design.
        card_strings = dict(strings)
        lede_key = 'lean.lede.' + example['registered_id']
        if lede_key in strings:
            card_strings[lede_key] = re.sub(r'(?:, |; |，|；)\{years\}(?: 年)?[.。]$',
                                           '.' if lang == 'en' else '。', strings[lede_key])
        card = lean_policy_fields(example, lang=lang, strings=card_strings)
        card['runs'] = card['runs'].replace(' percentage points', ' pp')
        card['runs'] = re.sub(r'(<p class="we-value">)(\[.*?\]|［.*?］)(</p>)',
                              lambda m: m[1] + nowrap(m[2]) + m[3], card['runs'])
        card["runs"] = card["runs"].replace(escape(strings['lean.figure.thin']), text('figure.thin'))
        first = runs[0]
        caption_key = 'figure_caption.psl' if example['registered_id'] == 'paid_sick_leave' else 'figure_caption.medicaid'
        caption = text(caption_key, reference=case_display_number(first['reference'], 0), band_level=int(first['band_level']))
        card['runs'] = re.sub(r'<figcaption>.*?</figcaption>', '<figcaption>' + caption + '</figcaption>', card['runs'], flags=re.S)
        card['runs'] = re.sub(r'(<desc[^>]*>).*?(</desc>)', lambda m: m[1] + caption + m[2], card['runs'], flags=re.S)
        # Use the same two-column variants table in both batches, retaining rows and aliases.
        def compact_table(match):
            table = match[0]
            table = re.sub(r'<caption>.*?</caption>', '<caption>' + text("variants") + '</caption>', table, flags=re.S)
            table = re.sub(r'<thead>.*?</thead>', '<thead><tr><th scope="col">' + text("variant") + '</th><th scope="col">' + text("estimate") + '</th></tr></thead>', table, flags=re.S)
            def compact_row(rowmatch):
                rowhtml = rowmatch[0]
                cells = re.findall(r'<td(?: [^>]*)?>(.*?)</td>', rowhtml, re.S)
                if len(cells) == 3:
                    run_id = re.search(r'data-run-id="([^"]+)"', rowhtml)[1]
                    run = next((r for r in example['runs'] if r['id'] == run_id), None)
                    value = batch1_scalar(run) if run else cells[1] + ' ' + cells[2]
                    if not run and example['registered_id'] == 'medicaid_expansion':
                        unit = 'pp' if lang == 'en' else '百分点'
                        if unit not in value:
                            value += ' ' + unit
                    if example['registered_id'] == 'medicaid_expansion' and run_id == 'S2/not_yet_treated':
                        cells[0] = cells[0].replace('Census outcome', text('sahie_model')).replace('人口普查局结果', text('sahie_model'))
                    new = '<td>' + cells[0] + '</td><td>' + value + '</td>'
                    rowhtml = rowhtml[:rowhtml.index('<td')] + new + '</tr>'
                elif len(cells) == 2 and example['registered_id'] == 'medicaid_expansion':
                    # Omission ranges and diagnostic rows have no run object.
                    unit = 'pp' if lang == 'en' else '百分点'
                    if unit not in cells[1]:
                        rowhtml = rowhtml.replace('</td></tr>', ' ' + unit + '</td></tr>')
                return rowhtml.replace(' colspan="2"', '')
            return re.sub(r'<tr data-run-id=.*?</tr>', compact_row, table, flags=re.S).replace('class="we-other-table"', 'class="we-other-table we-case-variants"')
        card["runs"] = re.sub(r'<table class="we-other-table">.*?</table>', compact_table, card["runs"], flags=re.S)
        if example["registered_id"] == "paid_sick_leave":
            card["runs"] = re.sub(r'<p class="we-source">.*?</p>', line("benchmark", text("psl_source"), "we-comparison-line"), card["runs"], flags=re.S)
        else:
            card["runs"] = card["runs"].replace('<p class="we-comparison-line">', '<p class="we-comparison-line">' + text("benchmark") + ('：' if lang == 'zh' else ': '))
            card['runs'] = card['runs'].replace('Benchmark: Context: ', 'Benchmark: ').replace('文献对照：背景参照：<wbr>', '文献对照：')
            card['runs'] = card['runs'].replace('NBER WP ', 'NBER Working Paper ')
        card["runs"] = re.sub(r'(?<![\w])p=', 'p = ', card["runs"])
        card['runs'] = card['runs'].replace('Omit one state/adoption group', 'Omit one jurisdiction/adoption group').replace('逐州／逐采纳组剔除', '逐一剔除辖区／采纳组')
        # Both batches use figure, Design, method, Benchmark, Variants.
        benchmark = ''.join(re.findall(r'<p class="we-comparison-line">.*?</p>', card['runs'], re.S))
        method = re.search(r'<p class="we-design">(.*?)</p>', card['runs'], re.S)
        variants = re.search(r'<table class="we-other-table we-case-variants">.*?</table>', card['runs'], re.S)
        intro = card['runs'].split('<p class="we-comparison-line">', 1)[0]
        years = first['years']
        design = line('design', text('batch1_design', periods=len(years), first=years[0], last=years[-1]))
        card['runs'] = intro + design + '<p class="we-design">' + sentence(method[1]) + '</p>' + benchmark + (variants[0] if variants else '')
        sections.append(render(template, dict(ctx, example=card), errs, "partials/worked_examples_body.html"))

    for case in selection["cases"]:
        identifier, shown = case["case_id"], case["shown"]
        primary = list(case["primary_estimates"].values())
        policy = words(case["policy"])
        if not shown:
            reasons = unique(words(r) for r in case["stop_reasons"])
            values = '<br>'.join(summary_scalar(r) for r in primary) if primary else text('status.stopped')
            stopped_rows.append(summary_row(identifier, policy, '<br>'.join(words(r["outcome"]) for r in primary), values, True))
            computed = ('；' if lang == 'zh' else '; ').join(
                (words(r['outcome'])[:1].lower() + words(r['outcome'])[1:] if lang == 'en' else words(r['outcome'])) + ' ' + scalar(r)
                for r in primary)
            stopped_sections.append('<aside class="we-stopped" id="' + identifier + '-heading"><p>' + policy + ('：' if lang == 'zh' else ': ') +
                            text("status.stopped") + ' · ' + (stopped_copy(reasons, computed, len(primary) > 1) if computed else reasons) + '</p></aside>')
            continue
        outcomes = (text('summary_outcome.' + identifier) if 'cases.summary_outcome.' + identifier in strings
                    and identifier != 'salary_history_ban' else '<br>'.join(words(r['outcome']) for r in primary))
        summary_policy = text('summary_policy.' + identifier) if 'cases.summary_policy.' + identifier in strings else policy
        rows.append(summary_row(identifier, summary_policy, outcomes, '<br>'.join(summary_scalar(r) for r in primary)))
        body = ['<article class="we-example" aria-labelledby="' + identifier + '-heading">',
                '<h2 id="' + identifier + '-heading">' + policy + '</h2>',
                '<p class="we-lede">' + words(case["treatment"]) + '</p>', '<div class="we-readouts">']
        for row in primary:
            estimate, interval, unit = scalar_parts(row)
            outcome = words(row['outcome'])
            body.append('<div class="we-readout"><p class="we-outcome">' + outcome + '</p><p class="we-value">' +
                        estimate + ' ' + unit + '</p><p class="we-value">' + nowrap(interval) + '</p><p class="we-interval-label">' +
                        text("interval_head", level=95) + '</p></div>')
        coverage = (case.get('checks', {}).get('calibration') or {}).get('point_interval_coverage')
        if coverage and coverage.get('rate') is not None and coverage['rate'] < .90:
            body.append('<p class="we-interval-note we-coverage">' + text('coverage.' + identifier if 'cases.coverage.' + identifier in strings else 'coverage.generic',
                        first=case['design']['first_period'][lang], last=case['design']['last_period'][lang],
                        outcomes=len(coverage['outcome_columns']), rate=case_display_number(100 * coverage['rate'], 1).rstrip('0').rstrip('.'),
                        nominal=case_display_number(100 * coverage['nominal_level'], 0)) + '</p>')
        body.append('</div>')
        panels = []
        for row in primary:
            figure = case.get("figures", {}).get(row["id"], {})
            factor, _, unit = case_display_scale(row["unit"], lang)
            events = [dict(event_time=p["event_time"], att=p["estimate"] * factor,
                           simultaneous95=[v * factor for v in p["band"]] if p.get("band") else None,
                           thin_support=p["hollow"], band_unavailable=p.get("band") is None)
                      for p in figure.get("points", [])]
            if not events:
                continue
            kind = case["design"].get("time_basis", {}).get("kind")
            axis = strings["cases.event_time.quarter" if kind in ("quarter", "legacy_quarterly") else "cases.event_time.fiscal" if kind == "fiscal_year" else "lean.event_time"]
            caption = text("figure_caption")
            svg = render_event_study(events, title=row["outcome"][lang], description=strings["cases.figure_caption"],
                    event_label=axis, estimate_label=strings["lean.estimate"], figure_id=identifier + '-' + row["id"],
                    before_label=strings["lean.before"], after_label=strings["lean.after"], plot_width=368, unit_label=unit)
            title = '<p>' + words(row['outcome']) + '</p>' if len(primary) > 1 else ''
            panels.append('<div class="we-plot">' + title + '<div class="we-chart">' + lean_event_svg(svg) + '</div></div>')
        if panels:
            body.append('<figure class="we-figure"><div class="we-plot-panels">' + ''.join(panels) + '</div><figcaption>' + caption + '</figcaption></figure>')
        body.append(line("design", case_design(case)))
        populations = primary[:1] if identifier == 'minimum_wage_step' else primary
        population_copy = unique(words(r['population']) for r in populations).replace('2010-2019', '2010–2019')
        if identifier == 'cigarette_tax':
            credit = 'CDC Tax Burden on Tobacco' if lang == 'en' else 'CDC《Tax Burden on Tobacco》'
            population_copy = population_copy.replace(credit, '<a href="https://data.cdc.gov/d/7nwe-3aj9">' + credit + '</a>')
            population_copy = population_copy.replace('ODC-By 1.0', '<a href="https://opendatacommons.org/licenses/by/1-0/">ODC-By 1.0</a>')
        body.append('<p class="we-design">' + sentence(words(case["estimand_line"]) + ' ' + population_copy) + '</p>')
        precision = unique(words(r["clock_precision_note"]) for r in primary if r.get("clock_precision_note"))
        if precision:
            body.append('<p class="we-design">' + precision + '</p>')
        comparisons = []
        benchmark_rows = primary[1:2] if identifier == 'right_to_work' else primary[:1]
        for row in benchmark_rows:
            if row.get("comparison_line"):
                comparison = comparison_words(row["comparison_line"])
                if lang == 'zh':
                    comparison = re.sub(r'^文献', '', comparison)
                if row["comparison_line"]['en'].startswith('Context: published estimate'):
                    reason = strings.get('cases.benchmark_difference.' + identifier)
                    if reason:
                        comparison += ' ' + escape(reason)
                comparisons.append(comparison)
        comparisons.extend(words(r["label"]) + ('：' if lang == 'zh' else ': ') + words(r["line"]) for r in case.get("visible_disagreements", []))
        body.append(line("benchmark", unique(comparisons) + ' ' + words(case["source_caption"]), "we-comparison-line"))
        other = case.get("other_estimates", [])
        if other:
            variants = []
            for row in other:
                label = words(row["label"])
                replacements = {
                    'Registered source-date reinclusion (empty exclusion set)': 'variant_source_dates',
                    'Ages 35–54; source-date reinclusion (empty exclusion set)': 'variant_source_dates_age35',
                    'Recorded-date sensitivity: Alaska, Nevada, Virginia': 'variant_minimum_wage_dates',
                    'Recorded-date sensitivity including NM and VT': 'variant_smoke_free_dates',
                    'Source-date sensitivity including NY on the LaValle clock; unresolved verification retained': 'variant_ny_date',
                    'Violent crime including rape; definition break disclosed': 'variant_violent_crime',
                }
                variant_keys = {'ny_taylor_clock': 'variant_ny_taylor',
                                'drop_mixed_controls': 'variant_drop_mixed',
                                'drop_large_cigarette_tax_overlap': 'variant_drop_tax_overlap',
                                'ny_lavalle_clock': 'variant_ny_date',
                                'violent_total': 'variant_violent_crime'}
                if row.get('variant_id') in variant_keys:
                    label = text(variant_keys[row['variant_id']])
                if row['label']['en'] in replacements:
                    label = text(replacements[row['label']['en']])
                if len(primary) > 1:
                    outcome_key = None
                    match = next((index for index, p in enumerate(primary) if p['outcome'] == row['outcome']), None)
                    if match is None:
                        # Diagnostics can carry older outcome wording. Bind the
                        # short series label by its registered identity instead.
                        match = next((index for index, p in enumerate(primary)
                                      if (row.get('outcome_column') is not None and
                                          p.get('outcome_column') == row['outcome_column']) or
                                      any(identity.startswith(p['id'] + '/') for identity in row.get('ids', []))), None)
                    names = {'salary_history_ban': ('main', 'age35'), 'minimum_wage_step': ('main', 'wage'),
                             'right_to_work': ('membership', 'coverage'), 'renewable_portfolio': ('share', 'price')}
                    if match is not None and identifier in names:
                        outcome_key = 'variant_outcome.' + identifier + '.' + names[identifier][match]
                    label += ' · ' + (text(outcome_key) if outcome_key else words(row['outcome']))
                if row.get("exploratory"):
                    label += ' · ' + text("exploratory")
                if row.get("kind") == "range":
                    factor, decimals, unit = case_display_scale(row["unit"], lang)
                    value = nowrap((' to ' if lang == 'en' else ' 至 ').join(display_number(v * factor) for v in row["point_range"]) + ' ' + escape(unit))
                else:
                    value = scalar(row)
                    comparison = row.get("comparison_line")
                    if comparison and comparison["en"] != "No comparable published point estimate for this window." and not comparison["en"].startswith(('Context:', 'Context only:')):
                        label += '<span class="we-row-comparison">' + comparison_words(comparison) + '</span>'
                ids = escape(' '.join(row.get("ids", [row.get("id", "")])), quote=True)
                variants.append('<tr data-record-ids="' + ids + '"><td>' + label + '</td><td>' + value + '</td></tr>')
            body.append('<table class="we-other-table we-case-variants"><caption>' + text("variants") + '</caption>' +
                        '<thead><tr><th scope="col">' + text("variant") + '</th><th scope="col">' + text("estimate") +
                        '</th></tr></thead><tbody>' + ''.join(variants) + '</tbody></table>')
        body.append('</article>')
        sections.append('\n'.join(body))
    keys = ["policy", "outcome", "estimate"]
    stopped = len(selection["omitted"]) + sum(not case["shown"] for case in selection["cases"])
    html = ('<div class="we-cases"><p class="we-selection">' + text("selection", specified=selection["prepared"], shown=selection["shown"], stopped=stopped) +
            '</p><table class="we-summary we-case-summary"><thead><tr>' + ''.join('<th scope="col">' + text(k) + '</th>' for k in keys) +
            '</tr></thead>' + ''.join(rows + stopped_rows) + '</table>' + '\n'.join(sections + stopped_sections) + '</div>')
    html = re.sub(r'<aside class="we-stopped".*?</aside>',
                  lambda m: m[0].replace(escape("Callaway–Sant'Anna"), nowrap(escape("Callaway–Sant'Anna"))), html, flags=re.S)
    return case_page_typography(html, lang)


def render_v5(component, lang):
    from scripts.viz_site import restructure_estimates, restructure_screening, restructure_explore, restructure_home
    if component == "progress":
        return escape(restructure_estimates.progress_line(lang))
    return {"estimates": restructure_estimates, "screening": restructure_screening,
            "explore": restructure_explore, "home": restructure_home}[component].render(lang)


def render(text: str, ctx: dict, errs: Errors, where: str, stack=()) -> str:
    lang, page, strings = ctx["lang"], ctx["page"], ctx["strings"]
    conditions = {"github": SHOW_GITHUB, "examples": has_examples(), "fixtures": (worked_fixture_mode() if page == "worked_examples" else FIXTURE_MODE), "design_opportunity": has_design_opportunity(), "policy_exposure": has_policy_exposure()}
    if not conditions["examples"]:
        # Several existing pages inline the whole dictionary. Keep their normal bytes stable.
        strings = {k: v for k, v in strings.items() if k not in ("nav.worked_examples", "story.levels.estimate.aria")}
        ctx = dict(ctx, strings=strings)
    if not conditions["design_opportunity"]:
        strings = {k: v for k, v in strings.items() if k not in ("nav.design_opportunity",)}
        ctx = dict(ctx, strings=strings)
    if not conditions["policy_exposure"]:
        strings = {k: v for k, v in strings.items() if k != "nav.policy_exposure"}
        ctx = dict(ctx, strings=strings)
    text = COND.sub(lambda c: c.group(2) if conditions[c.group(1)] else "", text)

    def sub_num(m):
        try:
            return escape(compute(m.group(5).strip(), m.group(6), lang, errs, strings), quote=True)
        except ExprError as exc:
            errs.error("%s [%s]: {{num:%s}}: %s" % (where, lang, m.group(5).strip(), exc))
            return m.group(0)

    def sub_tf(m):
        key, argtext = m.group(8), m.group(9)
        if key not in strings:
            errs.error("%s [%s]: unknown dictionary key {{tf:%s}}" % (where, lang, key))
            return m.group(0)
        values, bad = {}, False
        for piece in argtext.split("|")[1:]:
            name, eq, expr = piece.partition("=")
            name = name.strip()
            if not eq or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) or name in values:
                errs.error("%s [%s]: {{tf:%s}}: bad or repeated argument %r" % (where, lang, key, piece.strip()))
                bad = True
                continue
            try:
                values[name] = escape(compute(expr.strip(), None, lang, errs, strings), quote=True)
            except ExprError as exc:
                errs.error("%s [%s]: {{tf:%s}} slot %s: %s" % (where, lang, key, name, exc))
                bad = True
        template = strings[key]
        slots = set(SLOT.findall(template))
        for name in sorted(slots - set(values)):
            errs.error("%s [%s]: {{tf:%s}}: no value for slot {%s}" % (where, lang, key, name))
            bad = True
        for name in sorted(set(values) - slots):
            errs.error("%s [%s]: {{tf:%s}}: argument %s has no {%s} slot in the string" % (where, lang, key, name, name))
            bad = True
        if bad:
            return m.group(0)
        body = template if key.endswith("_html") else escape(template, quote=True)
        return SLOT.sub(lambda s: values[s.group(1)], body)

    def sub(m):
        if m.group(4):
            return sub_num(m)
        if m.group(7):
            return sub_tf(m)
        kind, arg, simple = m.group(1), m.group(2), m.group(3)
        if simple:
            return {
                "t_json": lambda: json_for_script(strings, False),
                "lang": lambda: HTML_LANG[lang],
                "html_lang": lambda: HTML_LANG[lang],
                "lang_code": lambda: lang,
                "page": lambda: page,
                "lang_switch": lambda: lang_switch_html(lang, page),
                "other_url": lambda: "/viz/%s/%s.html" % (OTHER[lang], page),
                "self_url": lambda: "/viz/%s/%s.html" % (lang, page),
                "v5_estimates": lambda: render_v5("estimates", lang),
                "v5_progress": lambda: render_v5("progress", lang),
                "v5_screening": lambda: render_v5("screening", lang),
                "v5_explore": lambda: render_v5("explore", lang),
                "v5_home": lambda: render_v5("home", lang),
                "worked_examples": lambda: render_worked_examples(ctx, errs),
                "l2_additions": lambda: render_l2_extension("render_additions", lang),
                "l2_gates": lambda: render_l2_extension("render_gates", lang),
                "l2_noise": lambda: render_l2_extension("render_noise", lang),
                "example_mode": lambda: "fixtures" if worked_fixture_mode() else "production",
                "design_opportunity": lambda: render_design_opportunity(ctx, errs),
                "policy_exposure": lambda: render_policy_exposure(ctx, errs),
                "policy_exposure_records": lambda: render_policy_exposure_records(ctx, errs),
                "treatment_timing": lambda: render_treatment_timing(ctx, errs),
                "map_sources": lambda: render_map_sources(ctx, errs),
            }[simple]()
        if kind == "example":
            if arg not in ctx.get("example", {}):
                errs.error("%s: unknown example field %s" % (where, arg))
                return m.group(0)
            return ctx["example"][arg]
        if kind == "t":
            if arg not in strings:
                errs.error("%s [%s]: unknown dictionary key {{t:%s}}" % (where, lang, arg))
                return m.group(0)
            return strings[arg] if arg.endswith("_html") else escape(strings[arg], quote=True)
        if kind == "data":
            data = load_data(arg, errs)
            return "null" if data is None else json_for_script(data, True)
        if kind == "css":
            path = CSS_DIR / (arg + ".css")
            if not path.is_file():
                errs.error("%s [%s]: {{css:%s}}: %s not found" % (where, lang, arg, path.relative_to(ROOT)))
                return m.group(0)
            css = CSS_COMMENT.sub("", read_text(path)).strip()
            if arg == "worked_examples" and worked_example_selection().get("cases"):
                css += "\n" + CSS_COMMENT.sub("", read_text(CSS_DIR / "worked_examples_cases.css")).strip()
            return css
        if kind == "nav":
            if arg == page:
                return 'class="vt-btn vt-active" aria-current="page"'
            if arg == "worked_examples" and page == "policy_exposure_records":
                return 'class="vt-btn vt-active" aria-current="true"'
            return 'class="vt-btn vt-link"'
        if kind == "partial":
            if arg in stack:
                errs.error("partial cycle: %s" % " -> ".join(stack + (arg,)))
                return ""
            path = PARTIALS_DIR / (arg + ".html")
            if not path.is_file():
                errs.error("%s: partial %r not found (partials/%s.html)" % (where, arg, arg))
                return ""
            if arg in ("header", "footer", "head_css", "motion"):
                from scripts.viz_site.shared_chrome import marked, render_slots
                slots = render_slots(lang, page, features=chrome_features())
                names = ("header", "nav") if arg == "header" else (("style", "motion") if arg == "head_css" else (arg,))
                return "\n".join(marked(name, slots[name]) for name in names)
            body = HTML_COMMENT.sub("", read_text(path))
            return render(body, ctx, errs, "partials/%s.html" % arg, stack + (arg,))
        return m.group(0)

    return PLACEHOLDER.sub(sub, text)


def lint_output(out: str, lang: str, where: str, errs: Errors, page: str = ""):
    if page == "design_opportunity":
        from scripts.viz_site.validate_design_opportunity import validate_public_fields
        for error in validate_public_fields(out):
            errs.error(where + ": " + error)
    for m in LEFTOVER.finditer(out):
        errs.error("%s [%s]: unresolved placeholder %s" % (where, lang, m.group(0)))
    for m in LEFTOVER_CALC.finditer(out):
        errs.error("%s [%s]: unresolved placeholder %s" % (where, lang, m.group(0)[:100]))
    if "DEV FALLBACK" in out:
        errs.error("%s [%s]: output contains 'DEV FALLBACK'" % (where, lang))
    for m in FETCH_JSON.finditer(out):
        errs.error("%s [%s]: output fetches a .json file: %s" % (where, lang, m.group(0)[:80]))
    if lang == "en":
        stripped = out.replace(lang_switch_html("en", page), "")      # the toggle carries CJK by design
        m = CJK.search(stripped)
        if m:
            i = m.start()
            errs.error("%s [en]: CJK in output near %r" % (where, stripped[max(0, i - 30):i + 30]))


PUBLIC_PAGE_TRANSFORM = lambda text, **kwargs: text


def build_page(page: str, lang: str, errs: Errors, write: bool, check_current=False) -> bool:
    src = PAGES_DIR / (page + ".html")
    where = "pages/%s.html" % page
    text = read_text(src)
    used = partials_used(text, errs)

    en_ok = load_dict("common", lang, errs)
    strings = dict(en_ok or {})
    for name in used + [page]:
        d = load_dict(name, lang, errs)
        if d:
            strings.update(d)
    if page == "worked_examples" and worked_example_selection().get("cases") and worked_fixture_mode():
        strings["we.intro_html"] = strings["cases.intro_html"]
    if page == "worked_examples" and "{{v5_estimates}}" not in text and not worked_fixture_mode():
        counts = load_data("l2_counts", errs)
        strings["we.intro_html"] = strings["we.intro_html"].format(**counts)
    if page == "worked_examples" and CASE_FIXTURES is not None and not FIXTURE_MODE and worked_example_selection().get("cases"):
        strings["we.fixture"] = strings["cases.fixture"]
    before = len(errs.errors)
    out = render(text, {"lang": lang, "page": page, "strings": strings}, errs, where)
    out = PUBLIC_PAGE_TRANSFORM(out, page=page, lang=lang)
    lint_output(out, lang, where, errs, page)
    dest = OUT_DIR / lang / (page + ".html")
    if check_current and (not dest.is_file() or dest.read_bytes() != out.encode("utf-8")):
        errs.error("%s: output missing or stale: %s" % (where, dest))
    if write and len(errs.errors) == before:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(out, encoding="utf-8")
        return True
    return False


# --------------------------------------------------------------------------- homepage story block (partials/home_story.html)

HOME_PARTIAL = "home_story"
HOME_PAGE = "home"                     # page name used for {{page}} / {{lang_switch}} while rendering the block
PREVIEW_DIR = DEPLOY / "_landing_options"
PREVIEW_TEMPLATE = """<!DOCTYPE html>
<html lang="{{html_lang}}">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1.0">
<meta name="robots" content="noindex">
<link rel="icon" href="data:,">
<title>{{t:preview.title}}</title>
{{partial:head_css}}
<style>
  .pv-bar { margin: 1.6rem 0 0.6rem; padding: 0.9rem 1rem; border: 1.5px dashed #9ca3af; border-radius: 10px; background: #f9fafb; color: #6b7280; font-size: 0.9rem; text-align: center; }
</style>
</head>
<body>
<main class="page">
{{partial:header}}
{{partial:home_story}}
<div class="pv-bar">{{t:preview.stub}}</div>
</main>
{{partial:footer}}
</body>
</html>
"""


def render_partial(name: str, lang: str, errs: Errors, page: str = HOME_PAGE) -> str:
    """One partial rendered for one language with the dictionaries it (and the partials it includes) need.
    Importable entry point for whatever injects the homepage block into another page."""
    text = "{{partial:%s}}" % name
    strings = dict(load_dict("common", lang, errs) or {})
    for n in partials_used(text, errs):
        d = load_dict(n, lang, errs)
        if d:
            strings.update(d)
    out = render(text, {"lang": lang, "page": page, "strings": strings}, errs, "partial:" + name)
    lint_output(out, lang, "partial:" + name, errs, page)
    return out


def build_home_preview(lang: str, errs: Errors, write: bool) -> bool:
    """Full preview page (site header + story block + stub bar + footer) -> deploy_output/_landing_options/final_<lang>.html.
    Not a production output."""
    where = "home preview"
    used = partials_used(PREVIEW_TEMPLATE, errs)
    strings = dict(load_dict("common", lang, errs) or {})
    for n in used:
        d = load_dict(n, lang, errs)
        if d:
            strings.update(d)
    before = len(errs.errors)
    out = render(PREVIEW_TEMPLATE, {"lang": lang, "page": HOME_PAGE, "strings": strings}, errs, where)
    lint_output(out, lang, where, errs, HOME_PAGE)
    if len(errs.errors) != before:
        return False
    # the toggle of a preview points at the sibling preview file, not at a (non-existent) /viz/<lang>/home.html
    out = out.replace('href="/viz/%s/%s.html"' % (OTHER[lang], HOME_PAGE), 'href="final_%s.html"' % OTHER[lang])
    if write:
        dest = PREVIEW_DIR / ("final_%s.html" % lang)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(out, encoding="utf-8")
    return True


STORY_START = "<!-- STORY:START -->"
STORY_END = "<!-- STORY:END -->"
STORY_BLOCK = re.compile(re.escape(STORY_START) + r".*?" + re.escape(STORY_END), re.S)
HOME_TARGET = DEPLOY / "policy_db.html"                 # hand-maintained EN homepage; update story and the table-note unit phrase


def render_story_block(lang: str, errs: Errors) -> str:
    """The rendered home_story partial wrapped in its STORY markers, LF newlines, no trailing newline.
    Importable: scripts/zh_pages/build.py swaps this block into the generated zh/policy_db.html."""
    body = render_partial(HOME_PARTIAL, lang, errs).strip("\n")
    return STORY_START + "\n" + body + "\n" + STORY_END


def inject_home(errs: Errors, write: bool, lang="en", target=None):
    """(Re)render the EN story into deploy_output/policy_db.html between STORY:START and STORY:END.
    The table-note unit phrase is also refreshed from home_story i18n; all other content
    outside the markers stays byte-for-byte unchanged (CRLF files stay CRLF). Idempotent.
    Returns 'written' | 'unchanged' | 'stale' (only when write=False) | None (errors recorded)."""
    check_dict_pair(HOME_PARTIAL, errs)
    check_dict_pair("pipeline_strip", errs)
    check_story_assets(errs)
    before = len(errs.errors)
    block = render_story_block(lang, errs)
    if len(errs.errors) != before:
        return None
    target = target or HOME_TARGET
    if not target.is_file():
        errs.error("inject-home: %s does not exist" % target)
        return None
    text = target.read_bytes().decode("utf-8")
    n_start, n_end = text.count(STORY_START), text.count(STORY_END)
    if n_start != 1 or n_end != 1 or text.index(STORY_START) > text.index(STORY_END):
        errs.error("inject-home: %s needs exactly one %s followed by one %s "
                   "(found %d and %d); nothing written" % (target, STORY_START, STORY_END, n_start, n_end))
        return None
    crlf = "\r\n" in text
    if crlf:
        block = block.replace("\n", "\r\n")
    new = STORY_BLOCK.sub(lambda _m: block, text, count=1)
    old_note_phrase = {"en": "The table below counts distinct policies",
                       "zh": "下方表格统计的是不重复的政策"}[lang]
    note_phrase = (load_dict(HOME_PARTIAL, lang, errs) or {}).get("story.table.note_phrase")
    if note_phrase:
        # Match the old unit exactly; an already-refreshed phrase is left alone.
        new = re.sub(re.escape(old_note_phrase) + r"(?!文本)",
                     lambda _m: note_phrase, new, count=1)
    from scripts.viz_site.shared_chrome import include_slots
    try:
        new = include_slots(new, lang, "policy_db", features=chrome_features())
    except ValueError as exc:
        errs.error("inject-home: " + str(exc))
        return None
    if crlf:
        new = new.replace("\r\n", "\n").replace("\n", "\r\n")
    if new == text:
        return "unchanged"
    if not write:
        return "stale"
    target.write_bytes(new.encode("utf-8"))
    return "written"


def check_story_assets(errs: Errors):
    """partials/story_grid.html must be exactly what make_story_assets.py generates from the current data files."""
    import importlib.util
    path = HERE / "make_story_assets.py"
    if not path.is_file():
        errs.error("make_story_assets.py is missing")
        return
    spec = importlib.util.spec_from_file_location("make_story_assets", path)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        fresh = mod.render_partial()
    except Exception as exc:                       # noqa: BLE001 - report any generator failure as a build error
        errs.error("make_story_assets.py: %s: %s" % (type(exc).__name__, exc))
        return
    target = PARTIALS_DIR / "story_grid.html"
    if not target.is_file() or read_text(target) != fresh:
        errs.error("partials/story_grid.html is stale or missing: run python3 scripts/viz_site/make_story_assets.py")


def main(argv=None) -> int:
    global FIXTURE_MODE, CASE_FIXTURES, _examples_cache, OUT_DIR, PREVIEW_DIR, HOME_TARGET
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--lang", choices=LANGS, help="build one language only")
    ap.add_argument("--page", help="build one page only (name without .html)")
    ap.add_argument("--check", action="store_true", help="validate, write nothing")
    ap.add_argument("--fixtures", action="store_true", help="use invented fixture results; requires an isolated --output-dir")
    ap.add_argument("--case-fixtures", type=Path, help="synthetic batch-2 sidecars; requires an isolated --output-dir")
    ap.add_argument("--output-dir", type=Path, help="isolated HTML output root; JSON inputs still come from deploy_output")
    ap.add_argument("--preview-home", action="store_true",
                    help="write only deploy_output/_landing_options/final_{en,zh}.html (homepage story preview, not a "
                         "production output); with --check just validate them")
    ap.add_argument("--inject-home", action="store_true",
                    help="ONLY update the homepage story between STORY markers (idempotent); default: EN "
                         "deploy_output/policy_db.html; with --output-dir: EN and ZH copies there. "
                         "With --check write nothing and fail for stale stories")
    args = ap.parse_args(argv)

    output_root = args.output_dir.resolve() if args.output_dir else DEPLOY
    if (args.fixtures or args.case_fixtures is not None) and (args.output_dir is None or output_root == DEPLOY.resolve() or DEPLOY.resolve() in output_root.parents):
        ap.error("--fixtures / --case-fixtures requires --output-dir outside deploy_output")
    if args.case_fixtures is not None and not args.case_fixtures.is_dir():
        ap.error("--case-fixtures must be an existing directory")
    if output_root != DEPLOY.resolve() and (output_root in DEPLOY.resolve().parents or output_root == ROOT):
        ap.error("--output-dir must be a separate directory, not a repository/output ancestor")
    if output_root != DEPLOY.resolve():
        targets = [output_root / "viz" / lang / (page.stem + ".html")
                   for lang in LANGS for page in PAGES_DIR.glob("*.html")]
        targets += [output_root / "policy_db.html", output_root / "zh/policy_db.html",
                    output_root / "_landing_options/final_en.html", output_root / "_landing_options/final_zh.html"]
        if any(output_root not in target.resolve().parents for target in targets):
            ap.error("isolated output paths must stay inside --output-dir (including symlinks)")
    FIXTURE_MODE = args.fixtures
    CASE_FIXTURES = args.case_fixtures
    _examples_cache = None
    _dict_cache.clear()
    _data_cache.clear()
    OUT_DIR = output_root / "viz"
    PREVIEW_DIR = output_root / "_landing_options"
    HOME_TARGET = output_root / "policy_db.html"

    errs = Errors()
    try:
        enabled = "{{v5_estimates}}" in read_text(PAGES_DIR / "worked_examples.html") or has_examples()
    except (ValueError, OSError, KeyError, TypeError) as exc:
        print("BUILD FAILED: worked examples results: " + str(exc), file=sys.stderr)
        return 1
    if enabled:
        check_dict_pair("worked_examples", errs)
        # Validate both languages before any page/nav/home can be written.
        for lang in LANGS:
            build_page("worked_examples", lang, errs, write=False)
        if errs.errors:
            print("BUILD FAILED:\n  - " + "\n  - ".join(errs.errors), file=sys.stderr)
            return 1
    if has_design_opportunity():
        from scripts.viz_site.validate_design_opportunity import validate
        try:
            payload = json.loads(DESIGN_DATA.read_text(encoding="utf-8"))
            design_errors = validate(payload)
            for error in design_errors:
                errs.error("design opportunity: " + error)
            check_dict_pair("design_opportunity", errs)
            for lang in LANGS:
                build_page("design_opportunity", lang, errs, write=False)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            errs.error("design opportunity: " + str(exc))
        if errs.errors:
            print("BUILD FAILED:\n  - " + "\n  - ".join(errs.errors), file=sys.stderr)
            return 1
    available = sorted(p.stem for p in PAGES_DIR.glob("*.html")
                       if p.stem != "design_opportunity" or has_design_opportunity())
    if args.page:
        if args.page not in available:
            print("no such page: pages/%s.html (available: %s)" % (args.page, ", ".join(available) or "none"),
                  file=sys.stderr)
            return 2
        pages = [args.page]
    else:
        pages = available
    langs = [args.lang] if args.lang else list(LANGS)
    if not enabled:
        pages = [p for p in pages if p != "worked_examples"]
        if not args.preview_home and not args.inject_home and (not args.page or args.page == "worked_examples"):
            for lang in langs:
                stale = OUT_DIR / lang / "worked_examples.html"
                if stale.exists():
                    if args.check:
                        errs.error("ineligible worked-example output still exists: %s" % stale)
                    else:
                        stale.unlink()

    if args.inject_home:                                    # isolated roots update both homepages
        targets = [("en", HOME_TARGET)]
        if output_root != DEPLOY.resolve():
            targets.append(("zh", output_root / "zh/policy_db.html"))
            if not args.check:
                for lang, target in targets:
                    source = DEPLOY / ("policy_db.html" if lang == "en" else "zh/policy_db.html")
                    if not target.exists():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(source.read_bytes())
        results = [inject_home(errs, write=not args.check, lang=lang, target=target) for lang, target in targets]
        for w in errs.warnings:
            print("warning: " + w, file=sys.stderr)
        if "stale" in results:
            errs.error("inject-home: stale story block(s): " + ", ".join(str(target) for (_, target), result in zip(targets, results) if result == "stale"))
        if errs.errors:
            print("\nBUILD FAILED (%d error%s):" % (len(errs.errors), "" if len(errs.errors) == 1 else "s"),
                  file=sys.stderr)
            for e in errs.errors:
                print("  - " + e, file=sys.stderr)
            return 1
        for (_, target), result in zip(targets, results):
            print(("wrote" if result == "written" else "checked unchanged") + " story block: " + str(target))
        return 0

    if args.preview_home:                                   # homepage story preview only
        check_dict_pair(HOME_PARTIAL, errs)
        check_dict_pair("pipeline_strip", errs)
        check_story_assets(errs)
        done = [l for l in langs if build_home_preview(l, errs, write=not args.check)]
        for w in errs.warnings:
            print("warning: " + w, file=sys.stderr)
        if errs.errors:
            print("\nBUILD FAILED (%d error%s):" % (len(errs.errors), "" if len(errs.errors) == 1 else "s"),
                  file=sys.stderr)
            for e in errs.errors:
                print("  - " + e, file=sys.stderr)
            return 1
        for l in done:
            print(("checked" if args.check else "wrote") + " deploy_output/_landing_options/final_%s.html" % l)
        return 0

    # dictionary files: each pair checked once; with --page only the files that page can see
    if args.page:
        names = {"common", args.page}
        names.update(partials_used(read_text(PAGES_DIR / (args.page + ".html")), errs))
    else:
        names = {p.name.rsplit(".", 2)[0] for p in I18N_DIR.glob("*.json")}
    for name in sorted(names):
        check_dict_pair(name, errs)

    # merged key sets per page must agree between languages
    for page in pages:
        used = partials_used(read_text(PAGES_DIR / (page + ".html")), errs)
        merged = {}
        for lang in LANGS:
            keys = set()
            for name in ["common"] + used + [page]:
                keys |= set(load_dict(name, lang, errs) or {})
            merged[lang] = keys
        for k in sorted(merged["en"] - merged["zh"]):
            errs.error("page %s: key %r present in en, missing in zh" % (page, k))
        for k in sorted(merged["zh"] - merged["en"]):
            errs.error("page %s: key %r present in zh, missing in en" % (page, k))

    written = []
    for page in pages:
        for lang in langs:
            if build_page(page, lang, errs, write=not args.check, check_current=args.check):
                written.append("viz/%s/%s.html" % (lang, page))

    if not args.page and (PARTIALS_DIR / (HOME_PARTIAL + ".html")).is_file():
        # the homepage story block is not part of any viz page: still validate it on every full build/check
        check_story_assets(errs)
        for lang in langs:
            build_home_preview(lang, errs, write=False)

    for w in errs.warnings:
        print("warning: " + w, file=sys.stderr)
    if errs.errors:
        print("\nBUILD FAILED (%d error%s):" % (len(errs.errors), "" if len(errs.errors) == 1 else "s"),
              file=sys.stderr)
        for e in errs.errors:
            print("  - " + e, file=sys.stderr)
        return 1
    for w in written:
        print("wrote " + str(output_root / w))
    if args.check:
        print("check ok: %d page(s) x %d language(s)" % (len(pages), len(langs)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
