#!/usr/bin/env python3
"""Build zh-CN copies of the three static pages (stdlib only, Python 3.9+).

    python3 scripts/zh_pages/build.py             # write deploy_output/zh/<page>.html
    python3 scripts/zh_pages/build.py --check     # validate dictionaries + report, write nothing
    python3 scripts/zh_pages/build.py --inventory # regenerate scripts/zh_pages/inventory/<page>.txt

Input : scripts/zh_pages/<page>.en.html; plus scripts/zh_pages/i18n/*.zh.json
Output: deploy_output/zh/<page>.html and the canonical EN documentation page

Pipeline per page (order matters):
  1. read EN (BOM stripped, CRLF -> LF, stale baked-in Cloudflare scripts removed); a homepage story block
     (<!-- STORY:START --> ... <!-- STORY:END -->, maintained by scripts/viz_site/build.py --inject-home) is set aside
  2. common.zh.json pairs, then <page>.zh.json pairs (exact substring replacement, in order; never applied to the story)
  3. <html lang="zh-CN">, canonical/og:url -> /zh/, link rewriting, language toggle (pill, 中 active)
  3b. the set-aside story block is replaced by the ZH rendering of the same partial (viz_site build.render_story_block)
  4. integrity check (ids / value= / data-* / /api/ strings must equal the EN page)
  5. untranslated-candidate report

Exit codes: 0 ok | 1 fatal (missing input, bad JSON, missing anchor) | 2 dictionary
errors (stale/ambiguous pairs) | 3 integrity check failed.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from collections import Counter, OrderedDict
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DEPLOY = ROOT / "deploy_output"
I18N = HERE / "i18n"
INVENTORY = HERE / "inventory"
PAGES = ["policy_db", "policy_groups", "documentation"]
PAGE_FILES = {p + ".html" for p in PAGES}
SITE_HOST = "natural-experiment-scanner.com"

CJK = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
LATIN_WORD = re.compile(r"[A-Za-z]{3,}")
# Strings that legitimately stay English; extend via i18n/keep_english.txt (one exact string per line).
KEEP_EXACT = {"RD", "DID", "DiD", "GitHub", "LinkedIn", "Fangyu Liu", "MIT License", "English",
              "LLM", "API", "CSV", "HTML", "D1", "R2", "Worker", "SQLite", "FTS5", "JSON",
              "AbortError", "DOMContentLoaded", "TWFE"}


class BuildError(Exception):
    def __init__(self, msg: str, code: int = 1):
        super().__init__(msg)
        self.code = code


# --------------------------------------------------------------------------- loading

CF_SCRIPT = re.compile(r"<script>\(function\(\)\{function c\(\)\{var b=a\.contentDocument.*?</script>", re.S)
CF_ANCHOR = re.compile(r'<a href="https://[^"/]+/cdn-cgi/content\?id=[^"]*"[^>]*></a>')


def load_source(page: str):
    path = HERE / (page + ".en.html")
    if not path.is_file():
        raise BuildError("missing input: %s" % path)
    text = path.read_bytes().decode("utf-8-sig")            # drops BOM
    text = text.replace("\r\n", "\n").replace("\r", "\n")   # pairs are written with \n
    text, n1 = CF_SCRIPT.subn("", text)                     # stale edge-injected snippets saved from the live site
    text, n2 = CF_ANCHOR.subn("", text)
    return text, n1 + n2


def render_screening_numbers(text: str) -> str:
    """Fill Method's localized screening facts from the existing funnel sidecar."""
    if "{{screening_" not in text:
        return text
    path = DEPLOY / "funnel_data.json"
    if not path.is_file():
        raise BuildError("missing screening data: %s" % path)
    data = json.loads(path.read_text(encoding="utf-8"))
    notes = data["notes"]
    counts = data["cross"]["*||*"]["n"]
    scores = notes["data_feasibility_at_min_gate_input"]
    values = {
        "id_verified": format(notes["verify_level_among_strict_enacted"]["id_verified"], ","),
        "enacted": format(counts[1], ","),
        "shortlist": format(counts[-1], ","),
        "zero_public": format(notes["final_stage_with_zero_public_dataset_matches"], ","),
        "data_share": "%.1f%%" % (100 * scores["n_ge3"] / scores["n"]),
        "data_input": format(scores["n"], ","),
        "missing_share": "%.0f%%" % (100 * notes["removed_by_kind"]["missing"] / notes["removed_total"]),
        "removed_total": format(notes["removed_total"], ","),
    }
    for name, value in values.items():
        text = text.replace("{{screening_" + name + "}}", value)
    if "{{screening_" in text:
        raise BuildError("unknown screening number placeholder")
    return text


