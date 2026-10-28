# Step 7-B1 — 독립 일봉 파서와 추적 어댑터

[공식 명세 조사 및 Step 7-B2 최소 호출 절차](DAILY_CHART_API_RESEARCH.md)를 함께 참조한다. 이 구현은 네트워크·OAuth·DB·주문 클라이언트를 제공하지 않는다. 기존 수집기와 Step 7-A 엔진도 수정하지 않는다.

## 구성

- `kiwoom_collector/daily_chart.py`: 불변 DailyRequest, DailyRow, DailyPage와 저장 응답 파서.
- `kiwoom_collector/daily_tracking_adapter.py`: 외부 근거 ClosingEvidence 및 Step 7-A Observation 변환.
- `tests/fixtures/daily_chart_synthetic.json`: 공식 필드 형식에 맞춘 합성 자료. 실측 응답·공식 예제 수치를 그대로 저장한 자료가 아니다. 테스트 캘린더의 휴장 설정도 합성이다.
- `tests/test_daily_chart.py`: 파싱·보류·불변성·D+1/D+2 연결 테스트.

## 오프라인 입력

```python
from kiwoom_collector.daily_chart import DailyRequest, parse_response

request = DailyRequest('ka11081', '005930', 'mock', '20261028', '0')
# saved_body와 아래 메타데이터는 실제로 보존한 자료에서 가져온다.
page = parse_response(
    saved_body, request=request, response_environment='mock',
    request_started_at=saved_request_started_at,
    response_received_at=saved_response_received_at,
    source=saved_source_identifier,
    http_status=200, cont_yn=saved_cont_yn, next_key=saved_next_key,
    response_api_id='ka11081',
)
```

종목코드와 환경은 명시적 입력이며 원본 코드 문자열과 시장 접미사를 보존한다. 요청 오류, HTTP/API 오류, 응답 코드·환경·API 불일치, 잘못된 배열 구조는 ValueError로 중단한다. 개별 필드 이상은 row.issues에, 날짜 중복·정렬·연속조회 이상은 page.issues에 남긴다. 원문은 page.raw 및 row.raw로 복사 조회할 수 있으며 frozen 모델 내부를 수정하지 않는다.

부분 가격·거래량·거래대금은 None이다. 부호 가격은 copy_abs 기반 Decimal로 바꾸고 원본 문자열을 남긴다. 부호가 두 번인 가격·NaN·Infinity·숫자 아닌 값은 거부한다. 0 가격은 미확인이며, 고가/저가 자동 교환·거래량 추정·현재가를 확정 종가로 추정하는 처리는 없다. 백만원→원 환산은 충분한 Decimal 정밀도에서 계산한다. 환산은 단위 변환이며 원 단위 원본 정밀도 검증이 아니다.

요청 adjustment 0/1과 행 raw의 upd_stkpc_tp 사건 구분자를 혼동하지 않는다. 원본 upd_rt 및 추가 응답 필드도 보존한다. 파서는 최신 날짜 우선 순서를 유지한다. 오름차순·혼합 정렬은 격리하고 날짜를 조용히 재정렬하지 않는다. 빈 배열은 빈 자료이며 휴장·정지·추적 완료가 아니다.

## 추적 연결의 검증 관문

`to_tracking(pages, snapshot, calendar=..., as_of=..., expected_session_basis=..., evidence=...)`는 Conversion을 반환한다.

- observations: 검증된 불변 Step 7-A Observation tuple.
- holds: `(page_fingerprint, row_fingerprint, reasons)` tuple. 보류된 원문은 입력 DailyPage에 유지된다.

ClosingEvidence는 **외부에서 검토한 실제 근거**의 전달 모델이다. 엔진이 근거의 진위를 인증하지 않는다. 단순히 날짜가 과거라는 이유로 status=final을 생성하면 안 된다. 테스트에서만 SYNTHETIC 증거를 사용한다.

어댑터는 fingerprint를 신뢰하기 전에 기존 파서로 저장 원문을 다시 파싱하고 현재 DailyPage 전체와 비교한다. dataclasses.replace()로 가격·날짜·거래량·환산값을 바꾸거나 검증 사유를 지운 복사 모델은 ValueError로 거부한다. 별도 검증 프레임워크나 새로운 fingerprint 체계는 추가하지 않는다.

