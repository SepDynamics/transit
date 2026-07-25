# Agency Onboarding Contract

This contract is completed before an agency feed is enabled in production. It
keeps Sentinel advisory: it consumes authorized data, preserves provenance, and
does not control CAD/AVL, dispatch, or signals.

## Required reliability lane

| Item | Acceptance condition |
|---|---|
| Agency owner | Named operational and data-governance contacts |
| Static GTFS | Routes/trips resolve and license permits retention |
| GTFS-RT vehicles | Freshness, coverage, route mapping, and outage behavior measured |
| GTFS-RT trip updates | Delay semantics and timestamp units verified |
| GTFS-RT alerts | JSON/protobuf/content encoding verified; malformed responses preserve prior good state |
| Route mapping | Agency route IDs, modes, direction conventions, and aliases reviewed |
| Case packs | Positive bus/rail/detour/alert windows plus quiet controls are labeled |
| Acceptance | Replay regression, public endpoint smoke checks, and operator review pass |

## Optional integration lanes

APC/fare, CAD/AVL, vehicle health, work orders, signal/controller, incident,
weather, event, accessibility, and demographic inputs require a separate data
addendum. Each addendum states purpose, owner, field-level classification,
retention, de-identification, access roles, audit requirements, and the pilot
success metric. Recommendations remain advisory until agency operating rules
explicitly authorize a deeper integration.

## LA Metro starter

`lametro` is an adapter, not an implied production claim. Swiftly credentials,
feed permission, archive retention approval, and labeled LA Metro case packs
are still required before it is enabled outside a local authorized pilot.
