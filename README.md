# 키움 REST API TOP20 수집 및 독립 시세 조회

기존 [0198] TOP20 순위 수집과 별도로, 성공 회차의 종목을 현재 시점에 조회·저장하는 시세 CLI를 제공합니다. 모든 새 시세 관측은 `test_mode=1`로 구분합니다. 정기 시세 자동 수집과 주문 기능은 없습니다.

## 시세 확장 검증 결과

2026-10-22 사용자 보고 기준, Windows 병원 PC의 실제 모의서버에서 다음 검증을 완료했습니다. 개발 환경에서 수행한 가상 HTTP 테스트와 사용자의 실제 모의 검증을 구분합니다.

- `ka10007` 단일 종목 시세 조회 및 DB 저장 성공.
- `ka10095` 2종목 시세 조회 및 DB 저장 성공.
- 기존 2종목 성공을 보존한 `quote-top20-test`에서 나머지 18종목을 9회 요청. 출력상 미전송 0건, 종목별 성공, issues 없음.

이는 해당 환경·종목·시점의 검증 결과입니다. ka10095의 공식 최대 종목 수와 가격 부호의 구체적 기준은 여전히 미확인입니다. 요청당 최대 2종목 정책을 유지하고, 원본 가격 문자열은 보존하며 절댓값 가격 정규화는 하지 않습니다.

## 추가 CLI

| 명령 | 동작 |
| --- | --- |
| `quote-test --collection-id UUID --limit 1` | ka10007로 기존 회차의 상위 1~2종목 조회·저장 |
| `quote-batch-test --collection-id UUID --limit 2` | ka10095로 상위 2종목을 한 요청으로 조회·저장 |
| `quote-top20-test --collection-id UUID --dry-run` | TOP20을 한 번 고정하고 기존 시도를 제외한 최대 10개 요청 계획 출력. 키·API 호출·운영 DB 변경 없음 |
| `quote-top20-test --collection-id UUID` | 최대 2종목씩 순차 조회·저장. 요청 시작 간 최소 1.1초, 429·인증·호출 제한 오류 시 후속 호출 중단 |

```powershell
# UUID를 같은 mock 환경의 성공한 ka00198 회차로 바꿉니다.
.\.venv\Scripts\python.exe -m kiwoom_collector quote-top20-test `
  --collection-id "회차-UUID" --environment mock --db data/stock1.db `
  --request-spacing 1.1 --recollect never --dry-run

# 실제 API 호출 및 DB 마이그레이션·저장: 대상과 백업을 확인한 후 직접 실행합니다.
.\.venv\Scripts\python.exe -m kiwoom_collector quote-top20-test `
  --collection-id "회차-UUID" --environment mock --db data/stock1.db `
  --request-spacing 1.1 --recollect never
```

`--request-spacing`으로 간격을 더 늘릴 수 있습니다. `--recollect never`는 기존 시도를 생략하고, `failed`는 성공 이력이 없는 실패·중단 대상을, `all`은 기존 성공까지 새 관측 차수로 수집합니다. 어떤 옵션도 과거 성공 행을 덮어쓰지 않습니다. 결과 불명 `running`과 강제 종료로 남은 잠금은 자동 재전송하지 않습니다.

API 환경은 `KIWOOM_ENV`와 명령의 `--environment`가 일치해야 합니다. 모의 실패 시 실전으로 자동 전환하지 않습니다. 과거 순위 시각, 현재 시세의 API 시각, HTTP 시작·종료 시각은 별도로 보존하며 과거 시세로 소급 취급하지 않습니다.

## 시세 DB 변경 및 기존 수집과의 호환성

시세 명령을 명시적으로 실행할 때만 기존 DB를 트랜잭션으로 확장합니다. 기존 DB를 삭제·초기화하지 않으며, 구 시세 테이블의 제약·기본 키 확장이 필요한 경우 기존 행을 복사·보존하고 실패 시 전체 롤백합니다.

| 테이블/열 | 역할 |
| --- | --- |
| `quote_batches` | 순위 회차 FK, 환경·API·테스트 구분, 실행 상태·시각·명세 버전. TOP20의 고정 목록은 `plan_json` |
| `quote_requests` | 요청 묶음별 대상 코드, HTTP 시작·종료, 상태·응답 코드·허용 시장 필드·진단 |
| `quote_observations` | `(collection_id, rank)`로 기존 순위 참조. 시세 원본·정규화·API 시각·HTTP 시각, `request_id` FK와 재수집 차수 `attempt` |
| `quote_top20_locks` | 같은 DB·환경의 중복 실행 차단 |

관측 기본 키는 `(collection_id, rank, api_id, test_mode, attempt)`입니다. 기존 시세는 `attempt=0`으로 보존하고 요청 연결을 추정해 채우지 않습니다. 거래대금은 원본 백만원 단위를 보존하며, 분석용 원화 환산은 Decimal 문자열로 별도 저장합니다.

`collections`·`ranking_records`·종목 마스터·테마 분류와 감사 이력은 시세 확장으로 수정하지 않습니다. 기존 `collect`, `collect --test-now`, `collect --schedule`의 호출 경로에도 시세 조회를 추가하지 않았습니다. 고정 [0198] 일정은 기존 KST **09:20, 09:30, 09:40, 09:50, 10:00, 11:00, 12:00, 13:00, 14:00, 15:00** 그대로입니다.

상세 설명: [ka10007](docs/QUOTES.md), [ka10095](docs/BATCH_QUOTES.md), [TOP20 분할·재개·재수집](docs/TOP20_QUOTES.md), [DB](docs/DATABASE.md).

## 오프라인 회귀 테스트와 Git 제외 파일

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
git diff --check
```

가상 HTTP와 임시 DB로 기존 CLI·일정·테마 보존, 시세 실패·누락·부분 성공, 환경 분리, 중복·재개·재수집, 요청 간격, dry-run의 DB/WAL 무변경, 마이그레이션·롤백을 검증합니다. 실제 서버를 호출하는 시세 명령은 오프라인 테스트 명령과 별개입니다.

`.env`·실제 키·SQLite DB/WAL/SHM·`data/`·`data.zip`·생성 HTML/CSV·가상환경은 커밋하지 않습니다. `.env.example`에는 빈 키 자리만 유지합니다.

공식 GitHub의 확인된 명세로 **인증과 조회순위 1회 조회 프로그램을 구현**했습니다. 사용자의 Windows PC에서 모의투자 키로 인증·단일 조회가 성공했고 HTS [0198]과 반환 데이터가 일치했음을 확인했습니다. 이번 서버 선택 변경은 실제 API 호출 없이 오프라인 테스트로 검증했습니다. `collect`로 KST 평일 09:00~15:30 자동 수집과 SQLite 누적 저장을 지원합니다. 주문 기능은 없습니다.

## 확인 결과

| 용도 | API ID | 요청 |
| --- | --- | --- |
| 접근토큰 발급 | `au10001` | `POST /oauth2/token` |
| 국내 실시간종목조회순위 | `ka00198` | `POST /api/dostk/stkinfo` |

조회순위 API의 존재와 종목코드·종목명·순위·기준가 대비 등락률 필드를 확인했습니다. 공식 저장소에 HTS 화면번호와의 직접적인 대응 설명은 없지만, 사용자가 Windows PC에서 모의투자 조회 결과와 HTS [0198] 데이터의 일치를 확인했습니다. 근거 파일, 응답 필드 전체, 모호한 설명은 [API 명세 조사 기록](docs/API_SPEC.md)에 정리했습니다.

## 1. Windows에 Python 설치

Python 3.11 이상을 설치하고 설치 화면에서 `Add Python to PATH`를 선택합니다. 프로젝트 폴더에서 PowerShell을 열고 확인합니다.

```powershell
py -3 --version
```

## 2. 패키지 설치

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

가상환경 활성화나 PowerShell 실행 정책 변경은 필요 없습니다. 아래 명령은 프로젝트 폴더에서 실행합니다.

## 3. API 키 준비 및 .env 설정

키움 REST API 서비스에서 APP KEY와 SECRET KEY를 발급하고, 해당 서비스의 안내에 따라 사용 권한과 필요한 IP 등록을 완료합니다. 키 값은 채팅이나 소스코드에 넣지 않습니다.

`.env`가 없는 경우에만 복사합니다.

```powershell
Copy-Item .env.example .env
notepad .env
```

```dotenv
KIWOOM_ENV=mock
KIWOOM_APP_KEY=본인의_APP_KEY
KIWOOM_SECRET_KEY=본인의_SECRET_KEY
```

서버 선택은 다음과 같습니다. 미설정 시 `mock`이 기본값이며, 빈값이나 다른 값은 오류로 처리합니다.

| KIWOOM_ENV | 서버 | 사용할 키 |
| --- | --- | --- |
| `mock` | `https://mockapi.kiwoom.com` | 모의투자 APP KEY / SECRET KEY |
| `real` | `https://api.kiwoom.com` | 실전투자 APP KEY / SECRET KEY |

서버를 바꿀 때는 해당 환경의 키 두 개도 함께 변경하세요. 서버 주소를 Python 파일에서 직접 수정할 필요는 없습니다. 임의 주소 입력은 지원하지 않습니다. `.env.example`은 빈 키가 있는 예제이므로 실제 키를 넣지 마세요.

저장 후 메모장을 닫습니다. `.env`는 Git에서 제외되며 키와 토큰은 콘솔에 표시하지 않습니다. 같은 이름의 환경변수가 이미 있으면 `.env`보다 우선합니다.

## 4. 설정만 검사 — API 호출 없음

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector check-config
```

인자를 생략해도 설정 검사만 합니다. 선택한 환경과 서버 주소를 표시하고, 환경값 및 두 키의 존재를 검사합니다. 키의 유효성을 확인하거나 API를 호출하지 않습니다. 현재 키가 없는 상태에서는 누락된 변수 이름과 함께 오류 종료하는 것이 정상입니다.

## 5. 키 준비 후 인증 확인 — 실제 API 호출 발생

아래 단계부터는 사용자가 키를 준비한 뒤 실행합니다. 이번 변경의 개발 환경에서는 실제 API를 호출하지 않았습니다.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector auth
```

성공하면 인증 성공과 한국 시간 기준 토큰 만료 시각을 표시합니다. 토큰 값은 표시하거나 파일에 저장하지 않습니다.

## 6. 조회순위 한 번 수집 — 실제 API 호출 발생

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector once
```

별도로 `auth`를 먼저 실행할 필요는 없습니다. `once`가 자동 인증 후 `qry_tp="1"`로 조회합니다. `1`은 1분 기준 집계 구분으로, 매분 반복한다는 뜻이 아닙니다. 실행은 한 번의 수집 회차로 끝납니다. 연속조회와 오류 복구가 있으면 여러 HTTP 요청이 발생할 수 있습니다.

수집 시각은 한국 시간으로 표시합니다. 실제 `bigd_rank` 값이 1~20인 항목만 순위순으로 출력합니다. API 응답의 종목코드·종목명·기준가 대비 등락률·직전 기준 대비 등락률·기준 일자·기준 시간을 JSON 한 줄씩 표시합니다. 없는 필드는 생성하지 않습니다. 결과가 20건 미만이거나 비어 있으면 안내합니다. **0건 응답은 수집 성공 검증을 뜻하지 않습니다.**

다른 집계 구분을 선택할 수도 있습니다.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector once --qry-tp 2
```

`1`: 1분 / `2`: 10분 / `3`: 1시간 / `4`: 당일 누적 / `5`: 30초.

## 오류가 발생하면

- 설정 오류: `.env` 파일 이름과 두 변수 이름, 입력 여부를 확인합니다.
- 인증/권한 오류: `KIWOOM_ENV`에 맞는 모의/실전 키인지, 사용 권한·등록 IP 조건을 충족하는지 확인합니다. 키나 토큰을 공유하지 마세요.
- 네트워크 오류·호출 제한: 제한된 재시도 후 중단합니다. 짧은 시간에 명령을 연속 실행하지 마세요. 공식 호출 제한 수치는 아직 확정하지 않았습니다.
- 토큰 만료/무효: 공식 구현의 오류 코드 및 HTTP 401에 대해 한 번 재발급 후 재조회합니다.
- TLS 검증 오류: 인증서/회사 프록시 설정을 확인하세요. 인증서 검증을 끄지 않습니다.
- 순위 값 오류: 임의 순위를 만들지 않고 중단합니다. 특히 `bigd_rank=0`의 의미는 공식 설명이 모호하여 추가 확인이 필요합니다.

오류 종료 코드는 1이며, 사용자가 Ctrl+C로 중단하면 130입니다. 원문 서버 메시지에는 민감한 정보가 섞일 수 있어 출력하지 않고 HTTP 상태나 API 오류 코드만 표시합니다.

