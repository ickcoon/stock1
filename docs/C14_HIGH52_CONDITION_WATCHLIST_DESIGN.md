# C14 확장 설계 — 52주 신고가·조건검색 관심종목

2026-10-31 KST. 조사·설계 문서만 추가했다. 기존 C14-A 미커밋 파일, C12/C13 및 운영 stock1을 보존했다.
OAuth·키움 API·조건검색 WebSocket 호출·DB 접근/변경·테스트·Commit/Push는 하지 않았다. 공개 공식 문서만 조회했다.

## 재사용 가능한 구조와 현재 한계

| 기존 기능 | 재사용 범위 | 확장 시 주의 |
|---|---|---|
| C12 equity_eligibility/am_candidate_adapter | 상품 후보와 제외·보류·미매칭 진단, 0198/0186 연결 | 조건검색에 등장해도 상품 필터를 우회하지 않음 |
| AM_MOMENTUM/am_briefing | 반복 TOP20·섹터 동반 상승·상대 거래대금 및 선정 근거 | 조건식 횟수를 반복 TOP20 횟수로 바꾸지 않음 |
| daily_chart/daily_tracking_adapter | 원본 일봉·단위·기업행위 관련 원문·확정성/수신 근거 | 수정 기준 혼용·미완성 봉·미래 수신 금지 |
| analyst/chart.py | MA3/5/20/60/200·단기 이격·20봉 매물대 추정 | 200봉만으로 완전한 52주 이력을 보장하지 않음 |
| conditions.py | 목록/일반검색 파서, 접두어 보존, 이력·중복 진단 | TARGETS의 기존 이름 3개 고정, 자동 갱신/실시간 구독 미구현 |
| condition_definitions/runs/members | 환경·검색식 ID/이름·요청/완료 시각·회차별 종목/원문 | 개별 종목 최초 편입 시각이나 검색식 본문/버전은 증명하지 못함 |
| condition_report/workbench | 복수 조건 합집합·중복·0198 교집합·근거 표시 | 최신 자료를 과거 시점의 이력으로 소급하지 않음 |
| external_tags | CSV 문자열 코드·출처·파일 hash·검증 UX의 설계 패턴 | 현 import는 섹터 DB를 쓰므로 조건식 이력용으로 실행하지 않음 |
| HTML dashboard/analyst JSON | 독립 계산 결과를 카드에 표시하는 구조 | 뉴스는 C14-A placeholder이며 이번에도 구현하지 않음 |

현 fetch_conditions는 Store 초기화·조건검색 테이블 migration·종목 마스터 등록을 수행한다. 읽기 전용 진단 명령으로 사용하지 않는다.
ConditionClient.open은 현재 authenticate를 직접 호출한다. C13 캐시/ensure_token이 WebSocket 로그인·재로그인에 자동 연결됐다고 주장하지 않는다. 향후 명시적 인증 어댑터와 연결 예산을 검토해야 한다.

## 조건검색 공식 지원 범위

확인일 기준 [키움 공식 명세](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json):

| API | 메시지 | 지원 범위 | 현재 프로젝트 |
|---|---|---|---|
| ka10171 | CNSRLST | 영웅문4에서 만든 조건식 목록 | 구현됨 |
| ka10172 | CNSRREQ, search_type=0 | 일반조회, seq 및 연속조회, KRX | 구현됨 |
| ka10173 | CNSRREQ, search_type=1 | 실시간 조회, 사전 목록조회 필요 | 미구현 |
| ka10174 | CNSRCLR | 실시간 조건 해제 | 미구현 |

