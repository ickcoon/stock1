# C12-I 0198·0186 분석 후보 필터 연결

2026-10-31. 이번 단계는 코드 연결 및 합성 검증만 수행했다. 실제 0198/0186 DB 조회 및 실데이터 전후 수치 검증은 다음 단계다.

## 연결 구조

기존 am_input_adapter.adapt → AM_MOMENTUM → am_briefing 경로에 명시적 candidate_filter 옵션을 추가했다.
0198은 ka00198 collections/ranking_records, 0186은 ka10032 turnover_runs/pages/rows를 기존 읽기 전용 어댑터로 읽는다.
0186의 기존 시간·단위·환경·KRX·증거 manifest 검증을 유지한다. 0186이라는 화면 명칭을 API ID로 사용하지 않는다.

candidate_filter=True에는 master_db가 필요하며 selection_cutoff_at 시점의 C12 preview/apply_candidates를 am_candidate_adapter.enrich/join으로 연결한다.
현재 마스터를 과거 관측에 소급 적용하지 않는다. cutoff와 classification.at이 다르거나 합성/실제 출처 범위가 다르면 거부한다. KRX만 허용한다.

이 모드에서는 기존 strict am_master_adapter.enrich를 실행하지 않는다. strict 상품 검증 미완료로 기존의 검증된 관측 시총/섹터를 일괄 지우는 것을 피하고, C12 상품 범위만 별도 적용한다.
기존 시총·섹터의 증거 manifest·관측 시각 검증은 그대로이며 C12의 proposed 섹터를 가져오거나 확정하지 않는다.
candidate_filter=False인 기존 기본 경로는 유지된다.

## 코드 정규화와 보존

숫자 문자열은 앞자리 0을 보존/6자리 보충하며 정수는 문자열로 변환한다. 문자열의 바깥 공백 제거 및 영문 대문자화를 수행한다.
float/bool/잘못된 형식 및 _NX/_AL 등 접미사 코드는 임의 절삭하지 않는다.
정규화 후 동일 snapshot 코드/종목 metric 키가 충돌하면 거부한다.
기존 manifest 증거 검증은 정규화 전 source record로 수행한다.

원본 TOP20 20개를 삭제하거나 재순위화하지 않는다. 원본 순위, 조회 반복 이력, 시총/거래대금/가격 metric, source reference를 유지한다.
정규화 전 ranking source도 candidate_filter.original_ranking_sources에 남긴다.
0186 raw now_rank와 stored_turnover_rank, 거래대금 원문, 상태/시간/단위 증거는 진단 자료에 보존한다.
기존 metric record fingerprint를 바꾸지 않기 위해 새 순위 진단 필드는 별도 diagnostic_records로 전달한다.

## 선택 및 진단

- analysis_candidate만 오전 후보에 통과시킨다. 이는 일반 보통주 verified 또는 최종 주도주 확정을 의미하지 않는다.
- confirmed_excluded/provisional_excluded/pending/unmatched는 각각 별도 groups 목록과 reason_counts로 출력한다.
- 미매칭 종목은 master_code_unmatched, 코드 형식 불량은 invalid_stock_code로 남긴다. invalid_code_rows는 고유 코드 수와 별도다.
- 원본 source record별 observation에 정규화 코드·원문·C12 confidence/근거를 남긴다. 수치 증거 검증 실패는 기존 adapter_diagnostics에서도 확인한다.
- 0186만 있고 TOP20에 없는 종목도 진단한다. 상품 범위 통과는 가능하지만 반복 TOP20 근거가 없으므로 AM 리더로 자동 승격하지 않는다.

AM_MOMENTUM의 원본 20개 관측 완전성·순위 반복 수를 유지하면서, 차단된 종목은 insufficient_evidence 및 product_candidate_filter_blocked로 남긴다.
차단된 종목의 섹터를 후보 동반 상승 근거에서 제외하여 다른 후보의 동반 상승을 부풀리지 않는다. 원본 섹터/metric은 input_document에 보존한다.
브리핑 후보 및 섹터 리더에는 분석 후보만 들어간다.
시총 조건·반복 TOP20·섹터 동반 상승·리더 순서 정책은 그대로다.
오전 거래대금 절대 하한은 None이다. 기존 cross_adapter의 과거 500/1000억 진단 필터를 AM 경로에 가져오지 않았다.

## 합성 결과

| 검증 사례 | 필터 전 고유 종목 | 상품 후보 통과 |
|---|---:|---:|
| 0198 TOP20 | 20 | 16 |
| 별도 0186 DB 연결 | 9 | 5 |

TOP20 합성 데이터의 나머지는 확정 제외 1, 잠정 제외 1, pending 1, 미매칭 1개다.
추가 미매칭 0186 전용 코드 1개와 코드 형식 불량 행 1개도 진단하는 별도 사례를 검증했다.
이 사례의 합집합은 유효 고유 코드 21개 중 분석 후보 16, 확정 제외 1, 잠정 제외 1, pending 1, 미매칭 2개다. 형식 불량 1행은 별도 집계다.
reports/c12i-synthetic-integration.json에 이 사례의 재현 가능한 분석 결과를 저장했다.
0186의 매우 작은 누적 거래대금(원문 0.001 백만원)도 단위 검증 후 보존하며 절대 거래대금 하한 때문에 탈락시키지 않는 테스트를 포함했다.

신규 합성 테스트 8개 + 기존 관련 테스트 121개 = 129개 PASS.
관련 범위: AM candidate adapter/input adapter/momentum/briefing/C10 연동, C12 후보/eligibility/preview, cross adapter.
전체 회귀 테스트는 실행하지 않았다. 합성 SQLite 생성/수정은 임시 테스트 DB에 한정하며 읽기 어댑터 실행 전후 바이트 불변을 검사했다.
실제 API 호출·개발/운영 DB 쓰기·운영 수집기/스케줄러 변경·Commit/Push는 없다.

## 사용과 다음 실제 검증

기존 am_input_adapter CLI에 --master-db와 --candidate-filter를 함께 지정한다. 기존 --ranking-db/--turnover-db/--capital-json/--evidence/--timing-policy 및 날짜·cutoff·환경 옵션은 유지된다.
API 호출이나 운영 설정 활성화는 포함하지 않는다. 별도 모듈에서 새 endpoint/수집기를 만들지 않았다.

다음 단계는 안정적으로 닫힌 기존 0198/0186 DB와 검토된 시간·단위 증거 manifest를 선택해 read-only dry run을 수행하는 것이다.
같은 cutoff의 마스터 이력, 관측 환경, 데이터 출처를 확인한 후 실제 전후 후보 수·미매칭·보류 목록·섹터 미확정·시총/거래대금 증거 누락을 보고한다.
당일 오전 이후에 등록된 마스터 이력을 과거 오전에 사용하면 미매칭/pending이 늘어날 수 있으며, 이를 해결하려고 미래 이력을 소급 적용하지 않는다.
실제 실데이터 통과 수는 이번 합성 결과나 기존 마스터 후보 2,556개에서 추정하지 않는다.
