# Step 7-C5-B — 아르비스 Telegram 오전 브리핑

Step 7-C6의 섹터별 간결 문자와 합성 dry-run은 [AM_SECTOR_BRIEFING.md](AM_SECTOR_BRIEFING.md)를 따른다.
기존 문자 전송과 예약 중복 방지는 유지하며 OpenAI TTS·Telegram 음성 전송은 차단한다.

## 범위

`kiwoom_collector.telegram_briefing`은 독립 수동 알림 모듈이다. 기본 동작은 dry-run이며 `--send`가 있어야 Telegram 전송 경로에 진입한다. 실제 최초 전송은 사용자 승인 후에만 수행한다. 이번 구현·테스트에서는 실제 메시지를 전송하지 않는다. 운영 DB, 수집기, 스케줄러, D0 저장 기능을 읽거나 변경하지 않는다. 자동 스케줄·자동 재시도·운영 데이터 연결은 구현하지 않았다.

현재 입력은 C5-A `am-momentum-review-v1`의 **합성 fixture 보고서만** 지원한다. `environment=real`도 이 단계에서는 합성 real 범위이며 실측 결과가 아니다. 미확정 `candidate_confirmed=false`, `d0_storage_allowed=false`를 검사한다. 추후 실측 입력에는 별도 승인된 내보내기/검증 어댑터가 필요하며 source_kind를 임의로 fixture로 바꾸면 안 된다.

## 입력 및 메시지

입력 파일은 개발 폴더 `/reports/` 아래 JSON과 선택적 HTML로 제한한다. 경로 이탈·외부로 연결되는 심볼릭 링크·잘못된 확장자·중복 JSON 키·과대 파일·알 수 없는 보고서 필드는 거절한다. JSON 최대 2 MiB, HTML 최대 4 MiB. 스키마·보고서 해시·실제 순위 이력과 횟수·환경·단위·관측/수신 시각·미확정 상태를 검증한다. 해시는 무결성 검사이며 발행자 인증 서명이 아니다.

메시지는 거래일, KST 마감 시각, mock/real 및 합성 출처, 잠정 상태, 관심도 동반 섹터와 최대 3개 관심도 후보, TOP5 횟수, 관측 시각, 주요 근거와 미검증 가격/거래대금/시총/유동성을 표시한다. 후보는 TOP5 반복 횟수 → TOP20 등장 횟수 → 마지막 관측 순위 → 종목코드 순으로 정렬한다. AM 분류의 확정 매매 신호로 표현하지 않는다. 4개 오전 슬롯/TOP20 완전성이 없거나 유효 후보가 없으면 TOP3 대신 자료 부족 메시지를 만든다. 후보가 1~2개면 그 수만 표시한다. 섹터는 보고서의 동반 등장 근거가 있는 분류 범위에서만 요약하며 시세 주도력은 미확정으로 표시한다.

`sendMessage`는 parse_mode 없는 일반 텍스트이며 UTF-16 기준 4096자 이내로 검사한다. 알 수 없는 API 본문이나 raw 응답을 전송하지 않는다. HTML 첨부는 JSON을 C5-A renderer로 다시 렌더한 **정확한 문서와 일치**해야 한다. 같은 embedded JSON에 임의 HTML/스크립트를 덧붙여도 거절된다. 문서는 고정 파일명 `arvis-am-review.html`로 multipart 업로드하므로 로컬 경로/원래 파일명을 보내지 않는다. 휴대폰에서 미리보기되지 않으면 다운로드 후 브라우저로 열도록 안내한다.

