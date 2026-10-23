# Natural Experiment Scanner - shared design and homepage story spec

Status: WP1 polish contract, 2026-10-04. [arbiter-r1.md](../../../docs/plans/polish-review/arbiter-r1.md) and section 15 below supersede conflicting historical recipes in sections 0–14. The owner's rules outrank this spec. Audience: page builders (EN and ZH) and the browser reviewer.
Companion source: [design_tokens.css](design_tokens.css). `partials/head_css.html` inlines this file through `{{css:design_tokens}}` and supplies common chrome and base rules. All consumers use these sources; no copied type or chrome system.
Read sections 0.3 (hard constraints), 0.4 (token migration) and "APE study" first, then build.
All px values are CSS px. D / T / M = desktop >= 1100, tablet 601-1099, mobile <= 600. dE = OKLab distance x100. Reference numbers were measured on a harness that loads the real `design_tokens.css` plus the snippets in this file (Chrome, 1440x900, 820x1180, 390x844 via iframe) with placeholder SVGs of the stated size and a 56px + 16px host header; builders should land within +-2px of them (+-16px for anything that depends on the real header height).

---

## 0. Diagnosis of the current homepage (why it looks bad)

Source: `deploy_output/_release/after/T_policy_db_1440.png`, `T_policy_db_390.png`, `T_zh_policy_db_390.png`, `deploy_output/_landing_options/story_flow_en_desktop.png`, `story_map_en_desktop.png`, `home4_en_desktop.png`. Numbers are read off the PNGs (+-5px) or taken from the partials' CSS. The preview still shows the OLD order (flow first, map second); the problems are in the figures themselves and in how they are stacked.

### 0.1 The flow graphic

- F1. It floats. No frame, no plate, no baseline: about 215px of ink in a 1,100px white field, centre of mass at roughly 55% of the width. A figure with no container reads as clip-art pasted onto the page, and it is the only figure without a frame while the map below has one.
- F2. Dead bands. ~65px of empty space above the first label (caveat ends y~200, first label y~268), ~65px below the last ink (y~485) before the caption (y~551): about one third of the 350px zone is nothing. Its two links ("Open the funnel", "Full pipeline diagram") sit 900px to the right of the caption they belong to.
- F3. Scale mismatch. The canvas is 1,100px wide, the text in it is 10-15px: stage sub-labels ~11.5px grey in two lines, "Core" pill ~10px, source counts ~12.5px. Below the 12px floor and tiny against the canvas. No label is a landing point; the only large type is the "7.8k" box.
- F4. Seven treatments in one figure: translucent bands, dashes inside bands, gradient trunk, icon circles, halo on "Core", hatched tail, dark gradient end box. Only one (band width) encodes data. Decoration, not data-ink.
- F5. The loudest object is wrong. The highest-chroma element is the red italic "ceremonial bills skipped" with its dashed leader: an exception, not the story. The second loudest is the navy "7.8k" box. The trunk, which carries the narrowing, is the palest thing on the canvas.
- F6. No counts between the first number (483k) and the last (7.8k), so nothing can be read along the trunk.
- F7. Mobile (390x844): the vertical flow is four full-width bordered 37px boxes (15px bold, count right-aligned) beside a ~14px pale rail with tight elbows and 30px icon circles overlapping it. It looks like a settings list, not a figure; the red dashed pill is again the loudest item. It starts at y~353 and fills the first viewport, so the map never appears. ZH: same, and the caveat breaks as "2016-" / "2026 年".

### 0.2 The map

- M1. Too big and under-composed at once. Frame ~1,100x700. At 1440x900 only its top 264px is visible: the Pacific-northwest corner (3 states). It is the largest object on the page, yet its legend, caption and any selector are below the fold.
- M2. Empty bands inside the card. The Albers composite fills only ~60-70% of its bounding box (Pacific, Gulf/Mexico, Atlantic corners); the rest of the card is blank. Card padding is ~14x16px, so the ragged shape touches the frame in the corners.
- M3. Base tone. Land is `#f1f5f9`: a blue-tinted pale (OKLab L .968, hue 248) on white (only dE 3.2 from the page). It covers ~60% of the map (all uncoloured states plus the 22 hatched ones) and reads as fog; coloured states look like stickers on it.
- M4. Three stroke systems plus a pattern: county mesh `#475569` at 20% / .4px (dark slate, greys the fog further), state mesh `#cbd5e1` .85px, coastline `#94a3b8` 1.1px (the same colour as the slate fill, so the coast looks like the edge of a slate state), then a hatch on 22 states. Four line weights and colours competing.
- M5. Ramp. 10 per-year steps at dE 3.6 each; far too close to match a legend swatch to a state, so neighbouring years are indistinguishable. The ends are both heavy: lightest `#60a5fa` is saturated sky blue (chroma .143), darkest `#1e3a8a` is near-navy (L .38) and turns AL/IL/ID into dark blobs that pull the eye away from the pattern.
- M6. Slate. `#94a3b8` has the same lightness as the lightest ramp step (L .711 vs .714; luminance contrast 1.01:1) and low chroma (.035), so it reads as a greyed-out blue ("disabled"), not a deliberate baseline. 2.56:1 on white, under the 3:1 a fill-encoded category should have.
- M7. Legend. Bottom-left of the frame under Alaska/Hawaii, ~12.5px text, a long label ("in place by 2016, onset unknown"), a 290px 10-segment bar with 4 ticks. ~600px below the headline and below the first viewport. Selector, legend and headline share no grid line.
- M8. Two figure languages. Map: bordered white card, 12px radius. Flow: nothing. Cards: 10px radius. Radii 6/8/10/12 are all in play.

### 0.3 Composition (what makes it ugly on first open)

- C1. Two huge figures of the same weight: ~290px of flow plus ~700px of map back to back, both full column width, both saturated blue, separated by one 23px heading. Nothing says which is the answer.
- C2. First viewport 1440x900: hero ends y~200, 65px gap, flow y 268-485, links y 551, map heading y 608, a 264px sliver of map. About 60% of the screen is the pipeline (engineering content); the figure the user chose as the star arrives as a sliver at the bottom.
- C3. First viewport 390x844: header ~100px, hero to y~330, then 500px of source boxes and rail. No map, no selector.
- C4. Type is small relative to the figures (hero 32.8px, lead 15-17px, labels 10-13px on 1,100px graphics): shapes dominate, labels read as afterthoughts.
- C5. Five attention colours on the first screen: black hero, blue accent word, pale-blue flow, navy box, red annotation, plus saturated map blues. No quiet area.
- C6. Left edges: brand x=16, hero x~163, flow labels right-aligned to x~320 (ragged left), map frame x~163. The flow has no left edge.

### 0.4 What this spec changes (finding -> rule)

| Finding | Fix | Where |
|---|---|---|
| C1, C2, C3 | ONE dominant figure: the map card, first. At 1440x900 the first viewport = hero + 3-number strip + h2 + the map (84-88% visible) with its family list; at 390x844 = hero + strip + h2 + family pills + >= 60% of the map. The flow is 4th, <= 340px, flat and quiet. | 5.2, 7, 0.5 |
| M1, M2 | Map card <= 620px tall at 1440 incl. legend (target 547). 2-column card: map 2/3 (679x425), family list 1/3 (339x497). Map column (map + readout + legend row) and list column are the same height, so no empty band. | 5.2 |
| F1, M8 | One shared frame `.nx-frame`: hairline `#e5e7eb`, surface `#f9fafb`, radius 12, padding 24/16/16. Both figures use it. Cards stay white, radius 8. | 5.1 |
| M3, M4 | Land `#eceef2` (neutral, chroma .005), state borders `#cdd2da` .75px, county mesh one path `#94a3b8` @25% .5px, coast `#aeb6c3` .9px, hatch `#cbd0d9`. | 2.4 |
| M5, M6 | 5 two-year bins, dE ~10 between neighbours, lightest `#a8c5f9`, darkest `#1e4798`; slate `#8793a6` (3.1:1 on white, dE >= 10.7 from every bin). | 2.4 |
| M7, C6 | Legend is one row directly under the map (13px text), left-aligned to the same inner grid line as the card text and the list (frame edge + 24). | 5.2, 4.3 |
| F2-F7 | Flow rebuilt: <= 340px frame, flat fills, no icons/halo/hatch/red annotation, counts at every gate, 14px minimum labels, vertical variant <= 900px (target ~720). Method text moved into one collapsed "Method details" line. | 5.5, 5.9 |
| C4 | Hero 44px serif (EN) / 40px sans (ZH), h2 28, lead 20, figure text >= 13px. | 3 |
| C5 | Colour budget: the map is the only multi-blue object; flow = one pale blue plus one accent box; amber and red never appear on the story. | 2, 8 |

### 0.5 Builder hard constraints (non-negotiable; each is checked in review)

B1. Order and content, nothing else: hero (question, 1 subline, 1 muted caveat line, no buttons) -> 3-number strip -> map section (h2, map card = map + family list + legend, 1 caption) -> example cards (h2, 3 cards in a row, 1 muted line) -> flow section (h2, ONE sentence, flow frame, one collapsed `<details>`) -> one closing link line. No Collaborate block, no GitHub button, no "What this is not", no defensive wording, no eyebrow labels, no extra links, no repeated caveat.
B2. At 1440x900 the first viewport shows: hero, strip, the map h2, the top of the map card, between 66% and 98% of the map height (reference 84% with the harness header; 88% with a 56px header; the ZH fold shows 96% because the ZH hero is one line, accepted as long as the legend row and the card bottom are still below the fold) and at least 5 of the 8 list rows. The card's bottom edge is below the fold. Test: `svg.top`, `svg.height`, row positions in the metrics JSON (section 12).
B3. At 390x844 the first viewport shows hero, strip, h2, the pill row, the one-line family summary and at least 50% of the map (reference 63% with a 112px real header). The pill row is ONE row, scrolls horizontally, scroll-snap start, first pill on the inner grid line, the selected pill scrolled into view on load.
B4. Map card at 1440 <= 620px (target 547). Columns: `minmax(0,2fr) minmax(0,1fr)`, 32px gap (map 679 wide, list 339 wide). Map aspect 1.6 (679x425). List = 8 rows x 62 = 497 plus 1px top rule. T: map max-height 440 centred, pills above, no list. M: map full-bleed (390x234).
B5. Flow frame at >= 1100: outer height <= 340 (SVG 1050x290 units + 24px padding + 2px borders; `svg{display:block}` or a 7px baseline gap appears). At <= 1099: vertical variant, frame <= 800 (T) / <= 900 (M), target ~720. `<details>` row 46px closed.
B6. Both figures use `.nx-frame`. At M the frame goes full-bleed (no side border, no radius, hairline top and bottom only); anything that is not the SVG or a scroll strip gets `.nx-in` (side padding = gutter 16).
B7. Colours only from section 2. No gradients anywhere (the legend ramp is 5 hard segments). No new hex values.
B8. The map is static: no autoplay, no slider, no transition on fills, no animation on hover. A hover, focus or tap does exactly two things: draws a 1.25px `#111827` outline on that state and updates the readout line below the map. No floating tooltip.
B9. Map layers in order: land fill; hatch (thin-coverage states); bin/slate fills; county mesh; state mesh; coast; hover outline. Stroke values in 2.4.
B10. No rendered text below 12px anywhere (SVG text counted after scaling). Legend, caption and meta text 13px. ZH label text 13px.
B11. No new breakpoints. Allowed: 1100, 900 (host-padding mirror inside the tokens only), 720 (cards row only), 600.
B12. No horizontal page scroll at 1440, 820, 390. Only the pill strip and the card strip (<= 719) scroll horizontally.
B13. Hero: EN uses the serif stack from 3.2 via `--nx-display`; ZH uses sans. `text-wrap: balance`, h1 max-width 15em.
B14. ZH: all rules in section 9; caveat segments are `.nx-seg` (nowrap).
B15. Default selected family = Property tax exemption (24 states, first laws across 2016-2026: the ramp is fully used). List ORDER is the user's order (occupational licensing first). Deliberate exception; see section 13, item 7.
B16. The closing line is one `<a>` (the whole sentence) with a no-break space before the arrow.
B17. Flow content: 4 sources (with counts) -> one trunk labelled "483k" -> Classify -> Extract -> Literature check -> Score -> Screen -> end box "7.8k"; a remaining count at every gate tick. Removed from the old version: red "ceremonial bills skipped" note, "Core" pill, halo, gradient trunk, icon circles, hatched/amber tails, dashes inside bands, "Open the funnel" / "Full pipeline diagram" links, "1.6% of all texts". Also removed (review round 1): the "ceremonial bills skipped" / "boilerplate chapters skipped" notes, the "square root" footnote (one plain sentence about compressed widths goes into Method details instead), hover/tap popovers longer than one plain sentence, and any "LLM call", "schema", "structural markers", "boilerplate", "skipped" wording. Count chips in the vertical variant are plain text, never pills. Exceptions kept by explicit user decision (review round 2): the chapter strands under Classify with the "<= 8 chapters" label stay in the figure; the sentence "Ribbon widths scale with the square root of the count." is allowed, but only inside the collapsed Method details, never in the figure frame.
B18. Do not edit values in `design_tokens.css`. If a token is missing, add a literal in page CSS and list it in the report.
B19. Strip numbers are computed from the data files at build time (EN: `483k`, `7.8k`, `8`; from 483,414 / 7,781 / the family count). No hard-coded numbers, no "gap" numbers (nothing about what is missing or not covered).
B20. Family list rows are generated from data: states with a law, first-law year range and first-law counts per year. No typed numbers.
B21. Only one caveat sentence before the results (hero line). The only other caveat-type line on the page is the user-approved "Design labels and scores are model-generated." under the cards (different content, not a repeat). `<details>` text must not restate either.

