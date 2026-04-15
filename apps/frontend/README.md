# Transit Sentinel Frontend

React and Vite frontend for the Boston-focused Transit Sentinel API.

## Surfaces

- public MBTA status page backed by `/api/status/*`
- protected operations console backed by `/api/transit/dashboard`
- lazy-loaded map view backed by `/api/transit/map`
- corridor and vehicle drilldowns backed by `/api/transit/history`
- scorecards backed by `/api/transit/scorecard`

The current copy should stay technical and operational. Avoid campaign-specific
or external-audience copy in the product UI.

## Polling Budget

The frontend intentionally avoids 5-second polling for every endpoint:

- sources: 30 seconds
- main dashboard: 10 seconds
- scorecard: 30 seconds, 60-sample limit
- map: 30 seconds
- selected history: 30 seconds, 36-sample limit
- public status page: 30 seconds

If a panel needs fresher data, prefer adding it to the dashboard payload or a
specific lightweight endpoint before increasing global polling frequency.

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

The production container serves static assets through nginx and proxies API
requests to the backend service. `npm run dev` is not a hosted runtime.

## Runtime Config

The app reads runtime config from `public/transit-sentinel-config.js` and
defaults to same-origin API requests unless `API_URL` or `VITE_API_HOST` is
set.

Set `API_BEARER_TOKEN` in the frontend container runtime config only for
trusted deployments where the ops API requires bearer auth. Public
`/api/status/*` requests do not need a token. The live public deployment uses
`OPS_CONSOLE_ENABLED=0` and no browser-visible bearer token.

The bundled API schema lives at `public/static/transit.openapi.yaml`.
