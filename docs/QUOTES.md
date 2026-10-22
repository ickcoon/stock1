# ka10007 독립 시세 테스트 — 1단계

후속 단계의 ka10095 실험·정규화·시세 스키마 확장은 [복수 시세 검증 설명](BATCH_QUOTES.md)에 정리되어 있습니다. 아래는 기존 ka10007 동작 설명입니다.

이 기능은 기존 성공한 [0198] 회차의 상위 1~2종목을 **명령 실행 시점에** 조회합니다. 과거 순위 시점의 시세를 복원하지 않습니다. `collect`, `collect --schedule`, `collect --test-now`, `once`, 조건검색에는 자동 연결하지 않습니다. 주문 기능은 없습니다. ka10095는 별도의 quote-batch-test에서만 사용합니다.

## 실행 전 확인

1. Python 3.11 이상 및 requirements.txt의 패키지가 설치되어 있어야 합니다. `.venv` 실행 파일이 없거나 이전 PC 경로를 가리키면 먼저 실행 환경을 복구하세요.
2. 기존 `.env`의 `KIWOOM_ENV=mock`, 모의 전용 키, 권한·등록 IP를 로컬에서 확인하세요. 프로세스 환경변수가 `.env`보다 우선합니다. 이 명령은 환경을 자동 변경하지 않습니다.
3. 같은 mock 환경의 `collections.status='success'`, `api_id='ka00198'`인 회차 UUID를 선택하세요. 상위 1~2행에 유효한 종목코드가 있어야 합니다. `--collection-id`는 필수이며 최신 회차를 자동 선택하지 않습니다.
4. 최초 실행은 대상 DB에 시세 테이블과 트리거를 추가합니다. 기존 백업 절차로 백업을 확보하고 실행하세요. 사용자가 Windows 병원 PC에서 ka10007 실제 모의 조회·저장 성공을 확인했습니다.

```powershell
# 실제 API 호출 발생: 아래는 사용자가 검토한 후 직접 실행하는 예시
.\.venv\Scripts\python.exe -m kiwoom_collector quote-test --collection-id <mock-회차-UUID> --environment mock --limit 1 --db data/stock1.db

# 같은 회차의 두 번째 종목까지 확대: 이미 시도한 첫 종목은 호출하지 않음
.\.venv\Scripts\python.exe -m kiwoom_collector quote-test --collection-id <mock-회차-UUID> --environment mock --limit 2 --db data/stock1.db
```

`--limit` 기본값은 1, 허용값은 1과 2입니다. API 환경 기본값은 mock입니다. 실전 테스트는 `KIWOOM_ENV=real`, 실전 키, 실전 순위 회차, 명시적인 `--environment real`이 모두 일치해야 합니다. `--environment`는 서버를 임의 전환하지 않습니다. 모의 미지원 오류에도 실전으로 전환하지 않습니다.

부모 순위 회차가 일반 수집(`test_mode=0`)이어도 **새 시세 관측은 항상 `test_mode=1`**입니다. 부모의 플래그·상태는 바꾸지 않습니다. 분석 시 시세 자체의 테스트 여부를 반드시 필터링하세요.

## 호출과 결과

- 인증이 필요하면 인증 요청 최대 1회, 신규 대상 종목당 시세 요청 최대 1회입니다. 시세 재시도·연속조회·401 재발급 재조회는 하지 않습니다. 새 클라이언트는 첫 인증을 수행하므로 1종목 테스트는 통상 총 2 HTTP 요청입니다.
- 모든 요청 시작 간 최소 1초 간격을 사용합니다. 이 제한기는 클라이언트 인스턴스 내부 정책이므로 같은 키의 다른 프로세스와 합산 제한을 조정하지 못합니다.
- 결과 JSON은 `batch_id`, 환경, 테스트 플래그, 상태, 신규 시도·생략 수와 종목별 상태·정적 진단을 표시합니다. 토큰·키·서버 메시지는 표시하거나 저장하지 않습니다.
- 종료 코드: 전부 성공 또는 전부 중복 생략은 0, 누락·부분 성공·API/DB 오류는 1, Ctrl+C는 130입니다. 중복 생략이 이전 관측의 성공을 의미하지는 않습니다.
- 같은 `(collection_id, rank, api_id, test_mode)`는 실패·중단·running까지 재조회하지 않습니다. 동시 실행도 HTTP 전에 DB에서 원자적으로 선점합니다. 배치 이력은 실행마다 별도로 남습니다. 강제 종료 후 running을 자동 성공 처리하거나 삭제하지 않습니다. 강제 재시도 옵션은 이번 단계에 없습니다.

