# Step 7-C7 — 읽기 전용 AM 입력 어댑터

집 PC의 기존 `stock1`에서만 개발한다. 이 단계는 데이터 연결 준비와 로컬 dry-run이다.
운영 DB, 수집기, 스케줄러, D0 저장 로직을 변경하지 않는다. HTTP·OpenAI·Telegram 전송은 없다.
자동 실행 등록이나 운영 배포도 하지 않는다.

## 조사한 저장 구조와 현재 접근 범위

2026-10-29 집 PC `data/stock1.db`를 기존 `cross_adapter.readonly_database`로 읽었다.
DB 해시가 읽기 전후 동일함을 확인했다. 병원 PC의 최신 운영 원본은 현재 접근할 수 없다.
집 PC에 있는 mock 자료를 병원 PC의 실제 시장 관측으로 취급하지 않는다.

| 자료 | 저장 구조 및 연결 키 | 시각·단위와 현재 확인 범위 |
| --- | --- | --- |
| 0198 | `collections`: id, environment, api_id, qry_tp, status, record_count, test_mode, scheduled_at, collected_at. `ranking_records`: collection_id, rank, stk_cd, stk_nm, raw_json | 집 PC 11회/220행. 운영 모드 mock 성공 10회는 10월 20일 09:20~15:00. 별도 test_mode=1 성공 1회는 10월 19일. |
| 수집 시각 | `scheduled_at`은 예약 시각, `collected_at`은 `Store.begin`에 전달한 실제 실행 시작 | 응답 수신·서버 시장 시각·DB commit 시각 전용 열이 없다. 시작 시각을 수신 시각으로 복제하지 않는다. |
| 시세 | `quote_batches`와 `quote_observations`를 batch_id로 연결. collection_id/rank의 원본 종목과 requested_code도 대조. `quote_requests`는 배치 요청 기록 | 집 PC 3배치/21관측/10요청. mock ka10007 1개, ka10095 20개. 모두 10월 22일 저녁 요청이므로 10월 20일 관심도와 결합할 수 없다. |
| 시세 값 | `cur_prc`, `flu_rt`, `trde_prica`, `date`, `tm`, `normalized_json` | 가격 KRW, 등락률 percent, 거래대금 백만원이라는 코드상 계약. 정규화는 명세 기반이며 실측 단위·부호·시각 검증 완료를 뜻하지 않는다. date/tm은 일부 관측에서 누락돼 있다. |
| 0186 | 별도 `turnover_runs`, `turnover_pages`, `turnover_rows`. run_id/page_no/row_no로 연결. raw_json·normalized_json 병존 | `ka10032`, trde_prica 백만원이라는 명세. 요청 시작/종료, 환경, 거래소 필터, 행 검증 상태 저장. 서버 시각·실측 단위·누적 범위는 기본 미검증. 이번 집 PC 조사에서는 실제 독립 수집 DB 위치를 확인하지 못했다. |
| 시총 | `market_cap.parse_response`의 별도 export records. requested_code, raw.stk_cd, raw.mac, 요청/수신 시각, issues, fingerprint | mac 억원이라는 명세이며 실측 단위와 시총 기준은 기본 미검증. 현재 운영 SQLite에 시총 테이블이 확인되지 않았다. 병원 실수집 export 접근 불가. 과거 로컬 caps.json 등 검토 산출물을 검증된 실수집 시총으로 자동 사용하지 않는다. |
| 섹터 | `stock_sectors`와 `sectors`: stock_code, sector_name, updated_at. 집 PC 9섹터/24태그 | 현재 태그의 수정 시각만으로 과거 분류 유효 시각을 추정하지 않는다. 별도 `sector_tag_evidence`의 observed_at/registered_at 계약은 코드상 존재하지만 이번 어댑터는 이를 자동 승격하지 않는다. 검토 근거 필요. |

따라서 지금 확인 가능한 것은 **저장된 레코드·스키마·mock 환경·시작/요청 시각과 날짜 불일치**이다.
09:50 시장 주도주, 단위가 검증된 누적 거래대금, 당일 시총 적격성은 현재 자료로 검증 완료라 할 수 없다.

## 어댑터 계약

`kiwoom_collector.am_input_adapter`는 기존 읽기 전용 접근기를 재사용한다.
파일 존재를 먼저 확인하고 `mode=ro&immutable=1`, `query_only`와 SQLite authorizer로 쓰기를 차단한다.
Windows에서는 읽기 핸들로 동시에 쓰기/삭제 공유를 거부한다. 활성 WAL/SHM/journal이 있거나
원본이 바뀌면 중단하며 checkpoint, 백업 복사, 마이그레이션, sidecar 삭제를 하지 않는다.
안정된 오프라인 원본/승인된 별도 export를 사용해야 한다.

`read_sources`는 원본의 허용된 필드만 추출한다. 원본 파일 경로, 인증정보, 전체 API 응답은
분석 JSON으로 복제하지 않는다. 소스 SHA-256과 선택 필드의 record fingerprint를 보존한다.
시총은 `source_kind=collected_export`와 기존 parse_response 형태의 records를 가진 JSON을 받는다.
합성 export는 명시적인 synthetic 실행에만 사용할 수 있다. 현재는 **KRX 전용**이며 NXT/SOR 코드와
0186의 다른 거래소 필터는 보류한다.

