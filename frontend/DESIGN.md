# CNTRL web app: the design system

The app matches the public website: cream paper, ink, one green accent, warm
charcoal in the dark. Everything here is already in `src/styles.css` (the
`:root` block at the top) and `src/components/`. This page says what the
pieces are and how to use them, so a page restyled next week looks like one
restyled today.

The one rule behind every rule below: **structure is information**. A border,
a colour, a mono face or a tag says something about the content. If it says
nothing, remove it.

## Tokens

All colour comes from `:root`; never write a hex value in a page. Use the site
names (left) in new code; the app aliases (right) exist so the older rules
keep rendering and are not wrong to use.

| Site name | Light | Dark | Alias | Use |
|---|---|---|---|---|
| `--paper` | `#faf7ef` | `#11130f` | `--bg` | the page, menus, modals, fields |
| `--ink` | `#1b1a15` | `#f4f4ef` | `--text` | text, the primary button |
| `--slate` | `#625c50` | `#a3a69b` | `--dim` | secondary text: ledes, labels, hints |
| `--faint` | `#756f63` | `#8e9186` | | tertiary: timestamps, placeholders |
| `--surface` | `#f2ece0` | `#1a1d17` | `--panel` | one step down: cards, the sidebar, code |
| `--line` | `rgba(70,50,20,.11)` | `rgba(255,255,255,.1)` | `--border` | hairlines between things |
| `--line-strong` | `rgba(70,50,20,.22)` | `rgba(255,255,255,.24)` | `--border-strong` | control borders (fields, ghost buttons) |
| `--wash` | `rgba(70,50,20,.07)` | `rgba(255,255,255,.08)` | `--panel-2` | hover, the current tab or link, tag backgrounds |
| `--accent` | `#557241` | `#a3bd8c` | | links, focus ring, a switch that is on, the current nav glyph |
| `--ok` | `#557241` | `#9ec48a` | `--green` | done, allowed, healthy |
| `--warn` | `#8a6212` | `#d9b36a` | `--amber` | pending, waiting for you, untrusted |
| `--bad` | `#a8322d` | `#e27d6f` | `--red` | failed, blocked, destructive |

