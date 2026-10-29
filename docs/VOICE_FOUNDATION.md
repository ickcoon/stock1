# Step 7-C5-C — 아르비스 양방향 음성 브리핑 기반

## 현재 구현과 경계

개발 폴더 stock1-dev 전용 기능이다. 기존 AM/Telegram 계약을 재사용하며 운영 폴더·DB·수집기·스케줄러·C4/D0는 접근/변경하지 않는다. 실제 OpenAI API, 유료 TTS, Telegram 발송, 업데이트 수신, 음성 다운로드, STT/GPT 호출, 자동 스케줄은 이번 단계에서 실행하지 않는다.

- `voice_briefing.py`: 보고서 기반 한국어 원고, 숫자/코드/가격/시각 발음, TTS Protocol, 음성 바인딩/형식 검증, dry-run CLI.
- `voice_dialogue.py`: 외부에서 전달받은 Telegram 음성 update 구조, STT 전사 바인딩, 명시적 보고서 조회, 근거 ledger와 근거 ID 선택형 답변 계약.
- `telegram_briefing.py`: 기존 예약·허용 목록·TLS·오류 처리 안에 선택적 sendVoice/sendAudio 경로 추가. 기존 문자/HTML 경로를 유지한다.

현재 보고서는 C5-A 합성 fixture만 지원한다. real 표기도 합성 real 범위이며 실측을 의미하지 않는다. candidate_confirmed=false, d0_storage_allowed=false는 유지된다. 실제 보고서 연결은 별도 승인된 내보내기/검증 단계가 필요하다.

## 원고와 발음

실제 보고서 필드만 원고에 반영한다. 날짜·KST 마감 시각, mock/real와 합성 출처, 관심도 상위 최대 3종목의 TOP5/TOP20 횟수, 관측 가격/시각, 동반 섹터, 누락/미검증 정보를 말한다. 4개 오전 슬롯/TOP20이 부족하면 임의의 TOP3를 만들지 않는다. 실제 JSON에 종목명이 없으므로 코드를 읽으며 알려진 회사명을 코드로부터 임의 보충하지 않는다. 이름 발음 helper는 향후 명시적 이름 필드 계약이 추가될 때 사용할 수 있다.

예시: `005930` → 공 공 오 구 삼 공, `0053F0` → 공 공 오 삼 에프 공, `SK하이닉스` → 에스 케이 하이닉스, `10500` 원 → 일만 오백 원, `09:50:12 KST` → 오전 아홉 시 오십 분 십이 초.

합성 기본 원고는 공백 제외 초당 7자라는 공개된 읽기 속도 가정으로 약 107초를 추정한다. 1~2분 분량을 목표로 한 추정이며 실제 음성 길이를 보장하지 않는다. 데이터 부족 예시도 사실/분석 한계를 설명하며 숫자나 종목을 만들어 채우지 않는다. 실제 길이는 생성 MP3 프레임에서 별도로 계산한다.

원고 예시 일부:

> 안녕하세요. 아르비스의 오전 관심도 브리핑입니다. 실제 음성을 연결하면 인공지능이 생성한 음성으로 안내합니다. 이천이십육 년 십 월 이십구 일, 분석 기준은 오전 아홉 시 오십 분입니다. 데이터 환경은 모의, mock입니다. 다만 이 보고서의 출처는 합성 픽스처이며 실측 결과가 아닙니다. 종목명은 보고서에 없으므로 종목 코드로 읽겠습니다.

`SpeechScript`는 원고 schema, 보고서 schema/version, evaluation_reference, 거래일·마감·환경, 원문 text와 SHA256, 추정 길이를 가진다. `AudioArtifact`도 보고서 버전·참조·원고 SHA256을 함께 전달하며 원고를 보고서에서 재생성해 일치 여부를 검사한다. 다른 보고서/원고의 음성은 거절한다.

## TTS 공급자 연결

`TTSProvider.synthesize(SpeechScript) -> AudioArtifact`를 구현해 주입한다. 원고를 수정하지 않고 받은 script/report 참조를 그대로 결과에 포함해야 한다. 공급자 내부 로그에도 입력 원고 외 인증정보·URL·키가 출력되지 않도록 해야 한다. 현재 실사용 공급자는 연결되어 있지 않다.

