# C12-AS — 독립 KRX 일별 증분 갱신

작업일: 2026-11-04 KST. 공식 개발 루트: `C:\Users\dada1\Documents\ARVIS\stock1-dev`.
브랜치: `integration/home-hospital-c12-20261103`. HEAD: `ac0e8655db12f8b9d6b03129a1fff44032c02ccc`.
실행 셸의 시작 폴더는 Downloads였으나 개발·수집·검증 명령은 공식 루트를 명시하여 실행했다.
기존 미커밋·미추적 AN~AR-2 결과를 유지했다. Git Commit/Push/Merge/Reset/Clean, 스케줄러 등록, 운영 배포는 하지 않았다.

## 실행 방법

아래 예시는 공식 루트에서 실행한다. Python 3.11 이상과 `requirements-krx-as-windows.txt`의 의존성이 필요하다.
실제 검증에는 이미 requests/python-dotenv/truststore가 설치된
`C:\Users\dada1\Downloads\stock1\.venv\Scripts\python.exe`를 `-B` 옵션으로 사용했다.
해당 가상환경에는 패키지를 설치하거나 파일을 쓰지 않았다. 시스템 Python에는 requests/dotenv가 없고 `.venv-home`에는 truststore가 없다.

```powershell
Set-Location -LiteralPath 'C:\Users\dada1\Documents\ARVIS\stock1-dev'
$py = 'C:\Users\dada1\Downloads\stock1\.venv\Scripts\python.exe'

# 조회 전용: API 호출·DB 생성 없음
& $py -B -m kiwoom_collector.krx_incremental

# 실제 갱신: 기본 종료일은 KST 어제
& $py -B -m kiwoom_collector.krx_incremental --execute

# 날짜 지정, 실패·누락 날짜는 다음 실행에서도 재시도
& $py -B -m kiwoom_collector.krx_incremental --execute --end 2026-11-03

# 과거 AP 이관 당시 빈 응답 UNKNOWN 날짜까지 재시도
& $py -B -m kiwoom_collector.krx_incremental --execute --retry-history

# 일별 저장과 별도 주기로 마스터만 갱신
& $py -B -m kiwoom_collector.krx_incremental --execute --master-only --master-date 2026-11-03

# ADR/HTML 강제 재생성; 완료 날짜의 API 요청은 생략
& $py -B -m kiwoom_collector.krx_incremental --execute --rebuild-adr
```

