#!/usr/bin/env python3
"""Throwaway preview: render partials/pipeline_strip.html with each i18n dictionary.

Writes deploy_output/pipeline_strip_preview_{en,zh}.html (stdlib only).

Placeholder contract shared with the future build script:
    {{t:some.key}}  ->  html.escape(dictionary["some.key"], quote=True)
Unknown keys abort the run; dictionary keys the partial never uses are reported.
"""
import html
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT_DIR = HERE.parents[1] / "deploy_output"
PARTIAL = HERE / "partials" / "pipeline_strip.html"
PLACEHOLDER = re.compile(r"\{\{t:([A-Za-z0-9_.\-]+)\}\}")
# Description length budgets (characters). Step 5 lists the real gates, so its budget is the tight one.
MAX_DESC = {"en": 90, "zh": 46}
MAX_DESC_KEY = {"pipe.step5.desc": {"en": 70, "zh": 36}}

PAGES = {
    "en": {"lang": "en", "title": "Natural Experiment Scanner", "h1": "Natural Experiment Scanner",
           "note": "Preview: pipeline strip (EN)"},
    "zh": {"lang": "zh-CN", "title": "自然实验政策扫描器", "h1": "自然实验政策扫描器",
           "note": "预览：流程条（中文）"},
}

PAGE_TEMPLATE = """<!DOCTYPE html>
<html lang="{lang}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title} - pipeline strip preview</title>
  <style>
    * {{ box-sizing: border-box; }}
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Noto Sans CJK SC", sans-serif; margin: 0; padding: 1rem; background: #ffffff; color: #111827; line-height: 1.55; }}
    .page {{ max-width: 1500px; margin: 0 auto; }}
    h1 {{ font-size: 1.25rem; margin: 0 0 0.3rem 0; font-weight: 700; color: #111827; }}
    .note {{ margin: 0 0 0.9rem 0; font-size: 0.82rem; color: #6b7280; }}
  </style>
</head>
<body>
<div class="page">
  <h1>{h1}</h1>
  <p class="note">{note}</p>
{strip}
</div>
</body>
</html>
"""


def render(template: str, strings: dict) -> str:
    missing = set()

    def sub(match):
        key = match.group(1)
        if key not in strings:
            missing.add(key)
            return match.group(0)
        return html.escape(strings[key], quote=True)

    out = PLACEHOLDER.sub(sub, template)
    if missing:
        raise KeyError("missing i18n keys: " + ", ".join(sorted(missing)))
    return out


def main() -> int:
    template = PARTIAL.read_text(encoding="utf-8")
    used = set(PLACEHOLDER.findall(template))
    problems = 0
    dicts = {code: json.loads((HERE / "i18n" / f"pipeline_strip.{code}.json").read_text(encoding="utf-8"))
             for code in PAGES}
    if set(dicts["en"]) != set(dicts["zh"]):
        print("en/zh key sets differ:", sorted(set(dicts["en"]) ^ set(dicts["zh"])))
        problems += 1
    for code, page in PAGES.items():
        strings = dicts[code]
        unused = set(strings) - used
        if unused:
            print(f"[{code}] unused keys: {sorted(unused)}")
            problems += 1
        for key, value in strings.items():
            limit = MAX_DESC_KEY.get(key, {}).get(code, MAX_DESC[code])
            if key.endswith(".desc") and len(value) > limit:
                print(f"[{code}] {key} is {len(value)} chars (max {limit})")
                problems += 1
        strip = render(template, strings)
        out = OUT_DIR / f"pipeline_strip_preview_{code}.html"
        out.write_text(PAGE_TEMPLATE.format(strip=strip, **page), encoding="utf-8")
        print(f"[{code}] wrote {out}")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