### 0.6 Token migration (builders saw earlier drafts; `story_map.html` already references some names)

| Old / drafted | Use now | Note |
|---|---|---|
| `--nx-base` (`#f1f5f9`) | `--nx-land` (`#eceef2`) | land fill and hover fill |
| `--nx-hover` | `--nx-land` | |
| `--nx-r0 ... --nx-r9` | `--nx-b1 ... --nx-b5` | classes `s-2017/18` -> b1, `19/20` -> b2, `21/22` -> b3, `23/24` -> b4, `25/26` -> b5 |
| `--nx-tap` | literal `44px` | |
| `--nx-slate` | same name, new value `#8793a6` | |
| `--sm-neutral`, `--sm-cm`, `--sm-bm`, `--sm-co`, `--sm-hatch`, `--sm-surface` | `--nx-land`, `#94a3b8`, `#cdd2da`, `#aeb6c3`, `#cbd0d9`, `--nx-plate` | stroke opacity/width per 2.4 |
| `--nx-rd*`, `--nx-fs-label`, `--nx-ls-label`, `--nx-accent-edge` | removed from tokens | literals in the snippets below (badge, card, flow) |
| `.nx-card*`, `.nx-badge*`, `.nx-score`, `.nx-closing a`, `.nx-legend`, `.nx-readout`, `.nx-sub`, `.nx-dots`, `.nx-dash` | section 5/6 snippets | moved out to keep the tokens file < 6 KB |
| rail layout (232px tab column) from the draft | list column on the RIGHT (1fr), map on the left (2fr) | `.nx-mapcard` grid changed |

Colour reference render on the real SVG (orientation only): scratchpad `h/map_new.png` vs `h/map_old.png`.

---

## APE study (reference site: https://ape.socialcatalystlab.org/)

Method: headless Chrome, screenshots at 1440x900 and 390x844 (full page in tiles), plus the rendered text, read-only. Everything below is an observation about structure and design thinking; no text, branding or imagery is to be reused. Page height: 8,833px at 1440, 10,126px at 390.

### A1. Section order (top to bottom)

1. A dismissible one-line update banner. 2. Header: wordmark in a serif with a small-caps tagline, 5 text links, one highlighted. 3. Hero, two columns: left = serif question headline (one word in italic blue), one dense paragraph, a light-blue "latest research" box, a 4-number strip, an amber warning box; right = "APE examples", a carousel (Briefs / Papers toggle) showing a real one-page research brief as a thumbnail with prev/next arrows, a title and a "Read Brief" link. 4. A dark navy band: "Living benchmark" (an animated chart with play/speed controls). 5. "Where the experiment stands": three numbered columns (01 / 02 / 03) separated by hairlines, then a 6-box process diagram with a feedback loop, then a bar timeline with one large blue count. 6. "LLM-as-a-Judge Tournament": a centred caveat paragraph first, three collapsed grey accordions (how it worked, ranking metrics, review status), a chart carousel with dots, a segmented filter (All / APE / Human) + search, then the leaderboard table (60 rows per page, 1,043 rows). 7. Footer with disclosures (presentations, privacy).

### A2. Hero treatment

Question-as-headline in a transitional serif at ~44px, one italic accent word in blue; a left column of text and numbers against a right column holding one concrete artifact (an example brief). Warm cream background, navy and amber accents, generous but dense: the whole hero (headline, paragraph, CTA, numbers, warning, example) fits in one 1440x900 viewport. The page uses colour bands (cream, navy, white) to separate sections.

### A3. Headline-numbers strip

Four numbers in a row directly under the paragraph: large serif numerals (~28px), tiny uppercase labels below, 1px vertical hairlines between cells, no boxes, no icons. Numbers are plain counts (ideas, papers, matches) plus one rate. It is the first thing that gives the project scale, and it takes about 60px of height.

### A4. Where the caveat sits

Before any result, in the hero, immediately under the number strip, as an amber-tinted box with an icon: it says the system is experimental, expect errors, none of it is peer reviewed. It is repeated in plain prose before the leaderboard and again in an "important caveats" accordion. Credibility comes from saying the limit before the first number can be misread, in the plainest verbs.

### A5. How examples are shown

As real artifacts, not descriptions: a thumbnail of an actual one-page brief (title, chart, text), with a headline title and a "Read Brief" link; a carousel of four with arrows; a Briefs/Papers toggle. Mobile: the same thumbnail at full width.

### A6. Leaderboard and how it is scannable

One row per paper: rank (muted, right-aligned), title (the only bold text) with a small source chip underneath, then 5 numeric columns in monospace tabular figures (right-aligned), then a single status glyph column. Hairline row separators, no zebra, generous 56px rows, sortable headers with tiny arrows, a segmented filter and a search box above, pagination below ("1-60 of 1043"), a "+" expander per row. Mobile: the table drops to rank, title, one metric and status ("swipe to see more columns"). The structure is: identity first, numbers aligned in columns, status last.

### A7. How methodology is explained

Narrative first (three numbered phases in columns, plain paragraphs), a boxed 6-step diagram with a feedback loop, then detail behind full-width grey accordions with serif titles and a chevron. Definitions of columns are accordions too. Nothing technical is shown unless the reader opens it.

### A8. Density and tone

Dense but calm: short paragraphs, first person plural, plain verbs, candid about failure ("expect bugs"), dates on data ("data as of ..."), units on every number. One serif for headings and numerals, a humanist sans for body, two accent colours (blue, amber/gold), uppercase tracked eyebrows over section titles.

### A9. What makes it feel credible and simple

(1) Real artifacts instead of claims; (2) the limitation stated before the first number; (3) numbers with units and dates; (4) a single scannable table instead of many charts at the point of decision; (5) methodology available but folded; (6) a consistent small vocabulary (ideas, papers, matches); (7) public everything (links to code, data, failures); (8) one section per question, in the order a sceptical reader asks them (what is it, does it work, how was it checked).

### A10. What transfers to us, what does not

| APE idea | Decision | Reason |
|---|---|---|
| Number strip with hairline separators under the hero | ADOPT (3 numbers, sans numerals) | Gives scale in 50px; our numbers are volumes, not performance |
| Caveat before any result, in the hero | ADOPT (already decided: one muted line) | Same credibility effect without an amber box |
| Show the real artifact | ADAPT: the 3 example cards are our "briefs" | We have no PDFs; cards show design, title, place/year, score |
| Leaderboard-style list: identity, aligned numerals, hairline rows, status | ADOPT as the family selector (name, states, years, dot timeline) | Turns the selector into a scannable table that also explains the map |
| Collapsed accordion for method | ADOPT as ONE `<details>` line under the flow | Keeps the page minimal, hides the long text |
| Narrative sentence before the diagram | ADOPT (one sentence under the flow h2) | Orients the reader before the graphic |
| Two-column hero (text left, artifact right) | ADAPT: map left, list right in the first figure | The map is our artifact; hero text stays single column (680px) |
| Horizontal carousel with arrows | ADAPT: 3 cards in a row; scroll-snap row on mobile; no arrows, no autoplay | Static, calm |
| Cream background, dark navy band, serif body | REJECT | Our site is light, neutral, minimal; serif only in the EN hero |
| Italic accent word in the headline | REJECT (accent word is upright, blue) | Italics banned (and fake in ZH) |
| Amber warning box with icon | REJECT | A muted caveat line is what the user decided |
| Uppercase tracked eyebrows | REJECT | Decided: no eyebrows |
| Animated benchmark chart, play controls | REJECT | The map is static by decision |
| 1,000-row leaderboard, filters, search, pagination, sort | REJECT | 8 rows; selection, not browsing |
| Long page with 5+ sections and several accordions | REJECT | Budget: 5 blocks, one accordion |
| Repeating the caveat three times | REJECT | One early line; nothing repeated |
| Four-number strip including a "win rate" | REJECT | Our strip holds only volume numbers computed from data; no evaluative number, no gap number |
| Banner, invited-talks list, privacy block | REJECT | Out of scope for the story |

---

## 1. Design principles

Refined means restrained: one neutral ramp, one blue, a map ramp used only on the map, and nothing else asking for attention. The page is editorial, calm and data-first: a quiet question, three numbers, then the evidence (the map and its family list) at a size that can be read, three concrete examples, and the pipeline as supporting detail with its explanation folded away. Concretely: (1) one dominant figure per screen; (2) six type sizes on desktop (44/28/20/16/13/12, plus 15 for UI) with weight 600 rather than size or colour used for emphasis; (3) every gap on an 8px grid with a 4px half step, and the same gap for the same relationship everywhere (heading to content 24, caption below figure 12, section to section 96); (4) generous whitespace between sections, tight inside figures, so figures never contain empty bands; (5) one left edge for all text and frames, one inner grid line (frame edge + 24) for everything inside frames; (6) hairlines instead of shadows, one radius per component class, flat fills, no gradients, no icons that carry no data; (7) numbers tabular, labels never below 12px, every colour on the map has a legend entry; (8) tables and lists are scanned, not read: identity left, numerals aligned right, hairline rows.

---

## 2. Palette

### 2.1 Neutrals and text (contrast on white unless stated)

| Token | Hex | Use | Contrast |
|---|---|---|---|
| `--nx-ink` | `#111827` | h1, h2, card titles, key numbers | 17.74 |
| `--nx-text` | `#374151` | body, pill text | 10.31 |
| `--nx-text-2` | `#4b5563` | lead, captions, legend labels, sentence under h2 | 7.56 (7.23 on plate) |
| `--nx-muted` | `#6b7280` | hero caveat, card meta, list meta, tick labels, readout. Only at >= 12px, only on white or plate | 4.83 (4.63 on plate) |
| `--nx-line` | `#e5e7eb` | frame/card/list/details hairlines, seam | decorative |
| `--nx-line-2` | `#d1d5db` | pill border (T/M) | 1.47; the label carries the identification (10.3:1), a conscious match with the old pages' pills |
| `--nx-plate` | `#f9fafb` | figure frame surface | 1.05 |
| `--nx-land` | `#eceef2` | map land, hover fill | 1.16 |
| `--nx-dark` | `#1f2937` | selected pill fill (T/M) | white on it 14.68 |

Rule: never lighter than `--nx-muted` for text; never `#9ca3af` for text (2.54:1).

### 2.2 Blue accent

| Token | Hex | Use | Contrast |
|---|---|---|---|
| `--nx-accent` | `#1d4ed8` | links, hero accent word, DiD badge text, selected-row bar, flow end box, focus ring | 6.70 on white; 6.41 on plate; white on it 6.70 |
| accent hover (literal) | `#1e40af` | link hover | 8.72 |
| `--nx-accent-soft` | `#eff6ff` | selected list row fill, DiD badge fill | accent on it 6.16 |
| flow blue (literal) | `#bfdbfe` | flow bands and trunk, DiD badge border, card-link hover border | fill only |

