# Transit Sentinel Meeting One-Sheet

Use this when opening `https://sepdynamics.co/` for a non-technical investor,
partner, or agency conversation.

## The Simple Point

Transit Sentinel watches the live MBTA public data feeds and turns them into a
ranked service-status surface: what looks unstable, why it was ranked, and what
evidence supports it.

The cleanest one-line explanation:

> MBTA alerts tell you what the agency has posted. Transit Sentinel combines
> alerts, vehicle positions, trip updates, and route data to show what deserves
> attention now.

## What You Are Looking At

- **Top status banner**: the network-level readout. It says whether the live
  MBTA system looks normal, advisory-level, delayed, or disrupted, plus a
  live feed freshness countdown.
- **Live vehicle map**: click "Show map" to see 300+ MBTA vehicles in
  real-time, color-coded by health (green=good, red=disruption). Click any
  vehicle for delay, route, and risk details.
- **What Needs Attention**: the ranked triage queue of elevated routes, with
  evidence chips (delay, alerts, risk score) and recommended actions.
- **Service Reliability**: a scorecard showing network-wide on-time percentage,
  stable vs. unstable routes, and average delay. Expand worst-performing routes.
- **Source & Feed Quality**: whether the current MBTA feed sample is fresh
  enough and has route, vehicle, trip-update, and alert coverage.
- **Priority alerts**: plain-language explanation of the route conditions
  Sentinel thinks are worth calling out.
- **Route status**: per-route cards grouped by rapid transit, bus, commuter
  rail, ferry, and other routes. Search a route like `Red Line`, `66`, or
  `Green-B`.

## What "Source" Means

The old raw feed string looked like:

```text
gtfs_rt_alerts+gtfs_rt_trip_updates+gtfs_rt_vehicle_positions
```

Translate that as:

- **Alerts**: official MBTA service-alert feed, including detours, stop moves,
  disruptions, accessibility notices, and planned service notices.
- **Trip updates**: prediction and delay signals for trips currently running or
  scheduled soon.
- **Vehicle positions**: live vehicle locations and route assignments.
- **Static GTFS route data**: schedule, route, stop, and direction metadata used
  to make the realtime data understandable.

Plain-English phrasing:

> The source is MBTA's public realtime feed: alerts, trip updates, and vehicle
> positions, anchored to the MBTA schedule and route map.

## Simple Comparison: MBTA Bus Alerts vs. Transit Sentinel

| Question | MBTA bus alerts | Transit Sentinel |
| --- | --- | --- |
| What is it? | Official public alert board for bus service notices. | Intelligence layer built on MBTA public feeds. |
| Main job | Publish rider-facing notices such as detours, stop moves, cancellations, and planned changes. | Rank route risk and explain why a route deserves attention. |
| Data used | Primarily agency-published alerts. | Alerts, trip updates, vehicle positions, route metadata, feed freshness, and rolling history. |
| View of the system | A list of notices, usually organized by mode and route. | Network banner, route status cards, priority alerts, and scorecard. |
| Best for | Riders checking whether MBTA has posted a known alert. | Operators, public-information teams, and partners asking what is unstable now. |
| Key limitation | It may not prioritize across the whole system or explain corroborating telemetry. | It cannot see private dispatch, crew, signal, or supervisor systems unless those are integrated later. |

Meeting-safe shorthand:

> MBTA alerts are the bulletin board. Transit Sentinel is the triage desk.

## Thirty-Second Talk Track

Transit Sentinel is not trying to replace MBTA.com. It uses the same public
standards agencies already publish, then adds the missing layer: ranking,
evidence, and memory. The live site proves this can run against a complex real
network before asking an agency for private integrations. Today it is Boston
only. The next commercial step is to turn this into a pilot offer around status
monitoring, operations triage, feed quality, or proof reports.

## Demo Path

1. Open `https://sepdynamics.co/`.
2. Point to the top banner: "This is the current network readout — severity,
   route count, disruption count, and feed freshness."
3. Click **Show map**: "These are live MBTA vehicle positions, color-coded by
   service health. Green is normal, red is disruption. Click any vehicle for
   delay and route details."
4. Point to **What Needs Attention**: "This is the ranked triage queue —
   Transit Sentinel prioritizes what needs a human to look at it first."
5. Point to **Service Reliability**: "A live scorecard of network health:
   on-time percentage, stable vs. unstable routes, average delay."
6. Point to **Source & Feed Quality**: "This tells us whether the input data
   is fresh and usable before we trust the status."
7. Search for a recognizable route, such as `Red Line`, `66`, or `Green-B`.
8. Open `https://sepdynamics.co/api/status/network` to show the same live
   state is available as a public JSON API.
9. Open `https://sepdynamics.co/api/status/feed-quality`,
   `https://sepdynamics.co/api/status/triage`, and
   `https://sepdynamics.co/api/status/map` to show the API products.
10. Explain that the private operations API exists behind auth at
    `/api/transit/*`, but the public site intentionally uses only `/api/status/*`.

## What Not To Overclaim

- Do not say this replaces dispatch.
- Do not claim non-Boston coverage yet.
- Do not imply public feeds reveal internal causes like crew availability,
  dispatch decisions, signal-system state, or supervisor actions.
- Do not present zero measured delay as proof that everything is healthy; it
  means the current sample did not contain a delay burden.

## Best Next Ask

The strongest ask is a focused pilot conversation:

> Which wedge is most valuable: live status/API monitoring, operations triage,
> feed-quality assurance, or reliability proof reports?
