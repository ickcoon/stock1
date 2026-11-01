# C14-A 독립 종목 analyst 및 차트 엔진

2026-10-31 KST. 기준 HEAD 7d200cff1b29fb18a8982f9f571c0c9624e0fcf1.
별도 stock1-c14-dev 개발 사본에서만 구현했다. 운영 stock1 및 C13 개발 사본은 수정하지 않았다.
C13 개발 사본은 시작 시 미커밋 변경이 없었으며 로그·가상환경 등 제외 파일은 그대로 보존했다.

## 범위와 기존 연결

- daily_chart.DailyRequest/parse_response/DailyPage/DailyRow를 재사용한다. 원문·부호 정규화·OHLCV 오류·수신 시각·fingerprint가 이미 존재한다.
- daily_tracking_adapter.ClosingEvidence를 확정성/단위/세션 근거 전달에 재사용한다. 기존 to_tracking은 원주가 추적용이며 수정주가를 거부하므로 수정하지 않았다.
- C12의 analysis_candidate는 일반 보통주 verified와 다르다. 기존 후보 필터·AM_MOMENTUM 선정 결과를 입력 근거로 받을 뿐 재선정하거나 섹터를 추가하지 않는다.
- 기존 eligibility_dashboard는 build/review → render, AM 브리핑은 검증된 보고서 → render 구조다. 현재 각자의 JSON 계약을 사용하므로 analyst 결과를 기존 build 입력으로 직접 넣지 않는다. 향후 독립 종목 카드에서 analyst JSON을 참조할 수 있다. 이번에는 HTML/대시보드·수집기·스케줄러를 수정하지 않았다.
- 뉴스 분석·모델 호출·주문·비중 계산·실제 API/DB 접근을 제공하지 않는다.

## 모듈과 인터페이스

신규 파일은 kiwoom_collector/analyst/__init__.py, chart.py, daily_adapter.py다.

```python
from kiwoom_collector.analyst import analyze_stock, from_daily_pages

# JSON 계약을 직접 받거나 기존 Step 7-B 저장 페이지로부터 만든다.
report = analyze_stock(analyst_input)
```

입력 schema_version은 analyst-input-v1이다. 다음 값은 원본/외부 검토에서 전달하며 엔진이 verified/final 근거를 만들어내지 않는다.

| 입력 | 필수 의미 |
|---|---|
| stock_code/stock_name | 문자열 종목 코드와 이름. 시장 접미사는 보존 |
| as_of/environment/market/data_origin | 타임존 있는 기준 시각, mock/real, KRX/NXT/SOR, synthetic_fixture/api_response/unverified |
| price | value(문자열), KRW, observed_at/available_at/source, basis_id |
| bars | trade_date, open/high/low/close/volume, final 상태, market_at/finalized_at/available_at/source, 단위·세션·basis_id |
| selection | 주도주 선정 근거, source, available_at, reference와 선택 종목 코드. AM_MOMENTUM row를 보존 가능 |
| price_basis | id, mode(adjusted/original), verified, corporate_actions_checked, comparable, volume_comparable, effective_at/available_at/source |
| calendar | as_of와 같은 범위의 completed_sessions(거래일 ISO 문자열 목록), verified, available_at/source |
| session_basis | regular_session 또는 검토한 chart_session 등 명시적 세션 기준 |

price_basis.comparable은 가격 계열과 관측 가격의 기업행위 반영 기준이 비교 가능하다는 외부 검토 결과다. 단순 original/adjusted 라벨만으로 통과하지 않는다.
volume_comparable은 별도 거래량 비교 기준이다. 수정주가 요청 1을 보냈다는 이유만으로 거래량의 기업행위 조정 여부를 확정하지 않는다.
calendar는 해당 as_of까지 완료된 거래 세션 목록이다. 주말/휴장/누락을 날짜 차이만으로 추정하지 않는다. 목록과 근거가 없으면 계산/검토를 보류한다.
가격·일봉·선정 근거의 종목/환경/시장 불일치는 통과시키지 않는다.

from_daily_pages에는 pages와 위 metadata, context_id, expected_session_basis, ClosingEvidence 목록을 전달한다.
원문 재파싱 결과와 DailyPage 전체를 비교해 변조를 거부하며 page+row fingerprint 및 context_id·수신/검토 시각·단위/세션을 결속한다.
정렬은 analyst 내부에서 거래일 오름차순으로 정규화하며 원본 인덱스/페이지 순서를 남긴다. 기존 파서나 추적 어댑터의 정책은 바꾸지 않는다.

AM 선정 근거를 연결할 때는 selection.source=AM_MOMENTUM, reference=기존 evaluation_reference, available_at=실제 보고서 생성/가용 시각을 사용한다. selection_cutoff_at을 보고서 생성 시각으로 대신 쓰지 않는다.

## 계산과 경계

모든 가격 산술은 Decimal로 수행하며 JSON에는 정확한 십진 문자열을 출력한다.

- MA3/5/20/60/200 = 해당 완료 거래 세션의 확정 종가 합계/N. 관측 현재가를 종가에 끼워 넣지 않는다.
- 각 MA에 사용한 거래일·종가 목록·사용 가능 봉 수·부족 이유를 출력한다. 필요한 세션이 누락/오류이면 더 오래된 봉으로 창을 메우지 않는다.
- 현재가와 MA5/20/200은 above/equal/below 또는 계산 불가로 구분한다.
- trend_state는 MA20과 MA60의 above/equal/below다. 이동평균 기울기나 미래 상승 예측은 아니다.
- 단기 이격은 price/MA × 100(100 기준)과 (price/MA − 1) × 100(0 기준)을 MA3/5 각각 출력한다.
- 과열 임계값은 없고 overheat_assessment=not_rated_no_threshold다. 이격이 커도 자동으로 과열/매수 신호를 만들지 않는다.

