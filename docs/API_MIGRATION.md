# API Migration

Transit Sentinel can move toward a FastAPI implementation, but the live MBTA
stack should not use a big-bang API rewrite. The migration gate is parity first:
prove the current handler and any candidate sidecar serve the same frontend and
public contracts before routing traffic.

## Parity Harness

`scripts/transit/api_parity.py` captures and compares API behavior for:

- public health and `/api/status/*`
- frontend-consumed `/api/transit/*` GET endpoints
- optional `/api/transit/history` when an entity id is supplied
- optional admin audit reads when an admin token is supplied

The harness compares:

- status codes
- JSON shape
- ETag presence
- conditional `If-None-Match` behavior
- exact body hashes and exact ETags only when explicitly requested

Exact body comparison is off by default because live MBTA payloads include
timestamps and can change between two requests.

Capture the current API:

```bash
make transit-api-parity ARGS="capture --base-url http://127.0.0.1:8000 --output-dir output/api-parity/current"
```

Capture protected operations endpoints with a read token:

```bash
TRANSIT_API_PARITY_BEARER_TOKEN=readonly-token \
make transit-api-parity ARGS="capture --base-url http://127.0.0.1:8000 --output-dir output/api-parity/current"
```

Compare the current API to a candidate sidecar:

```bash
TRANSIT_API_PARITY_BEARER_TOKEN=readonly-token \
make transit-api-parity ARGS="compare --baseline-url http://127.0.0.1:8000 --candidate-url http://127.0.0.1:8081"
```

Verify a running API against captured fixtures:

```bash
make transit-api-parity ARGS="verify-fixtures --base-url http://127.0.0.1:8000 --fixture-dir output/api-parity/current"
```

## FastAPI Sidecar Rules

Before adding routes under `/api/v2` or changing nginx/Caddy routing:

1. Keep the existing `scripts/transit/api.py` service as the production API.
2. Run the candidate FastAPI app on a separate internal port.
3. Share Valkey read-model inputs with the existing API; do not introduce a new
   write path during parity work.
4. Run `api_parity.py compare` against both implementations.
5. Require zero status, shape, ETag, and conditional GET diffs for public status
   and frontend-consumed operations endpoints.
6. Only then consider routing a narrow `/api/v2/*` path to the sidecar.

JWT and Server-Sent Events should wait until this parity gate exists for the
candidate routes they would replace.
