# C11-H — 혼합 시장 응답과 전체 수집 절차

## 근거와 한계

사용자 제공 실전 진단은 요청 mrkt_tp=0, 응답 코드 0=914건, other=1,571건,
총 2,485건, OAuth/조회 각 1회, 미게시다. 이번 작업에서는 원본을 조회하거나
OAuth/키움 API를 호출하지 않았다. other를 코스닥·ETF로 해석하지 않는다.

2026-10-30 [키움 공식 명세](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json)
재확인: mrkt_tp는 요청 시장 선택값이며 응답 marketCode/marketName은 개별 종목 필드다.
응답 코드표와 두 필드의 일치 보장은 명시되어 있지 않다. 요청 예제 0과 응답 예제
10/코스닥도 일치하지 않는다. 실제 집계는 요청과 다른 값이 존재함을 뒷받침하지만,
미확인 코드의 의미까지 증명하지 않는다.

## 변경한 정책

- 요청값은 행의 request_market에, 응답 원문은 market_code/market_name/raw에 분리한다.
  DB에는 catalog_items의 응답 필드와 별도 catalog_request_scope의 요청값을 보존한다.
  catalog_pages.market은 요청 범위(KOSPI/KOSDAQ)이며 응답 종목 시장 통계가 아니다.
- 기존 응답 0/10 해석(KOSPI/KOSDAQ)은 유지하되 요청값과 다르다는 이유로 거절하지 않는다.
  다른 1~3자리 숫자 코드(선행 0 제외)는 UNKNOWN으로 보존한다. 요청 코드표의 ETF=8,
  ETN=60 등을 응답 코드 의미로 복사하지 않는다.
- 필수 코드 누락/빈 값은 missing_response_market_code, 공백/padding/비숫자는
  invalid_response_market_code, 비문자열은 invalid_field_type_or_length로 중단한다.
  일반주식/ETF/ETN/리츠/스팩 유형은 시장 코드와 별개다. 명시적 companyClassName 힌트는
  미검증 유형으로 보존하고 common_stock 판정은 별도 증거가 있는 수동 검토만 사용한다.
- UNKNOWN은 신규 stock_master에 verified=false로 저장하고 자동 업종 연결은 하지 않는다.
  기존 알려진 종목이 UNKNOWN으로 오면 기존 stock_master를 덮어쓰지 않는다.
  최신 catalog 항목은 UNKNOWN으로 남기며 조회 상태 catalog_market_unclassified로
  활성 종목 사용·시총 갱신·업종 집계 대상에서 제외한다. 유형 검토로도 이를 우회하지 않는다.
- 두 요청 범위의 연속조회 완료, 요청별 행 수/페이지 수 일치, 알려진 응답 두 시장의
  최소 건수, 전회 대비 과도한 감소, 중복 코드, 연속키 순환, 시간·행·페이지 한도를 유지한다.
  요청별 페이지 행 수와 응답 시장별 건수를 분리해서 계산한다.
  동일 코드가 다른 요청/페이지에서 반복되어도 자동 병합하지 않고 중단한다.
  이름·시장·유형이 충돌하는 중복을 임의 선택하지 않는다.
- 모든 수집/검증과 dataset staging 후 단일 트랜잭션으로 게시한다. 오류 시 부분 게시하지
  않으며 수동 분류와 기존 데이터 보존 정책을 유지한다. 새 요청 범위 테이블도 같은
  트랜잭션에 포함한다. 기존 DB를 초기화하거나 운영 스키마를 변경하는 작업은 하지 않았다.

## 안전한 진단