PowerShell 진입점도 제공한다. 실행 정책을 영구 변경하지 않고 현재 자식 프로세스에만 옵션을 적용하는 예:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_krx_incremental.ps1 -Python $py -Execute -End 2026-11-03
```

스크립트는 자신의 위치로 프로젝트 루트를 결정하고 종료 코드를 전달한다. 성공 0, 수집·검증·ADR 실패 2.
빈 응답과 당일 보류는 미완료 상태이며 성공 코드라도 데이터 완료를 의미하지 않는다. `summary.json`의 날짜별 상태를 확인한다.
`--max-calls` 기본값 120(실행별), 간격 기본 1.1초. 일시 오류는 기존 Backfill의 최대 3회 재시도·지수 대기를 사용한다.
401/403/429/TLS 문제와 요청 예산 소진은 중단하며 다음 실행에서 다시 확인한다.

## 인증키

로딩 우선순위: 현재 프로세스의 `KRX_AUTH_KEY`, 다음으로
`C:\Users\dada1\Documents\ARVIS\stock1-dev-private\.env.krx.local`.
`--env-file`은 프로젝트 내부의 전용 파일 또는 위 전용 private 경로만 허용한다.
일반 `.env`, Kiwoom OAuth/토큰 캐시, Telegram 설정은 읽지 않는다.

확인 당시 private 파일의 `KRX_AUTH_KEY`는 비어 있었다. 기존
`C:\Users\dada1\Downloads\stock1\.env.krx.local`에는 비어 있지 않은 키가 있었다.
실제 API 검증에서는 기존 전용 파일을 읽고 자식 프로세스 환경으로만 전달했다. 인증 파일은 변경하지 않았다.
따라서 다음 수동 실행 전에는 **로컬에서 private 전용 파일에 키를 설정하거나 현재 프로세스의 환경변수로 전달해야 한다**.
인증키 값을 채팅·로그·명령행 인수·Git에 넣지 않는다. 전용 private 폴더의 기존 접근 제한은 유지한다.

## 저장·완료·재시도 정책

- 원본 seed: `diagnostics/krx-daily/ap2-20251001-20261030.db`. 읽기 전용으로 열고 SQLite online backup으로 최초 증분 DB를 생성한다.
- 증분 DB: **`diagnostics/krx-daily/as-incremental.db`**. 운영 DB와 경로가 분리된다.
- AP checkpoint를 SQLite backup으로 이관한 `diagnostics/krx-as/progress.db`에 날짜별 출처·검증 상태를 보존한다.
- 마지막 양 시장 정상 날짜 이후의 평일과 AS 미완료 날짜를 수집한다. 기존 seed의 실패·부분 누락도 재시도한다.
  seed의 과거 양 시장 빈 응답 22개 UNKNOWN 날짜는 `--retry-history`로 명시적으로 다시 요청한다.
- 완료 판정은 checkpoint 문구만 믿지 않고 양 시장 snapshot, 원문/정규화 건수와 오류 유무를 확인한다.
- 완료 날짜는 HTTP 호출 전에 제외한다. 과거 완료 날짜의 수정본 자동 덮어쓰기는 지원하지 않는다.
- 실행별 새 receipt cache를 사용하므로 이전 실행의 빈 응답·실패가 재시도를 막지 않는다.
- KST 당일·미래 날짜는 수집하지 않는다. 당일 확정 시각을 추측하지 않고 다음 날부터 조회하는 보수적 정책이다.
  `--end` 당일은 보류, 미래 종료일은 거부한다. 당일 데이터가 필요하면 공식 확정 제공 근거를 확인한 후 별도 정책 변경이 필요하다.
- 주말은 비거래일로 구분한다. 평일 빈 응답은 휴장일로 확정하지 않고 UNKNOWN/미완료로 둔다.
- 두 시장 일별 응답과 해당 기준일 마스터를 모두 받는다. 기존 parser/normalizer/DailyStore로 날짜·시장·식별자·OHLC·숫자를 검증한다.
- 일별 종목 집합과 날짜별 마스터 집합이 정확히 같아야 하며, 숫자 필수값 누락, 양 시장 코드 중복,
  시장 전체 미거래 상태, 직전 관측 대비 5% 초과 종목 수 감소는 보류한다. 정상 거래일의 대규모 변경도 수동 검토 대상이다.
- 가격 미형성·기준가격/시가총액 불일치 등 기존 품질 이슈는 원문·issues_json에 보존한다.
  식별 불일치·가격 모순·필수값 누락은 거부한다. ADR 적격성은 기존 AQ 정책으로 별도 평가한다.
- 날짜별 임시 `as-stage-*.db`에서 양 시장 검증을 마친 뒤 하나의 SQLite 트랜잭션으로 증분 DB에 반영한다.
  한 시장만 성공하거나 반영 중 실패하면 두 시장 모두 게시되지 않는다. 이전 날짜/원본 revision은 보존한다.
- OS 파일 잠금으로 중복 실행을 막는다. 프로세스 종료 시 잠금이 풀리며 lock 파일을 지울 필요가 없다.

독립 마스터 갱신은 실행 폴더의 `master-YYYY-MM-DD.json` 및 공식 원문 receipt에 보관한다.
기존 AO DB·종목명 매칭·원본 테마 DB를 덮어쓰지 않는다. 마스터 API에는 응답 기준일 필드가 없으므로
기존 `date_basis=request_basDd_no_response_date_field` 출처 한계도 그대로 기록한다.

## ADR·HTML

일별 저장 성공 시 기존 AQ의 aggregate/windowed/rolling과 HTML renderer를 재사용한다.
증분 경로에 한정된 입력을 허용하고 고정 AP 기간 대신 실제 데이터의 최저~최고 날짜로 계산한다.
KOSPI/KOSDAQ/COMBINED, 보통주 기본 계열과 UNKNOWN 참고 계열을 유지한다.
20일 상승 수 합계 / 하락 수 합계 × 100이며, 미확인 거래일·누락을 임의로 건너뛰지 않는다.
원본 금액 단위 미확인·잠정 종목 범위·관측 기반 달력이라는 기존 한계를 유지하여 수치는 provisional이다.

출력: `diagnostics/krx-aq/as-실행ID/adr20.db`, `adr20.json`, `adr20.html`.
현재 결과 포인터: `diagnostics/krx-as/latest-adr.json`.
현재 원본 snapshot 지문과 포인터가 다르면 재실행에서 ADR을 재시도한다. 실패한 산출물로 기존 정상 포인터를 덮어쓰지 않는다.
최신 실행 요약은 `diagnostics/krx-as/latest-run.json`, 실행별 원본 로그는 `krx-ap2/as-실행ID`에 남긴다.

## 실제 실행 결과

| 실행 ID | 목적 | API 요청 | 신규 행 | 종료 코드 |
|---|---|---:|---:|---:|
| 20261104-145858-f7bbcfc5 | 11/02~11/03 증분 + ADR | 8 | 5,535 | 0 |
| 20261104-150126-1706199d | 동일 범위 재실행 | 0 | 0 | 0 |
| 20261104-150218-01126aff | 11/03 마스터 단독 갱신 | 2 | 0 | 0 |

| 기준일 | KOSPI | KOSDAQ | 합계 |
|---|---:|---:|---:|
| 2026-11-02 | 939 | 1,828 | 2,767 |
| 2026-11-03 | 939 | 1,829 | 2,768 |

최종 증분 DB: **263거래일, 728,201행**, 2025-10-01~2026-11-03, **1,898,090,496바이트**.
기존 seed의 261거래일·722,666행과 snapshot 포인터는 유지했다. integrity_check=ok, foreign_key 위반 0.
최신 마스터 단독 갱신: 2026-11-03, 2,768종목. 기존 AO DB와 테마 매칭 결과는 변경하지 않았다.

2026-11-03 보통주 ADR20:

| 시장 | 상승 합계 | 하락 합계 | ADR20 |
|---|---:|---:|---:|
| KOSPI | 5,972 | 9,066 | 65.8724906243 |
| KOSDAQ | 12,275 | 19,628 | 62.5382107194 |
| 통합 | 18,247 | 28,694 | 63.5916916429 |

최신 HTML: `diagnostics/krx-aq/as-20261104-150126-1706199d/adr20.html`.
원래 AQ/AR-2 HTML은 보존되어 있다.

## 검증

합성·회귀 테스트와 실제 DB 검증은 구분한다. API 인증·실제 수집 성공은 위 10회 호출 결과이며 합성 테스트의 통과로 대체하지 않는다.

```powershell
# 합성 및 기존 KRX 회귀: API 호출 없음
& $py -B -m unittest tests.test_krx_incremental tests.test_krx_daily tests.test_krx_backfill tests.test_krx_master tests.test_krx_openapi tests.test_krx_adr -q

