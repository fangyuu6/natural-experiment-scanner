"""The pre-V5 Policy database's colour-coded detail panel, for Screening (R28).

Owner, 2026-10-14: opening a candidate on Screening shows the original panel he designed, with its 11 colour-coded blocks and the violet chapter
strip, as designed. Nothing is copied into the Screening builder. The panel's script and CSS are read from scripts/zh_pages/policy_db.en.html (the
page All records is built from) on every build, and the Chinese text is that page's own dictionary (scripts/zh_pages/i18n/*.zh.json) applied with the
pair rules of scripts/zh_pages/build.py. A change to the source shows up in the next Screening build; a change that no longer fits an edit below
stops the build.

What Screening changes is listed in EDITS and nowhere else:
  - the Overview pill names the score "AI design score" (the label every Screening card uses), not "Score";
  - the RD assignment rule goes through the cleaner the Screening card uses (no raw RD tokens);
  - the finished HTML goes through scCleanHtml, which removes raw date and snake_case tokens from the text and the tooltips (no raw date tokens).
"""
from __future__ import annotations

from functools import lru_cache
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PAGE = 'policy_db'

# The functions of the panel, in the order the page defines them: helpers, the two API calls, the chapter strip, the blocks.
FUNCTIONS = ('escape', 'pl', 'cleanLLM', 'parseStudies', 'displayDesign', 'fetchDetail', 'fetchGroup',
             'buildChapterStripHtml', 'renderSiblingDetail', 'buildDetailHtml')
# The chapter strip's buttons call this by name from inline onclick, so the page keeps it on window.
WINDOW_HOOK = 'window.renderSiblingDetail = renderSiblingDetail;'

# Screening's changes to the original script: (name, English text, Chinese text, replacement). Each text must occur exactly once in its language.
EDITS = (
    ('score label', '">Score: \'+scoreDisplay+\'</span>\'', '">综合得分：\'+scoreDisplay+\'</span>\'', '">\'+scText.score+scColon+scoreDisplay+\'</span>\''),
    ('RD assignment rule', 'escape(cleanLLM(r.rd_info))', 'escape(cleanLLM(r.rd_info))', "escape(scDisplayElement('rd_info',cleanLLM(r.rd_info)))"),
    ('cleaning wrapper', 'async function buildDetailHtml(r, containerId) {', 'async function buildDetailHtml(r, containerId) {', 'async function buildDetailHtmlLegacy(r, containerId) {'),
)

# CSS: the rules of the page's own <style> whose selectors name a part of the panel. Selectors that only exist in its table (the row, the toggle
# icon, the chapter badge of a closed row, the filters) stay behind; the two Chinese rules that undo inline italics and 12 px inside the table's
# open row are kept and aimed at Screening's panel.
CSS_KEYS = re.compile(r'detail|\.ww-|chapter|lit-note|\.studies|jur-badge|pitch-callout|blockquote|loading')
CSS_TABLE_ONLY = re.compile(r'#tableView|\.detail-row|\.expand|\.main-row|\.t-extra|\.chapter-badge|\.col-title')
CSS_ZH_INLINE = re.compile(r'^:lang\(zh\) #tableView \[style\*="[^"]+"\]$')


class SourceChanged(ValueError):
    """The original page no longer has what Screening reads from it."""


def _zh():
    from scripts.zh_pages import build as zh
    return zh


@lru_cache(maxsize=1)
def source_text():
    """The original page exactly as the Chinese build reads it (BOM dropped, LF newlines)."""
    return _zh().load_source(PAGE)[0]


# --- reading the script ----------------------------------------------------------------------------------------------------------------

