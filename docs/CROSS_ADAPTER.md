# Step 5-E 독립 읽기 전용 어댑터

기존 cross_analysis.py, Radar/Bubble, 정기 수집기와 DB 스키마를 변경하지 않는다. 새 cross_adapter.py가 0198 운영 DB와 ka10032 독립 DB를 읽고 출처를 보존한 JSON 및 진단 표를 만든다. API/OAuth/키 로딩/주문 기능은 없다.

## 원본 보존

Windows에서는 GENERIC_READ + FILE_SHARE_READ 핸들로 쓰기·삭제 공유를 허용하지 않은 상태에서 SQLite mode=ro, immutable=1, query_only를 적용한다. SQL authorizer로 DML/DDL/PRAGMA 변경도 거부한다. 이미 쓰기 핸들이 있거나 WAL/SHM/journal이 있으면 중단하며 체크포인트·DB 복사·복구·스키마 초기화를 하지 않는다. 원본 파일의 SHA-256을 읽기 전후 비교한다. Windows 이외에서는 writer_exclusion=false로 명시하며 동일 수준의 OS 강제 배타성을 보장하지 않는다.

원본 DB는 복제하지 않는다. 요청된 독립 JSON에는 분석에 필요한 원문 행·환경·시장·조회 조건·관측 시각·회차 ID·출처·해시를 내보낸다. test_mode와 source_kind는 소스별로 보존한다. 운영 0198과 일회성 테스트 0186 연결은 이 어댑터의 진단 전용 경로이며 기존 Step 5-D의 혼합 차단 기본 정책은 그대로다.

## 코드·유형

- 코드를 int로 변환·패딩·대소문자 변환·접미어 제거하지 않는다. 이름 조인도 하지 않는다.
- 기존 숫자 6자리 코드는 legacy_numeric_format으로 기존 형식 호환성을 유지한다. 이는 개별 상장 여부를 공식 마스터로 전수 확인했다는 뜻이 아니다.
- 영문 포함 6자리 코드는 단순 정규식 일치만으로 허용하지 않는다. 별도의 검토된 code_registry에 verified=true, 공식 근거 URL, 유형이 있어야 official_evidence로 허용한다.
- 형식 오류와 공식 유효성 미확인을 구분해 원본 행을 review 목록에 남긴다. 중복 코드/순위도 임의 선택하지 않는다.
- docs/code_registry_20261027.json은 이번 실제 11개 영문 포함 코드 중 발행 운용사 공식 문서로 확인한 ETF 6개다. 나머지 5개는 원본 그대로 검토 보류한다. 허용하지 않았다는 이유로 무효 종목이라고 단정하지 않는다.
- 유형이 없는 숫자 코드와 미검증 코드의 유형은 unknown이다. ETF/ETN/SPAC 등을 이름에서 추측하지 않는다. 현재 섹터 태그는 출처·수정 시각을 보존하며 과거 관측 당시 분류라고 확정하지 않는다.

공식 KRX 공시에도 영문 포함 단축코드 사례가 있다: [A0190G0 사례](https://kind.krx.co.kr/external/2026/05/08/000378/20260508001006/68152.htm), [A0126Z0 사례](https://kind.krx.co.kr/external/2025/11/20/000725/20251120002094/68156.htm). 이는 코드 체계에 영문이 존재한다는 근거이며 이번 11개 모두의 개별 검증을 대체하지 않는다. API 원문에는 A 접두사를 붙이거나 제거하지 않는다.

## 시각·시장·커버리지

0198의 collected_at/scheduled_at과 행의 raw_json을 보존한다. 기록되지 않은 HTTP 요청/수신 시각은 null이다. 0186의 request_started_at/request_finished_at, 바인딩된 HTTP 근거가 있으면 response_received_at/HTTP Date를 각각 보존한다. HTTP Date나 수집 시각을 시장 데이터 기준 시각으로 사용하지 않는다.

같은 KST 날짜에서 절대 관측 시차가 300초 이내면 collection_window_provisional=true다. 300초 포함, 301초는 제외다. 이는 사용자 지정 잠정 관측 기준이며 시장 시점 정합성 검증은 아니다. market_time_verified=false, candidate_confirmed=false를 항상 유지한다.

0198에 ka10032와 동등한 시장/관리/거래소 조건이 저장되지 않았으므로 market=null로 둔다. 양쪽 조건이 누락되면 unverified, 명시값이 다르면 mismatch, 같아도 declared_match_unverified로 표시한다. 같은 mock이라는 이유로 조건 일치를 추정하지 않는다.

0186 status=partial, pagination_complete=0, 페이지 수·cont-yn=Y·100개 원문을 보존한다. 누락 종목을 거래대금 0이나 저유동성으로 취급하지 않는다. TOP5 반복 횟수는 선택 시점까지 동일 KST 날짜·환경·qry_tp·운영/테스트 구분의 성공 관측만 센다. 검토 중인 다른 코드가 있는 회차에서도 유효한 코드의 실제 반복은 유지하지만 중복/잘못된 순위 등 모호한 회차는 제외한다.

## 분석 필터

1. 서버 원본 now_rank가 40 이하.
2. Decimal(trde_prica) × 1,000,000 >= 선택 금액.
3. 최소 금액은 500억원(기본: 50,000백만원) 또는 1,000억원(100,000백만원).
4. 코드 검토를 통과한 행만 분석 대상에 넣는다. 필터는 원본 100개에 분석 시점에 적용하며 DB/API 반환 수는 변경하지 않는다.

각 단계의 건수, 코드 검토, 중복·누락, 제외 사유를 별도 표시한다. 제외 사유는 한 행에 복수일 수 있으므로 사유별 건수를 단순 합산하지 않는다. 원본 정확 코드 교집합, 코드 검토 후 교집합, 필터 후 교집합을 구분한다.

금액 필터는 공식 선언 단위에 의존한 잠정 분석이다. unit_runtime_verified=false를 유지하며 HTS 0186 비교 전까지 실측 금액으로 확정하지 않는다. TOP20은 섹터 탐지, TOP5는 우선 검토, 나머지는 보조 관찰이다. 필터를 통과해도 모든 종목은 검증 보류이며 자동 후보 확정·점수·가중치·주문은 없다.

## 실행 예

```powershell
python -B -m kiwoom_collector.cross_adapter `
  --ranking-db data/stock1.db `
  --turnover-db data/step5c4-mock-20261027/ka10032-mock-once.db `
  --ranking-at 2026-10-27T15:00:00+09:00 --environment mock `
  --registry docs/code_registry_20261027.json `
  --http-evidence data/step5c4-mock-20261027/live-http-evidence.json `
  --min-turnover-eok 500 --format markdown
```

실행 가능한 프로젝트 Python을 사용한다. 이번에는 기존 작업용 Python 3.11.9로 검증했다. 출력은 stdout이며 원본 파일을 수정하지 않는다. 1,000억원은 --min-turnover-eok 1000을 사용한다. 바쁜 DB에 대한 재시도·복사·체크포인트는 자동 수행하지 않는다.

전체 회귀는 별도 소스 복사본·임시 테스트 DB에서 실행하며 운영 DB/.env 접근과 외부 네트워크를 차단한다. 실제 분석 결과는 data/step5e-compat-20261027/에 보존한다.