# 실제 DB 읽기 전용 검증: HTTP 호출 없음
$env:STOCK1_AS_ACTUAL = '1'
& $py -B -m unittest tests.test_krx_incremental_actual -q
Remove-Item Env:STOCK1_AS_ACTUAL
```

합성·회귀 **88개**, 실제 DB 검증 **5개**, 총 **93개**.
신규 거래일, 중복 방지, 주말·당일 보류, 빈 응답 후 재시작, API 일시 오류 재시도,
기준일 불일치, 마스터 누락·교차 시장 중복, 필수 숫자 누락, 양 시장 원자적 rollback,
독립 마스터 갱신, SQLite backup·무결성, 실행 잠금, ADR snapshot 지문을 검증한다.
실제 DB 검증은 seed 이력 키·포인터 보존, 양 시장 실제 origin, 무결성, 독립 SQL의 최근 20일 합계·Decimal 비율과 HTML 최신일을 확인한다.
기존 파일 전체 해시 비교 결과는 `diagnostics/krx-as/verification.json`에 기록한다.

## 변경 파일과 다음 단계 조건

신규: `kiwoom_collector/krx_incremental.py`, `scripts/run_krx_incremental.ps1`,
`tests/test_krx_incremental.py`, `tests/test_krx_incremental_actual.py`, `requirements-krx-as-windows.txt`, 본 문서.
기존 변경: `krx_openapi.py`(전용 private 인증 파일 경로 허용), `krx_adr.py`(고정된 AS 입력 경로·동적 기간 허용).
Collector 진입점·스케줄러·Telegram·운영 DB·원본 테마 DB 연결은 추가하지 않았다.

다음 수동 실행 전: private 인증키 또는 프로세스 환경변수 설정. 독립 영구 Python 환경이 필요하면 공식 루트에 별도 venv를 만들고 전용 requirements를 설치한다.
병원 적용 전: 병원 PC의 TLS·API 권한·출력 경로·파일 권한 검증, 공식 휴장일 근거와 당일 확정 제공 정책 검토,
실행 주기/실패 알림/로그 보존·디스크 용량 정책 확정이 필요하다. 과거 빈 평일과 ADR 종목 범위는 아직 잠정이다.
대시보드·C14 소비 경로는 여전히 기존 AP 자료를 참조할 수 있으므로 새 증분 DB로 전환할 때 별도 읽기 전용 연결 검증이 필요하다.
이번 단계에서는 병원 실전 적용 및 자동 실행 등록을 하지 않는다.
