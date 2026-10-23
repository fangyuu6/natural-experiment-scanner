#!/usr/bin/env python3
"""Build PROPOSAL_V5 into an isolated local preview, never deployment output.

python3 scripts/viz_site/build_v5.py
python3 scripts/viz_site/build_v5.py --check
python3 -m http.server 8765 --bind 127.0.0.1 --directory /private/tmp/nes-site-v5

The existing EN/ZH builders run in one process with one cached replication
snapshot: the one named in scripts/viz_site/data/snapshot_pin.json (path and
sha256), so a later replication wave cannot change the build. --check pins the
snapshot named in the receipt of the preview it checks instead. No replication
producer, network client, or release command runs.
"""
from __future__ import annotations

import argparse
from html import escape
import json
from pathlib import Path
import re
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.viz_site import build as viz
from scripts.viz_site.shared_chrome import include_slots
from scripts.zh_pages import build as zh
from scripts.viz_site.public_page import prepare_public_page, inherit_indexing


def home(lang):
    errors = viz.Errors()
    strings = viz.load_dict('common', lang, errors)
    for name in viz.partials_used('{{partial:head_css}}{{partial:header}}{{partial:home_story}}{{partial:footer}}', errors):
        strings.update(viz.load_dict(name, lang, errors) or {})
    strings['site.title'] = 'Natural Experiment Scanner' if lang == 'en' else '自然实验扫描器'
    body = viz.render('''<!DOCTYPE html>
<html lang="{{html_lang}}"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{{t:site.title}}</title>{{partial:head_css}}</head>
<body><div class="page">{{partial:header}}{{partial:home_story}}{{partial:footer}}</div>
<script>
if (['#explore','#database','#policies'].includes(location.hash)) {
  location.replace('/viz/{{lang_code}}/screening.html');
}
</script></body></html>''', {'lang': lang, 'page': 'policy_db',
                           'strings': strings},
                      errors, 'V5 homepage')
    viz.lint_output(body, lang, 'V5 homepage', errors, 'policy_db')
    if errors.errors:
        raise ValueError('; '.join(errors.errors))
    return body


def legacy_records(text, lang):
    text = re.sub(r'<!-- STORY:START -->.*?<!-- STORY:END -->', '', text, flags=re.S)
    text = include_slots(text, lang, 'all_records', features=viz.chrome_features())
    # Relative detail links originally lived at the root.
    text = re.sub(r'(href=["\'])(policy_groups|documentation)\.html',
                  lambda m: m[1] + ('/zh/' if lang == 'zh' else '/') + m[2] + '.html', text)
    error = json.dumps('Records are temporarily unavailable. Please try again.' if lang == 'en' else '暂时无法显示记录，请稍后重试。', ensure_ascii=False)
    text = text.replace('showError(e.message)', 'showError(' + error + ')')
    text = text.replace('showError(data.error)', 'showError(' + error + ')')
    text = text.replace('escape(e.message)', error)
    text = text.replace('href="#explore"', 'href="#tableView"')
    text = text.replace('<div id="tableView">', '<div id="tableView" tabindex="-1">')
    return text


def emit(path, text, check):
    if path.suffix == ".html":
        text = prepare_public_page(inherit_indexing(text, '/'.join(path.parts[-3:]) if 'viz' in path.parts[-3:] else ('zh/' if path.parent.name == 'zh' else '') + path.name))
    if check:
        if not path.is_file() or path.read_text() != text:
            raise ValueError('missing or stale preview: ' + str(path))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding='utf-8')


def install_preview(staged, destination):
    """Copy a completed preview, retaining every source and previous file.

    The owner prohibits deletion and moves. Backups and staged files remain;
    the receipt is copied last. A write failure restores prior file contents.
    """
    destination.mkdir(parents=True, exist_ok=True)
    previous = Path(tempfile.mkdtemp(prefix=destination.name + '-previous-', dir=destination.parent))
    shutil.copytree(destination, previous, dirs_exist_ok=True)
    files = sorted((p for p in staged.rglob('*') if p.is_file()),
                   key=lambda p: (p.name == 'build_v5.json', str(p)))
    try:
        for source in files:
            target = destination / source.relative_to(staged)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
    except OSError:
        shutil.copytree(previous, destination, dirs_exist_ok=True)
        raise
    print('Copied completed preview; previous files: ' + str(previous))
    print('Retained staged files: ' + str(staged))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--output-dir', type=Path, default=Path('/private/tmp/nes-site-v5'))
    ap.add_argument('--check', action='store_true')
    args = ap.parse_args(argv)
    out = args.output_dir.resolve()
    # This entry point is deliberately incapable of overwriting production or
    # any repository source, even when passed an unintended --output-dir.
    if out == ROOT or ROOT in out.parents or out in ROOT.parents:
        ap.error('V5 local preview must be outside the repository (use /private/tmp/nes-site-v5)')
    requested_out = out
    if args.output_dir.is_symlink():
        ap.error('preview output cannot be a symlink')
    if not args.check:
        if out.exists() and not (out / 'build_v5.json').is_file():
            ap.error('existing output is not a V5 preview')
        out.parent.mkdir(parents=True, exist_ok=True)
        out = Path(tempfile.mkdtemp(prefix=out.name + '-build-', dir=out.parent))
    from scripts.viz_site.restructure_estimates import load_snapshot, progress_line, pin_snapshot, build_metadata
    prior = None
    if args.check:
        prior = json.loads((out / 'build_v5.json').read_text())
        pin_snapshot(prior['snapshot_path'])
    snapshot = load_snapshot()
    binding = build_metadata()
    if prior and prior['snapshot_sha256'] != binding['snapshot_sha256']:
        raise ValueError('immutable preview snapshot digest changed')
    viz.PUBLIC_PAGE_TRANSFORM = lambda text, page, lang: prepare_public_page(inherit_indexing(text, f'viz/{lang}/{page}.html'))
    zh._viz_build = viz
    check_arg = ['--check'] if args.check else []
    if viz.main(['--output-dir', str(out)] + check_arg):
        return 1
    originals = {}
    if zh.main(['--output-dir', str(out)] + check_arg, rendered=originals):
        return 1
    for lang in ('en', 'zh'):
        dest = out / ('zh' if lang == 'zh' else '')
        for name, text in originals[lang].items():
            if name != 'policy_db':
                emit(dest / (name + '.html'), text, args.check)
        legacy = originals[lang]['policy_db']
        emit(out / 'viz' / lang / 'all_records.html', legacy_records(legacy, lang), args.check)
        page = home(lang)
        emit(dest / 'policy_db.html', page, args.check)
        emit(dest / 'index.html', page, args.check)
    metadata = {'local_preview_only': True, 'as_of': snapshot['as_of'],
                'snapshot_path': binding['snapshot_path'], 'snapshot_sha256': binding['snapshot_sha256'],
                'total': snapshot['total'], 'grouped_stage_counts': snapshot['grouped_stage_counts'],
                'progress_en': progress_line('en', include_date=False), 'progress_zh': progress_line('zh', include_date=False)}
    emit(out / 'build_v5.json', json.dumps(metadata, ensure_ascii=False, indent=2) + '\n', args.check)
    if not args.check:
        install_preview(out, requested_out)
    print(('Checked' if args.check else 'Built') + ' V5 local preview: ' + str(requested_out))
    print(progress_line('en'))
    return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except (ValueError, OSError, zh.BuildError) as exc:
        print('V5 BUILD FAILED: ' + str(exc), file=sys.stderr)
        sys.exit(1)
