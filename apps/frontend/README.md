# Transit Sentinel Frontend

Dashboard scaffold for the Transit Sentinel API.

Current state:

- transit endpoints are wired into the console
- runtime config is transit-native
- internal component and class names are transit-native

Target features:

- network overview
- line and corridor health
- terminal stress
- incident feed
- replay mode
- evidence and provenance drilldown

## Local Development

```bash
cd apps/frontend
npm install
npm run typecheck
npm run dev
```

The app reads runtime config from `transit-sentinel-config.js` and defaults to same-origin API requests unless `API_URL` or `VITE_API_HOST` is set.

The bundled API contract lives at `public/static/transit.openapi.yaml`.
The expected backend runtime is `scripts/transit/api.py`.
