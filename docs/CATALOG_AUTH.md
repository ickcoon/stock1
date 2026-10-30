# C11-B 인증 재사용 및 데이터 출처

## PowerShell 5.1 실전 숨김 입력 CLI

기존 here-string + `python -c` 실행 예시 대신 독립 모듈을 사용한다.
아래는 별도 승인 이후의 실행 명령이며 테스트 중에는 실제 실행하지 않는다.

```powershell
Set-Location C:\Users\dada1\Downloads\stock1-dev
.\.venv\Scripts\python.exe -B -m kiwoom_collector.catalog_real --execute-approved --oauth-approved --quota-reviewed
```

ASCII 명령과 입력 안내를 사용하므로 셸에 Python 한글 문자열·따옴표를 전달하지 않는다.
App Key와 Secret Key는 getpass로 숨김 입력한다. 숨김 입력을 지원하지 않으면 중단한다.
운영 stock1/.env는 읽기 전용으로 참조하며 mock 선언·App Key 비교가 불가능하거나
입력한 실전 App Key가 운영 App Key와 같으면 OAuth 전에 중단한다.

기존 Credentials(environment=real), OAuthProvider, 종목 수집 CLI를 재사용한다.
토큰은 Python 객체로 직접 전달하여 임시 환경변수나 인증 파일도 만들지 않는다.
DB 검증·장 종료 제한·실행 이름 중복 검사·개발 DB 단위 잠금을 통과한 뒤에만
숨김 입력과 OAuth를 수행한다. 인증 실패 시 재시도·모의 전환은 없다.
계정별 호출 한도는 별도 검토 대상이다.

개발 diagnostics/master/c10-master.db만 대상이며 OAuth 최대 1회, ka10099만
시장별 최대 50페이지, 수집 단계 300초, 자동 재시도 0회를 고정한다.
시장별 50페이지이므로 두 시장 합계 상한은 100페이지다. OAuth·DB 게시까지
합친 전체 프로세스의 300초 제한을 의미하지 않는다. 운영 코드·DB·스케줄러·
프로세스는 변경하지 않는다. 키·토큰은 보고서나 Git에 저장하지 않는다.
승인 플래그 없는 실행과 --help는 인증정보 입력·OAuth를 수행하지 않는다.

수정 검증: Windows PowerShell 5.1에서 모듈 --help 실행 성공, 신규 합성 테스트
9개 PASS, 전체 회귀 1,047개 PASS (180.910초). 이 합성 검증 중 실제 OAuth·ka10099 호출은 0회다.
이후 사용자의 최초 실전 실행은 response_market_mismatch로 실패했다.
실행 메타데이터와 다음 조사 방향은 [병원 종료 인계](HANDOFF_20261030_C11.md)를 참조한다.

## 이번 검증 범위

기존 `config.Credentials`와 `client.KiwoomClient.authenticate`를 재사용한다.
외부 OAuth·종목 조회 호출 없이 합성 응답으로 검증한다. 운영 코드, DB,
프로세스, 스케줄러, 인증 설정은 변경하지 않는다.

기존 프로젝트 토큰 캐시는 각 클라이언트 인스턴스의 메모리에 있다.
실행 중인 수집기의 메모리 토큰에 접근하거나 이를 갱신하지 않는다.
별도 클라이언트의 신규 토큰 발급이 동일 App Key의 기존 토큰에 미치는
서버 측 영향은 확인되지 않았다. 독립 인스턴스라는 사실만으로 서버 측
공존을 보장하지 않는다. 향후 발급은 사용자 승인과 영향 검토가 모두 필요하다.

## 설정 선택 및 무통신 확인

명시한 기존 파일을 `dotenv_values(interpolate=False)`로 읽는다.
`load_dotenv`를 호출하지 않으므로 프로세스 환경도 바꾸지 않는다.
필수 항목은 `KIWOOM_ENV`, `KIWOOM_APP_KEY`, `KIWOOM_SECRET_KEY`이다.
선택한 서버와 파일의 환경이 다르면 실패하며 모의/실전 자동 대체는 없다.
새 인증정보 파일, 토큰 캐시 파일을 만들지 않는다.

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.stock_catalog auth-check --credentials-env-file C:\Users\dada1\Downloads\stock1\.env --environment mock
```

2026-10-30 확인한 위 파일은 mock 선언이며 두 키가 존재한다.
검사는 OAuth 0회·조회 0회이며 키나 토큰 원문을 출력하지 않는다.
이는 사용자가 준비한 실전 인증정보의 부재를 뜻하지 않는다. 실전 이용 시
사용자가 지정한 기존 실전 설정 파일로 경로와 `--environment real`을 함께
선택한다. 출처가 불명확한 설정 파일을 탐색하거나 복제하지 않는다.

## 승인 이후 실행 예시 — 이번에는 실행하지 않음

기존 개발 마스터를 대상으로 장 종료 후 사용한다. 실행 이름은 매번 달라야 한다.
아래 mock은 키움 모의 서버의 실제 응답을 수집하는 선택이다.

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.stock_catalog collect --db diagnostics/master/c10-master.db --environment mock --name c11b-approved-once --credentials-env-file C:\Users\dada1\Downloads\stock1\.env --execute-approved --quota-reviewed --oauth-approved --token-impact-reviewed --max-pages 100 --seconds 300
```

