"""Reader-facing V5 page policy; no changes to research inputs or deploy output."""
from html.parser import HTMLParser
from pathlib import Path
import re
import unicodedata

# Ambiguous words (lease, cap, receipt, track) have valid economic meanings.
# Reject their engineering uses, without censoring policy titles or economic prose.
BANNED = re.compile(
    r'\b(?:freez(?:e|es|ing)|frozen|hash(?:es|ed|-locked)?|sha(?:-?256)?|'
    r'gate(?:s|d)?|cap(?:s|ped|ping)?|track(?:s)?|lease(?:s)?|receipt(?:s)?|primary[ -]+run|stage[ -]+keys?|internal[ -]+IDs?|rung(?:s)?|R0|R1a|R1b|'
    r'snapshot(?:s)?|queue(?:d|s)?|triage|adapter(?:s)?|schema(?:s)?|'
    r'persona(?:s)?|pipeline(?:s)?|DEV(?:\s+v?\d+)?|EX-?\d+|C\d+|'
    r'run[_ -]?\d+|(?:GPT|Claude|Sonnet|Opus|Gemini)[ -]?[\w.]*|'
    r'(?:engineering|worker|job)\s+lease|(?:build|audit)\s+receipt|'
    r'(?:R[012]|replication)\s+track)\b|'
    r'\b[\w.-]+\.(?:jsonl?|csv|tsv|ya?ml|py|md|txt)\b|'
    r'\b(?:attempted_matched|attempted_directional|attempted_not_consistent|'
    r'attempted_not_comparable|finished_uncounted|closed_restricted|waiting_for_access|'
    r'minimum_marriage_age)\b|'
    r'两人确认|兩人確認|数字对上[，,、\s]*待复核|數字對上待複核|口径待核|口徑待核|'
    r'已做完[，,、\s]*未计入|等期刊原表|等待期刊原表|等(?:待)?数据访问|数据受限|'
    r'AI\s*初筛关闭|等待访问|等待复核|受门槛限制|冻结|凍結|哈希|快照|'
    r'流水线|管线|闸门|门槛封顶|封顶评级|主运行|阶段键|内部编号|队列|适配器|模式定义',
    re.I,
)


class PublicText(HTMLParser):
    """Include collapsed sections and accessible labels; exclude code and CSS."""
    def __init__(self, source):
        super().__init__(convert_charrefs=True)
        self.parts = []
        self.skip = 0
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.skip += 1
        if not self.skip:
            attrs = dict(attrs)
            for key in ('aria-label', 'title', 'alt', 'placeholder'):
                if attrs.get(key):
                    self.parts.append(attrs[key])
            if tag == 'meta' and attrs.get('name') == 'description':
                self.parts.append(attrs.get('content', ''))

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.skip = max(0, self.skip - 1)

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def banned_terms(source):
    text = unicodedata.normalize('NFKC', ' '.join(PublicText(source).parts))
    # A petroleum pipeline is a policy subject, not an implementation term.
    text = re.sub(r'\bTrans[- ]Alaska Pipeline\b', 'Trans-Alaska oil transport', text, flags=re.I)
    found = {m.group() for m in BANNED.finditer(text)}
    # These fields are source policy titles and suggested economic variables.
    # Their ordinary economic uses are retained; no UI or prose is exempted.
    import json
    for payload in re.findall(r'<script type="application/json" id="sc-members">(.*?)</script>', source, re.S):
        for row in json.loads(payload):
            values = [row['title'], *row['elements'].values(), *row['scores'].values()]
            for value in values:
                for match in BANNED.finditer(value):
                    word = match.group().lower()
                    if word in {'pipeline', 'pipelines', 'queue', 'gate', 'frozen', 'freeze', 'freezes',
                                'cap', 'caps', 'capped', 'capping', 'track', 'tracks', 'lease', 'leases', 'receipt', 'receipts'}:
                        # These source fields describe physical infrastructure,
                        # utility connections, prices, rents and benefit receipt.
                        continue
                    if re.fullmatch(r'c\d+', word) and re.search(r'aircraft|airplanes|engines|oxygen masks', value, re.I):
                        continue
                    if word == 'dev' and 'Dominion Energy Virginia' in value:
                        continue
                    found.add(match.group())
    # Translation dictionaries can be inserted by JS after initial page load.
    for match in re.finditer(r'const scText=', source):
        payload, _ = json.JSONDecoder().raw_decode(source[match.end():])
        for value in payload.values():
            if isinstance(value, str):
                found.update(m.group() for m in BANNED.finditer(value))
    return sorted(found)


DETAIL_LINKS = '''<script id="public-details-links">
(function(){
  function openTarget(){
    var id;try{id=decodeURIComponent(location.hash.slice(1))}catch(e){return}
    var target=document.getElementById(id);if(!target)return;
    var node=target;
    while(node){if(node.tagName==='DETAILS')node.open=true;node=node.parentElement}
    target.scrollIntoView({block:'start'});
  }
  window.addEventListener('hashchange',openTarget);
  document.addEventListener('click',function(e){
    var link=e.target.closest('a[href]');if(!link)return;
    var url=new URL(link.href,location.href);
    if(url.origin===location.origin&&url.pathname===location.pathname&&url.hash===location.hash)
      openTarget();
  });
  openTarget();
})();
</script>'''


