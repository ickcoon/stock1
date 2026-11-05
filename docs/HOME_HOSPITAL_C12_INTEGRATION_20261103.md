# C12 / KRX home integration ? 2026-11-03

## Checkout and preservation

Original workspace: C:/Users/dada1/Downloads/stock1; branch feat/c14-chart-integrated-20261101; HEAD ac0e8655db12f8b9d6b03129a1fff44032c02ccc.
Untracked .venv-old and four docs were left untouched. The IDE checkout at C:/Users/Public/Documents/ESTsoft/CreatorTemp/stock1-permanent-dev/night-20261101/integration is a different checkout at eb321b1 with untracked docs/HANDOFF_20261101_NIGHT.md; it was not edited.

Integration checkout: C:/Users/Public/Documents/ESTsoft/CreatorTemp/stock1-c12-home-20261103.
Branch: integration/home-hospital-c12-20261103. HEAD remains ac0e865; changes are uncommitted and unstaged.
Remote: https://github.com/ickcoon/stock1.git. No commit, merge or push was performed.

## Comparison and integration

Fetched main and backup/hospital-c12-20261103. Main and common ancestor: 5b2080153ddea22117f92b6bd0a807a631faaf5e.
The supplied 87a58e1 object was absent; the actual backup tip is 87a5be1. Hospital tip differs from main by 69 files, 7,575 insertions and 37 deletions. Its final commit alone changes 26 files, 2,915 insertions and 4 deletions. Home and hospital share parent 4f0c2c2; each has one additional commit.

Imported the hospital commit's 23 new source/script/fixture/test files. Applied only its incremental changes to am_briefing.py and am_momentum.py. Added the two hospital credential ignore patterns. Preserved home format_mobile, replay, export, morning briefing, workflow, Telegram, input adapter and dependency changes. Existing C14/weekly modules already match the shared history and were not replaced.

Potential overlap: am_briefing.py is edited on both branches; additive delta applied cleanly. Comparing whole branch tips would also expose home-only adapter/workflow/Telegram/dependency work to replacement. No whole-tree replacement was used.

Fixed imported repository-root calculations in krx_openapi.py, theme_research.py and scripts/c12aj_checked_research.py, scripts/c12ak_checked_research.py from parents[3] to parents[1]. Added tests/test_c12_home_paths.py. Made two test_theme_research functions create their own prerequisite directory.

Full changed-file inventory: diagnostics/home-integration/changed-files.txt (ignored local artifact).

## Actual DB verification

Source: C:/Users/dada1/Downloads/stock1/data/reference/ARVIS_Theme_Master.sqlite (671,744 bytes). No copy into Git and no source edits.
Full SQLite integrity_check and quick_check: ok; foreign key violations: none; C12-AL schema adapter load succeeded.
Master counts: themes 85, stock names 556, relations 831; logical orphan relations 0.
SHA256 before and after reads: ac9e891a15d30f38cf88a4f915511e44d9ef3cdce208fd0c002c27b7996d04d3.
Original names, observations and relationships are retained. Detailed schema and reconciliation: diagnostics/home-integration/theme-db-verification.json.
Path configuration is explicit via existing --db argument; local paths are recorded in diagnostics/home-integration/home-config.json.
The full 47-stock AK analysis was not rerun: hospital AK/observation report inputs are not available in this checkout. The reported historical baseline remains 24/47 (51.06%), not a newly verified result.

## KRX and C12-AO

No process KRX_AUTH_KEY and no original .env.krx.local were found. Created an ignored, empty .env.krx.local in this integration checkout only. Enter the key locally after KRX_AUTH_KEY=; never paste it into chat. git check-ignore confirms exclusion.
Preflight found key_present=false and actual_api_calls=0. All seven services remain NOT EXECUTED: stk_isu_base_info, ksq_isu_base_info, stk_bydd_trd, ksq_bydd_trd, krx_dd_trd, kospi_dd_trd, kosdaq_dd_trd. Portal approval is independently unverified. HTTP status, real rows/fields, units, product classifications and real identifier contracts remain unverified.
Before live calls, verify current official specifications and service approval, then supply the existing --review contract evidence, --bas-dd and --output-dir diagnostics/krx-an/<run>. Existing fixture approval must not authorize live requests. No TLS verification bypasses.

C12-AO preparation: diagnostics/home-integration/c12ao-pending.json contains 556 unresolved raw names and six intended matching states. Official KRX master is unavailable; confirmed new code mappings remain 0. This is a preparation queue, not a completed matching implementation. Ambiguous names are not assigned codes.

## Next starting point

Work from this integration checkout and review its uncommitted diff. Use C:/Users/dada1/Downloads/stock1/.venv-home/Scripts/python.exe (system Python lacks requests; no dependency installation performed).
Enter the local KRX key and review official seven-service contracts/approval. Acquire KOSPI/KOSDAQ official masters, retain six-character codes including leading zeros and any official alphanumeric codes, then implement/reuse exact full-name, short-name and evidence-backed alias matching with ambiguity and historical listing states. Recompute the 47-stock coverage only when actual observation inputs and verified matches are available.
Operational DB, Collector settings, schedules, OAuth/token caches, Telegram settings and D0 snapshots were not changed. AP/AQ/AR and ADR20 are outside this preparation change.

## Validation results

Targeted offline integration suite: 148 tests, 0 failures, 0 errors, 0 skips, including all 17 KRX synthetic tests, imported function-style audit tests, original home briefing/replay/export tests, and 2 checkout-path regressions. Initial targeted run exposed test directory setup dependence; fixed and rerun passed. System Python initially failed to import requests; existing home venv resolved this without installing packages. Logs: diagnostics/home-integration/targeted-tests.txt.

Source reconciliation found one edge-whitespace variant (a leading space in an original observation); all 831 name/theme pairs agree after edge-whitespace comparison. Raw values were preserved.

Full unittest discovery (initial home venv): 1,364 tests, 8 failures and 15 errors; NOT a clean regression pass. It does not collect function-style tests; those were included separately in the 148-test targeted suite.
Failure groups: missing Pillow caused chart/dashboard errors, missing Windows trust dependency and an order-sensitive weekly token test, Windows protected-cache errors, and one full-suite workflow turnover assertion (0 versus 8). A focused recheck with the existing .venv (which contains Pillow and truststore) resolved chart/dashboard/workflow/TLS failures in that focused invocation; see chart-workflow-recheck.txt. This does not establish a clean full-suite result or resolve potential order dependencies.
Protected token tests rerun through the normal unittest entrypoint still had 2 failures and 5 errors out of 26, including cache_write_failed and failed child-process token result. These modules are byte-for-byte unchanged from the home HEAD; no protection or token-cache logic was relaxed. Investigate in the normal Windows user context before any release. Logs: regression.txt, token-recheck.txt, chart-workflow-recheck.txt in diagnostics/home-integration.
