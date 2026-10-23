# 주도섹터 레이더 v1

`sector radar`는 기존 SQLite의 성공한 ka00198 TOP20 회차를 날짜(KST)·환경으로 선택하여 독립 HTML을 생성합니다. 날짜의 모든 성공 회차를 각각 표시합니다. 기본 날짜는 KST 오늘, 환경은 mock이며 `--output`은 필수입니다. 다른 집계 구분(qry_tp)·테스트 회차는 화면에 표시하고 합산하지 않습니다.

```powershell
python -m kiwoom_collector sector radar --date 2026-10-20 --environment mock --db .\data\stock1.db --output .\data\sector-radar-20261020-mock.html
Start-Process .\data\sector-radar-20261020-mock.html
```

- 파랑: 조회순위상 인기. 관심 수와 TOP5/TOP10 포함 수는 순위 데이터만 사용합니다.
- 초록: 시세 상승이 확인된 주도주 후보. 시점이 일치하고 검증된 등락률이 양수인 경우입니다.
- 섹터 강도는 유효 관측의 상승·하락·보합 종목 수로 표시합니다. 분석 제외 수를 별도로 표시하며, 누락 시세를 보합/0으로 처리하지 않습니다.
- 시점 일치 조건: 순위 수집 시각, 요청 완료 시각, API 날짜+시각이 모두 존재하고 같은 KST 날짜이며, 순위 대비 요청 완료와 API 시각의 차이가 각각 ±300초 이내입니다. API 체결 시각이 오래된 경우도 보수적으로 제외합니다. 300초는 v1의 명시적 비교 허용 기준이며 동시 체결을 보장하지 않습니다.
- 10월 20일 순위에 10월 22일 사후 조회 시세를 붙이면 `시점 불일치·분석 제외`입니다. 원래 관측 시각과 시간 차이를 그대로 보여줍니다.
- 검증된 등락률은 저장된 normalized_json version=1, 지원 API, 기존 SPEC_VERSION 일치가 필요합니다. 과거 normalized_json 미보유 행을 재해석하거나 DB에 저장하지 않습니다.
- 가격 부호 미확정은 별도 표시합니다. 후보는 등락률 기준이며 가격 자체의 부호를 확정하거나 절댓값으로 변환하지 않습니다.
- 거래대금은 검증된 normalized_json의 turnover_source_unit=million_KRW 및 유한한 비음수 turnover_won만 원 단위로 표시합니다. 원본 백만원 값을 다시 변환하지 않습니다. 단위/값 미검증은 별도 표시하고 합산하지 않습니다.
- 복수 API/시도는 시점이 일치하는 유효 관측을 우선하고 그중 요청 완료가 최신인 것을 선택합니다. 유효 관측이 없으면 가장 최근 것을 보여줍니다. 모든 관측은 상세 근거에 남습니다.
- 현재 stock_sectors의 기존 태그와 출처를 재사용합니다. 자동 테마 생성은 없으며 복수 섹터를 허용합니다. 태그 없는 종목은 미분류로 표시합니다. 현재 태그를 과거 회차에 적용한다는 한계가 있습니다.

DB는 mode=ro + query_only + 읽기 트랜잭션으로 접근합니다. Store/마이그레이션/자격 증명/API 호출이 없으며 시세 테이블 없는 구형 DB도 시세 미수집으로 처리합니다. HTML은 내장 CSS와 네이티브 details만 사용하여 네트워크·스크립트 없이 열립니다. 기존 dashboard/workbench/스케줄에는 연결하지 않습니다.

## 실제 DB 스키마 확인 (2026-10-23)

main d9474c3의 data/stock1.db를 읽기 전용으로 확인했습니다.

| 테이블 | 확인한 키·연결 |
|---|---|
| collections | id PK, collected_at, environment, api_id, qry_tp, status, test_mode, scheduled_at |
| ranking_records | (collection_id,rank) PK, (collection_id,stk_cd) UNIQUE, stk_nm, raw_json |
| stocks | stock_code PK, stock_name, updated_at |
| sectors | sector_id PK, sector_name UNIQUE |
| stock_sectors | (stock_code,sector_id) PK, source, created_at, updated_at |
| quote_batches | id PK, collection_id FK, environment, api_id, spec_version, status, plan_json |
| quote_observations | (collection_id,rank,api_id,test_mode,attempt) PK, batch_id, requested_code, request_started_at/finished_at, raw_json, issues_json, normalized_json, 원본 시세 필드, request_id |

순위는 10월 19일 1회·10월 20일 10회(mock 성공), 시세에는 10월 22일 관측과 normalized_json이 NULL인 기존 행이 존재했습니다. 레이더는 실제 FK와 requested_code·환경·API를 확인하여 연결합니다. stocks는 태그 카탈로그이며 종목 표시명은 해당 순위 회차의 stk_nm을 사용합니다. 스키마를 변경하지 않았습니다.

검증:

```powershell
python -m unittest discover -s tests -v
```
