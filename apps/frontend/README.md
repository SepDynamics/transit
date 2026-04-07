# Transit Sentinel Frontend

React and Vite operations console for the Transit Sentinel API.

## Current Features

- network overview metrics
- corridor overview and trend watch
- incident feed
- vehicle inventory and drilldown
- replay scope and trace selection
- map view backed by `/api/transit/map`
- network and corridor scorecards backed by `/api/transit/scorecard`

## Local Development

```bash
cd apps/frontend
npm install
npm run typecheck
npm run dev
```

Production build:

```bash
npm run build
```

## Runtime Config

The app reads runtime config from `public/transit-sentinel-config.js` and
defaults to same-origin API requests unless `API_URL` or `VITE_API_HOST` is
set.

The bundled API contract lives at `public/static/transit.openapi.yaml`.
The expected backend runtime is `scripts/transit/api.py`.
