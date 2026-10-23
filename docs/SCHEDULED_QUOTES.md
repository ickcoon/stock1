# TOP20 순위·시세 선택적 자동 연동

기본 `collect --schedule`은 기존 하루 10회 순위만 수집합니다. 시간표는
09:20, 09:30, 09:40, 09:50, 10:00, 11:00, 12:00, 13:00, 14:00, 15:00 KST 그대로입니다.
`--with-top20-quotes`를 함께 지정해야 후속 ka10095 수집을 실행합니다.
이 옵션은 고정 일정에서만 허용되며 `once`, 간격 수집, `--test-now`에는 사용할 수 없습니다.

## 실행 예시 (이번 개발에서는 실행하지 않음)

이미 mock 자격 증명과 KIWOOM_ENV=mock이 설정된 환경:

```powershell
python -m kiwoom_collector collect --schedule --with-top20-quotes --db .\data\stock1-mock.db
```

이미 real 자격 증명과 KIWOOM_ENV=real이 설정된 환경:

```powershell
python -m kiwoom_collector collect --schedule --with-top20-quotes --db .\data\stock1-real.db
```

기존 순위 전용 명령:

```powershell
python -m kiwoom_collector collect --schedule --db .\data\stock1.db
```

위 명령은 장기 실행을 시작합니다. 실전 실행 전 복사 DB와 mock 환경에서 검증해야 합니다.
코드는 .env를 쓰거나 Windows 정기 작업을 생성하지 않습니다. 기존 휴장일 옵션을 그대로 사용할 수 있습니다.

## 흐름과 시간 제한

1. 기존 Store.begin → 순위 조회 → Store.finish가 성공한 신규 운영 회차만 전달합니다.
2. 방금 생성한 collection_id를 그대로 사용합니다. latest 검색, 과거 회차 재생, 자동 재시도는 없습니다.
3. 20개이면 2종목씩 10요청, 19개이면 마지막 1종목을 포함해 10요청입니다. 누락 종목을 생성하지 않습니다.
4. 같은 BatchQuoteClient가 순위와 시세를 조회해 토큰·세션·호출 간격 상태를 공유합니다.
5. 다음 요청을 DB에 claim한 뒤 호출하며 매 응답을 짧은 트랜잭션으로 저장합니다. HTTP 동안 DB 쓰기 잠금을 유지하지 않습니다.

QuotePolicy 설정 기본값: 신규 회차 최대 나이 180초, 후속 수집 창 120초,
다음 슬롯 여유 60초, 요청 시작 전 필요 예산 60초, 요청 시작 간격 기본 1.2초(최소 허용 1.1초)입니다.
매 요청 전 예산을 확인하고, 실제 클라이언트도 인증 전·전송 직전에 확인합니다.
순위 응답이 늦거나 다음 슬롯이 가까우면 시세를 생략하거나 중단합니다.
이 값은 운영 보호용 여유이며 정밀 동기화 기준이 아닙니다. 동기식 순차 실행이며 별도 작업 큐나 백그라운드 재시도가 없습니다.
requests의 기존 connect/read timeout(5/20초)을 유지합니다. read timeout은 응답 전체의 절대 제한이 아니므로
비정상적인 서버 지연까지 포함한 하드 실시간 보장은 아닙니다.

## 스키마 및 호환성

자동 연동의 첫 성공 회차에서만 시세 스키마 확장 함수를 실행합니다.
quote_batches/quote_observations의 test_mode 제약을 IN (0,1)로 확장하고 기본값 1은 유지합니다.
기존 모든 행과 명시적 인덱스/트리거, quote_requests와 잠금의 FK를 보존하며 트랜잭션/FK 검사로 검증합니다.
기존 collections/ranking_records 스키마와 값, collected_at의 의미는 변경하지 않습니다.