전송은 /api/dostk/websocket이며 HTTP 조회처럼 처리하지 않는다. [공식 WebSocket 클라이언트](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/core/ws_client.py)에도 로그인·메시지 처리 흐름이 있다.
실전/모의 주소가 기재돼 있어도 해당 계정·조건식의 모의 지원, HTS 결과 일치, 조회 한도까지 실측 검증된 것은 아니다. 자동 real 전환은 금지한다.
이번 조사에서 확인한 지원은 저장 조건의 조회/실행/실시간 해제다. 검색식 본문 생성·편집이나 모든 거래소 지원을 API가 보장한다고 주장하지 않는다.
HTS 저장 목록에서 실제 ID/이름을 확인하는 연결 검증은 별도 승인 후 진행한다. 이번에는 호출하지 않았다.

## 52주 고가 계산 계약

기준 D는 가격 관측이 속한 거래 세션이다. as_of의 날짜나 현재 PC 날짜를 무조건 대입하지 않는다.
주 지표의 창은 D−52주(364일) 이상, D 미만의 완료 세션이다. 해당 창의 모든 기대 거래 세션이 수신·확정·동일 가격 기준으로 검증돼야 한다.

- prior_52w_high = 위 창의 일봉 high 최댓값. high_dates, 시작/끝 날짜, 실제 세션 수, 기준/원본 reference를 저장한다.
- price_vs_prior_high_pct = (관측 가격 / prior_52w_high − 1) × 100.
- distance_below_prior_high_pct = (prior_52w_high − 관측 가격) / prior_52w_high × 100. 돌파 시 음수 그대로 표시한다.
- new_high_observed = 관측 가격 > prior_52w_high. 같으면 재접촉이며 엄격한 경신이 아니다.
- 당일 누적 고가가 별도로 검증돼 있으면 session_high_crossed를 분리한다. 현재가만으로 과거 장중 고가 경신 이력을 복원하지 않는다.
- 당일 확정 종가가 위 기준을 넘었을 때만 close_breakout_confirmed를 별도 표시한다. 장중 경신을 종가 돌파로 승격하지 않는다.
- 당일 관측을 포함한 running_52w_high는 별도 값이다. 이를 돌파 판정의 분모로 사용하지 않는다.

이는 rolling 52주 정의이며 연중 최고·역대 최고·고정 250/252거래일 고가와 다르다. 공식 ka10001의 250hgst는 250최고로 표기돼 있어 대체값으로 확정하지 않는다. 현재 market_cap의 정규화 FIELDS에는 이 필드가 없다.
이력 부족·신규 상장·누락 봉·수정주가/기업행위 비교 미확인·일봉 완결성 미확인이 있으면 full_window_verified=false, 52주 지표는 unavailable이다. 필요 시 sample_high/since_listing_high를 별도 참고값으로 표시하되 같은 지표로 이름 붙이지 않는다.
현재 200봉 MA가 계산된다는 사실만으로 52주 창이 완전하다고 보지 않는다. C14-A의 가격 기준·일봉 가용 시각·캘린더·Decimal 산술을 재사용한다.

### 최근 돌파 시도·근접·가산점

최근 N개 완료 세션 각각의 직전 52주 고가와 high/close를 비교한다. 접촉(high=기준), 장중 경신(high>기준), 종가 미돌파(close≤기준), 종가 돌파(close>기준)를 별도 사실로 저장한다.
lookback N과 근접 거리 기준은 버전 있는 설정으로 관리한다. 본문 전략의 규칙이라고 주장하지 않는다. N이 없으면 최근 시도 판정은 not_configured로 두고 관측 사실만 출력한다.
과거 시점의 실시간 신호를 주장하려면 그 당시 가용했던 자료가 필요하다. 나중에 받은 조정 이력으로 과거 봉을 다시 계산한 결과는 retrospective_as_of_current로 구분한다.
near_high_pct=5%는 실험 후보 가설로만 제안하며 기본 활성화는 false다. 3/5/10% 등 민감도와 오탐·손실·시장 국면별 차이를 후속 검증한다. 검증된 최적 구간이나 매수 기준이 아니다.
high52_bonus는 enabled=false, weight=0을 기본으로 별도 설정한다. 활성화하더라도 실험용 보조 설명이며 기존 AM 순위·매수/매도·비중을 자동 변경하지 않는다. MA 정배열·조건식 중복과 같은 근거를 여러 번 가산하지 않는다.

