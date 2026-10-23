# 수동 1회 TOP20 순위·시세 통합 테스트

`quote-integrated-test`는 mock 환경에서만 순위 조회 후 같은 신규 collection_id의
시세를 수집하고 종료합니다. 정기 스케줄러를 시작하거나 등록하지 않습니다.
실제 API 실행은 별도 승인 후 진행해야 합니다. 최초 구현은 가상 HTTP로 검증했고,
2026-10-23 별도 승인된 모의서버 실측 결과는 아래에 기록했습니다.
요청 간격 보완 이후의 검증은 오프라인으로만 수행합니다.

## 사용법

프로젝트 폴더의 PowerShell에서 새 폴더·새 DB를 지정합니다.
현재 PC에서는 기존 .venv 실행 파일 대신 확인된 Python 3.11 경로를 사용합니다.

```powershell
$python = 'C:\Users\Public\Documents\ESTsoft\CreatorTemp\stock1-python-3.11.9\python.exe'
$verifyDir = Join-Path $env:TEMP ('stock1-mock-integration-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $verifyDir | Out-Null
$testDb = Join-Path $verifyDir 'integration.db'

& $python -B -m kiwoom_collector quote-integrated-test --environment mock --db $testDb --qry-tp 1 --request-spacing 1.2
$LASTEXITCODE
```

- --db 필수: 부모 폴더가 존재하는 신규 .db/.sqlite/.sqlite3 파일만 허용합니다.
- 기본 운영 DB 경로, 기존 파일(빈 DB 포함), 심볼릭 링크, 잔존 WAL/SHM/journal/API 잠금 파일은 거부합니다.
- 파일은 xb로 독점 생성하므로 동시 실행/경로 경합에서도 기존 파일을 Store로 열지 않습니다.
- --environment mock 필수. 실제 credentials.environment와 API 주소도 mock이어야 합니다.
- --qry-tp는 기본 1이며 다른 값은 거부합니다. --request-spacing은 기본 1.2초, 최소 허용값 1.1초입니다.
- --schedule, --test-now, --collection-id, --recollect 등 다른 실행 경로의 인자는 거부합니다.
- 실패해도 생성한 DB나 잠금 파일을 삭제하지 않습니다. 같은 경로를 재실행하지 않습니다.

프로세스 환경변수가 .env보다 우선합니다. 이 명령은 .env를 변경하거나
real 설정을 mock으로 강제 덮어쓰지 않습니다. mock API 주소 검증은 키의 실제 유효성을 보장하지 않습니다.
다른 DB를 사용하는 수집 프로그램은 실행 잠금을 공유하지 않으므로 같은 계정으로 동시에 실행하지 마세요.
위 예시 자체는 CA 설정을 바꾸지 않습니다. Norton HTTPS 검사가 있는 PC에서는 아래의
검증된 로컬 CA 번들을 실행 자식 프로세스에만 적용하는 별도 실행 환경이 필요합니다.

## 요청 및 저장

- 같은 IntegratedQuoteClient/세션/토큰을 유지합니다.
- 토큰 발급 최대 1회, 순위 ka00198 최대 1회, 시세 ka10095 최대 10회입니다.
- 정상 20종목/19종목은 인증 1 + 순위 1 + 시세 10 = HTTP 시도 12회입니다.
- 순위 fetch_once(single_request=True)를 사용합니다. 연속조회가 필요하면 후속 요청 없이 실패합니다.
- 인증/HTTP 자동 재시도·토큰 재발급·과거 회차 검색·자동 재실행이 없습니다.
- 순위·시세 모두 test_mode=1, scheduled_at=NULL입니다. 운영 예정 시각을 만들어 넣지 않습니다.
- 공통 시세 분할·claim·저장·잠금 로직을 재사용하되 integrated_quote_runs로 실행 상태를 구분합니다.
- 신규 테스트 DB에는 테스트용 시세 테이블만 생성합니다. 운영용 test_mode=0 스키마 확장 함수는 호출하지 않습니다.

수집 창은 120초입니다. 새 HTTP 요청 전에는 30초 이상 예산을 요구하고,
공통 시세 실행부는 기존 QuotePolicy의 보수적인 요청 예산도 확인합니다.
connect/read timeout은 기존 5/20초이며, 응답 전체의 절대 마감은 아닙니다.

## 결과와 중단

수집 흐름이 시작되면 stdout에 JSON 요약 하나를 출력합니다.
사전 검증 또는 초기화 실패는 안전한 오류 메시지와 비정상 종료 코드로 표시합니다.

- collection_id, environment, test_mode, status, reason
- target_count, success_count, partial_count, failed_count, uncertain_count, uncollected_count
- counts_verified: DB에서 집계할 수 있었는지 여부. 집계 불가 값은 0 대신 null입니다.
- request_counts: auth/ranking/quotes HTTP 시도 수 (전송 불명 시도 포함)
- http_attempt_count, elapsed_seconds: 전체 시도 수와 정리 작업을 포함한 총 소요 시간