- 정기 운영 시세: test_mode=0, 부모도 test_mode=0이고 scheduled_at이 존재해야 합니다.
- 기존 명시적 테스트 시세: test_mode=1 그대로입니다. 기존 행을 재분류하거나 덮어쓰지 않습니다.
- mock/real은 위 구분과 독립적이며 부모 회차·클라이언트 환경이 일치해야 합니다.
- 신규 scheduled_quote_runs는 collection_id PK, batch_id, 상태, 사유, 수집 마감 시각을 저장합니다.
- 동일 운영 회차는 한 번만 claim합니다. stopped/partial/uncertain도 자동 재실행하지 않습니다.

quote-top20-test의 인자·명시적 재수집 정책은 유지하고 기본 간격만 1.2초로 늘렸습니다.
인증·순위·단일/복수 시세는 고해상도 시계와 재확인 대기를 사용하는 공통 전송 경로를 사용합니다.
관련 실측과 오프라인 보완 범위는 [INTEGRATED_TEST.md](INTEGRATED_TEST.md)를 참고하세요.
Sector Radar v1 코드와 선정 규칙도 유지합니다. 레이더는 기존처럼 동일 회차의 테스트/운영 관측 모두를 후보로 검토하므로
표시를 운영 관측만으로 한정하는 기능은 이번 범위에 포함되지 않습니다.

## 잠금·실패·복구

API 실행은 resolved DB 경로 + 환경별 OS 파일 잠금을 사용합니다.
CLI의 collect/once/auth/시세 테스트와 시세 실행 함수가 공유합니다. 정기 실행은 대기 시간에도 소유권을 유지하여 수동 시세와 충돌하지 않습니다.
잠금 파일은 DB 옆 *.api.lock이며 Git에서 제외합니다. 파일 존재 자체는 잠금 소유를 뜻하지 않고, 프로세스 종료 시 OS 잠금이 해제됩니다.
실행 중 파일을 삭제하지 마세요. 다른 DB 경로·다른 프로그램의 같은 계정 호출은 조정하지 않으므로 동시에 실행하지 않아야 합니다.

- 순위 실패/빈 응답: 후속 시세 없음. 순위 실패 처리 방식은 그대로입니다.
- 일부 종목 누락/실패: 원본·개별 상태·issues를 저장합니다. 과거 관측이나 0으로 대체하지 않습니다.
- 인증/호출 제한/모의 미지원: 나머지 요청을 중단합니다.
- 시간 부족: run=stopped, 미전송 대상은 요청을 만들지 않습니다. claim 뒤 전송 전 마감은 deadline_before_send로 표시합니다.
- timeout/전송 예외: request=uncertain, 관측=interrupted + transmission_unknown,
  run=uncertain으로 표시하고 quote_top20_locks를 보존합니다.
- Ctrl+C/예기치 않은 예외: 가능한 경우 같은 불명확 상태를 기록하고 잠금을 보존합니다.
- 강제 종료/전원 장애: running/잠금을 그대로 보존합니다. 이후 자동 복구·성공/실패 추정·재전송을 하지 않습니다.
- 잔존 잠금은 후속 시세와 수동 시세를 차단하지만 다음 순위 회차의 저장 상태를 바꾸지 않습니다.

복구는 실행 프로세스 종료 여부와 마지막 request/observation을 읽기 전용으로 조사한 뒤 별도 승인된 절차로 진행해야 합니다.
이번에는 잠금 강제 해제 명령을 추가하지 않았습니다. 재시작으로 과거 미수집 회차를 복구하지 않습니다.

## 검증

tests/test_scheduled_quotes.py는 가상 HTTP와 임시 SQLite만 사용합니다.
운영 DB에는 개발 중 마이그레이션하지 않습니다. 전체 회귀는 다음과 같습니다.

```powershell
python -m unittest discover -s tests -v
```

실제 API의 한도, 장중 부하, 실전 응답 필드 의미, 다른 프로그램과의 계정 단위 경합은 아직 검증되지 않았습니다.
