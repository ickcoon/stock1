# SQLite 데이터 모델

기본 경로: 프로젝트 루트의 `data/stock1.db`. 추가 패키지 없이 Python 표준 `sqlite3`를 사용합니다. WAL·외래 키·10초 잠금 대기를 사용합니다. API 요청 중에는 DB 쓰기 트랜잭션을 열어 두지 않습니다.

## collections — 수집 실행 이력

- `id`: UUID, 기본 키.
- `collected_at`: 회차 시작 시각, KST 오프셋 `+09:00`을 포함하는 ISO 8601 문자열(초 정밀도). API의 `dt`/`tm`과 다릅니다.
- `environment`: `mock`/`real`.
- `api_id`: `ka00198`, `qry_tp`: 요청 집계 구분.
- `test_mode`: SQLite INTEGER 0/1 (`false`/`true`). 기존 DB에는 비파괴적으로 열을 추가하며 기존 회차는 0으로 유지합니다. `collect --test-now`만 1입니다.
- `status`: `running`, `success`, `empty`, `error`, `interrupted`.
- `record_count`: 저장한 1~20위 종목 기록 수. 실패·빈 결과는 0.
- `error_stage`: `api_request` / `response_validation` / `db_store`. 이전 버전 오류는 NULL.
- `diagnostic_json`: 인증정보·필드값을 제외한 자료형·행 수·HTTP 상태·숫자 API 코드·실패 발견 위치의 JSON. 오류 시 기록.
- `error`: 프로그램에서 만든 안전한 오류 설명. HTTP 상태나 API 오류 코드는 설명에 포함될 수 있으나 서버 메시지 원문·요청 본문·토큰은 저장하지 않습니다.
- `response_json`: 완료된 조회 페이지들의 JSON 배열. 각 페이지에서 공식 종목 필드와 `return_code`만 보존합니다. 오류·중단으로 완료하지 못한 조회에는 이 값이 없습니다.

동일 `(collected_at, environment, api_id, qry_tp)`를 UNIQUE로 제한합니다. KST 초 정밀도로 정규화한 같은 시각은 API 호출 전에 원자적으로 중복 차단합니다. 동일 시각이라도 환경·집계 구분이 다르면 별도 데이터입니다. 시간 간격이 다른 회차는 서버 기준 `dt`/`tm`이 같아도 실제 관측 이력으로 누적합니다. 실패한 같은 시각의 회차를 덮어쓰지 않으며 재시도는 다음 주기에 새 ID로 기록합니다.

## ranking_records — 종목별 관측

- `collection_id`: `collections.id` 외래 키.
- `rank`: API `bigd_rank`를 검증한 정수(1~20), 분석·정렬용.
- `stk_cd`, `stk_nm`: API 문자열 그대로. 미제공 시 NULL.
- `raw_json`: 공식 종목 필드의 원래 문자열 값·필드명을 보존한 JSON. `bigd_rank`도 여기에는 원래 문자열로 유지합니다. 없는 필드는 생성하지 않습니다.

기본 키 `(collection_id, rank)`, 추가 UNIQUE `(collection_id, stk_cd)`로 한 회차의 중복 순위·중복 종목을 막습니다. 종목코드 인덱스와 수집 시각 인덱스로 추후 시계열/섹터 테이블 연결이 가능합니다. 별도 회차에는 동일 종목을 계속 저장합니다. NULL 종목코드에는 SQLite UNIQUE의 일반적인 NULL 규칙이 적용됩니다.

순위 기록 전체 삽입과 이력 완료 처리는 하나의 트랜잭션입니다. 중간 오류·Ctrl+C에는 해당 회차의 종목 기록 전부를 롤백합니다. 무결성 실패는 회차 오류로 기록하고 다음 주기로 넘어갑니다. 디스크/권한/잠금 등 일반 DB 오류는 프로그램을 종료하며 이때 남은 `running`은 미완료 회차입니다. 강제 종료한 회차도 `running`으로 남을 수 있으며 자동 삭제/덮어쓰지 않습니다.

## 원본 보존과 보안 경계

공식 근거는 [API 명세 조사](API_SPEC.md)의 JSON과 샘플입니다. 종목 필드 12개는 공식 명세상 모두 String입니다. API 응답에서 받은 숫자 문자열·부호·공백·선행 0을 보존하고, 예상 밖 자료형은 임의 변환하지 않고 해당 회차를 실패 처리합니다.