def _past_block(text, open_index):
    """Index just past the brace that closes text[open_index]; strings, regular expressions and comments are stepped over."""
    depth, i, n, prev = 0, open_index, len(text), ''
    while i < n:
        c, nxt = text[i], text[i + 1] if i + 1 < n else ''
        if c == '/' and nxt == '/':
            i = text.find('\n', i)
            i = n if i < 0 else i
            continue
        if c == '/' and nxt == '*':
            j = text.find('*/', i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in '"\'`':
            j = i + 1
            while j < n and text[j] != c:
                j += 2 if text[j] == '\\' else 1
            i, prev = j + 1, c
            continue
        if c == '/' and prev in '(,=:[!&|?{};':
            j, in_class = i + 1, False
            while j < n and text[j] != '\n':
                if text[j] == '\\':
                    j += 2
                    continue
                if text[j] == '[':
                    in_class = True
                elif text[j] == ']':
                    in_class = False
                elif text[j] == '/' and not in_class:
                    break
                j += 1
            i, prev = j + 1, ')'
            continue
        if c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if not depth:
                return i + 1
        if not c.isspace():
            prev = c
        i += 1
    raise SourceChanged('unbalanced braces in the original page script')


def _function(text, name):
    found = re.search(r'^    (?:async )?function ' + name + r'\(', text, re.M)
    if not found:
        raise SourceChanged('the original page has no function ' + name)
    return text[found.start():_past_block(text, text.index('{', found.end()))]


def _statement(text, start):
    """The declaration that begins with `start` at the start of a line, through its closing brace and semicolon (and newline)."""
    found = re.search(r'^' + re.escape(start), text, re.M)
    if not found:
        raise SourceChanged('the original page has no ' + start.strip())
    end = _past_block(text, text.index('{', found.start()))
    return text[found.start():end] + (';' if text[end:end + 1] == ';' else '') + '\n'


@lru_cache(maxsize=None)
def original(lang):
    """(topic map, functions) of the original script as the page of that language carries them, before any Screening edit."""
    text, zh = source_text(), _zh()
    topic = _statement(text, '    const topicDisplayMap = {')
    if WINDOW_HOOK not in text:
        raise SourceChanged('the original page no longer puts renderSiblingDetail on window')
    pieces = [_function(text, name) + ('\n' + '    ' + WINDOW_HOOK if name == 'renderSiblingDetail' else '') for name in FUNCTIONS]
    joined = '\n'.join([topic] + pieces) + '\n'
    if lang == 'en':
        return joined
    errors = []
    pairs = zh.load_pairs(zh.I18N / 'common.zh.json', errors) + zh.load_pairs(zh.I18N / 'policy_db.zh.json', errors)
    translated, _ = zh.apply_pairs(joined, pairs, 'Screening detail', errors, None, soft=True)
    if errors:
        raise SourceChanged('; '.join(errors))
    # The page's display vocabulary is defined by the pair that opens its script.
    opening = [pair for pair in pairs if pair['en'] == '<script>\n']
    if len(opening) != 1 or 'const ZH_VOCAB' not in opening[0]['zh'] or 'function zhLabel' not in opening[0]['zh']:
        raise SourceChanged('the Chinese dictionary no longer defines ZH_VOCAB and zhLabel in the pair that opens the script')
    return opening[0]['zh'].replace('<script>\n', '', 1) + translated


def absolute_links(code, lang):
    """The site's rule for the group link, as build_v5.legacy_records applies it to All records: the page's relative links become root-absolute."""
    return re.sub(r'(href=["\'])(policy_groups|documentation)\.html', lambda m: m[1] + ('/zh/' if lang == 'zh' else '/') + m[2] + '.html', code)


def edited(code, lang):
    for name, english, chinese, replacement in EDITS:
        old = chinese if lang == 'zh' else english
        if code.count(old) != 1:
            raise SourceChanged('the original page no longer fits the Screening edit "' + name + '" (' + str(code.count(old)) + ' matches)')
        code = code.replace(old, replacement)
    return code


def code(lang):
    """The panel's script for one language, with Screening's edits."""
    return edited(absolute_links(original(lang), lang), lang)


def script(lang):
    """JavaScript for the Screening page: the panel as scDetail. Pure display helpers (scCleanHtml, scDisplayElement, scIsZh) come from the page."""
    return ('// SCREENING DETAIL START: read from scripts/zh_pages/policy_db.en.html at build time (scripts/viz_site/legacy_detail.py)\n'
            'const scDetail=(()=>{\n'
            "const state={ignore_data:false},detailCache=new Map(),groupCache=new Map(),scColon=scIsZh()?'\\uff1a':': ';\n"
            + code(lang) +
            'async function buildDetailHtml(r, containerId) { return scCleanHtml(await buildDetailHtmlLegacy(r, containerId)); }\n'
            'return {fetchDetail, buildDetailHtml};\n'
            '})();\n'
            '// SCREENING DETAIL END\n')


# --- reading the CSS -------------------------------------------------------------------------------------------------------------------

def _split_selectors(selectors):
    parts, depth, current = [], 0, ''
    for c in selectors:
        depth += c == '('
        depth -= c == ')'
        if c == ',' and not depth:
            parts.append(current.strip())
            current = ''
        else:
            current += c
    return parts + [current.strip()]


def _past_css_block(css, open_index):
    depth, i = 0, open_index
    while i < len(css):
        c = css[i]
        if c in '"\'':
            i = css.index(c, i + 1)
        elif c == '{':
            depth += 1
        elif c == '}':
            depth -= 1
            if not depth:
                return i + 1
        i += 1
    raise SourceChanged('unbalanced braces in the original page styles')


def _rules(css, condition=None):
    """[(condition, selectors, body)] of a stylesheet without comments; the rules inside a @media block carry its condition."""
    found, i = [], 0
    while True:
        while i < len(css) and css[i].isspace():
            i += 1
        if i >= len(css):
            return found
        start = css.index('{', i)
        end = _past_css_block(css, start)
        head, body = css[i:start].strip(), css[start + 1:end - 1]
        if head.startswith('@media'):
            found.extend(_rules(body, head))
        elif not head.startswith('@'):
            found.append((condition, head, body))
        i = end


@lru_cache(maxsize=1)
def css_rules():
    """The panel's rules from the page's own <style>, in source order: [(media condition or None, selector list, declarations)]."""
    blocks = re.findall(r'<style>(.*?)</style>', source_text(), re.S)
    if len(blocks) < 2:
        raise SourceChanged('the original page no longer has its own <style> block')
    kept = []
    for condition, selectors, body in _rules(re.sub(r'/\*.*?\*/', '', blocks[1], flags=re.S)):
        chosen = []
        for selector in _split_selectors(selectors):
            if CSS_ZH_INLINE.match(selector):
                chosen.append(selector.replace('#tableView', '.sc-detail'))
            elif CSS_KEYS.search(selector) and not CSS_TABLE_ONLY.search(selector):
                chosen.append(selector)
        if chosen:
            kept.append((condition, ', '.join(chosen), body.strip()))
    if len(kept) < 40:
        raise SourceChanged('far fewer panel rules than expected in the original page: ' + str(len(kept)))
    return tuple(kept)


def css():
    """The panel's rules as one stylesheet text."""
    out, open_condition = [], None
    for condition, selectors, body in css_rules():
        if condition != open_condition:
            out.append('}' if open_condition else '')
            out.append('@media ' + condition[len('@media'):].strip() + '{' if condition else '')
            open_condition = condition
        out.append(selectors + '{' + re.sub(r'\s+', ' ', body).strip() + '}')
    out.append('}' if open_condition else '')
    return '\n'.join(part for part in out if part)
