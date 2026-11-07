# C15-13 최소 변경 배포 준비

이번 단계는 운영에 적용하지 않았다. 운영 Collector 실행/중지/재시작, 운영 DB/키/스케줄 변경, 실전 API 호출, Telegram 발송, commit/push 없음.

**판정: 패키지 모의 검증 통과 / 실발송 준비 미완료.** 운영 시장 시각 보호 장치와 외부 참조 경로 검토가 남아 있다. 단순히 발송 스위치를 켜면 사용할 수 있다는 의미가 아니다.

## A. 운영과 개발 차이

|대상|확인 결과와 패키지 처리|
|---|---|
|run_real_collector.py|운영에만 있는 실제 런처. 수정하지 않는다. 보호된 인증정보 로드, deployment_approved, 기존 프로필, 페이지 수 제한, text_only/보존, TLS, 휴장 및 예외 처리를 그대로 사용한다. dry-run도 자격증명을 읽으므로 이번에 실행하지 않았다.|
|operating_workflow.py|운영은 `review`에 아침 처리가 있고 개발은 `morning_review`/morning_briefing으로 분리되어 있다. 운영 `review` 입구에 opt-in C15 호출 3줄만 추가한다. 개발 파일 전체를 복사하지 않는다.|
|시장 시각 보호|운영 `__call__`은 recommendation_allowed가 false면 review 호출 자체를 보류한다. 현재 market_data_verification은 이를 true로 만들지 않는다. 이 로직은 **수정하거나 우회하지 않는다**. 따라서 실제 Collector 경로에서는 C15도 보류된다.|
|morning_review / morning_briefing|개발용 분리 구조는 운영에 이식하지 않는다. C15 어댑터만 기존 운영 review에서 호출한다.|
|telegram_briefing.py|운영 원본에 C15 validate/summary와 슬롯 예약/preflight 분기만 추가한다. 기존 API 클라이언트, 수신자 제한, 개인정보 검사, 발송/실패 기록, 보호된 설정 로드는 유지한다.|
|schedule.py|운영 0186 목록에 09:45만 추가. 기존 0198과 나머지 0186 슬롯, 인증/재시도/휴장 코드는 그대로다.|
|설정/계약|기존 workflow-config.json은 자동 교체하지 않는다. 검토용 overlay만 제공한다. 점수/단위 계약은 기존 C15 설정 재사용이다.|

AST 비교로 운영 `Workflow.__call__`, `market_data_verification`, Telegram `api_call`, `from_environment`가 격리본에서 동일함을 확인했다. 런처/Collector/client 등 보호 대상은 해시로 확인했다.

## B·C. 파일 목록과 최소 변경

패키지: `diagnostics/c15-13-package/`

- `review.patch`: 운영 원본 해시를 기준으로 만든 검토용 패치. 아직 운영 미적용.
- `manifest.json`, `file-list.csv`: 정확한 26개 파일과 추가/수정 구분, 이전/이후 해시.
- `settings-overlay.json`: C15 분석/발송 기본 OFF, 권장 발송 슬롯 09:40·09:50, 지연 상한 120초.
- `stage/`: 운영 소스 복사본에 최소 변경을 합친 **모의 검증용** 디렉터리. 전체를 배포하면 안 된다.

기존 수정은 `operating_workflow.py`, `telegram_briefing.py`, `schedule.py` 3개뿐이다. 신규는 23개다:

- C15 모듈 8개: c15_delivery, c15_preview, c15_turnover_selection, c15_identity, c15_leadership, c15_briefing, c15_turnover_audit, c15_briefing_data.
- 운영에 없는 기존 분석 의존 모듈 10개: c12_latest, krx_openapi, krx_master, c12_external_theme_bridge, c12_theme_expansion, theme_normalization, c12_theme_audit, c12_master_audit, c12_sector_audit, external_theme_master. 새 분석 엔진을 만든 것이 아니라 기존 C15의 필요한 모듈을 포함했다. 이미 운영에 있는 공통 모듈은 덮어쓰지 않는다.
- 설정 5개: config/c15-display-contract.json, c15-turnover-contract.json, c15-leadership.json, c15-preview.json, c15-analysis-paths.json.

원본 DB를 패키지에 넣거나 새 수집 시스템을 만들지 않았다. 현재 참조 설정은 개발 위치의 일별/ADR/테마/체크포인트 DB와 기존 종목 캐시를 읽기 전용으로 참조한다. 이 경로의 접근성·보존 책임을 운영 담당자가 승인하거나 안정된 읽기 전용 경로로 설정해야 한다. 경로 수정 시 패키지를 다시 만들고 재검증한다. 개발 머신의 임시 진단 경로를 검토 없이 운영 의존성으로 확정하지 않는다.

## 발송 정책

기존 스케줄러를 사용하며 분석 슬롯은 09:40/09:45/09:50이다. `c15_send_slots`로 그중 발송할 슬롯을 선택한다. 초기값은 09:40/09:50, 09:45는 기록 전용이다.