def indexing_source(relative):
    """A new route inherits the page it replaces; existing routes inherit themselves."""
    path = Path(relative.lstrip('/'))
    if path.name == 'index.html':
        path = path.with_name('policy_db.html')
    elif path.name == 'all_records.html':
        path = Path('zh/policy_db.html' if 'zh' in path.parts else 'policy_db.html')
    elif path.name == 'screening.html':
        path = path.with_name('overview.html')
    return path


def inherited_robots(relative, deploy=None):
    deploy = deploy or Path(__file__).resolve().parents[2] / 'deploy_output'
    source = (deploy / indexing_source(relative)).read_text()
    parser = _Robots(source)
    return parser.values


class _Robots(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.values = []
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'meta' and attrs.get('name', '').lower() == 'robots':
            self.values.append(attrs.get('content', ''))


def inherit_indexing(source, relative, deploy=None):
    from html import escape
    values = inherited_robots(relative, deploy)
    source = re.sub(r'<meta\s+name=["\']robots["\'][^>]*>\s*', '', source, flags=re.I)
    tags = ''.join('<meta name="robots" content="' + escape(v, quote=True) + '">\n' for v in values)
    return source.replace('</head>', tags + '</head>', 1)


def reader_chrome(source):
    """Small shared presentation repairs, keeping data and scripts intact."""
    if len(re.findall(r'<h1\b', source)) > 1:
        source = re.sub(r'<h1 class="site-brand">(.*?)</h1>', r'<div class="site-brand">\1</div>', source, flags=re.S)
    if re.search(r'<html[^>]*lang="zh', source):
        source = re.sub(r'(<title>.*?)Natural Experiment Scanner(.*?</title>)', r'\1自然实验扫描器\2', source)
        if '<section id="screening">' in source:  # Method body links only
            source = source.replace('/viz/en/', '/viz/zh/')
    # LLM remains in function/variable names; change only public terminology.
    chunks = re.split(r'(<script[^>]*type="application/json"[^>]*>.*?</script>)', source, flags=re.S)
    source = ''.join(chunk if i % 2 else re.sub(r'(?<![\w])LLM(?![\w])', 'AI', chunk) for i, chunk in enumerate(chunks))
    source = source.replace('>Ignore Data</button>', '>Design quality only</button>')
    source = source.replace('>忽略数据</button>', '>仅按设计质量</button>')
    source = re.sub(r'(<div class="warning-callout">).*?(</div>)',
                    lambda m: m[1] + ('设计建议由 AI 生成。' if re.search(r'<html[^>]*lang="zh', source) else 'Design suggestions are AI-generated.') + m[2], source, flags=re.S)
    return source


# Owner request, 2026-10-14 (R27), redrawn 2026-10-23 (V5.7): a small icon in the site's accent colour (--nx-accent, #1d4ed8) on every page.
# It is an inline data URI, so there is no new file and no release key: three bars rising in steps (a staggered adoption), the same mark as
# the header (partials/head_css.html). Dark browser tabs get the lighter token --nx-b2 (#7ba4f0).
FAVICON = ('<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,%3Csvg xmlns=\'http://www.w3.org/2000/svg\' viewBox=\'0 0 32 32\'%3E'
           '%3Cstyle%3E@media(prefers-color-scheme:dark){path{fill:%237ba4f0;stroke:%237ba4f0}}%3C/style%3E'
           '%3Cpath d=\'M4 28V20h7v8zM12.5 28V12h7v16zM21 28V4h7v24z\' fill=\'%231d4ed8\' stroke=\'%231d4ed8\' stroke-width=\'2\' stroke-linejoin=\'round\'/%3E%3C/svg%3E">')


def with_favicon(source):
    """Exactly one icon link in the head: the page's empty `data:,` icon (it only kept the browser from asking for /favicon.ico)
    is replaced, otherwise the icon goes at the end of the head. Idempotent."""
    head_end = source.find('</head>')
    if head_end < 0 or FAVICON in source[:head_end]:
        return source
    icon = re.compile(r'<link\s+rel=["\']icon["\'][^>]*>')
    if icon.search(source[:head_end]):
        return icon.sub(lambda match: FAVICON, source, count=1)
    return source[:head_end] + FAVICON + '\n' + source[head_end:]


# An API key written as a query parameter (plain, percent-encoded or JSON/HTML escaped), 8 or more characters long.
API_KEY_PARAM = re.compile(r'api[_-]?key(?:=|%3d|&#0*61;|&#x0*3d;|\\u003d)[A-Za-z0-9_\-]{8,}', re.I)


def credential_leaks(source):
    """How many API-key parameters the final HTML carries. A count only: the value is never returned or printed."""
    return len(API_KEY_PARAM.findall(source))


def prepare_public_page(source):
    """Idempotent shared behavior for all local-preview entry points."""
    source = simplify_adoption(source)
    source = reader_chrome(source)
    source = with_favicon(source)
    if 'id="public-details-links"' not in source:
        source = source.replace('</body>', DETAIL_LINKS + '\n</body>', 1)
    for name, count in legend_sizes(source):
        if count > 4:
            raise ValueError(f"Public legend {name} has {count} entries (maximum 4)")
    terms = banned_terms(source)
    if terms:
        raise ValueError('Public wording contains internal terms: ' + ', '.join(terms))
    leaks = credential_leaks(source)
    if leaks:
        raise ValueError(f'Public page contains {leaks} API key parameter(s) (api_key=...); remove them from the source data')
    return source


class _Tree(HTMLParser):
    def __init__(self, source):
        super().__init__()
        self.root = {'tag':'root', 'attrs':{}, 'children':[]}
        self.stack = [self.root]
        self.feed(source)

    def handle_starttag(self, tag, attrs):
        node = {'tag':tag, 'attrs':dict(attrs), 'children':[]}
        self.stack[-1]['children'].append(node)
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.stack.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack)-1, 0, -1):
            if self.stack[i]['tag'] == tag:
                self.stack = self.stack[:i]
                break


