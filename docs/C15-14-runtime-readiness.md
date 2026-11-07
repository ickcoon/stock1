# C15-14 정상 수집 보호 경로와 참조 DB 점검

**기술적 배포 준비 완료, 운영 미적용·발송 OFF.** 정상 예약 수집의 잠정 관측 브리핑을 허용하는 최소 패치와 참조 점검을 검증했다. 시장 원시 시각/실시간 가격을 검증했다고 주장하거나 매매 추천을 허용한 것은 아니다. 운영 배포 및 실제 발송에는 사용자 승인이 필요하다.

운영 Collector 중지/실행/재시작/수정, 운영 DB/키/스케줄 변경, 실전 API 호출, Telegram 발송, commit/push는 하지 않았다.

## A. 정확한 차단 원인과 해결

운영 `operating_workflow.market_data_verification`은 0198 원본 `dt`의 과거 날짜를 stale로 거부하지만, 당일 `dt/tm`이 실제 가격의 시각이라는 증거는 없다고 정의한다. 따라서 반환값은 항상 `market_trade_date_verified=false`, `market_asof_verified=false`, `recommendation_allowed=false`다. `Workflow.__call__`이 recommendation_allowed일 때만 review를 호출하므로 정상 완료 수집도 `held_unverified_market_data`로 보류됐다.

이 함수와 매매 추천 제한은 **그대로 유지**한다. C15 opt-in 오전 슬롯에서만 `c15_runtime.context`가 별도의 `allowed`를 판단한다. 의미는 **정상 장중 예약 수신에 기반한 잠정 관측**, 가격 시각 검증/추천 허용이 아니다. 기존 오후와 구형 아침 경로의 보호는 바꾸지 않는다.

허용 조건은 모두 충족해야 한다:

- 09:40/09:45/09:50 정각 due, 같은 KST 날짜의 due~120초 이내 현재 시각.
- 평일이며 기존 holidays_file에 등록된 휴장일이 아님. 달력 파일 오류도 보류.
- 해당 collection ID가 real/ka00198/success/test_mode=0이며 scheduled_at이 due와 정확히 일치.
- collected_at이 due~5초 이내이고 현재보다 미래가 아님. 기록 수 20개, 순위 1~20, 서로 다른 20개 코드.
- 원본에 명시된 날짜가 있으면 당일이어야 함. 과거/미래 날짜 거부.
- DB 검증 오류/누락은 보류. 추가 API 호출로 검증하지 않음.

`__call__`에서 이 조건을 통과한 C15에만 review 진입을 허용하고, review에서 다시 확인한다. 전송 직전에는 기존 발송 정책이 현재 시각을 다시 검사한다. 테스트 시계는 기존 `Workflow.clock` 주입으로만 사용했으며 운영 설정이나 CLI에 가짜 시각 옵션을 만들지 않았다.

|상황|처리|
|---|---|
|A. 정상 장중 완료|별도 수신 조건 통과 시 C15 분석 진입. 09:40/09:50 지정 슬롯 및 발송 스위치/검증/예약 통과 시 기존 전송 경로 사용.|
|B. 과거 자료 개발 재생|개발 검증에서만 시계 주입. 실제 현재 시각으로 과거 due를 실행하면 outside_live_window.|
|C. 재시작 후 남은 슬롯|기존 스케줄러의 과거 슬롯 미보충 유지. 이미 동결된 보고서는 frozen_replay_no_send. 예약 파일도 중복 차단.|
|D. 120초 초과|분석 진입 수신 조건에서 차단하고 전송 직전에도 만료 검사. 뒤늦은 발송 없음.|
|E. 휴장/비정상 시각|주말/명시 휴장 달력/달력 오류/다른 날짜/미래 due 차단. 기존 Collector의 MarketClosed·인증·재시도 처리 유지.|

