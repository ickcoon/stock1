# STOCK1 — 2026-10-30 야간 개발 최종 인계

작성·최종 확인: 2026-10-31 KST (10월 30일 야간 개발 종료 정리).
00:14 KST 최초 전체 실수집 성공 및 읽기 전용 검증 결과를 반영했다.
다음 작업은 **Step 7-C12 — 종목·섹터 분류 검증**이다.

## 종료 상태

- 저장소: ickcoon/stock1, 집 PC C:\Users\dada1\Downloads\stock1, 브랜치 main.
- 기능 개발 최종 커밋: d310b92d6ba23b0c4916786695f2ba2f20d79419 (C11-I).
- C11-I 종료 시 HEAD/origin/main/GitHub main이 위 SHA로 일치함을 확인했다.
- 직전 인계 문서 커밋은 fdff587122c76e0329090e989c84e44fa7acfad7이다.
  이번 Wrap-up은 인계 문서만 갱신한다. 최종 인계 커밋 SHA는 완료 보고와 Git 로그로 확인한다.
- 최신 전체 회귀: **1,073개 PASS**, 113.872초, 프로세스 종료코드 0.
  로컬 CreatorTemp/stock1-c11i-regression-final.log의 Ran 1073 tests / OK를 재확인했다.
  문서만 변경하므로 전체 테스트를 다시 실행하지 않았다.
- C11-J 최초 전체 실수집은 성공했다. 실제 종목 유형·섹터 분류와 공식 총수 대비 완전성은
  아직 검증되지 않았으며 일반주식 전체 마스터/분류 구축 완료로 표현하지 않는다.
- 이번 종료 정리에서 추가 OAuth·키움 API 호출, 운영 DB·수집기·스케줄러 변경은 없다.

## 로컬 자료 보존

추적 파일 미커밋 변경은 없었다. 다음 미추적 자료는 그대로 보존하고 Git에 넣지 않는다:

- .venv-old/
- docs/HOME_ENV_STEP6D_PREPARATION.md
- docs/HOME_MINIMAL_API_PREFLIGHT.md

기존 .env, 로컬 DB, diagnostics/reports/로그, 가상환경을 수정하거나 삭제하지 않았다.
검증은 개발 DB에만 mode=ro/immutable/query_only로 연결해 수행했다.
기본 개발 마스터 diagnostics/master/c10-master.db의 파일 존재와 Git 제외 상태를 확인했다.
인증정보 내용은 출력하지 않았다. reset/clean/강제 덮어쓰기·강제 push는 사용하지 않는다.

## C11-E~I에서 완료한 내용

- C11-E/F: 요청 mrkt_tp와 응답 marketCode의 의미 차이를 분석하고 제한된 오류 진단,
  첫 페이지 한 번만 조회하며 게시하지 않는 --diagnose-once 경로를 구현했다.
- C11-G: 집 PC DB 부재로 발생한 FileNotFoundError를 확인했다. --prepare-db로 기존 C10
  초기화 기능을 재사용해 개발용 DB를 준비했고 버전 1/synthetic=false/무결성 정상,
  종목·이력·종목 분류 연결·시총 0건과 기본 업종 분류 16개를 확인했다.
  기본 업종 분류 16개와 C11의 목표 분류 15개는 다른 체계다.
- C11-H: 요청 시장과 응답 종목 시장을 분리해 보존한다. 요청과 다른 응답 코드만으로
  전체 실패시키지 않으며 미지원 숫자 코드는 UNKNOWN으로 격리한다. 형식 오류·누락은
  중단한다. UNKNOWN은 활성 종목에 사용하지 않고 기존 알려진 마스터 값도 덮어쓰지 않는다.
- C11-I: 평일 KST 16:00 이후 자정 전, 토·일 종일을 기본 허용한다. 확인한 평일 휴장일과
  점검 구간은 config/catalog_time_policy.json 또는 --time-policy 파일로 설정한다.
  실제 시각으로 인증 전/키 입력 후 OAuth 직전/각 조회 직전에 검사한다.
  별도 실전 키와 운영 mock 키 분리, 호출·시간·재시도 제한과 중복 방지는 유지했다.

사용자 제공 실전 첫 페이지 진단은 요청 0, 응답 코드 0=914건, other=1,571건, 합계
2,485건이었다. 당시 OAuth/조회 각 1회, response_market_mismatch, database_updated=false였다.
other의 정확한 구성과 상품 유형은 아직 미확인이다. 코스닥·ETF로 단정하지 않는다.
추가로 사용자 제공 2026-10-30 23:57 실행은 로컬 시간 정책에 막혀 OAuth/조회 0회였다.
이번 Wrap-up에서는 이 실행들을 재호출하지 않았다.

