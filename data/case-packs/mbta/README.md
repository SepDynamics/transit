# MBTA Case Packs

Committed MBTA proof packs currently include:

## Positive incident packs

- [daytime_red_line_delay_spike](daytime_red_line_delay_spike/README.md) — Red Line southbound midday delay spike from real MBTA trip updates (April 3, 2026)
- [green_line_c_sunday_disruption](green_line_c_sunday_disruption/README.md) — Green Line C Sunday evening disruption with 3 high-impact alerts and active vehicles (June 8, 2026)
- [lowell_fitchburg_commuter_rail_delays](lowell_fitchburg_commuter_rail_delays/README.md) — Fitchburg and Lowell Line alert-based disruptions detected by live triage (June 8, 2026)
- [bus_route_101_sunday_disruption](bus_route_101_sunday_disruption/README.md) — Route 101 bus disruption with active advisories during Sunday evening (June 8, 2026)

## Quiet control packs

- [overnight_advisory_controls](overnight_advisory_controls/README.md) — Overnight accessibility, planned service, and stop-change advisories (April 4, 2026)
- [sunday_evening_quiet_control](sunday_evening_quiet_control/README.md) — Sunday evening low-service routes with minimal alerts and no delay burden (June 8, 2026)

## Running case packs

Run the full committed MBTA suite across all packs:

```bash
PYTHONPATH=. python3 scripts/transit/grade_calibration.py \
  --archive-root data/case-packs/mbta \
  --labels data/case-packs/mbta \
  --strict
