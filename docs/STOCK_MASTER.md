# Step 7-C10 — 별도 종목·섹터 마스터, 시총 캐시, 오전 관측 정책

## 구현 범위

개발 stock1-dev에만 구현했다. 운영 stock1 코드/DB/수집기/스케줄러/인증정보는 변경하지 않았다. 전체 상장종목 자동 수집, ka10001 실제 호출, Telegram/OpenAI 호출·전송 및 자동 운영 배포는 구현/실행하지 않았다. 통합 검증은 합성 관측만 사용한다.

개발 DB는 diagnostics/master/c10-master.db에 생성했다. 현재 **종목 0개, 산업 대분류 16개, 시총 캐시 0개**다. 전체 KOSPI/KOSDAQ을 담을 스키마·입력 경로를 만든 것이며 전체 종목을 이미 적재했다는 의미가 아니다. DB와 로컬 진단 산출물은 Git 제외, 코드·스키마·테스트·정책 설정·문서를 버전 관리한다.

## 데이터 구조

| 테이블 | 주요 필드와 역할 |
|---|---|
| stock_master | stock_code 문자열 PK, stock_name, market(KOSPI/KOSDAQ/UNKNOWN), listing_status(listed/delisted/unknown), verified, source, source_updated_at, registered_at |
| stock_history | 종목명·시장·상장 상태의 변경 전후 원본 및 출처, 등록 시각과 지문. as-of 조회는 당시 알려진 이력만 사용 |
| sector_master | stable sector_id, sector_name, kind(industry/theme), active, source, updated_at. 이름은 kind 안에서 유일 |
| stock_sector | code/sector FK, is_primary, proposed/verified/rejected, evidence/source, 원본 갱신 시각, valid_from/to, registered_at, source_sha256, record_hash. 복수 분류와 검토 이력 보존 |
| market_cap_cache | code+environment 별 이력, api/contract_version, raw_mac, 선언 단위·검증 단위, normalized_won, trade_date/observed_at, 요청/수신/등록 시각, source, verified, 허용 원문·검토 근거와 지문 |
| migration_log | 원본 SHA-256, 이관 등록 시각, 건수·기존 종목명 충돌 보고. 같은 원본 재실행은 idempotent |
| master_meta | 스키마 버전 및 synthetic 여부. 합성 마스터를 실수집 어댑터에 혼용하면 거절 |

코드는 선행 0을 보존하는 6자리 숫자/영문 대문자 문자열이다. 실제 관측된 0035S0 같은 형식도 허용하며 숫자로 변환하거나 다른 코드로 치환하지 않는다. 현재 마스터/캐시 연결은 KRX 기본 코드 기준이며 _NX/_AL 접미사를 자동 제거하지 않는다. 패턴 통과는 실제 상장 유효성 검증이 아니다.

산업 대분류: 반도체, 자동차, 로봇, 원전, 전력, 조선, 방산, 2차전지, 제약·바이오, 금융, 통신, 인터넷·게임, 건설, 화학, 철강, 우주항공. 프로젝트 분류 체계이며 공식 산업 분류로 주장하지 않는다. 시장 주도 테마는 kind=theme으로 별도 관리한다.

## 분류·누락·충돌 정책

- 검토 전 태그는 proposed, 검토된 유효 태그만 verified로 사용한다. 등록 시각이 기준 시각 이후면 당시 알려진 자료로 사용하지 않는다.
- 산업 대표 태그가 정확히 하나면 이를 C6의 대표 섹터로 연결한다. 대표 지정이 없고 유효 산업 태그가 하나뿐이어도 연결할 수 있다. 여러 산업 태그가 있는데 대표가 없거나 대표가 여러 개면 conflict로 보류한다.
- theme 복수 태그는 원래 보존하지만 산업 태그와 섞어 동일 기준의 TOP3를 부풀리지 않는다. C7 master_diagnostics에 보조 테마를 따로 남긴다.
- 태그가 없거나 검토 전이면 unclassified. 이름·가격 상승만으로 섹터를 추측하지 않는다.
- 종목명/상장 상태 변경은 이력에 남는다. 상장폐지·시장 미확인·미검증 종목은 캐시·브리핑 연결을 보류한다. 기존 캐시 이후 종목 정보가 바뀌면 stock_changed로 갱신 검토한다.
- 동일 시각의 충돌 시총은 conflict로 반환한다. 최근 자료가 만료/미검증이어도 예전 값을 임의로 대체하지 않는다. 환산 값과 검증 단위는 읽을 때도 다시 대조한다.
- 만료된 새 태그 때문에 이전 열린 태그가 되살아나지 않도록 동일 연결의 최신 검토를 먼저 선택한다. 조회 당시 알려진 자료와 효력 구간을 구별한다.

