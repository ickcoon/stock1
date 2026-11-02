# C12-AE 읽기 전용 Export 개발·검증

2026-11-02 KST. 브랜치 `feat/c14-chart-integrated-20261101`, HEAD `4f0c2c2`. 집 PC 합성 SQLite fixture로만 개발·검증했다. 병원 실전 DB·설정·런처·Collector에는 접근하지 않았다. Commit·Push·Merge 없음.

## 확인한 저장 스키마

| 저장 코드/모델 | 실제 저장 필드와 의미 |
| --- | --- |
| `storage.py`: collections | id, collected_at(수집 시작), scheduled_at(예정), environment, api_id, qry_tp, test_mode, status, record_count. response_json/error/diagnostic_json은 Export 제외 |
| ranking_records | collection_id, rank, stk_cd, stk_nm, raw_json. 코드·순위 및 raw에 실제 있는 dt/date/tm/cntr_tm만 허용 목록으로 추출. 이름과 전체 raw는 제외 |
| quote_batches/quote_observations | 기존 adapter가 지원하는 10007/10095의 요청 시작·종료, 가격·등락률·거래대금 및 API date/tm. 실제 존재하는 경우만 읽음 |
| `turnover_rank.py`: turnover_runs | api_id=ka10032, environment, source_kind, test_mode, status 및 요청 프로필. 기존 모델의 live_test/test_mode=1을 실전 환경 또는 검증 완료라는 뜻으로 바꾸지 않음 |
| turnover_pages | 페이지별 started_at/finished_at, HTTP/return/status. finished_at은 수신 시각이며 시장 시각 아님 |
| turnover_rows | run/page/row 식별자, stock_code, turnover_rank, validation_status, raw_json, normalized_json. raw stk_cd/now_rank/trde_prica와 실제 저장된 단위 선언만 추출 |
| `master_data.py`: stock_master/history, sector_master, stock_sector, market_cap_cache | 명시적으로 제공한 별도 마스터만 기존 read_master/Queries로 조회. 원래 등록·갱신·유효기간·시총 API 관측/수신 시각을 보존 |
| C13 TokenManager/ProtectedTokenCache | 인증 관리이며 시장 관측 DB가 아님. 저장 파일·인증 내용은 읽거나 Export하지 않음 |

현재 Workflow는 시점별 0186 DB를 사용한다. 이를 지원하기 위해 `am_input_adapter.adapt`의 turnover_db 입력을 기존 단일 경로 외에 list/tuple도 받도록 최소 확장했다. 같은 기존 read_sources 검증 루프를 사용하며 단위·시장·시각·선정 정책과 수집기는 수정하지 않았다. Export CLI에서는 `--turnover-db`를 반복 지정한다. 동일 DB를 중복 지정하면 거부한다.

## 재사용과 읽기 전용 보장

- `am_input_adapter.read_sources/adapt`: 기존 DB 모델, source fingerprint/SHA 결합 manifest 및 단위 변환 재사용.
- `am_data_diagnostic.proof_for`: 시장 시각·단위 검증 근거의 출처/범위/수신 시각 결합 검사 재사용.
- `cross_adapter.readonly_database`: mode=ro, immutable, query_only, SQL authorizer, WAL/SHM/journal 거부, 전후 SHA-256 검사. Windows에서는 읽기 핸들로 writer/delete 공유를 거부한다. 모든 입력 DB에 이 가드를 전체 Export 동안 유지한다.
- `master_data.read_master/Queries`, `am_candidate_adapter.enrich`: 당시 마스터 조회와 기존 적격성 정책 사용. 적격성은 저장 마스터에 대한 기존 정책 평가 결과임을 명시하며 저장된 매매 확정 신호라고 표현하지 않는다.
- `morning_briefing.freeze`: 기준시각 이후 관측/수신 자료 제외.
- 기존 privacy/scan: 마지막 허용 필드 결과도 검사하며 실패 시 파일을 만들지 않는다.

새 SQL은 기존 projection에 없는 raw API 시각과 normalized_json의 저장 단위 선언을 조회하는 SELECT뿐이다. Store·MasterStore·마이그레이션·UPDATE/INSERT/DELETE·checkpoint는 실행하지 않는다. 자동 backup/copy/Collector 중단도 하지 않는다.

## Export JSON

기존 C12-AD bundle 스키마를 그대로 사용한다:

```text
schema_version: c12-replay-input-v1
documents: 기존 am-momentum-fixture-v1 또는 am-momentum-readonly-v1 입력 객체들
export:
  version: c12-ae-v1
  trade_date / from_time / until
  source_summaries: 역할·DB SHA-256·hash_unchanged
  record_counts: 원본 역할·SHA·조회 레코드 수
  observations: 허용 필드의 원시 관측 목록
  omitted_records: 범위 밖·시각 부재·다른 환경 때문에 제외한 레코드의 정적 사유
  master_snapshots: 명시 제공 마스터에서 실제 조회 가능한 자료
  issues / unavailable: 변환 실패 시점·누락 입력
```

