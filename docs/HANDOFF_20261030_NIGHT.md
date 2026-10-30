# STOCK1 — 2026-10-30 야간 개발 최종 인계

작성·최종 확인: 2026-10-31 KST (10월 30일 야간 개발 종료 정리).
다음 작업은 **Step 7-C11-J**, 다음 개발 세션에서 별도 승인 후 진행한다.

## 종료 상태

- 저장소: ickcoon/stock1, 집 PC C:\Users\dada1\Downloads\stock1, 브랜치 main.
- 기능 개발 최종 커밋: d310b92d6ba23b0c4916786695f2ba2f20d79419 (C11-I).
- 문서 작성 전 HEAD/origin/main/GitHub main이 위 SHA로 일치함을 확인했다.
- 이번 Wrap-up은 인계 문서만 추가한다. 최종 인계 커밋 SHA는 완료 보고와 Git 로그로 확인한다.
- 최신 전체 회귀: **1,073개 PASS**, 113.872초, 프로세스 종료코드 0.
  로컬 CreatorTemp/stock1-c11i-regression-final.log의 Ran 1073 tests / OK를 재확인했다.
  문서만 변경하므로 전체 테스트를 다시 실행하지 않았다.
- 코스피·코스닥 전체 실제 수집은 아직 미실행이며 전체 마스터 구축 완료를 주장하지 않는다.
- 이번 종료 정리에서 추가 OAuth·키움 API 호출, 운영 DB·수집기·스케줄러 변경은 없다.

## 로컬 자료 보존

추적 파일 미커밋 변경은 없었다. 다음 미추적 자료는 그대로 보존하고 Git에 넣지 않는다:

- .venv-old/
- docs/HOME_ENV_STEP6D_PREPARATION.md
- docs/HOME_MINIMAL_API_PREFLIGHT.md

기존 .env, 로컬 DB, diagnostics/reports/로그, 가상환경을 열어 수정하거나 삭제하지 않았다.
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

## Step 7-C11-J — 다음 개발 세션

1. 작업 폴더·Git HEAD·working tree·원격 동기화를 확인하고 로컬 자료를 보존한다.
2. --prepare-db로 기존 개발 DB를 오프라인 검증한다. 기존 DB를 초기화하거나 교체하지 않는다.
3. 실제 전체 수집에 대한 명시적 승인을 받고 최신 REST 서비스/점검 공지, 별도 실전 키,
   호출 한도·토큰 영향을 확인한다. 로컬 시간 허용은 키움 서버 가동 보장이 아니다.
4. 승인 후 두 요청 범위(0/10)를 전체 연속조회한다. 이 명령은 개발 DB에 게시할 수 있다:

   ```powershell
   .\.venv\Scripts\python.exe -B -m kiwoom_collector.catalog_real --execute-approved --oauth-approved --quota-reviewed
   ```

5. 시장 코드별·허용된 시장명별 profile과 UNKNOWN 건수를 확인한다. 요청 범위와 응답 시장을
   혼동하지 않는다. 일반주식·ETF·ETN·리츠·스팩 등은 별도 유형 검토를 거치며,
   미확인 코드나 유형을 이름 추정으로 확정하지 않는다.
6. C11의 15개 섹터 매핑·미분류·충돌·수동 검토 필요 건수와 대표 섹터를 확인한다.
7. 개발 마스터의 갱신 전후 건수, 실행 기록, 무결성·외래키, 기존 수동 분류 보존과
   누락 후보를 검증한다. 공식 총수 대조 전에는 무누락을 단정하지 않는다.

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

이 문서의 실행 명령은 다음 세션 인계용이며 이번 종료 정리의 실제 호출 승인이 아니다.