`response_json`은 네트워크 응답의 바이트 단위 원본이 아닌 **보안상 허용한 공식 시장 필드의 JSON 보존본**입니다. 선택한 상위 20개 외에 같은 응답 페이지의 다른 순위도 여기에 남을 수 있습니다. 토큰 발급 응답·요청 헤더·authorization·서버 메시지 원문·미확인 필드는 저장하지 않습니다. 인증정보가 들어갈 수 있는 임의 필드를 보존하지 않기 위해 허용 목록을 사용합니다. 새 필드가 공식 추가되면 명세 확인 후 보존 목록을 확장해야 합니다.

거래대금 등 미제공 값은 생성하지 않습니다. 등락률은 실제 제공된 `base_comp_chgr`·`prev_base_chgr`만 원래 이름과 문자열로 저장합니다. 이를 전일 대비 등락률로 재해석하지 않습니다.

## 분석 예시

SQLite를 사용할 수 있는 도구에서 아래 SQL로 종목별 관측 이력을 조회할 수 있습니다.

```sql
SELECT c.collected_at, c.environment, r.rank, r.stk_cd, r.stk_nm, r.raw_json
FROM ranking_records AS r
JOIN collections AS c ON c.id = r.collection_id
WHERE r.stk_cd = '039490' AND c.test_mode = 0
ORDER BY c.collected_at;
```

백업은 수집기를 종료한 뒤 `data` 폴더 전체를 보존하세요. 실행 중에는 WAL 파일도 관련되므로 `.db` 파일만 임의 복사하지 마세요. 기본 보관 기간/자동 삭제 정책은 없으며 파일은 계속 증가합니다. DB/로그는 Git 제외 대상입니다.


`status`의 전체 횟수·기록 수와 `latest`는 테스트 회차도 포함합니다. 콘솔에서 테스트 횟수와 최신 회차의 `test_mode`를 확인할 수 있습니다. 동일 시각 중복 키에는 테스트 여부를 추가하지 않았으므로 운영/테스트라도 같은 초·환경·집계 구분이면 중복 차단됩니다. 테스트 오류·빈 결과도 테스트 플래그를 유지합니다. 인증 오류 응답/인증정보 원문은 저장하지 않습니다.


진단 열 추가는 기존 행을 삭제하거나 재작성하지 않는 `ALTER TABLE ... ADD COLUMN` 방식입니다. 진단의 필드 경로는 정적 공식 필드명과 행 인덱스로만 구성하며 실제 종목 데이터/요청 본문/헤더는 출력하지 않습니다. 상세 검증 실패에도 `response_json`에 실패한 시장 데이터 원문을 임의 저장하지 않습니다. 실패 이유는 `error`, 안전한 구조 요약은 `diagnostic_json`으로 확인하세요.


## 종목 마스터 및 섹터 분류 스키마

- `stocks`: `stock_code TEXT PRIMARY KEY`(ASCII 숫자 6자리 CHECK), `stock_name TEXT`, `updated_at`(KST). 코드를 정수로 변환하지 않습니다.
- `sectors`: `sector_id INTEGER PRIMARY KEY`, `sector_name TEXT UNIQUE`, `created_at`(KST).
- `stock_sectors`: `stock_code`→`stocks`, `sector_id`→`sectors` 외래 키, 복합 기본 키로 다대다 중복 연결 방지. `source`, `created_at`, `updated_at` 기록.
- `stock_sector_events`: 연결/출처 수정은 `action='tag'`, 해제는 `action='untag'`로 감사 이력을 추가합니다. `stock_code`, `sector_id`, `source`, `changed_at` 보존. 같은 출처의 동일 연결 요청이나 이미 없는 연결 해제는 감사 이력을 불필요하게 늘리지 않습니다.
- `schema_migrations`: `name` 기본 키와 `applied_at`. `stock_master_v1`의 기존 성공 관측 기반 초기화는 한 번만 실행합니다.

새 스키마·초기 종목 채우기·마이그레이션 마커는 하나의 트랜잭션입니다. 오류 시 롤백하고 다음 실행에서 재시도합니다. 이력 테이블은 재작성·삭제하지 않고 `ranking_records`에도 새 외래 키를 강제 추가하지 않습니다. 기존 데이터 중 코드/이름이 없거나 지원 형식 밖이면 원본 관측을 유지하고 마스터 등록만 건너뜁니다. 초기 이름은 `collections.collected_at`, 동률일 때 회차 rowid 순으로 마지막 유효 관측의 이름을 사용합니다. 테스트 모드 성공 관측도 실제 반환된 종목이면 포함됩니다. 자동 태그 추론이나 외부 사이트 조회는 없습니다.

새 수집 결과의 종목 마스터 갱신은 순위 기록·회차 완료와 같은 트랜잭션입니다. 저장 실패 시 마스터 변경도 함께 롤백합니다. 종목명이 바뀌어도 과거 `ranking_records.stk_nm`·`raw_json`은 그대로 유지합니다. `stocks.updated_at`은 마스터 최초 등록/이름 변경 처리 시각이며, 개별 관측 시각은 `collections`에서 조회하세요.