## 오프라인 테스트 — 키 및 API 호출 불필요

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

HTTP 세션을 모의 객체로 교체하여 인증·조회 요청 형식, 연속조회, 순위 선택, 토큰 복구, 네트워크·호출 제한 오류, 키 비노출을 검사합니다. 테스트 통과는 실제 인증/데이터 수신 성공을 의미하지 않습니다.

## 자동 수집과 저장

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector collect
```

- 기본 간격은 **300초(5분)**입니다. 장중 실행하면 즉시 첫 수집을 시작하고, 장외라면 다음 평일 09:00까지 기다립니다.
- 실행 시간은 **한국 시간 평일 09:00 이상 15:30 미만**입니다. 서버/PC의 지역 시간 설정과 관계없이 KST를 사용합니다. 15:30부터 새로운 회차는 시작하지 않지만 직전에 시작한 요청은 완료할 수 있습니다.
- 순차 실행하며 밀린 회차를 몰아서 호출하지 않습니다. 회차 소요 시간이 간격을 초과하면 완료 후 한 간격을 더 기다립니다. PC 절전·프로세스 종료 중에는 수집하지 않으며 자동 재실행/과거 데이터 복원도 하지 않습니다.
- API 실패는 콘솔과 수집 이력의 `error`에 남기고 다음 주기에 재시도합니다. 재시도·토큰 갱신은 기존 클라이언트를 재사용합니다. DB 쓰기 불능은 기록 유실을 막기 위해 프로그램을 중단합니다.
- Ctrl+C로 중단합니다. 진행 중인 회차는 `interrupted`로 기록하고 DB·HTTP 세션을 닫습니다. 비정상 강제 종료로 남은 `running`은 자동으로 성공 처리하지 않습니다.
- 결과가 20건 미만이면 받은 항목만 저장합니다. 0건은 `empty`로 구분하며 데이터 수집 성공으로 간주하지 않습니다.

간격은 초 단위로 변경합니다. 최소 60초로 제한하며 공식 요청 한도 수치를 의미하지 않습니다. 같은 키로 여러 수집기를 동시에 실행하면 호출 제한을 공유하므로 한 개만 실행하세요.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector collect --interval 60
```

`--qry-tp`는 API의 **조회 집계 구분**, `--interval`은 프로그램의 **수집 간격(초)**으로 서로 독립적입니다. 기본 집계는 기존 `once`와 같은 `1`입니다.

### 장외 테스트: 한 번 저장하고 종료

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector collect --test-now
.\.venv\Scripts\python.exe -m kiwoom_collector status
.\.venv\Scripts\python.exe -m kiwoom_collector latest
```

`--test-now`는 평일/주말/장중 시간 제한을 무시하고 **조회순위 HTTP 요청을 1회만** 실행합니다. 인증 토큰 발급은 별도 요청이므로 전체 HTTP 요청 수가 1회라는 뜻은 아닙니다. 인증 실패 시에는 조회를 시도할 수 없습니다. 테스트 모드의 인증·조회 모두 자동 재시도를 하지 않습니다. 연속조회가 필요하면 추가 호출 없이 오류로 기록하며 불완전한 결과를 종목 기록으로 저장하지 않습니다. 일반 `collect`와 기존 `once`의 재시도·연속조회 동작은 유지합니다.

DB의 `collections.test_mode=1`로 구분하며 일반 수집은 `0`입니다. 기존 DB는 시작할 때 열을 자동 추가하고 과거 이력은 `0`으로 유지합니다. 업그레이드 전 수집기를 종료하고 `data` 폴더를 백업하세요.

- 정상 결과: `success`, 받은 종목 저장 후 종료(종료 코드 0).
- 빈 결과: `empty`, 0건 및 응답 보존 후 종료(종료 코드 0). **당일 시장 데이터 수집 성공을 뜻하지 않습니다.**
- API/인증 오류: `error`, 안전한 오류 설명 저장 후 종료(종료 코드 1).
- Ctrl+C: `interrupted`, 종료 코드 130.
- 동일 초·환경·집계 구분의 이력이 이미 있으면 기존 중복 방지 규칙에 따라 호출/저장을 생략합니다. 잠시 후 재실행하세요.

`--interval`은 테스트 모드에서 사용하지 않습니다. `--qry-tp`와 `--db`는 그대로 지정할 수 있습니다. `status` 집계에는 테스트도 포함되며 테스트 회차 수를 별도로 표시합니다. `latest`도 테스트를 포함하며 `test_mode=True/False`를 표시합니다. 분석 시 운영 데이터만 사용하려면 `WHERE c.test_mode=0` 조건을 추가하세요.

테스트 모드는 모의투자 환경을 강제하지 않습니다. `.env`의 `KIWOOM_ENV=mock` 기본 정책은 유지되며, 현재 선택한 환경을 사용합니다. 장외 API는 빈 결과·오류·이전 기준 시각의 데이터를 반환할 수 있습니다. 응답을 임의로 채우거나 당일 데이터로 바꾸지 않습니다.

### 휴장일 처리 범위

**토·일요일만 자동 제외합니다. 공휴일·근로자의 날·거래소 임시휴장·개장 시간 변경·수능일 지연 개장은 자동 인식하지 않습니다.** 평일 휴장일에는 실행을 중단하거나 실행하지 마세요. 실행을 유지하면 API의 오류/빈 결과 또는 이전 기준 시각의 데이터가 저장될 수 있습니다. 서버가 반환한 `dt`·`tm`을 그대로 보존하므로 수집 시각과 API 기준 시각을 비교할 수 있습니다. 정상 HTTP 응답이라고 당일 장중 데이터임을 보장하지 않습니다. 거래소 공식 영업일 달력 연동은 아직 없습니다.

### 상태와 최근 종목 조회

다른 PowerShell 창에서 다음을 실행하면 수집 중에도 조회할 수 있습니다. API 키와 네트워크 연결 없이 실행 가능합니다.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector status
.\.venv\Scripts\python.exe -m kiwoom_collector latest
```

`status`는 DB 절대 경로, 최근 시도·데이터 수집 성공 시각, 누적 시도·기록 수, 상태별 회차 수를 표시합니다. `latest`는 최근 **완료된** 회차(`success` 또는 `empty`)의 환경·시각과 순위·종목코드·종목명을 표시합니다. 마지막 API 실패는 제외하며, 가장 최근 완료 회차가 빈 결과면 이전 성공 데이터로 대체하지 않습니다. 진행 중/오류 상태는 `status`로 확인하세요.

기본 DB는 실행 위치와 관계없이 **프로젝트 폴더의 `data/stock1.db`**입니다. 조회 명령에서 DB가 없으면 빈 DB를 초기화합니다. 별도 경로도 지정할 수 있으며 상대 경로는 현재 PowerShell 위치 기준입니다.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector collect --db data/stock1.db
.\.venv\Scripts\python.exe -m kiwoom_collector status --db data/stock1.db
```

`once`는 이전과 같이 콘솔 조회만 하며 DB에 저장하지 않습니다. 수집 스키마·원본 필드 보존 범위는 [DB 설명](docs/DATABASE.md)을 참고하세요. DB와 로그, `.env`는 Git에서 제외되며 프로그램은 로그 파일을 자동 생성하지 않습니다. DB에는 시장 데이터만 저장하더라도 개인 저장 파일이므로 공개 업로드하지 마세요.

## Windows PowerShell 실행 순서

최신 ZIP을 새 폴더에 풀고 기존 `.env`만 복사합니다. 기존 DB가 있다면 수집을 종료한 후 `data` 폴더도 복사하세요. 서버 환경과 키를 확인한 뒤 다음을 실행합니다.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m kiwoom_collector check-config
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m kiwoom_collector collect
```

다른 PowerShell 창에서 같은 폴더로 이동하여 `status`, `latest`를 실행하세요. 수집 창에서 Ctrl+C를 누르면 종료합니다. 섹터 분류·매매 신호·주문 기능은 포함하지 않습니다.

## 기존 Windows ZIP 프로젝트에 업데이트 적용

1. 실행 중인 프로그램을 종료합니다. 기존 프로젝트 폴더는 백업으로 보존합니다. `.env` 백업은 로컬에서만 보관하고 공유하지 마세요.
2. GitHub 저장소의 `main`에서 **Code → Download ZIP**을 선택해 새 폴더에 압축을 풉니다. 기존 폴더 위에 덮어쓰기보다 새 폴더 사용을 권장합니다.
3. 기존 폴더의 실제 `.env`만 새 프로젝트 폴더에 복사하고 `KIWOOM_ENV=mock` 한 줄을 추가합니다. 기존 모의투자 키 두 개는 그대로 둡니다. `.env.example`은 복사하지 않습니다. 이전 `.env.example`에 키를 넣었다면 키를 지우고 공유하지 마세요.
4. 새 폴더에서 PowerShell을 열어 가상환경을 새로 만들고 설치합니다. 이전 `.venv`는 복사하지 않습니다.

```powershell
py -3 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m kiwoom_collector check-config
```

5. 출력에 `mock (https://mockapi.kiwoom.com)`이 표시되는지 확인한 후 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector auth
.\.venv\Scripts\python.exe -m kiwoom_collector once
```

`auth`와 `once` 명령어는 이전과 같습니다. `once` 자체가 자동 인증합니다. 이전 `client.py`에서 서버 주소를 직접 바꾼 수정은 새 코드에 다시 적용하지 마세요.

기존 폴더를 그대로 유지해야 한다면 백업 후 새 ZIP의 `kiwoom_collector` 폴더와 `tests`, `README.md`, `docs`, `requirements.txt`, `.env.example`, `.gitignore`를 갱신하고, 기존 `.env`에 `KIWOOM_ENV=mock`을 추가하세요. `.env`와 `.venv`는 덮어쓰지 마세요. 두 방식 모두 사용자 환경변수가 설정되어 있으면 `.env`보다 우선합니다.


## 수집 검증 실패 원인 확인

이전 `검증 실패 후 종료` 메시지는 서로 다른 예외를 한데 묶었습니다. 기존 실패 이력만으로 정확한 필드나 DB 제약을 복구할 수는 없습니다. 최신 버전으로 업데이트한 뒤 한 번 재실행하면 다음처럼 구분됩니다.

| stage | 의미 | 표시 내용 |
| --- | --- | --- |
| `api_request` | 네트워크·인증·HTTP·API 업무 오류 | 안전한 오류 설명, 가능한 HTTP 상태/숫자 API 코드 |
| `response_validation` | JSON/목록/순위/시장 필드 자료형 불일치 | 실패 필드 경로, 예상·실제 자료형 또는 순위 조건 |
| `db_store` | SQLite 저장·무결성 제약 오류 | SQLite 숫자 오류 코드, 알려진 제약의 이름(중복 종목/순위 등) |

예를 들어 `selected[0].base_comp_chgr: expected=str actual=float`은 선택된 첫 종목의 해당 필드가 명세의 문자열 대신 숫자로 들어왔다는 뜻입니다. 이것은 **설명용 예시**이며 사용자의 실제 응답이 그렇다고 확인된 것은 아닙니다. `pages[0].item_inq_rank[0]`은 보존할 응답 첫 페이지의 첫 행을 의미합니다.

콘솔과 `status`에는 민감한 값을 제외한 안전 진단이 표시됩니다. `http_status`, 숫자형 `return_code`, 응답/목록 자료형, 행 수, 공식 필드별 자료형 분포, `source_stage`(검증이 발견된 위치)만 담습니다. API 키·접근토큰·헤더·종목 필드의 실제 값·서버 메시지 원문·알 수 없는 필드 이름은 진단에 포함하지 않습니다. 원본 응답 전체나 `.env`를 공유하지 마세요.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector collect --test-now
.\.venv\Scripts\python.exe -m kiwoom_collector status
```

`status`는 가장 최근 오류의 ID·시각·단계·원인·안전 진단도 보여줍니다. 이전 버전에서 저장한 오류는 `legacy_unknown`으로 표시하며 잃어버린 상세 정보를 추정하지 않습니다. 오류 회차를 `latest`에서 제외하는 기존 동작은 유지합니다.

**공식 명세와 `once` 비교:** `ka00198`의 목록은 선택적인 LIST, 종목 필드 12개는 선택적인 String입니다. 기존 `once`는 응답의 순위·목록을 확인해 출력하며 모든 필드 자료형을 확인하지는 않습니다. DB 저장은 공식 시장 필드의 문자열 자료형과 중복 종목까지 검증합니다. 따라서 `once` 성공만으로 SQLite 검증도 통과한다고 할 수 없습니다. 이 차이는 합성 응답으로 재현했으며, 실제 사용자 응답은 아직 확보하지 않았습니다. 실제 응답 근거 없이 숫자→문자열 변환이나 null→빈 문자열 변환을 추가하지 않았습니다.