## Step 7-C11-J — 최초 실수집 성공 및 읽기 전용 검증

집 PC 실행 폴더: diagnostics/catalog_runs/c11-real-20261031-001321-283470/.
manifest의 실행 ID와 DB 최신 catalog_runs 실행 ID가 일치한다.

| 항목 | 검증 결과 |
|---|---|
| 실행 시작 / 종료 (KST) | 2026-10-31 00:13:21 / 00:14:07 |
| status / success / database_updated | success / true / true |
| environment / synthetic / data_origin | real / false / api_response |
| actual_api_calls / oauth_calls / automatic_retries | 2 / 1 / 0 |
| KOSPI | 914 |
| KOSDAQ | 1,829 |
| 알려진 두 시장 합계 | **2,743** |
| UNKNOWN (시장 미분류) | **1,571** |
| stock_master 전체 / 고유 코드 | **4,314 / 4,314** |
| 최신 catalog 현재 종목 / 고유 코드 | **4,314 / 4,314** |
| dataset 행 / 요청 범위 보존 행 | 4,314 / 4,314 |
| DB PRAGMA integrity_check | ok |
| DB 외래키 위반 | 0 |
| master_meta | version=1, synthetic=0 |
| 검증 전후 DB SHA-256 | 동일 |
| dataset 지문 / DB 저장 지문 | 일치 |

**2,743은 두 알려진 시장의 합계이며 DB 전체 수가 아니다.** UNKNOWN 1,571건도
누락시키지 않고 별도로 보존되어 있다. 그 시장 코드의 의미나 상품 유형은 아직 확정하지 않는다.
요청 0은 1페이지 2,485행, 요청 10은 1페이지 1,829행이며 두 페이지 모두 has_more=false다.
페이지 연속조회 완료와 공식 전체 종목 수 대비 무누락은 별개의 검증이다.

실제 종목의 섹터 분류는 아직 검증되지 않았다. C11 taxonomy는 15개이며 현재 mapping_state는
unclassified 2,467건, review_needed 1,847건이다(전체 보존 4,314건 기준).
유형 검증 완료(type_verified=1)는 0건이다. 유형 힌트는 SPAC 64건, unknown 4,250건이며
이는 일반주식/ETF/ETN/리츠/스팩의 확정 분류가 아니다.

## Step 7-C12 — 종목·섹터 분류 검증

1. 실제 수집된 알려진 두 시장 2,743종목의 종류·시장 구분을 확인한다.
   별도 UNKNOWN 1,571건도 누락 없이 검토 대상으로 관리한다.
2. 일반주식과 ETF·ETN·스팩·리츠·우선주 등을 증거 기반으로 분리한다.
   요청 코드의 의미나 종목명만으로 응답 유형을 확정하지 않는다.
3. 15개 기본 섹터 매핑 현황과 대표 섹터, 충돌·수동 검토 필요 건수를 확인한다.
4. 미분류 종목과 업종 원문을 검토한다. 수집 성공을 분류 검증 완료로 취급하지 않는다.
5. 검증된 결과를 주도주 분석 입력 데이터에 연결할 준비를 한다.
   미분류 시장·미검증 유형·미검증 섹터를 활성 입력으로 임의 승격하지 않는다.

새 기능 구현이나 추가 실제 호출은 이번 종료 범위에 포함되지 않는다.

전체 수집 제한: OAuth 최대 1회, 시장별 최대 50페이지(조회 합계 최대 100회), 수집 단계
300초, 요청 시작 간격 1.2초, 자동 재시도 0회. 두 요청 범위의 모든 페이지와 검증이 끝나야
한 트랜잭션으로 게시한다. 중복 종목·코드 충돌·페이지 누락·형식 오류는 계속 중단 사유다.
자정을 넘긴 수집은 기존 collection_crosses_day 검사로 중단될 수 있어 주말 낮 실행을 권한다.
실패 시 자동 재실행하지 않고 manifest의 database_updated와 상태를 먼저 확인한다.
게시 전 실패는 DB 해시 보존을 확인하고, 게시 후 보고서 오류는 이미 갱신됐을 수 있음을 구분한다.

## 상세 문서

- [C11-E 분석](C11E_OFFLINE_ANALYSIS_20261030.md)
- [C11-F 제한 진단](C11F_MARKET_DIAGNOSTIC_20261030.md)
- [C11-H 혼합 시장 및 전체 실행 절차](C11H_MIXED_MARKETS_20261030.md)
- [C11-I 시간 정책](C11I_TIME_POLICY_20261031.md)

이 문서는 성공 결과와 다음 단계의 인계용이며 추가 실제 호출 승인이 아니다.
