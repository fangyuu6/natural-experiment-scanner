#!/usr/bin/env python3
"""Read-only local artifact checks; no browser, network, manifest or deploy writes."""
import argparse
from html import escape, unescape
from html.parser import HTMLParser
import json
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import unquote, urljoin, urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.release.make_manifest import scan_file
from scripts.viz_site.build import SHOW_GITHUB
from scripts.viz_site.public_page import banned_terms, legend_sizes, PublicText, inherited_robots, _Robots


class Page(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.ids, self.links, self.scripts = [], [], []
        self.script = None
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get('id'):
            self.ids.append(attrs['id'])
        if tag == 'a' and attrs.get('href'):
            self.links.append(attrs['href'])
        if tag == 'script' and attrs.get('type', '') not in ('application/json', 'application/ld+json'):
            self.script = ''

    def handle_data(self, text):
        if self.script is not None:
            self.script += text

    def handle_endtag(self, tag):
        if tag == 'script' and self.script is not None:
            self.scripts.append(self.script)
            self.script = None


class _Disclosures(HTMLParser):
    """The <details> elements that show nothing once their <summary> is set aside."""
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.stack, self.empty, self.summary_depth = [], [], 0
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        top = self.stack[-1] if self.stack else None
        if top and top['summary_done']:
            top['content'] = True  # any element after the summary, a nested <details> included
        if tag == 'details':
            self.stack.append({'id': dict(attrs).get('id') or '(no id)', 'summary_done': False, 'content': False})
        elif tag == 'summary' and top and not top['summary_done']:
            self.summary_depth += 1

    def handle_endtag(self, tag):
        if tag == 'summary' and self.summary_depth:
            self.summary_depth -= 1
            if not self.summary_depth and self.stack:
                self.stack[-1]['summary_done'] = True
        elif tag == 'details' and self.stack:
            node = self.stack.pop()
            if not node['content']:
                self.empty.append(node['id'])

    def handle_data(self, data):
        if self.stack and self.stack[-1]['summary_done'] and data.strip():
            self.stack[-1]['content'] = True


def empty_disclosures(source):
    """ids of the disclosures that open to nothing (Owner, 2026-10-14: Estimates' "Method & materials" did)."""
    return _Disclosures(source).empty


RAW_SCREENING = re.compile(r'\b[a-zA-Z][a-zA-Z0-9-]*_[a-zA-Z0-9_-]+\b|\b\d{4}-\d{2}-\d{2}T\d{2}:\d{2}|date_--')
# Owner, 2026-10-13: papers without a re-estimate are not a public category.
REMOVED_CATEGORY = re.compile(r'not[_ -]yet[_ -]replicated|尚未复现', re.I)


def standalone(number, parts):
    """Visible text parts that state `number` as a count; an anonymous label such as "Paper 461" is not one."""
    pattern = re.compile(r'(?<![\w.,])' + re.escape(str(number)) + r'(?!\w|[.,]\d)')
    return [part for part in parts if pattern.search(re.sub(r'(?:Paper|论文) \d{1,3}', '', part))]


# Owner, 2026-10-13/14: the Home page shows no author block, no update stamp and no paper title; the title is on Screening.
REMOVED_HOME_LINES = {
    'en': ('AEJ draft', 'University of Southern California', 'Results updated', 'Research design, rules and review',
           'AI agents write and run code', 'Earlier public draft', 'Method →',
           # Owner, 2026-10-14 (option B): the recall line and the label that sourced it are on Screening only.
           'Recall of published', 'Published-study benchmark', 'revised draft', '99/113', '87.6%'),
    'zh': ('AEJ 稿', '南加州大学', '结果更新', '研究设计、规则与审阅', '代码由 AI 智能体独立于原作者代码编写并运行',
           '较早公开稿', '方法 →',
           '召回率', '已发表研究基准', '修订稿', '99/113', '87.6%'),
}

# Owner, 2026-10-14 (R21): "independent" no longer describes the second AI coder, on any page.
SECOND_CODER_INDEPENDENT = re.compile(r'second,? independent|independent second|第二个独立|第二位独立|独立的第二')

# Owner, 2026-10-14 (V5.1): below the map the Home is the staircase Screen, Estimate, Explore. The blocks it replaced (doors,
# feature card, Explore text) are gone. Each step is a big word that links to its page and one short line of figures that the
# page itself prints. The words are the staircase's own where they fit the V5 page names (Screen, Estimate), else the navigation label.
# R26 (hub default): the Explore step shows the Questions list's counts (questions, finished), not one study's numbers, so that no
# single study stands for L3.
STAIRCASE_ROUTES = ('screening', 'worked_examples', 'synthesize')
STAIRCASE_WORDS = {'en': ('Screen', 'Estimate', 'Explore'), 'zh': ('筛查', '估计', '探索')}
# Numbers a line prints besides its page's figures: the Estimate step says "within 10%", the rule Estimates prints (content check of R25, M1).
STAIRCASE_LITERALS = ((), ('10',), ())
STAIRCASE_RULE = {'en': 'within 10% of', 'zh': '的 10%'}
REMOVED_HOME_BLOCKS = ('v5-door', 'home-evidence', 'home-feature', 'home-comparison', 'home-result', 'home-tolerance')
REMOVED_HOME_BLOCK_TEXT = {
    'en': ('−47.617', 'SPID', 'DellaVigna', 'Democratic tilt', 'absolute value', 'when larger', 'easiest-to-access'),
    'zh': ('−47.617', 'SPID', 'DellaVigna', '民主党倾向', '发表值绝对值', '标准误更大', '数据最易取得'),
}
# The figures as the pages the steps link to print them: Screening's count line and Explore's lead.
SCREENING_COUNTS = {'en': re.compile(r'([\d,]+) US policy texts · ([\d,]+) pass five screening rules'),
                    'zh': re.compile(r'([\d,]+) 份美国政策文本 · ([\d,]+) 条通过五项筛查规则')}
EXPLORE_COUNTS = {'en': re.compile(r'(\d+) policies \((\d+) adoptions\)'),
                  'zh': re.compile(r'(\d+) 项政策（(\d+) 次采纳）')}
NUMBER = re.compile(r'\d[\d,.]*\d|\d')


def nav_labels(lang):
    """The navigation labels of the three pages the staircase links to."""
    words = json.loads((ROOT / 'scripts/viz_site/i18n' / f'common.{lang}.json').read_text())
    return tuple(words['nav.' + key] for key in ('screening', 'worked_examples', 'synthesize'))


def screening_counts(page, lang):
    """(texts, candidates) as Screening's count line prints them, or None."""
    line = re.search(r'class="sc-countline">([^<]*)<', page)
    found = SCREENING_COUNTS[lang].search(line[1]) if line else None
    return found.groups() if found else None


def explore_counts(page, lang):
    """(policies, adoptions) as the lead of the adoption study on Explore prints them (V5.1: the study is a section of the page, the page
    head is about L3 as a whole), or None. A bare <header> lead is read too, for the shape the page had before."""
    text = unwrapped(page)
    lead = re.search(r'<section id="study"[^>]*>.*?<p class="ex-lead">(.*?)</p>', text, re.S) or re.search(r'<header>(.*?)</header>', text, re.S)
    found = EXPLORE_COUNTS[lang].search(unescape(re.sub(r'<[^>]+>', '', lead[1]))) if lead else None
    return found.groups() if found else None


# Owner, 2026-10-14 (V5.1, EXPLORE_PLAIN_COPY_V51.md): the Explore lead, labels and Methods are in plain words and a Questions list
# shows the 17 questions L3 works on. The jargon the copy file drops must not come back, the log-odds selector is gone, and an
# unfinished question shows no result, only its status word and the data it uses.
# V5.1 release (owner 2026-10-20: L3 is a broad layer): the page is Explore. Its head is one lead sentence and the one exploratory
# label; Findings lists the findings of scripts/viz_site/data/l3_findings.json and one "not seen" line; the Questions list holds 5
# finished, 5 in progress and 7 waiting for data (rows 6 and 11 finished, rows 2 and 17 in progress); the 35-policy adoption study is a
# section after the list. A finding line that is a template (the one on re-estimates) is compared as restructure_explore.finding_line fills it.
STATUS_WORDS = {'en': ('Finished', 'In progress', 'Waiting for data'), 'zh': ('已完成', '进行中', '等待数据')}
QUESTION_COUNTS = (4, 9, 4)       # V5.5: rows 7 and 10 are finished (the new stories answer them); V5.6: row 5 is in progress (16 laws)
FINDINGS_FILE = ROOT / 'scripts/viz_site/data/l3_findings.json'
FINDING_IDS = re.compile(r'\b(?:N1-V2|N11-M7|T64|P5|D4|X6|X8|REPGAP|SELREF|EX-1)\b')
CONFIRMED = re.compile(r'confirm|已确认|确认|证实', re.I)
REMOVED_EXPLORE_TEXT = {
    'en': ('tilt', 'pooled', 'common sign', 'inconclusive', 'time-stamped', 'private research record', 'og-odds', 'pre-specified',
           'alignment', 'of 648', 'exponentiated', 'fixed-effect', 'seat-belt', 'OR 1.4947', 'Standardized'),
    'zh': ('倾向', '合并', '同一符号', '尚无定论', '注明时间', '非公开研究记录', '对数优势', '预设', '对齐', '固定效应', '安全带', '标准化'),
}


# What a search snippet or a link preview shows is read from the head, not the body (content check of R25, M3).
HEAD_TEXT_REMOVED = {'en': ('SPID',), 'zh': ('SPID', '记录时间')}


def head_texts(head):
    """The page title and the descriptions in the head: the texts of <title> and of the description and title <meta> tags."""
    texts = [unescape(match) for match in re.findall(r'<title>(.*?)</title>', head, re.S)]
    for tag in re.findall(r'<meta\b[^>]*>', head):
        attrs = dict(re.findall(r'([\w:-]+)="([^"]*)"', tag))
        if attrs.get('name') in ('description', 'twitter:description', 'twitter:title') or attrs.get('property') in ('og:description', 'og:title'):
            texts.append(unescape(attrs.get('content', '')))
    return texts


def explore_question_groups(section):
    """[(heading, rows)] of the Questions list: each status heading (printed once) with the <li> rows under it."""
    return [(unescape(heading), re.findall(r'<li>.*?</li>', items, re.S))
            for heading, items in re.findall(r'<h3>([^<]*)</h3><ol class="ex-questions">(.*?)</ol>', section, re.S)]


def explore_question_counts(page, lang):
    """(questions, finished) as the Questions list of Explore prints them, or None."""
    section = re.search(r'<section id="questions".*?</section>', page, re.S)
    groups = explore_question_groups(section[0]) if section else []
    finished = next((len(rows) for heading, rows in groups if heading == STATUS_WORDS[lang][0]), 0)
    return (str(sum(len(rows) for _, rows in groups)), str(finished)) if groups else None


def explore_issues(page, lang, fitted, events):
    """Problems with the Explore page: dropped jargon (body and head), the selector, and the Questions list."""
    start = page.index('<main class="explore-v5">')
    body = page[start:]
    shown = ' '.join(PublicText(unwrapped(body)).parts)
    issues = ['dropped Explore wording is back: ' + term for term in REMOVED_EXPLORE_TEXT[lang] if term in shown]
    snippets = ' '.join(head_texts(page[:start]))
    issues.extend('dropped Explore wording is back in the title or description: ' + term
                  for term in REMOVED_EXPLORE_TEXT[lang] + HEAD_TEXT_REMOVED[lang] if term in snippets)
    issues.extend('dropped Explore number is back: ' + number for number in ('602', '549') if re.search(r'(?<!\d)' + number + r'(?!\d)', shown))
    if '<select' in body or 'data-scale' in body:
        issues.append('the log-odds selector is back')
    questions = re.search(r'<section id="questions".*?</section>', body, re.S)
    groups = explore_question_groups(questions[0]) if questions else []
    rows = [row for _, items in groups for row in items]
    if (tuple(heading for heading, _ in groups) != STATUS_WORDS[lang] or tuple(len(items) for _, items in groups) != QUESTION_COUNTS
            or len(rows) != 17 or 'ex-status' in body):
        issues.append('the Questions list must hold 17 questions under three headings, each printed once: '
                      + ', '.join(f'{count} {word}' for count, word in zip(QUESTION_COUNTS, STATUS_WORDS[lang])))
    elif '<a href="#h1">' not in rows[0] or 'id="h1"' not in body:
        issues.append('the first question must link to the adoption study figure (#h1)')
    elif unescape((re.search(r'<span class="ex-data">([^<]*)</span>', rows[0]) or [None, ''])[1]) != (f'{fitted} policies, {events} adoptions' if lang == 'en' else f'{fitted} 项政策，{events} 次采纳'):
        issues.append('the first question must carry the adoption study\'s own numbers')
    if questions and re.search(r'[\[\]%]|odds|\bOR\b|几率|范围', unescape(re.sub(r'<[^>]+>', ' ', questions[0]))):
        issues.append('the Questions list shows a result')
    issues.extend(explore_findings_issues(body, lang))
    return issues


def explore_findings_issues(body, lang):
    """Problems with the Explore page head and the Findings list (V5.1): the page is called Explore, the lead is one sentence followed by the
    one exploratory label (printed once), the Findings list shows the plain findings of the explore view of l3_findings.json word for word, no finding is called confirmed or shown with the id of its result file, the 35-policy study follows the Questions list and
    its reconciling sentence sits under its heading."""
    issues = []
    doc = json.loads(FINDINGS_FILE.read_text(encoding='utf-8'))
    rows = sorted(doc['findings'], key=lambda row: row['order'])
    head = re.search(r'<header><h1>([^<]*)</h1><p class="ex-lead">([^<]*)</p><p class="ex-label">([^<]*)</p></header>', body)
    if not head or unescape(head[1]) != nav_labels(lang)[2]:
        issues.append('the page head must be the heading Explore, one lead sentence and the exploratory label')
    elif unescape(head[3]) != doc['label'][lang]:
        issues.append('the exploratory label of l3_findings.json must follow the lead')
    shown = ' '.join(PublicText(unwrapped(body)).parts)
    if shown.count(doc['label'][lang]) != 1:
        issues.append('the exploratory label must be printed exactly once')
    # V5.5 (T237): the stories come first, one section per group of scripts/viz_site/data/l3_stories.json, word for word, before the technical checks.
    from scripts.viz_site.restructure_explore import load_stories
    groups, stories = load_stories()
    for key in groups:
        story_section = re.search(r'<section id="stories-%s".*?</section>' % key, body, re.S)
        story_items = re.findall(r'<li><h3>([^<]*)</h3><div><p>([^<]*)</p><p class="ex-numbers">([^<]*)</p></div></li>', story_section[0]) if story_section else []
        story_wanted = [(i[lang]['head'], i[lang]['why'], i[lang]['numbers']) for i in stories if i['group'] == key]
        if [tuple(unescape(x) for x in item) for item in story_items] != story_wanted:
            issues.append('the "%s" stories must be those of l3_stories.json in their order, word for word' % groups[key]['en'])
        elif story_section and not re.search(r'<h2[^>]*>%s</h2>' % re.escape(escape(groups[key][lang])), story_section[0]):
            issues.append('the "%s" stories need their heading' % groups[key]['en'])
    if 'id="stories-' + next(iter(groups)) + '"' not in body or body.index('id="stories-' + next(iter(groups)) + '"') > body.index('id="findings"'):
        issues.append('the stories must come before the technical checks')
    section = re.search(r'<section id="findings".*?</section>', body, re.S)
    items = re.findall(r'<li><h3>([^<]*)</h3><div><p>([^<]*)</p><p class="ex-numbers">([^<]*)</p></div></li>', section[0]) if section else []
    from scripts.viz_site.restructure_explore import explore_text
    known = {row['id']: row for row in rows} | {part['id']: part for part in doc['not_seen']['parts']}
    wanted = [(explore_text(item, 'head', lang), explore_text(item, 'why', lang), explore_text(item, 'numbers', lang, known[item['id']].get('computed')))
              for item in doc['explore']['items']]
    if [tuple(unescape(x) for x in item) for item in items] != wanted or not wanted:
        issues.append('the Findings list must show the %d findings of the explore view of l3_findings.json in their order, word for word' % len(wanted))
    if not section or f'<p class="ex-how">{escape(doc["explore"]["how"][lang])}</p></section>' not in section[0] + '</section>':
        issues.append('the Findings list must end with the "how we checked" line of l3_findings.json')
    if section and 'ex-notseen' in section[0]:
        issues.append('the Findings list must not carry a separate "not seen" line (T211)')
    if head and unescape(head[2]) != doc['explore']['lead'][lang]:
        issues.append('the lead must be the lead of the explore view of l3_findings.json')
    jargon = re.compile(r'noise line|reshuffle|random timing|excess|variance|false alarm|p\s*=|噪声线|重排|随机时点|误报|方差', re.I)
    issues.extend('a finding headline carries jargon: ' + unescape(h) for h, _, _ in items if jargon.search(unescape(h)))
    if CONFIRMED.search(shown):
        issues.append('an Explore result is called confirmed')
    issues.extend('a finding id of the result files is on the page: ' + term for term in sorted(set(FINDING_IDS.findall(shown))))
    study = re.search(r'<section id="study"[^>]*><h2[^>]*>([^<]*)</h2><p class="ex-lead">([^<]*)</p><p>([^<]*)</p>', body)
    if not study or unescape(study[3]) != doc['reconcile_35'][lang]:
        issues.append('the adoption study section must open with its heading, its lead and the reconciling sentence of l3_findings.json')
    elif body.index('id="questions"') > body.index('id="study"') or body.index('id="findings"') > body.index('id="questions"'):
        issues.append('the order must be Findings, Questions, then the adoption study')
    return issues


def home_staircase(home):
    """The steps of the Home staircase as (word, href, line): a broken list reads as fewer than three steps."""
    ladder = re.search(r'<ol class="nx-lv nx-stair"[^>]*>(.*?)</ol>', home, re.S)
    steps = []
    for item in re.findall(r'<li class="nx-st"[^>]*>(.*?)</li>', ladder[1] if ladder else '', re.S):
        word = re.search(r'<h3 class="nx-lv-word"><a href="([^"]+)">([^<]*)</a></h3>', item)
        line = re.search(r'<p class="nx-nd[^"]*"[^>]*>(.*?)</p>', item, re.S)
        if word and line:
            # The counted-up copy of a figure is aria-hidden; the screen-reader copy stays.
            text = re.sub(r'<[^>]+>', '', re.sub(r'<span class="nx-tally"[^>]*>.*?</span>', '', line[1]))
            steps.append((word[2], word[1], ' '.join(unescape(text).split())))
    return steps


def home_staircase_issues(home, lang, figures):
    """Problems with the Home staircase. `figures` holds the numbers of each step's line as the pages print them."""
    issues = []
    for block in REMOVED_HOME_BLOCKS:
        if re.search(r'class="[^"]*\b' + block + r'\b', home):
            issues.append('removed Home block is back: ' + block)
    shown = ' '.join(PublicText(home).parts)
    issues.extend('removed Home text is back: ' + text for text in REMOVED_HOME_BLOCK_TEXT[lang] if text in shown)
    steps = home_staircase(home)
    if len(steps) != 3 or home.count('nx-stair"') != 1:
        return issues + ['the staircase needs exactly three steps in one list']
    for (word, href, line), expected, label, route, numbers, literals in zip(steps, STAIRCASE_WORDS[lang], nav_labels(lang), STAIRCASE_ROUTES, figures, STAIRCASE_LITERALS):
        if word != expected or not label.startswith(word):
            issues.append('staircase word "' + word + '" is neither the staircase\'s word that fits the page name nor the navigation label "' + label + '"')
        if href != '/viz/' + lang + '/' + route + '.html':
            issues.append('staircase step ' + word + ' links to ' + href)
        if NUMBER.findall(line) != list(numbers) + list(literals):
            issues.append('staircase line "' + line + '" shows ' + str(NUMBER.findall(line)) + ', the page prints ' + str(list(numbers) + list(literals)))
        if lang == 'en' and len(line.split()) > 10 or lang == 'zh' and len(re.sub(r'[\s\d,.→，]', '', line)) > 20:
            issues.append('staircase line is longer than one short line: ' + line)
    return issues


def flow_issues(page, known):
    """The converging-flow figure on Screening: one figure between the intro and the candidate list that prints no number that
    this page's other text or the Method page does not print (the five gate counts of the old figure were never on a V5 page)."""
    if page.count('class="story-flow"') != 1 or '<script>' not in page[page.find('class="story-flow"'):]:
        return ['Screening needs exactly one converging-flow figure']
    start = page.index('<div class="story-flow"')
    end = page.index('<script>', start)
    issues = []
    if not page.index('class="sc-intro"') < start < page.index('class="sc-search"'):
        issues.append('the flow figure must sit between the intro and the candidate list')
    shown = ' '.join(PublicText(page[start:end]).parts)
    issues.extend('the flow figure prints a number no V5 page prints: ' + number for number in sorted(set(NUMBER.findall(shown)) - set(NUMBER.findall(known))))
    if re.search(r'\d(?:\.\d)?\s?k\b|\d\s?万', shown):
        issues.append('the flow figure abbreviates a count')
    return issues


# Owner, 2026-10-14: the Estimates and Method pages show neither the update stamp nor the AI-agent disclosure sentence.
REMOVED_ESTIMATES_METHOD_LINES = {
    'en': ('Results updated', 'Research design, rules and review', 'AI agents write and run code', 'code is written and run by AI agents'),
    'zh': ('结果更新', '研究设计、规则与审阅', '代码由 AI 智能体独立于原作者代码编写并运行'),
}

# Owner, 2026-10-14 (option C): the recall figures and their version come from the paper's current abstract (GLM 5.3 Flash
# run of September 30, 2026), for DiD and RD alike; Table 1's figures and the September 22 draft date are gone.
SCREENING_RECALL = {
    'en': ('99/113 · 87.6%', '[80.3, 92.5]', '12/17 · 70.6%', '[46.9, 86.7]', 'September 30, 2026'),
    'zh': ('99/113 · 87.6%', '[80.3, 92.5]', '12/17 · 70.6%', '[46.9, 86.7]', '2026 年 9 月 30 日'),
}
OLD_SCREENING_FIGURES = ('86/113', '76.1', '67.5', '83.0', '15/17', '88.2', '65.7', '96.7',
                         'September 22', '2026-09-22', '2026年9月22日', '2026 年 9 月 22 日', '9月22日', '9 月 22 日')

# Owner item 31 and the hub default after it (2026-10-14): precision from the recall run, no coverage clause, no Tables 2-3.
SCREENING_PRECISION = {
    'en': ('Precision in the research sample: DiD 58.7%; RD 42.2%.',
           'Source: revised paper (abstract, Appendix D) and model-comparison table, dated September 30, 2026.'),
    'zh': ('研究样本中的精确率：DiD 58.7%；RD 42.2%。',
           '来源：修订稿（摘要、附录 D）及模型比较表，日期：2026 年 9 月 30 日。'),
}
# The older run's precision before a % sign (not inside a longer number or a signed gap), its coverage fractions and label.
OLD_PRECISION_FIGURES = re.compile(r'(?<![\w.,−+-])(?:62\.1|44\.1)\s*%|(?<![\w.,/])(?:70/155|150/156)(?![\w/])|Classification coverage|分类覆盖率')


def paper_title(lang):
    return json.loads((ROOT / 'scripts/viz_site/i18n' / f'screening.{lang}.json').read_text())['paper_title']


def unwrapped(source):
    """Markup without the no-break spans and break hints, so a sentence can be compared as written."""
    return re.sub(r'<wbr>', '', re.sub(r'<span class="nx-seg">(.*?)</span>', r'\1', source))


def map_component(source):
    """The adoption map exactly as the shared partial renders it (card, caption, data and script)."""
    start = source.index('<style>\n.sm,.sm *')
    return source[start:source.index('</script>', source.index('motion.once(area,run')) + len('</script>')]


def screening_display_issues(source):
    """Check initial HTML and every member through the actual display JS, offline."""
    code = re.search(r'// SCREENING DISPLAY START(.*?)// SCREENING DISPLAY END', source, re.S)
    visible = ' '.join(PublicText(source).parts)
    issues = ['raw Screening text: ' + m.group() for m in RAW_SCREENING.finditer(visible)]
    members = re.search(r'<script type="application/json" id="sc-members">(.*?)</script>', source, re.S)
    strings = re.search(r'const scText=', source)
    if not members or not strings or not code:
        return issues + ['missing Screening display inputs']
    harness = """const vm=require('vm'); let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>{
const p=JSON.parse(input); const c={scText:p.strings}; vm.createContext(c);
vm.runInContext(p.code,c);
const rendered=p.rows.map(r=>c.scDisplayRecord(r));
process.stdout.write(JSON.stringify(rendered));});"""
    run = subprocess.run(['node', '-e', harness], input=json.dumps({'code':code[1], 'rows':json.loads(members[1]), 'strings':json.JSONDecoder().raw_decode(source[strings.end():])[0]}), text=True, capture_output=True)
    if run.returncode:
        return issues + ['Screening display execution failed: ' + run.stderr]
    display = ' '.join(part for html in json.loads(run.stdout) for part in PublicText(html).parts)
    if re.search(r'number from legislative text|provide the specific numeric cutoff|should be inserted here', display, re.I):
        issues.append('Screening card contains RD drafting instructions')
    issues.extend('raw Screening card text: ' + m.group() for m in RAW_SCREENING.finditer(display))
    # Internal terms in source policy quotations have economic meanings; the
    # shared scanner already checks those fields with its narrow exceptions.
    return sorted(set(issues))


def screening_member_issues(source):
    """R28: every candidate carries the integer id GET /api/policies/{id}/detail takes, and no two share one."""
    members = re.search(r'<script type="application/json" id="sc-members">(.*?)</script>', source, re.S)
    if not members:
        return ['missing Screening members']
    ids = [row.get('api_id') for row in json.loads(members[1])]
    issues = []
    if not ids or any(type(value) is not int or value <= 0 for value in ids):
        issues.append('a Screening member has no integer api_id')
    if len(set(ids)) != len(ids):
        issues.append('two Screening members share an api_id')
    return issues


# R28: the only network calls Screening makes, both on an explicit open of a candidate: its detail, and the family list the original panel
# draws in its Policy Family block (edge-cached, as on All records).
SCREENING_FETCHES = ('"/api/policies/" + id + "/detail"', '"/api/policy-groups/" + encodeURIComponent(family) + "?page=1&size=100"')
OTHER_REQUESTS = re.compile(r'XMLHttpRequest|sendBeacon|WebSocket|EventSource|importScripts|new Image\(')


def screening_fetch_issues(source):
    """The page makes exactly the two calls above, not at load: the display block is pure and the calls sit in the panel script."""
    code = '\n'.join(Page(source).scripts)
    issues = []
    if re.findall(r'\bfetch\s*\(((?:[^()]|\([^()]*\))*)\)', code) != list(SCREENING_FETCHES):
        issues.append('Screening must call fetch exactly for the candidate detail and its family list')
    for kind, start, end in (('display', '// SCREENING DISPLAY START', '// SCREENING DISPLAY END'), ('panel', '// SCREENING DETAIL START', '// SCREENING DETAIL END')):
        block = re.search(re.escape(start) + '(.*?)' + re.escape(end), code, re.S)
        if not block:
            issues.append('missing the Screening ' + kind + ' block')
        elif kind == 'display' and re.search(r'\bfetch\b', block[1]):
            issues.append('the Screening display block must not make requests')
    if OTHER_REQUESTS.search(code):
        issues.append('Screening makes a request other than the two detail calls')
    return issues


# R28: a synthetic API record that reaches every block and branch of the panel, with the raw tokens the page must not show; the page's own
# script draws it, so no real policy text is needed to check what the owner sees.
PANEL_FIXTURE = {
    'id': 900001, 'policy_id': 'XX-HB1-Regular Session', 'title': 'Example minimum marriage age law',
    'composite_score': 4.8, 'score_no_data': 4.7, 'is_feasible': 'true', 'primary_design': 'DID', 'best_design': 'RD',
    'policy_topic': 'labor_market', 'policy_family': 'minimum_marriage_age', 'status': 'enacted',
    'intuition_pitch': 'An age cutoff of 18 gives a sharp comparison between people just below and just above it.',
    'policy_significance': '4.0', 'identification': '5.0', 'treatment_intensity': '5.0', 'data_feasibility': '5.0',
    'verdict': 'Pursue: a clean cutoff and an interstate comparison.',
    'identifier': 'HB 1', 'bill_classification': 'bill', 'subject': 'Marriage', 'primary_sponsor': 'Jane Doe', 'session_name': '2018 Regular Session',
    'signing_date': '2018-06-20T00:00:00+00:00', 'latest_passage_date': '2018-05-30', 'latest_action_date': '2018-06-20', 'latest_action_description': 'Signed by the governor',
    'fulltext_source': 'title_only', 'fulltext_chars': 1413, 'document_url': 'https://example.org/bill', 'fulltext_url': 'https://example.org/bill/text',
    'treatment': 'Residents aged 17 and younger may not marry.', 'control_group': 'Residents aged 18 and older.',
    'treatment_timing': 'Effective 2018-06-20T00:00:00Z.', 'outcomes': 'educational_attainment, labor_force_participation', 'datasets': 'ACS, CPS',
    'enforcement_check': 'Marriage licences are checked at issue.', 'did_control_quality': 'within_state', 'identification_rd': '5.0', 'identification_did': '4.0',
    'llm_lit_saturation': 'blue_ocean', 'llm_lit_reasoning': 'Few papers study this law directly. [score-consistency injection: id_did >= 2]',
    'known_studies_parsed': [{'title': 'Early Teen Marriage and Future Poverty', 'citation': 'Dahl (2010)', 'venue': 'JPE'}],
    'unique_contribution': 'A recent sharp change with a clear cutoff.',
    'research_angles': json.dumps([{'question': 'What is the effect on schooling?', 'method': 'RD at age 18', 'outcome': 'educational_attainment',
                                    'benchmark_paper': {'title': 'Early Teen Marriage and Future Poverty', 'citation': 'Dahl (2010)', 'venue': 'JPE'}}]),
    'rd': 1, 'did': 1, 'rd_info': 'Variable: age | Cutoff: 18 | Unit: date_-- | Direction: below_cutoff | Variant: sharp_standard',
    'rd_type': 'fuzzy', 'rd_reasoning': 'RD at age = 18 (below_cutoff)', 'threshold_structure': 'notch', 'manipulation_channels': 'Age misreporting | Cross-border marriage',
    'dynamic_selection_risk': 'medium', 'manipulation_risk': 'high', 'manipulation_reason': 'People may misreport age.', 'measurement_mismatch_risk': 'severe',
    'power_risk': 'insufficient', 'data_access_difficulty': 'restricted_application', 'confounding_policies': 'Other laws set 18 as the age of majority.',
    'confound_severity': 'severe', 'rd_quote': 'Relative to the minimum age for marriage.',
    'did_reasoning': 'Treatment: residents of the state. Control: residents of other states.', 'parallel_trends_risk': 'high', 'staggered_adoption_bias': 'high',
    'anticipation_risk': 'medium', 'spillover_risk': 'high', 'policy_endogeneity_risk': 'medium', 'did_recommended_estimator': "Callaway-Sant'Anna",
    'pre_period_data_available': 'yes', 'did_quote': 'Relative to the minimum age for marriage.',
    'red_flags': 'DISCRETE RUNNING VARIABLE: report sensitivity to bandwidth. [score-consistency injection: id_did >= 2]',
    'siblings': [{'id': 900001, 'chapter_id': 'c1', 'composite_score': 4.8, 'rd': 1, 'did': 1},
                 {'id': 900002, 'chapter_id': 'c2', 'composite_score': 3.1, 'rd': 0, 'did': 1},
                 {'id': 900003, 'chapter_id': 'c3', 'composite_score': 0, 'rd': 0, 'did': 0, 'error': 'boilerplate'}],
    'chapter_manifest': [{'chapter_id': 'c1', 'ord': 1, 'marker': 'Sec. 1', 'char_len': 1200}, {'chapter_id': 'c2', 'ord': 2, 'marker': 'Sec. 2', 'char_len': 800},
                         {'chapter_id': 'c3', 'ord': 3, 'marker': 'Sec. 3', 'char_len': 100}, {'chapter_id': 'c4', 'ord': 4, 'marker': 'Sec. 4', 'char_len': 50}],
}
PANEL_GROUP = [{'title': 'Another state raises its marriage age', 'jurisdiction': 'Idaho State', 'composite_score': 4.1, 'rd': 1, 'did': 1},
               {'title': 'Marriage age for minors', 'jurisdiction': 'Texas State', 'composite_score': 3.2, 'rd': 0, 'did': 1},
               {'title': PANEL_FIXTURE['title'], 'jurisdiction': 'Example State', 'composite_score': 4.8, 'rd': 1, 'did': 1}]
# The blocks as the owner designed them, in drawing order: (English title, Chinese title, background, left rule). "Policy Family" is followed
# by the family's name in the heading.
PANEL_BLOCKS = (('Overview', '概览', '#f0fdf4', '#22c55e'), ('Bill Info', '法案信息', '#f8fafc', '#64748b'),
                ('Text Provenance', '文本来源', '#fff7ed', '#ea580c'), ('What & Who', '政策内容与对象', '#fff', '#9ca3af'),
                ('Literature', '文献', '#eef2ff', '#6366f1'), ('Research Angles', '研究角度', '#faf5ff', '#8b5cf6'),
                ('Policy Family', '政策族', '#ecfdf5', '#10b981'), ('RD Assessment', 'RD 评估', '#f0fdf4', '#22c55e'),
                ('DiD Assessment', 'DiD 评估', '#eff6ff', '#3b82f6'))
PANEL_WORDS = {'en': dict(score='AI design score: 4.8 (feasible)', strip='§ CHAPTERS (3)', combo='RD + DiD:', flags='Red flags:', date='June 20, 2018',
                          note="Literature assessment is based on the AI's training data (cutoff: Jul 2024)", rows=('Fetch:', 'Chars:'), link='/policy_groups.html#grp-'),
               'zh': dict(score='AI 设计评分：4.8（可行）', strip='§ 章节（3）', combo='RD + DiD：', flags='风险提示：', date='2018 年 6 月 20 日',
                          note='文献评估基于 AI 的训练数据', rows=('获取方式：', '字符数：'), link='/zh/policy_groups.html#grp-')}
PANEL_HARNESS = """const vm=require('vm');let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',async()=>{
const p=JSON.parse(input),calls=[];
const reply=(status,body)=>({ok:status<400,status,json:async()=>body});
const c={scText:p.strings,window:{},document:{getElementById:()=>null},fetch:async url=>{calls.push(url);
  if(url.startsWith('/api/policy-groups/'))return reply(200,{rows:p.group,total:p.group.length});
  if(url==='/api/policies/900001/detail')return reply(200,p.record);
  return reply(429,{error:'rate limit exceeded'});}};
vm.createContext(c);vm.runInContext(p.code+'\\nglobalThis.panel=scDetail;',c);
const out={};
out.html=await c.panel.buildDetailHtml(p.record,'dc-900001');
out.groupCalls=calls.filter(u=>u.startsWith('/api/policy-groups/')).length;
// the answer is kept for the session; a refusal is neither kept nor retried by the page
calls.length=0;await c.panel.fetchDetail(900001);await c.panel.fetchDetail(900001);out.keptCalls=calls.length;
calls.length=0;let refused=0;for(let i=0;i<2;i++){try{await c.panel.fetchDetail(900002)}catch(e){refused++}}out.refused=refused;out.refusedCalls=calls.length;
process.stdout.write(JSON.stringify(out));});"""


def screening_panel_issues(source, lang):
    """The opened candidate's panel as the page's own script draws it from PANEL_FIXTURE: the blocks, their colours and order, the labels,
    no raw tokens, one request per explicit fetch (kept when it works, not kept and not retried when it fails)."""
    display = re.search(r'// SCREENING DISPLAY START.*?// SCREENING DISPLAY END', source, re.S)
    detail = re.search(r'// SCREENING DETAIL START.*?// SCREENING DETAIL END', source, re.S)
    strings = re.search(r'const scText=', source)
    if not display or not detail or not strings:
        return ['missing the Screening panel script']
    run = subprocess.run(['node', '-e', PANEL_HARNESS], text=True, capture_output=True, input=json.dumps(dict(
        code=display[0] + '\n' + detail[0], strings=json.JSONDecoder().raw_decode(source[strings.end():])[0], record=PANEL_FIXTURE, group=PANEL_GROUP)))
    if run.returncode:
        return ['Screening panel execution failed: ' + run.stderr[:300]]
    out = json.loads(run.stdout)
    html, words, zh = out['html'], PANEL_WORDS[lang], lang == 'zh'
    shown = ' '.join(PublicText(html).parts)
    issues = ['raw text in the Screening panel: ' + m.group() for m in RAW_SCREENING.finditer(shown)]
    if re.search(r'\bLLM\b', shown):
        issues.append('the Screening panel says LLM, not AI')
    position = 0
    for block in PANEL_BLOCKS:
        title = block[1 if zh else 0]
        found = re.search(r'<div class="detail-section" style="([^"]*)"><h4[^>]*>' + re.escape(title), html)
        if not found or found.start() < position:
            issues.append('Screening panel block missing or out of order: ' + block[0])
            continue
        position = found.start()
        if ('background:' + block[2] + ';') not in found[1] or ('border-left:3px solid ' + block[3]) not in found[1]:
            issues.append('Screening panel block "' + block[0] + '" lost its colours (' + block[2] + ', ' + block[3] + ')')
    for name, needle in (('chapter strip', words['strip']), ('RD + DiD note', words['combo']), ('red flags', words['flags']), ('score label', words['score']),
                         ('literature note', words['note']), ('provenance rows', words['rows'][0]), ('provenance rows', words['rows'][1]),
                         ('readable date', words['date']), ('group link', 'href="' + words['link'])):
        if needle not in html:
            issues.append('Screening panel lacks its ' + name + ': ' + needle)
    strip, column = html.find('class="chapter-strip"'), html.find('class="detail-col"')
    if not (0 <= strip < column and html.count('class="chapter-pill') == 4):
        issues.append('the chapter strip must come first, with a pill for each of the four chapters')
    for needle, color in (('<strong>' + words['combo'], '#fef3c7'), ('<strong>' + words['flags'], '#fef2f2')):
        at = html.find(needle)
        if at >= 0 and color not in html[html.rfind('<div', 0, at):at]:
            issues.append('Screening panel note lost its colour ' + color)
    if re.search(r'>Score: |综合得分', html) or '2018-06-20T' in html:
        issues.append('the Screening panel shows the old score label or a raw date')
    if out['groupCalls'] != 1 or out['keptCalls'] != 1 or out['refused'] != 2 or out['refusedCalls'] != 2:
        issues.append('Screening requests: the family list once per panel, a detail answer kept, a refusal neither kept nor retried')
    return sorted(set(issues))


def method_shown_parts(texts, prefix):
    """The visible text parts of the Method page of one language."""
    return PublicText(texts[prefix + '/documentation.html']).parts


ROW = re.compile(r'<div class="v5-row" data-display-key="(p\d+)" data-comparable="(?:true|false)" data-category="(\w+)"><div class="v5-row-name">(.*?)</div><div class="v5-row-value">(.*?)</div></div>', re.S)


def estimates_row_issues(text, snapshot, lang):
    """Problems with the paper rows of the Estimates list (owner, 2026-10-14, R27).

    Each row, in list order, must show the label of its own paper and the gap computed for that paper: the citation of a paper whose
    numbers match, "Paper N" (N counting 1..k in list order) for the others, a confirmed paper's link to its detail section. A row
    that shows another paper's label, or a label with the wrong gap, is the mismatch this check exists for."""
    from scripts.viz_site import restructure_estimates as estimates
    rows = ROW.findall(text)
    ordered = estimates.plot_rows(snapshot)
    numbers = estimates.paper_numbers(snapshot)
    issues = []
    if len(rows) != len(ordered):
        return ['the list has ' + str(len(rows)) + ' rows, not ' + str(len(ordered))]
    for (key, category, name, value), (_, paper, gap) in zip(rows, ordered):
        shown = unescape(re.sub(r'<[^>]+>', '', re.sub(r'<small class="v5-coder-agrees">.*?</small>', '', name)))
        expected = estimates.label(paper, numbers.get(paper['paper_id']), lang)
        if shown != expected:
            issues.append(key + ': the row shows "' + shown + '", not "' + expected + '"')
        if category != estimates.public_category(paper) or unescape(value) != estimates.fmt_gap(gap):
            issues.append(key + ': the row of "' + shown + '" carries another category or gap than its paper')
        if (paper['stage_group'] == 'confirmed') != ('<a href="#v5-result-' in name):
            issues.append(key + ': only a confirmed paper links to a detail section')
        if estimates.public_category(paper) != 'number_matches' and not re.fullmatch(r'(?:Paper|论文) \d+', shown):
            issues.append(key + ': a paper whose numbers do not match is named: ' + shown)
    return issues


CARD_LABELS = {'en': ('Version', 'Target', 'Outcome / units', 'Sample', 'Estimator', 'Target coverage'),
               'zh': ('版本', '目标', '结果变量 / 单位', '样本', '估计方法', '目标覆盖')}
CARD = re.compile(r'<details class="v5-result" id="(v5-result-\d+)"><summary>(.*?)</summary>(.*?)</details>', re.S)


def confirmed_card_issues(text, snapshot, lang):
    """Problems with the confirmed papers' cards (V5.1): one card per confirmed paper in catalogue order, each printing the title, the
    DOI link, and the version, target, outcome, sample and estimator rows of its own reviewed description
    (scripts/viz_site/data/confirmed_descriptions.json), and a calculated published interval only where the description says so."""
    from scripts.viz_site import restructure_estimates as estimates
    descriptions = estimates.reviewed_descriptions()
    confirmed = [paper for paper in estimates.catalogue_papers(snapshot) if paper['stage_group'] == 'confirmed']
    cards = CARD.findall(text)
    if len(cards) != len(confirmed):
        return ['the page has ' + str(len(cards)) + ' confirmed cards, the snapshot ' + str(len(confirmed)) + ' confirmed papers']
    issues = []
    for (anchor, summary, body), paper in zip(cards, confirmed):
        entry = descriptions.get(paper['paper_id'])
        if entry is None:
            issues.append(anchor + ': a confirmed paper has no reviewed description')
            continue
        if unescape(re.sub(r'<[^>]+>', '', re.sub(r'<span>.*?</span>', '', summary))).strip() != paper['title']:
            issues.append(anchor + ': the card does not carry its paper\'s title')
        if 'href="https://doi.org/' + escape(paper['doi'], quote=True) + '"' not in body:
            issues.append(anchor + ': the card does not link its paper\'s DOI')
        shown = [(unescape(k), unescape(v)) for k, v in re.findall(r'<dt>([^<]*)</dt><dd>([^<]*)</dd>', body)]
        words, text_of = CARD_LABELS[lang], entry[lang]
        expected = [(words[0], estimates.version_display(entry)), (words[1], text_of['target']), (words[2], text_of['outcome']),
                    (words[3], text_of['sample']), (words[4], text_of['estimator'])]
        if shown[:5] != expected or [k for k, _ in shown] != list(words):
            issues.append(anchor + ': the card rows differ from the reviewed description of ' + paper['paper_id'])
        published = re.search(r'<th scope="row">[^<]*</th><td[^>]*>[^<]*</td><td[^>]*>[^<]*</td><td[^>]*>([^<]*)</td>', body)
        note, interval = text_of['published_interval_note'], unescape(published[1]) if published else ''
        if not published or (note and note not in interval) or (not note and ('(' in interval or '（' in interval)):
            issues.append(anchor + ': the published interval carries a note that its description does not, or lacks the one it has')
    return issues


def strip_issues(text, snapshot):
    """The gap strip must hold one lane per plotted estimate: its height grows with the lanes (V5.1: 45 plotted, not the 36 the strip was drawn for)."""
    from scripts.viz_site import restructure_estimates as estimates
    lanes = [int(top) for top in re.findall(r'class="v5-strip-lane"[^>]*style="top:(\d+)px"', text)]
    height = re.search(r'<div class="v5-strip"[^>]*style="height:(\d+)px"', text)
    plotted = sum(gap is not None for _, _, gap in estimates.plot_rows(snapshot))
    issues = []
    if len(lanes) != plotted or not re.search(r'\(' + str(plotted) + r' plotted\)|（图示 ' + str(plotted) + ' 项）', text):
        issues.append('the strip has ' + str(len(lanes)) + ' lanes, the snapshot ' + str(plotted) + ' comparable estimates')
    if not height or not lanes or int(height[1]) < max(lanes) + estimates.LANE_STEP:
        issues.append('the gap strip is shorter than its lanes')
    elif lanes != [estimates.LANE_TOP + n * estimates.LANE_STEP for n in range(len(lanes))]:
        issues.append('the strip lanes are not evenly spaced')
    return issues


def definitions_issues(text, snapshot, lang):
    """The definitions line defines the legend's groups beyond 10% with the names of the legend (Not comparable, Opposite sign), says what each
    holds with the counts of the snapshot, and names nothing that is not in the legend (F6: "Doesn't match" is gone)."""
    from scripts.viz_site import restructure_estimates as estimates
    labels = estimates.category_labels(snapshot, lang)
    line = re.search(r'<p class="v5-small">((?:Same direction|方向一致)[^<]*)</p>', text)
    mark = ': ' if lang == 'en' else '：'
    if not line:
        return ['the definitions line is missing']
    shown = unescape(line[1])
    issues = ['the definitions line does not define the legend\'s group ' + labels[key] for key in ('not_comparable', 'opposite_sign') if (labels[key] + mark) not in shown]
    issues += ['the definitions line names a group that is not in the legend: ' + word for word in ("Doesn’t match", '对不上') if word in shown]
    try:
        expected = estimates.third_group_definitions(snapshot, lang == 'en')
    except ValueError as exc:
        return issues + [str(exc)]
    if shown != expected:
        issues.append('the definitions line does not give the groups beyond 10% as the snapshot counts them: ' + str(estimates.third_group_split(snapshot)))
    return issues


def pin_issues(receipt):
    """A release build must come from the pinned snapshot: the receipt of the build and the pin file name the same capture and digest."""
    from scripts.viz_site import restructure_estimates as estimates
    pin = estimates.read_pin_file()
    if pin is None:
        return []
    return [name + ' of the build (' + str(receipt.get(name)) + ') is not the pinned one (' + pin[name] + ')'
            for name in ('snapshot_path', 'snapshot_sha256') if receipt.get(name) != pin[name]]


# V5.1 release (owner 2026-10-20): the Home page carries one animated figure of the three levels between the adoption map and the staircase
# (scripts/viz_site/motion_figure.py). Its words and numbers are read from the loaders of the pages that print them; it prints no count
# of maps or policies, because the Explore page names the database's own policy count (builder D, Addendum 2).
MOTION_DATA_FILE = ROOT / 'scripts/viz_site/data/motion_data.json'
MIN_MOTION_FINDINGS = 6
MOTION_FINDINGS_UNIT = {'en': 'findings', 'zh': '项发现'}
# The database's own policy count that Explore prints; the wall of maps must not sit beside a second count.
MOTION_FORBIDDEN_COUNT = 648
CJK = re.compile('[㐀-䶿一-鿿豈-﫿　-〿＀-￯]')
# Nothing in the figure may load or send anything.
MOTION_EXTERNAL = re.compile(r'https?://|//[a-z0-9.-]+\.[a-z]{2,}/|@import|url\(|<(?:link|iframe|img|video|audio|embed|object)\b|<script[^>]+src=|'
                             r'\bfetch\s*\(|XMLHttpRequest|WebSocket|sendBeacon|\bimport\(|xmlns', re.I)


def motion_region(home):
    """The figure as the Home page carries it: its style block, the section, and the script that follows (markup string), else None."""
    at = home.find('id="motion-figure"')
    section = home.rfind('<section class="mf-sec"', 0, at) if at >= 0 else -1
    style = home.rfind('<style>', 0, section) if section >= 0 else -1
    ladder = home.find('class="nx-sec nx-ladder"')
    end = home.rfind('</script>', 0, ladder) + len('</script>') if ladder >= 0 else -1
    return home[style:end] if 0 <= style < section < end else None


def motion_figure_issues(home, lang, screening, snapshot):
    """Problems with the Home figure. `screening` is (texts, candidates) as Screening's count line prints them (None when that line is
    gone, which is reported elsewhere). Checked: placement, the findings and the one exploratory label (l3_findings.json), the strip's
    figures (Screening's line, the pinned snapshot's counts, the number of findings), no printed count of maps or policies, the controls and
    the text alternative, nothing external, no Chinese on the English page, and the shipped data equal to data/motion_data.json."""
    from scripts.viz_site import restructure_estimates as estimates
    if 'class="mf-nojs"' in home:          # the fourteenth-round figure (owner 2026-10-21): its own gate
        from scripts.viz_site import motion_v14_check
        return motion_v14_check.issues(home, lang, screening, snapshot, nav_labels(lang))
    if home.count('id="motion-figure"') != 1:
        return ['the Home page needs exactly one animated figure (id="motion-figure"), it has %d' % home.count('id="motion-figure"')]
    region = motion_region(home)
    if region is None:
        return ['the animated figure must sit between the adoption map and the staircase, inside its section, style and script']
    issues = []
    after_map, before_stairs = home.find('id="sm-root"'), home.find('class="nx-sec nx-ladder"')
    if not 0 <= after_map < home.find('id="motion-figure"') < before_stairs:
        issues.append('the animated figure must come after the adoption map (id="sm-root") and before the staircase')
    if 'data-home-block' in region or region.count('class="mf-sec"') != 1:
        issues.append('the animated figure is one section and carries no data-home-block')
    doc = json.loads(FINDINGS_FILE.read_text(encoding='utf-8'))
    rows = sorted(doc['findings'], key=lambda row: row['order'])
    shorts = [row['short'][lang] for row in rows]
    shown = ' '.join(PublicText(region).parts)
    # The findings: the short labels of l3_findings.json in its order, all exploratory, the one label printed once.
    listed = re.search(r'<div class="mf-find" id="mf-findings">(.*?)</div>', region, re.S)
    # A hyphenated word is wrapped in a no-wrap span (gate B3, MF1); the text of each label is the file's.
    items = [unescape(re.sub(r'<[^>]+>', '', item)) for item in re.findall(r'<li>(.*?)</li>', listed[1])] if listed else []
    if len(rows) < MIN_MOTION_FINDINGS or any(row.get('status') != 'exploratory' for row in rows) or items != shorts:
        issues.append('the figure\'s findings list must hold the short labels of the %d exploratory findings of l3_findings.json in their order' % len(rows))
    # Home prints no ranges, so it prints label_home (Explore keeps the shared label); a file without label_home gives Home the shared one.
    home_label = doc.get('label_home', doc['label'])[lang]
    if shown.count(home_label) != 1 or region.count(escape(home_label)) != 1:
        issues.append('the exploratory label (label_home of l3_findings.json) must be printed exactly once in the figure')
    if home_label != doc['label'][lang] and doc['label'][lang] in shown:
        issues.append('the figure must not print the Explore label, which speaks of ranges: Home prints label_home')
    # The strip: three cells; every number in them is a loader value.
    strip = re.search(r'<ol class="mf-strip">(.*?)</ol>', region, re.S)
    cells = {int(level): ' '.join(unescape(re.sub(r'<[^>]+>', ' ', body)).split())
             for level, body in re.findall(r'<li class="mf-cell" data-lv="(\d)">(.*?)</li>', strip[1], re.S)} if strip else {}
    public = estimates.public_counts(snapshot)
    from scripts.viz_site import motion_figure
    if motion_figure.STRIP_NUMBERS:          # the owner may switch the numbers back on: then they are the loaders' and the staircase's words
        wanted = {1: list(screening) if screening else None,
                  2: [str(estimates.attempted(snapshot)), str(public['number_matches']), '10'],      # the staircase's line: papers, within 10% (the rule)
                  3: [str(len(rows))]}
        for level, numbers in wanted.items():
            if numbers is None:
                continue
            if NUMBER.findall(cells.get(level, '')) != numbers:
                issues.append('the figure\'s strip, level %d, must show exactly %s (the loaders\' figures), it shows %s'
                              % (level, numbers, NUMBER.findall(cells.get(level, ''))))
        # Screening and Estimates are said in the staircase's own words, each number next to its own unit (panel round 1, M6 and S2).
        stairs = home_staircase(home)
        squash = lambda text: re.sub(r'\s+', '', text)
        if len(stairs) == 3:
            for level in (1, 2):
                if not squash(cells.get(level, '')).endswith(squash(stairs[level - 1][2])):
                    issues.append('the figure\'s strip, level %d, must say what the staircase says: "%s"' % (level, stairs[level - 1][2]))
        if sorted(cells) != [1, 2, 3] or MOTION_FINDINGS_UNIT[lang] not in cells.get(3, ''):
            issues.append('the figure\'s strip needs three levels, the third counting "%s"' % MOTION_FINDINGS_UNIT[lang])
    else:                                    # art gate, hub decision Q2(a): the strip names the levels; the staircase carries the numbers
        if sorted(cells) != [1, 2, 3]:
            issues.append('the figure\'s strip needs three levels')
        for level in (1, 2, 3):
            if NUMBER.findall(cells.get(level, '')):
                issues.append('the figure\'s strip names the levels only (STRIP_NUMBERS is off); level %d shows a number' % level)
    # The three levels carry the names of the site navigation and the staircase (Screening, Estimates, Explore); the third is not "Patterns" (round two).
    named = [unescape(name) for name in re.findall(r'<li class="mf-cell" data-lv="\d"><p class="mf-lab">([^<]*)</p>', strip[1])] if strip else []
    if named != list(nav_labels(lang)):
        issues.append('the figure\'s strip must name its three levels as the navigation does: ' + ', '.join(nav_labels(lang)))
    # No count of maps or policies anywhere the figure prints (the visible text, the canvas strings, the markup outside scripts).
    motion = json.loads(MOTION_DATA_FILE.read_text(encoding='utf-8'))
    cfg = re.search(r'<script type="application/json" id="mf-cfg">(.*?)</script>', region, re.S)
    try:
        config = json.loads(cfg[1]) if cfg else None
    except ValueError:
        config = None
    if not config:
        issues.append('the figure needs its configuration (id="mf-cfg")')
        config = {'s': {}}
    printed = [shown, region.split('<script')[0]] + [' '.join(value) if isinstance(value, list) else str(value) for key, value in config['s'].items() if key != 'dot']
    for text in printed:
        if re.search(r'(?i)\bpatterns?\b|跨政策规律', text):
            issues.append('the figure still calls the third level Patterns; the site calls it ' + nav_labels(lang)[2])
        for number in (motion['tiles']['n'], motion['tiles']['spid'], MOTION_FORBIDDEN_COUNT):
            if re.search(r'(?<![\d,.])%d(?![\d,])' % number, text):
                issues.append('the figure prints %d, a count of maps or policies' % number)
        if re.search(r'\d+ (?:state )?polic(?:ies|y)\b(?! texts)' if lang == 'en' else r'\d+ 项(?:州)?政策', text):
            issues.append('the figure prints a count of policies')
    # What the canvas draws is what the loaders give.
    if config.get('s'):
        labels = list(estimates.category_labels(snapshot, lang).values())
        if (config.get('counts') != {'papers': estimates.attempted(snapshot), 'match': public['number_matches'], 'dir': public['same_direction'], 'nc': public['not_comparable'], 'os': public['opposite_sign']}
                or config.get('labels') != labels or config.get('findings') != shorts):
            issues.append('the figure\'s canvas data (counts, category labels, findings) differ from the loaders')
    data = re.search(r'<script type="application/json" id="mf-data">(.*?)</script>', region, re.S)
    try:
        shipped = json.loads(data[1]) if data else None
    except ValueError:
        shipped = None
    if shipped != {key: value for key, value in motion.items() if key not in ('inputs', 'asof')}:
        issues.append('the figure\'s map data differ from scripts/viz_site/data/motion_data.json')
    # Act 2 draws the nurse-compact map as the Home card does: its year bands, its legend labels and its colours (art gate, M1).
    card = re.search(r'<script type="application/json" id="sm-d">(.*?)</script>', home, re.S)
    if card and shipped:
        bands = json.loads(card[1])['p']['nurse_licensure_compact']['b']
        legend = re.findall(r'<i>([^<]*)</i>', (re.search(r'<span class="sm-rk" aria-hidden="true">(.*?)</span>', home, re.S) or [None, ''])[1])
        colors = [color.lower() for color in re.findall(r'\.sm \.r\d\{background:(#[0-9a-fA-F]{6})\}', home)]
        nurse = shipped['nurse']
        if nurse['bins'] != bands or nurse['binLabels'] != legend or (colors and nurse['binColors'] != colors):
            issues.append('the figure\'s nurse-compact map must use the Home card\'s own year bands, legend labels and colours')
    # Controls and text alternative.
    buttons = [dict(re.findall(r'([\w-]+)="([^"]*)"', tag)) for tag in re.findall(r'<button\b[^>]*class="mf-btn"[^>]*>', region)]
    toggle = [b for b in buttons if b.get('data-act') == 'toggle']
    replay = [b for b in buttons if b.get('data-act') == 'replay']
    if len(buttons) != 2 or len(toggle) != 1 or len(replay) != 1 or 'aria-pressed' not in toggle[0] or not all(b.get('aria-label') for b in buttons):
        issues.append('the figure needs two labelled buttons: toggle (with aria-pressed) and replay')
    if not re.search(r'\.mf-btn\{[^}]*width:44px;height:44px', region):
        issues.append('the figure\'s buttons must be 44 px')
    canvas = re.search(r'<canvas [^>]*>', region)
    label = unescape((re.search(r'aria-label="([^"]*)"', canvas[0]) or [None, ''])[1]) if canvas else ''
    if not canvas or 'role="img"' not in canvas[0] or 'aria-describedby="mf-findings"' not in canvas[0]:
        issues.append('the figure\'s canvas must be an image with a text alternative and describe itself by the findings list')
    else:
        for number in (screening[0] if screening else None, screening[1] if screening else None, str(estimates.attempted(snapshot)), str(len(rows))):
            if number and number not in label:
                issues.append('the canvas text alternative lacks the number ' + number)
        # Each result group is named with its count and unit (no bare numbers); no screened candidate becomes the shown design.
        names = estimates.category_labels(snapshot, lang)
        for key in ('number_matches', 'same_direction', 'not_comparable', 'opposite_sign'):
            said = ('%s: %d %s' % (names[key], public[key], 'paper' if public[key] == 1 else 'papers')) if lang == 'en' else ('%d 篇%s' % (public[key], names[key]))
            if said not in label:
                issues.append('the canvas text alternative must say "%s"' % said)
        if re.search(r'one candidate becomes|一个候选成为', label):
            issues.append('the canvas text alternative says a candidate becomes the shown design; the design shown is not a screened candidate')
        if any(re.search(r'\b(each|every|all)\b|每项|所有|全部', wording) for wording in config.get('s', {}).get('wallCap', [])):
            issues.append('the wall caption must not claim the whole universe of state policies')
    # Nothing external; English page without Chinese.
    issues.extend('the figure loads or sends something: ' + match.group() for match in sorted(set(MOTION_EXTERNAL.finditer(region)), key=lambda m: m.start())[:3])
    if lang == 'en' and CJK.search(region):
        issues.append('the English figure holds Chinese text')
    return issues


def motion_data_issues():
    """`make_motion_data.py --check`: the committed data file is what its inputs produce (read-only; a count line is the only output)."""
    import contextlib
    import io
    from scripts.viz_site import make_motion_data
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = make_motion_data.main(['--check'])
    except Exception as exc:  # inputs missing or changed shape: not a pass
        return ['make_motion_data.py --check failed: %s: %s' % (type(exc).__name__, str(exc)[:200])]
    return [] if code == 0 else ['make_motion_data.py --check failed: ' + (err.getvalue() or out.getvalue()).strip()[:200]]


# V5.1 release (owner 2026-10-20): the Original text link of a Screening candidate is the document's own address (path, query order and
# percent-encoding as written), minus credential parameters and the fragment. The embedded address of every member must already be what
# safe_url() makes of it, and the page's own display code must print exactly that address; the comparison with the raw database field is
# made on the whole shortlist by the release record (E3), because it needs the 1.5 GB table.
SCREENING_LINK = re.compile(r'<a href="([^"]*)" target="_blank" rel="noopener noreferrer">([^<]*?) ↗</a>')


def original_link_issues(source):
    """Problems with the 'Original text' links of a Screening page: counts only, never an address."""
    from scripts.viz_site.public_page import credential_leaks
    from scripts.viz_site.restructure_screening import safe_url
    code = re.search(r'// SCREENING DISPLAY START(.*?)// SCREENING DISPLAY END', source, re.S)
    members = re.search(r'<script type="application/json" id="sc-members">(.*?)</script>', source, re.S)
    strings = re.search(r'const scText=', source)
    if not members or not strings or not code:
        return ['missing Screening display inputs for the Original-text links']
    rows = json.loads(members[1])
    text = json.JSONDecoder().raw_decode(source[strings.end():])[0]
    harness = """const vm=require('vm'); let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',()=>{
const p=JSON.parse(input); const c={scText:p.strings}; vm.createContext(c);
vm.runInContext(p.code,c);
process.stdout.write(JSON.stringify(p.rows.map(r=>c.scDisplayRecord(r))));});"""
    run = subprocess.run(['node', '-e', harness], input=json.dumps({'code': code[1], 'rows': rows, 'strings': text}), text=True, capture_output=True)
    if run.returncode:
        return ['Original-text link display failed: ' + run.stderr[:200]]
    shown = json.loads(run.stdout)
    unsafe = differ = wrong_count = 0
    for row, html in zip(rows, shown):
        url = row.get('url') or ''
        links = [unescape(href) for href, label in SCREENING_LINK.findall(html) if unescape(label).strip() == text['original']]
        if url != safe_url(url) or credential_leaks(url) or '#' in url or re.search(r'api[_-]?key', url, re.I) or (url and not re.match(r'https?://', url)):
            unsafe += 1
        if len(links) != (1 if url else 0):
            wrong_count += 1
        elif links and links[0] != url:
            differ += 1
    issues = []
    if len(shown) != len(rows) or not rows:
        issues.append('Original-text links: the display code returned %d records for %d members' % (len(shown), len(rows)))
    if unsafe:
        issues.append('%d Original-text addresses carry a credential parameter, a fragment, a userinfo part or a scheme other than http(s)' % unsafe)
    if wrong_count:
        issues.append('%d Screening records do not show exactly one Original-text link where the member has an address (and none where it has none)' % wrong_count)
    if differ:
        issues.append('%d Original-text links differ from the address embedded for the member' % differ)
    return issues


def credential_issues(out):
    """No file of the output holds an API key parameter of 8 or more characters (plain, percent-encoded, JSON- or HTML-escaped).
    Every file counts, not only pages (the build receipt, scripts and data files included). A count per file; the value is never printed."""
    from scripts.viz_site.public_page import credential_leaks
    issues = []
    for file in sorted(path for path in Path(out).rglob('*') if path.is_file()):
        count = credential_leaks(file.read_bytes().decode('utf-8', errors='replace'))
        if count:
            issues.append('%s: %d API key parameter(s) (api_key=...) in the output' % (file.relative_to(out).as_posix(), count))
    return issues


# V5.1 release (builder A, A3): three controls whose focus ring an ancestor with overflow clips draw it inside themselves, in both
# languages. The controls, selectors, colour and rule come from scripts/zh_pages/tests/test_focus_rings.py (which checks the sources);
# this checks the shipped pages. Page key of that suite -> (English file, Chinese file) as shipped.
SHIPPED_FOCUS_PAGES = {'policy_groups': ('/policy_groups.html', '/zh/policy_groups.html'),
                       'documentation': ('/documentation.html', '/zh/documentation.html'),
                       'policy_db': ('/viz/en/all_records.html', '/viz/zh/all_records.html')}


def focus_ring_issues(texts):
    """Problems with the inset focus rings in the shipped Groups, Method and All records pages. `texts` maps a shipped path to its markup."""
    from scripts.zh_pages.tests.test_focus_rings import BLUE, CONTROLS, css_rules
    issues = []
    for page, (name, selectors, _ancestor, heads) in CONTROLS.items():
        rules = {}
        for lang, key in zip(('en', 'zh'), SHIPPED_FOCUS_PAGES[page]):
            if key not in texts:
                issues.append('%s: the page that carries the %s focus ring is missing' % (key, name))
                continue
            rules[lang] = css_rules(texts[key])
            for selector in selectors:
                found = [decl for sel, decl, context in rules[lang] if sel == selector and 'print' not in context]
                offset = re.search(r'outline-offset:(-?\d+)px', found[0]) if len(found) == 1 else None
                if len(found) != 1 or ('outline:2pxsolid' + BLUE) not in found[0] or not offset or int(offset[1]) > -2:
                    issues.append('%s: the %s needs one rule "%s" with a 2px %s outline offset by -2px or more (%d found)' % (key, name, selector, BLUE, len(found)))
                    continue
                # Nothing after the ring may turn the outline off or move it outside the box again.
                at = max(i for i, (sel, _, _) in enumerate(rules[lang]) if sel == selector)
                base = selector.split(':focus-visible')[0]
                for sel, decl, context in rules[lang][at + 1:]:
                    element = sel.split(' ')[-1].split(':')[0]
                    if ':focus' in sel and (sel.startswith(base) or element in heads + ('', '*', '[tabindex]')):
                        if re.search(r'outline(?:-style|-width)?:(?:none|0)', decl) or re.search(r'outline-offset:(?:0|[1-9])', decl):
                            issues.append('%s: a later rule "%s" turns the %s focus ring off or outside' % (key, sel, name))
        if len(rules) == 2:
            for selector in selectors:
                same = [sorted((sel, decl) for sel, decl, _ in rules[lang] if sel == selector) for lang in ('en', 'zh')]
                if same[0] != same[1]:
                    issues.append('%s and %s: the %s focus ring differs between English and Chinese' % (*SHIPPED_FOCUS_PAGES[page], name))
    return issues


# Fix round F3 (keyboard): on All records above 700 px the table header is sticky, so a row, a View link or a control inside an opened row that
# takes focus from below must not land under it. Rows and the focusable things inside them carry a scroll margin of the header's height plus
# 8 px, only while the header is sticky (701 px and wider). The ring rules themselves are checked by focus_ring_issues.
SCROLL_MARGIN_SELECTORS = ('#tableView tr.main-row', '#tableView td a', '#tableView td button', '#tableView td summary')


def scroll_margin_issues(texts):
    """Problems with the scroll margin of the keyboard targets in the shipped All records pages (EN and ZH)."""
    from scripts.zh_pages.tests.test_focus_rings import css_rules
    issues, seen = [], []
    for key in ('/viz/en/all_records.html', '/viz/zh/all_records.html'):
        if key not in texts:
            issues.append('%s: the page that carries the scroll margin is missing' % key)
            continue
        rules = [(sel, decl, re.sub(r'\s+', '', context)) for sel, decl, context in css_rules(texts[key])]
        wide = '@media(min-width:701px)'
        height = [decl for sel, decl, context in rules if sel == '#tableView' and context == wide and '--thead-h:' in decl]
        measured = re.search(r'--thead-h:(\d+)px', height[0]) if len(height) == 1 else None
        if not measured or int(measured[1]) < 40:
            issues.append('%s: #tableView needs --thead-h (the sticky header\'s height, 40 px or more) from 701 px up' % key)
        for selector in SCROLL_MARGIN_SELECTORS:
            found = [decl for sel, decl, context in rules if sel == selector and context == wide and 'scroll-margin-top' in decl]
            if len(found) != 1 or 'scroll-margin-top:calc(var(--thead-h)+8px)' not in found[0]:
                issues.append('%s: "%s" needs scroll-margin-top: calc(var(--thead-h) + 8px) from 701 px up (%d found)' % (key, selector, len(found)))
        # Never while the header is static (700 px and narrower): no margin outside the media query.
        if any(sel in SCROLL_MARGIN_SELECTORS and 'scroll-margin-top' in decl and context != wide for sel, decl, context in rules):
            issues.append('%s: the scroll margin must apply only from 701 px up' % key)
        seen.append(sorted((sel, decl, context) for sel, decl, context in rules if 'scroll-margin-top:calc' in decl or '--thead-h' in decl))
    if len(seen) == 2 and seen[0] != seen[1]:
        issues.append('/viz/en/all_records.html and /viz/zh/all_records.html: the scroll margin differs between English and Chinese')
    return issues


# V5.1 release (F3): a link built from an API field is not rendered when its address carries a credential query parameter. The live API can
# still return one (a govinfo key inside fulltext_url); Screening's panel and All records (its table and its panel) build links from the answer.
# This reads the shipped pages: the guard is defined and tests the listed names, no link is built from a raw field, and the page's own script,
# run on invented records, omits the link (label included), leaves a normal link as it was, and never prints the invented key. Groups builds
# no link from an API field; it has to stay that way or carry the guard. Messages hold counts and case names, never a key.
def detail_link_guard_issues(texts):
    """Problems with the credential guard on links built from API fields in the shipped Screening, All records and Groups pages (EN and ZH)."""
    from scripts.viz_site import legacy_detail as ld
    invented = 'INVENTEDkey0123456789abcdef'
    names = {'api[_-]?key', 'key', 'token', 'access_token', 'auth', 'authorization', 'password', 'secret', 'signature'}
    labels = {'en': {'page': 'Open source page', 'text': 'Open fetched text', 'document': 'Open source document'},
              'zh': {'page': '打开来源页面', 'text': '打开已获取文本', 'document': '打开源文件'}}
    harness = """const vm=require('vm');let input='';process.stdin.on('data',x=>input+=x);process.stdin.on('end',async()=>{
const p=JSON.parse(input),els={};
const el=id=>els[id]||(els[id]={textContent:'',innerHTML:'',disabled:false});
const c={scText:p.strings,window:{},document:{getElementById:el,querySelectorAll:()=>[]},fetch:async()=>{throw new Error('no network')}};
vm.createContext(c);vm.runInContext(p.code,c);
const out={panels:{},rows:{}};
for(const [name,record] of Object.entries(p.records))out.panels[name]=await c.panel.buildDetailHtml(record,'dc-1');
if(c.panel.renderTable)for(const [name,row] of Object.entries(p.rows)){c.panel.renderTable({total:1,rows:[row]});out.rows[name]=els.tbody.innerHTML}
process.stdout.write(JSON.stringify(out));});"""
    shared = {'id': 1, 'policy_id': 'XX-1', 'title': 'Example law', 'composite_score': 4.8, 'fulltext_source': 'fetched', 'fulltext_chars': 120}
    plain_page, plain_text = 'https://example.org/bill?id=3&session=2019', 'https://example.org/bill/text.pdf'
    keyed = 'https://api.example.test/package/htm?api_key=' + invented
    records = {'plain': (plain_page, plain_text), 'key in the text link': (plain_page, keyed), 'key in the second link field': (keyed, plain_text),
               'one address twice, with the key': (keyed, keyed), 'one address twice': (plain_text, plain_text)}
    # Which links must be there: (page, text, document) of the three provenance links.
    expected = {'plain': (1, 1, 0), 'key in the text link': (1, 0, 0), 'key in the second link field': (0, 1, 0),
                'one address twice, with the key': (0, 0, 0), 'one address twice': (0, 0, 1)}
    rows = {'plain': {'id': 1, 'policy_id': 'XX-1', 'title': 'Example law', 'url': 'https://example.org/bill'},
            'key in url': {'id': 2, 'policy_id': 'XX-2', 'title': 'Example law', 'url': keyed},
            'key in document_url': {'id': 3, 'policy_id': 'XX-3', 'title': 'Example law', 'document_url': keyed},
            'key in fulltext_url': {'id': 4, 'policy_id': 'XX-4', 'title': 'Example law', 'fulltext_url': keyed}}
    issues = []
    for kind, key, lang in (('screening', '/viz/en/screening.html', 'en'), ('screening', '/viz/zh/screening.html', 'zh'),
                            ('records', '/viz/en/all_records.html', 'en'), ('records', '/viz/zh/all_records.html', 'zh')):
        page = texts.get(key)
        if page is None:
            issues.append('%s: the page that carries the link guard is missing' % key)
            continue
        wanted = 1 if kind == 'screening' else 2
        lists = re.findall(r'return /\^\(\?:([^/]*)\)\$/i\.test\(name\.trim\(\)\)', page)
        if len(re.findall(r'function hasCredentialParam\(', page)) != wanted or len(lists) != wanted or any(set(item.split('|')) != names for item in lists):
            issues.append('%s: expected %d credential guard function(s) testing api_key, key, token, access_token, auth, authorization, password, secret and signature' % (key, wanted))
        if re.search(r'escape\(\s*(?:r|row)\.(?:url|document_url|fulltext_url)\s*\)', page):
            issues.append('%s: a link is built from an API address without the credential guard' % key)
        for needed in ('hasCredentialParam(r.document_url)', 'hasCredentialParam(r.fulltext_url)') + (('hasCredentialParam(viewUrl)',) if kind == 'records' else ()):
            if needed not in page:
                issues.append('%s: the guard call %s is missing' % (key, needed))
        try:
            if kind == 'screening':
                display = re.search(r'// SCREENING DISPLAY START.*?// SCREENING DISPLAY END', page, re.S)
                detail = re.search(r'// SCREENING DETAIL START.*?// SCREENING DETAIL END', page, re.S)
                strings = re.search(r'const scText=', page)
                if not display or not detail or not strings:
                    issues.append('%s: missing the Screening panel script for the link guard run' % key)
                    continue
                code = display[0] + '\n' + detail[0] + '\nglobalThis.panel=scDetail;'
                strings = json.JSONDecoder().raw_decode(page[strings.end():])[0]
            else:
                parts = [ld._statement(page, '    const topicDisplayMap = {')]
                if lang == 'zh':
                    parts += [ld._statement(page, '    const ZH_VOCAB = {'), ld._function(page, 'zhLabel')]
                parts += [ld._function(page, name) for name in ld.FUNCTIONS + ('statusBadge', 'srcBadgeClass', 'renderTable', 'hasCredentialParam')]
                code = ("const state={ignore_data:false,page:1,size:20},detailCache=new Map(),groupCache=new Map();let lastData=null;function updatePagination(){}"
                        "function toggleDetail(){}\n" + '\n'.join(parts) + '\nglobalThis.panel={buildDetailHtml,renderTable};')
                strings = {}
        except (ld.SourceChanged, ValueError) as error:
            issues.append('%s: the link guard run could not read the page script: %s' % (key, str(error)[:120]))
            continue
        run = subprocess.run(['node', '-e', harness], text=True, capture_output=True, input=json.dumps(dict(
            code=code, strings=strings, rows=rows if kind == 'records' else {},
            records={name: dict(shared, document_url=page_url, fulltext_url=text_url) for name, (page_url, text_url) in records.items()})))
        if run.returncode:
            issues.append('%s: the link guard run failed: %s' % (key, run.stderr[:200]))
            continue
        out = json.loads(run.stdout)
        if invented in run.stdout or re.search(r'api[_-]?key', json.dumps(out['panels']) + json.dumps(out['rows']), re.I):
            issues.append('%s: the credential parameter reaches the HTML of a panel or a table row' % key)
        word = labels[lang]
        for name, html in out['panels'].items():
            links = [(unescape(href), text) for href, text in re.findall(r'<a href="([^"]*)"[^>]*>([^<]*)</a>', html)]
            shown = tuple(sum(1 for _, text in links if text == word[part]) for part in ('page', 'text', 'document'))
            if shown != expected[name]:
                issues.append('%s: with the case "%s" the panel shows (source page, fetched text, source document) links %s, not %s' % (key, name, shown, expected[name]))
            if name == 'plain' and [href for href, text in links if text in (word['page'], word['text'])] != [plain_page, plain_text]:
                issues.append('%s: a link without a credential parameter changed' % key)
        for name, html in out['rows'].items():
            cell = re.search(r'<td class="c-link">(.*?)</td>', html, re.S)
            href = re.search(r'<a href="([^"]*)"', cell[1]) if cell else None
            if not cell or (name == 'plain') != bool(href) or (href and unescape(href[1]) != rows[name]['url']):
                issues.append('%s: the View link of the row "%s" is wrong (it must show only for a link without a credential parameter)' % (key, name))
    for key in ('/policy_groups.html', '/zh/policy_groups.html'):
        page = texts.get(key)
        if page is None:
            issues.append('%s: the Groups page is missing' % key)
            continue
        scripts = '\n'.join(re.findall(r'<script\b[^>]*>(.*?)</script>', page, re.S))
        if re.search(r'''<a[ "']|createElement\(\s*['"]a['"]|\b\w+\.(?:url|document_url|fulltext_url)\b|fulltext_url|document_url''', scripts) and 'hasCredentialParam' not in page:
            issues.append('%s: the Groups page builds a link from API data without the credential guard' % key)
    return issues


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir', type=Path, default=Path('/private/tmp/nes-site-v5'))
    args = ap.parse_args(argv)
    out = args.output_dir.resolve()
    receipt = json.loads((out / 'build_v5.json').read_text())
    from scripts.viz_site import restructure_estimates as estimates
    pin_errors = pin_issues(receipt)
    from scripts.viz_site.restructure_estimates import pin_snapshot, load_snapshot, build_metadata, attempted, progress_line, progress_html, PUBLIC_CATEGORIES, public_counts, category_labels
    pin_snapshot(receipt['snapshot_path'])
    snapshot = load_snapshot()
    assert build_metadata()['snapshot_sha256'] == receipt['snapshot_sha256']
    pages, texts, errors, scripts = {}, {}, list(pin_errors), []
    for file in sorted(out.rglob('*.html')):
        key = '/' + file.relative_to(out).as_posix()
        text = file.read_text()
        scan_file(key, file.read_bytes(), errors)
        terms = banned_terms(text)
        if terms:
            errors.append(key + ': banned public wording: ' + ', '.join(terms))
        if REMOVED_CATEGORY.search(text):
            errors.append(key + ': the removed "not yet replicated" category is still present')
        for name, count in legend_sizes(text):
            if count > 4:
                errors.append(key + ': legend ' + name + ' has ' + str(count) + ' entries')
        if _Robots(text).values != inherited_robots(key):
            errors.append(key + ': indexing differs from the page it replaces')
        parts = PublicText(text).parts
        errors.extend(key + ': "independent" describes the second AI coder: ' + m.group() for m in SECOND_CODER_INDEPENDENT.finditer(' '.join(parts)))
        if standalone(snapshot['total'], parts):
            errors.append(key + ': the catalogue size (' + str(snapshot['total']) + ') is displayed')
        errors.extend(key + ': the older run\'s precision or coverage is shown: ' + m.group() for m in OLD_PRECISION_FIGURES.finditer(' '.join(parts)))
        if file.name == 'screening.html':
            errors.extend(key + ': ' + issue for issue in screening_display_issues(text) + screening_member_issues(text) + screening_fetch_issues(text)
                          + screening_panel_issues(text, 'zh' if '/zh/' in key else 'en'))
        if 'id="public-details-links"' not in text:
            errors.append(key + ': missing shared details deep-link support')
        for name in empty_disclosures(text):
            errors.append(key + ': empty disclosure (a <details> with nothing after its <summary>): ' + name)
        parsed = Page(text)
        if len(parsed.ids) != len(set(parsed.ids)):
            errors.append(key + ': duplicate IDs')
        pages[key], texts[key] = parsed, text
        scripts.extend({'page': key, 'code': code} for code in parsed.scripts)
    for key, page in pages.items():
        for href in page.links:
            target = urlsplit(urljoin('https://preview.invalid' + key, href))
            if target.netloc != 'preview.invalid' or target.path.startswith('/api/'):
                continue
            path = target.path + ('index.html' if target.path.endswith('/') else '')
            if path not in pages:
                errors.append(key + ': missing local link ' + href)
            elif target.fragment and unquote(target.fragment) not in pages[path].ids:
                errors.append(key + ': missing anchor ' + href)
    label_sequences, citation_sequences = [], []
    for lang in ('en', 'zh'):
        key = '/viz/' + lang + '/worked_examples.html'
        text = texts[key]
        # V5.7: the pictogram grid is gone; the legend counts and the dot chart stand for the re-estimated papers only.
        assert 'class="v5-cell"' not in text and 'v5-grid' not in text and attempted(snapshot) < snapshot['total']
        assert len(re.findall(r'class="v5-row" data-display-key=', text)) == attempted(snapshot)
        assert text.count('class="v5-result"') == snapshot['confirmed_independent']
        # Owner, 2026-10-14 (R21): anonymous labels count 1..N down the list, the same in both languages; the detail anchors are
        # v5-result-1, v5-result-2 ...; no number on any page is a position in the catalogue.
        # R27: the papers whose numbers match are named by citation (reviewed surnames and year); only the others are anonymous, so
        # N is the number of papers that are not named, and each row must show the label and the gap of its own paper.
        named = sum(estimates.is_named(paper) for paper in snapshot['papers'] if paper.get('primary'))
        anonymous = [int(n) for n in re.findall(r'<div class="v5-row-name">(?:Paper|论文) (\d+)</div>', text)]
        if anonymous != list(range(1, attempted(snapshot) - named + 1)):
            errors.append(key + ': anonymous paper labels are not 1..N in list order')
        label_sequences.append(anonymous)
        errors.extend(key + ': ' + issue for issue in estimates_row_issues(text, snapshot, lang))
        errors.extend(key + ': ' + issue for issue in confirmed_card_issues(text, snapshot, lang) + strip_issues(text, snapshot) + definitions_issues(text, snapshot, lang))
        cited = [(re.match(r'[^\s()（）]+', shown)[0], re.findall(r'\d{4}', shown)[-1]) for shown in
                 (unescape(re.sub(r'<[^>]+>', '', re.sub(r'<small class="v5-coder-agrees">.*?</small>', '', name))) for _, _, name, _ in ROW.findall(text))
                 if not re.fullmatch(r'(?:Paper|论文) \d+', shown)]
        if len(cited) != named:
            errors.append(key + ': ' + str(len(cited)) + ' papers are named, the reviewed citations name ' + str(named))
        citation_sequences.append(cited)
        keys = re.findall(r'data-display-key="(p\d+)"', text)
        if keys != [f'p{n:02}' for n in range(1, attempted(snapshot) + 1)] or 'data-key=' in text:
            errors.append(key + ': row keys are not the list positions p01..pNN')
        anchors = re.findall(r'id="(v5-result-\d+)"', text)
        if anchors != [f'v5-result-{n}' for n in range(1, snapshot['confirmed_independent'] + 1)]:
            errors.append(key + ': detail anchors are not v5-result-1..M')
        # Owner, 2026-10-14 (R18): no "Method & materials" disclosure. The four policy_exposure stubs link to #method-materials,
        # which is one empty <div> at the end of the article (the local-anchor check above resolves those links to it).
        if text.count('id="method-materials"') != 1 or '<div id="method-materials"></div></article>' not in text or 'v5-method' in text:
            errors.append(key + ': #method-materials must be exactly one empty <div> (no disclosure, no v5-method)')
        # V5.7: a named paper's own link (SSRN, DOI or working-paper page) is the one place its DOI may appear.
        linked_text = text
        for url in estimates.paper_links().values():
            linked_text = linked_text.replace(escape(url, quote=True), '')
        for paper in snapshot['papers']:
            if paper['stage_group'] == 'confirmed':
                assert escape(paper['title']) in text
            else:
                for identity in (paper['paper_id'], paper.get('doi'), paper.get('title')):
                    if identity and (identity in linked_text or escape(identity) in linked_text):
                        errors.append(key + ': unconfirmed identity leaked')
        home = texts[('/zh' if lang == 'zh' else '') + '/policy_db.html']
        assert home.count('data-home-block=') == 2  # the headline block and the staircase (owner, 2026-10-14, V5.1)
        prefix = '/zh' if lang == 'zh' else ''
        shown = ' '.join(PublicText(home).parts)
        for line in REMOVED_HOME_LINES[lang] + (paper_title(lang),):
            if line in shown:
                errors.append(prefix + '/policy_db.html: removed Home text is still shown: ' + line)
        if snapshot['as_of'] in home:
            errors.append(prefix + '/policy_db.html: the update stamp is still on Home')
        if paper_title(lang) not in ' '.join(PublicText(texts['/viz/' + lang + '/screening.html']).parts):
            errors.append('/viz/' + lang + '/screening.html: the paper title is missing')
        # Home: headline and lead, then the shared adoption map, then the staircase.
        if home.count('id="sm-root"') != 1 or not home.index('class="home-thesis') < home.index('id="sm-root"') < home.index('class="nx-sec nx-ladder"'):
            errors.append(prefix + '/policy_db.html: the adoption map must sit once between the headline and the staircase')
        try:
            if map_component(home) != map_component(texts['/viz/' + lang + '/adoption.html']):
                errors.append(prefix + '/policy_db.html: the map differs from the Adoption page component')
        except ValueError:
            errors.append(prefix + '/policy_db.html: the shared map component is missing')
        if 'id="map-sources"' in home or 'nx-map-sources' in home:
            errors.append(prefix + '/policy_db.html: the sources table belongs on the Adoption page only')
        if '/viz/' + lang + '/adoption.html#map-sources' not in home:
            errors.append(prefix + '/policy_db.html: missing link to the map sources')
        screening_shown = ' '.join(PublicText(texts['/viz/' + lang + '/screening.html']).parts)
        method_shown = ' '.join(PublicText(texts[prefix + '/documentation.html']).parts)
        for figure in SCREENING_RECALL[lang]:
            if figure not in re.sub(r'\s+', ' ', screening_shown):
                errors.append('/viz/' + lang + '/screening.html: missing recall figure or version date: ' + figure)
        for sentence in SCREENING_PRECISION[lang]:
            if sentence not in re.sub(r'\s+', ' ', screening_shown):
                errors.append('/viz/' + lang + '/screening.html: missing the precision sentence or the Source line: ' + sentence)
        if SCREENING_RECALL[lang][-1] not in method_shown:
            errors.append(prefix + '/documentation.html: missing the date of the draft the screening figures come from')
        for page_key, page_text in ((prefix + '/policy_db.html', shown), ('/viz/' + lang + '/screening.html', screening_shown),
                                    (prefix + '/documentation.html', method_shown)):
            for old in OLD_SCREENING_FIGURES:
                if old in page_text:
                    errors.append(page_key + ': old screening figure or draft date is still shown: ' + old)
        for page_key in ('/viz/' + lang + '/worked_examples.html', prefix + '/documentation.html'):
            page_text = ' '.join(PublicText(texts[page_key]).parts)
            for line in REMOVED_ESTIMATES_METHOD_LINES[lang]:
                if line in page_text:
                    errors.append(page_key + ': removed text is still shown: ' + line)
        # The staircase shows figures that the pages it links to print: Screening's count line, Estimates' sentence and its 10% rule,
        # and the counts of Explore's Questions list (Explore's lead keeps the adoption study's own numbers, checked below).
        printed = (screening_counts(texts['/viz/' + lang + '/screening.html'], lang), explore_counts(texts['/viz/' + lang + '/synthesize.html'], lang),
                   explore_question_counts(texts['/viz/' + lang + '/synthesize.html'], lang))
        if None in printed:
            errors.append('/viz/' + lang + ': Screening\'s count line, Explore\'s lead or its Questions list changed shape; the staircase check needs review')
        else:
            matches = public_counts(snapshot)['number_matches']
            errors.extend(prefix + '/policy_db.html: ' + issue for issue in home_staircase_issues(
                home, lang, (printed[0], (str(attempted(snapshot)), str(matches)), printed[2])))
            if f' {attempted(snapshot)} ' not in progress_line(lang) or f' {matches} ' not in progress_line(lang):
                errors.append(prefix + '/policy_db.html: the Estimates sentence does not print the staircase figures')
            if STAIRCASE_RULE[lang] not in ' '.join(PublicText(text).parts):
                errors.append(key + ': Estimates no longer prints the 10% rule that the Home Estimate step names')
        # V5.1 release: the animated figure on Home, and the Original text links of Screening (counts only, never an address).
        errors.extend(prefix + '/policy_db.html: ' + issue for issue in motion_figure_issues(home, lang, printed[0], snapshot))
        errors.extend('/viz/' + lang + '/screening.html: ' + issue for issue in original_link_issues(texts['/viz/' + lang + '/screening.html']))
        # Explore: plain lead, no log-odds selector, the Questions list (owner, 2026-10-14, V5.1).
        if printed[1]:
            errors.extend('/viz/' + lang + '/synthesize.html: ' + issue for issue in explore_issues(texts['/viz/' + lang + '/synthesize.html'], lang, *printed[1]))
        # Screening: the converging-flow figure prints only numbers that V5 pages print (this page outside the figure, or Method).
        flow_page = texts['/viz/' + lang + '/screening.html']
        figure_start = flow_page.find('<div class="story-flow"')
        if figure_start >= 0 and '<script>' in flow_page[figure_start:]:
            known = ' '.join(PublicText(flow_page[:figure_start]).parts + PublicText(flow_page[flow_page.index('<script>', figure_start):]).parts + method_shown_parts(texts, prefix))
        else:
            known = ''
        errors.extend('/viz/' + lang + '/screening.html: ' + issue for issue in flow_issues(flow_page, known))
        counts = public_counts(snapshot)
        assert snapshot['total'] == 503 and sum(counts.values()) == attempted(snapshot)
        assert list(counts) == list(PUBLIC_CATEGORIES) and len(PUBLIC_CATEGORIES) == 4
        for category in PUBLIC_CATEGORIES:
            assert escape(category_labels(snapshot, lang)[category]) in text
        # Exactly four categories are displayed: the legend entries (with their counts) and the list groups.
        legend = re.search(r'<div class="v5-legend"[^>]*>(.*?)</div>', text, re.S)[1]
        assert re.findall(r'<span data-category="([^"]+)"', legend) == list(PUBLIC_CATEGORIES)
        assert [int(n) for n in re.findall(r'<b>(\d+)</b>', legend)] == [counts[c] for c in PUBLIC_CATEGORIES]
        assert text.count('id="v5-gap"') == 1 and 'id="v5-catalogue"' not in text
        # The count of papers without a re-estimate is shown on no page that reports results.
        removed = snapshot['total'] - attempted(snapshot)
        prefix = '/zh' if lang == 'zh' else ''
        for page_key in (key, prefix + '/policy_db.html', prefix + '/documentation.html'):
            if standalone(removed, PublicText(texts[page_key]).parts):
                errors.append(page_key + ': the number of papers without a re-estimate (' + str(removed) + ') is displayed')
        # The Estimates sentence keeps its no-break span and, like the Home one, carries no catalogue size (every page is checked above).
        assert progress_html(lang) in text and escape(progress_line(lang, include_date=False)) in unwrapped(text)
        assert str(snapshot['total']) not in progress_line(lang)
        assert 'data-stage=' not in text and 'id="v5-stage"' not in text
        assert '<button class="v5-cell"' not in text
        for paper in snapshot['papers']:
            if paper['stage_group'] != 'confirmed':
                for identity in (paper['paper_id'], paper.get('doi'), paper.get('title')):
                    if identity and (identity in home or escape(identity) in home):
                        errors.append(key + ': homepage unconfirmed identity leaked')
        # SHOW_GITHUB off: no GitHub link at all. On (V5.7, public repository): only the repository and its LICENSE, nothing else.
        own = re.compile(r'https://github\.com/fangyuu6/natural-experiment-scanner(?:/blob/main/LICENSE)?(?=")') if SHOW_GITHUB else re.compile(r'(?!)')
        assert 'github.com/' not in own.sub('', home) and 'github.com/' not in own.sub('', text)
        assert not re.search(r'\bfetch\s*\(', text)
    # V5.1 release: no API key parameter in any file of the output, the inset focus rings, the committed motion data.
    errors.extend(credential_issues(out))
    errors.extend(focus_ring_issues(texts))
    errors.extend(scroll_margin_issues(texts))
    errors.extend(detail_link_guard_issues(texts))
    errors.extend(motion_data_issues())
    if len(label_sequences) != 2 or label_sequences[0] != label_sequences[1]:
        errors.append('Estimates: the anonymous labels differ between English and Chinese')
    if len(citation_sequences) != 2 or citation_sequences[0] != citation_sequences[1]:
        errors.append('Estimates: the named papers (first author and year) differ between English and Chinese')
    js = subprocess.run(['node', '-e', "const vm=require('vm');let s='';process.stdin.on('data',x=>s+=x);process.stdin.on('end',()=>{for(const x of JSON.parse(s)){try{new vm.Script(x.code,{filename:x.page})}catch(e){console.error(e.message);process.exitCode=1}}});"],
                        input=json.dumps(scripts), text=True, capture_output=True)
    if js.returncode:
        errors.append('JavaScript syntax: ' + js.stderr)
    if errors:
        print('\n'.join(sorted(set(errors))), file=sys.stderr)
        return 1
    print(f'PASS: {len(pages)} HTML pages, local links/anchors, unique IDs, {len(scripts)} scripts, release content scan, public wording, legends ≤4, inherited per-page indexing, Screening display values, member ids, the two detail calls and the candidate panel')
    print(f'PASS: both languages, {attempted(snapshot)} anonymous-safe cells in {len(PUBLIC_CATEGORIES)} categories, {attempted(snapshot)} re-estimated rows (each with its own paper\'s label and gap), {named} named by citation, {snapshot["confirmed_independent"]} confirmed cards match their reviewed descriptions, the gap strip holds its {sum(gap is not None for _, _, gap in estimates.plot_rows(snapshot))} lanes, the build comes from the pinned snapshot, catalogue size shown on no page')
    print('PASS: Home staircase (three steps, figures as the linked pages print them, removed blocks absent); Screening flow figure (one figure, only numbers V5 pages print); Explore head (heading, lead, one exploratory label), plain Findings list and its "how we checked" line (the explore view of l3_findings.json), Questions list (17: 2 finished, 8 in progress, 7 waiting for data), the adoption study section, title and description, and dropped jargon')
    print('PASS: Home animated figure (one figure between the map and the staircase, the findings of l3_findings.json and one exploratory label, strip figures from the loaders, no count of maps or policies, two labelled controls, text alternative, nothing external, committed motion data current); Screening Original-text links (embedded address = safe_url of itself, shown exactly, no credential parameter or fragment); no API key parameter in any of the %d files of the output; inset focus rings in the shipped Groups, Method and All records pages, English and Chinese; the All records scroll margin; the credential guard on links built from API fields (Screening, All records, Groups)' % sum(1 for path in out.rglob('*') if path.is_file()))
    print('Browser layout/runtime checks are separate; this check does not measure overflow.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
