# Mirror Status

## What Was Done

- `/sep/cluster-sentinel` was copied to `/sep/transit-sentinel`
- the copy was created first so the source repo stayed untouched
- the new repo is the only place where transit-facing changes should happen

## What Still Mirrors The Source

- the legacy cluster runtime still lives under `scripts/cluster`
- legacy cluster compatibility tests still live under `tests/cluster`
- many runtime contracts are still GPU/cluster-shaped
- some non-transit evaluation and policy paths are still GPU/cluster-shaped

## What Was Extracted

- transit runtime modules now live under `scripts/transit`
- transit tests now live under `tests/transit`
- frontend config and class names are transit-native
- transit is the default CI and local engineering lane
- the old `scripts/cluster/transit_*` compatibility wrappers are gone

## What Was Reframed

- top-level README
- product docs under `docs/`
- frontend metadata, transit API surface, and app-facing descriptions
- transit runtime now writes to a dedicated Valkey-backed store instead of serving files directly from the legacy cluster API

## Safe Next Step

Focus product work on:

1. replayable MBTA case packs
2. transit policy tuning against labeled cases
3. baseline comparison against transit heuristics
4. removal of the remaining legacy cluster runtime once compatibility is no longer needed

That keeps the repo pointed at proof-of-value instead of more scaffold churn.