종목 수는 성공/부분 성공/실패/전송 불명/미수집으로 중복 없이 나눕니다.
부분 필드가 있는 관측을 정상 성공으로 합산하지 않습니다. 사전에 claim했지만 전송하지 않은
deadline_before_send 행은 미수집으로 집계하며 시세 값은 생성하지 않습니다.
기존 원본 문자열·정규화 상태는 DB에 보존하고 응답 본문·서버 메시지·인증값은 요약에 출력하지 않습니다.

순위 실패/빈 응답, HTTP 401/403/429, 인증·호출 제한·모의 미지원,
시간 예산 소진, 저장 실패, 전송 불명에서 후속 요청을 중단합니다.
일부 종목 누락은 실패로 저장하고 다른 묶음을 진행할 수 있습니다.

timeout/전송 예외와 Ctrl+C는 공통 실행부에서 uncertain 및 DB 잠금을 보존합니다.
강제 프로세스 종료는 running을 남길 수 있으며 종료 요약을 출력할 수 없습니다.
이미 저장한 정상 관측은 보존합니다. 잔존 잠금이나 상태는 임의로 해제·수정하지 않습니다.
복구 전 DB를 읽기 전용으로 조사하고, 추가 API 호출에는 별도 승인이 필요합니다.

종료 코드는 모든 대상이 정상 완료된 경우 0, 부분 성공/빈 결과/중단/실패는 1입니다.
잘못된 CLI 인자는 argparse의 종료 코드 2를 사용합니다.

## 범위와 오프라인 검증

기존 collect --test-now, quote-top20-test, 하루 10회 일정과 Sector Radar는 유지됩니다.
정기 운영 시세 경로는 기존 test_mode=0과 scheduled_at 검증을 유지합니다.

```powershell
python -m unittest discover -s tests -p test_integrated_test.py -v
python -m unittest discover -s tests -v
```

## 실제 모의서버 1회 통합 검증 기록 (2026-10-23)

기준 커밋 fd39e5d 위의 로컬 통합 테스트 구현으로 실행했습니다. 최초 시도는 TLS 인증서
검증 실패로 인증 단계에서 중단되어 순위·시세 요청은 0회였습니다. Norton 신뢰 체인 확인 후
별도 CA 번들을 테스트 자식 프로세스에만 적용해 승인된 재시험을 정확히 한 번 수행했습니다.
다음 수치는 요청 간격 보완 **전**의 실측이며 보완 코드의 실제 API 재검증 결과가 아닙니다.

| 항목 | 실측 결과 |
|---|---|
| 환경 / 조회 유형 | mock / qry_tp=1 |
| OAuth | 1회, HTTP 200, return_code=0, 토큰 발급 성공 |
| ka00198 | 1회, HTTP 200, return_code=0, 순위 20종목 |
| ka10095 | 2종목씩 10회, 모두 HTTP 200 / return_code=0, 시세 20종목 |
| 총 요청 | OAuth 포함 12회, 재시도 없음 |
| 종목별 결과 | 정상 20, 부분 성공·실패·전송 불명·미수집 각 0 |
| 시간 | 인증 HTTP 약 0.422초, 순위 HTTP 약 0.016초, 시세 구간 약 9.957초(내부 대기 포함) |
| 전체 소요 | CLI 집계 12.235초, 프로세스 전체 12.640초 |
| 저장 검증 | integrity_check=ok, 외래 키 위반 0건, 잔여 DB 잠금 0건 |

collection_id는 `85a8edf4-1d9f-4716-8ca3-4d6b3a82b7bc`입니다. 순위 20행과 시세 20행의
collection_id·순위·종목코드를 조인하여 전부 일치함을 확인했습니다. 순위와 시세는 모두
test_mode=1, environment=mock이며 scheduled_at=NULL입니다. 운영 DB와 이전 실패 DB를
재사용하지 않고 새 테스트 DB에 저장했습니다. 원본 설정·DB·소스·CA 관련 보호 파일 97개는
실행 전후 해시·크기·수정 시각이 동일했습니다. 인증정보·원문 서버 메시지는 문서에 옮기지 않습니다.

현재가·등락률·거래량·거래대금·시고저가·dt·cntr_tm을 20건 모두 수집했습니다.
현재가의 원문 접두 부호는 + 13건, - 7건이었습니다. 가격 부호의 경제적 의미를 확정하거나
절댓값으로 변환하지 않았고 price_status=unverified_sign_semantics 및 정규화 가격 NULL을 유지합니다.
거래대금은 기존 백만원→원 환산 정책을 적용했지만 실제 단위의 독립 대조는 하지 않았습니다.
필드 수집 성공은 부호·단위 의미의 검증 완료를 뜻하지 않습니다. API dt는 모두 20261023,
cntr_tm은 14:51:06~14:53:20이며 체결시각을 요청 시각으로 해석하지 않습니다.

