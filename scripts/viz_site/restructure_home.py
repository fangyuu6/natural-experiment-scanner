"""The Home staircase: Screen, Estimate, Explore (owner, 2026-10-14: the blocks below the map were weak; V5.1).

Each step is one big word that links to its page, and one line of figures. Every figure comes from the loader of a page
that prints it, so Home and that page cannot differ: Screening prints the texts and candidates, Estimates the papers and
the number matches, Explore the questions it lists and how many are finished (R26, hub default: no single study stands for
L3). The headline, lead and adoption map above the staircase live in partials/home_story.html, which also holds the
staircase script."""
from html import escape
import json
from pathlib import Path

from scripts.viz_site import restructure_estimates as estimates
from scripts.viz_site import restructure_explore as explore
from scripts.viz_site import motion_figure
from scripts.viz_site import restructure_screening as screening

HERE = Path(__file__).resolve().parent
# Dictionary prefix, page route and the figures of each step's line, in order. The two Screen figures are counted up on
# screen; the others appear with their step.
STEPS = (('screen', 'screening', ('texts', 'candidates')),
         ('estimate', 'worked_examples', ('papers', 'matched')),
         ('explore', 'synthesize', ('questions', 'finished')))
COUNTED = ('texts', 'candidates')


def figures():
    """The six figures of the staircase, read from the sources of the pages that print them."""
    snapshot = estimates.load_snapshot()
    printed = screening.load_data()
    asked = explore.load_questions()
    return {'texts': printed['total'], 'candidates': printed['count'],
            'papers': estimates.attempted(snapshot), 'matched': estimates.public_counts(snapshot)['number_matches'],
            'questions': len(asked), 'finished': sum(row['status'] == 'finished' for row in asked)}


def render(lang='en'):
    strings = json.loads((HERE / f'i18n/home_story.{lang}.json').read_text())
    shown = figures()

    def figure(name):
        text = f'{shown[name]:,}'
        if name in COUNTED:
            # Read once by screen readers; the visible copy counts up (see the script in the partial).
            return f'<b><span class="sr-only">{text}</span><span class="nx-tally" aria-hidden="true">{text}</span></b>'
        return f'<b>{text}</b>'

    steps = []
    for number, (key, route, names) in enumerate(STEPS, 1):
        word = escape(strings[f'story.{key}.word'])
        line = strings[f'story.{key}.line_html'].format(**{name: figure(name) for name in names})
        tail = 'nx-nd--end' if number == len(STEPS) else 'nx-nd--in'
        steps.append(f'<li class="nx-st" data-nx-intro="stair-step">'
                     f'<div class="nx-lv-row" data-nx-reveal><span class="nx-lv-no">L{number}</span>'
                     f'<h3 class="nx-lv-word"><a href="/viz/{lang}/{route}.html">{word}</a></h3></div>'
                     f'<p class="nx-nd {tail}" data-nx-reveal>{line}</p></li>')
    # The motion figure sits between the map card and the staircase; motion_figure.MOTION_PLACEMENT switches it.
    return (motion_figure.slot('home', lang, shown) +
            f'<section class="nx-sec nx-ladder" aria-labelledby="hs-levels" data-home-block="2">'
            f'<h2 class="nx-h2" id="hs-levels">{escape(strings["story.levels.h"])}</h2>'
            f'<ol class="nx-lv nx-stair" role="list">{"".join(steps)}</ol></section>')
