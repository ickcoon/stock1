# 실전 시세 필드 소규모 검증기

`quote-fields-test`는 사용자가 지정한 동일한 두 종목을 ka10095와 ka10007로 비교하는
실전(real) 전용 진단입니다. 순위 회차를 요구하거나 생성하지 않으며 기존 Store,
순위/시세 테이블, 정기 수집, Sector Radar에 연결하지 않습니다. 이번 구현 검증은
가상 HTTP와 임시 DB로만 수행합니다. 실제 OAuth·API 실행은 별도 승인 후 진행합니다.

## 입력과 실행 전 조건

- `--environment real`, `--db`, `--codes CODE1 CODE2` 필수.
- 종목코드는 기존 CODE 규칙의 서로 다른 두 코드이며 `|` 연결 길이는 20 이하입니다.
  같은 코드를 두 API에 그대로 전달하며 KRX/NXT/SOR 접미사를 제거하거나 추측하지 않습니다.
- 실제 설정의 environment=real과 base_url=https://api.kiwoom.com이 모두 일치해야 합니다.
  명령 옵션은 인증 환경이나 키를 자동 전환하지 않습니다.
- DB는 기존 부모 폴더 아래의 새 .db/.sqlite/.sqlite3 경로만 허용합니다. 기존 파일,
  심볼릭 링크, WAL/SHM/journal/환경별 API 잠금 파일, 기본 운영 DB 경로는 거부합니다.
  `xb`로 먼저 독점 예약하므로 빈 기존 DB도 사용할 수 없습니다. 실패 DB도 지우거나 재사용하지 않습니다.
- `--request-spacing` 기본 1.2초, 최소 1.1초. 인증·시세 모두 기존 고해상도 간격 제어를 재사용합니다.
- `--time-budget` 기본 120초, 허용 60~300초. 전송 직전 최소 30초 여유가 필요합니다.
- `--phase intraday|after-close|unspecified`는 사용자가 지정하는 관측 라벨입니다.
  기본 unspecified이며 시장 개장·휴장·세션을 자동 판정하거나 실행 예약하지 않습니다.

## 승인 후 사용할 명령

다음 예시는 **실전 전용 키·환경·검증된 CA 번들을 갖춘 별도 테스트 프로세스**에서만
실행합니다. 현재 설정이 mock이면 중단해야 합니다. 실전 키의 조회 권한·등록 IP,
같은 계정의 다른 수집 프로그램 종료 여부를 먼저 확인하세요. 비밀값을 명령행,
보고서, 채팅에 입력하거나 출력하지 않습니다. .env를 수정하지 않고 해당 테스트
프로세스에만 KIWOOM_ENV/실전 키/REQUESTS_CA_BUNDLE을 안전하게 전달합니다.

```powershell
$python = 'C:\Users\Public\Documents\ESTsoft\CreatorTemp\stock1-python-3.11.9\python.exe'
$testDir = Join-Path $env:TEMP ('stock1-real-fields-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testDir | Out-Null
$testDb = Join-Path $testDir 'fields.db'

# 이 명령은 실제 실전 시세 API를 호출합니다. 별도 실행 승인 후에만 사용합니다.
& $python -B -m kiwoom_collector quote-fields-test `
  --environment real --codes 005930 000660 --db $testDb `
  --request-spacing 1.2 --time-budget 120 --phase intraday
$LASTEXITCODE
```

Norton HTTPS 검사 환경에서는 기존에 출처와 TLS 체인을 대조한 로컬 CA 번들의
존재·해시를 다시 확인하고 테스트 프로세스에만 REQUESTS_CA_BUNDLE로 적용합니다.
실전 호스트의 인증정보 없는 TLS 검증 성공은 별도 실행 전 점검 사항입니다.
인증서를 설치/생성하거나 certifi 원본, TLS 검증 설정을 이 CLI가 바꾸지 않습니다.
검증 실패 시 중단하며 인증서 검증을 끄지 않습니다. 로컬 CA 번들·DB·로그는 Git 밖에 둡니다.