## 원본과 시각

공식 확인 필드만 보존합니다: `stk_cd`, `stk_nm`, `smbol`, `cur_prc`, `flu_rt`, `trde_qty`, `trde_prica`, `open_pric`, `high_pric`, `low_pric`, `date`, `tm`. 이 중 요구 시세 9개는 같은 이름의 TEXT 열과 `raw_json`에 저장합니다. 부호·선행 0·소수·빈 문자열을 변환하지 않습니다. 거래대금은 백만원이며 원화로 자동 환산하지 않습니다.

- API `date`/`tm`: YYYYMMDD/HHmmss 문자열 원본. 조회 시각으로 덮어쓰지 않습니다. 오래된 날짜도 원본을 보존하므로 순위 및 로컬 요청 시각과 비교해야 합니다.
- `request_started_at`/`request_finished_at`: 실제 시세 HTTP 요청 직전/응답 또는 전송 오류 직후의 KST ISO 시각(마이크로초). 인증·속도 제한 대기는 제외합니다. JSON 해석·저장 완료 시각도 아닙니다. 인증 실패로 시세 요청이 없으면 둘 다 NULL입니다.
- `quote_batches.started_at`/`finished_at`: DB 선점부터 전체 처리 종료까지의 시각입니다.

미제공·공백 필드, 잘못된 자료형·날짜시간 형식을 진단합니다. 숫자 문자열의 의미·가격 부호는 이번 단계에서 정규화하지 않습니다. 일부 시세가 유효한 문자열로 있으면 partial, 시세가 전혀 없으면 error입니다. 정상 문자열 `"0"`을 누락으로 처리하지 않습니다. 잘못된 자료형은 강제 문자열 변환 없이 보존 대상에서 제외합니다. 종목코드가 반환되었으나 요청과 다르면 error로 처리하고 시장 필드를 저장하지 않습니다. API 오류·연속조회 요구 응답에서도 시세를 정상 관측으로 저장하지 않습니다.

## 마이그레이션과 무결성

시세 명령만 `mode=rw`로 기존 DB를 엽니다. DB가 없으면 만들지 않습니다. 기존 `Store` 초기화·공통 마이그레이션·종목 마스터 갱신을 호출하지 않습니다. 성공 회차·환경·선택 코드를 검증한 뒤 `BEGIN IMMEDIATE` 내에서 스키마 추가와 선점을 원자적으로 수행합니다. 실패하면 새 스키마까지 롤백합니다. 재실행 시 CREATE IF NOT EXISTS로 기존 시세 데이터를 유지합니다.

- `quote_batches`: 회차 FK, 환경, test_mode=1, api_id=ka10007, 공식 명세 커밋, 배치 시각·상태.
- `quote_observations`: 기존 `(collection_id,rank)` FK 및 `(batch_id,collection_id)` FK, 요청 코드, HTTP/API 코드, 시세 요청 시각, 상태·진단·허용 시장 필드 JSON, 요구 시세 열.
- 삽입 트리거로 배치 환경과 성공한 순위 부모의 환경을 맞춥니다. HTTP 대기 중 쓰기 트랜잭션을 열어두지 않습니다.
- 시세 저장 오류·실패·중단은 `collections`/`ranking_records`에 UPDATE/DELETE를 하지 않습니다. 테마·종목 마스터·분류 감사 이력에도 쓰지 않습니다.

## 오프라인 검증

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p test_quotes.py -v
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

테스트는 가상 HTTP와 임시 DB를 사용합니다. 사용자가 Windows에서 ka10007 모의 조회 성공을 보고했으며 로컬 저장 원본도 확인했습니다. ka10095 실응답과 장중 갱신 지연 검증, 정기 수집 연결은 별도입니다.