달력에 없는 평일 휴장을 이 로컬 조건만으로 입증할 수는 없다. 기존 `MarketClosed` 응답 처리와 명시 휴장 파일을 유지한다. 원시 가격 시각 미검증은 잠정 관측의 한계로 남으며, 이를 검증 완료로 바꾸는 플래그는 없다.

## B. 구현 및 테스트

- `kiwoom_collector/c15_runtime.py` 신규: 읽기 전용 예약 수신 조건 검증.
- `kiwoom_collector/c15_references.py` 신규: 설정 기반 SQLite 존재/지원 스키마 점검 CLI. 0198/테마 필수, 나머지 선택.
- `kiwoom_collector/c15_delivery.py`: 필수 참조 누락 시 분석/발송 보류, 참조 진단 저장, live_context 없으면 발송 금지. 동결/실패 예약 보호 유지.
- `scripts/build_c15_14_package.py`: 운영 원본에 한정된 패치와 기존 C15 의존 모듈 조합. C15-13 생성기를 재사용한다.
- `scripts/check_c15_14_package.py`: 운영 해시 확인, 격리 복사본의 실제 `Workflow.__call__` 통합 재생. 운영 적용 기능 없음.
- `tests/test_c15_runtime.py`: 신규 15개 수신/휴장/실패/참조/읽기 전용 테스트.

**관련 회귀 334개 통과.** 정상 09:40/09:50은 실제 저장 자료로 바깥 Workflow부터 기존 Telegram HTTP 경계까지 도달했다. 09:45는 record_only이고 09:40 0186 대체 표시를 유지했다. 각 TOP3와 대표 3개, 등락률/시총/대금/ADR20/출처 시각을 확인했다.

통합 스크립트에서 실제 운영 DB는 URI mode=ro와 query_only로만 열었다. 테스트 Store에는 개발 위치의 실제 60행 발췌 fixture를 사용했고 운영 DB를 복제하거나 쓴 것은 아니다. 네트워크 차단·합성 Telegram 설정을 사용하고 api_call을 HTTP 전에 강제 중단했다. 정상 두 슬롯의 결과가 failed_or_unknown인 것은 의도된 모의 중단이며 발송 성공이 아니다. 이후 동일 슬롯은 duplicate_blocked다.

지연/과거 재생/휴장도 바깥 Workflow에서 review가 호출되지 않는 것을 검증했다. 필수 테마 DB 누락은 held_missing_required_reference, 선택 ADR 누락은 ADR20 미확인 브리핑으로 정상 처리했다. DB 누락 시 파일을 새로 만들지 않는다. 기존 ETF 등 제외, 시총 하한/1~2평일 참고값, 점수 가중치와 금액 점수 OFF 정책을 유지한다.

## C. 실제 DB 경로와 누락 정책

기준 경로:

- `R = C:\Users\dada1\Downloads\stock1-dev\runtime\c12v-real-20261102`
- `D = C:\Users\dada1\Documents\ARVIS\stock1-dev`

|자료|실제 경로|필수 여부·누락 시 처리|
|---|---|---|
|0198|R\data\stock1-real.db|필수. 없거나 스키마/해당 수집 검증 실패 시 보류.|
|0186|R\data\turnover\YYYY-MM-DD\HHMM.db|선택. 동일 거래일 과거 10분 이내 정상 자료를 기존 선택기로 사용. 없으면 미확인·부분 분석.|
|KRX 시총/상품 참고|D\diagnostics\lightweight-20261105\daily-120.db|선택. 누락/과거 참고값 부족 시 시총·유형 미확인/적격성 보류. 추정하지 않음.|
|테마 DB|D\diagnostics\lightweight-20261105\theme-master.sqlite|필수. 지원 스키마가 아니거나 없으면 브리핑 보류.|
|ADR20|D\diagnostics\lightweight-20261105\adr-60.db|선택. 없으면 ADR20 미확인. 점수 필수조건 아님.|
|KRX 신원 연결 이력|D\diagnostics\krx-as\progress.db|선택. 없으면 검증된 종목/시총 연결이 줄어들거나 보류.|
|기존 ka10001 캐시|C:\Users\dada1\Downloads\stock1-dev\diagnostics\master\c10-master.db|선택. 현재 유효 시총 캐시 0행. 없으면 기존 대체 참조만 사용.|