**장외 응답:** 조사한 공식 명세에는 장외에만 별도 형식을 반환한다는 보장이 없습니다. `item_inq_rank=[]` 또는 선택 목록 누락은 기존 `once`처럼 빈 결과로 처리합니다. 목록이 null/문자열/객체인 경우나 시장 필드가 명세와 다른 자료형인 경우는 임의로 빈 데이터로 바꾸지 않고 단계별 오류를 기록합니다. 기준 시각이 이전 영업일인 데이터도 있을 수 있으므로 API `dt`/`tm`과 수집 시각을 구분해야 합니다. 장외라는 이유만으로 이번 실패 원인을 확정하지 않습니다.

기존 DB에는 `error_stage`와 `diagnostic_json` 열을 추가하고 데이터를 보존합니다. 쓰기 실패 원인도 가능하면 별도 트랜잭션으로 기록합니다. DB 자체가 잠겼거나 디스크가 가득 차 실패 기록조차 불가능하면 콘솔에 이를 알리고 중단합니다. 이미 커밋한 `running` 시도 이력은 유지되지만 이런 상황에서 오류 상세 저장을 보장할 수는 없습니다.


## 종목별 섹터 태그 관리

섹터는 사용자가 검토해 부여하는 **수동 분류**입니다. 시스템이 섹터를 추론하거나 외부 사이트를 자동 수집하지 않습니다. 모든 `sector` 명령은 API 키/네트워크 없이 로컬 DB에서 실행합니다. 기본 DB는 기존 `data/stock1.db`이며, 다른 DB는 명령 끝에 `--db 경로`를 붙입니다.

### 종목 마스터와 마이그레이션

업데이트 후 처음 DB를 여는 명령(`status`, `latest`, `collect`, `sector`)에서 새 테이블을 추가하고 **과거 성공 수집 기록**의 종목을 `stocks`에 한 번 채웁니다. 동일 종목의 이름은 수집 시각순으로 가장 최근에 관측된 이름을 사용합니다. 이후 `collect`와 `collect --test-now`의 성공 회차 저장 시 종목을 자동 등록하고 이름 변경을 갱신합니다. 테스트 모드에서 실제 API가 반환한 종목도 마스터에 들어갑니다. `once`는 계속 콘솔 조회만 합니다.

기존 수집 이력과 원본 JSON은 삭제·수정하지 않습니다. 섹터 연결은 종목코드를 참조하므로 이름이 바뀌어도 유지됩니다. 종목 마스터 대상은 **ASCII 숫자 6자리 문자열**이고 앞자리 0을 보존합니다. 이름 미제공/공백 또는 이 범위 밖 코드의 관측은 경고 후 마스터에서만 제외하며 기존 순위 기록은 그대로 보존합니다. 추후 다른 코드 형식이 필요하면 공식 형식을 확인해 별도로 지원해야 합니다.

### 기본 명령과 실제 종목 예시

먼저 기존 수집으로 등록된 종목을 확인합니다.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector sector untagged
.\.venv\Scripts\python.exe -m kiwoom_collector sector show 005930
```

아래는 **삼성전자(005930), SK하이닉스(000660), NAVER(035420)가 이미 종목 마스터에 있을 때**의 수동 등록 예시입니다. 없는 종목은 오류로 거부합니다. 임의로 종목명을 만들어 등록하지 않으며, 해당 종목이 조회순위에 포함된 수집이 먼저 필요합니다. 예시 분류는 사용자가 검토할 태그이며 공식 업종 분류를 뜻하지 않습니다.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector sector add "반도체"
.\.venv\Scripts\python.exe -m kiwoom_collector sector add "IT하드웨어"
.\.venv\Scripts\python.exe -m kiwoom_collector sector add "인터넷"
.\.venv\Scripts\python.exe -m kiwoom_collector sector tag 005930 "반도체" --source "수동 검토"
.\.venv\Scripts\python.exe -m kiwoom_collector sector tag 005930 "IT하드웨어" --source "수동 검토"
.\.venv\Scripts\python.exe -m kiwoom_collector sector tag 000660 "반도체" --source "수동 검토"
.\.venv\Scripts\python.exe -m kiwoom_collector sector tag 035420 "인터넷" --source "수동 검토"
.\.venv\Scripts\python.exe -m kiwoom_collector sector show 005930
.\.venv\Scripts\python.exe -m kiwoom_collector sector list
.\.venv\Scripts\python.exe -m kiwoom_collector sector untagged
```

잘못 연결한 태그는 다음으로 해제합니다. 종목·섹터·수집 이력은 삭제되지 않고 연결만 해제됩니다.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector sector untag 005930 "IT하드웨어" --source "수동 정정"
```

`sector add`는 이름 앞뒤 공백을 제거하고 같은 이름의 중복 생성을 막습니다(내부 공백·대소문자 구분). `sector tag`는 기존 섹터에만 연결합니다. 같은 출처로 같은 태그를 다시 등록하면 변경하지 않습니다. 출처가 다르면 연결을 중복 생성하지 않고 출처·수정 시각을 갱신하고 감사 이력에 남깁니다. 출처 생략 시 `수동`이며, 예를 들어 본인이 자료를 검토한 경우 `--source "인포스탁 참고·2026-10-19 수동 검토"`로 기록할 수 있습니다. 출처 문구를 시스템이 사실 확인하는 것은 아닙니다.

### CSV 일괄 등록

UTF-8 또는 UTF-8 BOM CSV를 준비합니다. 기존 두 열 `stock_code,sector_name`과 내보내기의 세 열 `stock_code,stock_name,sector_name`을 모두 지원하며, `stock_name`은 검토용으로만 사용합니다. Excel에서는 종목코드를 **텍스트 열**로 지정하고 `CSV UTF-8`로 저장하세요. 이미 `5930`처럼 0이 지워진 코드는 자동 보정하지 않습니다.

```csv
stock_code,sector_name
005930,반도체
005930,IT하드웨어
000660,반도체
035420,인터넷
```

같은 종목의 서로 다른 섹터는 허용합니다. **같은 종목-섹터 쌍의 중복 행**, 잘못된 코드, 미등록 종목, 빈 섹터명/잘못된 열은 오류입니다. 한 행이라도 오류가 있으면 파일 전체를 적용하지 않습니다. 줄 번호별 오류를 표시하고 종료 코드 1을 반환합니다. DB에 이미 있는 동일 출처의 연결은 `unchanged`입니다. 다른 출처의 기존 연결은 덮어쓰지 않으며 `conflicts`와 오류를 표시합니다. 아래 외부 자료 가져오기 방식으로 확인일·이용조건을 제공하면 새 출처의 근거를 추가할 수 있습니다. CSV에 등장한 새 섹터는 저장 단계에서 자동 생성합니다.

먼저 검증만 실행합니다.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector sector import .\sectors.csv --source "수동 검토 CSV" --dry-run
```

결과의 `errors`가 빈 목록인지, `new_sectors`, `added`/`updated`/`unchanged` 계획이 맞는지 확인한 후 적용합니다.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector sector import .\sectors.csv --source "수동 검토 CSV"
.\.venv\Scripts\python.exe -m kiwoom_collector sector list
```

`--dry-run`은 섹터·연결·수정 이력을 저장하지 않습니다. 단, DB를 처음 열면 공통 스키마 마이그레이션과 기존 관측 기반 마스터 초기화는 실행됩니다. 실제 적용 때도 다시 검증하며 섹터 생성·모든 연결·출처 이력을 단일 트랜잭션으로 저장합니다. 오류가 나면 전체 롤백합니다. CSV의 출처는 `--source`로 한 번 지정하며 생략 시 `CSV`입니다.

## SQLite 백업, 업데이트 및 복원 (Windows)

### 업데이트 전 백업

모든 수집/조회 프로그램을 종료하고 기존 프로젝트 폴더의 PowerShell에서 실행하세요. SQLite backup API로 일관된 백업을 만들며 WAL 상태를 처리합니다. 파일 복사로 실행 중인 `.db`만 가져오지 마세요.

```powershell
@'
from pathlib import Path
from datetime import datetime
import sqlite3
original = Path("data/stock1.db").resolve()
assert original.is_file(), "기존 DB가 없습니다. 새 설치라면 이 백업 단계를 건너뛰세요."
backup = original.parent / "backups" / ("stock1-" + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".db")
backup.parent.mkdir(parents=True, exist_ok=True)
source = sqlite3.connect(original.as_uri() + "?mode=ro", uri=True)
target = sqlite3.connect(backup)
try:
    source.backup(target)
    assert target.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not target.execute("PRAGMA foreign_key_check").fetchall()
    print("백업 완료:", backup)
finally:
    target.close()
    source.close()
'@ | .\.venv\Scripts\python.exe -
```

Git으로 설치한 프로젝트라면 이어서 실행합니다. `git pull --ff-only`가 로컬 코드 변경 때문에 거부되면 변경 내용을 먼저 보존·검토하고 해결하세요. `.env`를 커밋하거나 `reset --hard`로 초기화하지 마세요.

```powershell
git switch main
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m kiwoom_collector status
.\.venv\Scripts\python.exe -m kiwoom_collector sector untagged
```

마이그레이션 후 위 섹터 등록 예시를 실행할 수 있습니다. Git 저장소가 없는 ZIP 설치라면 `git pull` 대신 최신 ZIP을 새 폴더에 풀고 **종료된 기존 프로젝트의 `.env`와 `data` 폴더를 복사**한 뒤 새 가상환경에서 설치·테스트하세요. 키나 DB를 GitHub에 올리지 않습니다.

### 백업으로 복원

복원은 DB를 백업 시점으로 되돌리므로 **그 이후의 수집·태그 변경은 복원 대상 DB에서 사라집니다.** 모든 프로세스를 종료하고 현재 DB도 위 방법으로 별도 백업한 다음 실행하세요. 과거 스키마를 복원하면 다음 실행에서 새 테이블을 다시 안전하게 추가합니다. 아래는 백업 경로를 입력받아 SQLite backup API로 복원합니다.

```powershell
$stock1Backup = Read-Host "복원할 백업 DB의 전체 경로"
@'
from pathlib import Path
import sqlite3
import sys
backup = Path(sys.argv[1]).resolve()
original = Path("data/stock1.db").resolve()
assert backup.is_file() and backup != original, "백업 경로를 확인하세요."
original.parent.mkdir(parents=True, exist_ok=True)
source = sqlite3.connect(backup.as_uri() + "?mode=ro", uri=True)
try:
    assert source.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert not source.execute("PRAGMA foreign_key_check").fetchall()
    target = sqlite3.connect(original)
    try:
        source.backup(target)
    finally:
        target.close()
finally:
    source.close()
print("복원 완료:", original)
'@ | .\.venv\Scripts\python.exe - "$stock1Backup"
.\.venv\Scripts\python.exe -m kiwoom_collector status
.\.venv\Scripts\python.exe -m kiwoom_collector sector list
```

DB 구조·출처 이력의 상세 설명은 [DB 문서](docs/DATABASE.md)에 있습니다.


## [0198] 섹터 군집 분석 MVP — sector summary

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector sector summary
```

이 명령은 **기존 DB를 읽기 전용으로 열고 조회만** 합니다. API 키·네트워크를 사용하지 않으며, DB 생성·마이그레이션·과거 관측·현재 태그 변경은 수행하지 않습니다. 다른 DB는 `sector summary --db 경로`로 지정합니다. DB/섹터 스키마가 없으면 안내 오류로 종료합니다. 업그레이드 전 DB라면 백업 후 `status`로 마이그레이션하고 다시 실행하세요.

### 분석 대상 및 계산 규칙

