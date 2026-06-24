# Lemma Frontend Design Digest

This digest documents the current product UI in this repository. It is based on the implemented Next.js app, Tailwind v4 token setup, shadcn/Radix primitives, pod workspace screens, records/tables, flows, assistants, desk runtime, dashboard, and landing surfaces.

## 1. Visual Theme & Atmosphere

Lemma currently reads as a warm operational workspace: calm, dense where work is dense, and intentionally low-drama. The app canvas is warm parchment (`--bg-canvas: #f5f4f0`), while primary work surfaces stay white (`--bg-surface` / `--surface-1: #ffffff`). The core visual language is "border-first": most surfaces are separated by warm grey borders and small ring shadows instead of heavy elevation.

Brand primary is indigo (`#6366f1` in light mode), the CTA matches (`#6366f1`), and the active accent is gold (`#d99a32`). Coral (`#df6a45`) appears as the attention/semantic signal.


**Key Characteristics**

- Warm parchment app canvas: `#f5f4f0`, not stark white.
- Indigo primary identity: `#6366f1` light mode, `#818cf8` dark mode.
- Gold accent: `#d99a32` for delight, progress, and active rails.
- Coral attention: `#df6a45` for human review and needs-response signals.
- Border-first containment with subtle ring shadows.
- Operational density in records/tables: sticky table headers, inline editable cells, side sheets, compact metadata.
- Compact radius in primitives: `2px`, `4px`, `6px`, `10px`, `12px`, `9999px`.
- Larger radius only in softer home/landing/template surfaces.
- Motion is restrained: fade, slide-up, breathing loader, ambient drift only where it serves atmosphere.

## 2. Color Palette & Roles

### Light Mode Core

| Role | Token | Value | Use |
| --- | --- | --- | --- |
| Canvas | `--bg-canvas` | `#f5f4f0` | App background, page base |
| Surface | `--bg-surface`, `--surface-1` | `#ffffff` | Cards, sheets, tables, popovers |
| Subtle Surface | `--bg-subtle`, `--surface-2` | `#f0efec` | Table hovers, inputs, secondary panels |
| Muted Surface | `--bg-muted`, `--surface-3` | `#e8e6e2` | Secondary buttons, selected rails |
| Brand Primary | `--brand-primary` | `#6366f1` | Logo bars, selected states, strong identity |
| Brand Secondary | `--brand-secondary` | `#6b6a66` | Supporting brand text and chart color |
| Brand Accent | `--brand-accent` | `#d99a32` | Delight, progress, active rails, highlights |
| Brand Warm | `--brand-warm` | `#d99a32` | Atmospheric warmth (aliases accent) |
| Brand Coral | `--brand-coral` | `#df6a45` | Attention, human-review emphasis |
| Brand Tan | `--brand-tan` | `#e7dbc9` | Accent button and brand badge fill |
| Focus Blue | `--focus-blue` | `#6366f1` | Accessible focus rings |
| CTA Background | `--cta-bg` | `#6366f1` | Primary buttons |
| CTA Foreground | `--cta-fg` | `#ffffff` | Primary button text |

### Text Scale

| Role | Token | Value | Use |
| --- | --- | --- | --- |
| Primary | `--text-primary` | `#141414` | Headings, active nav, important cells |
| Secondary | `--text-secondary` | `#6b6b6b` | Body text, table content, nav labels |
| Tertiary | `--text-tertiary` | `#9a9a9a` | Metadata, labels, helper copy |
| Soft | `--text-soft` | `#b0b0b0` | Placeholders, muted unavailable values |
| Inverse | `--text-inverse` | `#ffffff` | Text on dark fills |
| On Brand | `--text-on-brand` | `#ffffff` | Text on brand/CTA fills |

### Borders

| Role | Token | Value | Use |
| --- | --- | --- | --- |
| Subtle | `--border-subtle` | `#e5e5e5` | Default separators and quiet card borders |
| Default | `--border-default` | `#d4d4d4` | Inputs, active card boundaries |
| Strong | `--border-strong` | `#c0c0c0` | Hovered controls and emphasized boundaries |

