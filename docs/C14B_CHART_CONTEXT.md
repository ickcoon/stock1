# C14-B 완료봉 추세·252거래일 신고가 분석

```python
from kiwoom_collector.analyst import analyze_stock_context
report = analyze_stock_context(saved_analyst_input, candidate_context=c12_envelope)
```

입력은 기존 `analyst-input-v1`이며 `from_daily_pages`도 그대로 재사용한다. API·DB 접근 없이 전달된 자료만 계산한다. 기존 `analyze_stock`과 C14-A 출력 계약은 변경하지 않았다.

출력 `arvis-chart-context-v1`의 주요 필드:

| 필드 | 계약 |
|---|---|
| stock_code/as_of/environment/market/data_origin | 코드·기준 시각·서버·거래소·합성 여부 유지 |
| analysis | 기존 C14-A 결과 전체, 원본 관측 가격과 검증 근거 포함 |
| reference_close | 검증된 캘린더의 마지막 완료 세션 종가·출처·수신 시각. 현재가와 구분 |
| moving_averages/close_comparisons | MA3/5/20/60/200과 종가 above/equal/below. 부족하면 null |
| close_short_disparity | MA3/5의 종가÷MA×100 및 (종가÷MA−1)×100 |
| high52 | 기준 완료봉 **이전 252개 완료 세션**의 high 최댓값·동일 고점 날짜·창 범위·종가 돌파 여부 |
| supply_proxy | 최근 20봉 중 상위 5개 양의 거래량 봉의 OHLC 대표가격·가격 범위·종가 대비 거리 |
| high_vs_supply_distance | 같은 종가를 분모로 고점과 추정 매물대 중 어디가 더 가까운지 비교 |
| supporting_factors | MA20>MA60, 종가>MA200, 종가>직전252봉고점의 개별 가산 근거. 결측은 null |
| candidate_context | 아래 C12 envelope 원형 유지. 기존 후보·순위·제외 판정 변경 없음 |
| coverage | complete/partial와 계산 불가 항목 |
| explanation | 추세 양호·저항 주의·매매 보수·매매 회피 후보·적극 관찰 가능·데이터 검토 필요 |
| order_signal/position_size | 항상 false/null. 자동 주문 없음 |

`candidate_context`는 선택 입력이다. C12 결과의 decision을 읽기 전용으로 감싼다:

```json
{
  "stock_code": "005930", "environment": "mock", "market": "KRX",
  "data_origin": "synthetic_fixture", "available_at": "2026-10-28T10:00:00+09:00",
  "source": "SYNTHETIC C12",
  "decision": {"state": "analysis_candidate", "reasons": ["synthetic_review"]}
}
```

코드·환경·시장·출처 종류가 분석 입력과 일치하고 source와 기준 시각 이전 available_at이 있어야 한다. state는 analysis_candidate/confirmed_excluded/provisional_excluded/pending/unmatched 중 하나다. 조건 불일치는 오류로 중단한다. 이 envelope를 실제 C12 엔진에 자동 연결하거나 DB에 저장하지 않는다.

종가<MA20이면 회피 경고가 최우선이다. MA20 이상·MA5 미만이면 보수적 관찰, MA5 이상이면 적극 관찰 가능이다. 종가 위의 추정 매물대가 있으면 저항 주의, MA20>MA60과 종가>MA200이면 추세 양호로 설명한다. 근거·선정 출처가 미검증이면 데이터 검토 필요이며, 부분 자료는 coverage와 개별 null 값에 표시한다. 가산점 숫자·근접 기준·과열 임계값·후보 순위 변경은 임의 도입하지 않았다.

252세션 기준 고점은 달력상 52주/364일 고점 및 API의 250일 고점과 다르다. 이번 사용자 요청의 **252거래일 근사 정의**를 사용한다. 기준 종가 돌파 확인에는 최소 253개 완료 세션이 필요하다. 기준봉은 고점 창에 넣지 않으며, 종가=고점은 재접촉이지 돌파가 아니다.

`distance_below_high_percent=(고점−종가)/고점×100`, `rise_to_high_percent=(고점/종가−1)×100`이다. 돌파 후 음수는 그대로 보존한다. 추정 매물대 거리는 `(대표가격/종가−1)×100`이며 거리 순서 비교에는 rise_to_high_percent를 쓴다. 임의의 가까움/멀음 임계값 대신 숫자와 상대 순서를 제공한다.

필요한 세션·봉·가격 비교 근거·거래량이 부족하거나 미래 수신·중복·미완료·잘못된 OHLC가 있으면 해당 계산을 unavailable/null로 표시한다. 누락 봉을 이전 봉으로 대체하지 않고 봉을 생성하지 않는다. 기준봉이 없으면 과거 종가로 대체하지 않는다. 수정주가·기업행위·거래량 조정 비교 근거는 기존 C14-A 검증을 따른다.

매물대는 일봉 OHLCV 기반 잠재 저항 **추정**이다. 가격별 실제 체결 분포·보유자 원가·매도 잔량이 아니다. 뉴스·조건검색·스케줄·52주 가산점 최적화·실제 주문은 이번 구현 범위가 아니다.
