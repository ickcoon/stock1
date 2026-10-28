# Step 7-B2-A — 독립 일봉 API 실측 준비

## 범위와 Cloud 검증

기준 main: `418a4d3e0c03265c3b6abd001b684402369c304d` (Step 7-B1).
Cloud에서는 **OAuth 및 키움 시세 API를 호출하지 않았다**. 공식 공개 GitHub 자료와 공식 포털 문서 URL만 조사했다. 기존 B1 파서, 추적 어댑터, 인증/HTTP 클라이언트, 운영 CLI, DB, 수집기와 스케줄러는 수정하지 않았다. 신규 진입점은 `python -m kiwoom_collector.daily_diagnostic`이다.

Python 3.11.17 가상환경에 기존 requirements.txt의 python-dotenv 1.2.1, requests 2.32.5, websocket-client 1.9.0을 설치했다. 시작 시 644개 회귀 테스트가 실패·오류·스킵 없이 통과했다. 최종 결과는 PR에 기록한다. 합성 HTTP 응답으로 동작을 테스트한 것이며 실제 일봉·실전 인증 검증을 통과했다는 뜻이 아니다.

## 공식 명세 재확인 (2026-10-28 KST)

- [기존 B1 조사](DAILY_CHART_API_RESEARCH.md), [B1 파서·어댑터 계약](DAILY_CHART.md).
- [공식 REST API JSON](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/953e5dbff123f437ab4d11a78a95191a685eb51f/kiwoom/_data/kiwoom_api_spec.json): 실제 다시 받은 main은 `953e5dbff123f437ab4d11a78a95191a685eb51f`. `ka10081` 존재, `ka11081` 없음.
- [공식 Plugin 정규화 코드](https://github.com/Kiwoom-Securities/Kiwoom-Plugin/blob/7dcf8218f2fc791082decd48932dfdc4276a769e/kw_runtime/normalize.py): 실제 다시 받은 main은 `7dcf8218f2fc791082decd48932dfdc4276a769e`. 가격 절대값 정규화와 백만원 단위 처리는 확인했으나, Plugin의 거래량 추정은 채택하지 않는다.
- [ka10081 포털](https://openapi.kiwoom.com/m/guide/apiguide/07/ka10081), [ka11081 포털](https://openapi.kiwoom.com/m/guide/apiguide/07/ka11081): 이번 Cloud의 일반 요청과 권한 재시도 모두 HTTP 403. B1의 공식 포털 확인 기록을 구분하여 유지하며 이번에 본문을 다시 검증했다고 주장하지 않는다.

| 항목 | ka10081 | ka11081 |
| --- | --- | --- |
| 이름 | 주식일봉차트조회요청 | 주식일봉차트정규장조회요청 |
| 근거 | 이번 공식 GitHub 재확인 + B1 포털 기록 | B1 공식 포털 기록만. 이번 포털 재확인 불가 |
| 요청 | POST `/api/dostk/chart` | 동일 (B1 기록) |
| 필수 본문 | `stk_cd`, `base_dt` (YYYYMMDD), `upd_stkpc_tp` | 동일 (B1 기록) |
| 필수 헤더 | `api-id`, `authorization: Bearer …`; JSON Content-Type | 동일 (B1 기록) |
| 응답 배열 | `stk_dt_pole_chart_qry` | `result_list` (B1 기록; 다른 키로 추정·자동 대체 안 함) |
| 거래대금 | `trde_prica`, 백만원 | B1 포털 기록상 동일 |
| 수정 정보 | 요청 0/1; 행 `upd_stkpc_tp`, `upd_rt`는 B1 포털에서 확인 | B1 포털 기록상 동일, 수정비율 세부 설명은 미확인 |

`cur_prc`(문서상 현재가), `open_pric`, `high_pric`, `low_pric`는 원, `trde_qty`는 1주, `trde_prica`는 백만원이다. `dt`는 거래일 YYYYMMDD다. `pred_pre`, `pred_pre_sig`, `trde_tern_rt`도 원문을 보존한다. 거래대금×1,000,000은 B1의 Decimal 환산이며 원 단위 정밀도가 실제 검증된다는 뜻이 아니다. 가격 부호는 원문과 함께 기록하며 B1의 절대 가격 정규화 결과를 분리해 보여준다. `cur_prc`를 확정 종가로 선언하지 않는다.

문서상 다음 페이지가 있으면 응답 헤더 `cont-yn=Y`, `next-key`를 후속 요청에 전달한다. **이 도구는 커서 정보를 기록만 하고 절대로 다음 페이지를 요청하지 않는다.** `Y`는 실패나 전체 이력 완결이 아니며 `more_available`이다. 누락 헤더는 B1대로 `unknown`이고 N을 임의 생성하지 않는다.

요청 `upd_stkpc_tp=0`은 원주가, `1`은 수정주가 요청이다(B1 포털 기록). 수정 적용은 권리발생일 이후 `base_dt`와 요청 1에 의존한다. 행의 같은 이름 필드는 기업행사 구분자이며 요청 0/1과 다르다. 실제 수정 누적, 거래대금 수정 적용, 정규장·일반 차트의 세션 차이, 확정/정정 시점, 무거래/정지/휴장/빈 응답의 구별은 미확인이다. API 간 자동 전환이나 수정주가 역산은 하지 않는다.

## 기본 실행: 요청 계획만 표시

```powershell
# 키·토큰·.env 없이 실행 가능. HTTP, DB, 출력 파일 생성 모두 없음.
python -m kiwoom_collector.daily_diagnostic

# 여전히 비호출. 명시적 요청 조건을 미리 검토
python -m kiwoom_collector.daily_diagnostic --api-id ka11081 --stock-code 005930 --base-date 20261027 --adjustment 0
```

기본 종목은 삼성전자 `005930`, SK하이닉스 `000660`이다. `--stock-code`를 지정하면 기본값을 대체하고 최대 두 개의 서로 다른 종목만 허용한다. 코드 문자열·시장 접미사는 B1 DailyRequest로 검증하고 보존한다. mock은 B1 계약대로 KRX만 허용한다. 파서가 허용하는 형식이 종목의 실제 상장 여부를 보증하지 않는다.

기준일을 생략한 계획의 `base_dt`는 null이다. 전 거래일/오늘/휴장일을 추정해서 채우지 않는다. `--real`에는 현재 KST 날짜보다 과거인 명시적 기준일이 필수이다. 이 검사는 확정 봉의 증명이 아니며 공식 캘린더를 대신하지 않는다.

## 로컬 PC에서만 명시적으로 실측

아래는 **사용자가 실제 호출을 선택할 때**의 예이며 Cloud에서는 실행하지 않았다. 운영 수집기와 동시 호출하면 다른 프로세스의 API 제한에 영향을 줄 수 있다. 이 독립 도구는 운영 DB의 lock 파일에도 접근하지 않으므로 프로세스 간 전체 호출량을 제어하지 않는다. 운영 프로세스를 자동으로 중지하거나 재시작하지 않는다.

`--real`은 **선택한 환경으로 실제 HTTP를 허용하는 플래그**이며 실전 서버 선택 플래그가 아니다.

- `--environment mock` 기본값: `.env`의 `KIWOOM_ENV=mock`과 일치해야 하며 mock 서버 호출.
- `--environment real`: `.env`의 `KIWOOM_ENV=real`과 일치해야 하며 실전 서버 호출.
- 불일치 시 전송 전에 종료하며 자동 환경 전환하지 않는다.
- 결과의 `environment`와 `data_origin`을 항상 확인한다. mock 응답을 실전 원천 또는 검증된 과거 자료로 취급하지 않는다.

먼저 사용자 본인이 과거 기준일·시장·세션·원주가 여부를 확인한다.

```powershell
$baseDate = Read-Host "공식 캘린더로 확인한 과거 기준일 (YYYYMMDD)"

# mock 1종목, 기존 인증 구조 재사용. OAuth 최대 1회 + 일봉 최대 1회.
# .env에는 기존 APP KEY/SECRET KEY 및 KIWOOM_ENV=mock을 사용한다.
python -m kiwoom_collector.daily_diagnostic --real --authenticate --environment mock --api-id ka10081 --stock-code 005930 --base-date $baseDate --adjustment 0
```

OAuth 자동 발급은 기본으로 금지한다. `--real`만 있고 토큰도 없으면 `auth.status=blocked`, 일봉 전송 0회로 종료하며 진단 보고서만 남긴다. `--authenticate`는 별도의 명시적 OAuth 허용이다. 실패 시 발급 재시도·토큰 갱신·일봉 재전송을 하지 않는다.

유효 토큰을 이미 안전하게 확보했다면 프로세스 환경변수 `KIWOOM_ACCESS_TOKEN`을 주입하여 재발급 없이 사용할 수 있다. CLI 인자로 토큰을 받거나 토큰 파일을 읽지 않는다. 기존 `.env` 인증 설정은 동일하게 필요하며 토큰 값은 대화·명령행 인자·Git에 넣지 않는다. 환경 토큰의 만료 시각은 추정하지 않고 unknown으로 기록한다. 만료 응답이 오면 코드와 원인을 보고하고 끝낸다.

```powershell
# KIWOOM_ACCESS_TOKEN은 안전한 로컬 환경 설정으로 미리 주입되어 있을 때만 사용.
# OAuth 0회, 일봉 최대 1회.
python -m kiwoom_collector.daily_diagnostic --real --environment mock --api-id ka10081 --stock-code 000660 --base-date $baseDate
```

`ka11081`은 `--api-id ka11081`로 명시적으로 선택한다. 사용 전 로컬에서 공식 포털의 최신 정의·지원 환경을 재확인해야 한다. 기본 실행은 두 API를 비교 호출하지 않는다. 종목을 생략하면 두 종목에 각 1회씩, 첫 오류가 없을 때만 두 번째를 호출한다. 같은 코드를 중복 지정하거나 3종목 이상 요청할 수 없다.

## 전송 제한과 결과 읽기

`DailyDiagnosticClient`는 기존 KiwoomClient의 `.env`, 인증 함수, Session과 `_request` 간격 제어를 재사용한다. 독립 Session 가드는 고정 호스트의 OAuth와 승인된 일봉 요청만 허용한다. 기본 redirect 금지, 자동 재시도 0회, 페이지 추가 0회, 실패 후 다음 종목 실행 중단이다. OAuth는 명시 허용 시 최대 한 번, 일봉은 선택한 종목별 최대 한 번이며 전송 불명 네트워크 오류도 시도 횟수에 포함한다. 기존 기본 요청 간격 1.2초(최소 1.1초) 정책을 유지하되 공식 호출 제한 수치나 wire-time 보장을 주장하지 않는다.

각 결과는 요청 코드·본문·API ID, KST 요청 시작/종료·응답 수신 시각, HTTP 상태, 실제 `return_code`, 정제된 서버 원인, 실제 거래일, OHLC 후보·원문 부호, 거래량·거래대금, 수정 관련 원문, 정렬 및 B1 사유를 표시한다.

- `parsed`: B1 구조·필드 검사를 통과. 원천·단위·마감 확정까지 실측 검증되었다는 뜻이 아님.
- `parsed_with_issues`: 파서는 실행됐지만 누락·미래 날짜·역전·중복·수치 등의 사유가 있음. 정상값이나 0으로 채우지 않음.
- `empty`: 실제 빈 배열. 휴장·정지·완료를 추정하지 않음.
- `http_error`, `api_error`, `invalid_json`, `parser_error`, `transport_error`: 실패 원인·확인 가능한 실제 코드 보존. 가짜 응답 없음.
- `not_attempted`: 앞선 오류나 인증 실패로 요청하지 않은 종목. 실패한 요청과 구별함.

응답 정렬을 자동으로 고치지 않으며 미래/기준일 이후 행은 B1의 사유로 격리한다. `units.status=documented_not_runtime_verified`, `close_status=cur_prc_candidate_not_verified_final_close`, `eligible_for_tracking=false`를 명시한다.

### 추적 어댑터 재사용 범위

도구는 D0 스냅샷·캘린더·ClosingEvidence를 조작해서 만들지 않는다. 기본 CLI는 기존 운영 DB를 읽을 수 없으므로 추적 실행은 `not_run_missing_d0_or_calendar`이며 관측 생성 0개다. 파싱 통과를 추적 통과로 바꾸지 않는다.

오프라인 Python 검토 함수 `review_tracking(page, snapshot=..., calendar=..., as_of=...)`는 호출자가 별도로 제공한 문맥에만 기존 `daily_tracking_adapter.to_tracking`을 사용한다. `evidence=()`로 호출하므로 여전히 확정 증거 없음 사유로 보류한다. CLI에는 증거 자동 생성·관측 저장·D0 변경 기능이 없다. 기존 B1 어댑터의 미래 수신 시각 방어를 테스트하며 반환 문맥을 소급하지 않는다.

## 출력 및 보안

기본 경로는 `diagnostics/daily_chart/<mock|real>/<KST시각-고유ID>/report.json`이다. `/diagnostics/`는 Git 제외이다. 파일은 새로만 만들고 기존 보고서를 덮어쓰지 않는다. Unix에서는 디렉터리 700, 보고서 600 권한으로 만들며 Windows ACL은 사용자 환경을 따른다. 프로젝트 안의 임의 소스·data 디렉터리를 출력으로 지정할 수 없다. 프로젝트 밖의 명시적 `--output-dir`은 지원하지만 Git 추적 여부와 로컬 접근권한을 별도로 관리한다.

운영 DB·schema·데이터 접근은 전혀 없으며 SQLite 모듈·Store를 사용하지 않는다. OAuth 응답 원문, authorization, 쿠키, 계좌 필드, 키·토큰은 저장/출력하지 않는다. 응답 헤더는 `api-id`, `cont-yn`, `next-key`만 남긴다. 본문은 문서로 확인한 필드의 안전 투영으로만 보존한다. 알 수 없는 필드, 비정상 컨테이너 내부값과 민감한 메시지는 제거/마스킹하고 알려진 키·토큰 문자열도 제거한다. 따라서 결과는 인증정보가 제거된 진단 기록이며 전체 원본 덤프나 원문 fingerprint의 대체물이 아니다. 인증정보를 예외 문자열로 출력하지 않는다.

## 로컬에서 추가로 검증할 사항

1. 공식 포털 최신 요청·응답 정의, 특히 이번 재접속이 차단된 ka11081.
2. 선택 환경·종목·기준일의 실제 배열 키, 응답 코드 echo와 시장 접미사.
3. HTS/공식 자료와 OHLC 후보·거래량·거래대금의 실제 일치, 백만원 단위 정밀도.
4. 원주가/수정주가와 행 기업행사 정보. 각각 다른 실행으로 최소 호출 비교.
5. 정규장·일반 차트의 세션/시간외 포함 범위와 확정·사후 정정 규칙.
6. 실제 페이지 순서, 커서, 무거래·거래정지·휴장·빈 응답의 구분.
7. 확정·단위·D0 호환성에 대한 실제 근거를 갖추기 전에는 Step 7-A 추적 관측으로 전달하지 않음.

## 오프라인 검증 명령

```powershell
python -m unittest discover -s tests -p test_daily_diagnostic.py -v
python -m unittest discover -s tests -v
python -m kiwoom_collector.daily_diagnostic
```

신규 테스트는 실제 진입점의 네트워크·SQLite 차단, 실제 인증 없는 계획 실행, 요청 예산, 오류·토큰 만료 무재시도, 명시적 OAuth 최대 1회, B1 및 어댑터의 미래/정렬/단위 보류, 마스킹, 운영 data 출력 차단을 합성 fixture와 mock transport로 확인한다.

2026-10-28 KST 최종 Cloud 검증: **673개 통과, 실패 0·오류 0·스킵 0, 50.865초, 종료 코드 0**. 기존 644개와 신규 진단 29개를 포함한다. 실제 키움 OAuth/시세 요청과 Windows PC 실측은 수행하지 않았다.