현재 태그의 출처가 변경되면 `stock_sectors.created_at`은 유지하고 `updated_at`과 `source`만 갱신합니다. 이전 출처는 감사 이력에 남습니다. `untag`는 연결만 제거하고 종목·섹터·관측·감사 이력은 삭제하지 않습니다. 외래 키 기본 RESTRICT 동작으로 연결 또는 감사 기록에 참조된 종목·섹터의 임의 삭제를 막습니다.

CSV는 UTF-8/BOM, 정확한 두 열 `stock_code,sector_name`(열 순서 변경 허용)을 받습니다. 앞뒤 공백을 제거한 후 코드를 6자리 ASCII 숫자 텍스트로 검증하고 마스터 존재 여부를 확인합니다. 같은 파일의 중복 종목-섹터 쌍은 오류입니다. 다른 섹터를 같은 종목에 여러 개 지정할 수 있습니다. 전체 검증과 적용은 쓰기 예약 트랜잭션에서 실행하므로 검증 후 다른 프로세스가 바꾸는 경합을 줄입니다. `--dry-run`은 태그를 저장하지 않지만 시작 시 공통 마이그레이션은 수행합니다. 원본 파일이나 인증정보는 DB에 보관하지 않습니다.

분석에서 종목별 현재 태그를 연결하려면 `ranking_records.stk_cd = stocks.stock_code`, 이어서 `stock_sectors`와 `sectors`를 조인하면 됩니다. 현재 태그와 과거 시점의 태그는 다르므로 과거 분류를 재현할 때는 감사 이력을 사용해야 합니다. 섹터 점수/매매 신호는 이번 변경에 없습니다.

## SQLite 리소스 정리와 Windows 테스트

Python 3.11의 `with sqlite3.connect(...) as db:`는 트랜잭션을 커밋/롤백하지만 연결을 닫지는 않습니다. 테스트용 직접 연결은 `with closing(sqlite3.connect(...)) as db, db:`로 감싸 트랜잭션 종료 후 반드시 `close()`가 실행되도록 합니다. `unittest.addCleanup`은 연결 해제 이후 임시 디렉터리를 삭제하는 LIFO 순서를 유지합니다.

마이그레이션의 과거 관측 순회 커서는 성공과 예외 경로 모두 명시적으로 닫습니다. `Store` 초기화 실패 시 연결을 닫는 기존 처리와 트랜잭션 롤백은 유지합니다. 파일 삭제 오류 무시, 삭제 재시도, GC 강제 실행으로 문제를 우회하지 않습니다. 리소스 회귀 테스트는 연결·커서 참조를 일부러 유지한 상태에서 닫힘을 확인하고 DB 파일을 실제로 이름 변경·삭제합니다. 이 변경은 스키마나 기존 이력·태그 데이터를 변경하지 않습니다.


## 읽기 전용 섹터 summary

`sector summary`는 쓰기용 `Store`를 생성하지 않고 SQLite URI `mode=ro`와 연결별 `PRAGMA query_only=ON`을 사용합니다. WAL을 반영하기 위해 `immutable=1`은 사용하지 않습니다. 회차 선택·관측·태그 조회는 하나의 읽기 트랜잭션 안에서 수행해 동시 수집/태그 변경으로 서로 다른 시점의 데이터가 섞이지 않게 합니다. 커서·트랜잭션·연결은 예외 시에도 정리합니다. DB/필수 테이블이 없으면 자동 초기화/마이그레이션 없이 오류로 종료합니다. 스키마 변경은 없습니다.

최신 성공 ka00198 회차의 `ranking_records`를 `stock_sectors` 및 `sectors`에 연결합니다. `stocks.stock_name`을 조회 당시 이름으로 대신 쓰지 않습니다. 같은 종목은 한 섹터 내 한 번, 각기 다른 섹터에는 각각 반영합니다. 전체 고유 수는 섹터 조인 이전의 종목코드로 계산하며 빈 코드는 별도 미식별 관측으로 보고합니다. 유효 코드가 중복된 레거시 관측이 있는 경우 코드별 최상위 순위 기록만 사용합니다. 관측명·코드·순위·태그는 변경하지 않습니다.

`collection.record_count`는 저장 당시 집계값, `observed_record_count`는 이번 조회에서 실제로 읽은 순위 1~20 관측 수입니다. `top20_ratio_percent` 분모는 고정 20이며 미수집 종목을 생성하지 않습니다. 분석의 `cluster_candidate`는 고유 종목 수가 2 이상인 단순 조건으로 투자 판단을 의미하지 않습니다.