### 2.3 Other fixed colours

- RD badge (shared with the table page): text `#166534`, fill `#ecfdf5`, border `#bbf7d0`; 6.77:1. The only green on the page.
- Amber `#f59e0b` / `#b45309` and red `#b91c1c`: reserved for the explorer pages. NOT used on the homepage story.
- Score numerals: `--nx-ink`, not green.

### 2.4 Map colours

Five two-year bins. Neighbour dE is 10.1-10.8 (the old 10-step ramp was 3.6).

| Bin | Years | Hex | OKLab L | On white |
|---|---|---|---|---|
| `--nx-b1` | 2017-18 | `#a8c5f9` | .82 | 1.75 |
| `--nx-b2` | 2019-20 | `#7ba4f0` | .72 | 2.50 |
| `--nx-b3` | 2021-22 | `#5283e0` | .62 | 3.70 |
| `--nx-b4` | 2023-24 | `#3263c3` | .52 | 5.66 |
| `--nx-b5` | 2025-26 | `#1e4798` | .42 | 8.71 |

| Element | Value | Why |
|---|---|---|
| Slate "in place by 2016" `--nx-slate` | `#8793a6` | 3.11:1 on white (2.98 on plate); dE to b1..b5 = 16.8 / 10.7 / 12.6 / 19.0 / 26.5; chroma .032 so it reads as grey, not pale blue |
| Land `--nx-land` | `#eceef2` | neutral (chroma .005); dE 3.6 from the plate, 14.8 from b1 |
| Frame surface `--nx-plate` | `#f9fafb` | same as the old `card-panel`; hairline `#e5e7eb` |
| Hatch "not enough data" | `#cbd0d9`, 0.5px lines, 7px pitch, 45 deg, over land | dE 9.3 from land: visible as a class, quiet |
| County mesh (one path) | stroke `#94a3b8`, opacity .25, width .5px, non-scaling | dE 5.9 on land (visible, faint), 3.0-3.5 on b1-b3, 7.6 on b5; does not grey the fills |
| State mesh | stroke `#cdd2da`, .75px | dE 8.7 from land; on blue a light separator |
| Coast | stroke `#aeb6c3`, .9px | dE 17.5 from land; hierarchy coast > state > county |
| Hover outline | stroke `#111827`, 1.25px, non-scaling, on top | |
| Keyboard focus outline | stroke `#1d4ed8`, 2px | |

Limits (accepted): in greyscale print slate sits between b2 and b3 in lightness (every swatch is labelled). The explorer pages still use slate `#94a3b8` and a dark-means-longer-ago ramp (section 13, items 3 and 4).

### 2.5 Flow graphic colours

Bands and trunk: flat `#bfdbfe`. Gate notches: 2px `#ffffff` cut across the trunk. Dots: `--nx-accent`. End box: flat `--nx-accent`, white text. Text: ink / text-2 only.

---

## 3. Type system (system fonts only; no web-font loading)

All roles come from `design_tokens.css`. `--nx-type-*` are canonical; `--fs-*`, `--site-fs-*` and the existing `--nx-fs-*` names alias them. Shared role values stay identical at every breakpoint except the homepage hero.

| Role | Size / weight | Line height / use |
|---|---|---|
| Tick | 12 / 400 (ZH 13 minimum) | Rendered SVG text must meet the floor |
| Caption / metadata | 13 / 400–500 | 1.5 EN, 1.7 ZH |
| Body / subtitle / control | 15 / 400–500 | 1.6 EN, 1.75 ZH; touch inputs 16 |
| Section / table caption | 18 / 650 | Examples, records, documentation h2 |
| Page title | 20 / 700 sans | 1.3 EN, 1.4 ZH; every content route |
| Count / point estimate | 22 / 600 | Tabular numbers; interval 15 / 400 |
| Story heading / featured result | 28 / 600 | Story headings; exposure featured result |
| Homepage hero | 44 / 38 / 32 at D / T / M | 600; EN serif, ZH sans and 4px smaller |

At <=360px the EN hero is 28px. D >=1100, T 601–1099, M <=600. No other role introduces a responsive size ladder. The story lead aliases body 15px in both languages; story counts alias count 22px. Staircase words use sans 28px desktop and 22px mobile, with 22px counts and 13px node labels, installed by their owner.

Sans stack: `-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Noto Sans CJK SC", "Microsoft YaHei", sans-serif`. Serif, reserved for the EN homepage hero: `"Iowan Old Style", Charter, Georgia, "Noto Serif", "Times New Roman", serif`.

Use tabular numerals for counts and estimates. Sentence case, no tracked uppercase eyebrows, no italics. Chinese labels use tracking 0. Documentation h3 is body size / 650; deeper headings use body or caption size / 600. Use margins and weight for hierarchy.

---

## 4. Spacing, rhythm, columns, alignment

### 4.1 Scale

8px grid with a 4px half step: 4, 8, 12, 16, 24, 32, 48, 64, 96. 28 and 36 appear only as "gap/2" at M and T.

### 4.2 Vertical rhythm

`S` = 96 / 72 / 56 (D/T/M). Half = S/2 = 48 / 36 / 28.

| Between | D | T | M |
|---|---|---|---|
| host header -> hero top (story padding-top) | 48 | 40 | 32 |
| hero caveat -> strip | 32 | 32 | 24 |
| strip -> map section | 48 (S/2) | 36 | 28 |
| map section -> cards section | 96 (S) | 72 | 56 |
| cards section -> flow section | 96 | 72 | 56 |
| flow section -> closing line | 48 (S/2) | 36 | 28 |
| closing line -> seam rule | 96 (S) | 72 | 56 |
| seam rule -> old content | add 32 in the host | 24 | 24 |

Inside a section: h1 -> lead 16; lead -> caveat 16; h2 -> figure/cards 24 / 24 / 16; h2 -> sentence 12, sentence -> frame 24 / 24 / 16; frame -> caption 12; frame -> details row 16; frame padding 24 / 16 / 16.

### 4.3 Columns and alignment

