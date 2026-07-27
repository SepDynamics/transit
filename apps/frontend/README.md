# Transit Sentinel Frontend

React and Vite frontend for the LA Metro Transit Sentinel API.

## Surfaces

- public LA Metro status page backed by `/api/status/*`
- protected operations console backed by `/api/transit/dashboard`
- lazy-loaded map view backed by `/api/transit/map`
- corridor and vehicle drilldowns backed by `/api/transit/history`
- scorecards backed by `/api/transit/scorecard`
- operator-only alternative-service preview backed by
  `/api/transit/alternative-advisories/options` and
  `/api/transit/alternative-advisories`

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
VITE_OPS_CONSOLE_ENABLED=1 npm run dev
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

`API_BEARER_TOKEN` is legacy browser-side configuration and must never contain
an operator or admin credential. The generated runtime config is public
JavaScript, so every browser user can read it. An Operations deployment should
use a same-origin authenticated proxy or BFF that injects the backend operator
credential server-side after stripping client-supplied authorization. The live
public deployment uses `OPS_CONSOLE_ENABLED=0` and an empty
`API_BEARER_TOKEN`; `/api/status/*` requests do not need a token.
Operations is disabled when runtime configuration is missing and must be
enabled explicitly only on its protected host.

The alternative-service preview intentionally bypasses browser token config
and only works through that protected same-origin deployment. See
`../../docs/ADVISORY_OPERATOR_PREVIEW.md` for the trust boundary and endpoint
contract.

The bundled API schema lives at `public/static/transit.openapi.yaml`.