## 호출량과 실패 처리

정상 순서는 OAuth 1회 → ka10095 두 종목 1회 → ka10007 첫 종목 1회 → 두 번째 종목 1회입니다.
총 최대 4회이며 같은 세션과 토큰을 사용합니다. 순위·주문·다른 API·모의 서버 경로는 허용하지 않습니다.
기존 authenticate와 _post를 재사용하되 attempts=1로 고정하고 토큰 재발급도 차단합니다.
HTTP 401/403/429/5xx, API 오류, 인증/응답/종목 연결 실패, 연속조회 요구, DB 실패에서
후속 전송을 중단합니다. 누락 필드만 있는 정상 응답은 partial로 보존하고 다른 API의 근거를 수집합니다.

요청은 전송 전에 DB에 running으로 선점하고 전송 직전 시각을 커밋합니다. HTTP 중 DB 쓰기
트랜잭션을 유지하지 않습니다. 시간 부족으로 전송하지 못한 선점 행은 not_sent이며, 아직
선점하지 않은 후속 요청/관측을 만들지 않습니다. timeout/연결 오류 등 전송 불명은 uncertain,
사용자 중단도 전송 이후라면 uncertain입니다. 강제 종료는 running을 남길 수 있습니다.
기존 상태를 임의로 성공/실패로 바꾸거나 잠금을 지우거나 자동 재실행하지 않습니다.
이미 커밋된 정상 관측은 보존합니다. 저장 검증이 불가능하면 counts_verified=false와 집계 null을 출력합니다.

전체 예산은 초기화와 요청 간격 대기를 포함합니다. 응답 후 예산 초과도 중단 상태로 표시하며
받아 저장한 관측은 보존합니다. 기존 connect/read timeout(5/20초)을 유지하므로 서버가 계속
조금씩 보내는 경우까지 포함한 절대 실행시간 상한은 아닙니다. 다른 DB/프로그램의 같은 계정 호출은
기존 DB+환경별 OS 잠금으로 조정할 수 없으므로 동시 실행하지 않아야 합니다.

## 필드와 비교의 의미

근거는 기존 SPEC_VERSION에 해당하는 [키움 공식 명세](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/953e5dbff123f437ab4d11a78a95191a685eb51f/kiwoom/_data/kiwoom_api_spec.json)입니다.

| 비교 항목 | ka10007 | ka10095 | 표시/정책 |
|---|---|---|---|
| 현재가 | cur_prc | cur_prc | 부호·선행 0 원본과 진단용 절댓값 병기, 단위 원 |
| 기준 가격 | pred_close_pric | base_pric | 전일종가와 기준가를 같은 값으로 가정하지 않음 |
| 전일대비 | 없음 | pred_pre | 원, 진단용 가격 차이와 대조 |
| 방향 기호 | smbol | pred_pre_sig | 1/2 상승, 3 보합, 4/5 하락이라는 명세와 등락률 대조 |
| 등락률 | flu_rt | flu_rt | 부호 있는 Decimal 문자열, % |
| 거래량 | trde_qty | trde_qty | 비음수 정수, 명세상 1주 |
| 거래대금 | trde_prica | trde_prica | 명세상 백만원→원 진단용 환산, float 미사용 |
| 일자/시간 | date/tm | dt/cntr_tm/bid_tm | 요청 시각·API 시간·체결 시간·호가 시간 구분 |

현재가 절댓값은 `current_price_abs_for_comparison`이라는 **진단용 파생값**이며 원본을
덮어쓰지 않습니다. 가격 부호의 경제적 의미는 계속 price_status=unverified_sign_semantics입니다.
기준 가격이 양수일 때만 가격 크기-기준 가격 및 그 비율을 계산합니다. 등락률과 비교하는
0.01%p 허용차는 진단용 기준으로, 공식 반올림 규칙을 검증했다는 뜻이 아닙니다.
현재가 접두 부호, 등락률 방향, 방향 기호, 기준 가격과의 관계가 다르면 각각 different로 표시합니다.
보합의 가격 접두 부호는 임의 해석하지 않습니다. 기준 가격 0/누락은 계산 불가로 남깁니다.