- `api_id='ka00198'`, `status='success'`인 회차 중 수집 시각이 가장 최근인 **한 회차**를 선택합니다. 이후 실패/빈 결과가 있어도 최신 성공 회차를 사용하므로 출력 시각을 꼭 확인하세요. 시각이 같으면 회차 rowid가 큰 것을 선택합니다.
- 모의/실전, 집계 구분, 테스트 모드를 나누어 필터링하지 않고 최신 성공 회차를 선택합니다. `collection`에 KST 수집 시각·회차 ID·`environment`·`is_mock`·`test_mode`·`qry_tp`를 모두 표시합니다. 장외 테스트의 이전 기준 시각 데이터가 최신 성공 회차일 수도 있습니다.
- 그 회차의 순위 1~20 기록만 사용합니다. 종목명은 **해당 회차 관측값**, 태그는 **분석 실행 당시 현재 태그**입니다. 과거 시점의 태그를 재현하는 분석은 아닙니다.
- 섹터별 종목 수, 종목코드·관측명·조회순위 목록, 최상위 순위(가장 작은 순위), 상위 20종목 대비 비율을 출력합니다. 비율은 `섹터의 고유 종목 수 / 20 × 100`이며, 실제 관측이 20개 미만이어도 분모는 20입니다. `observed_record_count`로 실제 관측 수를 따로 확인하세요.
- 한 종목의 모든 태그를 각 섹터 집계에 반영하므로 섹터별 종목 수의 합과 비율의 합이 전체 수/100%를 넘을 수 있습니다. `unique_stock_count`와 `tagged_unique_stock_count`는 종목코드 기준 고유 수이며 중복 계산하지 않습니다.
- `unclassified_stocks`와 `unclassified_stock_count`는 코드가 있지만 태그가 없는 고유 종목 목록/수입니다. 코드 미제공·빈 코드 관측은 고유 종목이라고 추정하지 않고 `unidentified_records`·`unidentified_record_count`에 별도 표시합니다.
- 섹터가 2종목 이상이면 `cluster_candidate=true`, `label="군집 후보"`입니다. 그 외에는 `단일 종목`으로 표시합니다. 결과는 종목 수 내림차순 → 최상위 순위 오름차순 → 섹터명 순으로 정렬합니다.
- 성공 회차가 없으면 분석 대상 없음으로 표시하고 빈 결과를 반환합니다. 섹터 추론·주도섹터 판단·매매 신호·주문은 구현하지 않습니다.

예를 들어 **실제로 선택된 회차에 삼성전자와 SK하이닉스가 모두 등장하고 두 종목에 반도체 태그가 있다면**, 반도체는 `stock_count=2`, `top20_ratio_percent=10.0`, `label="군집 후보"`로 출력됩니다. 현재 회차 포함 여부나 실제 순위는 DB 관측값에 따라 달라지며 임의로 채우지 않습니다.

### Windows PowerShell 업데이트 및 실행

수집기를 종료하고 README의 SQLite 백업 절차를 먼저 수행합니다.

```powershell
git switch main
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m kiwoom_collector sector summary
```

필요한 경우 현재 태그는 기존 `sector show 005930`, `sector show 000660` 명령으로 확인합니다. `summary`는 최신 성공 회차를 보여주므로 삼성전자·SK하이닉스가 그 회차의 상위 20종목에 없으면 해당 섹터 집계에 나타나지 않습니다.

Windows Python 3.11 자동 테스트 정의는 `.github/workflows/windows-tests.yml`입니다. GitHub Actions에서 `Windows Python 3.11 tests` 실행 결과를 확인할 수 있습니다. 테스트는 API 키 없이 수행합니다.


## 미분류 종목 CSV 작업 흐름 (Excel)

기존 CLI는 `sector import 파일경로 --source "출처" --dry-run`입니다. 이번 기능은 이 구문과 DB 구조를 유지합니다. 새로 추가한 내보내기는 기존 `stocks`·`stock_sectors`의 **현재 태그가 한 개도 없는 종목**만 선택합니다. 최신 조회순위의 미분류 20개만 내보내는 것이 아니라 종목 마스터 전체가 대상입니다. 이미 한 개라도 태그가 있는 종목은 제외되지만 CSV에 그 종목을 직접 추가해 다른 태그를 연결할 수 있습니다.

### 1. 업데이트 및 내보내기

수집기를 종료하고 기존 README의 백업 절차로 DB를 백업합니다. `.env`, `data/stock1.db`는 그대로 두고 Git 프로젝트 폴더의 PowerShell에서 실행하세요.

```powershell
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m kiwoom_collector sector export-untagged --output data/untagged.csv
```

내보낸 파일은 **UTF-8 BOM(`utf-8-sig`)**, 헤더는 다음과 같습니다.

```csv
stock_code,stock_name,sector_name
005930,삼성전자,
000660,SK하이닉스,
```

위 두 종목은 형식 설명용 예시입니다. 실제 파일에는 본인 DB의 미분류 종목만 종목코드순으로 출력합니다. 종목코드는 문자열 `005930` 그대로 내보내며 `="005930"` 같은 Excel 수식을 만들지 않습니다. 태그를 입력할 `sector_name`은 비워 둡니다. 종목명에 쉼표/따옴표가 있어도 표준 CSV 인용 규칙을 사용합니다. 미분류가 없으면 헤더만 출력하고 `row_count=0`을 표시합니다.

출력 폴더는 없으면 생성합니다. **이미 존재하는 CSV는 덮어쓰지 않습니다.** 다시 내보낼 때는 편집본을 보존하고 `--output data/untagged-2.csv`처럼 다른 이름을 쓰세요. `.csv` 이외의 출력 경로는 거부하며 `.env`나 DB 파일을 덮어쓰지 않습니다. 태그/종목명/이력을 변경하지 않지만, 다른 섹터 관리 명령과 마찬가지로 DB를 여는 과정에서 필요한 기존 공통 마이그레이션은 실행될 수 있습니다. 이번 변경에 새 DB 테이블/열은 없습니다.

### 2. Excel에서 종목코드를 텍스트로 가져오기

CSV를 더블클릭하면 Excel이 `005930`을 숫자 `5930`으로 자동 변환할 수 있습니다. BOM은 한글 인코딩을 해결할 뿐 숫자 자동 변환은 막지 않습니다.

1. 빈 Excel 통합 문서를 열고 **데이터 → 텍스트/CSV에서**로 파일을 선택합니다.
2. 파일 원본을 **65001: Unicode (UTF-8)**, 구분 기호를 **쉼표**로 선택합니다.
3. **데이터 변환**을 선택합니다. Power Query에서 자동 생성된 **변경된 유형(Changed Type)** 단계가 숫자 변환을 했다면 삭제합니다.
4. `stock_code` 열의 데이터 형식을 **텍스트**로 지정한 뒤 닫기 및 로드합니다. `005930`, `000660`처럼 여섯 자리가 유지되는지 확인합니다. 이미 숫자로 바뀐 뒤 텍스트로 바꾸는 것만으로는 0이 복원되지 않으므로 그 경우 원본 CSV부터 다시 가져오세요.
5. `sector_name`을 검토해 입력합니다. **stock_code·헤더는 바꾸지 마세요.** `stock_name`은 참고용이며 수정하더라도 종목 마스터나 과거 이름을 갱신하지 않습니다.
6. **다른 이름으로 저장 → CSV UTF-8(쉼표로 분리)**로 `data/sector-tags-reviewed.csv`에 저장합니다. Excel을 닫아 저장 내용과 파일 잠금을 정리합니다.

**한 행은 한 종목-한 섹터**입니다. 여러 태그는 같은 종목의 행을 복사하고 각 행에 다른 섹터를 적습니다.

```csv
stock_code,stock_name,sector_name
005930,삼성전자,반도체
005930,삼성전자,IT하드웨어
000660,SK하이닉스,반도체
```

한 셀의 `반도체;AI`나 `반도체/AI`를 자동으로 분리하지 않습니다. 입력한 셀 전체가 한 섹터명입니다. 아직 분류하지 못한 종목은 **가져올 편집본에서 해당 행을 제거**하고 원본 내보내기 파일에는 남겨 두세요. 빈 `sector_name`은 기존과 같이 오류로 처리하며 조용히 건너뛰지 않습니다. 같은 종목-같은 섹터의 중복 행도 오류입니다. 행 제거는 DB 종목/태그 삭제를 뜻하지 않습니다.

### 3. 사전 검증

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector sector import data/sector-tags-reviewed.csv --source "Excel 수동 검토" --dry-run
if ($LASTEXITCODE -ne 0) { throw "CSV 검증 실패: 오류 행을 수정한 후 다시 검증하세요." }
```

`errors=[]`, `applied=false`, `dry_run=true`인지 확인하고 `new_sectors`, `added`, `updated`, `unchanged` 계획을 검토합니다. 앞자리 0이 지워진 코드, 미등록 종목, 빈 섹터명, 중복 행, 잘못된 헤더·열 수는 전체 적용을 막습니다. 출처는 실제로 확인한 내용을 적으세요. 예를 들어 본인이 자료를 검토했다면 `--source "인포스탁 참고·수동 검토"`로 기록할 수 있지만 시스템이 외부 내용을 확인하거나 섹터를 추정하지는 않습니다.

기존 2열 CSV도 같은 명령으로 계속 사용합니다. 허용되는 헤더는 정확히 `stock_code,sector_name` 또는 `stock_code,stock_name,sector_name`이며 두 형식 모두 열 순서 변경은 허용합니다. 다른 열/중복 헤더는 오류입니다. 별도 변환 명령은 필요하지 않습니다.

### 4. 실제 적용과 summary 확인

검증 결과를 확인한 파일에 같은 출처를 지정해 적용합니다.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector sector import data/sector-tags-reviewed.csv --source "Excel 수동 검토"
.\.venv\Scripts\python.exe -m kiwoom_collector sector list
.\.venv\Scripts\python.exe -m kiwoom_collector sector summary
```

실제 적용도 파일과 현재 DB를 **다시 전체 검증**합니다. 별도의 사전 검증 인증서를 저장하거나 기존 import 구문을 바꾸지는 않습니다. 따라서 사전 검증 후 Excel에서 코드가 변했거나 미등록 종목이 추가됐으면 실제 적용이 차단됩니다. 오류 시 새 섹터·태그·감사 이력 전체를 롤백합니다. 기존 수집 이력/종목명/다른 태그는 보존합니다.

같은 출처의 동일 태그는 `unchanged`로 처리해 생성 시각·수정 시각·출처·감사 이력을 바꾸지 않습니다. **기존 연결에 다른 출처를 지정하면** 덮어쓰기를 막고 `conflicts`와 오류를 표시합니다. 외부 자료는 아래 확인일·이용조건이 있는 가져오기 방식으로 근거를 추가하세요. 새 태그를 추가해도 해당 종목의 다른 기존 태그를 지우지 않습니다.

`sector summary`는 현재 태그를 읽으므로 새 분류가 즉시 반영됩니다. 단, 그 종목이 **최신 성공 수집 회차의 상위 20종목에 포함**돼 있어야 집계에 나타납니다. CSV를 가져오기 위해 API를 새로 호출할 필요는 없습니다. 자동 테마 추정·점수 계산·주문 기능은 추가하지 않았습니다.

`data` 아래의 CSV와 DB는 기존 Git 제외 규칙을 따릅니다. `.env`·API 키·토큰을 CSV에 넣거나 공유하지 마세요. 코드 업데이트는 기존 `.env`나 DB를 교체하지 않습니다.

## KST 하루 10회 고정 일정 수집과 시점별 섹터 비교

정정된 시간표는 다음 **10개**입니다. 이 모드에서는 09:25/09:35나 과거 11회 시간표를 사용하지 않습니다.

| 오전 집중 | 이후 추적 |
| --- | --- |
| 09:20, 09:30, 09:40, 09:50, 10:00 | 11:00, 12:00, 13:00, 14:00, 15:00 |

### 업데이트·실행 (Windows PowerShell)

기존 수집기를 종료하고 README의 SQLite 백업 절차를 먼저 실행합니다. 기존 `.env`와 `data/stock1.db`는 그대로 유지하세요.

```powershell
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m kiwoom_collector status
.\.venv\Scripts\python.exe -m kiwoom_collector check-config
.\.venv\Scripts\python.exe -m kiwoom_collector collect --schedule
```

`.env`의 기본 `KIWOOM_ENV=mock`을 우선 사용합니다. `status` 또는 수집기 시작 시 기존 DB에 `scheduled_at` 열과 중복 방지 인덱스를 추가합니다. 기존 종목·태그·감사 이력·수집 기록은 삭제하지 않고, 과거 회차의 예정 시각은 NULL로 남깁니다.

`collect --schedule`은 다음과 같이 작동합니다.