`c15_briefing_enabled`, 별도 `c15_send_enabled`, 기존 `telegram_send_enabled`를 분리했다. C15 send 기본값은 false이므로 기존 오후 Telegram 정책을 바꾸지 않는다. 승인 후 두 send 스위치가 true이고 지정 슬롯이며 due부터 120초 이내인 신규 결과만 전송 가능하다. 지연 상한은 1~300초에서 설정한다. 과거 거래일/미래 due는 발송하지 않는다.

동결 보고서가 이미 있으면 재시작 시 항상 `frozen_replay_no_send`다. 신규 분석이어도 허용 지연을 넘으면 `expired_no_catchup`이다. 이전 발송의 실패/불명확 상태 예약도 삭제하지 않으며 재시도하지 않는다. 동일 날짜·수신자·슬롯 예약은 기존 Telegram 독점 파일 생성으로 보호한다. 기존 오전 예약도 C15 발송을 막는다. 기존 오전 발송과 C15를 동시에 켜면 안 되며, 당일 롤백 시에도 이미 생성된 슬롯 예약과 발송 이력을 확인해야 한다.

09:45 수집 실패 시 C15-10B의 동일 거래일·이전 시각·최대 10분·정상 저장 검증을 유지한다. 미래 자료를 사용하지 않는다.

## D. 반영 전 백업 대상

백업은 승인된 점검 시간에 운영 담당자가 수행한다. 이번에는 하지 않았다.

- manifest에서 action=patch인 기존 3개 파일의 **원본 바이트**와 workflow-config.json.
- 런처/등록된 실행 명령·작업 스케줄 정의는 변경하지 않지만 복구 기준으로 기록한다.
- notification_state와 reports의 기존 발송/예약 이력은 보존한다. 배포 패키지의 모의 상태를 이곳으로 복사하지 않는다.
- 운영 DB와 키는 코드 배포 대상이 아니다. DB 백업이 별도로 필요하면 승인된 SQLite 온라인 백업 절차를 사용한다. WAL 사용 중인 DB 파일을 단순 복사해 일관된 백업이라고 간주하지 않는다.

## E. 배포 후 확인 항목

승인 후에도 먼저 발송 OFF로 확인한다. 0198 기존 세 슬롯 유지, 0186 09:45 단일 수집 및 기존 슬롯 유지, 페이지 수/요청 간격/완료 시간/실패 이력, 09:45 실패 시 대체 시각 표시를 확인한다. 0198/0186 원시값이나 운영 DB 구조를 바꾸지 않는다.

각 reports/c15-YYYYMMDD-HHMM의 telegram.json/txt와 diagnostics.json 생성, TOP3/대표 3개, 등락률/시총/대금/ADR 참고/출처 시각, 36개 이름 제외 근거, 불확실 종목 보류를 확인한다. 시총은 최근 1~2평일 종가 참고, 기존 하한 유지다. 별도 휴일 달력 구축은 하지 않아 휴일이 끼면 보수적으로 미확인이 늘 수 있다.

실제 발송을 승인한 뒤에만 기존 수신자 제한/예약/기록을 확인한다. 기존 시장 시각 보호에 의해 held_unverified_market_data가 기록되면 **보호 장치 정상 동작**이며 발송 성공으로 보고하지 않는다. 이를 해결하려면 별도 검증 정책 결정과 승인이 필요하고 이번 패키지에서 자동 해제하지 않는다.

## F. 롤백

운영 담당자가 승인된 절차로 새 실행 진입을 막고 실행 중 작업 종료를 확인한 뒤 백업한 기존 3개 파일과 workflow-config.json을 복원한다. 신규 파일은 manifest에서 add인 정확한 목록만 별도 보관 위치로 이동하거나 그대로 둔다. 광범위 삭제는 하지 않는다. 운영 DB·키·예약·발송 이력은 되돌리거나 삭제하지 않는다. 오후 경로/기존 수집 스케줄이 복원됐는지 확인한 뒤 승인된 기존 재개 절차를 따른다. 패치 역적용보다 원본 바이트 복원을 우선한다.

## G. 테스트

관련 **144개 테스트 통과**, 신규 발송 정책 테스트 12개 포함. 기본 OFF/이중 스위치, 권장 09:45 OFF, 설정 변경, 지연 경계, 날짜 변경, 미래 due, 동결 재시작 차단을 검증했다. 기존 상품 제외/시총/0186 대체/Telegram 계약과 중복 방지 회귀를 포함한다.

운영 복사본에서 실제 저장 자료로 `patched runtime Workflow.review → C15 → 기존 Telegram deliver`를 세 시점 재생했다. SQLite는 mode=ro URI만 허용하고 네트워크/자격증명 로드를 차단했다. 합성 Telegram 자격증명으로 HTTP 직전 강제 중단 후 중복 차단을 확인했다. 실제 Collector의 바깥 시장 시각 guard도 모의 저장소로 실행하여 held 상태를 확인했다. **직접 review 재생 성공은 실제 Collector에서 guard를 통과했다는 의미가 아니다.**