단위는 unit_status=spec_declared_not_independently_verified입니다. 원화 환산값이 계산됐거나
두 API 값이 같아도 독립적인 단위 검증 완료로 승격하지 않습니다. 같은 시장·일자·관측 시각의
HTS 표시와 별도 대조해야 합니다. 누적 거래대금을 현재가×거래량과 같다고 가정하지 않습니다.

두 API는 순차 조회이므로 값 차이를 different_observations로 표시하며 곧바로 오류나 단위 불일치로
판정하지 않습니다. 요청 시각과 API 제공 시각을 함께 확인합니다. 장중과 정규장 종료 후 시험은
별도 승인·새 DB·phase 라벨로 구분하며 시간외 거래를 정규장 종가와 섞지 않습니다.

숫자/날짜 필드는 허용 목록과 형식으로 제한합니다. 정상 원본 문자열의 부호·선행 0·공백은
보존하고 누락/빈 문자열/정상 0/잘못된 형식을 구분합니다. 임의 텍스트·객체·민감값은 원문 대신
정적 issue 코드만 남깁니다. 토큰·키·계정·헤더·서버 메시지·인증 응답 본문·전체 시세 응답은 저장하지 않습니다.

## 저장과 출력

새 DB에 field_runs, field_requests, field_observations, field_comparisons 네 테이블만 만듭니다.
environment=real과 test_mode=1을 분리해 기록합니다. collection_id나 가짜 순위·섹터 태그를
만들지 않고 기존 순위/시세 스키마를 마이그레이션하지 않습니다. Sector Radar는 이 진단 DB를
자동 사용하지 않으며 기존 정규화 규칙과 분석 상태도 변경하지 않습니다.

stdout은 JSON 객체 하나, stderr는 간결한 콘솔 요약입니다. JSON에는 run_id, 환경, phase,
상태/중단 이유, 요청 횟수·시각·HTTP/API 코드, 코드별 원본/정규화·교차 비교, 미검증 상태가 있습니다.
request_counts는 실제 전송 시도 수(전송 불명 포함)이며 성공 응답 수와 구분합니다.
정상 시세 관측은 2종목×2 API=4건입니다. 미수집 관측은 생성하지 않습니다.
완료는 종료 코드 0, 부분 성공/오류/중단은 1, 사용자 중단은 130, 잘못된 CLI 인자는 2입니다.
완료는 전송·저장을 뜻하며 가격 부호·단위 의미가 확정됐다는 뜻이 아닙니다.

## 오프라인 검사

```powershell
python -B -m unittest discover -s tests -p test_quote_fields.py -v
python -B -m unittest discover -s tests -v
```

가상 HTTP, 임시 DB, 실제 CLI 인자 처리, 전송 전/후 저장 실패, 마감, 부분 응답,
인증·호출 제한·TLS 실패, 원본/숫자 정밀도, 비밀값 차단, 강제 종료를 검사합니다.
실제 실전 인증·응답·단위·호스트 TLS·다른 프로그램과의 계정 경합은 아직 검증하지 않았습니다.

2026-10-23 Windows / Python 3.11.9의 격리 소스 복사본에서 신규 35개 집중 테스트와
전체 326개(기존 291개+신규 35개)가 모두 통과했습니다. 전체 unittest 실행은 71.215초,
실패·오류·skipped는 0개입니다. 테스트 전후 원본 보호 파일 90개의 해시·크기·수정 시각이
동일했고, 외부 네트워크 및 원본 DB의 SQLite 연결은 차단했습니다. 실제 키움 API와 OAuth는
호출하지 않았습니다.
