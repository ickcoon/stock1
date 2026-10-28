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

### Step 7-B2-C3: 진단 Session의 명시적 CA 설정

이 진단 CLI만 `trust_env=False`를 사용한다. 환경 프록시·netrc 인증 및
`REQUESTS_CA_BUNDLE`/`CURL_CA_BUNDLE` 환경값을 자동으로 채택하지 않는다.
별도 CA가 필요한 PC에서는 출처를 검토한 공개 PEM을 `--ca-bundle`로 명시한다.
공통 KiwoomClient와 운영 수집기의 설정은 변경하지 않는다.

```powershell
# 별도 승인 범위: 모의 OAuth 최대 1회 + 삼성전자 일봉 최대 1회
python -m kiwoom_collector.daily_diagnostic --real --authenticate --environment mock --api-id ka10081 --stock-code 005930 --base-date 20261027 --adjustment 0 --ca-bundle data/home-validation-20261028/requests-ca.pem
```

명시한 CA 파일이 없거나 읽을 수 없거나 올바른 인증서 번들이 아니면 HTTP 전에
종료한다. 기본 certifi로 자동 대체하지 않는다. CA 옵션 생략 시에는 기본 certifi와
인증서 검증을 유지한다. 시스템 신뢰 저장소·certifi 원본·환경 설정을 수정하지 않는다.
기존 공통 인증·timeout·요청 간격을 재사용하며 진단 Session의 재시도만 명시적으로 0회로
설정한다. TLS/호스트명 검증을 비활성화하는 옵션은 없다.

집 PC의 기존 성공 번들을 오프라인 대조한 결과 SHA256은
`829a4ba9b36629e579d8c7e8950c9266db2fc15d09cc9fcdadbbb23b2b2da71f`로
기존 성공 기록과 일치했다. 141개 인증서 모두 현재 certifi 또는 Windows ROOT의
서버 인증용 신뢰 루트에서 출처를 확인했다. certifi에 추가된 23개는 모두 현재
Windows의 서버 인증 신뢰 대상이며 출처 미확인 인증서는 없었다. 오래된 만료 루트
4개도 포함되어 있다. 파일을 임의 정리하거나 새 인증서를 추가하지 않았으며,
실제 연결에는 OpenSSL의 만료·체인·호스트명 검증을 그대로 적용한다. 이는 해당 PC의
기존 신뢰 설정을 재사용하는 것이며 모든 PC에서 이 번들을 자동 신뢰하라는 의미가 아니다.

결과의 `tls`에는 trust_env, 검증 유지 여부, CA 선택과 명시한 번들의 SHA256을 남긴다.
OAuth 전송 실패 시에는 안전한 예외 클래스명(예: SSLError, ConnectTimeout)만 추가하고
인증정보가 섞일 수 있는 원래 예외 문자열은 출력하지 않는다. 기본 계획 모드는 여전히
인증·HTTP·CA 파일 로딩 없이 실행한다.

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

## Step 7-B2-C3 로컬 보완·실측 결과 (2026-10-28 KST)

- 실측 전 전체 회귀: **688개 통과, 실패 0·오류 0·스킵 0, 89.732초**.
  기존 681개 및 CA·프록시·안전 실패·예외 클래스·CLI 연결 테스트 7개를 포함한다.
- 사용자 승인 범위대로 모의 OAuth 1회 + ka10081 삼성전자 005930 1회만 실행했다.
  둘 다 HTTP 200, return_code=0. 재시도·연속조회·실전 전환·SK하이닉스 호출은 없다.
- 기준일 20261027, 원주가 요청 0. 일봉 요청은 16:53:31.702649,
  응답은 16:53:31.765443 KST. B1 구조·필드 검증을 통과한 내림차순 600행이며
  거래일 범위는 2026-10-27~2024-05-08이다. page/row issues는 비어 있다.
- 2026-10-27 응답: 시가 222,000원, 고가 225,500원, 저가 220,000원,
  cur_prc(종가 후보) 225,000원, 거래량 14,196,879주,
  거래대금 원문 3,162,206백만원 → 3,162,206,000,000원.
  가격 원문에 +/- 부호는 없으며, 행의 upd_stkpc_tp와 upd_rt는 빈 문자열이다.
- pagination=more_available이나 추가 페이지는 호출하지 않았다. 이 1페이지를
  전체 과거 이력 완결로 취급하지 않는다.
- 단위는 여전히 documented_not_runtime_verified이며, 모의 원천·세션 집계·마감 확정과
  HTS 일치는 미검증이다. eligible_for_tracking=false, 관측 생성 0개를 유지했다.
  운영 DB·D0 스냅샷·운영 수집기·스케줄러는 변경하지 않았다.
- 민감정보를 제거한 보고서는 Git 제외 경로
  `diagnostics/daily_chart/mock/20261028T165330-7cab9cffc22d/report.json`에 보존했다.
  CA PEM·토큰·실측 보고서를 소스에 포함하지 않으며 Commit/Push는 별도 승인 사항이다.