| 관문 | 요구사항 |
| --- | --- |
| 동일성 | snapshot과 코드·환경·시장 일치. 여러 페이지는 API·기준일·수정주가 설정도 일치 |
| 중복·정렬 | 동일 날짜 중복은 해당 날짜를 격리. 페이지 자체 중복/정렬 이상은 그 페이지 전체 격리. 페이지 간 순서 이상은 입력 전체 격리 |
| 거래일 | 캘린더가 해당 날짜를 거래일로 확인. 주말·휴장·미확인 날짜는 추정 금지 |
| 미래 | 응답은 as_of 이전에 수신. 날짜는 base_dt 및 수신일 이내 |
| 증거 결속 | 정확한 page fingerprint + row fingerprint + D0 episode_id 및 비어 있지 않은 근거 출처 |
| 마감 | status=final 또는 명시적 halted. 불완성·미확인 봉은 보류 |
| 시간 | market_at은 행의 거래일이며 요청 시작 이전. verified_at은 실제 응답 수신 이후, as_of 이전. 명세로 확인되지 않은 마감 시각을 고정하지 않음 |
| 단위 | 근거의 KRW / share / million_KRW가 모두 확인되어야 함. 문서 단위만으로 runtime 검증을 자동 통과하지 않음 |
| 세션 | ka10081은 chart_session, ka11081은 regular_session. 호출자가 명시한 expected_session_basis와도 일치. 정확한 집계 범위 검증은 근거 제공자의 책임 |
| D0 비교 기준 | 요청 0이고 원주가 기준과 D0 가격·거래량·시장·세션 기준 호환성이 확인됨. 요청 1은 근거가 있어도 이 어댑터에서는 보류 |

OHLCV 누락·오류는 확정 관측으로 넘기지 않는다. 거래대금이 없는 경우에도 단위 근거가 확인되고 OHLCV가 온전하면 turnover_won=None인 관측은 허용한다. 거래대금 오기·음수 등은 단순 누락과 달리 보류한다. VWAP 분자·분모는 제공하지 않으므로 항상 미확인이다.

halted는 별도 명시적 근거가 있어야 한다. 이 경우 placeholder 0 가격과 누락 OHLCV는 최종 가격으로 넘기지 않고 전부 None으로 보존한 halted 관측을 만든다. 데이터 자체의 구조 오류·단위/시장 불일치까지 무시하지 않는다. 빈 응답·0 거래량만으로 halted를 생성하지 않는다.

실제 전달 Observation.available_at은 증거 검증 시각이다. 따라서 늦게 조회한 과거 일봉이나 늦게 검증된 행이 D0 당시·D+1 당시 자료로 소급 노출되지 않는다. raw_json에는 API·요청·원문 행·단위·수정 설정·세션·수신 시각·근거 시각·fingerprint·페이지 완결 상태가 보존된다. 이는 새 관측 추가이며 D0 스냅샷 작성·변경을 수행하지 않는다.

`cont-yn=Y` 또는 미확인이라도 현재 페이지에서 확인된 행 자체는 근거를 충족하면 사용할 수 있다. 이것은 전체 이력 완결을 의미하지 않는다. 원본 응답 커서의 전달·페이지 체인 인증·자동 연속조회는 구현하지 않았으며, 외부 호출자가 페이지 묶음의 실제 연속성을 검증해야 한다. 여러 페이지의 날짜 겹침과 순서만 방어적으로 검사한다.

## 검증

```powershell
python -m unittest discover -s tests -p test_daily_chart.py -v
python -m unittest discover -s tests
```

외부 API 요청 없이 공식 형식 기반 합성 fixture로 수행한다. 실제 가격·거래량·거래대금 원천, 세션 집계 범위와 확정·정정 시각은 Step 7-B2 검증 대상으로 남긴다.

2026-10-28 KST 최초 검증 결과: 신규 25개 및 기존 614개를 합한 639개 통과. 최종 보완에는 원문과 복사 모델의 불일치 차단 및 정상 복사 허용 테스트 5개를 추가했다. 기존 Step 7-A·Radar·Bubble·Step 6-D 회귀 테스트를 포함하며 실제 키움 API는 호출하지 않는다.

최종 전체 회귀 결과: **644개 통과, 실패 0·오류 0·스킵 0, 89.975초, 종료 코드 0**. 일봉 관련 30개 테스트를 포함한다. 기존 Git 제외 규칙을 유지하면서 가상환경 백업·인증서·주요 토큰 캐시 파일의 제외 규칙을 추가했다.