### States

| Role | Token | Value | Use |
| --- | --- | --- | --- |
| Success | `--state-success` | `#16a34a` | Ready state, positive badges, booleans |
| Warning | `--state-warning` | `#d97706` | Unsaved, pending, date/time type badges |
| Error | `--state-error` | `#dc2626` | Destructive actions, errors, delete affordances |
| Info | `--state-info` | `#0891b2` | Info badges, focus support, flow/data marks |

### Dark Mode Core

Dark mode preserves the same structure with deep neutral surfaces:

- `--bg-canvas: #11120f`
- `--bg-surface: #1a1a1a`
- `--bg-subtle: #222222`
- `--bg-muted: #2a2a2a`
- `--text-primary: #ececec`
- `--text-secondary: #9e9e9e`
- `--text-tertiary: #6e6e6e`
- `--border-subtle: #2a2a2a`
- `--border-default: #3a3a3a`
- `--border-strong: #555555`
- `--cta-bg: #818cf8`
- `--cta-fg: #ffffff`

## 3. Typography Rules

### Implemented Font Families

- Product UI: `IBM Plex Sans`, via `--font-ibm-plex-sans`.
- Code/technical UI: `Source Code Pro`, via `--font-source-code-pro`.
- Landing serif: `Fraunces`, via `--font-landing-serif`.
- Landing sans: `Inter`, via `--font-landing-sans`.
- Landing mono: `IBM Plex Mono`, via `--font-landing-mono`.


### Product Type Scale

| Token | Size | Use |
| --- | --- | --- |
| `--text-xs` | 12px | Labels, metadata, chips |
| `--text-sm` | 13px | Dense UI, sidebar, table labels |
| `--text-base` | 16px | Body baseline |
| `--text-md` | 18px | Secondary headings |
| `--text-lg` | 20px | Card/dialog titles |
| `--text-xl` | 24px | Page headings |
| `--text-2xl` | 30px | Large page title |
| `--text-3xl` | 36px | Create/onboarding headings |
| `--text-4xl` | 44px | Marketing/product hero support |
| `--text-5xl` | 52px | Large display |
| `--text-6xl` | 64px | Max display |

### Leading & Tracking

- Tight: `--leading-tight: 1.2`
- Snug: `--leading-snug: 1.35`
- Normal: `--leading-normal: 1.5`
- Relaxed: `--leading-relaxed: 1.7`
- Tight tracking: `--tracking-tight: -0.02em`
- Snug tracking: `--tracking-snug: -0.01em`
- Normal tracking: `0`
- Label tracking: `0.04em`, `0.08em`, `0.16em`

### Typographic Behavior

- Product `h1` and `h2` use IBM Plex Sans, `font-weight: 700`, and `letter-spacing: -0.03em`.
- Product `h3` to `h6` use IBM Plex Sans, `font-weight: 700`, and `letter-spacing: -0.01em`.
- UI components often use `text-sm`, `font-medium` or `font-semibold`.
- Labels use uppercase at 10px to 12px with `0.08em` to `0.16em` tracking.
- Table cells use small type, truncation, and tabular numbers for numeric fields.
- Landing pages intentionally diverge: Fraunces light/italic display, Inter body, larger visual rhythm.

## 4. Spacing, Radius & Layout

### Spacing Scale

The global scale is tokenized:

- `--space-1`: 4px
- `--space-2`: 8px
- `--space-3`: 12px
- `--space-4`: 16px
- `--space-5`: 20px
- `--space-6`: 24px
- `--space-8`: 32px
- `--space-10`: 40px
- `--space-12`: 48px
- `--space-16`: 64px
- `--space-20`: 80px
- `--space-24`: 96px
- `--space-32`: 128px

### Radius Scale