승인 플래그는 검토 확인이며 서버 측 토큰 공존을 입증하는 수단은 아니다.
영향을 확인하지 못하면 발급하지 않고 기존 `--token-env` 경로를 유지한다.
파일 인증 경로는 OAuth 최대 1회, 조회 최대 200회(시장별 100페이지),
자동 재시도·자동 토큰 재발급 0회다. OAuth와 첫 조회 사이에도 기본 1.2초를
둔다. 토큰 잔여 유효시간이 조회 제한 시간 + 30초 미만이면 중단한다.
300초는 기존 수집 단계 시간 예산이며 OAuth 요청·대기·DB 게시까지 포함한
프로세스 전체 제한은 아니다. 장중 실행 차단 및 기존 API 자원 잠금을 유지한다.
이 잠금은 DB 경로와 환경 단위이므로 서로 다른 DB를 사용하는 운영 수집기와
개발 마스터의 계정별 호출 한도를 조율하지 못한다. 별도 호출 한도 검토가 필요하다.
두 시장 전체 수집과 검증 성공 후에만 기존 원자적 게시가 수행된다.

## 서버 환경과 출처

| environment | data_origin | 의미 |
|---|---|---|
| real | api_response | 키움 실전 서버에서 받은 API 응답 |
| mock | api_response | 키움 모의 서버에서 받은 API 응답 |
| mock 또는 real | synthetic_fixture | 합성 테스트 자료 |
| mock 또는 real | unverified | 출처를 입증하지 못한 기존 자료 |

`mock`이라는 이유만으로 합성 자료로 분류하지 않는다. 사용자가 전달한
조회 데이터 동일성 안내와 별개로 TR별 값의 완전한 일치까지 이번 무통신
검증으로 주장하지 않는다. `ka10099_live`는 실제 HTTP 수집 경로를 뜻하며
실전 서버라는 뜻이 아니다.

새 수집 결과·게시 보고서·catalog_runs에는 독립적인 출처를 기록한다.
기존 catalog_runs 스키마 확장은 향후 개발 마스터 게시 트랜잭션에서만
수행하며 기존 행의 기본값은 unverified다. 서버 환경만으로 소급 승격하지
않는다. 조회 모듈은 확장 전 DB도 읽으며 출처 미확인 종목을 검토 상태로
돌려준다. 기존 C7 어댑터·C9 진단은 명시적 합성 입력에만 fixture 출처를
표시하며, 나머지 과거 입력은 출처 미확인으로 유지한다.

인증 점검은 DB를 열지 않는다. 이번 작업은 실제 개발 마스터 데이터도
갱신하지 않으며 테스트 DB에만 스키마 확장과 게시를 검증한다.

## ka10099 모의 지원 확인 및 실전 대안

[키움 공식 명세](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json)의
ka10099 항목에는 실전·모의 도메인이 모두 기재되어 있다. 시장값은 KOSPI 0,
KOSDAQ 10이며 응답의 cont-yn/next-key에 따라 연속조회한다. 명세 기재와
현재 계정·서버에서의 실제 제공 여부는 구분한다. 이번에는 조회하지 않았다.

승인 후 위 mock 명령으로 HTTP 정상, return_code=0, list 구조·시장 코드,
두 시장의 최소 건수와 전체 페이지 완료를 검증한다. 일부 페이지 성공만으로
전체 수집 성공이나 지원 확인을 선언하지 않는다. 비정상 응답은 실패 처리하여
기존 마스터를 보존한다. 인증·한도·일시 장애도 가능하므로 일반적인 실패를
곧바로 모의 미지원으로 단정하지 않는다. 원문 메시지는 토큰 유출 방지를 위해
그대로 저장하지 않는다. 공식 오류 명세·고객지원으로 미지원 여부를 확인한다.

미지원이 확인되면 자동 전환하지 않는다. 별도 승인 후 기존 실전 설정 파일을
명시하여 동일 명령의 `--environment real`, `--credentials-env-file <기존실전파일>`,
새로운 `--name`으로 실행한다. 서로 다른 서버의 페이지를 합치지 않는다.
전송 모듈은 ka10099와 두 시장, 종목정보 endpoint만 허용한다. 계좌·주문·토큰
폐기 API는 이 경로에서 사용하지 않는다. 실전 REST 조회도 HTTP POST지만
주문을 수행하지 않으며 운영 DB에는 쓰지 않는다. 개발 마스터 게시만 수행한다.

## 기존 저장 자료의 출처 입증 한계

C7은 수집 실행 메타데이터와 원본 필드의 검토용 추출본을 읽고, 외부 검증
manifest의 source SHA-256 및 record fingerprint와 결합한다. 해시가 일치한다는
사실만으로 실제 API 수신 사실을 증명하지 않는다. 서버 환경, API ID, 실행·수신
시각, 수집 로그 등 검토 근거가 필요하며 인증정보는 검증 자료에 포함하지 않는다.
기존 로컬 스냅샷의 mode=ro 연결은 이번 추가 점검에서 열기 오류로 실패했다.
정책 우회나 운영 원본 재접근 없이 중단했으므로 행별 출처 입증은 완료하지 않았다.
기존 출처 미확인 자료는 unverified로 남긴다. 모의 서버라는 사실은 합성의 근거도,
실제 수신을 입증하는 충분한 근거도 아니다.

## 검증 결과 (2026-10-30)

- 인증·출처 전용 테스트 13개 PASS, 기존 종목 수집 테스트 26개 PASS.
- 최종 전체 회귀 1,038개 PASS (166.681초).
- 한국어 출처 레이블을 포함하는 UTF-8 manifest를 기존 테스트가 Windows
  기본 CP949로 읽던 두 지점에 명시적 UTF-8을 적용했다.
- 기존 개발 마스터는 작업 전 SHA-256과 일치한다. 운영 수집기 PID
  27380·21896의 실행 상태를 읽기 전용으로 확인했다.
- 구현 기준 HEAD는 4c059c57313f2c5dd37734527b28619cc10125fd이다.
  최종 검증 이후 C11-B 코드·테스트·이 문서만 Commit/Push 대상으로 선택한다.