- 시작 시각 이후 가장 가까운 예정 시각부터 대기합니다. 예를 들어 09:40:01에 시작하면 09:40을 실행하지 않고 09:50까지 기다립니다. 정확히 예정 시각에 시작하면 해당 회차를 실행합니다.
- 예정 시각 **전에 실행 중이던 프로세스**가 깨어날 때의 OS 타이머 오차는 최대 5초까지 허용합니다. 이 범위를 넘기면 해당 시각은 누락 처리하고 다음 미래 시각만 기다립니다. PC 절전·프로세스 중단 중의 누락 회차는 소급하지 않습니다.
- 한 회차의 API 재시도·토큰 갱신·연속조회는 기존 클라이언트 정책을 유지하지만, 누락된 여러 회차를 한꺼번에 요청하지 않습니다. 회차가 길어져 다음 예정 시각을 놓치면 역시 소급하지 않습니다.
- `collected_at`은 실제 회차 시작 시각(KST), `scheduled_at`은 예정 시각(KST)입니다. 응답 수신 완료 시각을 의미하지 않습니다. API 자체의 `dt`/`tm`은 원본 JSON에 별도로 남습니다. 콘솔과 `latest`, `sector timeline`에서 예정/실제 시각을 확인할 수 있습니다.
- 같은 예정 시각·환경·API·집계 구분은 DB에서 한 번만 예약됩니다. 성공뿐 아니라 오류/빈 결과/중단/미완료도 재시작 시 다시 호출하지 않습니다. 모의·실전 및 서로 다른 `qry_tp`는 별개 데이터입니다. 동일 설정으로 수집기는 하나만 실행하세요.
- `collect` 및 `collect --interval 초`의 기존 간격 모드는 호환성을 위해 유지합니다. 이번 목표의 고정 시간표를 사용하려면 반드시 `--schedule`을 붙입니다. `--schedule`과 `--interval` 또는 `--test-now`는 함께 사용할 수 없습니다. 장외 단일 검증은 기존 `collect --test-now`를 사용합니다.
- 기본 집계 구분은 기존과 같은 `--qry-tp 1`(1분 기준 조회)입니다. 수집 주기가 10분/1시간이라고 API 집계를 자동 변경하지 않습니다.
- Ctrl+C로 종료합니다. 프로그램을 계속 켜 두면 다음 평일의 시간표로 이어집니다. Windows 작업 스케줄러로 실행한다면 09:20보다 먼저 실행하고 PC 절전 해제·시각 동기화를 확인하세요. 자동 OS 작업 등록은 하지 않습니다.

### KRX 휴장일

주말은 자동 제외합니다. **거래소 공휴일·근로자의 날·연말 휴장·임시휴장 등은 자동 다운로드/판정하지 않습니다.** 한국거래소 공식 공지·영업일 자료를 사용자가 확인한 후 `data/krx-holidays.txt`에 날짜를 한 줄씩 입력할 수 있습니다.

```text
# KRX 공식 자료로 확인한 휴장 날짜만 YYYY-MM-DD 형식으로 입력
# 아래에 실제 확인한 날짜를 추가하세요. 이 예시는 날짜를 제공하지 않습니다.
```

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector collect --schedule --holidays data/krx-holidays.txt
```

UTF-8/BOM, 빈 줄과 `#` 주석을 지원합니다. 파일은 시작할 때 한 번 읽으므로 수정 후 수집기를 재시작하세요. 파일이 없어도 주말 제외 모드로 실행할 수 있지만, **파일 미지정 또는 누락된 휴장일에는 평일 휴장일도 호출될 수 있습니다.** 파일 제공 자체가 달력의 완전성을 보장하지 않으므로 매년 갱신하세요. 지연 개장/단축 거래일의 변경 시각을 자동 반영하지 않으므로 해당 날짜에는 실행을 중단하는 보수적인 운영을 권장합니다. 휴장일 파일은 고정 일정 모드 전용입니다.

### 시점별 섹터 비교 (DB 읽기 전용)

다른 PowerShell 창에서 실행합니다. `--date`를 생략하면 KST 오늘, `--environment` 기본값은 **mock**입니다. 수집기 `.env`를 읽지 않으므로 실전 데이터를 분석하려면 `--environment real`을 명시합니다.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector sector timeline --date 2026-10-19
.\.venv\Scripts\python.exe -m kiwoom_collector sector timeline --date 2026-10-19 --environment real
```

날짜는 실제 수집 날짜로 바꾸세요. 과거 예제 날짜 자체에 데이터가 있다고 가정하지 않습니다. 필요하면 같은 `--qry-tp`와 `--db`를 지정합니다. 이 명령은 API 호출 없이 읽기 전용으로 동작하며 DB/태그를 변경하거나 스키마를 자동 마이그레이션하지 않습니다.

보고서에는 하루 10개 시점을 모두 표시합니다.

- `attempt`: 회차 ID, 예정/실제 시작 시각, 저장 상태. 시도하지 않은 시각(아직 미래인 시각 포함)은 `missing`, 빈 결과는 `empty`, 오류는 `error`, 진행 중은 `running`으로 구분합니다.
- `summary`/`sectors`: 해당 성공 회차의 섹터별 고유 종목 수·목록·조회순위·최상위 순위·20개 대비 비율과 상위 5/10위 포함 수·비율·평균 순위. 상위 5/10 비율의 분모는 각각 5/10입니다. 다중 태그는 섹터마다 반영하지만 고유 종목과 섹터 내부에서는 중복 계산하지 않습니다.
- `versus_previous`: 바로 직전 **예정 시각** 대비 신규 진입·이탈·잔존 수/잔존율·공통 종목 순위 변화 및 섹터별 변화. 잔존율은 `공통 고유 종목 수 / 직전 시점 고유 종목 수`, 순위 개선은 `이전 순위 - 현재 순위`로 양수일수록 상위로 이동한 것입니다. 한쪽이 실패/누락/빈 결과면 비교 불가이며 더 오래된 성공 회차로 대체하지 않습니다. 분모가 0이면 비율은 NULL입니다.
- `09:40 review_candidates`: 2종목 이상 함께 등장한 오전 1차 관심 군집 후보. 이보다 이른 자료도 보존하며 09:40부터의 후보 관찰 기준으로 사용합니다.
- `13:00/14:00/15:00 versus_0940`: **그날 같은 환경·집계 구분**의 09:40 성공 회차 대비 포함 수·멤버·순위 변화. 기준이 없으면 오전 대비 비교 불가입니다. `baseline_assessment`는 포함 수 유지/감소와 구성 교체 여부를 따로 보여줍니다.
- `new_since_0940_clusters`: 09:40에는 2종목 미만이고 현재는 2종목 이상인 군집. 오전 10시경 이미 형성됐을 수도 있으므로 오후 최초 형성이라고 단정하지 않습니다.
- `afternoon_new_clusters`: 오후 직전 예정 시점(13:00은 12:00, 14:00은 13:00, 15:00은 14:00)에 2종목 미만이었다가 현재 2종목 이상이 된 군집. 직전 자료가 없으면 `afternoon_new_clusters_available=false`로 신규 형성 판정을 보류합니다.
- `15:00 review_candidates`/`review_stocks`: 현재 2종목 이상 군집과 그 안의 고유 종목을 순위순으로 나열한 **종가매매 검토용 관심 목록**입니다. 미관측 종목을 추가하거나 수익 가능성 순위를 추정하지 않습니다.

분석은 `scheduled_at`이 있는 고정 일정 회차만 사용하며 `test_mode=1`과 과거 간격/단일 수집은 제외합니다. 이를 실제 수집 시각으로 끼워 맞춰 예정 회차로 간주하지 않습니다. 09:40 기준 자료가 없으면 분석을 위해 소급 호출하지 않습니다. 현재 수동 태그를 하루 전체에 적용하므로 과거 분류 상태의 재현은 아닙니다.

**판단 한계:** 조회순위와 군집은 관심도 관측입니다. 주도섹터·매수 신호를 확정하지 않습니다. 가격 상승률·거래대금 검증과 종가매매 적합성 판단은 미구현/데이터 부족으로 표시합니다. 기존 API 원본의 기준가 대비 등락률을 다른 시점 수익률로 바꾸거나 거래대금을 생성하지 않습니다. 점수 기반 매매 판단·주문 기능은 없습니다.

## 로컬 HTML 대시보드와 후보 상태 분류


기존 `sector timeline`의 포함 수·TOP5/TOP10·평균 순위·진입/이탈·잔존율·09:40 비교를 그대로 재사용합니다. 추가 기능은 `classification` 상태 분류와 `sector dashboard` HTML 출력입니다. 스키마 변경 없이 읽기 전용 분석이며 API·인증정보를 사용하지 않습니다.

```powershell
git pull --ff-only origin main
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m kiwoom_collector sector dashboard --date 2026-10-20 --environment mock --output data/dashboard-20261020-mock.html
Start-Process .\data\dashboard-20261020-mock.html
```

날짜는 본인이 수집한 날짜로 바꾸세요. DB 경로가 다르면 `--db`를 지정합니다. 실전 환경은 `--environment real`과 다른 출력 파일명을 사용합니다. `--environment`를 생략하면 mock이며 `.env`의 환경을 읽지 않습니다. `.env`·기존 DB·태그를 변경하거나 덮어쓰지 않습니다. HTML 파일도 기존 파일을 덮어쓰지 않으므로 재생성할 때는 다른 이름을 지정하세요.

HTML은 **생성 순간의 정적 보고서**입니다. 새 수집/태그 수정 후에는 새 파일로 다시 출력해야 합니다. 웹 서버·CDN·외부 JavaScript·네트워크 연결 없이 브라우저에서 로컬 파일로 열 수 있습니다. 브라우저 자동 실행은 명령에서 하지 않으며 Windows의 `Start-Process`로 엽니다.

화면 구성:

- **시간별 추이:** 섹터별 포함 수 열지도와 작은 선 그래프. 시각 셀을 클릭하면 그 시점 상세로 이동합니다. 결측 구간의 선은 연결하지 않습니다.
- **시점 상세:** 예정/실제 시작·상태, 고유 종목 수, 다중 태그별 포함 수, TOP5/10·평균 순위와 종목 목록, 미분류 목록.
- **직전 비교:** 전체와 섹터별 신규/이탈·잔존 수, 전체 잔존율. 대응 성공 자료가 없으면 비교 불가를 그대로 보여줍니다.
- **09:40 대비 상태:** 아래의 명시적 규칙과 구성 종목 교체 여부. 13:00·14:00·15:00에서 분류합니다. 오전 시점은 형성과 원래 지표를 관찰하며, 09:40 군집을 기준으로 삼습니다.
- **15:00 관심목록:** 해당 시각 성공 회차의 2종목 이상 군집에 포함된 고유 종목만 순위순으로 표시합니다. 15:00이 누락·실패·빈 수집이면 다른 시각으로 대체하지 않습니다. 상승률·거래대금은 미검증입니다.

### 상태 분류 규칙 — 가격 강세가 아닌 조회 관심도

09:40에 2종목 이상이면 오전 군집 후보로 정의합니다. 현재/기준이 모두 성공 관측일 때만 분류합니다.

| 조건 | 표시 |
| --- | --- |
| 09:40 또는 현재가 누락·실패·빈 결과 | 판단 불가 (0으로 대체하지 않음) |
| 오전 후보가 현재 관측에서 0종목 | 소멸 |
| 오전 후보가 현재 1종목 | 약화 |
| 오전 후보가 현재 2종목 이상이며 아래 네 지표와 구성 종목이 모두 같음 | 오전 강세 유지(관심도 기준) |
| 네 지표가 전부 비악화이고 하나 이상 개선 | 오전 대비 강화 |
| 네 지표가 전부 비개선이고 하나 이상 악화 | 약화 |
| 개선·악화가 엇갈림 | 혼조 |
| 오전 2종목 미만 → 현재 2종목 이상, 직전 오후 비교에서도 군집 신규 전환 | 오후 신규 부상(직전 대비) |
| 오전 2종목 미만 → 현재 2종목 이상이지만 직전 비교가 없거나 이미 군집 | 오전 대비 신규 군집(발생 시점 미확인) |
| 그 외 | 군집 미형성 |

네 지표는 **포함 종목 수·TOP5 포함 수·TOP10 포함 수·평균 순위**입니다. 수는 클수록 개선, 평균 순위는 작을수록 개선입니다. 규칙에 없는 가중치·수익률 추정은 추가하지 않습니다. '소멸'은 해당 관측의 관심 군집에서 사라졌다는 뜻이며 산업 자체의 소멸이나 투자 판단이 아닙니다. 성공 관측이 20건 미만이면 표본 부족 경고를 표시하며 분류는 잠정적입니다.

같은 시점에 약화/소멸 섹터와 강화/신규 섹터가 함께 있으면 **관심군집 교체 후보**로 표시하지만 인과관계나 주도섹터 교체를 확정하지 않습니다. 지표가 유지돼도 구성 종목은 바뀔 수 있으므로 `composition_changed`를 따로 표시합니다. 오후 신규 부상은 바로 직전 예정 시각 대비 전환이며 그날 최초 형성을 보장하지 않습니다.

### 데이터 구분과 한계

모의투자/실전은 기존 `environment`로 엄격히 필터링하고 상단에 크게 표시합니다. `test_mode=1` 회차는 모두 제외합니다. 합성/데모 데이터를 만들어 실제 수집처럼 표시하지 않습니다. 모의투자 API 응답도 합성 테스트 픽스처와는 다르며, 보고서의 출처는 DB에 저장된 환경값을 기준으로 합니다.

누락·오류·빈 수집·진행 중은 표와 그래프에서 **—**이며 0이 아닙니다. 성공 관측에서 해당 태그 종목이 없을 때만 0입니다. 섹터별로 동일 종목을 한 번만 세지만 다중 태그 때문에 섹터 간 합계는 전체 고유 종목 수보다 클 수 있습니다. 과거 관측명과 현재 수동 태그를 사용하며 과거 태그 상태를 복원하는 분석은 아닙니다.

조회순위만으로 주도섹터나 매수 신호를 확정하지 않습니다. 가격 상승률·거래대금·종가매매 적합성은 **미검증**이며 자동 주문은 없습니다. HTML에는 시장 관측과 수동 분류가 포함되므로 `data` 아래에 보관하세요(기존 Git 제외 대상). API 키·토큰·원문 인증 응답은 포함하지 않습니다.

## 대시보드 검수: 전체/섹터 교체율과 미분류 비율

교체율을 다음처럼 명시해 기존 진입·이탈·잔존 계산에 추가했습니다.

- **직전 대비 전체 고유 종목 교체율:** `직전 전체 집합에서 이탈한 코드 수 / 직전 전체 고유 코드 수 × 100`. 다중 태그는 전체에서 중복 계산하지 않습니다.
- **직전 대비 섹터 내 교체율:** 같은 식을 해당 섹터의 직전/현재 코드 집합에만 적용합니다. 전체 교체율이나 섹터별 비율의 합/평균으로 대체하지 않습니다.
- **09:40 대비 섹터 내 교체율:** 상태 분류 표에서 별도 열로 표시하며 분모는 오전 기준의 해당 섹터 고유 종목 수입니다. 직전 대비 교체율과 비교 기준이 다릅니다.
- 이전 집합이 없으면 비율은 NULL/—입니다. 새 군집에 임의의 100%를 주지 않습니다. 관측이 누락·실패·빈 결과면 비교 자체가 불가입니다. 이전 집합이 있고 현재 해당 종목들이 전부 빠지면 100%입니다.
- 종목 수가 늘어도 기존 종목이 모두 남아 있으면 이탈 기준 교체율은 0%입니다. 현재 집합 중 새 종목의 비중은 별도 JSON 필드 `current_entry_percent`로 제공합니다. 둘을 혼동하지 마세요.

예: 직전 A/B/C/D → 현재 A/C/E/F는 신규 E/F, 이탈 B/D, 잔존 A/C로 전체 교체율 50%, 잔존율 50%입니다. 섹터 X가 A/B → A/E면 섹터 내 교체율 50%, 섹터 Y가 A/C → A/C면 0%입니다. A가 X와 Y 양쪽에 있어도 전체에서는 한 번만 셉니다.

**같은 종목 수여도** TOP5/TOP10 포함 수와 평균 순위가 전부 비악화이면서 하나 이상 개선되면 강화, 반대면 약화, 지표가 엇갈리면 혼조입니다. 지표가 모두 같지만 멤버가 바뀌면 `구성 교체(지표 유지)`로 표시합니다. 지표가 강화/약화하면서 멤버가 바뀐 경우는 원래 상태와 함께 `일부 교체`/`전면 교체` 및 교체율을 표시합니다. 구성 교체 자체만으로 강하거나 약해졌다고 단정하지 않습니다. 구성 추가/축소도 따로 표시합니다.

대시보드 상단에는 **선택한 시점**의 미분류 종목 수와 비율을 표시합니다. 미분류 비율은 `미분류 고유 종목 수 / 코드가 확인된 전체 고유 종목 수 × 100`이며 고정 20개나 태그별 합계가 분모가 아닙니다. 코드 미식별 관측 수는 따로 표시하고 분모에 넣지 않습니다. 전체 고유 수가 0이면 비율은 —입니다. 누락 시점을 클릭하면 상단도 —/missing으로 바뀌며 다른 시점의 값을 가져오지 않습니다.

이번 검수 변경은 읽기 전용 분석·HTML과 테스트에만 적용하며 DB 스키마·기존 데이터·수집 시간표를 변경하지 않습니다. 과거 HTML은 자동 수정되지 않으므로 최신 코드로 **새 파일명**의 대시보드를 생성하세요.

## 연속 시점 관심도 판정과 외부 분류 연결 준비

직전 예정 시점이 성공 관측인 경우, 모든 시각의 섹터 비교 표에 **관심도 강화·유지·약화·혼조**를 표시합니다. 포함 수·TOP5 수·TOP10 수·평균 순위 개선값이 모두 비악화이고 하나 이상 개선이면 강화, 반대면 약화, 모두 같으면 유지, 방향이 엇갈리면 혼조입니다. 새로 관측되거나 사라진 섹터는 신규 관측/관측 이탈로 표시합니다. 09:40 대비 오후 후보 판정은 별도 표로 유지합니다.

종목 구성은 관심도 방향과 독립적입니다. 지표가 동일하지만 멤버가 바뀌면 **관심도 유지·구성 교체**로 표시하고, 신규·이탈·잔존 종목과 섹터 내 교체율을 나란히 보여줍니다. 전체 상위 종목 교체율은 고유 종목 전체 집합, 섹터 내 교체율은 해당 태그 집합을 분모로 사용합니다. 미분류 목록·상단 비율의 분모는 코드가 확인된 관측 고유 종목 수입니다.

화면에서 원래 수·순위·진입/이탈은 **관측값**, 강화/유지/약화와 후보 분류는 **규칙 판정**으로 명시합니다. 수집 없는 셀은 `missing`이며, 실패/빈 수집도 각 상태를 유지하고 차트를 연결하지 않습니다. 성공한 관측에서 태그가 없는 경우의 실제 0과 구분합니다. 가격 상승률·거래대금은 여전히 미검증이며 해당 값으로 매매 신호를 판정하지 않습니다.

향후 외부 분류를 연결할 때 기존 `stock_sectors` 다대다 구조와 `source`·`updated_at`을 재사용할 수 있도록 분석의 섹터 출력에 `tag_assignments`를 추가했습니다. 각 항목은 `stock_code,source,updated_at`으로 구성되며, HTML의 '분류 출처·수정 시각'에서 확인할 수 있습니다. 현재 출처는 사용자가 기록한 문구이며 외부 서비스가 검증됐다는 뜻은 아닙니다. 외부 자료의 확인일·다중 출처 근거·이용조건 기록은 아래 외부 CSV 기능에서 관리합니다. 외부 자동 수집·자동 테마 추정은 하지 않습니다.

기존 수집 프로세스의 중지·재시작은 필요하지 않습니다. 별도 PowerShell에서 테스트와 새 HTML 생성을 실행할 수 있습니다. HTML 생성은 기존과 같이 읽기 전용 연결을 사용하고 기존 보고서를 덮어쓰지 않습니다.

## HTS 조건검색 1단계: 목록 조회·일반 검색 저장

`condition list`와 `condition fetch`를 **직접 실행할 때만** 인증 및 WebSocket API를 호출합니다. 기존 `collect`/고정 시간표는 조건검색을 호출하지 않습니다. 실시간 편입·이탈 구독, 주문, 자동매매는 없습니다.

| 내부 이름 | HTS에 저장된 정확한 이름 |
| --- | --- |
| `sector_seed` | 주도주종베 |
| `sector_cross` | 당일주도주(상따겸용) |
| `independent_high` | 끼도주 |

매번 `ka10171` 목록을 받아 이름을 정확히 매핑합니다. 세 이름 중 누락·중복이 있으면 일반 검색과 DB 저장을 시작하지 않습니다. 이름의 공백·괄호도 일치해야 합니다. `seq`는 서버 반환값을 사용하며 고정하지 않습니다. 식별자의 주변 공백은 제거하되 원래 `raw_seq`도 보존합니다. API 목록으로 조건식 내부 수식까지 검증하지는 못하므로 HTS 설정도 확인하세요.

Windows 프로젝트 폴더의 PowerShell에서 다음 순서로 실행하세요. 기존 `.env`, `data/stock1.db`를 복사·덮어쓰기·삭제하지 않습니다. Git 체크아웃이라면 먼저 `git status`로 로컬 변경을 확인하고, 배포된 변경을 `git pull --ff-only`로 적용하세요. ZIP 사용자는 수정된 소스·테스트·문서·`requirements.txt`만 적용하고 `.env`와 `data` 폴더는 유지하세요. DB 백업은 위의 SQLite backup 절차를 사용합니다.

```powershell
# 1. 새 WebSocket 의존성 설치
.\.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 실제 API 호출 없이 전체 테스트
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