## 요청 시작 간격 보완

실측에서 설정 1.1초에도 시작 간격 일부가 약 1.094~1.098초로 기록됐습니다.
당시 Python 3.11 Windows의 time.monotonic은 GetTickCount64, 해상도 15.625ms였고,
단일 sleep 뒤 재확인하지 않는 경로가 있었습니다. 실측에는 소스 밖의 간격·타이밍 보호 도구도
사용됐으므로 모든 원인을 제품 코드 하나로 단정하지 않습니다.

이후 인증·순위·ka10007·ka10095 전송을 공통 KiwoomClient._request로 모았습니다.
기본 간격은 1.2초로 늘리고 최소 허용 1.1초 옵션은 유지합니다. 고해상도 perf_counter
(해당 Windows에서 QueryPerformanceCounter)를 사용해 이전 전송 시작+간격을 계산하고,
대기에서 일찍 깨어나도 목표 시각을 다시 확인합니다. 시세 마감 검사와 요청 시각 기록은
대기 후, 실제 Session.post 직전에 실행합니다. 인증·순위·시세가 같은 클라이언트의 간격 상태를 공유합니다.

이 정책은 mock/real 공통의 보수적인 로컬 정책이며 공식 호출 한도를 새로 정의하지 않습니다.
기존 재시도 허용 경로의 횟수·지수 대기는 유지하고, 통합 테스트의 재시도 금지도 유지합니다.
하루 10회 시간표·예정 시각·DB 스키마·레이더 선정 기준은 변경하지 않습니다.
다른 DB/프로세스의 같은 계정 호출까지 직렬화하거나 서버 도착 간격을 보장하지는 않습니다.

오프라인 검사는 가상 전송 함수의 실제 진입 시각, 조기 기상, 초과 대기, 느린 응답,
인증→순위→시세 전환, mock/real, 대기 후 마감, 명시적 1.1초와 새 기본값을 검증합니다.
실제 perf_counter와 sleep을 사용하는 가상 HTTP 검사도 포함합니다. 보완된 1.2초 정책의
실제 모의 API 간격은 이번 변경에서 재측정하지 않았습니다.

2026-10-23 Windows / Python 3.11.9의 격리 소스 복사본에서 전체 **291개 테스트가 모두 통과**했습니다
(unittest 74.926초, skipped 없음). 기존 280개를 유지하고 요청 간격 10개 및 통합 흐름의
명시적 1.1초 검사 1개를 추가했습니다. 운영·테스트 분리, 일정 보호, 마이그레이션, 부분 실패·중단,
기존 CLI·레이더·대시보드·브라우저 작업실 회귀를 포함합니다. 테스트 DB·생성물·브라우저 프로필은
임시 경로에 두고 외부 네트워크와 원본 DB 접근을 차단했습니다. 검증 중 발견한 인증 직후
시간 예산 소진 사례는 헤더 생성 전과 간격 대기 후의 마감 검사로 보완하고 전체 검사를 재실행했습니다.

## Norton HTTPS 검사와 로컬 CA

관측된 인증서 발급자는 Norton Web/Mail Shield Root였고, Windows 저장소의 공개 루트와
실제 mockapi.kiwoom.com TLS 체인의 루트가 지문·DER 바이트까지 일치함을 확인했습니다.
확인된 로컬 루트의 SHA-1 식별 지문은 `6E40EC506C5BCF14FBF58B6B84A401524B9123AA`입니다.
이 지문은 해당 PC의 검증 기록이며 다른 PC의 인증서를 자동 신뢰하는 기준으로 사용하지 않습니다.

certifi 원본의 전체 117개 CA와 확인한 공개 Norton 루트 1개를 합친 별도 PEM을 Git 밖에
보관했습니다. 개인키 내보내기나 시스템 루트 설치는 하지 않았습니다. 테스트 자식 프로세스에만
REQUESTS_CA_BUNDLE을 지정했으며 verify=False, 전역 환경변수 변경, .env 수정은 사용하지 않았습니다.
임시 폴더의 번들은 정리될 수 있으므로 다음 승인된 실행 전에 존재·해시·인증서 출처를 재확인해야 합니다.
번들·인증서·실행 DB·원문 응답·로그는 Git에 넣지 않습니다. Norton 갱신으로 루트가 바뀌면
기존 번들을 임의로 재사용하거나 새 루트를 무조건 추가하지 않습니다.

남은 검증 범위: 보완 정책의 실제 요청 간격, 반복·장시간 장중 부하, 실전 응답, 다른 프로그램과의
계정 단위 경합, 가격 부호와 거래대금 단위의 독립 대조입니다. 실제 API 재실행에는 별도 승인이 필요합니다.