| Token | Value | Use |
| --- | --- | --- |
| `--radius-sm` | 2px | Tiny marks, loader bars |
| `--radius-md` | 4px | Checkboxes, tiny pills, selected inner controls |
| `--radius-lg` | 6px | Buttons, inputs, rows |
| `--radius-xl` | 10px | Cards, panels |
| `--radius-2xl` | 12px | Assistant shells, larger panels |
| `--radius-full` | 9999px | Pills, avatars, circular buttons |

Actual components also use Tailwind utility radii:

- `rounded-md`: default buttons, inputs, tabs, nav items.
- `rounded-lg`: dialogs, popovers, table containers, cards.
- `rounded-xl`: table shells, builder rows, kanban columns.
- `rounded-2xl` / `rounded-[1.4rem]` / `rounded-[1.6rem]`: home, template, and softer dashboard cards.
- `rounded-full`: pills, profile buttons, selected floating action bar.

## 5. Depth & Elevation

Lemma uses a ring-first elevation model. Most surfaces are flat or nearly flat; high elevation is reserved for overlays, drawers, popovers, and selected-row action bars.

| Level | Token | Treatment | Use |
| --- | --- | --- | --- |
| Flat | none | Border only | Static cards, table rows, sidebars |
| Ring | `--shadow-xs` | `0 0 0 1px` subtle text mix | Table shells, active tabs, small icon blocks |
| Small | `--shadow-sm` | Ring plus 1-2px shadow | Cards, assistant strips, hover surfaces |
| Medium | `--shadow-md` | 10px blur, clipped | Interactive card hover, landing CTA |
| Large | `--shadow-lg` | 25px blur | Dialogs, popovers, sheets, selected-row bar |
| Extra Large | `--shadow-xl` | 32px blur | Highest priority overlays only |

Rule: if a surface is not floating, interactive, or selected, prefer border and background contrast before adding shadow.

## 6. Responsive & Mobile

The product must be usable on 375–430px phones. The shell adapts; complex builders degrade gracefully rather than pretending to work.

**Breakpoints.** Tailwind defaults: `sm` 640, `md` 768, `lg` 1024, `xl` 1280. `md` is the shell boundary: below it, inline sidebars are hidden and navigation moves into off-canvas drawers (`MobileSidebarDrawer` in the pod shell, the Sheet-based drawer in home/dashboard chrome). Feature CSS uses max-width 980/860/640 queries; new sections need a usable state at 375px.

**Rules for new UI**

- Never gate an action on hover alone. The global override in `styles/utilities.css` keeps `opacity-0 group-hover:opacity-100` reveals visible on touch; display/visibility-based reveals are flagged by the design audit (`hoverOnlyDisplayReveal`).
- Every `height: 100vh` needs a `100dvh` fallback on the next declaration (audit: `viewportHeightWithoutDvhFallback`). Tailwind `*-screen` utilities are already remapped to `dvh` globally.
- No unconditional fixed widths ≥360px; clamp with `max-w-*`, `min()`, or a breakpoint prefix (audit: `fixedWidthsWiderThanPhones`). Dialogs inherit `w-[calc(100vw-2rem)]` from `DialogContent` — don't override `w-` back to a fixed pixel value.
- Touch targets: shared `Button`, `Checkbox`, and `.lemma-shell-icon-button` get an invisible 44px hit-slop on coarse pointers via `.tap-target`. Add `tap-target` to new compact custom controls.
- Form controls must render at ≥16px on touch (global rule in `styles/base.css`) or iOS zooms on focus; don't undo it with smaller arbitrary font sizes on inputs.
- Tooltips never fire on touch — icon-only actions need an `aria-label` and should not hide essential information behind a tooltip.
- Pinch zoom stays enabled (`viewport` in `app/layout.tsx`); never reintroduce `maximumScale: 1` / `userScalable: false`.
- Canvas/editor surfaces (flows, Monaco) are view-only below `md`: hide palettes and config asides, disable drag/connect, show a "open on a larger screen to edit" notice.
- QA every new surface at 375px before shipping: no horizontal scroll on the page body, reachable primary actions, readable text.