| 가격 구간 | zone_guide |
|---|---|
| 현재가 < MA20 | avoidance_priority |
| 현재가 ≥ MA20이며 현재가 ≥ MA5 | aggressive_review_possible |
| MA5 > 현재가 ≥ MA20 | conservative_review |
| 현재가 또는 MA5/20 계산 불가 | data_review_required |

MA20 아래를 먼저 검사하므로 역배열에서 MA5 위지만 MA20 아래인 종목도 회피 우선이다.
가격/기업행위 기준·캘린더·선정 근거·출처가 미확인인 경우 실제 review_status는 data_review_required다.
MA200 부족 등은 개별 unavailable과 위험 사유로 표시한다. MA5/20이 유효한 경우 기본 구간 가이드는 남길 수 있지만 전체 위험 목록과 함께 검토해야 한다.
order_signal=false와 position_size=null은 모든 결과에서 유지된다. 공격적 검토 가능은 기계적 매수나 고정 비중을 뜻하지 않는다.

## 고거래량 가격대 추정

최근 완료 세션 20봉에서 거래량이 큰 양수 봉 상위 5개를 표시한다. 이는 표시를 위한 휴리스틱이며 검증된 매매 임계값이 아니다.
각 봉의 low~high, close, volume, 대표값 (high+low+close)/3, 현재가와의 거리 및 원본 수치를 출력한다.
대표값이 현재가보다 높으면 estimated_overhead_supply 위험을 표시한다. 실제 체결 분포·보유자 원가·가격대별 거래량(VP)/VWAP을 복원했다고 주장하지 않는다.
20봉 거래량·비교 기준이 부족하면 추정 불가다. 음수·소수·비정상 포화 거래량을 재구성하지 않는다. 0 거래량도 검토 사유로 남기며 정지/매물 부재로 확정하지 않는다.
거래량만 잘못된 경우 해당 종가의 MA 사용 가능성과 거래량 추정 가능성을 분리한다.

## 데이터 품질·미래 누출 방지

- 타임존 없는 시각은 거부한다. trade_date와 market_at 날짜가 맞고 market_at ≤ finalized_at ≤ available_at ≤ as_of인 확정 봉만 사용한다.
- 당일 미완성 봉·늦게 수신/검토한 과거 봉·미래 거래일은 계산에서 제외한다. 날짜만 과거라고 당시 가용했던 자료로 보지 않는다.
- 수정주가/기업행위 근거의 effective_at/available_at도 as_of를 넘을 수 없다. 기준이 섞인 봉은 보류한다.
- 중복 날짜는 둘 다 격리하고 누락/잘못된 OHLC는 0 채움·가격 교환·평균값 대체를 하지 않는다.
- 미래 가격과 미래 선정 근거는 결과에 원문/수치까지 복제하지 않는다. 격리 목록에는 날짜·인덱스·사유만 기록한다.
- 정상 사용 봉의 정규화 OHLCV와 API 원본 숫자 문자열(부호 포함), page/row fingerprint·수신/검토 시각은 evidence에 남긴다. 원본 DailyPage도 변경하지 않는다.
- 현재가는 원래 observed_at/available_at과 경과 초를 출력한다. 오래된 관측을 실시간 가격으로 갱신하거나 새 값으로 추정하지 않는다.

## C14-B 뉴스 인터페이스

extensions.news_analysis는 현재 status=not_analyzed, report_reference=null, input_contract=arvis-news-analysis-v1이다. 뉴스 수집·요약·AI 호출·결합 판단은 구현하지 않았다.
다음 단계의 최소 별도 뉴스 결과는 stock_code, as_of, available_at, status, summary, risk_factors, evidence 목록 및 report_reference를 가진다.
각 evidence는 원본 출처·기사 식별자·published_at·received_at·판단에 사용한 사실을 보존해야 한다. 생성/모델 버전도 감사 metadata로 남길 수 있다.
결합 시 동일 종목/기준 시각을 확인하고 미래 공개/수신 뉴스는 제외한다. 뉴스 결과가 없는 상태를 긍정 뉴스로 해석하거나 차트의 데이터 보류를 뉴스로 해제하지 않는다.
이 뉴스 계약은 C14-B를 위한 확장 지점이며 이번 단계에서는 placeholder 외 뉴스 결과를 받거나 처리하지 않는다.

## 합성 검증 및 산출물

신규 차트/어댑터 합성 검사 22개 + 기존 관련 검사 60개 = 82개 PASS. 전체 회귀 테스트는 실행하지 않았다.
정배열/역배열, MA5 경계·이탈/MA20 유지·이탈, 고거래량 상방 구간, MA200 부족, 수정 기준 미확인, 거래량 기준 분리, 미완성 봉, 미래 시각/날짜, 누락·중복·OHLC·거래량 이상 및 원문 변조를 검증했다.
관련 범위는 analyst, 기존 daily_chart, AM_MOMENTUM, C12 후보 정책이다. 기존 파일은 수정하지 않았다.
reports/c14a에는 synthetic-input.json, synthetic-analysis.json, related.log, verification.json을 로컬 예제로 저장한다. 실데이터가 아니며 Git 제외 대상이다.
Commit/Push 및 전체 회귀 테스트는 보류했다. 다음 단계는 뉴스 계약 구현 또는 검토된 저장 일봉/가격/선정 근거로 읽기 전용 실데이터 검증이다.
