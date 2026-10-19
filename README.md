# 키움 REST API 조회순위 수집기 — 인증·단일 조회 단계

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