# 3. 설정 확인 (키 값 출력/API 호출 없음)
.\.venv\Scripts\python.exe -m kiwoom_collector check-config

# 4. 여기부터 실제 인증/API 호출: 저장 조건식 이름과 seq 확인 (DB 쓰기 없음)
.\.venv\Scripts\python.exe -m kiwoom_collector condition list

# 5. 세 조건식 일반 검색 및 SQLite 저장
.\.venv\Scripts\python.exe -m kiwoom_collector condition fetch
$LASTEXITCODE

# 6. 이후는 DB만 읽음: 기본 mock 환경의 최근 조건검색 배치
.\.venv\Scripts\python.exe -m kiwoom_collector condition report --environment mock
```

다른 DB를 지정하려면 각 명령에 `--db data/stock1.db`를 추가합니다. API 환경은 기존 `.env`의 `KIWOOM_ENV`로 선택하고 기본은 `mock`입니다. `report --environment real`은 저장된 실전 데이터만 읽는 필터이며 서버를 호출하거나 `.env`를 변경하지 않습니다. 모의 키를 실전 서버에 사용하지 마세요.

**모의투자 조건검색 지원은 아직 실제 검증되지 않았습니다.** 공식 명세에 모의 WebSocket 도메인은 있으나 기능 지원을 확정하지 않습니다. `8104` 수신 시 `모의투자 미지원`을 표시하고 자동 실전 전환하지 않습니다. 다른 API 오류는 숫자 코드와 환경을 표시합니다. 동시 연결 수·조건검색 호출 제한 수치는 미확인입니다. 요청을 순차 처리하고 최소 1초 간격을 두는 것은 프로그램 자체 정책이며 공식 한도 준수를 보증하는 수치는 아닙니다. 현재 수집기를 종료·재시작하는 동작은 없지만, 같은 키의 추가 서버 연결이 기존 접속에 미치는 영향은 공식 제한 확인이 필요합니다.

`fetch`는 배치 ID 하나에 세 검색 회차를 만듭니다. 정상 실행 시 세 조건식 각각의 전체 페이지를 받은 뒤 저장하며 성공 코드는 0입니다. 검색 실패 시 종료 코드 1이며, 해당 회차의 실패 원인과 받은 안전한 응답을 보존하고 나머지 회차는 `앞선 검색 실패로 실행하지 않음`으로 기록합니다. 부분 페이지의 종목은 완료 결과로 저장하지 않습니다. Ctrl+C는 미완료 회차를 `interrupted`로 기록하고 연결·DB를 닫습니다. 인증·목록·이름 매핑 단계에서 실패하면 검색 회차가 생성되기 전이므로 콘솔에만 오류가 남습니다. 재실행은 새 배치로 누적됩니다.

추가 테이블은 `condition_definitions`, `condition_runs`, `condition_members`입니다. 기존 조회순위 기록·일정·섹터 태그·태그 이력을 삭제하거나 재작성하지 않습니다. 기존 DB 초기화/마이그레이션 경로를 재사용하고 조건검색 테이블은 별도 트랜잭션으로 추가합니다. 이름이 있는 새 주식은 종목 마스터에 등록하되 기존 종목명·수동 태그는 변경하지 않습니다. 회차에서 관측한 이름은 조건검색 기록에 별도로 남습니다. 이름이 없으면 임의 이름을 생성하지 않습니다.

보존하는 원본은 **공식 응답 필드의 원래 문자열 값과 페이지 구조**입니다. `return_msg`, 인증 프레임, 토큰, 헤더, 미확인 필드는 보안상 저장하지 않으며 알려진 인증정보 문자열도 마스킹합니다. 중복 원본 행은 `response_json`에 남고, 정규화 멤버는 회차 내 종목코드로 중복 제거합니다. `A005930`은 문자열 `005930`으로 저장하고, ELW/ETN의 `J`/`Q` 접두어는 코드 충돌을 막기 위해 유지합니다. 가격·등락률의 임의 변환이나 조건검색 결과의 순위 생성은 하지 않습니다.

`report` 검증 항목:

- `complete_batch`: 세 검색이 모두 `success` 또는 `empty`인지 확인. 실패를 과거 성공으로 대체하지 않습니다.
- `conditions`: 조건식별 식별자, 관측 시각, 상태, 고유 종목 수(`record_count`), 회차 내 중복 원본 행 수(`duplicate_count`), 종목·미분류·섹터 목록.
- `unique_stock_count`: 완료된 검색 결과의 합집합 고유 종목 수. 실패한 검색은 합집합 범위에 포함하지 않습니다.
- `cross_condition_duplicate_stock_count`/`duplicate_stocks`: 2개 이상 조건식에 등장한 고유 종목 수와 출현 조건식.
- `unclassified_stocks`: 결과 합집합 중 현재 섹터 태그가 없는 종목. 섹터는 자동 추론하지 않습니다.
- `sectors`: 합집합 내 섹터별 고유 종목 수. 다중 태그로 섹터 간에는 같은 종목이 포함될 수 있습니다.
- `ranking_intersections`: 조건식별로 **같은 환경·같은 KST 날짜이며 검색 요청 시각 이전**의 최신 [0198] 시도를 확인합니다. 테스트 회차는 제외합니다. 해당 시도가 성공했을 때만 교집합을 계산하고 관측 시차(`age_seconds`)·조회 구분(`qry_tp`)을 표시합니다. 실패·빈 결과·미수집은 `stocks=null`이며 0건으로 판단하지 않습니다. 시차가 길면 동시 관측으로 해석하지 마세요. 세 조건식도 순차 조회이므로 서로 관측 시각이 다릅니다.

DB 조회는 읽기 전용 연결에서 일관된 스냅샷으로 수행하며 `.env`나 키가 없어도 됩니다. 최초 조건검색 저장 전에는 스키마/이력 없음 오류를 내고 DB를 생성하지 않습니다. 미분류 종목의 이름이 마스터에 등록되었다면 기존 `sector export-untagged` → CSV 편집 → `sector import ... --dry-run` → `sector import ...` 흐름을 재사용할 수 있습니다. 이 내보내기는 전체 마스터의 미분류 목록이며 조건검색 결과만으로 제한하지 않습니다.

실제 검증에서는 HTS와 각 조건식 결과의 종목코드를 비교하고, `complete_batch`, 미분류 목록, 중복 수, 교집합의 시차를 확인하세요. 검색 시점 차이로 결과가 달라질 수 있습니다. 검색 결과는 조건 충족 관측이며 주도섹터 확정이나 매수 신호가 아닙니다.

## 외부 테마 CSV 통합: 출처별 근거와 분류 작업 우선순위

핀업·인포스탁 등의 자료를 **사용자가 직접 확인하고 사용 가능한 범위에서 작성한 CSV**를 가져옵니다. 사이트에 접속하거나 자동 스크래핑하지 않으며 해당 서비스의 이용 허가를 프로그램이 보증하지 않습니다. 실제 이용조건 URL, 확인일, 허용 범위·제한에 대한 메모를 사용자가 기록합니다. 예제 URL을 실제 서비스의 약관으로 간주하지 마세요.

추가 기능은 로컬 파일·SQLite만 사용합니다. 기존 [0198] 하루 10회 일정, API 호출, 조건검색 동작, 과거 수집 기록은 변경하지 않습니다. 종목·태그는 모의/실전이 공유하는 분류 마스터이며 `--environment`는 우선순위·전후 비교에 사용할 관측 이력만 구분합니다.

### 1. 미분류 우선순위 CSV 내보내기

프로젝트 폴더의 Windows PowerShell에서 실행하세요. 기존 `.env`, `data/stock1.db`를 덮어쓰거나 삭제하지 않습니다. DB 백업은 위의 SQLite backup 절차를 사용하세요.

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m kiwoom_collector sector export-priority --environment mock --output data/priority-untagged.csv
```