observations에는 원본 API, 코드, 순위, 수집 시작/예정/요청 시작/수신 시각, 별도 검증된 시장 관측 시각, 거래대금 원문과 단위·출처·검증 여부가 있다. raw 및 저장 rank의 문자열/정수 타입을 유지한다. 동일 코드가 여러 시점에 나타나면 각각 보존한다. `180,000` 같은 원문 숫자 표현도 정규화한 문자열로 덮어쓰지 않는다.

시장 시각이 없으면 null 및 unavailable로 표시한다. collected_at/finished_at을 시장 시각으로 복제하지 않는다. 실제 API가 제공한 dt/tm/date/cntr_tm은 stored_api_fields에 원형 숫자 문자열로 별도 보존하며 그 자체를 검증된 시장 시각으로 승격하지 않는다. manifest로 검증한 시각만 market_observed_at으로 표시한다.

저장 normalized_json의 `turnover_source_unit`, `unit_spec_verified`, `unit_runtime_verified`는 실제 존재할 때 보존한다. 사양상 million_KRW라는 선언과 실측 단위 검증은 별개다. 바인딩된 검증 manifest가 없으면 해당 단위를 리플레이의 검증된 KRW로 추정 변환하지 않는다. 정상 manifest가 있는 경우 기존 adapter가 million_KRW→KRW 변환을 수행한다. 원문 금액·단위는 export.observations에 함께 남는다.

자유 텍스트 이름·사용자 ID·request/run ID·경로·전체 raw/response/proof payload는 전달하지 않는다. 증거의 자유 텍스트는 digest로 대체한다. 섹터 이름은 기존 BROAD_SECTORS에 있는 명칭만 허용하고 다른 문자열은 누락 표시 또는 리플레이에서 제외한다. 이는 임의 텍스트·개인정보 유출을 막는 Export 제한이며 원래 DB나 선정 정책을 변경하지 않는다. 시총은 실제 저장된 cache 값과 원본 시각만 포함한다. 마스터 메타데이터의 관측/등록 시각도 cutoff 시각으로 새로 만들어 채우지 않으므로 오래된 근거는 리플레이에서 보류될 수 있다.

## 시각 필터 및 C12-AD 연결

CLI from-time/until은 KST HH:MM이다. 기본 09:00~09:50. 요청 범위에 기존 AD 시점 09:40/45/50 중 하나가 포함돼야 한다. 원시 거래 자료는 검증된 수신 시각, 저장된 수신 시각, 또는 없을 때 명시적으로 구분한 수집/요청 시작 시각으로 필터한다. 알려진 수신 시각이 until 뒤이면 시작 시각으로 되돌려 포함하지 않는다. 메타데이터는 until 이전에 알려진 원본 갱신·등록 시각을 유지한다.

documents는 요청 시점별 기존 adapter 입력이고, AD의 `load`가 그대로 읽는다. AD 소스·입력 스키마 변경은 없다. adapter 변환이 실패한 시점은 빈 입력과 export.issues를 생성한다. 원시 안전 관측은 따로 보존된다. 정상 변환이어도 manifest나 당시 적격성 근거가 없으면 AD에서 보류가 정상이며 Export 성공이 후보 선정 성공을 뜻하지 않는다.

## 실제 집 PC 실행

기존 합성 fixture가 만든 임시 SQLite 바이트만 로컬 `reports/c12ae-home-20261102/ranking.db`, `0186.db`에 저장해 사용했다. 병원 DB 복사본이 아니다.

```powershell
.venv/Scripts/python.exe -m kiwoom_collector.c12_export --ranking-db reports/c12ae-home-20261102/ranking.db --turnover-db reports/c12ae-home-20261102/0186.db --evidence reports/c12ae-home-20261102/manifest.json --date 2026-10-29 --environment mock --synthetic --output reports/c12ae-home-20261102/verified-1.json
.venv/Scripts/python.exe -m kiwoom_collector.c12_replay --input reports/c12ae-home-20261102/verified-1.json --date 2026-10-29 --output reports/c12ae-home-20261102/verified-replay.json
```

새 output 파일만 허용한다. 이미 실행한 경로는 재사용하지 말고 새 파일명을 지정한다. 일부 시간 범위는 `--from-time 09:30 --until 09:45`. 별도 마스터는 실제 존재하는 경우 `--master-db <명시한 경로>`. 0186 파일 여러 개는 `--turnover-db <0940 파일> --turnover-db <0950 파일>`로 지정한다. 경로들은 JSON에 들어가지 않는다.

