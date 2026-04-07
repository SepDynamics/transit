# Transit Sentinel 90-Day Plan

## Goal

Use the current repo surface to deepen public-data proof, harden the live lanes,
and turn the existing console plus calibration stack into a more deployable
product.

## Days 1-30: Data Lane Hardening

- keep MBTA archiving continuously
- validate LA Metro websocket archive quality under live load
- turn new archive windows into labeled case packs
- keep replay imports and the cross-city case-pack gate green

Exit criteria:

- both current agency lanes are producing usable live archives
- new real-data case packs are landing from current archive output
- live and replay views remain aligned in the API and dashboard

## Days 31-60: Reporting And Proof Surfaces

- expand scorecard/report outputs
- generate replay-based retrospectives
- package proof artifacts for demos and external reviews
- refine notification workflows around real incident traffic

Exit criteria:

- scorecards are useful beyond the dashboard
- replay runs can be turned into shareable summaries quickly
- notification routing is credible for operational demos

## Days 61-90: Product Hardening

- split the frontend into smaller maintainable components
- improve mobile and small-screen behavior
- decide on auth/RBAC requirements for any broader deployment
- choose the next public agency target only if the feed contract is concrete

Exit criteria:

- the console is easier to maintain and demo
- the repo has a clear path from public proof to production hardening
- any new agency scope is backed by a real implemented public-data lane
