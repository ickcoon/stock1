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