## ka10001 단위·파싱 조사

2026-10-30 [키움 공식 명세](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json)의 ka10001을 다시 확인했다. POST /api/dostk/stkinfo, 요청 stk_cd, 응답 mac의 공식 선언 단위는 **억원**이다. 기존 market_cap.parse_response를 그대로 재사용한다.

- mac은 원문 문자열을 보존하며 Decimal ×100,000,000으로 원화 환산 진단한다. 선행 0·양수 부호·소수는 보존하고 음수 시총·잘못된 수치·코드 불일치·API 실패는 검증 승인하지 않는다. float를 사용하지 않는다.
- flo_stk는 상장주식 필드이지만 공식 설명에 천원 단위가 기재된 충돌이 여전히 있다. 주/천주로 추측하지 않고 가격×주식 수 시총 재계산에 사용하지 않는다.
- cur_prc 부호를 임의 절댓값으로 바꾸지 않는다. 시총 mac 환산은 현재가 부호 해석과 구별한다.
- 기존 10/27 저장 mock 응답 JSON을 읽기 전용으로 재파싱했다. raw mac=9847037, 선언 단위 억원, 환산 진단=984703700000000원이다. **실측 단위·시총 기준 검증은 여전히 false**이며 실제 캐시에 검증 자료로 적재하지 않았다. 로컬 검토는 reports/step7c10-stored-cap-review.json에 보존했다. 새로운 API 호출은 0회다.
- 파싱이나 공식 단위 선언만으로 verified가 되지 않는다. 원문 지문/코드/환경에 결합된 외부 검토의 unit_runtime_verified, market_cap_basis_verified, 거래일·관측 시각·근거가 모두 있어야 verified_unit과 normalized_won을 승인한다. 이 검토 입력은 신뢰 경계이며 HTS 대조·서명을 자동 수행하는 기능은 아니다.
- **3,000억원 초과** 판정은 검증된 normalized_won에만 적용한다. 정확히 3,000억원은 통과하지 않는다. 기존 장마감 Step 6의 별도 정책은 변경하지 않는다.

캐시는 환경, 거래일, 원본 관측·수신 시각을 검사한다. 기본 TTL은 3,600초이며 수신 시각뿐 아니라 원본 관측 시각도 TTL 안이어야 한다. 전 거래일 자료는 TTL이 남더라도 당일 AM에 재사용하지 않는다. 조회 시각이 미래인 자료, 그때 아직 등록되지 않은 검토 자료도 제외한다.

Queries.refresh_plan은 캐시를 먼저 조회하고 active 종목의 missing/expired/unverified/stock_changed만 중복 제거해 제한된 갱신 요청 목록으로 반환한다. 기본 최대 10개, 설정 최대 20개이며 actual_api_calls=0이다. conflict·상장 미확인·상장폐지 종목은 먼저 검토해야 한다. 네트워크 실행 계층은 없다.

## 오전 관측 정책

[config/am_observation_policy.json](../config/am_observation_policy.json)을 C7/C8에 명시적으로 제공하면 am-window-v1을 적용한다.

| 설정 | 기본값 | 의미 |
|---|---:|---|
| receipt_grace_seconds | 45 | 기준 시각 이후 수신을 허용하는 시간 |
| observation_before_seconds | 90 | 기준 시각 이전 시장 관측 허용 범위 |
| observation_after_seconds | 30 | 기준 시각 이후 시장 관측 허용 범위 |
| capital_ttl_seconds | 3600 | 당일 검증 시총 캐시의 최대 관측/수신 나이 |

