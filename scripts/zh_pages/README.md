# zh_pages: zh-CN copies of the static pages

Reads `deploy_output/{policy_db,policy_groups,documentation}.html` (never modified) and writes
`deploy_output/zh/<page>.html`. Nothing here uploads or deploys anything.

    python3 scripts/zh_pages/build.py --check      # validate dictionaries, print untranslated counts, write nothing
    python3 scripts/zh_pages/build.py              # write deploy_output/zh/*.html
    python3 scripts/zh_pages/build.py --inventory  # refresh inventory/<page>.txt (+ inventory/manual/<page>.txt)
    # experiments: --pages a,b  --i18n DIR  --out DIR

Workflow
1. Pick strings from `inventory/<page>.txt`; copy the RAW source substring (entities, inline tags) from the EN page.
2. Add `[english, chinese]` pairs to `i18n/<page>.zh.json` (`i18n/common.zh.json` runs first on every page).
   Exact substring replacement, in order, on LF-normalised text (BOM dropped, CRLF to LF).
   0 matches = stale, error. More than 1 match = error unless `{"en":..,"zh":..,"all":true}`. `"optional":true` tolerates absence.
3. Never change `value="..."`, `id=`, `data-*`, `/api/` strings or JS map keys (exit 3 if they differ from EN).
   Use delimiters (`>Enacted</option>`) and keep JS string literals valid (no bare `'` or `"`).
4. Data-driven labels (topic, status, source family) come from the API: inject a display map with a pair.
   Free-text policy content (titles, LLM pitches, group names) stays English.
5. Re-run `--check` after every EN edit; stale pairs fail immediately.

Automatic steps: pairs, `<html lang="zh-CN">`, canonical/og:url to `/zh/`, links (relative page links kept,
relative assets such as `score_distribution.png` made root-absolute, `/api/` untouched), language toggle (the EN pages
carry the `a.lang-switch` pill themselves; build.py regenerates it in zh form: href = root EN URL, lang="zh-CN", the
中 side active; it injects the pill, and its CSS if missing, only when an EN page has none), removal of stale baked-in
Cloudflare snippets. Note: the toggle does not carry the `#hash` over. Exit codes: 0 ok, 1 fatal, 2 dictionary, 3 integrity.
Optional `i18n/keep_english.txt` (one exact string per line) silences known keep-English strings in the counts.

Homepage story block: `deploy_output/policy_db.html` carries the homepage story between `<!-- STORY:START -->` and
`<!-- STORY:END -->` (EN text written by `python3 scripts/viz_site/build.py --inject-home`, which touches nothing else).
This build sets that block aside before the pairs run (so no pair can match inside it) and, after the pairs, swaps in the
ZH rendering of the same partial (`viz_site/build.py` `render_story_block("zh")`); its strings live in
`scripts/viz_site/i18n/home_story.zh.json` and `pipeline_strip.zh.json`, not in `i18n/policy_db.zh.json`. The markers must
exist in the EN page (exit 1 otherwise) and both scripts can be re-run in either order: this one never writes the EN page.
