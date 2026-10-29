# Step 7-C8 — 병원 PC 실데이터 검증 준비

`kiwoom_collector.am_data_diagnostic`는 C7 입력 어댑터와 기존 SQLite 읽기 전용 접근기를
사용하는 로컬 진단 CLI다. 집 PC 검증은 합성 fixture에서 만든 격리 DB만 사용한다.
병원 원본 검증은 병원 PC에서 별도 실행한다. 운영 DB·수집기·스케줄러·Telegram 코드는
수정하지 않았으며 전송·OpenAI·매매 호출·배포 기능이 없다.

## 병원 PC 한 번 실행

병원 PC에서는 기존 개발 저장소 `stock1-dev`에서 실행하고 `--ranking-db`에는 운영
`stock1`의 **실제 기존 DB 경로**를 지정한다. 집 PC에는 stock1-dev를 만들지 않는다.
아래 경로는 예시이므로 바꾸고, 날짜·환경도 원본과 일치시킨다. 진단은 원본을 만들지 않는다.

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.am_data_diagnostic --ranking-db "C:\실제운영경로\stock1\data\stock1.db" --date 2026-10-29 --cutoff 2026-10-29T09:50:00+09:00 --environment real --name hospital-am-20261029
```

이 호출만으로 0198 슬롯별 저장 TOP20 완전성, 시세 테이블의 당일 종목별 자료,
원문 시각, 단위 검증 상태, 현재 섹터 연결과 부족 근거를 확인한다.
0186·시총 경로를 생략한 것은 **미제공**이며 자료가 존재하지 않는다고 단정하지 않는다.
같은 DB 안에 0186 테이블이 있어도 자동 추측하지 않으며 `--turnover-db`로 같은 경로를
명시할 수 있다. 운영 폴더나 인증정보를 자동 탐색하지 않는다.

기존 0186 DB·시총 export·별도 검토 manifest의 실제 위치를 알고 있다면 한 호출에 추가한다.
시총과 manifest 형식은 [AM_INPUT_ADAPTER.md](AM_INPUT_ADAPTER.md)를 따른다.

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.am_data_diagnostic `
  --ranking-db "C:\실제운영경로\stock1\data\stock1.db" `
  --turnover-db "C:\실제보관경로\ka10032.db" `
  --capital-json "C:\실제보관경로\ka10001-collected-export.json" `
  --evidence "C:\실제검토경로\am-verification-manifest.json" `
  --date 2026-10-29 --cutoff 2026-10-29T09:50:00+09:00 `
  --environment real --name hospital-am-20261029-reviewed
```

`--test-mode`는 명시적으로 테스트 순위만 선택할 때 사용한다. `--synthetic`은 합성 검증
전용이며 병원 실수집 원본에는 사용하지 않는다. `--format json`은 stdout도 JSON으로
출력한다. 기본 stdout은 한국어 요약이다. `--send`나 네트워크 실행 옵션은 없다.

### 결과와 종료 코드

개발 저장소의 Git 제외 디렉터리 `reports/`에 다음 두 파일을 새 이름으로만 만든다.

- `hospital-am-20261029.diagnostic.json`: 필드별 시각·단위·연결과 missing_evidence.
- `hospital-am-20261029.summary.txt`: 같은 결과의 한국어 요약.

기존 이름은 덮어쓰지 않는다. 다시 실행할 때 새 `--name`을 사용한다.

| 종료 코드 | 의미 |
| --- | --- |
| 0 | 현재 연결된 선정 후보가 C7 엔진과 C8 진단 기준을 통과했다. 매매 신호나 시장 전체 순위 검증 완료를 뜻하지 않는다. |
| 2 | 진단 결과는 생성했으나 근거 부족·소스 미제공·조회 불가·자료 불일치 등으로 브리핑 검증을 보류한다. 정상적인 보류 결과다. |
| 1 | 잘못된 실행 범위·manifest 형식·출력 이름 충돌 등으로 결과를 생성하지 못했다. 원문 예외와 경로는 출력하지 않는다. |

## 무엇을 구분해 검증하는가