실제 CLI 결과: phases=3, observations=24. 같은 입력을 두 번 Export한 JSON SHA-256은 모두 `50E57DAA7CFE0FCCA8F47F9217210E07BCC8C4BF0BC29B4AB3726A9E44CE0230`였다. AD 연결은 09:40/45/50 모두 `섹터 선정 보류 / 보류`, 이유 `종목 적격성 검증 불가`였다. 해당 합성 CLI 입력에는 마스터를 주지 않았으므로 값을 채우지 않은 결과다. 별도 마스터가 있는 경우의 실제 저장 시총·섹터·적격성 전달은 테스트 fixture로 검증했다.

## 검증

- 신규 Export **13개 PASS**: 읽기 전용/원본 bytes·mtime 유지, 반복 결과 일치, 날짜·시각 범위, 코드 앞자리 0, 동일 코드 여러 시점, 복수 시점별 0186 DB, 단위 선언과 검증 구분, 원문 숫자 포맷, 수신 시각 부재, 종료 시각 이후 수신 배제, PII/인증/경로 payload 제외, 원본 마스터 시각 유지, sidecar 거부·SQL 쓰기 차단, Export CLI→AD CLI 연결.
- 관련 `test_c12*.py`: 최종 **55 PASS**(Export 13, 기존 AD 11, AC 12, X 12, chart 7).
- `test_am*.py`: **101 PASS**. 복수 경로 지원 후 기존 단일 DB adapter 동작 유지.
- `test_telegram*.py`: **53 PASS**.
- 관련 검사 합계 **209 PASS**, 최종 실패/오류 0. 전체 저장소 테스트를 수행한 것은 아니다.
- 실제 API/Telegram 호출은 소켓·requests·fetch/token·deliver를 차단하고 호출 0건을 assert했다. 실제 주문 경로도 호출하지 않는다.
- 비변경 대상은 임시 합성 DB다. 병원 실전 DB에 접근해 비변경을 검증한 것은 아니다.
- 기존 matplotlib 사용자 캐시 권한 경고는 임시 캐시로 대체되어 차트 검사가 통과했다. Export는 matplotlib을 사용하지 않는다.
- `git diff --check` 통과.

## 병원 PC에서 실행하기 전 확인 사항

1. 실제 런처/프로세스의 0198 DB와 시점별 0186 DB 경로·스키마를 사람이 확인한다. 이 도구는 경로를 탐색하거나 설정 파일을 읽지 않는다. 실제 경로를 확인했다고 주장하지 않는다.
2. 승인된 기존 snapshot 절차로 준비한 안정적인 자료 또는 writer/sidecar가 없는 원본만 사용한다. 수집 중인 DB·WAL/SHM/journal이 보이면 중단한다. 파일만 임의 복사하거나 강제 checkpoint/Collector 종료로 해결하지 않는다.
3. 실제 환경·날짜·test_mode·API/페이지 상태를 확인한다. 병원 실전 자료를 `--synthetic`으로 표시하지 않는다. environment=real과 source_kind=live_test가 곧 시장 시각·단위 검증을 의미하지 않는다.
4. source SHA/fingerprint와 정확히 결합된 기존 검증 manifest, 당시 마스터/시총 cache가 실제로 있는지 확인한다. 없으면 단위·시장 시각·후보 적격성은 미확인/보류가 정상이다. Export하려고 새 근거를 작성·추정하지 않는다.
5. 알려진 수신 시각이 09:50:00을 넘으면 최종 범위에서 제외된다. 실전 시각 계약과 자료 누락 빈도를 별도로 검토한다. 실제 정각까지 데이터가 도착한다고 가정하지 않는다.
6. 실행 전후 원본 SHA와 파일 상태를 확인하고 신규 목적지로 Export한다. JSON의 unavailable/issues 및 허용 필드만 들어 있는지 확인한 뒤 집 PC에 전달한다. DB·인증 파일·전체 로그를 함께 전달하지 않는다.
7. 집 PC에서 AD로 실행하되 기존 상태를 복원할 당시 근거가 없는 경우 검증 불가로 남긴다. 실전 Collector/런처/스케줄러/전송·주문 경로에는 연결하지 않는다.

## 이번 변경 파일

- 신규 `kiwoom_collector/c12_export.py`
- 기존 `kiwoom_collector/am_input_adapter.py`: 단일/복수 turnover 경로를 기존 read_sources로 읽도록 최소 확장
- 신규 `tests/test_c12_export.py`
- 이 문서

기존 사용자 파일·미커밋 C12-AC/AD 작업은 보존했다. requirements 추가, 운영 시간표·인증·DB·런처·Windows 스케줄러·main 변경은 없다.
