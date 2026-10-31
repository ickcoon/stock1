# C12-G 상품 유형 원본 근거 조사 및 자동 판정 설계

조사일: 2026-10-31 KST. 구현·DB 적용 전 설계다. C12-F 코드는 변경하지 않았다.

## 원본 및 보존 확인

대상은 diagnostics/master/c10-master.db의 기존 catalog_items와 catalog_runs다.
4,314개 raw_json에 code/name/listCount/auditInfo/regDay/lastPrice/state/marketCode/marketName/upName/upSizeName/orderWarning/companyClassName/nxtEnable 14개 필드가 동일하게 존재한다.
marketCode, marketName, regDay는 빈 값이 0개이며 companyClassName은 2,657개가 빈 값이다.
upName은 1,582개가 빈 값이다. 빈 값은 일반주식의 증거가 아니다.

catalog_items는 market_code/market_name을 별도 보존하고 companyClassName/orderWarning 등은 raw_json에 보존한다.
security_type/type_verified/type_evidence는 파생 판정이며 원본 필드와 다르다.
catalog_runs에는 적용 시각·원본 fingerprint·규칙 hash·출처가 있고 stock_history와 stock_sector는 별도 이력이다.
기존 수집 출처는 real/synthetic=false/api_response이며 적용 시각은 2026-10-31T00:14:07.114282+09:00이다.
저장 dataset.json의 정규화 fingerprint를 재검산해 DB와 일치 확인했으며 4,314개 raw_json과 원본의 불일치는 0개다.
이는 로컬 수집 파일 간 일관성 확인이며 원본 발행자의 전자서명 검증을 의미하지 않는다.

읽기 전용 mode=ro/immutable/query_only 및 Windows 쓰기 공유 차단을 사용했다. DB SHA-256 전후 동일:
`9da603a0ab476866a2f86dd3f8185c384093b72c70a921366f2a6d9bff357066`.

## 공식 명세에서 확인한 범위

2026-10-31 공개 [키움 공식 명세 JSON](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json)의 ka10099 항목을 확인했다. 공개 문서 조회만 했으며 OAuth·시세·종목정보 API를 호출하지 않았다.

- companyClassName: 회사분류, 코스닥만 존재. 상품 유형을 포괄적으로 확정하는 필드가 아니다.
- marketCode/marketName: 응답의 시장구분코드/시장명. 응답 코드별 정의는 비어 있다.
- mrkt_tp: 요청 시장 선택값에는 ETF/ETN/리츠/펀드 등의 코드표가 있다. 이 표를 응답 코드의 보장된 계약으로 그대로 간주하지 않는다.
- orderWarning: 투자유의 상태. 값 1은 ETF에만 전달된다는 설명이 있다. 0은 ETF가 아니라는 증거가 아니다.
- upName/upSizeName/state/regDay/listCount/nxtEnable은 각각 업종·규모·상태·상장일·주식수·NXT 여부로, 보통주/우선주를 가르는 직접 근거가 아니다.

## 실제 원본 후보와 신뢰 수준

| 유형 후보 | 실제 원본 조건 | 수 | 제안 상태 |
|---|---|---:|---|
| ETF | marketCode=8, marketName=ETF | 1,186 | 아래 1개 외 provisional |
| ETN | 60/ETN | 350 | provisional |
| ETN | 70/ETN(손실제한) | 1 | provisional |
| ETN | 90/ETN(변동성) | 8 | provisional |
| REIT | 6/리츠 | 23 | provisional |
| infrastructure_product | 2/인프라투자금융 | 2 | provisional |
| mutual_fund | 4/뮤추얼펀드 | 1 | provisional |
| SPAC | 10/코스닥 + companyClassName=스팩 | 64 | provisional |
| 합계 | 중복 없음 | 1,635 | 직접 근거 1 + 추가 검토 1,634 |

이 후보들은 종목명 정규식이 아니라 API 응답의 명시적 분류 필드에서 나왔다.
그러나 명시적 라벨이라는 사실과 검증된 자동 판정 규칙이라는 사실을 구분한다.
스팩 64개도 이름 추정이 아니지만 회사분류의 enum/유효성 계약은 공식 명세에 정의되지 않아 이번에 verified로 승격하지 않는다.
우량기업 466·중견기업 508·신성장기업 259·벤처기업 345·외국기업 15 등의 회사분류도 보통주 증거로 사용하지 않는다.

### 문서로 직접 지지되는 최소 자동 판정

종목 265690은 원본 orderWarning=1이고 marketCode=8/marketName=ETF와 일치한다.
따라서 C12-H에서 원본 일관성 및 시점·출처·충돌 검사를 통과하면 ETF라는 계산상 verified 판정과 excluded_type:ETF를 도출할 수 있다.
이는 DB type_verified를 수정한다는 뜻이 아니다. 기존 원본 및 저장 verified=0은 그대로 두고 별도 증거 결과를 만든다.
다른 상품군/수동 검토와 충돌하면 자동 확정하지 않고 pending으로 둔다.