- `preflight-results.json`: 격리 재생과 보호 장치 결과.
- `tests.json`: 테스트 범위.
- `patch-validation.json`: 개발 복사본에만 패치를 적용하고 격리본과 정규화 내용이 일치한 결과.
- [09:40 메시지](../diagnostics/c15-13-package/replay/reports/c15-20261105-0940/telegram.txt), [09:45 메시지](../diagnostics/c15-13-package/replay/reports/c15-20261105-0945/telegram.txt), [09:50 메시지](../diagnostics/c15-13-package/replay/reports/c15-20261105-0950/telegram.txt).

기존 C15-10B 미적용 패치는 그대로 보존했다. 새 review.patch에도 같은 09:45 변경이 포함되므로 **둘 다 적용하지 않는다**.

## H. 사용자 승인 후 실행 절차

지금 실행 가능한 것은 다음 **개발 점검 명령만**이다. 런처를 실행하지 않는다. 점검은 운영을 읽고 개발 폴더에 새 재생 결과를 쓴다.

```powershell
Set-Location 'C:\Users\dada1\Documents\ARVIS\stock1-dev'
.venv/Scripts/python.exe scripts/check_c15_13_package.py
```

해시 차이가 있으면 즉시 보류한다. stage를 직접 고치지 말고 현재 운영 기준으로 패키지를 재작성한다. 최초 패키지 생성 스크립트 `build_c15_13_package.py`는 기존 출력 디렉터리를 덮어쓰지 않는다.

다음은 **별도 운영 배포 승인 및 운영 담당자의 점검 시간 확보 후에만** 사용할 절차다:

1. 외부 참조 경로와 시장 시각 guard 제약을 먼저 검토한다. 실발송까지 목표라면 guard를 정당하게 통과할 근거/정책을 별도 승인해야 한다. 미해결 상태로 실발송 준비 완료라 하지 않는다.
2. 기존 운영 관리 절차로 Collector의 파일 교체 중 실행/재진입이 없도록 한다. 이번 단계에서는 해당 절차나 프로세스를 실행하지 않았다. 임의로 두 번째 Collector를 띄우지 않는다.
3. manifest의 이전 SHA256과 실제 파일이 일치하는지 확인하고 위 백업 목록을 확보한다. 승인된 경로로 아래 변수 값을 지정한다.

```powershell
$Runtime = 'C:\Users\dada1\Downloads\stock1-dev\runtime\c12v-real-20261102'
$RuntimeRepo = 'C:\Users\dada1\Downloads\stock1-dev'
$Package = 'C:\Users\dada1\Documents\ARVIS\stock1-dev\diagnostics\c15-13-package'
# CRLF 운영 원본을 기준으로 검증된 옵션. 실패하면 진행하지 않는다.
git -C $RuntimeRepo apply --check --ignore-space-change --directory=runtime/c12v-real-20261102 "$Package\review.patch"
# 위 점검 및 별도 배포 승인이 완료된 경우에만 적용한다.
git -C $RuntimeRepo apply --ignore-space-change --directory=runtime/c12v-real-20261102 "$Package\review.patch"
```

Git 저장소의 하위 폴더에서 패치가 건너뛰어지지 않도록 실제 저장소 루트와 directory를 명시했다. 운영 소유자 계정으로 실행한다. 다른 계정에서 dubious ownership이 나오면 중단하며 전역 safe.directory를 자동 변경하지 않는다.

4. 기존 workflow-config.json에 settings-overlay.json의 **C15 키만 병합**한다. 기존 deployment_approved/프로필/Telegram/휴장/보존/페이지 수/notification_state를 교체하지 않는다. 두 C15 enabled는 우선 false다. 원본 대비 수정 범위를 검토하고 패치 파일 내용과 줄바꿈 정규화 기준으로 대조한다.
5. 파일/참조 권한 및 import 검증 후 별도 승인된 단계에서 c15_briefing_enabled만 true, c15_send_enabled는 false로 두고 운영 담당자의 기존 Collector 재개 절차를 따른다. 일정/명령 등록을 새로 만들지 않는다.
6. 다음 거래일 발송 OFF 관찰로 E 항목을 확인한다. 시장 시각 guard 보류라면 정책을 임의 우회하지 않고 중단 사유를 보고한다.
7. 실제 발송에 대한 **별도 승인**과 guard/수신자/중복 상태 확인 후 c15_send_enabled를 true로 전환한다. 09:40/09:50 기본, 09:45 기록 전용 유지. 과거 동결 브리핑을 삭제해 재발송하지 않는다.

현재 패키지에는 자동 운영 적용/프로세스 제어 스크립트가 없으며, 이번 작업에서 위 운영 명령을 실행하지 않았다.