def load_pairs(path: Path, errors: list):
    """Return list of dicts {en, zh, all, optional, ref}; append format problems to errors."""
    if not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as exc:
        raise BuildError("%s: invalid JSON (%s)" % (path.name, exc))
    if not isinstance(data, list):
        raise BuildError("%s: top level must be a JSON list" % path.name)
    pairs = []
    for i, item in enumerate(data):
        ref = "%s[%d]" % (path.name, i)
        if isinstance(item, (list, tuple)) and len(item) == 2:
            en, zh, flags = item[0], item[1], {}
        elif isinstance(item, dict) and "en" in item and "zh" in item:
            en, zh, flags = item["en"], item["zh"], item
        else:
            errors.append("%s: entry must be [en, zh] or {en, zh, all?}" % ref)
            continue
        if not isinstance(en, str) or not isinstance(zh, str) or not en:
            errors.append("%s: en/zh must be strings and en must be non-empty" % ref)
            continue
        pairs.append({"en": en.replace("\r\n", "\n"), "zh": zh.replace("\r\n", "\n"),
                      "all": bool(flags.get("all")), "optional": bool(flags.get("optional")), "ref": ref})
    return pairs


def short(s: str, n: int = 70) -> str:
    s = s.replace("\n", "\\n")
    return s if len(s) <= n else s[: n - 1] + "…"


def apply_pairs(text: str, pairs: list, label: str, errors: list, hits: Counter = None, soft: bool = False):
    applied = 0
    for p in pairs:
        n = text.count(p["en"])
        if hits is not None:
            hits[p["ref"]] += n
        if n == 0:
            if not (soft or p["optional"]):
                errors.append("%s: %s not found (stale?): %r" % (label, p["ref"], short(p["en"])))
            continue
        if n > 1 and not p["all"]:
            errors.append("%s: %s matches %d times, add context or use {\"all\": true}: %r"
                          % (label, p["ref"], n, short(p["en"])))
            continue
        text = text.replace(p["en"], p["zh"])
        applied += 1
    return text, applied


# --------------------------------------------------------------------------- transforms

def set_lang(text: str) -> str:
    m = list(re.finditer(r"<html\b[^>]*>", text))
    if len(m) != 1:
        raise BuildError("expected exactly one <html> tag, found %d" % len(m))
    tag = m[0].group(0)
    new = re.sub(r'\blang="[^"]*"', 'lang="zh-CN"', tag) if "lang=" in tag else tag[:-1] + ' lang="zh-CN">'
    return text[: m[0].start()] + new + text[m[0].end():]


def _own_page_path(path: str):
    """'/policy_db.html' or '/' -> page filename, else None."""
    if path in ("/", ""):
        return "policy_db.html"
    name = path.lstrip("/")
    return name if name in PAGE_FILES and path.startswith("/") and "/" not in name else None


