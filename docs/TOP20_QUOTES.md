# 독립 TOP20 분할 시세 테스트

2026-10-22 사용자 보고: Windows 병원 PC에서 기존 2종목 성공을 유지한 채 나머지 18종목에 대해 실제 모의 요청 9회를 수행했고, 미전송 0건·종목별 성공·issues 없음으로 확인했습니다. 개발 환경의 오프라인 회귀 테스트와 이 사용자 실증 결과를 구분합니다.

`quote-top20-test`는 지정된 성공 ka00198 회차의 실제 1~20위 종목을 순위순으로 한 번 고정하고, ka10095 요청을 최대 2종목씩 최대 10개로 나눕니다. 20개 미만이면 실제 행만 사용하며 마지막 묶음은 1종목일 수 있습니다. 요청 필드 문자열 길이는 기존 20 이하 정책을 유지합니다. 이는 공식 최대 종목 수를 확정했다는 뜻이 아닙니다.

정기 collect/하루 10회 일정/조건검색과 연결하지 않았습니다. 기존 순위·종목 마스터·테마·환경설정은 변경하지 않습니다. 모든 새 시세는 test_mode=1입니다. ka10007과 기존 quote-batch-test도 유지합니다.

## 먼저 dry-run

```powershell
py -3.11 -m kiwoom_collector quote-top20-test `
  --collection-id "기존-mock-회차-UUID" --environment mock `
  --request-spacing 1.1 --recollect never --db data/stock1.db --dry-run
```

패키지가 .venv에 설치되어 있으면 `py -3.11` 대신 `.\.venv\Scripts\python.exe`를 사용하세요.

dry-run은 키·.env·HTTP 클라이언트·공통 Store를 로드하지 않고 마이그레이션하지 않습니다. SQLite의 mode=ro도 WAL/SHM을 만들 수 있어 원본을 SQLite로 직접 열지 않습니다. 원본 DB/WAL을 바이트 읽기로 임시 폴더에 복사하고 그 사본에만 SQLite를 연결합니다. 복사 중 DB/WAL 변경이 감지되면 실패하며 다시 실행해야 합니다. 원본 WAL의 커밋을 무시하는 immutable 방식은 쓰지 않습니다. 테스트에서 원본 DB/WAL/SHM의 바이트와 파일 목록 보존을 확인합니다.

출력 JSON:

- `targets`: 고정한 전체 TOP20, `selected`: 이번 실행 대상, `skipped`: 기존 상태로 제외한 대상.
- `requests`: 순위·코드·실제 stk_cd 입력 문자열을 가진 요청 묶음.
- `expected_quote_requests`: 시세 요청 예정 수. 인증 요청은 별도이며 첫 인증이 필요하면 통상 1회 추가됩니다. 긴 실행에서 토큰 만료가 발생하면 인증이 더 필요할 수 있습니다.
- `actual_api_calls=0`, `dry_run=true`.
- `blocked_by_batch_id`: 같은 DB·환경의 기존 TOP20 실행 잠금. 값이 있으면 실제 실행이 차단됩니다.
- 과거 `ranking_collected_at`/`ranking_scheduled_at` 및 `temporal_relation=current_quote_for_historical_ranking`.

로컬 기존 2종목 성공 회차 `c24d981d-63f1-49f7-bf9d-799ab46776fc`로 수행한 dry-run에서는 전체 20종목 중 삼성전자·SK하이닉스 2종목을 보존·생략하고, 남은 18종목을 9개 요청으로 계획했습니다. 실제 HTTP 호출은 0회입니다. 회차는 2026-10-20 15:00 KST의 순위이며, 이후 실제 실행하는 시세는 현재 관측입니다.

이 회차에는 `0035S0`, `0158S0`처럼 영문자가 포함된 원본 종목코드도 있습니다. 이전 프로그램의 숫자 6자리 제한을 ASCII 대문자·숫자 6자리(+기존 시장 접미사) 구문 검사로 완화해 **서버가 반환했던 코드를 그대로** 보냅니다. 이것은 요청 구문 허용이며 해당 종목의 ka10095 실서버 지원을 증명하지 않습니다. 종목 마스터·테마의 코드 제약은 변경하지 않았습니다.

## 실제 실행 — 별도 승인 후

다음 명령은 실제 API 호출과 지정 DB의 시세 스키마 확장·저장을 수행합니다. 실행 전 백업과 환경을 확인하고 승인받아야 합니다. 구현 검증에서는 이 경로를 가상 HTTP와 임시 DB로만 실행했습니다.