최근 20봉 매물대 추정 영역을 동일 가격 기준의 prior_52w_high와 비교해 영역이 아래/걸침/위에 있는지 및 거리를 표시한다. C14-A의 일봉 기반 추정이라는 한계를 유지하며 확정 저항·체결 가격별 분포로 표현하지 않는다.

## 전략 태그와 본문 확인

전략 태그는 larry_williams_volatility_breakout, minervini_trend_template, darvas_box로 서로 독립적으로 등록한다.
기존 TARGETS의 주도주종베/당일주도주/끼도주 alias를 교체하거나 섹터 태그로 저장하지 않는다. 새 condition registry에서 HTS 이름·seq와 전략 태그를 명시적으로 매핑한다.
조건식 ID는 환경/사용자 범위·목록 snapshot·조건식 버전과 함께 식별한다. 이름/번호만 같다고 같은 규칙으로 확정하지 않으며 변경 전 매핑을 덮지 않는다.
목록 API만으로 검색식 상세 규칙을 알 수 있다고 가정하지 않는다. 본문이 없으면 rule_status=awaiting_body_rules다. 조건식 이름이 일치한 것은 해당 전략의 정식 규칙 검증이 아니다.

구현 전에 책/판본/장·페이지와 사용자가 검토한 규칙을 별도로 확보한다. 공통 확인 사항은 사용 가격·거래량, 기간·계수, 진입 확인 시점, 종가/장중 구분, 거래일, 수정주가, 갭·결측·정지 처리 및 신호 유효기간이다.
각 전략의 범위 정의, 추세 템플레이트의 세부 필터, 박스 확정/갱신/돌파 규칙은 본문 확인 전 수식이나 숫자로 채우지 않는다. 목차·전략명에서 추정 구현하지 않는다.

## 관심종목 및 출현 이력 모델 — 논리 설계만

기존 운영 DB는 변경하지 않는다. 우선 오프라인 JSON 계약으로 설계하고 영속화가 필요할 때 별도 분석 저장소를 검토한다.

| 개체 | 최소 필드/원칙 |
|---|---|
| condition_definition | 내부 ID, strategy_tag, environment/app_scope_id(키 원문 아님), raw seq/name, 목록 snapshot, rule_version/근거, available_at |
| condition_run | run_id, definition revision, request_started_at/response_received_at, status, complete/pagination, source_kind, 환경·시장·원본 hash |
| condition_occurrence | run/event ID, definition revision, raw_code, namespace/normalized_code, 관측 구간·received_at·available_at, event_type, source reference |
| watchlist_entry | 분석 범위별 종목 ID 1개, 태그/원천 목록, first_observed_at/last_observed_at, eligibility 및 pending 사유 |
| metric/analysis_result | 종목·기준 시각·available_at·module_version·config hash·evidence refs·quality/risk 상태 |

관심 목록은 (environment, venue, instrument_namespace, normalized_code)별 한 행이다. 상장시장 KOSPI/KOSDAQ과 거래시장 KRX/NXT/SOR는 별도 필드로 유지한다.
출현 이력은 검색식별로 여러 행을 유지한다. 같은 run+definition revision+종목은 중복 제거하되 서로 다른 조건/시각/원문 이력은 삭제하지 않는다. 전송 재수신은 event ID 또는 원문 hash+회차로 멱등 처리한다.
mock/real·fixture/API 응답은 섞지 않는다. J/Q(ELW/ETN) 접두어를 A(주식)처럼 제거하지 않는다. 기존 조건 파서는 A/J/Q+숫자 6자리만 허용하므로 영문 포함 종목코드는 지원 근거 확인 전 보류·진단하며 임의 숫자 변환하지 않는다.
0198/0186도 원본 코드와 정규화 코드·상품 범위를 유지해 결합한다. 미매칭·제외 상품은 관심 이력 자체를 지우지 않고 분석 후보 통과 여부를 별도로 표시한다.
조건검색만 출현한 종목에 TOP20 반복/수급 강도를 만들지 않는다. 복수 검색식 출현 횟수는 근거의 독립성이나 매수 점수가 아니다.