def rewrite_links(text: str, stats: Counter) -> str:
    def repl(m):
        attr, q, url = m.group(1), m.group(2), m.group(3)
        new = url
        low = url.lower()
        if url == "" or url.startswith(("#", "mailto:", "tel:", "javascript:", "data:")):
            stats["kept"] += 1
        elif re.match(r"^(?:https?:)?//", low):
            host = re.sub(r"^(?:https?:)?//", "", url).split("/")[0].lower()
            path = "/" + re.sub(r"^(?:https?:)?//[^/]*/?", "", url)
            base, suffix = re.match(r"([^#?]*)(.*)", path).groups()
            page = _own_page_path(base) if host.endswith(SITE_HOST) else None
            if page:
                new = "%s//%s/zh/%s%s" % (url.split("//")[0], host, page, suffix)
                stats["absolute->zh"] += 1
            else:
                stats["external"] += 1
        elif url.startswith("/"):
            base, suffix = re.match(r"([^#?]*)(.*)", url).groups()
            page = _own_page_path(base)
            if page and not base.startswith("/zh/"):
                new = "/zh/" + page + suffix
                stats["root->zh"] += 1
            else:
                stats["root-absolute kept"] += 1      # /api/..., /score_distribution.png, /zh/...
        else:
            base, suffix = re.match(r"([^#?]*)(.*)", url).groups()
            base = base[2:] if base.startswith("./") else base
            if base in PAGE_FILES:
                stats["relative page kept"] += 1      # resolves to /zh/<page> from /zh/
            else:
                new = "/" + base + suffix             # assets live at the site root, not under /zh/
                stats["relative asset->root"] += 1
        return "%s=%s%s%s" % (attr, q, new, q)

    text = re.sub(r"\b(href|src)=([\"'])([^\"'<>]*)\2", repl, text)
    # og:url (content=, not href=)
    def og(m):
        url = m.group(2)
        host_path = re.match(r"(https?://[^/]+)(/[^#?]*)(.*)", url)
        if host_path and host_path.group(1).endswith(SITE_HOST) and _own_page_path(host_path.group(2)):
            stats["og:url->zh"] += 1
            return m.group(1) + host_path.group(1) + "/zh/" + _own_page_path(host_path.group(2)) + host_path.group(3) + '"'
        return m.group(0)
    return re.sub(r'(<meta\s+property="og:url"\s+content=")([^"]*)"', og, text)


def shared_chrome():
    # Script invocation has scripts/zh_pages on sys.path, not the repository root.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from scripts.viz_site import shared_chrome as chrome
    return chrome


LANG_SWITCH_ANCHOR = re.compile(r'<a\b[^>]*\bclass="lang-switch"[^>]*>.*?</a>', re.S)


def lang_switch_zh(page: str) -> str:
    return shared_chrome().language_switch("zh", page, legacy=True)


def apply_lang_switch(text: str, page: str) -> str:
    """zh pages get the pill toggle with the 中 side active.

    The EN pages carry the toggle themselves (EN side active, href = their /zh/ counterpart); it is regenerated here
    in its zh form. Only when an EN page has no toggle yet is one injected (last in the top-right link group, i.e.
    after the LinkedIn anchor), together with its CSS if the page lacks it."""
    n = len(LANG_SWITCH_ANCHOR.findall(text))
    if n > 1:
        raise BuildError("%s: %d language toggles found, expected at most one" % (page, n))
    if n == 1:
        return LANG_SWITCH_ANCHOR.sub(lambda m: lang_switch_zh(page), text, count=1)
    anchor = None
    for rx in (r'<a href="https://www\.linkedin\.com/[^"]*"[^>]*>.*?</a>', r'<a href="https://github\.com/[^"]*"[^>]*>.*?</a>'):
        found = list(re.finditer(rx, text, re.S))
        if found:
            anchor = found[0]
            break
    if not anchor:
        raise BuildError("%s: cannot place language toggle (no LinkedIn/GitHub anchor found)" % page)
    text = text[: anchor.end()] + "\n      " + lang_switch_zh(page) + text[anchor.end():]
    if ".lang-switch" not in text.split("<body")[0] and "</head>" in text:
        text = text.replace("</head>", shared_chrome().render_slots("zh", page, features=viz_build().chrome_features())["style"] + "\n</head>", 1)
    return text


# --------------------------------------------------------------------------- homepage story block

