# PR #3 CI failure analysis and minimal fixture fixes

## Baseline and environment

PR head before this fix: `6571ccc0e51f81502ff7e09bcb0894c91cd0adc0`; main: `7d200cff1b29fb18a8982f9f571c0c9624e0fcf1`. The original [PR run](https://github.com/ickcoon/stock1/actions/runs/41254746682) executed 1,150 tests in 295.279 seconds: 3 failures, 12 errors.

The previous [main run](https://github.com/ickcoon/stock1/actions/runs/41204439341) already had the same 12 errors, protected-cache multiprocessing failure and voice artifact-path failure. It had three browser assertion failures instead of the PR's single dashboard subtest failure: 1,128 tests, 5 failures, 12 errors. C14-A's 22 tests were added by the PR and were not among the failures. This is evidence of pre-existing fixture/readiness problems, not proof that intermittent browser outcomes are identical.

Both logs report Windows Server 2025 (10.0.26100), Python 3.11.9, and workspace `D:\a\stock1\stock1`, using the same Windows test workflow. Their runner image versions differ: main `20261021.278.1`, PR `20261030.294.1`. A fresh main workflow was dispatched without changing main to compare on the current hosted runner: [baseline run](https://github.com/ickcoon/stock1/actions/runs/41257277516).

Original logs include Python installation environment variables but do not record TEMP/TMP or their canonical paths. Do not claim a particular original TEMP alias from those logs alone. The workflow now prints only OS/Python/cwd/temp-path metadata and the allowlisted TEMP, TMP, RUNNER_TEMP and GITHUB_WORKSPACE variables; no full environment dump or service credentials.

## All 15 original failing checks

Names below are exact module/class/method identifiers. The dashboard failure is its width=1360 subtest.

| # | Test | Direct failure |
|---|---|---|
| 1 | test_c10_am_integration.MasterAdapterIntegrationTests.test_c8_diagnostic_uses_master_and_window_without_rewriting_raw_times | development_master_directory_required |
| 2 | test_c10_am_integration.MasterAdapterIntegrationTests.test_master_c7_c6_and_text_support_normal_collection_mode_and_receipt_delay | development_master_directory_required |
| 3 | test_c10_am_integration.MasterAdapterIntegrationTests.test_master_delisted_stock_cannot_retain_legacy_eligibility | development_master_directory_required |
| 4 | test_c10_am_integration.MasterAdapterIntegrationTests.test_missing_evidence_not_replaced_by_master | development_master_directory_required |
| 5 | test_c10_am_integration.MasterAdapterIntegrationTests.test_unverified_sector_cache_and_source_scope_cannot_force_candidates | development_master_directory_required |
| 6 | test_token_manager.ProtectedCacheTests.test_cache_failure_cooldown_survives_restart | cache_redirected |
| 7 | test_token_manager.ProtectedCacheTests.test_corrupt_cache_does_not_trigger_unbounded_reissue | cache_redirected |
| 8 | test_token_manager.ProtectedCacheTests.test_different_app_keys_have_independent_cache_and_lock | cache_redirected |
| 9 | test_token_manager.ProtectedCacheTests.test_failed_encryption_stops_before_oauth | cache_redirected |
| 10 | test_token_manager.ProtectedCacheTests.test_invalidation_compare_and_swap_preserves_newer_token | cache_redirected |
| 11 | test_token_manager.ProtectedCacheTests.test_mock_real_and_secret_rotation_separated | cache_redirected |
| 12 | test_token_manager.ProtectedCacheTests.test_restart_restores_dpapi_cache_without_oauth | cache_redirected |
| 13 | test_eligibility_dashboard.EligibilityDashboardTests.test_desktop_mobile_sector_details_timeline_and_overflow | TypeError: null documentElement while reading scrollWidth |
| 14 | test_token_manager.ProtectedCacheTests.test_two_processes_issue_once | child exitcodes [1,1], expected [0,0]; both children raised cache_redirected |
| 15 | test_voice_foundation.VoiceFoundationTests.test_artifact_writes_only_reports_and_preserves_existing | artifact parent differs from noncanonical fixture REPORTS |

## Traceback roots and classification

1–5: `test_c10_am_integration.py:96 setUp -> master_data.py:74 create_master -> master_data.py:66 writable_path`. The fixture patched MASTER_ROOT using a noncanonical temporary path. The production guard intentionally rejects paths whose resolved location differs from the supplied path.

6–12: `test_token_manager.py:136 manager -> token_cache.py:64 ProtectedTokenCache.__init__`. Construction rejected the noncanonical test-owned root before DPAPI or issuer invocation. This is distinct from the earlier unregistered-user-profile CryptProtectData error 2.

13: `test_eligibility_dashboard.py:180 -> inner (:179) -> bubble_browser_helpers.py:41 evaluate`. Access to the srcdoc document raced document creation. The helper waited a fixed 150ms after Page.setDocumentContent, without checking document/iframe/font readiness. Browser scheduling explains the varying affected tests across runs; assertions must wait for readiness, not be weakened.

14: `test_token_manager.py:202` checks process exit codes. Child `process_get (:33) -> token_cache.py:64` fails for the same root as 6–12. No concurrent OAuth was actually exercised in that failing run.

15: `test_voice_foundation.py:107` compares each returned path's parent with patched REPORTS. `voice_briefing.write_artifacts` returns paths under `REPORTS.resolve()` by contract. The fixture compared those with its original alias.

These are test environment portability/readiness defects. No production collector/DPAPI/path-guard change is needed. In a local synthetic reproduction using a Windows 8.3 temporary-path alias, the unchanged tests produced exactly 12 errors and 2 path-related failures; canonicalizing only the three fixtures made the same 67-test reproduction pass. The exact alias mechanism of the original runner was not recorded, but the rejected noncanonical-root condition and its fix are reproduced.

## Minimal changes and validation

- Resolve the test-owned temporary roots in test_c10_am_integration.py, test_token_manager.py, and test_voice_foundation.py before configuring strict production interfaces.
- Replace the browser helper's fixed sleep with a bounded 10-second wait for complete top/nested documents, srcdoc navigation and loaded fonts. Missing/broken pages still fail; existing geometry/DOM assertions are unchanged.
- Add the allowlisted CI environment diagnostic above. Workflow triggers, runner, dependency installation and full unittest command remain unchanged.
- No tests removed or skipped; no fixture uses operational data; DPAPI remains user-scoped with no plaintext or LOCAL_MACHINE fallback. All production modules remain unchanged.
- Synthetic alias reproduction: 67 PASS after the fixture fixes; focused original failure/C13/C14/browser/startup coverage: 135 PASS in 14.325 seconds.
- Normal-user local validation: 141 AST/import checks; 242 related tests PASS in 26.578 seconds; all 1,150 regression tests PASS in 119.504 seconds, exit code 0, no failures/errors/skips. External connections and original operational-data access remained blocked. CI results remain authoritative for the hosted runner and must be checked on the final pushed SHA before merge.

Main merge and operational deployment remain separately authorized work. This fix does not validate real-server token issuance, hospital scheduler-account DPAPI restoration or analyst decisions on real market data.
