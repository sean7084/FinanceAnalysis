---
kind: frontend_style
name: Dark Financial Dashboard — CSS Modules + Vite with Lightweight Charts & Recharts
category: frontend_style
scope:
    - '**'
source_files:
    - frontend/package.json
    - frontend/src/index.css
    - frontend/src/App.css
    - frontend/src/components/layout/AppShell.tsx
    - frontend/src/components/charts/CandlestickChart.tsx
---

## What system/approach is used

The frontend (`frontend/`) is a **React 19 + TypeScript + Vite** single-page application. Styling is done with **plain CSS files imported by the app**, not a component library or CSS-in-JS solution. The project uses:

- **Vite** as the build tool (no Webpack, no PostCSS config in repo).
- **Plain `.css` files** co-located at `src/index.css`, `src/App.css`, and any per-component styles if added later.
- **CSS custom properties** defined in `:root` of `index.css` for global tokens (font family, colors, background).
- **BEM-like class naming** (e.g. `app-shell`, `side-nav`, `nav-link`, `metric-card`, `data-table`, `chart-card`, `comparison-series-card`).
- **Responsive breakpoints** via `@media (max-width: ...)` rules inside the same CSS files.
- Two charting libraries provide visual styling: **lightweight-charts** (candlestick) and **recharts** (line/bar), both configured programmatically to match the dark theme rather than through external themes.

There is **no Tailwind, Bootstrap, Material UI, styled-components, Emotion, SCSS, or CSS modules** — just vanilla CSS consumed directly by React components via `className`.

## Key files and packages

- `frontend/package.json` — declares dependencies `react`, `react-router-dom`, `lightweight-charts`, `recharts`; dev deps include `vite`, `typescript`, `eslint`, `vitest`. No styling framework dependency.
- `frontend/src/index.css` — global design tokens, base layout (`app-shell` grid, `side-nav`, `content-area`), shared primitives (`card`, `chip`, `status-chip`, `data-table`, `metric-grid`, `toast-banner`), and responsive rules at `980px`.
- `frontend/src/App.css` — leftover Vite template styles (`hero`, `#center`, `#next-steps`, `ticks`) using CSS nesting; not part of the production dashboard shell.
- `frontend/src/components/layout/AppShell.tsx` — renders the two-column shell (`aside.side-nav` + `main.content-area`) and drives navigation links that toggle an `active` modifier class.
- `frontend/src/components/charts/CandlestickChart.tsx` — demonstrates inline chart theming via lightweight-charts options (`background`, `textColor`, `grid` line colors, `upColor`/`downColor`) matching the dark palette.
- `frontend/src/pages/*.tsx` — pages compose the shared classes from `index.css` (e.g. `card`, `metric-grid`, `data-table`, `table-pagination`) to build dashboards, backtest workbench, indicator board, etc.

## Architecture and conventions

1. **Global stylesheet-first approach.** All visual tokens live in `src/index.css` under `:root` and are referenced by class names throughout the app. Components do not define their own color palettes — they reuse `.card`, `.metric-card`, `.chip`, `.status`, `.data-table`, etc.
2. **Dark financial theme.** The root sets `color: #e8eef5`, `background-color: #07111f`, and body uses layered radial gradients (blue `#3f99ff` and green `#14c98a` glows). Cards use `rgba(14, 31, 51, 0.82)` backgrounds with `rgba(255,255,255,0.08)` borders. Accent states use blue/green gradients (e.g. active nav link `linear-gradient(130deg, rgba(37,174,255,0.35), rgba(33,204,133,0.3))`).
3. **Layout via CSS Grid/Flexbox.** The shell is a fixed `200px 1fr` grid on desktop, collapsing to a single column below `980px` where the sidebar moves above content and nav wraps horizontally.
4. **Component-level styling via shared classes.** Components like `CandlestickChart` wrap their host `<div>` in `className="card chart-card"`, relying on the shared `.card` base plus a modifier `.chart-card` for chart-specific spacing/margins. There is no per-component CSS file convention enforced yet.
5. **Charts themed in code.** Both lightweight-charts and recharts charts configure colors directly in props/options (e.g. `background: '#0f1a28'`, `upColor: '#22c76f'`, `downColor: '#ff5e5e'`) so chart visuals stay consistent with the dark palette without a separate theme file.
6. **No design-token abstraction layer.** Colors, radii, and spacings are repeated as literal values across the stylesheet (e.g. `border-radius: 14px`, `border-radius: 8px`, `border-radius: 999px`); there is no centralized token map beyond the top-level `:root` variables.

## Conventions and constraints

- **Styling language:** Plain CSS only — no preprocessors, no CSS-in-JS, no utility frameworks. Confirmed by the absence of any `tailwind.config.*`, `postcss.config.*`, `*.scss`, or styled-component imports in the frontend source tree.
- **Class naming:** BEM-style compound class names (`app-shell`, `side-nav`, `nav-link.active`, `metric-grid`, `data-table th`, `comparison-series-swatch`) are used consistently; modifiers are expressed as additional classes rather than nested selectors.
- **Theme enforcement:** Global colors are declared once in `:root` of `index.css`; all pages and components derive from these tokens rather than defining new palettes.
- **Responsive strategy:** Single breakpoint at `980px` inside `index.css` collapses the two-column shell into a stacked layout and adjusts navigation wrapping.
- **Chart styling rule:** Chart libraries are configured programmatically to match the dark theme rather than loaded with a prebuilt theme — see `CandlestickChart.tsx` options for background/grid/up/down colors.
- **Accessibility hints:** Focus-visible outlines are set via `&:focus-visible { outline: 2px solid var(--accent); }` in `App.css`, indicating a preference for visible focus indicators on interactive elements.