STORY_RX = re.compile(r"<!-- STORY:START -->.*?<!-- STORY:END -->", re.S)
STORY_SENTINEL = "<!--@@ZH-STORY@@-->"      # stands in for the block while the pairs / link rewriting run
_viz_build = None


def viz_build():
    """scripts/viz_site/build.py imported as a module (it owns render_partial / render_story_block)."""
    global _viz_build
    if _viz_build is None:
        path = ROOT / "scripts" / "viz_site" / "build.py"
        if not path.is_file():
            raise BuildError("missing %s (needed for the homepage story block)" % path)
        spec = importlib.util.spec_from_file_location("viz_site_build", path)
        mod = importlib.util.module_from_spec(spec)
        sys.modules["viz_site_build"] = mod
        spec.loader.exec_module(mod)
        _viz_build = mod
    return _viz_build


def set_aside_story(page: str, text: str):
    """Return (text with the story block replaced by STORY_SENTINEL, found). policy_db must carry exactly one block."""
    n_start, n_end = text.count("<!-- STORY:START -->"), text.count("<!-- STORY:END -->")
    if page == "policy_db" and (n_start, n_end) != (1, 1):
        raise BuildError("%s: expected one <!-- STORY:START --> and one <!-- STORY:END --> marker, found %d and %d "
                         "(run python3 scripts/viz_site/build.py --inject-home)" % (page, n_start, n_end))
    if (n_start, n_end) == (0, 0):
        return text, False
    blocks = STORY_RX.findall(text)
    if (n_start, n_end) != (1, 1) or len(blocks) != 1:
        raise BuildError("%s: unbalanced STORY markers (%d start, %d end)" % (page, n_start, n_end))
    return STORY_RX.sub(lambda _m: STORY_SENTINEL, text, count=1), True


def render_story(lang: str) -> str:
    """Render current story data in memory, including markers and LF newlines."""
    vb = viz_build()
    errs = vb.Errors()
    vb.check_dict_pair(vb.HOME_PARTIAL, errs)
    vb.check_dict_pair("pipeline_strip", errs)
    block = vb.render_story_block(lang, errs)
    if errs.errors:
        raise BuildError("homepage story (%s) failed:\n  - " % lang + "\n  - ".join(errs.errors), 2)
    return block


def render_zh_story() -> str:
    return render_story("zh")


# --------------------------------------------------------------------------- integrity

STRUCT = OrderedDict([
    ("id=", re.compile(r'\bid="([^"]*)"')),
    ("value=", re.compile(r'\bvalue="([^"]*)"')),
    ("data-*", re.compile(r'\b(data-[a-z-]+="[^"]*")')),
    ("/api/ strings", re.compile(r"[\"'](/api/[^\"'?]*)")),
    ("DOM lookups", re.compile(r"(?:getElementById|querySelectorAll|querySelector)\(\s*[\"']([^\"']+)[\"']")),
])


def integrity(en: str, zh: str):
    problems = []
    for name, rx in STRUCT.items():
        a, b = Counter(rx.findall(en)), Counter(rx.findall(zh))
        if a != b:
            gone, new = list((a - b).elements()), list((b - a).elements())
            problems.append("%s differ: removed %s added %s" % (name, [short(x, 40) for x in gone[:5]],
                                                                  [short(x, 40) for x in new[:5]]))
    for m in re.finditer(r"fetch\(\s*[\"'`]([^\"'`]+)", zh):
        if not m.group(1).startswith(("/", "http")):
            problems.append("relative fetch() path would break under /zh/: %r" % m.group(1))
    return problems


# --------------------------------------------------------------------------- string scan