| 항목 | 진단 내용 |
| --- | --- |
| 0198 | 같은 거래일·환경·test_mode·ka00198/qry_tp=1에서 09:20·09:30·09:40·09:50의 성공 회차를 확인한다. 20행·순위 1~20·고유 종목 20개·record_count 일치와 중복 회차를 점검한다. 저장 완전성과 검증된 완전성은 별도다. |
| 순위 시각 | scheduled_at은 예약, collected_at은 시작이다. 어느 것도 실제 응답 수신 시각으로 복제하지 않는다. 원본 해시·레코드 지문에 묶인 외부 검토 근거가 관측/수신 시각과 분 단위 대응까지 확인해야 검증 완료다. |
| ka10095 | raw_json의 **종목별 dt/cntr_tm**을 읽는다. 이는 체결 시각이며 배치 종료 시각과 다른 개념이다. 누락되면 배치 시각으로 채우지 않는다. 원문·저장 date/tm·검토 근거가 다르면 보류한다. |
| 시세 요청/응답 | request_started_at과 local_request_finished_at을 표시한다. 실제 response_received_at은 독립 검토가 저장 종료 시각과 일치할 때만 표시한다. 종료 시각만 있다는 이유로 실제 수신 검증을 승인하지 않는다. |
| 시세 값과 단위 | cur_prc KRW, flu_rt percent, trde_qty shares, trde_prica million_KRW는 명세상 단위다. 실측 단위 검증은 별도다. 가격 부호, 정수 거래량, 오전 누적 범위와 원문/저장 값 불일치를 확인한다. 음수 가격을 임의 절댓값으로 바꾸지 않는다. |
| 0186 | 제공한 DB의 turnover_runs/pages/rows 구조와 당일 실제 저장 회차·상태·행 수·페이지·요청 시각을 표시한다. 빈 회차도 숨기지 않는다. 페이지 수신 시각은 그 페이지 레코드들의 검토 근거가 일치할 때만 표시한다. |
| ka10001 | 제공한 collected export의 존재·레코드 수와 실제 시총 연결 수를 구분한다. 종목·환경·거래일·시각·mac 단위·시총 기준 검증 없이는 시총 적격성을 승인하지 않는다. |
| 섹터 | 종목코드에 연결된 현재 태그의 수와, 기준 시점에 유효하다고 검토된 연결 수를 구분한다. 기준 이후 수정 태그나 검토 근거 없는 태그는 후보 선정 근거로 삼지 않는다. |
| AM 후보 | C7 변환 → 기존 AM 분석 → 후보별 가격·거래대금·시총·섹터 연결을 재사용한다. 현재 선정된 종목의 기준 분 시세 진단과 네 슬롯 검증이 모두 있어야 ready_for_verified_briefing=true다. |

시세를 받지 않은 TOP20 종목도 코드별 missing_evidence로 표시한다. 가능 판정은 현재
선정된 후보에 한정된다. 0186이 미제공이어도 ka10095의 거래대금이 독립 검증됐다면
기존 엔진의 후보 검토는 가능할 수 있지만, 0186 소스 확인은 계속 미완료로 남는다.
거래대금 절대 하한은 추가하지 않는다. 오후 종베·D0 기준도 호출하지 않는다.

09:50:00 cutoff에는 09:50:12에 수신한 값이 들어갈 수 없다. 이후 시각을 진단하려면
실제로 의도한 cutoff를 명시해야 하며, 도구가 시각을 자동으로 늦추지 않는다.
현재 범위는 KRX뿐이다. NXT/SOR 코드나 다른 시장·환경을 임의 결합하지 않는다.

## 개인정보와 원본 보존

출력은 허용한 코드·검증된 형식의 시각·숫자·정적 사유만 사용한다. 원본 이름·회차 ID·파일 경로·
raw_json 전체·인증 필드·서버 메시지·원문 예외는 포함하지 않는다. 추적에는 SHA-256과
레코드 지문을 쓴다. JSON에는 구체적인 정적 reason 코드를, 요약에는 한국어 설명을 남긴다.
검증 manifest는 외부 검토를 기록하는 신뢰 경계이며 도구가 HTS 대조를 자동 수행하는 것은 아니다.

원본 접근은 `mode=ro&immutable=1`, query_only와 SQLite authorizer를 사용한다.
Windows에서는 읽기 핸들로 쓰기/삭제 공유를 거부한다. WAL/SHM/journal, 사용 중인 파일,
조회 사이의 해시 변경은 보류한다. 도구는 수집기를 중지하거나 스케줄러를 바꾸거나
checkpoint·마이그레이션·sidecar 삭제·운영 DB 복사를 실행하지 않는다.
안정된 원본에 접근할 수 없는 경우 해당 사유를 받아 별도로 준비한 안정된 자료로 재실행한다.

## 집 PC 검증

테스트는 기존 합성 fixture → 임시 SQLite와 시총 export → 합성 검토 manifest를 만들며
ka10095 종목별 원문 시각과 거래량을 추가한다. 운영 DB를 읽지 않는다.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_am_data_diagnostic.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

정상 4슬롯, 시각 미검증, 종목별 시각 누락/충돌, 원문 값 불일치, 거래량·단위 누락,
이전 거래일, 환경 불일치, 중복/불완전 TOP20, 0186 빈 회차, 시총 미제공,
섹터 시점 불일치, WAL·없는 소스, 읽는 동안 변경, 개인정보 제외와 출력 보존을 검증한다.

2026-10-29 집 PC 합성 검증: C8 테스트 20개, 전체 회귀 959개 PASS.
첫 전체 실행의 기존 모바일 iframe 테스트 1회 실패는 별도 13개 테스트와 전체 재실행에서 통과했다.
병원 실데이터 검증 완료를 뜻하지 않는다.