공식 계약: [sendMessage](https://core.telegram.org/bots/api#sendmessage), [sendDocument](https://core.telegram.org/bots/api#senddocument). 공식 문서의 텍스트 4096자와 multipart 문서 전송 계약을 사용하며, 프로젝트의 HTML 크기 제한은 Telegram 허용량보다 작게 고정한다.

## 비밀 설정 — 실행 프로세스 환경변수만 사용

모듈은 `.env`를 자동 로드하지 않으며 Kiwoom 자격증명을 읽지 않는다. 운영 `.env`, 토큰, 인증서를 개발 폴더로 복사하지 않는다. 아래 코드는 PowerShell 세션에만 값을 설정하며 파일/Git에 저장하지 않는다. 토큰이나 Chat ID를 채팅·명령 인자·스크린샷·Git에 붙여 넣지 않는다.

```powershell
$tgTokenInput = Read-Host 'Telegram Bot Token' -AsSecureString
$env:ARVIS_TELEGRAM_BOT_TOKEN = [System.Net.NetworkCredential]::new('', $tgTokenInput).Password
$tgChatInput = Read-Host '개인 Chat ID' -AsSecureString
$env:ARVIS_TELEGRAM_CHAT_ID = [System.Net.NetworkCredential]::new('', $tgChatInput).Password
$env:ARVIS_TELEGRAM_ALLOWED_CHAT_IDS = $env:ARVIS_TELEGRAM_CHAT_ID
```

여러 허용 대상이 꼭 필요하면 ALLOWED_CHAT_IDS에 쉼표로 구분한 명시적 목록을 설정한다. 실제 CHAT_ID는 목록과 정확히 일치해야 한다. 개인 채팅의 양의 정수 ID만 허용하며 음수 그룹 ID, @채널명, 빈 목록은 거절한다. 봇은 사전에 사용자가 만든 개인 봇이며 사용자가 봇과 개인 대화를 시작하고 자신의 Chat ID를 확인해야 한다. 이 모듈은 Chat ID 탐색용 getUpdates/getChat 호출을 하지 않는다.

비밀 필드·토큰 패턴·인증/계좌 관련 텍스트·PC 경로를 검사하고, 설정된 실제 토큰/Chat ID가 메시지·보고서에 포함돼도 거절한다. 실패 출력은 고정된 사유 코드만 사용한다. credentials 객체의 repr에는 값이 없으며 API 응답 본문·요청 URL·헤더를 로그로 출력하지 않는다. HTTP 디버그 URL 로그도 전송 범위에서 비활성화한다. 자유 텍스트의 모든 비밀을 패턴만으로 검출할 수는 없으므로 최초 전송 전 dry-run과 문서 검토가 필요하다.

## 실행

새 합성 예시 생성(기존 파일은 덮어쓰지 않음):

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.am_momentum --fixture tests/fixtures/am_momentum_synthetic.json --name telegram-demo
```

기본 dry-run — 인증정보/네트워크/전송 이력 쓰기 불필요:

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.telegram_briefing --report reports/telegram-demo.json --html reports/telegram-demo.html
```

Windows 콘솔 인코딩과 무관하게 JSON 출력은 ASCII escape를 사용한다. 다음처럼 읽으면 메시지를 사람에게 읽기 쉬운 형태로 볼 수 있다.

```powershell
(.\.venv\Scripts\python.exe -B -m kiwoom_collector.telegram_briefing --report reports/telegram-demo.json --html reports/telegram-demo.html | ConvertFrom-Json).message
```

**사용자 승인 및 대상·내용 확인 후에만** 실제 전송:

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.telegram_briefing --report reports/telegram-demo.json --html reports/telegram-demo.html --send
```

`--html`을 생략하면 메시지만 전송한다. 첨부 생략 후 같은 보고서를 다시 실행해도 중복으로 차단되므로, 실제 실행 전 첨부 여부를 결정한다.

## 전송·중복·오류 정책

HTTPS 고정 호스트 `api.telegram.org`, TLS 검증 유지, redirects=False, timeout=(5,20), retries=0, trust_env=False를 사용한다. 토큰은 공식 Bot API URL 인증에만 사용하고 별도의 인증 헤더나 Kiwoom 키/계좌정보를 보내지 않는다. `ok=true`, message_id, 반환 개인 채팅 ID도 검사한다.

Git 제외 `/notification_state/`의 독점 예약 파일은 **거래일 + 선정 유형 + 보고서 스키마 버전 + 환경 + 시장 + 대상 ID 해시**로 구분한다. 같은 날짜 보고서를 재생성해 evaluation_reference가 바뀌어도 다시 보내지 않는다. 로컬 디렉터리 한 곳에서만 중복을 차단한다. 서로 다른 PC의 독립 이력은 공유되지 않으므로 최초 운영 발송 PC는 한 곳으로 정해야 한다. 원격 Telegram의 exactly-once 전송을 보장하는 기능은 아니다.

네트워크 호출 전에 예약을 디스크에 기록하고, 메시지 성공과 문서 성공을 구분해 기록한다. 기록에는 토큰·원문 Chat ID·PC 경로·API 원문 오류가 없다. HTTP 실패·API 오류·타임아웃·프로세스 중단·문서만 실패한 경우에도 예약을 유지한다. 다음 실행은 duplicate_blocked이며 자동 재시도/자동 재전송 옵션은 없다. 복구가 필요하면 실제 수신 여부를 사용자가 확인한 후 별도 수동 절차를 결정해야 한다. 운영 수집 성공/실패와는 완전히 별개다.

CLI는 dry_run/sent/duplicate_blocked에서 0, 검증 거절 또는 failed_or_unknown에서 1을 반환한다. 재시도 스케줄러에 연결하지 않는다.

## 테스트 및 다음 단계

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_telegram_briefing.py
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

기본 테스트는 합성 AM 보고서·임시 reports/state·가짜 HTTP 세션만 사용한다. 운영 DB/API/스케줄러 접근은 없다. JSON/HTML 일치, 기본 dry-run, 순위 요약, 시각/환경/미확정 표시, 민감정보·허용 대상 검사, 원문 오류 차단, 중복 및 동시 실행 예약, HTTP/API/타임아웃/부분 실패를 검증한다.

다음 단계는 사용자 승인 후 개인 봇에 합성 보고서 1회 전송하여 휴대폰 다운로드를 확인하는 것이다. 이후 별도 승인으로 실측 read-only 내보내기 어댑터와 실제 수신 완료 시각을 검증한다. 09:50:00 시점에는 09:50 회차 수신이 끝나지 않을 수 있으므로, 분석 마감과 알림 실행 시각을 구분하는 정책부터 정해야 한다. 그 뒤 독립 발송 호스트와 실패 복구 정책을 결정하며, 운영 수집기/스케줄러 연결은 이번 단계에 포함하지 않는다.

검증 기록 (2026-10-29 KST): 신규 Telegram 테스트 29개 통과. 최종 전체 회귀 810개, 149.674초, 실패 0·오류 0·스킵 0. 운영 경로와 외부 네트워크 접근을 차단한 테스트 프로세스에서 실행했다. 실제 Telegram 전송은 0회이며 HTTP 전송 테스트는 가짜 세션을 사용했다.
