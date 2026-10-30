# Step 7-C11 — ka10099 KOSPI/KOSDAQ 마스터 갱신

## 현재 범위

개발 stock1-dev에 구현하고 fixture 및 가짜 HTTP 세션으로만 검증했다. 실제 ka10099/OAuth/Telegram/OpenAI 호출, 운영 stock1 접근·변경, 수집기 중단, 스케줄러 변경은 없다. C10의 실제 개발 마스터에도 종목/시총을 입력하지 않았다. 전체 상장종목이 이미 구축됐다는 의미가 아니며 실제 수집은 별도 승인 후 실행한다.

## 공식 계약 조사

2026-10-30 [키움 공식 명세 JSON](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json)의 ka10099와 [공식 리스트 예제](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/examples/%EA%B5%AD%EB%82%B4%EC%A3%BC%EC%8B%9D/%EC%A2%85%EB%AA%A9%EC%A0%95%EB%B3%B4/list_domestic_stocks.py)를 확인했다. POST /api/dostk/stkinfo, mrkt_tp=0/10, 응답 list 배열이다.

처음에는 연속조회 인수를 보내지 않고, 응답 cont-yn=Y일 때 next-key를 다음 요청 헤더에 그대로 전달한다. 시장을 바꿀 때 키를 초기화한다. Y인데 키가 없거나 빈 페이지, 키 순환, 중복 코드, 필수 시장 코드 누락/잘못된 형식, 실패 HTTP/API 응답, 잘못된 배열/상장일을 거절한다. 종료 헤더가 올 때까지 두 요청 범위를 수집해야 반영한다. 요청 시장과 개별 응답 시장은 분리하며 [C11-H 정책](C11H_MIXED_MARKETS_20261030.md)을 따른다. 연속 키는 메모리에서만 전달하고 산출물에는 지문만 남긴다.

공식 예제의 10페이지/0.2초는 샘플 설정이며 완전 수집 보장이 아니다. 새 구현은 기본 시장별 최대 100페이지·전체 20,000행·300초 예산·요청 시작 간 1.2초를 사용한다. 제한에 도달했는데 Y가 남아 있으면 성공으로 자르지 않고 실패한다. 자동 재시도·자동 토큰 발급/갱신·추가 시장 조회는 없다.

명세에 권위 있는 전체 종목 수가 없으므로 정상 종료 헤더만으로 시장 전체 누락이 절대로 없다고 증명할 수는 없다. 기본 시장별 최소 100행과 직전 정상 수집 대비 20% 초과 감소 거절을 추가했다. 첫 수집은 별도 공식 목록/검토한 총수와 대조해야 한다. 보고서의 complete_pagination과 universe_completeness를 구분한다. 공식 예제 응답에는 중복 코드와 시장의 예시 불일치도 있어 이를 실제 정답 데이터로 복사하지 않았다.

## 저장 구조 및 원자성

C10 stock_master/stock_history/stock_sector/sector_master/market_cap_cache를 유지한다. C11 적용 시에만 같은 **개발용** DB 안에 아래 확장 테이블을 트랜잭션으로 추가한다. 기존 C10 전용 DB는 확장 테이블 없이도 그대로 읽는다.

| 테이블 | 내용 |
|---|---|
| catalog_runs | 환경·합성 구분, 수집 시작/종료/반영 시각, 데이터·규칙 지문, 전후 건수·변경 보고 |
| catalog_pages | 시장별 페이지 번호/행 수, 요청·수신 시각, 연속 여부, 키 지문 |
| catalog_items | run+종목코드 PK, 이름/시장, 원본 upName/regDay/state/marketCode/marketName 및 허용 원문, 목록 존재 여부, 유형·검증 상태, 매핑 상태·대표 ID, 원본 갱신 시각·출처 |
| catalog_taxonomy | C11 대분류 15개의 고정 키와 기존 sector_master ID 연결 |
| catalog_changes | 신규/종목명 변경/시장 이전/업종·상장일·상태·유형 변경/범위 누락/재등장 기록 |

1. DB writer를 열기 전에 양 시장 전체 페이지를 메모리에 수집·검증한다.
2. 새 run 디렉터리에 정제 dataset.json을 먼저 쓴다. 이 단계의 디스크 실패도 기존 마스터를 변경하지 않는다.
3. 개발 경로·합성 구분·환경·지문·페이지 연속성/개수·급감·관측 역행을 다시 검증한다.
4. 하나의 BEGIN IMMEDIATE 트랜잭션으로 확장 스키마, 종목/분류/원문/변경 로그를 함께 반영한다. 오류는 모두 rollback한다. 수작업 태그의 전체 행을 비교해 보존도 검증한다.
5. 완료 후 changes.json/manifest.json을 저장한다. 커밋 뒤 보고서 파일 쓰기만 실패한 경우에는 database_updated=true 및 별도 종료 코드 3으로 정직하게 알린다. 이 경우 DB의 catalog_runs 및 catalog_changes가 변경 기록이다. 재수집을 자동으로 반복하지 않는다.