| 공급자 | 이번 단계 | 후속 연결 방법 |
| --- | --- | --- |
| DisabledTTS | 기본값. 음성 미설정 오류를 안전한 문자 대체 상태로 반환 | 별도 설정 불필요 |
| SilenceFixtureTTS | 외부 호출 없이 유효한 무음 MP3 프레임 생성, is_test=true | 전송/컨테이너 테스트 전용. 한국어를 읽는 음성이 아님 |
| 로컬 TTS | 미연결 | 승인한 로컬 엔진을 고정 어댑터 내부에서 호출하고 MP3로 반환. 사용자 질문을 명령어나 인자로 실행하지 않을 것 |
| OpenAI TTS | 미연결, API Key 미사용 | 승인 후 최신 공식 API/모델을 선택해 어댑터 작성. OPENAI_API_KEY는 실행 환경변수로만 읽고 Git/원고/메타데이터에 넣지 않을 것 |

[공식 OpenAI TTS 가이드](https://developers.openai.com/api/docs/guides/text-to-speech)는 지원 출력 포맷과 AI 생성 음성 고지를 설명한다. 이 단계에서는 특정 모델·SDK·유료 호출을 고정 구현하지 않는다. 공급자 연결 시 최신 계약과 한국어 발음을 다시 검증한다.

출력은 우선 MP3/audio-mpeg로 한정한다. [Telegram sendVoice](https://core.telegram.org/bots/api#sendvoice)와 [sendAudio](https://core.telegram.org/bots/api#sendaudio)의 MP3 전송을 사용한다. MPEG Layer III 프레임/길이/크기(4 MiB 이하, 180초 이하)를 검사하며 ID3 표기도 처리한다. 이는 구조 검사이지 음성 내용을 STT로 재검증하는 기능은 아니다. 공급자가 실제로 해당 원고를 발화했는지는 신뢰된 어댑터와 청취/후속 검증이 필요하다.

테스트용 무음은 약 1.045초이며 **실제 발화 결과가 아니다**. is_test=true 음성은 전송기에서 제외하고 문자/HTML만 유지한다. 테스트 마커를 바꾸어 실사용 음성으로 위장하면 안 된다.

## 실행과 산출물

기본 dry-run은 외부 TTS·Telegram 호출이나 전송 이력 쓰기가 없다.

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.voice_briefing --report reports/am-synthetic-review-final.json --html reports/am-synthetic-review-final.html
```

테스트용 MP3와 원고 메타데이터 생성(새 이름을 사용):

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.voice_briefing --report reports/am-synthetic-review-final.json --html reports/am-synthetic-review-final.html --provider fixture --name voice-demo
```

`reports/voice-demo.speech.json`과 `reports/voice-demo.mp3`를 생성한다. reports는 Git 제외이며 경로 이탈·심볼릭 링크·덮어쓰기를 거절한다. 생성한 speech.json에는 보고서 참조·원고·음성 형식·길이·해시·테스트 마커가 있으며 키/Chat ID/PC 경로는 포함하지 않는다.

`--send`는 실제 Telegram 발송 경로이므로 사용자 승인 전에는 실행하지 않는다. 이번 CLI에는 실제 TTS 공급자가 없어 disabled는 문자 대체, fixture도 테스트 음성을 제외한 문자/HTML만 전송하게 된다. 실제 음성은 승인된 공급자가 반환한 결과를 프로그램에서 `telegram_briefing.deliver(..., voice=(script,audio), voice_method='sendVoice')` 또는 sendAudio로 전달해야 한다. Chat ID와 Telegram 환경변수 설정은 [기존 문서](TELEGRAM_BRIEFING.md)를 따른다.

문자 → HTML → 음성 순서다. TTS 생성 오류에는 문자 브리핑을 유지한다. 음성 업로드 오류가 나도 이미 발송한 문자/HTML을 취소하거나 재전송하지 않는다. 같은 날짜·선정 유형·보고서 버전·환경·시장·대상은 C5-B의 예약을 공유한다. 문자만 보낸 뒤 같은 보고서에 음성을 추가해 다시 보내는 것도 중복으로 차단한다. 자동 재시도는 없으며 실제 수신 여부를 확인한 뒤 별도 복구 절차를 결정한다.

## 양방향 질문 계약

흐름은 아래와 같이 나눴다. 이번 단계에는 각 경계의 자료형과 메모리 보고서 저장소만 있고 실제 수신 루프/네트워크/AI 서비스는 없다.

`제공된 update → accept_voice → IncomingVoice → STTProvider → Transcript → ReportKey/ReportRepository → AnswerContext → AnswerProvider의 근거 ID 선택 → grounded_answer → 후속 답변 TTS`

- 개인 chat.type, 양의 정수 Chat ID 허용 목록, 발신자 ID를 검사한다. 허용되지 않은 입력은 무시한다. file_id는 제한된 opaque 참조이며 URL/파일 경로로 사용하지 않는다. 음성은 허용 MIME, 180초/4 MiB 제한을 가진다. 계정·Chat ID·file_id·전사 텍스트는 repr에 노출하지 않는다.
- STT 결과는 해당 메시지/chat/file/수신 시각의 해시 binding과 일치해야 한다. 질문 길이/민감정보를 검사하며 질문을 파일 경로·쉘 명령·보고서 수정 지시로 실행하는 경로는 없다.
- ReportKey는 거래일·환경·마감 시각·선정 유형·schema·시장이다. MemoryReports는 명시적으로 제공한 보고서 객체만 저장하고 조회 시 복사본을 반환한다. 같은 키의 중복 보고서는 임의로 최신을 고르지 않고 거절한다. 다른 시장·환경·시점의 보고서 반환을 차단하고, 질문 수신 시각보다 늦게 생성된 보고서를 사용하지 않는다.
- AnswerContext에는 보고서 fingerprint, 고정 key, 신뢰하지 않는 질문 텍스트, 근거 ID/JSON pointer/정확한 value/관측 시각 ledger, missing_evidence가 있다. 허용 작업은 제공 보고서 읽기 하나뿐이다.
- 미래 GPT 인터페이스는 근거 ID를 선택하는 계약이다. 자유롭게 생성한 사실 문장을 그대로 채택하지 않는다. grounded_answer는 알려진 ID에 연결된 사실만 출력하며 잘못된 참조/존재하지 않는 근거를 거절한다. 인용 자료의 보고서 버전도 일치해야 한다.
- 주문/운영 변경 요청은 거부 답변을, 미래 예측이나 자료 부족은 알 수 없다는 답변을 만든다. 키워드 분류는 보조 기능이며 보안 경계는 DB/명령/매매 실행 도구 자체가 없다는 점이다. 원본 선정 결과는 변경하지 않는다.

다음 단계: 수신 호스트/개인 chat 승인, update 중복/재전달 방지와 인증, 제한된 파일 다운로드, 승인된 STT 공급자, 질문/개인정보 보존 정책, 실측 read-only 보고서 저장소, 근거 선택용 GPT 어댑터, 답변 원고/음성 바인딩, 실제 한국어 청취 검증을 각각 연결해야 한다. 답변 음성 전송 및 자동 스케줄은 아직 연결하지 않았다.

## 테스트

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_voice_foundation.py
.\.venv\Scripts\python.exe -B -m unittest discover -s tests
```

합성 JSON·임시 reports/state·가짜 TTS와 HTTP 세션을 사용한다. 실제 Telegram/OpenAI 호출, 운영 DB 접근, 실제 수신기는 테스트하지 않는다. 원고/발음/미검증/환경, MP3 구조, 보고서/원고 바인딩, 테스트 음성 발송 금지, dry-run, 두 전송 메서드·중복·부분 실패, 허용 대상/민감정보, 전사·보고서·근거/미래 정보 계약을 검증한다.

검증 기록 (2026-10-29 KST): 신규 음성 기반 테스트 30개와 기존 Telegram 독립 테스트 29개 통과. 최종 전체 회귀 840개, 154.760초, 실패 0·오류 0·스킵 0. 개발 폴더의 임시 경로를 사용하고 외부 네트워크·운영 경로 접근을 차단했다. 실제 OpenAI/TTS 서비스 호출·Telegram 전송·음성 수신기·자동 스케줄 연결은 수행하지 않았다.