### 나머지 1,634개

코드와 명칭의 조합은 유용한 근거지만 응답 enum 보장 또는 코드별 독립 상품 목록과의 대조가 추가로 필요하다.
이번 단계에서는 provisional 후보로만 두고 eligibility는 pending을 유지한다.
원본 응답 라벨을 검토한 버전별 규칙이 마련되면 확대 가능하나 이번 보고서가 해당 규칙의 자동 승인을 의미하지 않는다.

## 일반 보통주 판별의 한계

KOSPI 914 + KOSDAQ 1,829 = 2,743개이며 이 중 스팩 후보 64개를 빼면 2,679개다.
이 2,679개를 보통주로 간주할 수 없다. 보통주/우선주/기타 권리 구분 필드가 없고 제외 상품이 아니라는 것만으로 보통주가 증명되지 않는다.
최소 추가 근거는 종목코드별 증권 종류/주식 종류(보통주·우선주)가 명시된 권위 있는 목록 또는 근거·검토 시각이 있는 코드별 review다.
원본의 상장 시장·상장 상태·존재 여부·판정 기준 시각도 별도로 확인해야 한다.
이번에는 새로운 목록/API를 수집하지 않았다. 현재 원본만으로 자동 포함 가능한 종목은 0개다.

## 예상 수치

| 시나리오 | 포함 | 제외 | 보류 |
|---|---:|---:|---:|
| 현재 C12-F 그대로 / 이번 실행 결과 | 0 | 0 | 4,314 |
| C12-H에서 문서 근거 최소 규칙 구현 시 | 0 | 1 | 4,313 |
| 추가 근거 검토 후 1,635개 후보 전부 승인되는 경우에 한한 확대 시나리오 | 0 | 1,635 | 2,679 |

권고 기본값은 두 번째 행이다. 세 번째 행은 확정 예측이나 승인 완료 수치가 아니다.
최소 규칙의 제외 사유별 수는 ETF 1, ETN/REIT/SPAC/펀드/인프라/우선주 0이다.
현재 DB의 verified 수는 여전히 모든 유형에서 0이다.

## C12-H 최소 구현 범위

1. 읽기 전용 C12 조회에 원본 companyClassName/marketCode/marketName/orderWarning과 catalog run/코드/시각/원본 hash를 연결하는 증거 객체를 추가한다. raw_json과 기존 분류·시장·섹터 이력은 수정하지 않는다.
2. 순수 evidence evaluator를 만든다. 결과는 candidate_type, confidence(verified/provisional/pending/conflict), rule_id/version, raw_fields, source_reference, as_of를 담는다. DB verified와 계산상 verified를 별도 필드로 구분한다.
3. 최소 규칙은 orderWarning=1 + 8/ETF의 일치만 자동 ETF 판정한다. 원본 불일치·미확인 출처·기준 시각 이후 자료·기존 수동 검증과 충돌은 pending이다. 나머지 표의 정확한 조합은 provisional 후보만 반환한다. 이름은 판정에 쓰지 않는다.
4. C12-F의 미검증 판정 순서와 충돌하지 않도록 검증된 evidence 결과만 받는 별도 adapter를 둔다. 원본 레코드는 보존하고 파생 입력에서만 type_verified/type_evidence를 전달해 기존 decide를 재사용한다. provisional은 통과시키지 않는다. sector_turnover도 동일 evaluator/adapter를 호출해 별도 우회 경로가 없게 한다.
5. mutual_fund/infrastructure_product는 evidence 결과에서 표현하되 기존 C11 rules_document의 review 허용 유형 확장/DB 게시 경로는 이번 최소 범위 밖에 둔다.
6. 관련 합성 테스트: 직접 ETF 근거, 값 0의 비역추론, 코드·명칭 충돌, companyClassName=스팩 provisional, 이름만 일치, 보통주 비승격, 이력 cutoff, 수동 검증 충돌, 원본 불변, 집계 제외 및 중복 방지. 실DB 읽기 전용 예상 수치 재확인.

이번에는 새 정책 코드를 구현하지 않았다. 기존 C12-F 관련 합성 테스트 6개만 실행해 PASS했다. 새 설계의 판정 로직 테스트는 구현하는 C12-H에서 추가한다.
운영 환경·SQLite DB 쓰기·전체 회귀 테스트·Commit/Push 없음. 기존 미커밋 C12-F 변경은 그대로 보존했다.

기계 판독 감사 결과: reports/c12g-source-evidence-20261031.json.