종목명·시장 등 실제 변경이 있을 때만 C10 stock_history를 추가해 기존 시총 캐시를 불필요하게 무효화하지 않는다. 변하지 않은 종목도 catalog_items에 최신 원문·페이지 수신 시각이 남는다. 같은 데이터 재적용과 과거 수집의 늦은 반영은 거절한다. mock/real은 한 카탈로그 DB에서 섞지 않는다. 서로 다른 환경은 별도 C10 마스터를 사용한다.

종목이 빠져도 삭제하거나 listing_status=delisted로 확정하지 않는다. missing_from_requested_scope와 missing_possible로 기록하고 기존 원문/이력을 보존한다. C10 조회에서는 catalog_missing_review로 보류한다. 시장 이전은 양 시장을 모두 모은 뒤 판정한다. 카테고리 차이·조회 이상일 수도 있으므로 누락을 상장폐지로 단정하지 않는다.

collected_counts는 이번 API에서 실제 받은 고유 종목 수이고, after_counts는 누락 의심 종목도 보존한 stock_master 행 수다. 두 수가 다를 수 있으며 이를 신규 수집 완전성으로 혼동하지 않는다.

## 아르비스 대분류 및 수작업 보호

1. 반도체
2. 자동차
3. 로봇
4. 원전·전력
5. 조선
6. 방산·우주
7. 2차전지
8. 제약·바이오
9. IT·AI
10. 금융
11. 화학·소재
12. 철강·기계
13. 건설·인프라
14. 소비재·유통
15. 기타

공식 산업 분류가 아닌 아르비스 탐지용 분류다. C10의 기존 16개 분류는 삭제/재명명하지 않는다. 동일한 산업 이름은 기존 ID를 재사용하고 새 이름만 추가하며, C11의 15개 집합은 catalog_taxonomy로 명시한다.

[config/arvis_sector_mapping.json](../config/arvis_sector_mapping.json)은 **upName 완전 일치**만 사용한다. 종목명, 이름의 키워드, 가격 상승으로 테마를 생성하지 않는다. 빈 업종·미등록 업종은 미분류이고 기타로 자동 밀어 넣지 않는다. 같은 업종에 서로 다른 대상 규칙이 있으면 conflict다.

제공된 기본 규칙은 reviewed=false다. 매핑된 산업은 proposed 태그로 보존하되 확정 대표 분류를 만들지 않는다. 검토한 규칙만 버전과 함께 reviewed=true로 바꿀 수 있다. 규칙 지문은 수집 run에 보존한다. 업종명 원문은 공백/내용을 임의 변환하지 않는다.

기존 수작업 산업·복수 테마 태그는 원본 행 그대로 보존한다. 수작업과 자동 규칙이 충돌하면 수작업을 덮지 않고 manual_review_needed로 보류한다. 자동 생성 source 접두사 ka10099_mapping:만 도구가 만료 처리한다. 이 접두사는 도구 전용으로 예약한다. 이후 새 수작업 검토가 등록되면 카탈로그 재수집 없이 C10 as-of 조회에 반영된다.

대표 산업은 분석 시 0개 또는 1개다. 복수 대표/충돌은 하나를 임의 선택하지 않는다. 테마 태그는 복수 보존하지만 테마별 합계는 서로 더할 수 없다. aggregate_turnover는 각 종목의 같은 시점 금액을 정확히 한 번 받아 대표 산업에만 배분하고, 중복 관측/복수 대표는 거절·제외한다. 기존 C6도 한 candidate.sector에만 묶어 합산하므로 동일 종목을 여러 섹터에 반복 배분하지 않는다.

## ETF/ETN/SPAC 등 유형

ka10099의 공식 응답에는 모든 종목을 일반주식/우선주/ETF/ETN/SPAC 등으로 확정하는 보편적 유형 필드가 없다. mrkt_tp의 ETF=8/ETN 계열 등 다른 카테고리를 이번 수집에 추가하지 않는다. 응답 marketCode가 요청 시장과 달라도 형식이 유효하면 보존하며, 지원되지 않는 응답 코드는 UNKNOWN으로 분리하고 활성 종목에서 제외한다. 요청 코드의 유형 의미를 응답 코드에 그대로 적용하지 않는다.