모두 C15에서는 **SQLite URI mode=ro + PRAGMA query_only=ON**으로 연다. 운영 Collector 자체의 기존 수집 쓰기는 변경하지 않는다. 데이터셋은 새로 복제하지 않고 현재 경량 자료를 재사용했다. 운영/개발 모두 같은 실제 자료를 참조하며, 차이는 개발 미리보기 설정의 상대 경로를 패키지 설정에서 명시적 절대 경로로 정리한 것이다.

`config/c15-preview.json`은 runtime_root, ranking_relative, turnover_relative, identity_checkpoint, eligibility_master_db를 지정한다. 패키지 `config/c15-analysis-paths.json`은 시총/테마/ADR 경로를 지정한다. 기존 수집용 대형 DB를 C15 필수로 추가하지 않았다. 현재 11/05 09:45의 정확한 0186 DB는 없지만 09:40 대체가 검증됐으므로 필수 참조 사전 점검은 통과한다. 경로/스키마 존재는 자료의 최신성·적격성 통과와 별개다.

이 경량 참조 경로는 앞으로도 사용하므로 진단 정리 작업에서 삭제하지 않아야 한다. 사용자 계정의 읽기 권한도 실제 배포 전 점검한다. 시총은 현재 재생에서 11/03 종가 참고이며 미래 거래일/미확인 수치를 채우지 않는다. 별도 전종목 마스터나 수집 시스템은 만들지 않았다.

읽기 전용 점검:

```powershell
# 지금 개발 환경에서 실행 가능. 키/API/프로세스에 접근하지 않는다.
.venv/Scripts/python.exe -m kiwoom_collector.c15_references --config config/c15-preview.json --date 2026-11-05 --slot 09:45
.venv/Scripts/python.exe scripts/check_c15_14_package.py
```

첫 명령은 필수 자료가 없으면 종료 코드 2다. 두 번째는 개발 폴더에 격리 재생/모의 예약만 저장한다. 배포 후 첫 명령의 config를 운영 config 절대 경로로 지정해 확인할 수 있다. 패키지 해시 점검은 **배포 전 원본** 비교이므로 실제 적용 후 옛 원본 해시로 재점검하면 drift가 나오는 것이 정상이다.

## D. 최종 배포 패키지

`diagnostics/c15-14-package/manifest.json`, `file-list.csv`에 정확한 **28개** 파일이 있다. 기존 수정 3개, 신규 25개이며 기존 workflow-config.json의 C15 키 병합은 별도 설정 작업이다.

- 기존 `operating_workflow.py`: C15에 한정한 정상 수신 조건과 review 연결. 기존 market_data_verification·추천 제한·인증/예외/휴장 처리는 유지.
- 기존 `telegram_briefing.py`: C15 검증/본문/슬롯 예약·preflight만 추가. 전송 클라이언트·수신자 제한·발송 기록은 기존 코드.
- 기존 `schedule.py`: 0186 09:45 한 번 추가. 기존 0198과 나머지 슬롯 유지.
- 신규 20개 Python 모듈: C15-13에 포함했던 C15 및 기존 분석 의존 18개 + c15_runtime/c15_references. 전체 정확한 이름은 file-list.csv가 기준이다.
- 신규 5개 설정: c15-display-contract, c15-turnover-contract, c15-leadership, c15-preview, c15-analysis-paths JSON.

`settings-overlay.json`: c15_briefing_enabled=false, c15_send_enabled=false, c15_send_slots=[09:40,09:50], c15_send_max_lag_seconds=120, c15_preview_config=운영 설정 경로. 기존 global telegram_send_enabled 및 오후 발송 정책/notification_state/인증/휴장 설정은 변경하지 않는다.