### 검증 manifest

명세나 숫자 파싱만으로 `verified=true`를 생성하지 않는다. 별도로 검토한
`am-verification-manifest-v1`의 `records[record_fingerprint]`가 필요하다.
manifest 생성기는 운영 코드에 제공하지 않는다. 테스트에서만 합성 검토 근거를 만든다.
manifest는 HTS 대조 등의 **외부 검토를 기록하는 신뢰 경계**이며, 서명/독립 검증을 자동 수행하지 않는다.

필수 근거는 다음과 같다.

- 문서의 `synthetic`이 실행 모드와 일치해야 한다.
- 레코드마다 source_sha256, verified, environment, market=KRX, trade_date,
  market_verified, market_time_verified, observed_at, received_at, evidence_reference를 제공한다.
- observed_at ≤ received_at ≤ cutoff이며 둘 다 같은 거래일이어야 한다.
  저장된 요청 종료/응답 수신 시각이 있으면 received_at과 정확히 일치해야 한다.
  date/tm이 있으면 manifest의 시장 시각과도 일치해야 한다.
- 순위는 성공한 ka00198/qry_tp=1, 명시한 test_mode, 실제/예약/검증 관측의 같은 분,
  20행과 record_count 일치를 요구한다. 중복 종목·순위·슬롯은 거절한다.
- 시세는 성공한 요청, 같은 환경·정확한 parent collection/code/rank·batch API 연결을 요구한다.
  시세 테이블의 test_mode=1은 별도 진단 수집 구조이며 부모 순위 test_mode와 구분해서 기록한다.
  다른 거래일 시세를 이전 순위 시각으로 되돌려 붙이지 않는다.
- units와 unit_runtime_verified를 요구한다. trde_prica=million_KRW와
  turnover_period=intraday_cumulative인 경우만 KRW로 변환한다.
  mac=hundred_million_KRW와 market_cap_basis_verified가 함께 있어야 시총을 변환한다.
  가격은 price_sign_verified가 없으면 누락으로 둔다. 등락률은 직접 관측 flu_rt만 사용한다.
- 현재 태그의 시점 유효성도 검토해야 하며 updated_at보다 앞선 시점에 알려졌다고 주장할 수 없다.
- 두 소스의 동일 시각·종목·지표가 겹치면 임의 우선순위로 덮지 않고 거절한다.

데이터가 적거나 검증 근거가 없으면 rejected 사유와 unavailable_sources에 기록하고 비워 둔다.
진단상 accepted 레코드는 사용할 수 있는 필드가 있다는 뜻이며 모든 지표 검증 완료를 뜻하지 않는다.
개별 후보의 missing_evidence가 최종 적격 여부를 결정한다.

## 엔진과 출력

합성 입력은 기존 fixture 계약을 유지한다. 실수집 준비 입력은 별도
`am-momentum-readonly-v1`, `source_kind=readonly_adapter`를 사용한다.
AM 분석과 문자 포맷터는 실제 소스 구분을 그대로 유지한다. fixture-only 기존 입력에
live_test를 섞는 동작은 계속 거절한다.
보존 입력 키는 `input_document`이며 JSON·HTML·문자를 재계산 검증한다.
이전 C6 정책의 저장 보고서는 새 정책으로 소급 승인하지 않고 새 이름으로 재생성해야 한다.

오전 거래대금 절대 하한은 없다. 상대 누적 금액과 관측 증가 근거를 사용한다.
이전 관측 없으면 속도는 미확인이다. 오후 종베의 필터와 D0 저장은 호출하지 않는다.
시총 3,000억 초과와 기존 관심도·상승률·동반 강세 조건은 유지한다.
현재 실수집 보고서는 Telegram `deliver`에서도 차단한다. dry-run prepare/HTML 검증만 가능하다.

```powershell
# 검증 근거 없는 집 PC 원본: 순위를 보류하는 것이 정상이다.
.\.venv\Scripts\python.exe -B -m kiwoom_collector.am_input_adapter --ranking-db data/stock1.db --date 2026-10-20 --cutoff 2026-10-20T09:50:00+09:00 --environment mock --name step7c7-home-readonly-20261029

# 별도 검토 자료가 준비되면 --evidence, --turnover-db, --capital-json을 명시한다.
# --synthetic은 합성 DB 통합 테스트에만 사용한다. --send 옵션은 존재하지 않는다.
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_am_input_adapter.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

실제 집 PC dry-run에서 섹터 0개, 네 슬롯 검증 보류, network_called=false를 확인했다.
DB의 관측 자체가 없다는 뜻이 아니라 수신/시장 시각 검증 근거가 없어 분석에 승인하지 않았다는 뜻이다.

## 보완할 항목

병원 PC의 같은 거래일 순위·시세·0186·시총 원본을 안전하게 읽을 수 있는 위치가 필요하다.
응답 수신 시각, 실제 시장 기준 시각, 거래대금 단위·오전 누적 범위, 시총 단위·기준,
시점별 섹터 근거를 HTS 등과 대조해 manifest에 묶어야 한다.
이 작업은 기존 수집기를 바꾸지 않았으므로 저장 구조 보완은 후속 별도 작업이다.
여러 거래일 shadow dry-run 이후에만 실데이터 선정 신뢰도를 평가할 수 있다.
