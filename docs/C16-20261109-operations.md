# 2026-11-09 병원 PC 운영 점검 (2026-11-07 확인)

DEV 개발 저장과 LIVE 운영 반영은 별개다. 이번에는 LIVE 파일·DB·인증정보·스케줄·프로세스를 변경하지 않았다. Telegram 시험 전송도 하지 않았다.

## 캘린더

정상 HTML: `diagnostics/c16-k-naver-20261105/dashboard.html`. 파일 탐색기에서 직접 열고 **주도주 캘린더 → 2026-11-05 → 시간**을 선택한다. 구형 `diagnostics/c16-a-20261106-final/dashboard.html`은 정상 HTML로 연결된다.

빈 화면 원인은 공통 `.session-frame{display:none}`을 캘린더 iframe에서 해제하지 않은 CSS였다. 캘린더 선택 시 패널과 iframe 모두 표시하도록 수정했다. 스냅샷은 이미 HTML에 내장되어 있어 fetch·서버가 필요 없다.

`reports/leader-snapshots/YYYY-MM-DD/HHMM.json`은 중복 저장·덮어쓰기를 하지 않는다. 11/05 오전 3개 기록은 **기존 분석 결과 사후 보관**, 오후 2개는 **저장 관측값 사후 복원**이다. 당시 자동 저장 기록으로 취급하지 않는다. 후속 미니차트도 당시 관측 차트로 소급하지 않는다. 기존 JSON은 변경하지 않았다.

새 결과 생성 시 스냅샷 저장, C16 HTML 재생성 시 캘린더 갱신이다. 열린 HTML이 파일 변화를 자동 조회하지는 않는다. HTML·차트·DB·로컬 결과는 Git에 포함되지 않는다.

## 시장 자료

기존 `config/analysis-paths.json`의 SQLite를 읽기 전용으로 확인했다. `adr20`, `breadth_daily`에는 ADR20·상승/하락 종목 수가 있다. 11/04 ADR20은 KOSPI 68.52, KOSDAQ 62.30(잠정). 자료 생성 시각은 11/05 12:39 KST라서 오전 스냅샷에서는 제외하고 오후에서만 재사용한다.

국내 지수 수준·등락률·지수 MA5/MA20, 전일 Nasdaq, Nasdaq100 선물, 동일 기준의 야간선물 20:00/익일06:00 값, 주요 시장 이슈는 저장 자료에서 확보하지 못했다. 개별 종목 일봉을 지수 일봉으로 대체하지 않는다. 현재 시장 분류는 **판단 자료 부족**이다.

