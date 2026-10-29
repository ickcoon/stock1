# Step 7-C5-A — AM_MOMENTUM portable review

This is a fixture-only, offline analysis module. It never opens SQLite, calls Kiwoom, imports credentials, writes D0 events, or changes operational dashboards/schedulers. Synthetic mock and synthetic real scopes are supported separately. Neither is evidence of live market performance.

## Run in stock1-dev

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.am_momentum --fixture tests/fixtures/am_momentum_synthetic.json --name am-synthetic-review
```

Creates `reports/am-synthetic-review.json` and `.html` from one analysis object. `/reports/` is Git ignored. Only a new basename is accepted; custom output roots, traversal, symlinked report directories and overwriting existing artifacts are rejected. No upload or publish feature exists. Do not copy the operating `.env`, credentials, database or authentication caches into the development project.

## Input and point-in-time contract

`am-momentum-fixture-v1` requires `source_kind=fixture`, trade_date, environment (mock/real), and market (KRX/NXT/SOR; mock requires KRX). Default cutoff is 09:50:00 KST on trade_date; explicit aware AM cutoffs from 09:20 through 09:50 are supported. Generation cannot precede cutoff.

Ranking snapshots require api_id=ka00198, qry_tp=1, status=success, explicit slot, observed_at and received_at, plus stocks with stk_cd/rank. Every source, sector tag and numerical observation repeats environment, market and source_kind. Mixed scopes are rejected. Codes use the existing quotes.CODE contract, including alphanumeric codes. Existing bubble_core.unique_stocks and complete supply stock normalization and TOP20 completeness; duplicate codes/ranks or duplicate accepted slots are rejected rather than increasing counts.

Both observation and receipt must fall on trade_date, with observed_at <= received_at <= cutoff. Rankings must actually be observed within their scheduled minute. A 09:50 response received at 09:50:12 is unavailable to a strict 09:50:00 decision. Missing slot information is not reconstructed. Rejected snapshots are recorded by input index and static reason, never arbitrary source identifiers. Future numerical/tag observations are excluded. Tag basis must also have been received before the particular coappearance it supports.

Optional securities entries contain `sectors` (name, basis, observation/receipt timestamps) and lists for price, turnover, market_cap and liquidity. Numerical entries require decimal string value, unit=KRW, verified=true, basis and observation/receipt timestamps. The verified flag is supplied synthetic evidence, not independent validation by this module. An eventual live adapter must validate unit/sign semantics, cumulative turnover scope, timestamp and verification provenance before setting it. Unverified/wrong-unit values cannot support momentum. Same-instant duplicate metrics are rejected as ambiguous revisions. No price reconstruction, guessed missing values or unverified unit conversions occur.

## Exploratory classification, not a validated strategy

- attention_leader: at least two TOP5 appearances and at least two TOP20 appearances, with TOP5 presence in the latest accepted slot. Missing slots remain disclosed.
- momentum_candidate: attention_leader plus all expected TOP20 snapshots complete, increasing verified price and cumulative turnover across distinct observed instants, point-in-time sector coappearance, verified market cap >= 300 billion KRW, and positive intraday liquidity with explicit verified basis.
- insufficient_evidence: attention threshold is not met. Missing market evidence can coexist with attention_leader; classifications never hide missing_evidence.

These thresholds are initial review heuristics. No closing cumulative turnover 100 billion KRW requirement is applied in the morning. Positive liquidity is a minimal evidence gate, not a validated execution-size capacity test. Changes/rates compare first and last usable observations and are not a continuous-time or full-market measurement. Rank change is first observed rank minus last observed rank; positive means improvement. Absence stays null, never rank 21. TOP20 observed ratio uses accepted snapshots as denominator; missing slots and incomplete TOP20 coverage are separate.

Sector names are deduplicated per stock, peers per sector and slot are unique codes, and tags never multiply stock rank metrics. No leader/follower causal relationship is inferred from query ranks.

## Portable output

`am-momentum-review-v1` includes trade_date, cutoff, generated_at, source scope, expected/accepted/missing slots, completeness, diagnostics, policy and per-stock rank_history, counts, observed ratio, rank_change, timed sector basis/companions, verified numerical observations with units, deltas and rates, reasons and missing_evidence. evaluation_reference hashes the canonical analysis before adding the hash itself. No arbitrary input metadata, raw API responses or file paths are copied.

HTML has an overview, stock classifications, observed changes, missing evidence, complete analysis details, and an embedded `application/json` payload identical to the JSON file. All labels are validated and escaped; sensitive keys, authentication values and path-like values are rejected. CLI failures use generic messages without echoing input or machine paths. Keep only synthetic, non-sensitive fixture values; arbitrary secrets in free text cannot be reliably identified by pattern matching.

Another PC/AI can recompute counts, observed rank movement, same-sector peer counts, price/turnover deltas/rates and classification from the report without a hospital DB. It cannot recover excluded future/rejected numerical values, unavailable slots, unobserved ticks or original raw API bodies. Re-evaluation under a later cutoff needs separately approved source exports and an adapter, not a retroactive D0 selection.

## C4 and operational boundary

candidate_confirmed=false and d0_storage_allowed=false at report and stock level, for every classification. C4 d0_store contracts, final review and closing turnover checks are unchanged; no call to it exists. No D0 snapshot is created, rewritten or promoted. Only AM is implemented here; PM models are out of scope.

Before live connection: approve a read-only export separately; preserve actual request/receipt/market times, environment and source kind; retain sector classification availability times; verify price signs, turnover units/scope, capital basis and intraday liquidity; define freshness/candidate tracking coverage. Missing verified facts must remain missing. The fixture-only module deliberately rejects live_test source data.

## Validation

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_am_momentum.py
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

Tests use synthetic fixtures and temporary report roots and cover future receipt/observation exclusion, counts, rank changes, deduplicated sector evidence, missing and unverified metrics, mixed scopes, immutable input, no DB/API/D0 calls, exclusive output paths, privacy rejection and exact HTML/JSON equivalence.

2026-10-29 KST validation: 24 new AM tests passed; final full regression 781 passed in 138.233 seconds, failures/errors/skips 0. The test process blocked operating-project access and external network connections. The final synthetic HTML was checked in a local offline browser at desktop and mobile widths; its embedded data exactly matched the JSON artifact.