- Story column: content `max-width 1100px`, centred (x 170..1270 at 1440). Text column <= 680px (lead, caption, caveat, sentence, details text; h1 <= 660). Figure column = 1100.
- Gutters at T/M: text edge 24px (T) / 16px (M) from the viewport edge (`--nx-gutter`; `--nx-host` mirrors the host's body padding 16 / 11.2).
- ONE left edge for h1, lead, caveat, strip, h2, frame and card outer edges, captions, details, closing line. Chrome (old header) is exempt. Nothing outside a frame is centred.
- ONE inner grid line inside frames and cards: x0 + 25 (border + 24 padding): the map, legend, card text, flow sources sit on it; the list column's right edge sits at x0 + 1075.
- Right edges: frames, card row, details end at x0 + 1100.
- Cards: 3 equal columns, 16px gap (356px at D).
- Baselines: badge and score centred on one line; score value and label share a baseline; list rows: name and "28 states" share a baseline, dots and range share a centre line.
- Alignment never uses nudges.

### 4.4 Seam with the old table page

Closing line, then a 1px `#e5e7eb` hairline across the story column (`.nx-seam`), then old content (header and filters untouched). The jump from x0=170 to the table's x=16 is deliberate: the hairline marks story -> tool.

---

## 5. Components

### 5.0 Number strip `.nx-strip` (new, from the APE study)

Sibling right after `.nx-hero`. Three cells: number (number-display, ink) over a 13px text-2 label; 1px `--nx-line` vertical hairlines between cells; padding 0 24 (first cell 0 on the left); no boxes, no icons, no background. EN: `483k` / policy texts, `7.8k` / on the shortlist, `8` / policy families mapped. ZH: `48.3万` / 份政策文本, `7,781` / 份入围候选, `8` / 个政策族已绘图 (万 for >= 10,000, exact integers below). Numbers come from data (B19). M: three equal columns, 12px side padding, labels may wrap to two lines (strip 64px tall; ZH 47px). Reference D: 51.7px tall. Markup: `<div class="nx-strip"><div><b>483k</b><span>policy texts</span></div>...</div>`.

### 5.1 Shared figure frame `.nx-frame`

Background `--nx-plate`, border 1px `--nx-line`, radius 12, padding 24 / 16 / 16, no shadow. Both figures. At M: full-bleed (`margin-inline:-16px`, no side border, radius 0, side padding 0, top/bottom hairlines kept); children other than the SVG or a scroll strip use `.nx-in`. SVG inside: `display:block;width:100%;height:auto` (snippet below).

### 5.2 Map card (the dominant figure)

DOM order (matters for the grid): `.nx-frame.nx-mapcard` > `.nx-tabs[role=tablist]` (the family list), `.nx-fsum` (one-line summary, T/M only), `.nx-mapcol` (the `<svg>` then `.nx-readout`), `.nx-legend`.

D >= 1100 (reference at 1440: card 547px tall):
- Inner 1050 wide, padding 24. Grid `minmax(0,2fr) minmax(0,1fr)`, 32px gap: column 1 = 679 (map above, legend row below), column 2 = 339 (family list, rows 1-2).
- Map 679 x 425 (aspect 1.6). Readout 24px, 8px under the map. Legend row 28px, 12px under the readout (it wraps to 2 rows only if labels run long). Column 1 height = 425 + 8 + 24 + 12 + 28 = 497 = list height (8 x 62 + 1). No empty band either side.
- The family list is the selector (5.3). No title in the card; the selected row is the title. No stats panel, no notes.
T 601-1099: pills (one scrolling row, bleeding to the frame's inner border, first pill on the inner line), one-line summary (`.nx-fsum`: "24 states · first laws 2016-2026"), map max-height 440 centred, readout, legend row. Card ~640 at 820 (the 620 cap is a desktop rule; T cap 660).
M <= 600: full-bleed band: 16 top padding, pill row (padding-inline 16), summary (`.nx-in`), map full width (390 x 234), readout and legend (`.nx-in`). Reference: band 482px tall.
Caption under the card, outside, left-aligned, 12px below: one line <= 100 characters, e.g. "Each state is coloured by the year of its first matching law in the database." (copy owner decides the words).
Readout: 13/24 muted, left edge = map left edge. Default "Hover or tap a state"; on hover/focus/tap "{State} · {year | in place by 2016 | no law found | not enough data}". `aria-live="polite"`; height reserved.
Legend (one row at D under the map; wraps naturally at T/M), 13px text-2, 8px swatch-label gap, 24px between items:
1. "First matching law" 13/600 ink + ramp: 5 hard segments, 140 x 10, 1px gaps, outer radius 3, b1..b5; end labels 12px muted tabular under the ramp ends: "2017-18" (left) and "2025-26" (right).
2. Swatch 12x12 r3 `--nx-slate` "In place by 2016".
The legend holds ONLY the ramp and the slate entry. User decision (outranks the rule "every fill has an entry"): no legend entry for the hatched states or the plain land, because they would be statements about what is not covered / not found. Labels <= 24 characters EN, <= 10 ZH.

### 5.3 Family list (leaderboard-style selector; from the APE study)

Eight rows, user order: Occupational licensing, Property tax exemption, Teacher certification, Workers' compensation, Insurance regulation, Administrative procedure, Sales tax exemption, Alcohol licensing (ZH labels <= 8 characters).
Row anatomy at D (62px tall, padding 8x12, hairline `--nx-line` below, list has a hairline on top):
- Line 1: name 15/500 (selected 600) left; "28 states" 13/400 muted tabular right (ZH "28 个州").
- Line 2: dot timeline left; first-law range "2016-2020" 13/400 muted tabular right.
- Dot timeline = 11 slots for 2016..2026, 4px gap. Slot empty: 4px dot `#d1d5db`. Slot with first laws: dot colour = the bin colour of that year (2016 = `--nx-slate`, 2017-18 b1, 2019-20 b2, 2021-22 b3, 2023-24 b4, 2025-26 b5), diameter 6px (1 law), 8px (2-3), 10px (>= 4). The strip is <= 150px wide, `aria-hidden`. It doubles as a per-family mini legend and shows the family's timing at a glance.
- States: default transparent/white; hover `--nx-land`; selected = `--nx-accent-soft` fill, 3px `--nx-accent` left bar, name 600 ink (matches the adoption explorer's family list); focus ring 2px `--nx-accent`, offset -2.
Behaviour: `role=tablist` (`aria-orientation=vertical` at D), rows are `role=tab`, `aria-selected`, `aria-controls` the map. Roving tabindex. Up/Down (Left/Right at T/M) move focus; Home/End; Enter or Space or click or tap SELECTS (manual activation); the map repaints instantly and the readout resets. Row text includes the numbers for assistive tech via `aria-label` ("Property tax exemption, 24 states, first laws 2016 to 2026").
Rows are `<button>`s with four spans: name, `.nx-x` states, `.nx-x.nx-dots` dots, `.nx-x` range (the tokens grid places them 1-2-3-4 in a 2x2 grid; `.nx-x` is hidden below 1100).
T and M: the same buttons render as pills (the `.nx-x` spans are hidden): 44px tall, padding 0 16, border 1px `--nx-line-2`, fill white, gap 8, radius 99, one row, `overflow-x:auto`, `scroll-snap-type:x proximity`, `scroll-snap-align:start`, scrollbar hidden; selected = `--nx-dark` fill, white text. The selected pill is scrolled into view with `scrollIntoView({inline:'nearest', behavior:'auto'})`. Optional right-edge fade `-webkit-mask-image: linear-gradient(90deg,#000 calc(100% - 24px),transparent)`.
Transition: `background` 120ms ease-out.
Longest label ("Administrative procedure" 187px) fits on line 1 of the 339px column with "17 states" (measured).

### 5.4 Example cards (a row; scroll-snap on mobile)

- `ul.nx-cards` of three `li > .nx-card` (white, 1px `--nx-line`, radius 8, padding 24 / 16). Top row: design badge left (RD green pair, DiD blue pair; 12/600, padding 2x8, radius 4, 1px border), score right ("Score" 13/500 muted + value 16/600 ink tabular, 6px gap). 12px below: title 16/600 ink, line-height 1.4 (max 3 lines; no ellipsis). 8px below: meta 13/500 muted "{jurisdiction} · {year}".
- Row: 3 equal columns, 16 gap, equal heights (`height:100%`; measured 158.6px at D with a two-line title).
- < 720 (and M): a horizontal strip: cards 82% of the width (max 320), 12px gap, `scroll-snap-type:x proximity`, `scroll-snap-align:start`, the strip bleeds to the viewport edge with padding-inline = gutter so the next card peeks (that is the swipe cue); no arrows, no dots. Strip height ~140.
- Hover only when the card is a link (`a.nx-card`): border `#bfdbfe`, 120ms. No lift, no shadow.
- Caption under the row: "Design labels and scores are model-generated." 13px text-2, 12px below. h2 above; no sub-line.

### 5.5 Flow graphic (supporting figure, 4th)

Section: h2, then ONE sentence (`.nx-sub`, 16/1.6 text-2, <= 24 words, e.g. EN "Each text passes five checks in turn; what remains is a shortlist for a person to read." - copy owner decides), then the frame, then the details row (5.9). No other text, no links.
Frame `.nx-frame`, D: SVG viewBox 1050 x 290 (= the frame's inner width at a 1100 column: scale 1.0; 0.97 at a 1100px viewport), frame outer height 340. Hard cap 340. Below 1100: vertical variant (HTML/CSS rows plus a thin SVG rail), never a scaled desktop SVG.
Desktop layout in SVG units (x from the frame's inner left edge):
- Sources: x 0-196, four rows, pitch 48 (centres y 78, 126, 174, 222): name 14/600 ink, count 13/400 text-2 tabular on the next line. Left-aligned on the inner grid line.
- Bands: x 204-340, flat `#bfdbfe`, thickness 0.063 units x sqrt(count) (315,727 -> 35.4; 125,919 -> 22.4; 41,496 -> 12.8; 272 -> 1.0, minimum drawn 1.5), eased curves converging on the trunk centreline y = 150.
- Trunk: x 340-940, flat `#bfdbfe`, 44 thick at the start (label "483k" 14/600 ink above it), stepping down at each gate in proportion to sqrt(remaining count).
- Gates: five at x = 400, 520, 640, 760, 880 (pitch 120). Gate = 2px white notch through the trunk + 6px `--nx-accent` dot under it. Name above (14/600 ink, <= 2 lines, label box <= 112 wide, centred on the gate), sub-line under the name (13/400 text-2, <= 2 lines of <= 22 characters), remaining count below the trunk (14/600 ink tabular: "100k", "94.5k", "27.8k", "7.8k"). A 1px `#d1d5db` leader from label to notch is allowed.
- End box: x 944-1050, y 118-182, radius 8, flat `--nx-accent`; "7.8k" number-display 28/700 white, "on the shortlist" 13/500 white.
- One line bottom-left 13/400 muted: "Texts remaining after each step" (copy owner).
- Names/sub-lines end by y = 100; trunk top y = 128; counts from y = 186; label boxes keep >= 8 units apart (check with `getBBox`). Nothing may be cut by the viewBox.
- Optional motion: one white 1px dashed centre line on the trunk (`.nx-dash`, section 6). Nothing else moves.
Vertical variant (<= 1099), fluid to the frame's full inner width (no narrower max-width, or side bands appear): a 6px `#bfdbfe` rail on the inner grid line, 10px `--nx-accent` dots at the gates, rail thickness steps as on desktop; source rows 36 tall (name left 14/600, count right 13/400 tabular); "483k" node row 44; five gate rows 64 tall (name 15/600, sub-line 13/400 text-2, remaining count right 15/600 tabular); end box 72 tall, flat accent, "7.8k" left, "on the shortlist" right. Target ~720 at M, caps 900 (M) / 800 (T).

### 5.6 Closing link line

One line in the text column, left-aligned, 16/500 `--nx-accent`, the whole sentence is the `<a>`, ending with `&nbsp;->` (U+2192). Min height 44. Hover `#1e40af` + underline (host rule). No button, no icon, no rule above it.

### 5.7 Section headings

h2 per section (map, cards, flow), sentence case, no trailing period, no eyebrow labels (decision: none). Length <= 8 words / <= 44 characters EN (<= 2 lines at 390); <= 18 characters ZH. Suggested: map "A rule arrives in one state, then another"; cards "Three candidates from the shortlist"; flow "From 483k policy texts to 7.8k candidates". Only the flow h2 has a sentence under it.

### 5.8 Borders, radii, shadows

1px borders only (`--nx-line`, `--nx-line-2` for pills at T/M). Radii: 4 badges, 8 cards, 12 frames, 99 pills. No shadows. No dividers between sections; spacing does the job.

### 5.9 Method details (single collapsed line; from the APE accordions)

`<details class="nx-details"><summary>Method details</summary><p class="nx-cap">...</p></details>` directly under the flow frame (16px gap), full figure width.
- Closed: 46px tall (44px summary + 2px hairlines): 1px `--nx-line` above and below, no fill, no radius, summary 15/500 text on the left, a 20px muted "+" on the right (a "−" when open). Whole row clickable (44px tap). No marker, no chevron animation, no bold, no icon.
- Open: the paragraph below (13/1.5 text-2, <= 680px, <= 120 words, plain language, 16px under the last line). Content = what the five checks do, one short sentence each; it must not restate the hero caveat or the cards caption and must not use engineering terms (JSON, schema, sub-scores, hard caps); the one allowed exception is the ribbon-width sentence above.
- Collapsed by default, `open` never set by script.
ZH label: "方法详情".

---

## 6. Motion

- Map: fully static. No autoplay, no slider, no transition or animation on fills, strokes or the SVG. A family switch repaints instantly. Hover/focus: outline + readout only. `.nx-map *{transition:none}` if page CSS sets any.
- Flow dashes (optional): only the trunk's centre dash line, `stroke-dasharray: 6 8`, 3s linear loop (14 units per 3s, ~4.7 units/s), opacity <= .6. No hover effect. Under `prefers-reduced-motion: reduce` it is static (the tokens' rule disables all animation and transition under `.story`).
- Hover transitions <= 120ms ease-out, properties `background`, `border-color`, `color` only. No transform, translate, scale or shadow animation. The details "+" swaps to "−" without animation.
- Scrolling strips: `scrollIntoView` with `behavior:'auto'`. No smooth scroll, no autoplay carousel.

Snippets (copy into the page CSS; these are not in `design_tokens.css`):

```css
/* figures */
.nx-frame svg{display:block;width:100%;height:auto;margin-inline:auto}
.nx-map svg{max-height:440px}
@media(min-width:1100px){.nx-map svg{max-height:none}}
.nx-readout{min-height:24px;margin-top:8px;font-size:13px;line-height:24px;color:var(--nx-muted)}
.nx-fsum{margin:0 0 12px;font-size:13px;line-height:24px;color:var(--nx-muted)}
@media(min-width:1100px){.nx-fsum{display:none}}
.nx-legend{display:flex;flex-wrap:wrap;align-items:center;gap:8px 24px;margin-top:12px;font-size:13px;line-height:1.3;color:var(--nx-text-2)}
.nx-legend>span{display:inline-flex;align-items:center;gap:8px}
.nx-ramp{display:grid;grid-template-columns:repeat(5,1fr);gap:1px;width:140px;height:10px;border-radius:3px;overflow:hidden}
.nx-sw{width:12px;height:12px;border-radius:3px}
.nx-mapcard .nx-dots{display:flex;align-items:center;gap:4px}   /* needs the .nx-mapcard prefix: .nx-mapcard .nx-x sets display:block */
.nx-dots i{border-radius:50%}                                    /* width/height/background set inline per dot */
.nx-sub{max-width:680px;margin:-12px 0 var(--nx-head);color:var(--nx-text-2)}
/* cards */
.nx-cards{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;list-style:none}
.nx-card{display:block;height:100%;padding:var(--nx-pad);background:#fff;border:1px solid var(--nx-line);border-radius:8px;color:inherit;text-decoration:none;transition:border-color var(--nx-tr)}
a.nx-card:hover{border-color:#bfdbfe}
.nx-card-top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}
.nx-badge{display:inline-block;padding:2px 8px;border:1px solid;border-radius:4px;font-size:12px;font-weight:600;line-height:1.5;letter-spacing:.02em}
.nx-badge.rd{background:#ecfdf5;color:#166534;border-color:#bbf7d0}
.nx-badge.did{background:var(--nx-accent-soft);color:var(--nx-accent);border-color:#bfdbfe}
.nx-score{display:inline-flex;gap:6px;font-weight:600;color:var(--nx-ink);font-variant-numeric:tabular-nums}
.nx-score small,.nx-card-meta{font-size:13px;font-weight:500;color:var(--nx-muted)}
.nx-card-meta{margin-top:8px;line-height:1.4}
.nx-card h3{font-size:var(--nx-fs-body);line-height:1.4;font-weight:600;color:var(--nx-ink)}
.story:lang(zh) .nx-badge{font-size:13px;letter-spacing:0}
@media(max-width:719px){.nx-cards{display:flex;gap:12px;overflow-x:auto;scroll-snap-type:x proximity;scrollbar-width:none;margin-inline:calc(var(--nx-gutter)*-1);padding-inline:var(--nx-gutter);scroll-padding-inline:var(--nx-gutter)}
.nx-cards>li{flex:0 0 min(82%,320px);scroll-snap-align:start}}
/* closing line, flow dash */
.nx-closing a{display:inline-flex;align-items:center;min-height:44px;font-weight:500}
@keyframes nx-flow{to{stroke-dashoffset:-14}}
.nx-dash{stroke-dasharray:6 8;opacity:.6;animation:nx-flow 3s linear infinite}
```

---

## 7. Wireframes and height budgets

Total story height (padding-top to the seam hairline): D target ~2,150, hard limit <= 3,000. M target ~2,400 (with the flow at ~720), soft limit 3,300, hard limit <= 5,500. Per-section caps are reviewed too.

### 7.1 Desktop 1440 (story column x 170..1270). y relative to the story top; (n) = height in px; measured on the harness.

```
y=0      [story padding-top 48]
y=48     Where are the natural                 serif 44/49, 2 lines (98.6), "hiding?" blue         HERO 210 (cap 240)
         experiments hiding?
         [16] lead 20/30, 2 lines (60) [16]
         Screening shortlist for human review · US policy only · State laws 2016-2026   13/19.5 muted
y=258    [32]
y=290    483k   |  7.8k             |  8                          number-display 28 (hairline separators)   STRIP 52
         policy texts | on the shortlist | policy families mapped   13/17 text-2
y=342    [48]
y=390    A rule arrives in one state, then another        h2 28/33.6                                  MAP SECTION ~636 (cap 680)
         [24]
y=447    +--------------------------------------------------------------+ +------------------------------+  frame 1100 x 547
         | 24                                                           | | Occupational licensing  28 states |
         |  MAP 679 x 425 (static; land, bins, slate, hatch)            | |  . o . . .  . . . .      2016-2020 |
         |                                                              | +------------------------------+
         |                                                              | | Property tax exemption  24 states |  <- selected: soft blue, 3px bar
         |                                                              | |  . . o O O . o . O .     2016-2026 |
         |                                                              | | Teacher certification   19 states |
         |                                                              | | Workers' compensation   19 states |
         |                                                              | | Insurance regulation    20 states |
         |                                                              | | Administrative procedure 17 states|
         |                                                              | | Sales tax exemption     16 states |
         +-- Hover or tap a state   (readout 24) ------------------------+ | Alcohol licensing       13 states |
         | First matching law [b1][b2][b3][b4][b5]  [slate] In place by 2016   (28)
         +-------------------------------------------------------------------------- list 339 x 497 (8 rows x 62) ----+
y=994    [12] Each state is coloured by the year of its first matching law in the database.   caption 13/19.5
y=1026   [96]
y=1122   Three candidates from the shortlist           h2                                           CARDS SECTION 248 (cap 290)
         [24]
         +-------------------+ [16] +-------------------+ [16] +-------------------+        each 356 x ~159
         | [RD]     Score 4.3 |      | [DiD]    Score 4.7 |      | [DiD]    Score 4.5 |
         | Allows person who..|      | Contracts in ...   |      | Post-9/11 GI Bill  |
         | OR State · 2017    |      | CA State · 2023    |      | US Federal · 2009  |
         +-------------------+      +-------------------+      +-------------------+
         [12] Design labels and scores are model-generated.
y=1370   [96]
y=1466   From 483k policy texts to 7.8k candidates     h2                                          FLOW SECTION 497 (cap 520)
         [12] Each text passes five checks in turn; what remains is a shortlist for a person to read.   (25.6)
         [24]
y=1561   +----------------------------------------------------------------------------------------+  frame 1100 x 340 (cap 340)
         | sources  --bands-->  trunk 483k ==[|]==[|]==[|]==[|]==[|]== [ 7.8k ]                    |
         | (4 rows)             Classify  Extract  Literature check  Score  Screen   counts under ticks |
         +----------------------------------------------------------------------------------------+
y=1901   [16]
y=1917   Method details                                                                          +    details row 46
y=1963   [48]
y=2011   Explore how rules spread in all eight policy families ->                                     closing (44)
y=2055   [96]
y=2151   ----------------------------------------------------------------------------  seam hairline (story height 2,152)
```

First viewport at 1440x900 (harness: story top y=72): hero 120-330, strip 362-414, h2 462, card top 519, map 544-969 (84% visible at y=900; 88% with a 56px host header), list rows 544-1041 (5.7 rows visible), legend and card bottom below the fold.

### 7.2 Mobile 390 (story column x 16..374). y relative to the story top, EN harness (header 67px).

```
y=0      [story padding-top 32]
y=32     Where are the natural              serif 32/38, 2 lines (75.5)  (3 lines allowed with Georgia)      HERO 255 (cap 330)
         experiments hiding?
         [16] lead 18/27, 4 lines (108) [16] caveat 13/19.5, 2 lines (39)
y=287    [24]
y=311    483k      | 7.8k            | 8                       24/26 numbers                              STRIP 64
         policy texts | on the shortlist | policy families mapped (labels may wrap)
y=375    [28]
y=403    A rule arrives in one      h2 22/28, 2 lines (56)                                                    MAP SECTION ~600 (cap 700)
         state, then another
         [16]
=================================================================  hairline (full-bleed band top)       band 482
  [16] ( Occupational licensing )( Property tax exemption )-> scroll row, 44, padding-inline 16, snap start
  [12] 24 states · first laws 2016-2026                       (.nx-fsum, 13/24 muted, .nx-in)
  [12] [ MAP 390 x 234, edge to edge ]
  readout 24 (.nx-in)
  [12] legend (.nx-in): ramp 140x10 + end labels, then 3 swatches wrapping (77)
  [16]
=================================================================  hairline (band bottom)
         [12] caption 13/19.5 x 2 lines (39)
y=1000   [56]
y=1063   Three candidates from the shortlist    h2 (28)                                                       CARDS SECTION ~240 (cap 300)
         [16]
         [ card 1, 281 wide, 138 tall ][ card 2 (peeks) ...   -> horizontal scroll-snap strip
         [12] caption 13/19.5 (1-2 lines)
y=1250   [56]
y=1333   From 483k policy texts       h2 (2 lines 56) + [12] sentence 16/24 x 2 lines (48) + [16]             FLOW SECTION ~900 (cap 1,100 incl. heading)
         to 7.8k candidates
=================================================================  flow band (full-bleed), content with .nx-in
         4 source rows (36) -> rail -> 483k node (44) -> 5 gate rows (64) -> end box (72)   target 720, cap 900
=================================================================
         [16] Method details      +        details row 46 (inside the gutter)
         [28]
         Explore how rules spread in all eight policy       closing, 2 lines (48), arrow glued to "families"
         families ->
         [56]
         ------------------------------------------------------------  seam
```

Reference harness numbers (EN, 390x844, header 67px): hero 99-354, strip 378-442, h2 470, band 542-1024 (pills 559-603, summary 615-639, map 651-885 = 63% visible at y=844 with a real 112px header, legend 929-1006), cards 1130-1344, flow section 1400-1723 (placeholder 137px flow; the real one adds ~580), story 1,789 -> ~2,370 with the real flow.

### 7.3 Tablet 820

Hero 38px, 2 lines; strip single row; h2 24; pill strip one row (scrolls, bleeds to the frame's inner border); map max-height 440, centred, legend row, no list; cards 3 columns of 241px (padding 16); flow vertical, fluid to the frame width, <= 800 tall; details as D. Harness: story 2,011 with a 234px placeholder flow, ~2,490 with the real flow.

---

## 8. Figure rules

1. Text floor: 12px rendered for tick labels and years, 13px for legend, captions, list meta and gate sub-lines, 14px for names and counts in the flow. SVG text is measured after scaling: authoring size x (rendered width / viewBox width) >= 12. The desktop flow is authored at the 1050-unit frame width and shown only at >= 1100 (scale >= 0.97), so its minimum authored size is 13 units. Never show a scaled desktop SVG below 1100; use the vertical variant.
2. Label collisions: bounding boxes must not overlap and keep >= 8 units apart. Resolve in this order: shorten the text, allow a second line (max 2), stack name above value, move the label to the other side of the line. Never rotate, never shrink below the floor, never truncate with an ellipsis, never let a label cross a band or a state boundary on the map.
3. Legend rules: inside the card; adjacent to what it explains (one row directly under the map); one entry for the ramp and one for slate (the hatch and plain land are deliberately not listed, user decision); one swatch size (12x12, r3); labels <= 24 characters; no uppercase titles; ramp = 5 hard segments, no gradient.
4. Data-ink: flat fills; one stroke colour per layer; no shadows, glow, halo, 3D, icon circles, gridlines, gradient trunk or decorative tails; thickness encodes quantity only where stated (flow bands, sqrt scale); colour encodes year only on the map and the dot timelines.
5. Colour budget: map = slate + 5 blues + 2 neutrals + hatch; flow = one pale blue + one accent; list dots reuse the map colours. No other hue anywhere in the story.
6. Numbers: separators in full counts; compact "100k / 94.5k / 7.8k" only in the strip, at gate ticks and in the end box; one decimal at most; tabular.
7. Static means static (section 6).
8. Accessibility: each SVG `role="img"` + `aria-label`; map states keyboard-focusable with the outline in 2.4; the list is a tablist with the keys in 5.3; the dot timeline is `aria-hidden` because the range text and aria-label carry the information.
9. Map geometry: Albers USA composite with AK/HI insets, no state labels, no frame lines around insets, no graticule; county and state meshes are single paths with `vector-effect: non-scaling-stroke`.
10. Scannable lists: identity left, numerals right, tabular, hairline rows, one selected row; no zebra, no icons.

---

## 9. ZH-specific rules

1. Line-height 1.75 body and lead, 1.7 caption, 1.3 hero, 1.4 h2 (tokens). Hero 40/34/28 at weight 600 (not 700-800), sans only, tracking 0, `text-wrap: balance`.
2. No italics anywhere, including Latin words inside ZH text; `font-synthesis: none`. Emphasis = weight 600 or `--nx-ink`, not slant, not underline.
3. Punctuation: full-width ， 。 、 ； ： ？ ！ （ ） with no space next to them; quotation marks without inner spaces; the separator " · " keeps ordinary spaces; numeric ranges use an en dash "2016-2026" (nowrap). `line-break: strict` keeps closing punctuation off line starts.
4. Han-Latin spacing: one ordinary space between Han characters and a Latin word or digit group ("LLM 标注 RD、DiD", "州法律 2016-2026 年"). Exceptions: digits directly followed by 万, 亿 or % are glued (48.3万, 1.6%). The caveat line is built from `.nx-seg` spans so "2016-2026 年" never splits.
5. Minimum text 13px (labels, legend, captions, list meta; 12px only for SVG tick years). Pill and row labels <= 8 characters.
6. Lead <= ~36 characters per line at 18px in a 680px column (a maximum, not a target).
7. Brand names and abbreviations (GitHub, RD, DiD, LLM) stay Latin; digits are Latin.
8. Strip and flow compact numbers use 万 (48.3万); below 10,000 write the exact integer (7,781).
9. Hero accent: the last clause ("藏在哪里？") in `--nx-accent`.
10. Family list in ZH: name 15/500, "28 个州" and range 13px muted; same layout, heights, dots.
11. Same layout, frames, heights and tokens as EN; if a ZH title runs longer, shorten the text.
12. V5 page titles (Home, Screening, Estimates, Explore): the ZH h1 uses line-height 1.15, as in EN, not the 1.3 of item 1. This is the R6 title unification (owner taste), measured in `docs/plans/site-restructure/FUNCTION_REVIEW_V5_R13.md`, finding 7: the ZH title wraps to two lines on Home and Explore at 820, 390 and 320.

---

## 10. Anti-patterns (instant review items)

1. A figure without the shared frame, or two frame styles.
2. Two figures of equal weight in view; the flow outweighing the map.
3. A 10-step per-year ramp, a gradient legend, any ramp step within dE < 8 of its neighbour.
4. A blue-tinted fog land; strokes darker than `#aeb6c3` on land; dark county lines.
5. Slate with the same lightness as a ramp step, or a legend that does not name it (the hatch and plain land are exempt by user decision).
6. A legend more than one row away from the map, or floating under Alaska.
7. Empty bands inside figure cards (map narrower than its column, a list shorter than the map column, SVG with padding, flow with 60px of nothing); frame padding above 24.
8. Red or amber on the story; red italic annotations; halos; icon circles; hatched tails; gradients; shadows; "Core" pills.
9. Text below 12px, grey lighter than `#6b7280`, labels smaller than the numbers they explain.
10. Eyebrows, uppercase tracked small caps, section number badges.
11. Right-aligned links far from their object; "Open the funnel" / "Full pipeline diagram" links; a button row; a GitHub or Collaborate block; "What this is not" boxes; defensive wording; mentions of what is not collected; engineering jargon.
12. Green score numerals; any per-design colour other than the RD/DiD pair that matches the table.
13. Serif anywhere except the EN hero; italics anywhere; centred body text.
14. Hover effects that move things; smooth scroll; autoplay; carousel arrows or dots; looping animation other than the optional trunk dash.
15. A floating tooltip on the map; a stats panel in the map card; a second caption; a caveat repeated in the details or elsewhere.
16. A strip with more than 3 numbers, a gap/"missing" number, an evaluative number (rate, accuracy) or an icon.
17. An orphaned arrow, a single word on the last line of a lead, a heading broken into 3 lines.
18. A scaled-down desktop SVG on tablet/mobile; a page-level horizontal scrollbar; any horizontally scrolling thing except the pill strip and the card strip.
19. New breakpoints, colours, radii or type sizes ("close enough" values such as 14.5px or 17px).
20. A divider between every section; nested cards; cards inside frames; more than one `<details>`.
21. A list row taller than 62px at D, more than two lines per row, a header row on the list, or sort controls.
22. Different layout, spacing or type in ZH than in EN.

---

## 11. Review rubric (0-5 each; pass = every criterion >= 4 and total >= 34/40)

Hard fails (affected criterion scores 0; re-work): page horizontal scroll at 1440/820/390; text < 12px; map card > 620 at 1440; flow frame > 340 at 1440; B2 or B3 not met; map animated; any forbidden element (B1, B17, B19-B21); strip with other than 3 data-computed numbers.

| # | Criterion | What a 5 looks like (4 = one minor miss; <= 3 = fix required) |
|---|---|---|
| 1 | Hierarchy | A 2-second squint test at 1440x900 and 390x844 reads: hero, three numbers, the map. Adjacent type levels differ by >= 1.25x. The map card is the only multi-blue object in the first viewport and visibly heavier than the flow card (one pale blue + one accent box). The caveat is visibly secondary. The list reads as the map's legend/selector, not a second figure. |
| 2 | Rhythm / spacing | Every gap equals the spec table within +-2px; same relationship, same gap. No empty band inside any figure; map column and list column equal height at D (+-4px). Story height within budget; map card <= 620; flow <= 340. |
| 3 | Alignment / grid | One left edge (+-1px) for h1, lead, caveat, strip, h2, frames, cards, captions, details, closing. One inner grid line (frame edge + 24; T/M +16) for map, legend, card text, flow sources. Cards equal height. List numerals right-aligned to one edge. No horizontal page scroll. |
| 4 | Typography | Only the specified sizes/weights/line-heights. Hero serif (EN) as spec and survives the Georgia fallback test without overflow; ZH hero sans 600. Lead <= 680px, h1 <= 15em. No orphans, no italics, tabular numerals everywhere (strip, list, cards, ticks). ZH rules in section 9 met. Nothing below 12px. |
| 5 | Colour / contrast | Only palette hexes (check computed colours against section 2). Text >= 4.5:1, body >= 7:1. One accent blue. Map: 5 bins + slate + land + hatch + strokes exactly as specified; list dots reuse them. No amber/red/green (except the RD badge) on the story. Selected row soft-blue + bar, selected pill dark. |
| 6 | Figure quality | Map: static, instant repaint, legend (ramp + slate) adjacent, county texture barely visible, slate deliberate, 5 bins separable at arm's length. List: 8 rows, dots correct per family, selection and keyboard work. Flow: counts at every gate, no label collisions, text >= 13 units, flat fills, vertical variant authored not scaled, heights in caps. Both figures share the frame and look like a pair. |
| 7 | Consistency with old pages | Old header/footer untouched; same blue `#1d4ed8`, focus ring, dark active pill (T/M) and soft-blue selected row as the adoption explorer; RD/DiD badges identical to the table's; radii from 4/8/12/99; hairline `#e5e7eb`. The seam to the table is one hairline with the specified space. EN and ZH identical in layout. |
| 8 | Polish / details | All states exist and are correct: row hover/selected/focus, pill hover/selected/focus, card hover (links only), map hover/focus outline, details open/closed; no layout shift when switching families (readout and legend heights reserved); tap targets >= 44px at T/M; 1px lines crisp at DPR 1 and 2; arrow glued to its word; `prefers-reduced-motion` verified; no console errors; selected pill scrolled into view; card strip peeks the next card at M. |

Score each criterion for EN and ZH at 1440, 820 and 390 and keep the lowest. Total = sum of 8 (max 40).

---

## 12. Review protocol

Builders deliver, per round, in `deploy_output/_release/story_review/rN/` (N = 1, 2):

1. Full-story screenshots (hero top to seam), DPR 1: `story_{en|zh}_{1440|820|390}.png` (6 files).
2. First-viewport screenshots: `first_{en|zh}_1440x900.png`, `first_{en|zh}_390x844.png` (4 files). The most important images.
3. Map recolour check, EN 1440, card only: `map_fam{1|2|8}_en_1440.png` (Occupational licensing, Property tax exemption (default), Alcohol licensing).
4. `tabs_scrolled_en_390.png` (pill strip scrolled to the end), `cards_scrolled_en_390.png` (card strip scrolled), `details_open_en_1440.png`, `details_open_en_390.png`.
5. Hero fallback test with `--nx-display: Georgia`: `hero_georgia_en_1440.png`, `hero_georgia_en_390.png`.
6. `metrics_{en|zh}_{1440|820|390}.json`, produced in the page: `docScrollWidth` vs `innerWidth`; top/height/left/width of `.story`, `.nx-hero`, `.nx-h1`, `.nx-lead`, hero caveat, `.nx-strip`, each `.nx-sec` and its h2, `.nx-mapcard`, `.nx-tabs`, first/last row or pill, map svg, `.nx-readout`, `.nx-legend`, `.nx-cards`, first card, flow frame, `.nx-details`, closing line, `.nx-seam`; computed font-size and font-family of `.nx-h1`; minimum computed font-size over all text under `.story` (SVG text x CTM scale); first-viewport fractions (map visible at 1440x900 and 390x844, rows visible); left x of h1, strip, h2s, frames, cards, captions, details, closing (agree within 1px); per family, the dot colours and sizes rendered.
7. A one-page report: files changed, literals added outside tokens/snippets (with reason), anything in 0.5 / 5 not met.

Reviewer procedure:
1. Open the 4 first-viewport shots, then the 6 full shots, then the metrics. Score the 8 criteria (EN and ZH, lowest per criterion).
2. Return one numbered change list. Each item: `#N [MUST|SHOULD] file > section > selector { property: current -> target }  (criterion, screenshot)`. Max 25 items, ordered by rubric weight (hard fails first, then the lowest criteria). Values are exact (px, hex, token), never "make it nicer".
3. Round 2: builders apply every MUST and SHOULD and resubmit the same deliverables. The reviewer verifies only the listed items and regressions; no new taste requests in round 2.
4. After round 2: pass if every criterion >= 4 and total >= 34. Otherwise the remaining items go to the user as "accepted deviations" or "needs a decision" with a recommendation. Maximum two rounds.

Builder pre-flight (before round 1): metrics clean; B2-B5 numbers met; no text < 12px; no hex outside section 2; family switch repaints with no layout shift; strip and list numbers come from data; EN and ZH screenshots side by side.

---

## 13. Decisions for the user (each has a default the builders follow unless told otherwise)

1. Hero font: EN serif stack (Iowan Old Style / Georgia / Noto Serif), ZH sans. Default: yes. Off switch: `--nx-display: var(--nx-sans)`.
2. Map ramp: 5 two-year bands (2017-18 ... 2025-26) rather than 10 per-year steps, because 10 steps cannot be told apart; the readout still gives the exact year and the list dots show the per-year timing. Default: 5 bands.
3. Slate `#8793a6` is slightly darker/greyer than the explorer pages' `#94a3b8`. Default: new value on the homepage; align the explorers later (one line each in `adoption.html` and `funnel.html`).
4. Ramp direction: homepage later = darker; adoption explorer darker = longer ago (opposite). Default: leave as is.
5. Flow loses the red "ceremonial bills skipped" note, "Core" pill, icons and hatch tail (not in your list). The Classify sub-line may still say "skips ceremonial bills".
6. Example cards link to their table row? Default: no (static, no hover). If yes, `a.nx-card` hover is defined.
7. Default selected family: Property tax exemption (24 states across 2016-2026), although the list is in your order with Occupational licensing first (28 states, mostly "in place by 2016", laws only in 2016-2020: the weakest first impression).
8. T and M use a one-row scrolling pill strip and a vertical flow (a wrapped tab grid measured 3 rows at 820; a scaled desktop flow would put text under 12px). On M the list's numbers are reduced to a one-line summary under the pills.
9. Strip numbers: EN `483k / 7.8k / 8`; ZH `48.3万 / 7,781 / 8`. All from data (483,414; 7,781; 8 families), no gap numbers.
10. The caption "Design labels and scores are model-generated." under the cards stays (you approved it); it is different from the hero caveat, so nothing is repeated.

---

## 14. Direction 4: hero | map in one block (supersedes B1 order, B2, B3, B4, B6 in part, 5.2, 5.3, 5.7 map h2, section 7, and the map caption)

Decided by the user. Story order is now: HERO BLOCK (text left, map card right) -> flow section -> closing line -> seam. Removed from the page: the separate map section, its h2 "A rule arrives in one state, then another" (kept as a visually hidden heading) and the caption under the card ("Colored states show the year of their first matching law." duplicates the legend title). Everything in sections 2-4, 6, 8-10 stays (palette, type, motion, figure rules, ZH, anti-patterns).
Files: `design_tokens.css` (tokens, +2 variables, accent no-wrap), NEW `design_hero.css` (2.0 KB: hero grid + compact map card; load after the tokens with `{{css:design_hero}}`), source of truth = `scripts/viz_site/design/` (mirrored to `deploy_output/_release/`, byte-identical; the spec there is `DESIGN_SPEC.md`).

### 14.1 Composition and grid (desktop >= 1100)

- One grid, `.nx-herogrid`: columns `minmax(0,2fr) minmax(0,3fr)`, column gap 48 (`--nx-hero-gap`). In the 1100px story column: text column 420.8, card 631.2. `align-items:center`: the text block is vertically CENTRED against the card (offset ~136px above and below at 1440; the strip is not pinned to the bottom, a pinned strip leaves a 300px hole in the middle and a hairline does not fix it).
- Text column (`.nx-herotext`, order: h1, lead, caveat, strip): h1 38/1.15 (ZH 34/1.3), EN serif stack, weight 600, tracking -0.02em, accent phrase "DiD and RD designs" in `--nx-accent` and `white-space:nowrap` (so the accent never splits across lines); lead 19/1.5 (ZH 17/1.75), text-2; caveat 13 muted; strip unchanged (28/700 numerals, 13px labels, hairline separator, 32px above). Gaps: h1 -> lead 16, lead -> caveat 16, caveat -> strip 32. The 38px / 19px sizes are set by `.nx-herotext{--nx-fs-hero:38px;--nx-lh-hero:1.15;--nx-fs-lead:19px}` (the ZH rules subtract 4px / 2px from these automatically). They are NOT the 44/20 of the single-column hero because the column is 421px wide: 44px wraps to 3 lines and splits the accent.
- Wrapping behaviour: EN h1 is 2 lines while the story column is its full 1100px (viewport >= ~1150: "Screening Policy Text / for DiD and RD designs", 88px); at viewports 1100-1149 or with the Georgia fallback it is 3 lines ("Screening Policy / Text for / DiD and RD designs"); both are accepted. ZH h1 is 2 lines ("在政策文本中筛查 / DiD 与 RD 设计"). Lead 4 lines EN (114px), 3 lines ZH (89px).
- Right column = the map card (14.2). Top edge of the card = top edge of the grid; nothing sits above it.
- Below the grid: `S` (96 / 72 / 56) to the flow section (the existing `.nx-sec` rule; no special case). Flow -> closing S/2, closing -> seam S, as before.

### 14.2 Compact map card (inside `.nx-herocard`)

Not a new component: the same `.nx-frame.nx-mapcard`, laid out by `design_hero.css` ONLY when it sits inside `.nx-herocard` (so the full "map | list" variant keeps working elsewhere). Reference numbers at 1440 (card 631.2 x 609.6 with 8 families, EN):

```
+-------------------------------------------------------------+  card 631 wide, 1px border, radius 12, plate #f9fafb, padding 24
| 24 states · first laws 2016–2026          Hover or tap a state |  header row 24 + 8 gap: left = family summary, right = hover readout (13/24 muted)
|                                                             |
|   MAP 581 x 364 (aspect 1.6, static, no padding inside)     |  svg full inner width
|                                                             |
| First matching law [b1][b2][b3][b4][b5]  [slate] In place by 2016 |  legend row 28 (12 above), one row, left-aligned
|                                                             |  16
| [Occupational ] [Property tax] [ Teacher    ] [ Workers'   ] |  family grid: 4 columns x 2 rows, cells 139 x 50, gap 8
| [ licensing   ] [ exemption  ] [certification] [compensation] |  (list order = the user's order, left to right, top to bottom)
| [ Insurance   ] [Administrat.] [ Sales tax   ] [ Alcohol    ] |
| [ regulation  ] [ procedure  ] [ exemption   ] [ licensing  ] |
+-------------------------------------------------------------+  24 padding; total 24+32+364+12+28+16+108+24+2 = 610
```

- Header row: `.nx-mapcol` becomes a 2-column grid (`1fr auto`): `.nx-fsum` left (nowrap), `.nx-readout` right (nowrap, text-align right, reserved 24px so nothing moves). Hover or focus on a state: readout shows "{State} · {year | in place by 2016}" exactly as before; outline on the state; nothing else. The family summary line is the card's title line: there is no other title.
- Map: static; `max-height` removed (width-driven); 581 x 364 at 1440, 553 x 346 at 1100.
- Legend: unchanged content (ramp with end labels + slate entry), one row under the map, 12 above; it wraps to two rows only if labels are long.
- Family grid (the selector): `display:grid; grid-template-columns:repeat(var(--nx-cols),minmax(0,1fr)); grid-auto-rows:minmax(48px,auto); gap:8px`, 16px below the legend. `--nx-cols` is set inline on `#sm-tabs` by the generator: n <= 4 -> n (one row); 5-6 -> 3 (two rows, no orphan row); 7-12 -> 4; > 12 -> 5. Cell: white, 1px `--nx-line-2`, radius 8 (the one place a selector is not a 99px pill: two-line labels do not fit a pill), padding 6x10 (6x6 at 1100-1279), text 15/500 `--nx-text` centred, wraps to 2 lines (3 are tolerated, the row grows to 68px and the card to <= 630); `overflow-wrap:anywhere` for a pathological word. Hover: fill `--nx-land`. Selected: fill `--nx-dark`, border `--nx-dark`, text white (the same dark selected state as the pills at T/M; the soft-blue + bar of the full list is not used here). Focus: 2px `--nx-accent` ring, offset 2. Tap target >= 48 (>= 44 rule).
- Counts, year ranges and dot timelines are NOT shown in the cells (139px cells cannot hold three lines at >= 13px without growing to ~78px rows and a 666px card); the selected family's count and range live in the header line, and every cell keeps the full data in its `aria-label` ("Property tax exemption, 24 states, first laws 2016 to 2026"). If the user later wants numbers in the cells: rows 76px, card 666px, still inside the 700 cap.
- Data tolerance (tested with 6, 8, 10 and 12 families and 45-character names at 1440):

| Families | Cols x rows | Card height at 1440 |
|---|---|---|
| 1-4 | n x 1 | 552 |
| 5-6 | 3 x 2 | 610 |
| 7-8 | 4 x 2 | 610 (EN, 2-line labels; ZH 606) |
| 9-12 | 4 x 3 | 668 |
| 8 with one 3-line label | 4 x 2 | 628 |

Hard cap 700 at 1440 for any number of families up to 12; target <= 620 for 8. At 1440x900 (story top 72) the card bottom is at 730-790, always above the fold line.
- Long names: prefer <= 26 characters EN / 8 ZH; longer names wrap to 3 lines. Never truncate, never ellipsis, never shrink below 15px.
- Counts vary freely: the numbers appear only in the header line and aria-labels; no layout depends on them.

### 14.3 Tablet (601-1099) and mobile (<= 600)

Stack, no two-column grid, no change to the existing figure layout except that the standalone section and caption are gone:

| Block (top to bottom) | T 820 | M 390 |
|---|---|---|
| story padding-top | 40 | 32 |
| text: h1 / lead / caveat / strip | 38/1.15 serif (2 lines, 87), lead 19 (2 lines, 57), caveat 19.5, strip 50; text block 277 | h1 32/1.18 (2 lines, 75), lead 18 (4 lines, 108), caveat 19.5, strip 47; block 306 (ZH 282) |
| gap text -> card | 36 (S/2) | 28 |
| map card | 638: pill strip (44, one row, scrolls, first pill on the inner line), summary line, map 754 x 440 max, readout, legend row | 467 full-bleed: pill strip 44, summary line, map 390 x 244, readout, legend (2 rows, 53) |
| gap card -> flow h2 | 72 | 56 |

- The family selector at T/M stays the pill strip (spec 5.3 T/M: radius 99, dark selected, scroll-snap, fade). The 4 x 2 grid is desktop-only: at 820 the pills row is one line of 44px against a 600px+ grid, which is the better trade for the map.
- The summary line is the first child of `.nx-mapcol`, so the order is pills, summary, map, readout, legend (as today).
- First viewport 390x844 (real header ~112px): hero text, strip, pill row, summary line and the WHOLE map (map bottom ~820); the legend is just below the fold.

### 14.4 Heights and first viewport

| | D 1440 | T 820 | M 390 |
|---|---|---|---|
| text block (top -> bottom, abs y) | 256 -> 593 (centred) | 107 -> 385 | 99 -> 405 |
| map card (top -> bottom, abs y) | 120 -> 730 (610) | 421 -> 1059 (638) | 433 -> 900 (467) |
| flow h2 (abs y) | 826 | 1131 | 956 |
| story height (padding-top to seam) | ~1,360 | ~2,030 | ~1,860 |
| hard budget | 1,800 | 2,600 | 2,500 |

(Absolute y with the harness header 56 + body 16.) First viewport at 1440x900 must show: the whole hero text column, the whole card including the family grid (card 120 -> 730), and the flow h2 + sentence peeking at the bottom edge (825-905) as the scroll cue. The flow FRAME must start below y=900 so only one figure is dominant. At 1440x800 or 1366x768 the card still fits (730 < 768).

### 14.5 Tokens and files (what changed)

- `design_tokens.css` (5,977 bytes): added `--nx-hero-gap:48px` and `--nx-cols:4` (default; the generator overrides inline); `.nx-accent{white-space:nowrap}`; removed the dead `.nx-hero+.nx-strip+.nx-sec` selector and the unused `.nx-num`. Nothing else changed.
- `design_hero.css` (2,053 bytes, new): `.nx-herocard` spacing, the caption hide (`.nx-herocard .sm>.nx-cap{display:none}`), `.story .sr-only` (the old table page has no `.sr-only`), the 2fr/3fr grid, the `.nx-herotext` size overrides, the compact card rules scoped to `.nx-herocard .sm ...`. Self-contained; uses only existing tokens.
- No other token changes. The full "map | list" rules in the tokens file stay (used by the generator's standalone variant).

### 14.6 Review rubric additions (apply on top of section 11)

- 1 Hierarchy: squint test at 1440x900 = hero text, then the card; the map is the only multi-blue object; the text block is vertically centred on the card (centre-to-centre within 8px).
- 2 Rhythm: card <= 620 (8 families) / <= 700 (<= 12); flow frame top >= 900 at 1440x900; gap grid -> flow = S; no gap larger than S anywhere inside the hero block except the text offsets (136 +- 16).
- 3 Alignment: text column left edge = x0; card right edge = x0 + 1100; header row text sits on the card's inner grid line (x + 25) and the grid cells' outer edges on the same inner lines; 2fr/3fr gap 48 exactly.
- 6 Figure quality: all 8 families clickable, selection recolours instantly, keyboard Up/Down/Left/Right/Home/End work in the grid, 6/10/12-family dry runs do not overflow.
- 8 Polish: no page scroll jump on load (selected-tab scrolling must not move the window), readout does not shift the header row, cells never exceed 3 lines.
- Hard fails added: horizontal page scroll; card > 700; text column and card overlapping; map caption or the old map h2 still visible.

### 14.7 Owner change lists

(a) Map owner (`make_story_map.py`, `partials/story_map.html`, its i18n and JS; keep the full "map | list" layout working):
1. Move `<p class="nx-fsum ..." id="sm-fsum">` inside `.nx-mapcol` as its FIRST child (keep the id and the JS that updates it; keep `.sm .nx-fsum{display:none}` at >= 1100 for the full variant). At T/M the visual order must stay pills, summary, map, readout, legend.
2. Emit `style="--nx-cols:N"` on `#sm-tabs` from the family count: n <= 4 -> n; 5-6 -> 3; 7-12 -> 4; > 12 -> 5 (add the same rule to the generator's self-check).
3. Remove `aria-orientation="vertical"` from the tablist (the hero layout is a grid; the full variant may set it via the existing layout script only when the list is vertical).
4. Keyboard (grid): Left/Right move focus +-1 (wrap), Up/Down move +-`cols` (cols = number of tracks in `getComputedStyle(tablist).gridTemplateColumns` when `display:grid`, else 1 in the vertical list, and +-1 in the horizontal pill strip), Home/End, Enter/Space select (manual activation), roving tabindex. Unchanged semantics for T/M.
5. Selected-tab scrolling: run only when `tablist.scrollWidth > tablist.clientWidth + 1` (pill strip) and use `tablist.scrollTo({left: tab.offsetLeft - (tablist.clientWidth - tab.offsetWidth) / 2, behavior: 'auto'})`. Never `scrollIntoView` (in the grid layout it would scroll the page on load).
6. Hover readout: nothing to change except the text must be `white-space:nowrap`-safe: longest string "District of Columbia · in place by 2016" (EN) fits the 583px header next to the summary at 1100 (555px inner); ZH is shorter.
7. Self-check: add "hero context" checks (card inside `.nx-herocard`, viewport 1440x900): card height <= 620, header row 1 line, svg width 581 +- 2 and aspect 1.6, tab cells >= 48 tall, no overflow, `--nx-cols` consistent with the family count, plus dry runs with 6, 10 and 12 families and a 45-character name. Produce `map_v5_{en,zh}_{1440,1100,820,390}_{family}.png` for the review.
8. No other change: palette, strokes, bins, legend content, static behaviour, aria labels (each tab keeps name + count + range).

(b) Assembler (`partials/home_story.html`, `i18n/home_story.*.json`, `build.py` only if `{{css:...}}` needs a second name):
1. Include `{{css:design_hero}}` right after `{{css:design_tokens}}`.
2. New DOM (hero + strip move into the left cell; the map section becomes the right cell):
```html
<div class="nx-herogrid">
  <div class="nx-herotext">
    <div class="nx-hero"> h2.nx-h1 (accent span) / p.nx-lead / p.nx-cap </div>
    <div class="nx-strip"> ... </div>
  </div>
  <section class="nx-herocard" aria-labelledby="hs-map">
    <h2 class="sr-only" id="hs-map">{{t:story.map.h}}</h2>
    {{partial:story_map}}
  </section>
</div>
<section class="nx-sec" aria-labelledby="hs-flow"> ... flow ... </section>
```
3. Delete the old `<section class="nx-sec" aria-labelledby="hs-map">` wrapper and its visible `<h2 class="nx-h2">`; keep the i18n key `story.map.h` (now the hidden heading; no visual change to the copy). The caption is hidden by design_hero.css (the partial may keep emitting it).
4. Remove from the page CSS anything duplicating `design_hero.css`; keep the existing flow, details, closing and seam CSS. The `.nx-hero+.nx-strip+.nx-sec` rule is gone from the tokens, so the flow section takes the plain `.nx-sec` margin (S): nothing to add.
5. Re-verify the hero copy fits: EN h1 two lines at viewport >= 1150, ZH two lines; lead <= 4 lines EN / 3 lines ZH at 421px. If a longer subline is ever used, the text column grows and stays centred (up to ~470px of text before it exceeds the card height).
6. The hero H2 stays an h2 (the page's single h1 is the site title above).
7. Screenshots (review round): `{en,zh}_{1440,1100,820,390}_{fold,full}.png` plus `en_1440_fam{1,8}.png` (first and last family selected).

(c) Flow section spacing: no change. Gap hero block -> flow = S (96/72/56) through the plain `.nx-sec` rule; flow -> closing = S/2 through `.nx-sec.nx-end`; do not add a margin to `.nx-herogrid+.nx-sec` beyond `design_hero.css`. The flow section content (h2, one sentence, figure, details) is untouched. Check once at 1440x900 that the flow frame starts below the fold (y >= 900); if the real header is shorter than 56px, nothing needs to change (the h2 and the sentence peek in, the frame stays out).

### 14.8 Reference renders

Harness (built from the real tokens + design_hero.css + the live story markup, with the real map): scratchpad `d4/f_en_fold.png` (EN 1440), `f_zh_fold.png`, `f_en1100_fold.png`, `f_en820_*`, `f_en390_*`, plus dry runs `f_n6`, `f_n10`, `f_long`. Measured: EN card 120 -> 729.6 (609.6), text block 256.5 -> 593.1, h1 2 lines 38px, flow h2 825.6; ZH card 605.6, h1 2 lines 34px; at 1100 card 591.6, h1 3 lines.

### 14.9 Round-4 addendum: curated map card (supersedes the numbers in 14.2 and 14.4 where they differ)

The right-hand card now shows ONE real policy at a time (8 curated policies, 4 x 2 name-only grid at >= 1100, pill strip below). Rules:
- Frame height at 1440: target <= 640, hard cap 700 (EN and ZH, all 8 policies); card incl. caption row <= 700; at 1440x800 the whole frame ends above y=800.
- Header row: policy title (15/600 ink) left, "N states · first-last year" (13 muted) right. One line.
- Readout: ONE reserved line (24px, 4px under the map), nowrap with ellipsis; format "State · year" and, for a state with a bill in the database, " · In our database: {short title}". Legend sits 8px below it. No empty band: svg bottom -> legend top <= 44px.
- Legend: title = the year basis of that policy ("Year signed", "Year benefits first paid", "Year enacted"), ramp 5 hard segments, 64px each, tick labels with >= 14px gaps, dot entry "Bill in our database" (white dot, 1.25px `#111827` outline, r 5.5 in the 975-unit viewBox); the dot entry wraps below 1280px.
- Caption: one definition sentence, 13px text-2, under the frame, flush with the frame edge; the Sources disclosure shares that row (caption left, "Sources +" right; panel below). No nested scrollers; one line per state; <= 2 domains per state, prefer .gov.
- Hero grid structure at >= 1100: `.nx-herotext{grid-area:1/1;align-self:center}`; `.nx-herocard` and its `.sm` child are `display:contents`; the frame is `grid-area:1/2`; caption and Sources are `grid-column:2` (rows 2-3). The text block is centred on the FRAME and never moves when Sources opens (test: h1 top identical open vs closed). The old `padding-bottom` workaround must not be used.
- Hidden h2 (`story.map.h`) describes the card: "Policy adoption by state and year".


## 15. WP1 shared consumer contract (2026-10-04)

### 15.1 Chrome and grid

One inline header, nav, pill and footer serves all route families in EN and ZH. `head_css.html` owns the styles; generated chrome uses these selectors:

- `header.site-head[data-nx-chrome="header"]`: `h1.site-brand > a.site-brand-link` points home in the current language; the existing `.site-sub` remains inside that link. `.site-links` contains the existing documentation, GitHub and author links. The language link is the header's third sibling.
- `a.lang-switch > span.lang-pill`: the outer target is a real 44px target, inner outline 28px. Inner content is the existing SVG, EN label, `.lang-sep` containing `|`, and 中 label; `.on` marks the current language with ink and 600 weight. Immediate navigation, no glide.
- `.view-toggle[data-nx-chrome="nav"]`: existing outlined 6px-radius controls, dark active control and existing separator. Each target is at least 44px. Active routes use `aria-current="page"`; exposure records may mark the exposure parent `true`. Documentation marks its header link current.
- `footer.site-footer[data-nx-chrome="footer"]`: two centred `.ft-l` flex lines, 13px muted type. Author plain, `.ft-lic` muted and underlined, invitation link accent / 500. `.ft-sep` has `.35em` inline margins in both languages. Preserve all existing wording and destinations.

At <=600px the brand and pill occupy row one; `.site-links` occupies one left-aligned second row. Nav is one nonwrapping row with native horizontal overflow in its own box. The active item's utility adjustment changes only horizontal offset, immediately, with no focus transfer. No masks or new hints. At larger widths the same nav stays in one native scrolling row; print alone may wrap. Header-to-nav gap is `--nx-chrome-nav-gap:12px`; nav-to-content gap is `--nx-chrome-content-gap:16px`. `.page .page-title` has no additional top margin.

White ground, ink `#111827`, body `#4b5563`, muted `#6b7280`, line `#e5e7eb`, accent `#1d4ed8`. Shell max `--nx-shell:1500px`, content max `--nx-col-fig:1100px`. `.nx-content` supplies the latter column. Page titles/cards/figures authored for 1100px share its edge. Documentation retains its sidebar plus readable 72ch EN / 40em ZH body column, centred as one unit. Its WIP line belongs to that content column.

### 15.2 Role and motion hooks

Optional CSS roles: `.nx-page-title`, `.nx-section-title`, `.nx-count`, `.nx-featured`, `.nx-interval`, `.nx-caption`, `.nx-tick`. Existing `.page-title`, `.stat`, `.nx-h1`, `.nx-h2`, `.nx-strip` and `--fs-*` consumers remain supported. Owner modules remove competing local title/number values when integrating.

| Duration token | Value | Easing token |
|---|---|---|
| `--nx-motion-feedback` | 120ms | `--nx-ease-feedback: ease-out` |
| `--nx-motion-panel` | 180ms | `--nx-ease-panel: cubic-bezier(.2,.7,.2,1)` |
| `--nx-motion-reveal` | 320ms | `--nx-ease-reveal` aliases panel |
| `--nx-motion-value` | 600ms | `--nx-ease-value` aliases panel |
| `--nx-motion-landing` | 1200ms | `--nx-ease-landing: ease-out` |

`--nx-ease-draw: cubic-bezier(.65,0,.35,1)` serves sequence-specific drawing durations. `--nx-tr` is the feedback duration/easing compatibility alias. Every transition and animation opts in through JS-added `html.nx-motion`, including colour changes. Round 2: the runtime executes in head and adds a temporary `nx-motion-boot` flag only with supported JS and permitted motion, before the first figure paint. Shared CSS prepares known drawing/count states during parsing. `once()` registers cleanup before adding `nx-motion-pending`; DOMContentLoaded removes boot. Unregistered content becomes static then, with a five-second startup safety net. The root `nx-motion` class alone conceals nothing. Final geometry and values are base HTML/CSS. Sequences have cleanup and bounded watchdogs; reduced-motion changes, errors and print finish them. Unsupported observers and no JS preserve final figures.

`NXMotion.landing(target)` resolves section targets to their first heading and table targets to their caption, then applies `.nx-landing` for the shared 1200ms background fade; the class has no base cue. `[id]` uses `scroll-margin-top:var(--nx-anchor-offset)` (default 16px); the utility updates this from the active content toolbar when applicable. Anchor jumps are immediate in every mode. Reduced motion and print disable transitions/animation and force `scroll-behavior:auto`.

The utility's once/cleanup/cancel contract and accepted figure sequences are defined by the arbiter's motion section. No local `nx-anim` convention. Each illustration runs once, one at a time; explicit interaction transitions may repeat after a new action. No navigation animation, table cascade, inferred-date playback or estimate tally.

### 15.3 Integration and browser verification

Shared CSS is inlined into generated pages and marked original-page slots, without a new served asset. Original-page owners remove legacy chrome rules while preserving body/API behavior and translation checks. Browser reviewer checks all route families and both languages at 1440/820/390/320: title baselines, horizontal-only nav scroll, 44px targets without overlap, EN/中 round trips, footer separation, focus, no page overflow, live reduced-motion changes, print and no-JS final figures. Static checks do not establish browser acceptance.

### 15.4 Inclusion and presentation APIs

`shared_chrome.render_slots(lang, page, features=...)` returns unmarked `style/header/nav/footer/motion` strings. `marked(slot, body)` creates `<!-- NX:SLOT:START -->` / `<!-- NX:SLOT:END -->` markers, where SLOT is STYLE, HEADER, NAV, FOOTER or MOTION. Original owners install all five slots, including empty bodies; place STYLE and MOTION in head, HEADER/NAV before page content and FOOTER after it. Remove their old chrome markup and rules. `include_slots` refreshes slot bodies and moves legacy MOTION slots into head once; subsequent refreshes are byte-idempotent. Features are `github/examples/design_opportunity/policy_exposure`; use `build.chrome_features()` to keep eligibility data-driven. The viz head_css partial emits style and motion slots; the header emits header and nav. Pages must not add a second motion partial. The ZH builder translates only body text, regenerates localized chrome and exempts only exact retired chrome pairs; body ID/data/API integrity remains enforced. Legacy consumers keep their previous pill structure until opting in.

`window.NXMotion.once(element, run, {finish, threshold:.35, maxDuration:5000})` requires the final-state `finish(reason)` callback before arming. `transition(element, run, {finish,maxDuration})` supersedes earlier work on the same element. Callback context exposes `timer/clearTimer`, `raf/cancelRaf`, `animate/track/smil`, `onFinish/finish`, `active/paused/pause/resume`, `releaseIntro`; use these to track every resource. Duration is active time, capped at 60000ms; supply enough for an entire finite sequence. Pending drawing/count states are prepared by shared CSS; labels and final accessible values remain readable. `nx-motion-pending` clears at start; `nx-motion-armed` and `nx-motion-running` replace it. `nx-motion-frame` marks the first committed RAF value. Finish clears temporary flags and marks introductions complete. A missing media event is handled by the next tracked frame/timer or watchdog, committing every final state and removing the root class. One shared 120ms preference safety timer continues through animation pause and stops when the last record finishes; paused work cannot remain frozen after an event is missed. `ctx.releaseIntro()` releases the shared illustration slot at reveal completion while tracked finite dots continue. Missing WAAPI easing uses the existing feedback/panel/reveal/value token by duration; explicit figure curves remain intact. Interaction uses `nx-motion-interaction`. `query`, `allowed()` / `allow()`, `finishAll(reason)` and `landing(element)` are shared. Handles expose `finish/cancel/active/state`. The map owns visibility and pause controls; `pause/resume` suspends tracked work and watchdog. Observe a short leading region for tall figures. Consumer print/reduce rules retain their own final geometry. `[data-nx-anchor-toolbar]` identifies the sticky content toolbar whose actual geometry sets the anchor offset.

The optional WP4 module exports `render_case_page(selection, ctx, errs, template, *, services)`. Inputs are copies; original selected results remain unchanged. Services are `render_case_page` (base renderer, without extension recursion), `lean_event_svg`, `case_page_typography`, `render`, `case_display_scale` and `case_display_number`. If the module or entry point is absent, the legacy base renderer remains available. Existing public helpers stay compatible. WP4 changes presentation through this hook, without edits to this builder or frozen adapters.

Round-2 isolated originals: `python3 scripts/zh_pages/build.py --out /private/tmp/site-preview/zh` writes both languages under `/private/tmp/site-preview`, leaving deploy_output unchanged. Full output generation remains hub-owned.

### Polish round 4 motion contract

Observer deliveries from one rendering turn are batched before starting introductions;
ready introductions run in DOM order, independent of registration or callback order.
`NXMotion.once` accepts optional `prepare()` after the finalizer is registered and only
when motion is allowed. It runs synchronously before observation, marks
`nx-motion-prepared`, and commits the finalizer on any failure. Finish removes all
preparation flags. Consumer boot gates must release prepared geometry and preserve
final no-JS, reduced-motion and print states. Map fills/dots remain empty while pending.