일반조회는 해당 조회에서 관측됐다는 사실이다. 정확한 최초 편입 시각이 없으면 source_event_at=null로 두고 요청~응답 구간과 receipt를 저장한다.
first_observed_at을 실제 최초 편입 시각으로 부르지 않는다. 실패/미실행/부분 페이지의 부재를 이탈로 추정하지 않는다. 실시간 I/D 이력은 별도 단계다.

## HTS/CSV 경로

최초 구현은 기존 저장 결과 또는 수동 CSV의 읽기 전용 preview를 우선한다. 조회 권한/모의 지원/HTS 일치가 제한되면 이 경로를 사용한다.
CSV 필드는 stock_code, raw_code, condition_external_id, condition_name, strategy_tag, observed_at(선택), timezone, source_reference, rule_version 등으로 한다. imported_at/file_sha256/행 번호는 시스템이 별도로 기록한다.
CSV 출현 시각이 미확인이면 비워 두고 import 시각을 과거 출현 시각으로 대신하지 않는다. available_at은 실제 수신/가져오기/검토 시점 이후로 둬 소급 노출을 막는다.
문자열 코드·원본·파일 hash 검증 패턴만 재사용한다. 기존 sector import/fetch_conditions를 실행해 운영 마스터/섹터에 쓰지 않는다.
API 연결은 추후 별도 승인으로 목록→일반조회부터 확인하고, 인증 갱신이 WebSocket 세션 재로그인을 자동 해결한다고 가정하지 않는다. 실시간 구독은 후순위다.

## 종합 대시보드

수급/뉴스/차트 MA/52주 위치/매물대 추정/조건식 이력을 각각 독립 결과로 저장하고 종목·as_of·available_at 기준으로 읽기 전용 조합한다.
한 종목 카드에 여섯 패널을 두되 원본 날짜·출처·검증/잠정/보류·이전 관측 상태를 각각 표시한다. 가장 늦은 데이터 시각으로 다른 모듈의 시각을 덮지 않는다.
결합 시 모든 사용 사실의 available_at≤as_of를 요구한다. 뉴스 미구현/미수집을 좋은 뉴스로, 결측을 0점/위험 없음으로 바꾸지 않는다.
후보 필터와 기존 15개 섹터는 유지하며 추가 가산점은 기본 OFF다. 최종 자동 매매 결정·주문·매수 비중·일괄 종합 점수는 만들지 않는다.

## 최소 구현 순서

1. 52주 지표/관심 이력 JSON 계약과 버전 설정을 확정하고, 검토된 저장 일봉에서 순수 계산기를 구현한다. 창 경계·동일가 재접촉·장중/종가·상장 이력 부족·기업행위·미래 누출을 합성 검증한다. 전략 본문 규칙은 별도 확인한다.
2. 수동 CSV 또는 이미 저장된 조건검색 결과를 읽기 전용으로 정규화하는 관심종목 adapter를 만든다. 한 종목 다중 태그·이력 멱등성·미매칭·상품 제외·시점 차단을 검증하고 0198/0186 결과에 조인한다. 기존 운영 저장 경로는 변경하지 않는다.
3. analyst의 독립 결과 reference를 연결하는 종목별 통합 카드만 추가한다. 이후 승인된 조건 API 일반조회/HTS 대조를 별도 진행하고, 실시간 구독과 가산점 최적화는 충분한 검증 뒤로 미룬다.

이번 단계에는 코드·DB·대시보드 수정이나 테스트 실행이 없으며, 이 설계 문서만 추가했다.
