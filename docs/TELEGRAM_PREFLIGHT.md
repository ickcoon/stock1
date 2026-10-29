# Telegram 합성 메시지 1회 전송 준비

2026-10-29 KST 확인: HEAD `a2ed1f3cad0c11282837dfa06259b468eae92e38`, 시작 시 working tree 깨끗함.
개발 대상은 stock1-dev뿐이다. 운영 폴더는 조회하거나 수정하지 않았다.

C5-C는 voice_briefing/voice_dialogue와 테스트로 구현되어 있다. C5-D의 명시적인 단계 표기는 찾지 못했다. VOICE_FOUNDATION 문서상 실제 수신 루프, STT/GPT 공급자, 후속 답변 음성 연결은 미구현 후속 작업이다. 기존 문서의 840개 테스트 통과 기록은 이전 기록이며 이번 세션에서 재실행한 결과가 아니다.

## 로컬 설정 검증

개발 폴더의 PowerShell 터미널에서 실행:

```powershell
.\scripts\prepare_telegram_test.ps1
```

스크립트는 Python 실행을 먼저 확인하고, 없는 경우 합성 fixture 보고서를 생성한다. Bot Token과 개인 Chat ID는 숨김 입력을 사용한다. 프로세스 환경변수만 일시 설정하며 개인 Chat ID 하나를 허용 목록으로 사용한다. 설정 형식과 메시지 검증만 수행하고 종료 시 기존 환경변수를 복원한다. 값은 파일/명령 인자/Git에 저장하지 않는다. 네트워크를 통한 토큰 유효성이나 실제 수신자 확인은 하지 않는다.

준비 메시지는 `reports/telegram-once-preflight.json`을 사용한다. HTML/음성 없이 문자 1건을 대상으로 하며 보고서의 날짜는 합성 fixture 날짜다. 스크립트에는 전송 옵션이 없다. 실제 전송은 별도 승인 후 수행해야 한다. 기존 전송기는 중복 예약을 사용하며 실패/결과 불명에도 자동 재시도하지 않는다.

## Python 복구 및 검증 결과

최초 실행은 참조 Python 3.11 부재로 실패했고 `py -0p`도 설치된 Python이 없다고 응답했다. 이후 공식 Python 3.11.9 설치 파일의 PSF 서명을 확인하고 복구했다. 설치 프로그램은 기존 3.11.7 등록 정보를 찾아 지정한 경로 대신 사용자 설치 경로를 업그레이드했다. 사용자 설치 본체는 `C:\Users\dada1\AppData\Local\Programs\Python\Python311`에 있다.

최종 개발 실행 경로는 `.venv\Scripts\python.exe`, base Python은 개발 폴더 안의 `.venv\python-repair\runtime\python.exe`다. 사용자 설치 본체를 복사하고 `venv --upgrade --without-pip`으로 참조를 갱신했다. 기존 패키지는 유지했다. 기존 가상환경은 `.venv-hospital-backup`에 보존했으며 원본 파일 1,786개와 백업 파일 수, pyvenv.cfg 해시 일치를 확인했다. 기존 가상환경/파일은 삭제하지 않았다.

2026-10-29 KST 검증:

- requirements.txt의 python-dotenv 1.2.1, requests 2.32.5, websocket-client 1.9.0 모두 일치. pip check 통과.
- 사용자 Python으로 복구한 개발 가상환경에서 전체 회귀 840개 통과, 144.563초.
- 개발 폴더 내부 runtime으로 참조를 전환한 뒤 Telegram 29개 및 음성 기반 30개 재검증 통과.
- 합성 `reports/telegram-once-preflight.json` 및 HTML 생성. 문자 단독 dry-run 성공, 최종 HTML 포함 dry-run도 성공.
- 테스트와 dry-run은 기존 `.venv/test-guard/sitecustomize.py`로 운영 경로/외부 네트워크 접근을 차단했다.
- 실제 자격증명 입력/유효성 확인은 미실행. 실제 Telegram 전송 및 OpenAI API 호출 0회.

설치 자료, runtime, 백업, 합성 보고서, 테스트 로그는 기존 Git 제외 경로에 있다. requirements.txt와 애플리케이션 코드는 변경하지 않았다. 미커밋 준비 스크립트는 보존했다.

## C5-D 최초 전송 거절 진단

후속 진단에서 `telegram_briefing.py`에 읽기 전용 `--check`를 추가했다. 기존 전송 검증/안전장치는 유지한다. 이 옵션은 설정, JSON, 선택적 HTML, 기존 중복 예약을 확인하고 네트워크 호출이나 예약 생성 없이 고정 상태/사유만 출력한다. `--send`와 동시에 사용할 수 없다.