기준 09:50:00은 그대로 보존하고 판단 마감은 09:50:45로 별도 표시한다. 09:50:12 수신은 정상 범위 안에서 사용하되 원문 observed_at/received_at을 바꾸지 않는다. 09:49:00 관측은 범위 안, 09:48:00 관측은 기본 범위 밖이다. 서로 다른 거래일은 유예를 적용해도 결합하지 않는다. 시각/단위/가격 부호/원본 연결 근거가 아예 없는 자료를 허용 창만으로 승인하지 않는다.

09:40 예비 탐색은 09:20/09:30/09:40 세 슬롯, 09:50 최종 탐색은 네 슬롯의 저장·검증 완전성을 요구한다. 09:40:45/09:50:45 이전에 해당 유예 전체를 알고 있었다고 보고할 수 없으며 generated_at이 판단 마감보다 이르면 거절한다. 섹터/종목 수가 부족하면 TOP3를 억지로 채우지 않는다.

정책을 지정하지 않은 기존 C6~C9 보고서는 종전 strict 계약을 유지한다. 과거 결과를 새 정책으로 소급 재해석하지 않으며 새 이름으로 보고서를 생성해야 한다. 새 정책은 보고서의 observation_policy/decision_deadline_at과 보존 입력에 포함되어 재계산 검증된다. Telegram 문자 포맷은 기존 구조에 예비/최종 및 수신 반영 마감만 덧붙이며 실제 발송 코드는 호출하지 않는다.

C9-C에서 드러난 정규 시세 test_mode=0도 새 정책에서 지원한다. 부모 순위·코드·배치 API·환경은 계속 검증하고, 시세/배치 test_mode는 서로 같아야 한다. 정규 시세 0은 정규 부모 0에만 연결한다. 독립 진단 시세 1은 기존 계약도 유지한다. 기존 strict 보고서의 의미는 바꾸지 않는다.

## C7/C6 연결

am_input_adapter.adapt(..., observation_policy=정책dict, master_db=별도DB)를 사용한다. 기존 순위/시세는 원본 SHA-256·레코드 지문에 연결된 검토 근거를 계속 요구한다. 마스터가 순위 관측·실제 수신 시각을 만들어내지 않는다.

마스터에서 해당 시점에 알려진 종목/분류를 조회하고, 당일 유효 검증 시총 캐시를 공급한다. 분류의 observed_at/received_at은 **기준 시각의 참조 메타데이터 조회**를 뜻하며 시장/API 관측으로 표시하지 않는다. 원본 source_updated_at, registered_at, valid_from/to, record_hash는 master_diagnostics에 별도로 보존한다. 시총 관측·수신 시각은 원래 값을 유지한다. metadata 등록을 과거로 되돌려 이관하지 않는다.

기존 검토 섹터와 마스터 대표 분류가 충돌하거나 시총 소스를 동시에 제공하면 임의 우선순위로 덮지 않는다. 상장폐지/종목 미확인은 기존 연결이 있어도 보류한다. C6 AM_MOMENTUM과 거래대금 절대 하한 없음 정책, 3,000억원 초과·관심도 반복·동반 강세 조건은 유지한다.

## 입력·조회 API

`master_data.create_master`, `MasterStore.put_stock/put_sector/put_tag/put_capital`, `import_stocks`, `read_master`의 Queries.stock/classification/capital/refresh_plan을 제공한다. 모든 쓰기는 개발 diagnostics/master 아래 DB에만 허용하며 기존 DB를 자동 생성/마이그레이션하거나 운영 DB를 writer로 열지 않는다. MasterStore context의 변경은 한 트랜잭션이며 실패하면 롤백한다. read_master는 기존 C8 오프라인 안전 접근기를 사용하므로 작성 중인 마스터를 무조건 읽으려 하지 않고 보류한다.

```powershell
# 개발 마스터 신규 생성. 같은 이름이 있으면 실패하며 덮어쓰지 않는다.
.\.venv\Scripts\python.exe -B -m kiwoom_collector.master_data init --name new-master

# 사용자가 준비한 종목 JSON을 입력. 네트워크 수집 없음.
.\.venv\Scripts\python.exe -B -m kiwoom_collector.master_data import-stocks `
  --db diagnostics/master/new-master.db --input supplied-stock-list.json