def js_string_literals(src: str):
    """Return [(offset, decoded_value)] for string literals in JS source (comments/regex aware)."""
    out, i, n, prev = [], 0, len(src), ""
    while i < n:
        c = src[i]
        nxt = src[i + 1] if i + 1 < n else ""
        if c == "/" and nxt == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and nxt == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "\"'`":
            j, buf = i + 1, []
            while j < n:
                d = src[j]
                if d == "\\" and j + 1 < n:
                    buf.append({"n": "\n", "t": "\t"}.get(src[j + 1], src[j + 1]))
                    j += 2
                    continue
                if d == c or (d == "\n" and c != "`"):
                    break
                buf.append(d)
                j += 1
            out.append((i, "".join(buf)))
            i, prev = j + 1, c
            continue
        if c == "/" and (prev == "" or prev in "(,=:[!&|?{};"):      # regex literal
            j, in_cls = i + 1, False
            while j < n and src[j] != "\n":
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    in_cls = True
                elif src[j] == "]":
                    in_cls = False
                elif src[j] == "/" and not in_cls:
                    break
                j += 1
            i, prev = j + 1, ")"
            continue
        if not c.isspace():
            prev = c
        i += 1
    return out


def is_candidate(s: str, keep: set, relaxed: bool = False) -> bool:
    s = re.sub(r"\s+", " ", s).strip()
    if not s or CJK.search(s) or not LATIN_WORD.search(s):
        return False
    if s in KEEP_EXACT or s in keep:
        return False
    if re.match(r"^(?:https?://|mailto:|/|#|\.)", s) or "@" in s and " " not in s:
        return False
    if " " not in s:
        core = s.rstrip(":.\u2026")
        if re.search(r"[_/=\[\]<>]", core) or re.search(r"\w\.\w", core):        # identifiers, paths, code
            return False
        if relaxed:       # word sitting between HTML tags inside a JS string, e.g. '>skip</span>'
            return bool(LATIN_WORD.search(core)) and not re.search(r"[A-Z][a-z]+[A-Z]", core)
        if re.search(r"[()]", core) or not re.match(r"^[A-Z][A-Za-z-]*$", core) or not LATIN_WORD.search(core):
            return False                                   # outside tags only Capitalised words are labels
    return True


JS_CODE = re.compile(r"=>|\w\([^)]*\)\s*;|[a-z-]+:\s*[^;\s]+;|#[0-9a-fA-F]{3,6}\b"
                     r"|\b(?:style|class|onclick|colspan|href|target)=|=\W*$|^[a-z-]+:\s*[\w#.%-]+$|^[,)]")


def js_segments(value: str):
    """Split a JS string literal that may contain HTML into UI-text segments (text nodes + title/placeholder attrs)."""
    segs = []
    for m in re.finditer(r"\b(?:title|placeholder|aria-label|alt)=\"([^\"]*)(?:\"|$)", value):
        segs.append((m.group(1).strip(), True))
    body = re.sub(r"<[^>]*>", "\x00", value)
    body = re.sub(r"<[^>]*$", "\x00", body)                 # tag cut off by string concatenation
    for seg in body.split("\x00"):
        for part in re.split(r"[\"']\s*/?>", seg):          # '">' closes an attribute opened in an earlier literal
            part = part.strip(" \t\n\"'>;")
            if part:
                segs.append((part, False))
    return segs


BLOCK_TAGS = {"p", "li", "td", "th", "h1", "h2", "h3", "h4", "h5", "h6", "div", "footer", "label", "button",
              "option", "a", "text", "title", "section"}