`review.patch`는 아직 미적용이다. stage는 운영 파일의 모의 검증용 복사본이며 통째로 덮어쓰면 안 된다. C15-13 또는 C15-10B 패치와 중복 적용하지 않는다. C15-14 패치 하나에 09:45 수집 변경이 포함된다.

## E·F. 위험과 롤백

남는 위험은 원시 시장 시각 미검증, 명시 달력 밖 휴장, 경량 참조 경로 삭제/노후화, 0186 부분 수집, ADR 사후참고, 캐시 없는 시총·유동성 적격성 보류다. 메시지는 잠정 관측으로 유지한다. 발송 실패/불명확 예약은 재시도하지 않아 브리핑이 한 번 빠질 수 있으며 이는 중복 방지를 위한 선택이다.

승인된 운영 반영 전에는 기존 3개 파일과 workflow-config.json 원본을 백업하고 기존 등록 실행 명령/프로필을 기록한다. `backup-rollback-plan.json`에 목록이 있다. DB/키/발송 예약은 배포 대상이 아니다.

실패 시 승인된 기존 운영 관리 절차로 새 실행 진입을 막고 파일 교체가 가능한 시점을 확보한 뒤 백업한 3개 파일과 설정을 복원한다. 신규 파일은 정확한 add 목록만 별도 보관하거나 그대로 둔다. notification_state/reports/운영 DB/키를 롤백하거나 예약을 지워 재발송하지 않는다. 같은 날 구형 발송으로 복귀할 때에도 당일 C15 발송 이력을 먼저 확인한다. 이번에는 중지나 복원을 실행하지 않았다.

## G. 적용 가능 여부와 승인 후 순서

**현재 구성의 잠정 관측 브리핑에 대한 기술적 운영 적용은 가능하다.** 두 차단 요인은 코드/설정/격리 테스트에서 해결했고 필수 참조 사전 점검도 통과했다. 이는 실제 배포·실제 발송·서버 가격 시각 검증 완료를 뜻하지 않는다. manifest의 technical_ready_for_deployment=true와 ready_for_live=false는 각각 준비 완료와 실제 발송 미활성 상태를 구분한다.

승인 후에는 C15-13 문서의 백업·운영 관리 절차를 따르되 **C15-14 패키지 경로만** 사용한다. 실행 전 운영 원본 SHA256을 재확인하고, 변경되었으면 새 기준으로 재작성한다. 운영 소유자 계정으로 다음 patch check/apply를 단계별 수행한다. 이번에는 수행하지 않았다.

```powershell
$RuntimeRepo = 'C:\Users\dada1\Downloads\stock1-dev'
$Package = 'C:\Users\dada1\Documents\ARVIS\stock1-dev\diagnostics\c15-14-package'
git -C $RuntimeRepo apply --check --ignore-space-change --directory=runtime/c12v-real-20261102 "$Package\review.patch"
# 별도 배포 승인·백업·기존 실행 관리 절차 완료 후에만:
git -C $RuntimeRepo apply --ignore-space-change --directory=runtime/c12v-real-20261102 "$Package\review.patch"
```

그 후 settings-overlay의 C15 키만 병합하고 우선 c15_briefing_enabled=true, c15_send_enabled=false로 분석·기록을 관찰한다. 실제 발송 승인 후에만 C15 send를 true로 변경한다. global Telegram 스위치/수신자 allowlist/기존 notification_state도 유효해야 한다. 09:40/09:50 기본 발송, 09:45 기록 전용을 확인하며 기존 아침 발송과 C15를 동시에 켜지 않는다. 과거 브리핑은 재발송하지 않는다. 가짜 시계는 운영에서 사용하지 않는다.

결과물: [09:40](../diagnostics/c15-14-package/previews/0940/telegram.txt), [09:45](../diagnostics/c15-14-package/previews/0945/telegram.txt), [09:50](../diagnostics/c15-14-package/previews/0950/telegram.txt). 상세 근거는 integration-results.json, reference-inventory.json, tests.json, patch-validation.json에 있다.