```powershell
py -3.11 -m kiwoom_collector quote-top20-test `
  --collection-id "기존-mock-회차-UUID" --environment mock `
  --request-spacing 1.1 --recollect never --db data/stock1.db
```

실제 환경은 KIWOOM_ENV 및 그 환경의 키와 일치해야 합니다. 실전은 --environment real까지 명시해야 하며 자동 전환하지 않습니다. 병렬 HTTP 요청·무조건 재시도는 없습니다. 요청 시작 간 최소 1.1초이며 --request-spacing으로 더 늘릴 수 있습니다. 유효하지 않은 간격, NaN, 무한대는 거부합니다. 동일 클라이언트의 토큰과 요청 시계를 재사용합니다. 같은 DB·환경의 TOP20 실행 및 구 시세 테스트 사이에는 잠금을 적용합니다. 다른 DB나 별도 프로그램의 동일 계좌 호출은 이 잠금·유량 제한에 포함되지 않습니다.

## 보존과 재수집

| --recollect | 동작 |
| --- | --- |
| never (기본) | 기존 성공·실패·중단·미완료 이력이 있는 대상 생략. 미시도만 조회 |
| failed | 성공 이력이 없는 실패·부분 성공·중단 대상과 미시도 조회 |
| all | 결과가 확정된 기존 성공까지 새 차수로 재조회 |

어떤 옵션도 과거 행을 덮어쓰지 않습니다. 결과 불명 running은 all이어도 자동 재전송하지 않고 실행을 차단합니다. 새 관측의 `attempt`를 증가시키고 과거 성공을 보존합니다. 기존 행은 attempt=0, request_id=NULL로 유지하며 원본·수집 시각을 소급 재작성하지 않습니다.

실행별 quote_batches에 `plan_json`을 저장합니다. quote_requests는 실제 소규모 요청별로 한 행이며, quote_observations의 nullable request_id 외래 키로 연결합니다. 시세 관측 기본 키는 `(collection_id,rank,api_id,test_mode,attempt)`로 확장합니다. 이 재구성은 트랜잭션 안에서 수행하고 기존 열·행·외래 키를 보존하며 오류 시 롤백합니다. 기존 단일/2종목 CLI는 계속 attempt=0만 선점하므로 재실행으로 새 시세를 만들지 않습니다.

각 요청 직전에 그 묶음만 선점합니다. 아직 전송하지 않은 후속 종목에는 관측 행을 만들지 않습니다. Ctrl+C는 진행 요청을 interrupted로 남기고 정상 완료된 종목을 보존하며 잠금을 해제합니다. 기본 재실행은 미전송 종목만 조회합니다. interrupted 묶음도 다시 조회하려면 failed를 명시합니다. 강제 프로세스 종료는 running·잠금이 남을 수 있으며 자동 성공 처리나 강제 잠금 해제 옵션은 없습니다. 이 경우 기존 실행이 종료되었는지와 DB 상태를 검토해야 합니다.

## 오류·시각

- HTTP 401/403/429, 인증·환경·호출 제한 코드, 모의 미지원: 해당 결과 저장 후 남은 요청 중단. 서버 메시지에 포함된 공식 숫자 코드 형식도 제한·인증 분류에 사용하되 메시지 원문은 저장하지 않습니다.
- 일반 전송/서버 오류: 해당 요청 실패를 기록하고 다음 묶음 진행. 자동 재시도 없음.
- 누락·중복·예상 밖 종목코드 및 필드 누락: 기존 ka10095 검증을 사용해 종목별 원본·정적 진단 기록. 정상 종목과 실패가 섞이면 partial.
- DB 저장 오류: 추가 HTTP 호출 중단. 기존 순위 성공 상태는 유지.
- 중복 생략의 종료 코드 0은 과거 실패가 성공했다는 뜻이 아닙니다. 전체 targets, skipped.previous_status, observations를 함께 확인하세요.

순위 과거 시각, 실행 시각, HTTP 요청 시작/종료, API dt/cntr_tm/bid_tm을 구분합니다. 기존 가격 부호 미검증·NULL 정규화 정책도 유지합니다. 순차 조회이므로 TOP20 전체 동시 시세라고 표시하지 않습니다.

## 오프라인 검증

```powershell
py -3.11 -m unittest discover -s tests -p test_top20_quotes.py -v
py -3.11 -m unittest discover -s tests -v
```

가상 HTTP·임시 DB로 dry-run 무변경, WAL 반영, 고정 목록, 2종목 분할·홀수 꼬리, 기존 성공 보존, 요청 FK, 1.1초 이상 간격, 제한·인증 중단, 누락·부분 실패, 재개·재수집, 환경 분리, 이전 스키마 마이그레이션과 롤백을 검증합니다.