## Step 7-B2-D 저장 응답 재검증 및 다음 호출 계획

추가 OAuth·시세 호출 없이 C3의 정제된 저장 보고서를 B1으로 다시 파싱했다.
600행 모두 검증 사유가 없으며 엄격한 날짜 내림차순, 중복 0건이다.
최신 거래일은 2026-10-27, 가장 오래된 거래일은 2024-05-08이다.
요청 기준일 및 실제 수신일 이후 거래일은 없고 OHLC 관계도 일관된다.
거래대금은 별도 Decimal 계산 `3162206 * 1000000 = 3162206000000`과 일치했다.
이는 저장 값의 단위 환산 검증이며 실제 시장 집계·원 단위 정밀도의 독립 확증은 아니다.

2026-10-28 공식 공개 포털을 다시 읽어 다음 문구를 확인했다.
[ka10081 공식 문서](https://openapi.kiwoom.com/m/guide/apiguide/07/ka10081)는
cur_prc를 현재가(원), trde_prica를 거래대금(백만원), 요청 upd_stkpc_tp=0을 원주가,
1을 수정주가로 설명한다. 일봉의 cur_prc는 종가에 대응하는 후보로 해석할 수 있지만,
포털 표만으로 해당 모의 응답의 정규장 확정 종가·세션 집계 범위를 증명할 수는 없다.
따라서 `cur_prc_candidate_not_verified_final_close`를 유지한다.
원주가 0도 과거 응답의 사후 정정 부재나 D0 당시 가용성을 보장하지 않는다.
행의 upd_stkpc_tp/upd_rt 빈 문자열을 요청 옵션의 대체 증거로 사용하지 않는다.

| HTS 대조 항목 | 저장된 2026-10-27 값 | 남은 확인 |
| --- | --- | --- |
| 종목·시장·거래일 | 삼성전자 005930, KRX 코드, 2026-10-27 | 같은 시장·일자·세션 조건 선택 |
| 시가 / 고가 / 저가 | 222,000 / 225,500 / 220,000원 | 원주가 차트의 동일 OHLC인지 확인 |
| cur_prc | 225,000원 | HTS 종가와 일치 및 마감 확정·정정 기준 확인 |
| 거래량 | 14,196,879주 | 동일 세션 합계·시간외 포함 여부 확인 |
| 거래대금 | 3,162,206백만원 | 동일 집계 범위와 표시 단위·절사/반올림 확인 |
| 수정 설정 | 요청 0, 행 사건·비율 빈값 | HTS 수정주가 해제 조건 및 기업행사 반영 차이 확인 |
| 자료 원천 | 모의 서버 | 실전 시장 원천과 동일하다고 가정하지 않음 |

관측 가용 시각은 2026-10-28 16:53:31.765443 KST이다. 이때 조회한 과거 행을
그 거래일의 D0 선정 당시 이미 알았던 자료로 소급하지 않는다. 기존 D0 스냅샷은
수정하지 않으며, 단위·세션·마감 확정·D0 기준 호환 증거가 없으면 추적 어댑터는
보류한다. 이번 CLI는 관측을 생성·저장하지 않았고 자동 D0·D+1·D+2 연동도 하지 않았다.

### SK하이닉스 000660 계획 — 아직 실행하지 않음

별도 실측 승인 후에만 모의 서버, ka10081, 000660 1종목, 기준일 20261027,
원주가 0, 검토한 CA 번들 및 trust_env=False로 실행한다. 유효 환경 토큰이 있으면
OAuth 0회, 없으면 명시 승인된 OAuth 최대 1회, 일봉 최대 1회다.
CA 해시·환경을 실행 직전 재확인하고 인증 또는 일봉 오류 시 즉시 종료한다.
재시도·연속조회·실전 전환·다른 종목·운영 DB 기록·D0 수정은 하지 않는다.

```powershell
# 아래는 별도 승인 후 실행할 명령이며 Step 7-B2-D에서는 실행하지 않음.
python -m kiwoom_collector.daily_diagnostic --real --authenticate --environment mock --api-id ka10081 --stock-code 000660 --base-date 20261027 --adjustment 0 --ca-bundle data/home-validation-20261028/requests-ca.pem
```

결과는 삼성전자와 동일하게 요청·수신 시각, HTTP/API 코드, 날짜·정렬·중복,
OHLC 후보·원문 부호·거래량·거래대금·기업행사·페이지 상태·B1 호환성을 확인한다.
실제 자료와 CA PEM은 Git 제외 경로에만 보존한다.

Step 7-B2-D 보존 전 재검증: 전체 **688개 통과, 실패 0·오류 0·스킵 0,
85.465초, 종료 코드 0**. 저장 보고서 재파싱과 공식 문서 확인만 수행했으며,
추가 OAuth·일봉 실측 및 D0·D+1·D+2 자동 연동은 수행하지 않았다.