실패 시 market_diagnostic.market_profile, 성공한 단일 진단의 같은 필드 및 전체 수집
dataset.pages[].market_profile에 페이지별 코드/시장명 조합과 건수를 기록한다.
코드는 ASCII 숫자 1~3자리만 그대로 집계하고, 나머지는 missing/empty/non_string/
unrecognized/invalid_row로 분류한다. 숫자 코드를 표시하는 것과 의미를 부여하는 것은 다르다.
시장명은 코스피/코스닥/KOSPI/KOSDAQ/ETF/ETN/리츠/REIT/스팩/SPAC/코넥스/KONEX/K-OTC만
표시하며 그 밖의 원문은 unrecognized로 집계한다. 임의 문자열·종목명·키·토큰·헤더·연속키는
진단에 포함하지 않는다. 미등록 시장명이 많아도 원문 출력으로 우회하지 않는다.
기존 response_market_counts의 other는 호환성을 위해 유지하지만 새 profile은 숫자별로 나눈다.

## 별도 승인 후 전체 수집 절차 (이번에는 실행하지 않음)

1. HEAD/작업 폴더를 확인한다. 오프라인 DB 준비·검증만 실행한다:

   ```powershell
   .\.venv\Scripts\python.exe -B -m kiwoom_collector.catalog_real --prepare-db
   Get-FileHash .\diagnostics\master\c10-master.db -Algorithm SHA256
   ```

2. [C11-I 시간 정책](C11I_TIME_POLICY_20261031.md)의 허용 시각인지, 개발 DB와 운영 DB가 분리됐는지, 별도 실전 키와
   호출 한도·토큰 영향 승인이 있는지 확인한다. 장 마감 가드는 휴장/특별장 달력을 대신하지 않는다.
   기존 운영 mock 키 확인 경로가 유효해야 한다. 보호 조건을 제거하지 않는다.

3. **실제 전체 수집에 대한 별도 승인 후에만** 아래 명령을 한 번 실행한다:

   ```powershell
   .\.venv\Scripts\python.exe -B -m kiwoom_collector.catalog_real --execute-approved --oauth-approved --quota-reviewed
   ```

   --diagnose-once를 생략하는 명령이다. KOSPI/KOSDAQ 요청 범위를 모두 연속조회하며
   OAuth 최대 1회, 시장별 최대 50페이지(조회 최대 100회), 수집 시간 예산 300초,
   시작 간격 1.2초, 자동 재시도 0회다. OAuth/게시까지 포함한 전체 300초 보장은 아니다.
   완료·검증 시 개발 DB에 게시하므로 단일 호출 진단과 승인 범위가 다르다.

4. 고유 실행 폴더의 manifest에서 success=true, database_updated=true,
   automatic_retries=0, oauth_calls<=1을 확인한다. dataset의 두 요청 범위 마지막
   has_more=false와 페이지별 profile, changes의 알려진 시장별 건수 및
   unclassified_market_count를 확인한다. UNKNOWN은 별도 검토 대상으로 남긴다.
   공식 총수와 대조하기 전에는 전체 시장 무누락·일반주식 유형 검증 완료라고 보고하지 않는다.

5. 실패하면 자동 재실행하지 않는다. database_updated=false와 실행 전후 개발 DB 해시
   동일 여부를 확인한다. 중복 코드가 발견되면 실제 응답 구조 근거를 추가 검토하며,
   검증 삭제나 무조건 dedup으로 우회하지 않는다. published_report_error처럼 게시 후 보고서
   저장 오류는 database_updated=true일 수 있으므로 manifest 상태를 먼저 확인한다.

## 테스트

새 합성 혼합 fixture는 실제 응답 복제가 아니다. 요청/응답 시장 분리, UNKNOWN 보존과
비활성화, 기존 알려진 마스터 보존, 유형 힌트 분리, 민감 문자열 비출력, 혼합 단일 진단,
중복 거절 및 기존 원자성·페이지 누락·수동 분류 보존 회귀를 검증한다.

전체 회귀 1,065개 PASS (119.948초, 종료코드 0). 로그는 로컬 CreatorTemp의
stock1-c11h-regression.log에 보관하며 Git에 포함하지 않는다. 실제 DB 게시·OAuth·키움 API
호출은 수행하지 않았다. 기존 로컬 미추적 문서와 가상환경은 보존했다.
