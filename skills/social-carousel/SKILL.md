---
name: social-carousel
description: |
  A three-card social-media carousel laid out as 1080×1350 portrait cards
  (Instagram card-news standard, 4:5 ratio) — three cinematic, on-brand
  panels with display headlines that connect across the series ("onwards."
  → "to the next one." → "looking ahead."). Each card has a brand mark,
  a number / total, a caption, and a "loop" affordance. Use when the brief
  asks for a "carousel post", "social carousel", "Instagram carousel",
  "LinkedIn series", "X thread cards", "카드뉴스", or "三连发". Korean
  text is rendered via Pretendard — pairs well with wkhtmltopdf/wkhtmltoimage
  for pixel-precise PNG/PDF export at 1080×1350 (Instagram) or A4 (print).
triggers:
  - "social carousel"
  - "carousel post"
  - "instagram carousel"
  - "linkedin carousel"
  - "x thread cards"
  - "social series"
  - "카드뉴스"
  - "인스타 카드뉴스"
  - "三连发"
  - "轮播图"
od:
  mode: prototype
  platform: desktop
  scenario: marketing
  featured: 7
  preview:
    type: html
    entry: index.html
  design_system:
    requires: true
    sections: [color, typography, layout, components]
  example_prompt: "Design a 3-card cinematic social carousel — ‘onwards.’, ‘to the next one.’, ‘looking ahead.’. 1080×1350 portrait cards (Instagram 카드뉴스 standard, 4:5), drop-into-Instagram ready."
---

# Social Carousel Skill

Produce a 3-panel social carousel on a single dark stage. Each panel is a
1080×1350 portrait card (Instagram 카드뉴스 standard, 4:5 aspect ratio) —
connected as a series, but each readable on its own.

## Workflow

1. **Read the active DESIGN.md** (injected above). Pick the loudest serif
   token for the headline lockups and a mono token for stamps / counters.
2. **Pick the theme + 3 captions** from the brief. The captions must read
   as one sentence when stacked: ("onwards." → "to the next one." →
   "looking ahead." or "input." → "iterate." → "ship.").
3. **Stage** — full-bleed dark page. Top header strip:
   - Left: serif italic display "Three posts. One beat."
   - Just below the title: a one-line description in muted mono ("1080×1350
     · 카드뉴스 4:5 · cinematic video loops · minimal type. Drop into Instagram,
     LinkedIn, or X — each post stands on its own or runs as a three-part
     series.").
   - Right: small mono badge "SERIES · 01 → 03".
4. **Cards** — 3 portrait cards in a horizontal row (wraps to stack on narrow
   viewports). Each card is `aspect-ratio: 4 / 5` (1080×1350) with rounded 12px
   corners and a subtle 1px border, plus a soft drop shadow. The taller portrait
   format gives more vertical room for headline lockups stacked above the
   bottom caption strip.
   - Background: a layered gradient that *suggests* a cinematic photo — for
     example, panel 1 = warm dawn meadow (stacked greens with a cyan sky
     wash); panel 2 = forest dusk (warm oranges fading into deep teals);
     panel 3 = pink-mountain ridge (rosy peaks against a dim violet sky).
     Use `radial-gradient` + `linear-gradient` only — no images.
   - Top-left chip: brand wordmark in serif italic ("Jerrod Lew") with a
     small accent dot.
   - Top-left below chip: micro mono index "AI · 01 / 03" (and 02, 03).
   - Bottom-left: the headline lockup in white serif display, italic accent
     on one word.
   - Bottom-right corner: a `1× LOOP` mono stamp inside a thin border.
   - Bottom strip caption: small caps mono describing the imagined frame
     ("Man, walking forward — close.", "Woman, stepping into frame.",
     "Woman, overlooking the city.").
5. **Write** a single HTML document:
   - `<!doctype html>` through `</html>`, CSS inline.
   - Load **Pretendard** for any Korean / sans-serif text via the jsDelivr
     CDN (`https://cdn.jsdelivr.net/gh/orioncactus/pretendard@v1.3.9/dist/web/static/pretendard.min.css`)
     so 한글 카피도 디자이너급 자간/렌더링이 보장됩니다.
   - Cards are sized via `width: clamp(260px, 28vw, 360px)` so 3 fit
     comfortably across most desktops at 4:5 portrait and stack at < 1100px.
   - `data-od-id` on stage, each card, each headline.
6. **Self-check**:
   - The three headlines together form one sentence and feel cinematic.
   - Mono is used only for the wordmark index, the loop stamp, and the
     bottom captions. The headlines stay serif. Korean copy uses Pretendard.
   - Each panel's color story is distinct — no two share a dominant hue.
   - At 4:5 the headline lockup must not crowd the bottom caption — keep
     ≥ 24% vertical breathing room above the caption strip.

## Export to Instagram-ready PNG / A4 PDF

The HTML output renders 1:1 in the OD preview. To produce a pixel-precise
1080×1350 PNG (or A4 PDF) for upload, pair with **wkhtmltoimage / wkhtmltopdf
+ Pretendard** — the combination Korean designers use for crisp 한글 자간:

```bash
# Card-news PNG (1080×1350 per panel)
wkhtmltoimage --width 1080 --height 1350 --enable-local-file-access \
  --custom-header User-Agent "Mozilla/5.0" \
  panel-01.html panel-01.png

# A4 PDF (use the same skill output, target A4 sheet)
wkhtmltopdf --page-size A4 --margin-top 0 --margin-bottom 0 \
  --margin-left 0 --margin-right 0 --enable-local-file-access \
  carousel.html carousel.pdf
```

## Output contract

Emit between `<artifact>` tags:

```
<artifact identifier="carousel-slug" type="text/html" title="Carousel — Title">
<!doctype html>
<html>...</html>
</artifact>
```

One sentence before the artifact, nothing after.