대상은 지정 환경의 **전체 성공 [0198] 이력과 성공한 조건검색 회차에 등장한 종목의 합집합** 중 현재 태그가 없는 종목입니다. 최신 시점만의 목록은 아닙니다. 테스트 회차, 오류/미완료 검색, 지원하지 않는 코드 형식은 제외하고 종목코드당 한 행만 출력합니다. 정렬은 양쪽 데이터 출현 여부 → 출현 조건식 종류 수 → 총 출현 회차 수 → 최고 조회순위 → 코드입니다. 분류 작업을 위한 우선순위이며 투자 점수가 아닙니다. 콘솔 JSON의 `priorities`에 출현 횟수·조건식·마스터 등록 여부를 표시합니다. 미완료 조건검색의 부분 결과를 성공 데이터처럼 사용하지 않습니다.

CSV는 기존 내보내기와 같은 `stock_code,stock_name,sector_name` 3열, UTF-8 BOM입니다. 위에서 설명한 Excel **데이터 → 텍스트/CSV 가져오기 → 종목코드를 텍스트 형식으로 지정**하는 절차를 사용하세요. CSV를 더블클릭해 열면 BOM이 있어도 앞자리 0이 사라질 수 있습니다. 모든 내보내기는 기존 파일을 덮어쓰지 않습니다.

직접 확인한 테마를 `sector_name`에 입력하고 `data/themes-reviewed.csv`로 저장합니다. 한 종목에 여러 테마가 있으면 행을 추가하세요. 기존 `sector export-untagged`는 계속 전체 종목 마스터의 미분류 목록을 내보냅니다.

### 2. 표기 차이는 명시적인 별칭으로 관리

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector sector add "이차전지"
.\.venv\Scripts\python.exe -m kiwoom_collector sector alias add "2차전지" "이차전지" --source "핀업"
.\.venv\Scripts\python.exe -m kiwoom_collector sector alias list
```

출처별 별칭을 우선 적용하고, 출처를 생략한 전역 별칭(`source="*"`)을 그다음 적용합니다. 공백 제거 외에 유사 문자열/동의어를 자동 추론하지 않습니다. 정식 섹터는 먼저 생성해야 합니다. 별칭의 대상 변경, 기존 섹터의 자동 병합·재태깅은 지원하지 않습니다. 잘못 연결된 별칭은 DB를 임의 수정하지 말고 별도 정정 절차를 설계한 뒤 처리하세요. 입력 테마명과 실제 연결한 정식 섹터는 근거에 각각 남습니다. 일반 CSV 가져오기에도 출처에 맞는 별칭을 적용합니다.

### 3. 기존 CSV에 메타데이터를 옵션으로 제공하여 dry-run

기존 2열 또는 3열 CSV를 그대로 재사용할 수 있습니다. 다음 값은 예제가 아니라 **직접 확인한 실제 값**을 입력해야 합니다. 확인일은 자료를 실제로 확인한 날짜이며 자동 생성하지 않습니다.

```powershell
$themeSource = "핀업"
$observedAt = Read-Host "테마 구성을 직접 확인한 날짜 (YYYY-MM-DD)"
$termsUrl = Read-Host "직접 확인한 해당 서비스 이용조건 URL (https://...)"
$termsNote = Read-Host "이용 범위와 제한에 대한 확인 메모"
$termsCheckedAt = Read-Host "이용조건을 확인한 날짜 (YYYY-MM-DD)"

$importArgs = @(
  "sector", "import", "data/themes-reviewed.csv",
  "--source", $themeSource,
  "--observed-at", $observedAt,
  "--terms-url", $termsUrl,
  "--terms-note", $termsNote,
  "--terms-checked-at", $termsCheckedAt,
  "--environment", "mock"
)

.\.venv\Scripts\python.exe -m kiwoom_collector @importArgs --dry-run
$LASTEXITCODE
```

`errors=[]`인지 확인하고 `counts`/`new_tags`/`existing_tags`/`new_evidence`/`existing_evidence`/`new_stocks`/`conflicts`를 검토하세요.

- `new_tags`/`existing_tags`: 종목-정식 테마 연결 기준 목록과 고유 연결 수입니다. 한 연결에 여러 출처·확인일이 있어도 신규 연결은 한 번만 셉니다.
- `new_evidence`/`existing_evidence`: 종목-정식 테마-출처-확인일 기준입니다. 다른 출처나 다른 날짜의 확인은 추가 근거가 됩니다.
- `additional_source`: 같은 태그에 다른 출처가 존재한다는 검토 정보입니다. 기존 태그의 출처·생성일·수정일을 유지하고 새 근거만 추가합니다.
- `different_theme`: 같은 종목에 다른 테마가 존재한다는 검토 정보입니다. 다중 테마를 허용하므로 오류나 의미상 모순으로 단정하지 않으며 기존 태그를 삭제하지 않습니다.
- `errors`: 잘못된 코드/날짜/필수 메타데이터, 정규화 후 중복 행, 동일 근거의 이용조건 변경 등입니다. 하나라도 있으면 전체 적용을 막습니다. 이용조건의 과거 기록은 수정하지 않고 새 확인일에 새 근거로 기록합니다.
- `new_stocks`: 마스터에 없는 6자리 코드에 검토한 `stock_name`까지 제공한 경우입니다. 이름이 없으면 오류입니다. 기존 종목명은 CSV로 수정하지 않습니다.

외부 dry-run은 읽기 전용 DB 연결을 사용해 스키마·태그·이력을 생성하지 않습니다. 실제 적용 시 파일과 현재 DB 상태를 다시 검증합니다. dry-run 후 파일/별칭/DB가 바뀌면 새 검증 결과가 달라질 수 있으므로 다시 검토하세요.

### 4. 적용 및 전후 비교 보고서

```powershell
# dry-run을 검토한 뒤 같은 인자로 적용
.\.venv\Scripts\python.exe -m kiwoom_collector @importArgs
$LASTEXITCODE

# 전체 근거·기존 태그 감사 이력·이용조건 확인 기록
.\.venv\Scripts\python.exe -m kiwoom_collector sector provenance 005930

# 마지막 가져오기의 영구 보존 보고서를 JSON 파일로 내보내기
.\.venv\Scripts\python.exe -m kiwoom_collector sector import-report latest --output data/theme-import-report.json

# 기존 분석에 새 분류가 반영되는지 확인 (API 호출 없음)
.\.venv\Scripts\python.exe -m kiwoom_collector sector summary
.\.venv\Scripts\python.exe -m kiwoom_collector condition report --environment mock