Other tokens: `--field-bg` (what an input sits on), `--bubble` (the
operator's chat message), `--overlay` (the modal scrim), `--shadow-pop`
(dropdowns, toasts) and `--shadow-win` (windows, modals), `--glass` (the nav
only; nothing else blurs).

Theme: light is the default. `index.html` stamps `data-theme` on `<html>`
before first paint from `localStorage['jarvis.theme']`, else the OS's
`prefers-color-scheme`. The toggle in the nav (`useTheme` in `App.jsx`) flips
and remembers it. Dark is `:root[data-theme='dark']`; there is no second
media-query copy, so add a dark value in that one block.

### Type

Two faces, self-hosted in `public/fonts/` (OFL; licences beside them):

- **Schibsted Grotesk** (`--sans`) for every word. Body is 15px/1.5, 400.
  Headings are 600 with `letter-spacing: var(--track-head)` (-0.02em).
- **Martian Mono** (`--mono`) for data only: ids, hashes, sizes, hosts, paths,
  commands, code, tool and model names. Never for labels or prose. `code`,
  `kbd`, `pre` and `.mono` get it; nothing else should.

Scale: `--fs-xs` 11, `--fs-sm` 12, `--fs-md` 13, `--fs-base` 14 (controls),
`--fs-lg` 15 (body), `--fs-xl` 18 (a header bar's title), `--fs-2xl` 24 (a
document page's title), `--fs-3xl` 32 (rare). Use the steps, not .5 sizes.

Section labels are **sentence case**, `--fs-sm`, 600, slate. Not tracked, not
uppercase: that was the commonest tell of the old look. (The last rule in
`styles.css` forces this on the old uppercase classes until each is rewritten;
delete a class from that list when you restyle its page.)

### Spacing and shape

Spacing ladder `--sp-1` (2px) … `--sp-11` (40px); 8px (`--sp-4`) is the
default gap. `--page-gap` (16px) is page head to first content, everywhere.

Shape is one radius per kind of thing:

| Thing | Radius |
|---|---|
| buttons, tabs, tags, nav links | `--radius-pill` |
| inputs, selects, consoles, inline blocks | `--radius-md` (8px) |
| list rows, menu items | `--radius-lg` (10px) |
| cards | `--radius` (12px) |
| menus, windows | `--radius-xl` (14px) |
| modals, the login card | `--radius-2xl` (16px) |

Pills are for things you press or read as a word; squarer corners are for
things you type into or read in. A field must never look like a button.

Focus: `:focus-visible` draws a 2px accent ring; don't remove or restyle it.

## Components (`src/components/`)

Import from `./components/index.js`. Each renders the classes `styles.css`
defines, so a page that adopts one changes no geometry.

- **Button** `variant="primary" | "ghost" | "link" | "icon"`, `danger`.
  Primary is ink on paper (one per view: the thing you came to do). Ghost is
  the secondary: a hairline pill, filled on hover. `danger` on a primary is a
  red fill; on a ghost, red text and a red hairline. `link` is bare text.
  No button lifts or brightens on hover; hover is a background change.
- **Input / Checkbox / Select**: label above, control on `--field-bg` with a
  `--line-strong` border, accent on focus. Placeholders are sentence case and
  say what goes in ("Project name"), not a question.
- **Card** (`.card` / `.panel`): `--surface` on paper with a `--line` border.
  Cards group a set of controls or a list; they do not wrap one line of text.
  Don't nest cards.
- **PageHeader / Page**: a document page (`variant="doc"`) is a 960px column
  with a 24px title; a full-height page (`split` / `fill`) has a 60px header
  bar with an 18px title and the page's tabs on the right.
- **Tabs**: pills with no track; the current one is ink on a wash, the same
  idiom as the nav. Use for a page's sub-views only.
- **Tag / Badge**: a tag is a small pill on a wash for a quiet word
  ("in-guest"); `tone="pending" | "running" | "done" | "error" | "untrusted"`
  colours it by state token on a hairline. Badge is a count or a short ok word
  in the ok tint. One tag per row at most; a row with four tags has three
  words that belong in a column instead.
- **Toggle**: a switch; on is the accent. Label beside it, sentence case.
- **Menu / MenuItem / MenuSep**: a paper dropdown with a `--line-strong`
  border and `--shadow-pop`. No blur.
- **Modal**: paper, `--radius-2xl`, `--shadow-win`, head / body / foot. Full
  bleed on a phone.
- **EmptyState**: a slate line that says what would be here and how to get
  it ("No projects yet. Create one above."). Never a mood ("all clear ✓"),
  never a slogan.
- **Wordmark**: the pixel CNTRL, drawn as the site draws it. It appears in the
  nav and on the login and setup cards and nowhere else. It is the app's one
  flourish.

## Do and don't

Do
- One accent. Green is for links, focus, on, and the current nav glyph. State
  colours (`--ok` / `--warn` / `--bad`) are for state, by token.
- Say state in words next to the colour ("blocked", "waiting for you"), so
  it reads without colour.
- Keep the operator's copy plain and specific: sentence case, active voice,
  American spelling, the same verb on the button and in the toast.
- Use `--surface` for the one level of elevation. Paper, surface, done.
- Mono for data, sans for words, in the same line if that is what the line is
  ("`web_read` failed on `example.com`").

Don't
- No gradients, no glass outside the nav, no blur on menus, no hover lifts,
  no glow. No emoji or decorative glyphs in UI text; an SVG icon or a word.
- No tracked all-caps labels. No "A · B · C" meta strings; use a sentence or
  a list.
- No `color-mix` to invent a new colour for one place. If a tint is needed
  it is `color-mix(in srgb, var(--token) N%, transparent)` over a token.
- No slogans or personality copy in empty states or placeholders.
- No new radius, font size or spacing value. Climb the ladders.
- No second font for "display". Schibsted at 600 is the display face.

## Restyling a page (wave 2)

1. Screenshot it in both themes first (`/tmp`'s `shot.mjs` recipe: puppeteer-
   core against a local backend on :18000, `jarvis.theme` in localStorage).
2. Replace page-local colours, radii and sizes with tokens; replace hand-rolled
   buttons, tags, tabs and empty states with the components.
3. Cut: every tag, border and chip that says nothing; every uppercase label;
   every bit of copy that sells or jokes.
4. Delete the page's classes from the override list at the end of
   `styles.css` once they are rewritten.
5. Screenshot again. Both themes. Phone width (768px) too.