companyClassName이 명시적으로 ETF/ETN/기업인수목적회사인 경우에도 우선 미검증 힌트로 저장한다. 종목명에 '스팩'이 들어간다는 이유로 유형을 확정하지 않는다. 규칙 파일의 security_reviews에 코드별 유형·검증 여부·독립 근거·검토 시각을 명시할 수 있다. 현재 보고된 유형 힌트와 충돌하거나 상장일보다 오래된 검토는 승인하지 않는다.

유형 미확인은 catalog_security_review, 검증된 비일반주식은 catalog_security_excluded로 C10/AM 연결을 보류한다. 데이터 수집·보존과 일반주식 적격성 승인은 별개다. 기본 security_reviews는 비어 있으므로 첫 목록 수집만으로 모든 종목이 AM 적격으로 바뀌지 않는다.

## 실행 방법 — 이번에는 실제 API 미실행

통신 없는 계획 확인:

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.stock_catalog plan `
  --db diagnostics/master/c10-master.db --environment real
```

합성 통합 검증은 별도 합성 DB를 생성한 뒤 사용한다. 실제 C10 DB에 fixture를 넣으면 scope 오류로 거절한다.

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.master_data init --name c11-fixture --synthetic
.\.venv\Scripts\python.exe -B -m kiwoom_collector.stock_catalog fixture `
  --db diagnostics/master/c11-fixture.db --environment mock `
  --input tests/fixtures/ka10099_synthetic.json --name c11-fixture-first
```

다음 실제 실행 명령은 **별도 사용자 승인 후**에만 사용한다. 기존 유효 토큰을 STOCK1_KIWOOM_ACCESS_TOKEN 프로세스 환경변수로 주입해야 하며 값은 명령행·문서·로그에 적지 않는다. 이 프로그램은 .env/앱키/시크릿을 탐색하거나 OAuth를 발급하지 않는다.

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.stock_catalog collect `
  --db diagnostics/master/c10-master.db --environment real `
  --name hospital-ka10099-first-approved `
  --execute-approved --quota-reviewed `
  --max-pages 100 --seconds 300 --minimum-per-market 100
```

실행 조건: KST 16:00~23:49, 사용자가 실제 장 종료와 계정/토큰의 합산 유량 여유를 확인한 상태. 특별 장 운영/휴장일을 자동 검증하는 기능은 아니다. 16:00 조건만으로 다른 프로세스의 API 사용이 없다고 보장하지 않는다. 같은 개발 마스터의 중복 갱신은 별도 로컬 자원 잠금으로 차단하지만 운영 수집기 파일/잠금/프로세스는 조작하지 않는다.

HTTPS 호스트·POST 경로·API ID·시장 두 개를 고정한다. TLS 검증을 끄지 않고 리다이렉트도 따르지 않는다. 요청 연결/읽기 timeout, 전체 예산 경계 및 5MiB 응답 크기 상한을 사용한다. 이는 모든 OS/네트워크 정체를 강제 종료하는 하드 실시간 제한은 아니다. 토큰 만료/호출 한도/네트워크 실패는 중단하며 자동 인증·재시도하지 않는다. 콜렉터와의 합산 유량 검토는 별도 승인 조건이다.

산출물은 diagnostics/catalog_runs/<name>의 manifest.json, 정제 dataset.json, changes.json이다. 기존 이름은 덮어쓰지 않는다. DB/산출물은 Git 제외이며 API 토큰, 헤더 원문, 서버 메시지, 알 수 없는 응답 필드는 저장하지 않는다. 연속 키도 지문만 보존한다.

종료 코드: 0 성공/plan, 2 수집·검증·반영 실패, 3 DB 반영 완료 후 보고서 IO 실패, 130 사용자 중단. 종료 상태가 불명확하면 DB catalog_runs와 manifest의 database_updated를 확인하고 자동 재실행하지 않는다.

## 검증

정상 양 시장 수집/연속조회/전역 간격, 페이지·시장 중복, HTTP/API 오류, 키 누락·순환, 최소 건수·급감·시간 초과, 부분 실패 시 원본 해시 불변, 반영 도중 오류의 스키마 포함 rollback, 이름·시장·누락·재등장 이력, 수작업 보존, 미분류·규칙 충돌, 유형 검토, 테마 중복 합산 방지, staging/후속 보고서 IO 실패를 합성 자료로 검증한다.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_stock_catalog.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

2026-10-30 전체 회귀 **1,025개 PASS**(164.865초), C11 fixture/통신 경계 테스트 **26개 PASS**. fixture 시각을 고정한 최종 재검증도 26개 통과했다. 전체 로그는 reports/step7c11-regression-20261030.txt, C11 로그는 reports/step7c11-catalog-tests-final.txt에 보존했다. 실제 서버의 반환량·계정별 지원·장후 호출 성공은 아직 검증하지 않았다.
