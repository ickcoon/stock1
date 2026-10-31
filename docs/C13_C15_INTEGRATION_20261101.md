# C13–C15 integration — 2026-11-01 KST

## Scope

Base: official main `7d200cff1b29fb18a8982f9f571c0c9624e0fcf1`. C13 TokenManager, user-scoped DPAPI cache and authentication integration are already present in that commit and are reused without modification.

This change adds the independent C14-A analyst package (`analyze_stock`, `from_daily_pages`), its 22 synthetic tests, the C14-A usage/design document, the C14 condition-search/52-week-high watchlist design, and the two C15 market-intelligence/domestic-breadth ADR designs. C14 watchlist and C15 are designs only. News analysis remains unimplemented.

Collector retains its existing `python -m kiwoom_collector collect` entry point. Analyst is a separate, offline Python interface; no standalone analyst CLI is implemented. It is not wired into collector execution, authentication, DB writes or scheduling. C15 has no executable module or schedule.

## DPAPI diagnosis

The restricted validation account had no registered profile, loaded user hive or matching SID directory in the DPAPI store, while inherited USERPROFILE/APPDATA referred to a different user. An independent in-memory CryptProtectData call failed with Win32 error 2 before any cache-file write.

Under the normal Windows user with its profile loaded, the same independent call and unchanged C13 WindowsDPAPI implementation completed synthetic encryption/decryption successfully. Profile registration, loaded hive and matching SID store were confirmed; key contents were not inspected. This does not establish an administrator-permission requirement. Run user-scoped DPAPI validation under the intended account with its profile loaded. Do not substitute environment variables, copy keys, widen ACLs, enable LOCAL_MACHINE protection or add plaintext fallback.

Microsoft's implementation documents the profile-related file-not-found case: [ProtectedData source](https://source.dot.net/System.Security.Cryptography.ProtectedData/System/Security/Cryptography/ProtectedData.cs.html). See also [CryptProtectData](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata).

## Validation and deployment boundary

Original C14/C15 files, backup and integration copies matched the existing SHA-256 manifest for all eight files. Existing development copies, backups and earlier validation logs are preserved outside this change.

Latest integration validation under the normal Windows user with its profile loaded: 141 Python files passed AST/import checks; 242 related tests passed in 26.816 seconds; the full regression suite passed all 1,150 tests in 119.826 seconds, exit code 0, without failures, errors or skips. This includes 26 TokenManager tests and 22 analyst tests. Local test logs and environment are excluded from Git. Validation uses synthetic HTTP responses, temporary caches/DBs, removed service credential environment variables, an external-socket audit block, and an original-data access block. Only loopback connections for synthetic browser tests are allowed.

No real OAuth/Kiwoom API request, hospital access, deployment, collector restart, operational DB change or task-scheduler change is part of this integration. Main merge requires separate approval.

Before operational use, validate the hospital execution account/profile and cache directory ACLs, synthetic save/restore under the actual scheduler context and after reboot, cache identity across credential rotation, system time, and cross-PC token issuance effects. Any real-server validation and deployment require their own authorized window. Review corporate-action/price-basis, completed-session calendar, bar finality and point-in-time selection evidence before relying on analyst output from saved real data.