def legend_sizes(source):
    """Count every legend, including the map's nested year-color entries."""
    tree = _Tree(source)
    found = []
    def walk(node):
        attrs = node['attrs']
        classes = attrs.get('class', '').split()
        is_legend = any(x.endswith('-legend') or x == 'legend' for x in classes)
        if is_legend:
            children = node['children']
            count = len(children)
            if 'nx-legend' in classes:
                def year_labels(n):
                    if 'sm-rk' in n['attrs'].get('class', '').split():
                        return len(n['children'])
                    return sum(year_labels(c) for c in n['children'])
                count = year_labels(node) + sum(c['attrs'].get('id') == 'sm-lgdot' for c in children)
            found.append((attrs.get('id') or ' '.join(classes), count))
        for child in node['children']:
            walk(child)
    walk(tree.root)
    return found


def simplify_adoption(source):
    """Project the existing map onto three year bands plus the evidence dot.

    Only presentation bands change; all policy years and source records remain.
    """
    import json
    match = re.search(r'(<script type="application/json" id="sm-d">)(.*?)(</script>)', source, re.S)
    if not match:
        return source
    data = json.loads(match[2])
    if all(len(p['b']) == 3 for p in data['p'].values()):
        return source
    for policy in data['p'].values():
        bins = policy['b']
        policy['b'] = [[bins[0][0], bins[1][1]], [bins[2][0], bins[3][1]], bins[4]]
    payload = json.dumps(data, ensure_ascii=False, separators=(',', ':')).replace('</', '<\\/')
    source = source[:match.start(2)] + payload + source[match.end(2):]
    # The static fallback depicts the first selected tab; JS uses the same bins.
    first = re.search(r'class="nx-tab[^"\n]* on[^"\n]*"[^>]*data-f="([^"]+)"', source)
    initial = data['p'][first[1] if first else 'nurse_licensure_compact']
    def path(match):
        tag = match[0]
        year = re.search(r'data-y="(\d+)"', tag)
        if year:
            index = next(i+1 for i, pair in enumerate(initial['b']) if int(year[1]) <= pair[1])
            tag = re.sub(r'class="sm-s s-\d"', f'class="sm-s s-{index}"', tag)
        return tag
    source = re.sub(r'<path class="sm-s s-\d"[^>]*>', path, source)
    source = source.replace('repeat(5,1fr)', 'repeat(3,1fr)')
    source = re.sub(r'<span class="nx-ramp" aria-hidden="true">.*?</span>',
                    '<span class="nx-ramp" aria-hidden="true"><i class="r1"></i><i class="r2"></i><i class="r3"></i></span>', source)
    labels = ''.join(f'<i>{a}–{str(b)[-2:]}</i>' if a != b else f'<i>{a}</i>' for a,b in initial['b'])
    source = re.sub(r'(<span class="sm-rk" aria-hidden="true">).*?(</span>)', lambda m:m[1]+labels+m[2], source)
    source = source.replace('for(var i=0;i<5;i++)if(y<=p.b[i][1])return i+1;return 5',
                            'for(var i=0;i<p.b.length;i++)if(y<=p.b[i][1])return i+1;return p.b.length')
    source = source.replace('</head>', '<style>.sm{--nx-b1:#a8c5f9;--nx-b2:#5283e0;--nx-b3:#1e4798}.sm .r1{background:#a8c5f9}.sm .r2{background:#5283e0}.sm .r3{background:#1e4798}.sm .s-1{fill:#a8c5f9}.sm .s-2{fill:#5283e0}.sm .s-3{fill:#1e4798}</style></head>', 1)
    return source