사용자가 설정한 동일 PowerShell 터미널에서 실행:

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.telegram_briefing --report reports/telegram-once-preflight.json --check
```

- `configuration / configuration_required`: 토큰 부재 또는 형식 불일치. 기존 조건은 ASCII 숫자 6~15자리, 콜론, ASCII 영숫자/밑줄/하이픈 20~100자리다.
- `configuration / private_chat_allowlist_required`: 개인 Chat ID 또는 쉼표 구분 허용 목록 형식 문제. 각 ID는 0으로 시작하지 않는 양의 정수 1~20자리여야 한다. 음수 그룹 ID와 @이름은 지원하지 않는다.
- `configuration / recipient_not_allowed`: 정리된 Chat ID가 허용 목록의 항목과 정확히 일치하지 않는다.
- `json_report`: 보고서 경로/JSON/필수 필드/해시/개인정보 차단 검증 실패. 실제 비밀값과 보고서 내용이 겹치는 경우도 차단한다.
- `html_attachment`: 명시적으로 --html을 지정했을 때만 검사한다. 첨부는 필수가 아니다.
- `duplicate_blocked`: 이미 같은 대상/거래일/보고서 유형의 예약이 있다. 예약은 삭제하거나 우회하지 말고 이전 실제 수신 상태부터 확인한다.

dry-run은 Configuration.from_environment를 호출하지 않으므로 값 존재/목록 일치 및 dry-run 성공만으로 실제 전송의 설정 검증 통과를 보장하지 않는다. 사용자 터미널의 환경변수는 진단 에이전트 프로세스에 전달되지 않았다. 따라서 과거 포괄적 rejected 응답의 정확한 실패 조건은 아직 미확정이며 동일 터미널의 --check 출력으로 확정해야 한다. 비밀값을 공유할 필요는 없다.

검증 결과: 기존 합성 보고서는 테스트용 설정 주입 시 문자 전용 preflight_ok, mock 및 candidate_confirmed=false 유지. Telegram 테스트 34개(진단 5개 추가), 음성 기반 30개 통과. 기존 전체 회귀 840개 기록은 보존하고 반복하지 않았다. 실제 전송/OpenAI 호출은 수행하지 않았다.

preflight_ok 확인 및 사용자 재승인 후에만 실행할 문자 1건 명령:

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.telegram_briefing --report reports/telegram-once-preflight.json --send
```

## C5-F MP3 전송 실패 진단

실제 음성 전송 결과는 failed_or_unknown / telegram_transport_failed였다. 기존 api_call은 session.post와 response.json에서 발생한 일반 예외를 모두 같은 코드로 바꿨다. 따라서 DNS/TCP/TLS/타임아웃/JSON 해석 실패 중 어느 경우인지 과거 기록만으로 확정할 수 없다. 기존 음성 예약은 reserved 및 voice_sent=false이며 상세 예외가 없다. message_sent=false와 document_sent=false는 음성 전용 모드에서 문자/문서를 요청하지 않았다는 뜻이고, voice_sent=false는 성공 확인이 없다는 뜻이다. 실제 Telegram 수신 여부는 알 수 없다.

| 항목 | 문자 | 음성 |
|---|---|---|
| 메서드 | sendMessage | sendVoice 또는 sendAudio |
| 요청 본문 | JSON | MP3 포함 multipart/form-data |
| 호스트/인증 | 고정 api.telegram.org, URL의 Bot Token | 동일 |
| TLS/리다이렉트 | verify=True, redirects=False | 동일 |
| 타임아웃 | (5,20) | 동일 |
| 프록시·재시도 | trust_env=False, retries=0 | 동일 |

97.104초는 오디오 재생시간이며 업로드 시간이나 API 처리 시간과 같지 않다. multipart 업로드/응답 수신이 하나의 requests session.post에 묶여 있으므로 응답을 받기 전 타임아웃을 업로드 실패로 단정하지 않는다. 실패 단계 upload_or_response는 이 불확실성을 명시한다. 설정과 기존 문자 성공 흐름은 변경하지 않았다.

최소 수정은 TelegramRequestError와 고정 진단 분류다. 기록 필드는 method, stage, http_status, api_error_code, exception_type뿐이다. DNS/TCP/TLS/인증서/연결·읽기 타임아웃을 예외 체인의 알려진 타입으로 분류하며 예외 메시지는 보지 않는다. HTTP 비200과 HTTP 200의 ok=false를 구분하고, error_code는 허용된 공개 정수만 남긴다. 응답 JSON 오류·수신자 불일치도 단계별로 구분한다. 설명문, 헤더, URL, Chat ID, 요청 본문, 오디오 바이트는 기록하지 않는다.

새 실패는 기존 배타적 예약 파일의 열린 descriptor에 failed_or_unknown과 정제된 failure를 fsync한다. 기존 예약은 삭제/재작성하지 않는다. session.post가 응답 전에 실패하면 http_status=null이며 서버가 요청을 받지 않았다는 보장은 아니다. 본문이 비정상이어도 실제 메시지가 생성됐을 수 있으므로 failed_or_unknown을 유지한다. 중복 방지·재시도 없음·문자/HTML 자동 대체 없음 정책을 유지한다. OpenAI TTS는 다시 호출하지 않는다.

다음 조치는 휴대폰에서 실제 수신 여부를 먼저 확인하는 것이다. 결과 불명 예약을 지우거나 새 파일명으로 우회해 재전송하지 않는다. 새 파일명도 동일 거래일/대상 음성 중복 키를 바꾸지 않는다. 미수신으로 판단하더라도 실제 재전송은 별도 승인과 복구 절차 결정 후에만 가능하다. 이번 진단에서는 실제 Telegram/OpenAI를 호출하지 않는다.

최종 검증: 전체 회귀 **890개, 166.224초, OK**. Telegram 39개, 음성 기반 30개, TTS 40개 포함. 외부 네트워크/운영 경로 접근은 audit guard로 차단했다. 기존 실제 Telegram 예약 두 개는 SHA256 일치로 보존을 확인했다. 로그는 Git 제외 `.venv/test-results-c5f-telegram-diagnostic.txt`에 보관한다. 이번 수정은 telegram_briefing.py, test_telegram_briefing.py, 본 문서이며 앞선 미커밋 작업은 유지했다. 실제 재전송/OpenAI 호출/Commit/Push는 수행하지 않았다.