# 분류 후 남은 우선 검토 종목 (새 파일명 사용)
.\.venv\Scripts\python.exe -m kiwoom_collector sector export-priority --environment mock --output data/priority-remaining.csv
```

보고서는 `import_id`로 보존합니다. `sector import-report 가져오기ID`로 특정 보고서를 조회할 수 있습니다. `latest`는 환경 구분 없이 가장 최근에 적용한 가져오기이며 보고서 내부 `environment`가 비교 대상 환경입니다. `provenance`, 보고서 조회, 우선순위 내보내기는 읽기 전용이며 인증정보가 필요 없습니다.

`before`와 `after`에 종목 마스터 전체(`master`)와 고정된 성공 관측 합집합(`observed`)의 미분류 수·비율을 표시합니다. `sector_changes`에는 섹터별 전체 마스터 종목 수와 관측 합집합 종목 수의 전후 값을 표시합니다. 적용 중에는 같은 트랜잭션·같은 관측 종목 집합을 사용해 수집기에서 새 데이터가 들어와 분모가 바뀌는 문제를 막습니다. 새 종목 등록으로 마스터 분모가 커진 경우에는 전후 `stock_count`도 함께 비교하세요. 분모가 없으면 비율은 0이 아닌 `null`입니다. 다중 테마로 섹터 간 종목 수 합은 전체 고유 종목 수보다 클 수 있습니다.

새 테이블은 `sector_external_sources`, `sector_source_terms`, `sector_tag_evidence`, `sector_external_imports`, `sector_aliases`입니다. 실제 외부 가져오기 또는 별칭 생성 시 기존 테이블을 ALTER/삭제하지 않고 추가하며, 가져오기의 스키마·새 마스터·태그 연결·근거·이용조건·보고서는 한 트랜잭션으로 커밋/롤백합니다. 기존 태그는 분석용 연결에 그대로 두고 출처별 근거를 추가합니다. 과거 태그의 확인일을 임의로 생성하지 않으며 근거가 없으면 `null`로 표시합니다. 새 CSV에 빠진 종목이나 테마를 자동 삭제하지 않습니다. 동일 근거 재가져오기는 중복 태그/근거를 만들지 않지만 적용 실행 자체는 새 가져오기 보고서로 남습니다. 수동 `sector untag`는 기존 명시적 연결 해제 기능을 유지하며 과거 근거는 삭제하지 않습니다.

### 행마다 출처와 확인일이 다른 CSV

지원 헤더는 `stock_code,sector_name` 필수에 아래 선택 열을 조합할 수 있습니다.

```text
stock_name,source,observed_at,terms_url,terms_note,terms_checked_at
```

이 중 메타데이터 열이 하나라도 있거나 확인일/이용조건 옵션이 있으면 외부 자료 가져오기 모드를 사용합니다. 비어 있는 행별 값은 같은 이름의 CLI 기본값으로 보충하며 모두 없으면 필수 메타데이터 오류입니다. `terms_checked_at` 열의 CLI 이름은 `--terms-checked-at`입니다. 한 파일에 핀업·인포스탁 자료를 섞을 수 있지만 각 행의 이용조건은 해당 출처에서 실제 확인한 내용이어야 합니다. `source` 명칭을 매번 동일하게 사용하세요.

메타데이터 없는 기존 2/3열 가져오기와 수동 태깅 명령은 유지합니다. 단, **기존 CSV 가져오기가 다른 출처를 자동 덮어쓰던 동작은 중단**했습니다. 충돌 시 파일 전체를 적용하지 않고 확인일·이용조건을 포함한 외부 모드 사용을 안내합니다. 명시적인 수동 `sector tag`의 출처 정정 동작은 감사 이력을 남기는 기존 방식으로 유지하며, 외부 근거 기록 자체는 수정하지 않습니다.

## CLI 옵션 지원 범위 및 실제 진입점 검증

옵션 범위와 하위 명령의 인자 수는 `__main__.py`의 단일 검증 규칙으로 확인합니다. 옵션은 명령 앞/중간/뒤에 사용할 수 있습니다. 지원하지 않는 옵션은 DB나 인증정보를 열기 전에 종료 코드 2로 거부합니다.

| 옵션 | 지원 명령 |
| --- | --- |
| `--environment mock/real` | `sector timeline`, `sector dashboard`, `sector export-priority`, `sector workbench`, 외부 모드 `sector import`, `condition report` |
| `--output` 필수 | `sector export-untagged`, `sector export-priority`, `sector workbench`, `sector dashboard` |
| `--output` 선택 | `sector import-report` (JSON 파일) |
| `--source` | `sector tag`, `sector untag`, `sector import`, `sector alias add` |
| `--observed-at`, `--terms-url`, `--terms-note`, `--terms-checked-at` | `sector import`만 |
| `--dry-run` | `sector import`만 |
| `--date` | `sector timeline`, `sector dashboard` |
| `--qry-tp` | `once`, `collect`, `sector timeline`, `sector dashboard` |
| `--interval`, `--schedule`, `--test-now`, `--holidays` | `collect`만 (기존 상호 배타 조건 유지) |

`--environment`는 저장된 관측의 분석 범위이며 API 서버 전환 옵션이 아닙니다. `condition list/fetch`는 `.env`의 `KIWOOM_ENV`만 사용하므로 `--environment`를 거부합니다. `sector provenance`, `sector alias`, `sector import-report`도 환경 필터를 받지 않습니다. 태그·출처 근거는 공통 마스터이며 특정 환경 데이터로 복제하지 않습니다. `sector import --environment ...`는 외부 모드를 선택하므로 확인일·이용조건이 없으면 오류입니다.

URL·이용조건 메모·이용조건 확인일을 아직 확인하지 않았다면 외부 가져오기를 적용하지 마세요. 생략/빈값을 `확인 완료`, 오늘 날짜, 임의 URL 등으로 채우지 않습니다. dry-run과 실제 적용 모두 필수 메타데이터 누락을 오류로 보고하며 확인 근거·태그·가져오기 보고서를 저장하지 않습니다. 빈 셀을 CLI의 실제 확인값으로 보충하는 것은 지원하지만, 둘 다 미입력인 값을 임의 생성하지 않습니다.

`tests/test_cli_entrypoint.py`는 내부 함수 호출 대신 별도 프로세스에서 실제 `python -m kiwoom_collector ...`를 실행합니다. 임시 DB/CSV를 사용하고 자식 프로세스의 네트워크 연결을 차단합니다. 우선순위 CSV, 외부 가져오기 dry-run/적용, provenance, 보고서 파일, 별칭, 기존 명령, 옵션 범위, 빈 이용조건의 저장 방지를 함께 검증합니다. 전체 테스트와 Windows 검증은 다음 순서로 실행하세요.

```powershell
git status --short
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m kiwoom_collector --help
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_cli_entrypoint.py -v
.\.venv\Scripts\python.exe -m kiwoom_collector sector export-priority --environment mock --output data/priority-verify.csv
.\.venv\Scripts\python.exe -m kiwoom_collector sector provenance 005930
```

`git pull`이 로컬 수정 때문에 중단되면 해당 파일을 보존하고 변경을 병합하세요. `reset --hard`, `.env` 복사, DB 삭제로 해결하지 마세요. 출력 CSV가 이미 있으면 새 파일명을 사용합니다. 가져오기 및 보고서는 위의 직접 확인한 메타데이터 예시를 따릅니다. `import-report latest`는 실제 적용 이력이 없으면 명확한 오류를 반환하며 임의 보고서를 만들지 않습니다.

## 로컬 미분류 종목 작업실 (HTML → 확인한 CSV)

`sector workbench`는 기존 `sector export-priority`와 같은 조회 함수·정렬을 사용해 **DB를 읽기만 하고 독립 HTML 파일을 생성**합니다. 웹 서버, 외부 스크립트, 사이트 스크래핑, 키움 API 호출이 필요 없습니다. 브라우저는 SQLite에 직접 연결하지 않으며 실제 DB 적용은 기존 외부 CSV importer로만 수행합니다. 기존 수집기·스케줄·조건검색 프로세스를 재시작하지 않습니다.

Windows 프로젝트 폴더의 PowerShell에서 실행하세요. 기존 `.env`, DB를 복사·초기화·삭제하지 마세요. 출력 HTML이 이미 있으면 다른 파일명을 지정합니다.

```powershell
git status --short
git pull --ff-only
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -s tests -v

.\.venv\Scripts\python.exe -m kiwoom_collector sector workbench --environment mock --output data/sector-workbench.html
Start-Process (Resolve-Path data/sector-workbench.html).Path
```

`--db data/stock1.db`로 DB를 선택할 수 있습니다. `--environment mock/real`은 관측 이력 필터이며 API 서버를 호출하지 않습니다. `--output`은 새 `.html` 파일 경로로 필수입니다. `--source`, 날짜/이용조건 옵션은 HTML 생성 옵션이 아니며 브라우저에서 태그별로 입력합니다. DB나 필요한 기존 스키마가 없으면 쓰기용 Store를 열거나 자동 마이그레이션하지 않고 오류로 종료합니다.

1. 왼쪽의 미분류 종목을 우선순위대로 선택합니다. 코드·이름으로 검색할 수 있습니다. 대상은 해당 환경의 전체 성공 [0198]·조건검색 이력에 등장한 미분류 종목 합집합이며, 단순히 모든 마스터 종목을 보여주는 화면은 아닙니다.
2. 실제 DB에서 계산한 누적 [0198] 등장 회차 수, 최근 7일 등장 회차 수, 조건검색 누적 등장 회차 수·조건식 종류, 최종 관측 시각을 확인합니다. 최근 7일은 HTML 생성 시각 직전 7×24시간이며 범위를 화면에 KST로 표시합니다. 테스트 회차·실패 회차·다른 환경은 제외합니다. 조건검색 등장 기록 없음은 조건검색 자체를 실행하지 않았는지, 실행했으나 해당 종목이 없었는지를 구분하는 표현이 아닙니다.
3. 기존 테마를 검색해 선택합니다. 별칭도 검색에 사용하지만 실제 선택·CSV에는 정식 테마명을 사용합니다. 괄호 숫자는 현재 DB의 소속 종목 수입니다. 여러 테마를 선택하거나 새 이름을 입력할 수 있습니다. 새 테마는 이 창의 선택에만 추가되며 **CSV를 실제 import할 때** DB에 등록됩니다.
4. 각 테마의 출처, 자료 확인일, 이용조건 URL·메모·확인일을 직접 입력합니다. 반복 입력은 상단의 접힌 메타데이터 영역에서 입력 후 현재 종목의 미확인 태그에 복사할 수 있습니다. 복사만으로 확인 표시가 켜지지 않으며 날짜나 URL을 자동 생성하지 않습니다.
5. 태그별 확인 체크박스 또는 **현재 종목 태그를 직접 확인했음** 버튼으로 검토한 항목을 표시합니다. 일괄 확인은 모든 필수 값이 유효해야 합니다. 메타데이터를 나중에 수정하면 해당 확인 표시가 해제됩니다.
6. **확인한 태그 CSV 다운로드**를 누릅니다. 미확인 태그는 제외하며 화면에 제외 수를 표시합니다. 확인 표시가 있어도 빈값·잘못된 날짜·URL 등 오류가 있으면 내보내기를 중단합니다. 선택 해제는 이번 CSV에서만 제외할 뿐 기존 DB 태그를 삭제하지 않습니다.

CSV는 기존 외부 import가 읽는 `stock_code,stock_name,sector_name,source,observed_at,terms_url,terms_note,terms_checked_at` 형식의 UTF-8 BOM입니다. 다중 테마는 여러 행으로 출력하고 인용부호·쉼표를 CSV 규칙에 맞게 처리합니다. 종목코드는 원래 문자열이며 Excel에서는 텍스트 열로 가져오세요.

다운로드 파일을 프로젝트의 `data/themes-reviewed.csv`로 옮긴 다음 실행합니다. HTML에서 확인한 메타데이터가 행마다 있으므로 CLI에서 임의 기본값을 보충할 필요가 없습니다.

```powershell
.\.venv\Scripts\python.exe -m kiwoom_collector sector import data/themes-reviewed.csv --environment mock --dry-run
$LASTEXITCODE

# errors=[]이고 conflicts·new_stocks·new_tags·new_evidence를 검토한 뒤에만 적용
.\.venv\Scripts\python.exe -m kiwoom_collector sector import data/themes-reviewed.csv --environment mock

.\.venv\Scripts\python.exe -m kiwoom_collector sector import-report latest --output data/workbench-import-report.json
.\.venv\Scripts\python.exe -m kiwoom_collector sector provenance 005930

# 적용 후 남은 종목으로 새 작업 화면 생성
.\.venv\Scripts\python.exe -m kiwoom_collector sector workbench --environment mock --output data/sector-workbench-next.html
```

**작업 화면은 스냅샷이며 선택 내용의 자동 저장·복원은 없습니다.** 새로고침/창 닫기 전에 확인한 태그를 CSV로 다운로드하세요. 미확인 항목은 다운로드되지 않습니다. 브라우저 다운로드 폴더·이름은 브라우저 설정에 따릅니다. HTML 파일을 공유하면 포함된 종목·테마·출처 목록도 공유되므로 로컬 `data/`에 보관하세요. 인증정보·API 원본 응답·토큰은 HTML에 포함하지 않습니다.

미확인 자료는 HTML에서 DB에 적용할 수 없으며, CSV를 수동 편집해 필수 메타데이터를 없애도 기존 importer가 다시 검증해 거부합니다. HTML 생성 후 DB가 바뀔 수 있으므로 반드시 현재 DB에 대한 dry-run을 검토해야 합니다. 이용조건 확인 체크는 사용자 진술이며 외부 서비스의 허가나 분류의 정확성을 자동 검증했다는 뜻이 아닙니다.

테스트에는 읽기 전용 DB 보존, 우선순위 일치, 최근 7일 범위, 환경 분리, 조건검색 테이블 없는 DB, 안전한 HTML 문자열 처리, 실제 `python -m` 진입점과 브라우저의 편집→CSV→기존 importer 왕복을 포함합니다. 브라우저 검사는 Chromium/Edge/Chrome이 설치되어 있으면 headless로 실행하고 없으면 해당 검사만 skipped로 표시합니다. 클라우드 관리 브라우저의 로컬 파일 탐색 제한 때문에 동일 HTML을 메모리에 렌더링하여 검사하며 애플리케이션 서버를 띄우지 않습니다. 실제 Windows 파일 열기는 위 `Start-Process` 절차로 확인하세요.

### 주도섹터 레이더 v1 (읽기 전용)

조회순위상 인기와 시점이 일치하는 시세 상승 후보를 구분하는 독립 HTML 보고서입니다. 기존 시간별 대시보드·미분류 작업실과 별도로 실행합니다.

```powershell
python -m kiwoom_collector sector radar --date 2026-10-20 --environment mock --db .\data\stock1.db --output .\data\sector-radar-20261020-mock.html
Start-Process .\data\sector-radar-20261020-mock.html
```

시점 비교, 단위·누락값 처리, 실제 스키마 확인 결과는 [SECTOR_RADAR.md](docs/SECTOR_RADAR.md)를 참고하세요.
