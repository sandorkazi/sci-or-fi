# sci-or-fi

Interactive Sci vs Fi charts, published via [GitHub Pages](https://pages.github.com/).

Live site: `https://<user>.github.io/<repo>/`

## Structure

- `index.html` — landing page, links into `output/`
- `output/` — standalone `.html` visualizations (each is a self-contained page)
  - `output/index.html` — index of all outputs
  - `output/star-trek.html` — Star Trek: Sci vs Fi by season
- `.nojekyll` — disables Jekyll processing so Pages serves files as-is

## Add a new chart

1. Drop the new file in `output/`, e.g. `output/dune.html`
2. Add a link in `output/index.html` and in root `index.html`
3. Commit and push — the Pages workflow redeploys automatically

## Enable GitHub Pages (one-time)

1. Push this repo to GitHub on the `main` branch
2. Go to Settings → Pages → Build and deployment → Source: **GitHub Actions**
3. Push to `main`; the `Deploy static content to Pages` workflow publishes the repo root, so:
   - `/` → `index.html`
   - `/output/star-trek.html` → the chart