KRX의 [공식 서비스 목록](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd)은 KOSPI/KOSDAQ 시리즈 일별시세를 제공한다. 승인된 서비스의 과거 확정 일별 지수가 확보되면 전일 등락률과 MA5/MA20 계산에 활용할 수 있다. [키움 공식 API 안내](https://openapi.kiwoom.com/m/guide/apiguide)에는 업종일봉조회 ka20006이 있지만 현재 수집 어댑터에는 연결되어 있지 않다. 이번에는 새 수집이나 API 호출을 추가하지 않았다.

[eSignal 야간선물](https://esignal.co.kr/kospi200-futures-night/)과 [추정 도출값 안내](https://esignal.co.kr/usage/?vid=8)는 시세가 자체 산식의 Derived Data이며 실제 시세와 다를 수 있다고 명시한다. 공식 거래소 시세로 사용할 수 없다. 공개 문서에서 안정적인 API, 20:00/06:00 동일 시계열, 과거 조회, 자동 수집 허용조건을 확인하지 못했다. 자동 수집은 구현하지 않았으며 변동률도 계산하지 않았다. 이용 허용 및 시계열 제공 방식은 운영자 확인이 필요하다. 접근 제한 우회나 TLS 검증 해제는 하지 않았다.

## LIVE 오전: 코드·스위치 연결, 실제 기동 확인 미완료

확인한 실행 루트는 `C:\Users\dada1\Documents\ARVIS\stock1-live`, 진입은 `ARVIS 시작.bat → Start-RealCollector.ps1 → run_real_collector.py --run`이다. 최신 저장 프로세스 기록은 11/07 14:45 KST의 이 루트를 가리키며 해당 로그에 fatal/stop 기록은 없었다. 이는 현재 생존 여부나 중복 실행 없음의 증명은 아니다. 프로세스 조회가 액세스 거부되어 재시도하지 않았다.

`workflow-config.json`: c15_briefing_enabled, c15_send_enabled, telegram_send_enabled 모두 true. c15_send_slots는 09:40/09:50, 09:45는 내부 기록 전용이다. 120초 발송 창·재시작 과거 보고 재발송 방지·관측 컨텍스트 검사는 기존 그대로다. 필수 랭킹 DB와 섹터 매핑 스키마는 읽기 전용 점검을 통과했다. 월요일 거래대금 파일은 당일 수집 전이므로 아직 없는 것이 정상이다.

LIVE 오전 브리핑은 뉴스 검색·C16 캘린더를 호출하지 않아 해당 기능의 실패가 오전 메시지를 막지 않는다. 메시지 생성·발송은 `c15_delivery.process → telegram_briefing.summary/deliver`로 연결되어 있다. 수집 컨텍스트가 보류되면 전송하지 않는다.

**인증 로더 차이:** `stock1-live.env`에는 키움·KRX·NAVER API HUB·Telegram 값이 모두 있으나 현재 LIVE 실행 코드는 이 파일을 사용하지 않는다. 키움은 `%LOCALAPPDATA%\Stock1\credentials\kiwoom-real.env`, Telegram은 프로세스 환경변수 또는 `telegram-config.dpapi`를 사용한다. KRX 모듈은 기존 KRX_AUTH_KEY 환경변수 경로다. LIVE Telegram 로더는 아직 ALLOWED_CHAT_IDS를 필수로 검사한다. DEV 로더는 BOT_TOKEN/CHAT_ID만으로 허용 목록을 기본 구성한다.

기존 키움 파일과 Telegram 보호 설정은 현재 도구 세션에서 PermissionError가 발생했다. dada1의 정상 실행까지 실패한다는 뜻은 아니지만, 현재 점검만으로 실제 설정 로딩을 완료했다고 판정할 수 없다. 추가 권한 검사·ACL 변경·보호 파일 재시도는 하지 않았다.

## LIVE 오후: DEV 참고 후보 보고는 미반영

LIVE `session_review.afternoon_review`는 14:30 send=false, 15:00 send=true이며 기존 Step6 적격 검사를 통과한 후보만 허용한다. 더 앞의 source_market_time_unverified 보류 조건도 유지된다. 11/05·11/06 저장 workflow 결과는 오전·오후 모두 해당 검증에서 보류된 기록이다. 이 과거 기록으로 월요일 결과를 예단하지 않는다.

DEV `c15_delivery.process_pm`의 14:30/15:00 **참고용 분석** 메시지 경로는 LIVE에 없다. 후보가 없으면 임의 종목을 만들지 않는다. 기존 LIVE 오후 정책을 바꾸지 않았으므로 신규 오후 보고 준비는 미완료다. 오전 가동의 필수 조건이 아니다.

## 월요일 사용자가 할 일

1. dada1 계정으로 병원 PC에서 기존 Collector 창/작업 관리자를 확인한다. 이미 실행 중이면 추가 실행하지 않는다. 동일 루트의 단일 인스턴스를 사용한다.
2. Collector가 멈춰 있다면 LIVE 루트에서 다음 **API 호출 없는 기존 기동 점검**을 먼저 실행한다. 인증값은 출력하지 않는다.

   ```powershell
   .\.venv\Scripts\python.exe -B run_real_collector.py
   ```

   `real_launch_ready`와 `telegram_configuration_ready: true`를 함께 확인한다. 실패하면 임의 재실행·권한 변경 대신 표시된 stage/error_type으로 원인을 확인한다.
3. 성공하고 Collector가 없는 경우 `ARVIS 시작.bat`을 09:10 전에 한 번 실행하고 절전되지 않게 유지한다. 09:40/09:50 자동 발송은 실제 Collector 가동·수집 성공이 필요하다. 지금 수신 성공을 확정할 수 없다.
4. 결과는 `reports/workflow/2026-11-09/0940.json`, `0950.json`의 review/delivery와 `logs/real-collector-*.log`에서 확인한다. held는 분석/관측 보류, delivery 실패는 발송 단계다. 기동 실패는 credentials_load 등 stage/error_type으로 구분한다.

## 향후 최소 LIVE 반영 범위

기존 보호 설정으로 위 기동 점검이 통과한다면 월요일 오전에 캘린더나 새 인증 로더 배포는 필요 없다. 실패한다면 운영이 멈춘 것을 사용자가 확인한 뒤에만 별도 반영한다.

새 env 통합 시 필요한 검토 대상은 LIVE entrypoint의 키움 파일 지정, 공통 environment_file, config/KRX 로더와 Telegram Configuration.from_environment의 선택 허용 목록 처리다. 기존 시작 스크립트가 구형 자격증명 점검을 선행하므로 이 의존성도 함께 검토해야 한다. 파일만 복사해서 통합 완료라고 간주하면 안 된다.

**DEV operating_workflow.py를 LIVE에 통째로 덮어쓰지 않는다.** LIVE에는 별도의 c15_runtime 관측 컨텍스트 보호 코드가 있고 DEV와 동일하지 않다. 오후 process_pm 연결은 오전과 분리한 후속 작업이다. 반영 전 코드·인증정보를 보존하고, DB·시작 배치·스케줄은 유지한다. 실행 중인 프로세스의 자동 중단/재시작/코드 교체는 이번 작업에 포함하지 않는다.

## Git 및 확인 범위

실제 `stock1-*.env`, `.env*`(빈 `.env.example` 제외), 보호 자격증명, DB, reports, diagnostics, 대용량 임시파일은 제외한다. 머신 전용 analysis-paths.json/c15-preview.json은 로컬에 보존하고 `.example.json`만 저장한다. 신규 PC에서는 예제를 복사해 **실제 존재하는 기존 DB 경로**를 지정해야 하며 예제만으로 DB가 생기지 않는다.

관련 캘린더·뉴스·오후 참고 후보·인증 로더·발송 정책 테스트 72개 통과. 로컬 JS 실행으로 11/05 5개 시간대·월 이동·빈 날짜·CSP hash를 확인했다. 실제 브라우저 시각 검증은 완료하지 못했다. 더 넓은 과거 테스트 묶음에는 sibling import 설정, 기존 머신 경로/로컬 산출물, 이전 UI 기대값에 의존하는 실패가 있어 전체 테스트 통과나 LIVE 배포 검증 완료를 주장하지 않는다.