class Scanner(HTMLParser):
    """Collects user-visible strings; the 4th tuple field is a 'unit' key (block element) for effort counting."""
    VOID = {"meta", "link", "br", "img", "input", "hr", "col", "source"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.items, self.js_blocks = [], [], []
        self._script, self._script_line, self._script_buf, self._opt_value = None, 0, [], ""
        self._bid = 0

    def _hint(self, tag, attrs):
        d = dict(attrs)
        h = "<" + tag + ("#" + d["id"] if d.get("id") else "") + ("." + d["class"].split()[0] if d.get("class") else "")
        return h + ">"

    def handle_starttag(self, tag, attrs):
        d = dict(attrs)
        line = self.getpos()[0]
        for key in ("placeholder", "title", "aria-label", "alt"):
            if d.get(key):
                self.items.append(("attr", "L%d @%s %s" % (line, key, self._hint(tag, attrs)), d[key], ("a", line, key)))
        if tag == "meta" and d.get("content") and (d.get("name") in ("description", "keywords")
                                                   or (d.get("property") or "").startswith("og:")):
            if d.get("property") != "og:url" and d.get("property") != "og:type":
                self.items.append(("meta", "L%d %s" % (line, d.get("name") or d.get("property")), d["content"],
                                   ("m", line, d.get("name") or d.get("property"))))
        if tag == "option":
            self._opt_value = d.get("value", "")
        if tag in ("script", "style"):
            self._script, self._script_line = tag, line
        if tag not in self.VOID:
            bid = None
            if tag in BLOCK_TAGS:
                self._bid += 1
                bid = self._bid
            self.stack.append((tag, self._hint(tag, attrs), bid))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if self.stack and self.stack[-1][0] == tag:
            self.stack.pop()

    def handle_endtag(self, tag):
        if tag == "script" and self._script == "script":
            self.js_blocks.append((self._script_line, "".join(self._script_buf)))
            self._script_buf = []
        if tag in ("script", "style"):
            self._script = None
        for k in range(len(self.stack) - 1, -1, -1):
            if self.stack[k][0] == tag:
                del self.stack[k:]
                break

    def handle_data(self, data):
        if self._script == "script":
            self._script_buf.append(data)      # html.parser delivers CDATA in several chunks
            return
        if self._script == "style" or any(e[0] in ("code", "pre", "style") for e in self.stack):
            return
        txt = re.sub(r"\s+", " ", data).strip()
        if not txt:
            return
        tag, hint, _ = self.stack[-1] if self.stack else ("", "<root>", None)
        if tag == "option" and self._opt_value:
            hint = "<option value=%s>" % self._opt_value
        unit = next((e[2] for e in reversed(self.stack) if e[2] is not None), ("t", self.getpos()))
        kind = "title" if tag == "title" else "text"
        self.items.append((kind, "L%d %s" % (self.getpos()[0], hint), txt, unit))


def func_name_at(js: str, pos: int) -> str:
    last = ""
    for m in re.finditer(r"(?:async\s+)?function\s+(\w+)|(?:const|let)\s+(\w+)\s*=", js[:pos]):
        last = m.group(1) or m.group(2)
    return last or "top"


def scan_strings(text: str, keep: set):
    """Return list of (kind, hint, string, unit) candidates (no CJK, looks like UI English)."""
    sc = Scanner()
    sc.feed(text)
    sc.close()
    found = [it for it in sc.items if is_candidate(it[2], keep)]
    for line0, js in sc.js_blocks:
        lines = js.split("\n")
        for off, val in js_string_literals(js):
            line = line0 + js.count("\n", 0, off)
            if re.search(r"console\.\w+\(", lines[js.count("\n", 0, off)]):
                continue
            has_tag = "<" in val or ">" in val
            for seg, is_attr in js_segments(val):
                if not is_attr and JS_CODE.search(seg):
                    continue
                if is_candidate(seg, keep, relaxed=has_tag):
                    found.append(("js", "L%d js:%s" % (line, func_name_at(js, off)),
                                  re.sub(r"\s+", " ", seg).strip(), ("j", off)))
    return found


def load_keep(i18n: Path = I18N) -> set:
    p = i18n / "keep_english.txt"
    return {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()} if p.is_file() else set()


def summarize(found: list) -> str:
    kinds = Counter(f[0] for f in found)
    uniq = len({f[2] for f in found})
    units = len({f[3] for f in found})
    parts = ", ".join("%s %d" % (k, kinds[k]) for k in ("title", "meta", "text", "attr", "js") if kinds.get(k))
    return "%d candidates = %d unique strings, %d block units (%s)" % (len(found), uniq, units, parts)


# --------------------------------------------------------------------------- main

def write_inventory(page: str, found: list):
    order, seen = [], {}
    for kind, hint, s, _unit in found:
        if s in seen:
            seen[s][3] += 1
        else:
            seen[s] = [kind, hint, s, 1]
            order.append(s)
    units = len({f[3] for f in found})
    lines = ["# %s.html: user-visible English strings (TSV: kind<TAB>location<TAB>string<TAB>count)" % page,
             "# auto-scan: %d unique strings, %d occurrences, ~%d block units (<p>/<td>/<li>/... count once)."
             % (len(order), len(found), units),
             "# Excluded: <code>/<pre> text, identifiers, numbers, API data values. Text nodes are split at inline tags",
             "# (<strong>, <code>, <a>): translate whole <p>/<td> raw substrings, not the fragments. L### = source line.",
             "# js:<function> = string literal inside inline JS (concatenated fragments; keep quotes/escapes valid)."]
    for s in order:
        kind, hint, _, cnt = seen[s]
        lines.append("%s\t%s\t%s\t%d" % (kind, hint, s.replace("\t", " "), cnt))
    manual = INVENTORY / "manual" / (page + ".txt")
    n_manual = 0
    if manual.is_file():
        extra = [ln for ln in manual.read_text(encoding="utf-8").splitlines() if ln.strip()]
        n_manual = sum(1 for ln in extra if not ln.startswith("#"))
        lines.append("# ---- hand-maintained supplement (inventory/manual/%s.txt): fragments the scanner cannot classify ----" % page)
        lines.extend(extra)
    INVENTORY.mkdir(parents=True, exist_ok=True)
    (INVENTORY / (page + ".txt")).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(order), len(found), units, n_manual


def main(argv=None, *, rendered=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--check", action="store_true", help="validate dictionaries, print candidate counts, write nothing")
    ap.add_argument("--inventory", action="store_true", help="regenerate inventory/<page>.txt from the EN pages")
    ap.add_argument("--pages", default=",".join(PAGES), help="comma list (default: all three)")
    ap.add_argument("--out", default=str(DEPLOY / "zh"), help="output dir (default: deploy_output/zh)")
    ap.add_argument("--output-dir", help="isolated site root; writes EN at the root and ZH under zh/")
    ap.add_argument("--i18n", default=str(I18N), help="dictionary dir (default: scripts/zh_pages/i18n)")
    args = ap.parse_args(argv)
    i18n = Path(args.i18n)
    pages = [p.strip() for p in args.pages.split(",") if p.strip()]
    bad = [p for p in pages if p not in PAGES]
    if bad:
        raise BuildError("unknown page(s): %s (choose from %s)" % (bad, PAGES))

    keep = load_keep(i18n)
    dict_errors, fmt_errors = [], []
    common = load_pairs(i18n / "common.zh.json", fmt_errors)
    common_hits, results, en_results = Counter(), {}, {}
    chrome_retired = set()

    for page in pages:
        en, stripped = load_source(page)
        if page == "policy_db":
            # Normalize the generated EN story in memory. Preview and check
            # compare the two current renderings without touching production.
            core, has_story = set_aside_story(page, en)
            if has_story:
                en = core.replace(STORY_SENTINEL, render_story("en"))
        chrome = shared_chrome()
        try:
            en_core, chrome_slots = chrome.detach_slots(en)
        except ValueError as exc:
            raise BuildError(page + ": " + str(exc))
        if chrome_slots:
            # The hub's serialized generation refreshes EN originals too.
            en_results[page] = chrome.restore_slots(render_screening_numbers(en_core), chrome_slots, "en", page,
                                                    features=viz_build().chrome_features())
        elif page == "documentation":
            en_results[page] = render_screening_numbers(en)
        en_core, has_story = set_aside_story(page, en_core)      # story strings live in viz_site/i18n, not in the inventory
        if args.inventory:
            u, t, b, m = write_inventory(page, scan_strings(en_core, keep))
            print("[%s] inventory: %d unique strings, %d occurrences, ~%d block units, +%d manual -> inventory/%s.txt"
                  % (page, u, t, b, m, page))
            continue
        page_pairs = load_pairs(i18n / (page + ".zh.json"), fmt_errors)
        active_common = chrome.body_pairs(common, en_core, chrome_slots)
        chrome_retired.update(p["ref"] for p in common if p not in active_common)
        active_page = chrome.body_pairs(page_pairs, en_core, chrome_slots)
        text, n_common = apply_pairs(en_core, active_common, page + " common", dict_errors, common_hits, soft=True)
        text, n_page = apply_pairs(text, active_page, page, dict_errors)
        text = set_lang(text)
        stats = Counter()
        text = rewrite_links(text, stats)
        if not chrome_slots:
            text = apply_lang_switch(text, page)
        if has_story:
            story = render_zh_story()
            if text.count(STORY_SENTINEL) != 1:
                raise BuildError("%s: story placeholder lost while translating" % page)
            text = text.replace(STORY_SENTINEL, story)
        # Generated chrome is localized directly; body structure remains strict.
        en_body, _ = chrome.detach_slots(en)
        problems = integrity(en_body, text)
        found = scan_strings(text, keep)
        text = render_screening_numbers(text)
        text = chrome.restore_slots(text, chrome_slots, "zh", page,
                                    features=viz_build().chrome_features())
        results[page] = text
        print("[%s] pairs applied: common %d/%d, page %d/%d | baked-in CF snippets removed: %d"
              % (page, n_common, len(common), n_page, len(page_pairs), stripped))
        print("    links: " + (", ".join("%s %d" % kv for kv in sorted(stats.items())) or "none"))
        print("    untranslated candidates: " + summarize(found))
        for p in problems:
            dict_errors.append("%s INTEGRITY: %s" % (page, p))

    if args.inventory:
        return 0
    for c in common:
        if common_hits[c["ref"]] == 0 and not c["optional"] and c["ref"] not in chrome_retired:
            dict_errors.append("common: %s matched no page (stale?): %r" % (c["ref"], short(c["en"])))
    integ = [e for e in dict_errors if "INTEGRITY" in e]
    other = fmt_errors + [e for e in dict_errors if "INTEGRITY" not in e]
    if other or integ:
        print("\nFAILED: %d problem(s)" % (len(other) + len(integ)), file=sys.stderr)
        for e in other + integ:
            print("  - " + e, file=sys.stderr)
        return 2 if other else 3
    if rendered is not None:
        rendered.update(en=en_results, zh=results)
    if args.check:
        print("\n--check OK: dictionaries valid, nothing written.")
        return 0
    out = Path(args.output_dir) / "zh" if args.output_dir else Path(args.out)
    # An isolated ZH preview must keep its refreshed EN counterparts in the same
    # isolated root, rather than writing through to the shared deployment tree.
    isolated = out.resolve() != (DEPLOY / "zh").resolve()
    en_out = out.parent if isolated else DEPLOY
    if isolated and (en_out.resolve() == DEPLOY.resolve() or en_out.resolve() in DEPLOY.resolve().parents or
                     DEPLOY.resolve() in en_out.resolve().parents):
        raise BuildError("isolated --out needs a separate root with a zh subdirectory")
    if isolated and any(en_out.resolve() not in target.resolve().parents for target in
                        [en_out / (page + '.html') for page in en_results] +
                        [out / (page + '.html') for page in results]):
        raise BuildError("isolated preview targets must stay inside their root (including symlinks)")
    out.mkdir(parents=True, exist_ok=True)
    en_out.mkdir(parents=True, exist_ok=True)
    for page, text in en_results.items():
        with open(en_out / (page + ".html"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("wrote %s (%d bytes)" % (en_out / (page + ".html"), len(text.encode("utf-8"))))
    for page, text in results.items():
        with open(out / (page + ".html"), "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print("wrote %s (%d bytes)" % (out / (page + ".html"), len(text.encode("utf-8"))))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BuildError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        sys.exit(exc.code)
