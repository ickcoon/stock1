# Step 7-C5-E — 한국어 TTS와 독립 Telegram 음성 전송

개발 폴더 stock1-dev 전용이다. 운영 경로/DB/수집기/스케줄러, 실시간 시세, STT/GPT, 자동 스케줄은 연결하지 않는다. 이 단계에서는 실제 유료 API나 Telegram을 호출하지 않는다. C5-D 미커밋 진단 개선과 기존 문자 전송 계약을 보존한다.

## 모델과 공식 계약

2026-10-29 KST 확인: [Speech API Python SDK 레퍼런스](https://developers.openai.com/api/reference/python/resources/audio/subresources/speech/methods/create)의 `audio.speech.create` 요청 계약을 사용한다. [TTS 가이드](https://developers.openai.com/api/docs/guides/text-to-speech)는 한국어와 MP3, 스타일 지시를 지원한다. 기본은 `gpt-4o-mini-tts`, `marin`, MP3, speed=1.0이다. 임의 모델로 자동 대체하지 않는다.

공식 문서에는 이 모델이 deprecated이며 2027-01-06 종료 예정이라고 표시된다. 실제 실행 승인 시 지원/계정 접근 가능성을 다시 확인해야 한다. 404는 `tts_model_unavailable`, 400은 `tts_request_rejected`로 반환한다. API 원문 오류는 출력하지 않으므로 400에서 모델 오류라고 단정하지 않는다.

[모델 가격](https://developers.openai.com/api/docs/models/gpt-4o-mini-tts)은 입력 텍스트 $0.60/백만 토큰, 출력 오디오 $12.00/백만 토큰이다. dry-run에 모델·음성·과금 기준을 표시한다. 토큰 수를 측정한 실측 호출은 아직 없으므로 최종 청구액을 사전에 확정하지 않는다. 원고/수신 크기/실측 길이 제한은 로컬 검증이며 서버의 생성 토큰 상한 파라미터는 아니다. 응답이 로컬 검증에서 거절돼도 이미 요청된 생성 비용은 발생할 수 있다.

현재 SDK를 별도 설치하지 않고 기존 requests로 동일한 공식 HTTP 계약 `POST https://api.openai.com/v1/audio/speech`를 구현했다. 의존성 파일 변경은 없다. 실제 SDK 바이너리를 설치/실행한 검증이 아니라 공식 Python SDK의 타입·요청 예시와 계약을 대조한 것이다.

## 분리된 세 실행 모드

작업 디렉터리는 `C:\Users\dada1\Downloads\stock1-dev`다. 기존 합성 보고서 `reports/telegram-once-preflight.json`을 사용한다. 다른 보고서도 기존 엄격한 fixture-only AM 계약을 통과해야 한다. 실측 JSON이라고 가장하거나 날짜/상태/지문을 임의 수정하지 않는다.

### A. 원고·설정·경로 dry-run — 승인/외부 API 불필요

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.openai_tts --report reports/telegram-once-preflight.json --name korean-briefing-once
```

네트워크 세션/예약/파일을 생성하지 않는다. API Key는 존재 여부만 표시한다. 원고, 보고서 바인딩, 설정 허용값, 이름/출력 경로를 검증한다. 기존 파일을 덮어쓰지 않으며 세 파일이 모두 있으면 오디오와 생성 기록도 재검증한다. `generation_name_available=false`면 같은 이름의 생성은 실행하지 않는다. 실패 기록을 삭제하거나 이름을 바꿔 무조건 재시도하지 말고 별도 비용 승인을 받아야 한다.

### B. TTS 1회 생성 — 별도 사용자 승인 후에만

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.openai_tts --report reports/telegram-once-preflight.json --name korean-briefing-once --generate --approve-tts
```

`--generate`와 `--approve-tts`가 모두 필요하다. 한 공급자 인스턴스와 한 실행에서 HTTP 요청은 최대 한 번이며 자동 재시도하지 않는다. Telegram은 호출하지 않는다. 요청 전에 보고서·원고·키·출력 파일 충돌을 검사하고 `reports/korean-briefing-once.tts-attempt.json`을 배타적으로 예약/fsync한다. 실패/중단/결과 불명에도 예약을 유지한다. 실패한 TTS를 문자로 대신 보내지 않는다.

성공 시 Git 제외 reports 안에 다음 파일을 만든다.

- `korean-briefing-once.mp3`: 실제 Speech API 응답.
- `korean-briefing-once.speech.json`: 기존 SpeechScript, 보고서 지문, 원고 SHA256, 오디오 SHA256, 공급자, 형식, 길이, is_test=false.
- `korean-briefing-once.tts-attempt.json`: 생성 완료 여부와 보고서/원고/오디오 지문. 비밀값·PC 경로 없음.

이후 A 명령으로 저장된 파일을 다시 검증하고 로컬에서 직접 청취한다. 구조/해시 검증은 실제 발화 내용, 한국어 품질 또는 무음 여부의 청취 검증을 대체하지 않는다. 검증된 로컬 파일을 신뢰하는 계약이며 지문은 외부 서비스의 암호학적 발행 인증이 아니다.

### C. 검증된 음성 1개 전송 — B와 독립된 별도 승인 후에만

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.openai_tts --report reports/telegram-once-preflight.json --name korean-briefing-once --send-voice --approve-telegram
```

기본 `sendVoice`로 음성 1건만 보낸다. `--method sendAudio`를 추가하면 같은 파일을 오디오 1건으로 보낸다. 문자와 HTML을 함께 보내지 않는다. C는 OpenAI Key나 TTS 재생성이 필요 없다. 두 실행 모드는 동시에 사용할 수 없고 승인 플래그도 서로 공유하지 않는다. B 성공이 C 승인이 되지 않는다.

보고서에서 원고를 다시 생성해 메타데이터와 완전히 비교하고, MP3 지문·구조·길이·생성 완료 기록·비시험 공급자를 검증한다. 형식 검사용 SilenceFixtureTTS/is_test=true 산출물은 거절한다. 전송은 기존 허용 목록/고정 호스트/TLS/timeout/비재시도/오류 비공개 경로를 재사용한다.

음성 전용은 명시적 별도 전송 종류 `voice-only-v1` 예약을 사용한다. C5-D 문자 예약은 그대로 유지하며 문자 성공 후 음성 1건의 독립 승인을 지원한다. 기존 묶음 전송에서 음성을 이미 보냈거나 기존 예약이 진행 중/결과 불명이면 새 음성 전용 발송도 차단한다. 거래일·선정 유형·schema·환경·시장·개인 대상별 음성 예약은 sendVoice/sendAudio, 파일 이름, 재생성 여부와 무관하게 공유한다. 오류/결과 불명에도 예약을 유지해 재전송을 차단한다. 예약을 삭제하거나 우회하지 않는다. 로컬 개발 폴더에서만 중복을 막으며 서로 다른 PC 사이의 exactly-once를 보장하지 않는다.

## 환경변수와 보안

| 환경변수 | 용도 |
|---|---|
| `OPENAI_API_KEY` | B에만 필요. 프로세스 환경변수, 출력/파일 저장 금지 |
| `ARVIS_TTS_MODEL` | 기본 gpt-4o-mini-tts. 허용 모델은 위 모델과 gpt-4o-mini-tts-2025-12-15 |
| `ARVIS_TTS_VOICE` | 기본 marin. 공식 내장 음성 허용 목록 |
| `ARVIS_TTS_FORMAT` | 기본 mp3. 현재 MP3만 허용하며 다른 형식은 거절 |
| `ARVIS_TTS_SPEED` | 기본 1.0, 유한 숫자 0.25~4.0 |
| `ARVIS_TELEGRAM_BOT_TOKEN` | C에만 필요. 기존 비공개 환경변수 |
| `ARVIS_TELEGRAM_CHAT_ID` | C 개인 수신 대상 |
| `ARVIS_TELEGRAM_ALLOWED_CHAT_IDS` | C 명시적 개인 대상 허용 목록 |

OpenAI Key는 사용자가 같은 PowerShell 터미널에서 직접 숨김 입력한다. `.env`는 자동 로드하지 않는다.

```powershell
$ttsInput = Read-Host 'OpenAI API Key (hidden)' -AsSecureString
try {
    $env:OPENAI_API_KEY = [System.Net.NetworkCredential]::new('', $ttsInput).Password.Trim()
} finally {
    $ttsInput.Dispose()
    Remove-Variable ttsInput
}
# 작업 종료 후
# Remove-Item Env:OPENAI_API_KEY -ErrorAction SilentlyContinue
```

키/토큰/Chat ID를 채팅, 명령 인자, 로그, 보고서, Git에 붙여 넣지 않는다. 공급자 설정 repr에도 키가 없으며 HTTP 디버그 로그를 호출 범위에서 끄고 복원한다. 응답 오류 본문·헤더·인증정보를 출력하지 않는다. trust_env=False, redirects=False, TLS 검증 유지, retries=0, timeout=(5,90), 수신 최대 4 MiB다. API 입력 최대 4096자보다 작은 로컬 원고 상한 2000자를 사용한다. 반환 MP3가 60~120초를 벗어나면 저장/전송하지 않는다.

## 원고와 기존 기능

원고는 날짜/KST 기준 시각, mock/real 및 합성 출처, 최대 3종목의 TOP5/TOP20 관측 횟수·근거·동반 섹터, 관측 가격, 미검증/누락 항목을 읽는다. 종목명이 없는 JSON에는 회사를 추정해 넣지 않고 코드 각 자리를 읽는다. 코드/숫자/금액/시각을 한글로 전처리하며 TOP5/TOP20도 풀어 읽는다. 후보 미확정·매매 확정 아님·합성 데이터·AI 음성 고지를 유지한다. 정보가 부족하면 임의의 TOP3나 수익률을 만들지 않는다. 1~2분 목표의 읽기 속도 추정과 실제 프레임 길이는 별개다.

원고 예시:

> 안녕하세요. 아르비스의 오전 관심도 브리핑입니다. 이 음성은 인공지능이 생성한 음성입니다. 이천이십육 년 십 월 이십구 일, 분석 기준은 오전 아홉 시 오십 분입니다. 데이터 환경은 모의, mock입니다. 다만 이 보고서의 출처는 합성 픽스처이며 실측 결과가 아닙니다. 이번 내용은 매매 확정 신호가 아닙니다.

기존 `telegram_briefing --send` 문자 전송과 문자/HTML/음성 묶음의 키/흐름은 유지한다. 구형 voice_briefing CLI에서 TTS 미설정/무음 fixture로 `--send`할 때 문자로 자동 대체하던 경로는 명시적으로 거절하도록 강화했다. 실제 생성/전송에는 이 문서의 분리 CLI를 사용한다. voice_dialogue/STT/GPT 계약은 변경하지 않았다.

## 검증

기본 테스트는 합성 JSON, 임시 파일, 가짜 HTTP/TTS만 사용한다. 외부 네트워크/운영 경로 접근을 audit guard로 차단해 실행한다. 실제 OpenAI/Telegram 호출과 한국어 청취 검증은 승인 후 별도 수행해야 한다.

2026-10-29 KST 최종 검증: 전체 회귀 **870개, 163.888초, OK**. 기준 840개에 기존 C5-D 진단 5개와 C5-E 25개가 추가됐다. 기존 음성 기반 30개와 Telegram 문자/진단 34개도 포함해 통과했다. 오프라인 CLI dry-run은 `network_called=false`, `candidate_confirmed=false`, 원고 예상 107초, 새 생성 이름 사용 가능을 확인했다. 실제 TTS 파일 생성/Telegram 발송/OpenAI 호출은 0회다. 결과 로그는 Git 제외 `.venv/test-results-c5e-final.txt`에 보존한다.
