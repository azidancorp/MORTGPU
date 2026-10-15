# MORTGPU Docs — Theme & Style

Dark charcoal theme with red/green highlights, hard corners, and the Saira
typeface. Everything in this folder should follow these rules.

## Palette

| Token | Hex | Use |
|---|---|---|
| `--bg-base` | `#161616` | Page background |
| `--bg-surface` | `#1d1d1d` | Cards, sections, lists |
| `--bg-raised` | `#242424` | Table headers, elevated surfaces |
| `--bg-inset` | `#101010` | Code blocks, inputs |
| `--text-primary` | `#e6e6e6` | Body text |
| `--text-secondary` | `#b0b0b0` | Secondary text |
| `--text-muted` | `#7d7d7d` | Captions, footnotes |
| `--red` | `#ff4136` | Primary highlight — headings, errors, danger |
| `--red-bright` | `#ff6157` | Hover/active red |
| `--green` | `#34d973` | Secondary highlight — links, success, H2s |
| `--green-bright` | `#55ec8f` | Hover/active green |
| `--border` | `#2e2e2e` | Default borders |
| `--border-strong` | `#454545` | Emphasized borders |

## Gradients

Layered, AQL-style — glow first, tinted charcoal beneath, never a flat fill
where a surface can carry depth:

| Token | Value | Use |
|---|---|---|
| `--grad-page` | red radial glow (top-right) + green radial glow (bottom-left) over a `#1c0d0c → #161616 → #0c1710` diagonal | `body` background |
| `--grad-surface` | `#1d1d1d → #161616` vertical | Cards, headings, lists, blockquotes |
| `--grad-raised` | `#242424 → #1d1d1d` vertical | Table headers |
| `--grad-inset` | `#141414 → #101010` vertical | Code blocks, inputs |
| `--grad-red` | `#ff6157 → #ff4136 → #d92b21` vertical | Red buttons |
| `--grad-red-soft` | translucent red → `#33110f` left-to-right | Red callouts, H1 sheen |
| `--grad-green` | `#55ec8f → #34d973 → #23b357` vertical | Green buttons |
| `--grad-green-soft` | translucent green → `#0f2416` left-to-right | Green callouts, H2 sheen |
| `--grad-rim` | conic transparent → red → green → transparent | `.rim` top edges |
| `--grad-scroll` | red → green vertical | Scrollbar thumb |

## Rules

1. **Hard corners everywhere.** `border-radius: 0` — no rounded elements, ever.
2. **Charcoal backgrounds for everything.** No element may show a default
   browser background; every surface uses one of the four `--bg-*` tokens or
   a `--grad-*` built from them.
3. **Red and green are the only accent colors.** Red = attention, errors,
   primary buttons, H1 accents. Green = links, success, focus states, H2 accents.
4. **Moderately high contrast.** Text never goes darker than `--text-muted`
   on charcoal, and pure black/white are avoided.
5. **Saira for all UI and prose.** Falls back to `Segoe UI`/system sans.
   Monospace (code) uses JetBrains Mono or Fira Code.
6. **Left-border accents** (4px, red or green) mark headings, quotes, code
   blocks, and callouts.
7. **Gradients are layered, never loud.** A surface gradient stacks a tinted
   glow or sheen on top of a charcoal base; accent gradients (buttons,
   scrollbar, rims) are the only places red and green blend into each other.

## Usage

Link the theme in any docs page:

```html
<link rel="stylesheet" href="theme.css">
```

Or import it into another stylesheet:

```css
@import url('theme.css');
```

Use the utility classes for inline emphasis: `.text-red`, `.text-green`,
`.bg-base`, `.bg-surface`, `.bg-raised`, and callout variants
`.callout.red` / `.callout.green`. Gradient utilities: `.border-grad`
(charcoal fill, red→green border) and `.rim` (conic red→green top edge).