```

종목 JSON 계약: schema_version=stock-master-import-v1, synthetic=false/true, records 배열. 각 레코드는 stock_code, stock_name, market, listing_status, source, updated_at, registered_at, verified(미지정 시 false)를 받는다. 출처의 상장·시장 검토 없이 verified=true를 넣지 않는다. 같은 파일의 중복 코드는 전체 거절한다. 태그/시총 입력은 위 Python API로 명시적으로 검토 상태와 근거를 제공한다. 자동 승인/자동 외부 갱신은 없다.

## 기존 자료 재사용과 이관

이전에 승인받아 만든 개발 스냅샷만 조사했다: 종목 133개, 섹터 10개, 태그 26개, 복수 태그 종목 4개. 반도체/2차전지 같은 대분류와 반도체 MLCC/소부장/테슬라 같은 세부 테마가 혼재되어 있다. 전체 KOSPI/KOSDAQ 목록이나 검증된 대표 산업 분류로 간주할 수 없다.

`migrate_legacy`는 **개발 폴더 안의 안정된 오프라인 원본만** 읽는다. 원본 DB 해시를 전후 검증하고 stock_sectors의 source/updated_at/sector_name 및 원본 해시를 보존한다. 기존 시장/상장 정보가 없으면 UNKNOWN/unknown/verified=false로 옮긴다. 태그는 일단 legacy theme/proposed로 보존해 이름 유사성만으로 산업 대표를 추측하지 않는다. broad industry로의 매핑·대표 지정은 별도 검토한다. 목적지에 종목명 충돌이 있으면 기존 이름을 덮지 않고 stock_conflicts에 기록한다.

```powershell
# 예시: 운영 원본이 아니라 이미 확보한 개발 스냅샷을 사용한다.
.\.venv\Scripts\python.exe -B -m kiwoom_collector.master_data migrate-legacy `
  --db diagnostics/master/new-master.db `
  --source diagnostics/online_snapshots/hospital-c9b-20261030-once/snapshot.db `
  --registered-at '2026-10-30T15:00:00+09:00'
```

위 실제 자료 이관 예시는 이번 단계에서 실행하지 않았다. 합성 이관에서 복수 태그·알파벳 코드·출처·갱신일·원본 해시 보존, 중복 재실행, 이름 충돌, 원본 불변성을 검증했다. 원본으로 운영 stock1 경로를 전달하면 개발 원본 제한으로 거절한다. 운영 수집 DB에 새 테이블을 추가하지 않는다.

## C7/C9 실행 예시 — 로컬 자료만

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.am_data_diagnostic `
  --ranking-db diagnostics/online_snapshots/hospital-c9b-20261030-once/snapshot.db `
  --master-db diagnostics/master/c10-master.db `
  --timing-policy config/am_observation_policy.json `
  --date 2026-10-29 --cutoff 2026-10-29T09:50:00+09:00 `
  --environment mock --name c10-local-reviewed
```

이번 단계에서는 실제 자료 재진단을 실행하지 않았다. 빈 마스터이거나 검토 근거가 없으면 보류가 정상이며, 위 명령만으로 후보 검증이 완료되는 것은 아니다.

## 테스트 및 검증

합성 테스트는 숫자/단위/지문·날짜/환경/TTL, 3,000억원 경계, 복수 태그/대표 충돌/미분류, 명칭 변경/상장폐지/시총 무효화, 이관 보존/롤백, 12초 수신 지연, 유예 경계, 오래된 관측/다른 날짜 차단, 09:40 예비/09:50 최종, 정규 test_mode=0, C7→C6→문자 포맷 재계산 검증을 포함한다. 외부 호출 없이 수행한다.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_master_data.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_c10_am_integration.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

2026-10-30 최종 전체 회귀 999개 PASS(164.973초). 마지막 캐시 거래일 처리 보완 후 마스터 16개와 통합 11개도 모두 PASS했다. 로그는 reports/step7c10-regression-final-20261030.txt, step7c10-master-final.txt, step7c10-integration-final.txt에 보존한다. 실제 자료의 단위/시총 기준 검증 완료, 전체 종목 적재 완료, 운영 배포 완료를 뜻하지 않는다.
