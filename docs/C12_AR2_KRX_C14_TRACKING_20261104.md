# C12-AR-2 — KRX 일봉·기존 C14·D+2 추적·캘린더 조회 연결

작업일 2026-11-04 (KST). 브랜치 `integration/home-hospital-c12-20261103`. 이전 사용량 중단 시 변경을 유지하고 이어서 완료했다. Git Reset/Clean/Commit/Push/Merge는 실행하지 않았다.

## 1. 재개 상태와 C14 연결

중단 상태의 observed_daily.py 공통 계산부 추출 변경을 보존했다. context.py에도 252거래일 고점 계산을 공통 함수로 옮기는 미완성 변경이 남아 있었으며, 함수 내 `basis_ok/calendar` 미정의 참조를 수정해 마무리했다. 기존 C14 엄격 검증을 완화하거나 KRX를 키움 DailyPage/ka10081 응답으로 만들지 않았다.

KRX `daily_latest` → 출처별 검증 → 기존 `analyst-input-v1` 입력 및 공통 `analyze_observed_bars` → 기존 `analyze_context`, `chart_card`, `with_mini_chart`를 연결했다. 이동평균·이격률·거래량·저항 계산을 새로 만들지 않았다. 고점은 기존 **기준봉 이전 252거래일** 정의다. strict context는 수정주가/기업행위/최종성 미검증으로 unavailable을 유지하고, 관측값 계산을 별도의 provisional 필드로 제공한다.

실제 KRX SQLite 261거래일·722,666행은 그대로다. AR-1에서 연결한 33종목을 읽어 **33개 chart-card JSON**과 **32개 PNG**를 만들었다.

- 60거래일 미니 캔들차트 **30종목**.
- 부분 차트 **2종목**: `0158S0` 9봉, `0035S0` 22봉. MA5는 가능한 경우 계산하고 MA20/MA60과 고점은 부족 시 NULL/unavailable이다. 영문 포함 6자리 코드도 원문 유지.
- 차트 보류 **1종목**: `012340`. 최근 미거래/가격 미형성으로 유효 연속 봉이 없으며 0값 가격이나 임의 봉을 그리지 않았다.
- 252거래일 고점 잠정 산출 **26종목**, 미산출 7종목. 새 상장/부족/연속 이력 미충족을 건너뛰지 않았다.
- 실제 그림은 기존 MA5/MA20/MA60, 종가, 저항 후보 구간, 거래량을 재사용한다. 매수 확정이나 보유자 원가로 해석하지 않는다.
- 거래대금·시총은 환산하지 않았다. 추적 turnover_won/VWAP은 검증 근거가 없어 NULL이다.

**출력:** `diagnostics/c12-ar2/20261104-v1/charts.html`. 종목별 `input.json`, `strict_context.json`, `observed.json`, `card.json`, `excluded.json`, `mini.png`. card schema는 기존 `arvis-chart-card-v1`이며 기존 fingerprint 계약을 유지한다. 전체 요약은 `summary.json`.

## 2. 시각과 미래 데이터 차단

- 거래일 = KRX day/BAS_DD, 원본 이용 가능 시각 = 실제 `collected_at`, 분석 기준 = `as_of`, 어댑터 일괄 입력 시각 = `materialized_at`을 분리했다.
- **실제 수신 시각 ≤ 분석시각**, **거래일 < 분석일** 조건을 모두 충족해야 한다. 수신 시각이 없거나 당일 16:00 보수 경계 이전에 온 일별 자료는 보류한다. 파일 mtime을 당시 이용 가능 시각으로 사용하지 않는다.
- 이 단계의 차트 기준시각은 `2026-11-04T14:11:19.159939+09:00`이다. 같은 KRX 자료를 2026-10-26 09:50에 넣은 검증에서는 과거 수신 증거가 없으므로 입력 봉/MA/고점을 모두 비워 두었다. 백필된 과거 봉의 거래일이 오래됐다는 이유로 당시 오전에 알았다고 가정하지 않았다.
- 16:00은 어댑터의 보수적인 일별 경계이며 공식 체결/공표 타임스탬프가 아니다. 원본 수집 시각은 수정하지 않았다.
- 원문 가격 수정주가/기업행위·거래량 비교 기준 미확정은 `price_basis_verified=false`, provisional 및 출처 KRX로 전파한다. strict C14 분석 결과를 verified로 승격하지 않는다.

## 3. D0~D+2 추적 결과

기존 **leader_tracking.py를 수정하지 않고** Snapshot/Observation/analyze/명시적 거래일 캘린더를 재사용했다. 실제 생산 D0 스냅샷은 알려진 체크아웃/보고서 검색에서 발견되지 않았다(`d0-inventory.json=[]`). 따라서 새 실전 D0 선정 기록을 만들지 않았다.

실제 KRX 가격 검증에는 **선정이 아닌 진단용 기준일** 2026-10-23을 사용했다. `diagnostic_anchor_not_selection`, candidate_confirmed=false, order_signal=false로 분리했다. 33개 진단 중 **32개가 D+1/D+2 OHLCV를 완성**, 1개는 가격 미형성 자료로 미수신/부족 상태다. 이는 실전 선정 성과 32건이라는 뜻이 아니다.

- D0 **10/23 금요일 → D+1 10/26 월요일 → D+2 10/27 화요일**.
- 양 시장 비어 있지 않은 정상 스냅샷 및 원문-정규화 건수가 일치하는 **261개 OPEN 인덱스**를 검증했다. 주말만 명시적 폐장으로 처리하고, 빈 응답 평일 22일은 **UNKNOWN**으로 남겼다. 이런 미확정 평일이나 범위 밖을 만나면 calendar_unknown으로 멈추며 임의로 다음 수집일까지 점프하지 않는다. 공식 휴장 달력으로 가장하지 않았다.
- 원본 일봉 수신 시각을 보존하되 추적 이벤트는 **실제 어댑터 일괄 생성 시각과 원본 수신 시각 중 늦은 시각** 이후에만 이용 가능하다. 늦게 수집된 D0 봉은 별도 이벤트이며 기존 Snapshot을 덮어쓰지 않는다.
- 기존 추적 엔진의 D0 종가 대비 변화율·고가 돌파·저가 이탈·거래량 변화·MA3/MA5 근접도를 그대로 계산했다. 수정주가 미확정인 진단 패턴은 잠정 참고이며 주문 신호가 아니다.
- 정식 Snapshot의 기본 연결은 가격/거래량·D0 호환성 검증 증거가 없으면 **held**다. 검증 메타데이터는 episode_id/basis_id/evidence_reference/verified_at 및 비교 가능 플래그, 기존 D0 봉이 있으면 해당 fingerprint에 결합돼야 한다. 진단 모드로 정식 선정 Snapshot을 우회하는 것은 거절한다.
- 과거 오전 추적 33건은 이벤트 0건·가격 metrics 없음으로 검증했다. D0 payload는 합성/진단 테스트에서 전후 동일하다. 생산 D0 원본이 없으므로 생산 에피소드 연결 성공으로 보고하지 않는다.

실제 가격을 사용한 진단 예: `396300`. 아래 값은 잠정·회고 진단이며 선정 성과가 아니다.

| 구간 | 거래일 | O/H/L/C | D0 종가 대비 % | 거래량 | D0 거래량 대비 % |
|---|---|---|---:|---:|---:|
| D+1 | 2026-10-26 | 9470 / 10080 / 9320 / 9360 | 1.628664 | 7613036 | 19.886253 |
| D+2 | 2026-10-27 | 9550 / 9930 / 9280 / 9530 | 3.474484 | 4025756 | -36.604424 |

파일: `diagnostic-anchors.json`, `diagnostic-tracking.json`, `morning-blocked-tracking.json`, `morning-blocked-chart.json`.

## 4. 캘린더 조회 인터페이스

`leader_calendar.query_day(day, loaded, as_of=..., materialized_at=..., arvis_records=..., captures=..., episodes=...)`를 제공한다. 기존 external_theme_master.calendar_day / c12_external_theme_bridge.calendar를 재사용했다. 새 캘린더 전체 UI는 만들지 않았다.

- `external_trader_close`: 원문 외부 장마감 테마/종목/전사 출처. 자동 ARVIS 선정으로 바꾸지 않는다.
- `arvis_selection`: 실제 입력된 선정 기록만. 당시 관측시각/명시적 가용 시각 및 기존 슬롯 계약을 검증한다.
- `arvis_capture`: 저장된 C12 포착 관측. 실제 입력은 mock 0198 관측이며 완료·선정 상태를 미확정으로 보존했다.
- `external_theme_repetitions`: 원문 테마별 등장 날짜·횟수. 유사 테마명을 합치거나 반복 등장만으로 주도 확정하지 않는다.
- `tracking`: D0/D+1/D+2의 기존 엔진 결과. 결과 as_of가 조회시각 이후이면 제외한다. diagnostic 여부를 유지한다.
- 원본에 과거 이용 가능 시각이 없으면 최초 어댑터 읽기 시각으로 보수 처리한다. 장마감 기록은 당일 09:50에 노출하지 않고, 빈/미확보 슬롯을 “후보 없음”으로 해석하지 않는다.

실제 원본 DB로 4개 조회 예제를 저장했다(`calendar-queries.json`). C12 포착일 10/20, 진단 D0 10/23, 외부 최신 기록일 2026-11-02, 그 외부 기록일의 오전 09:50을 구분했다. 오전 조회에는 외부 종목/테마/미래 추적 결과가 없다. 실제 ARVIS 선정 수는 **0**으로 유지했다.

## 5. 122630 ETF 검토

`122630`은 **KODEX 레버리지 ETF**, 표준코드 **KR7122630007**로 확인했다. [KRX KIND 상품개요](https://kind.krx.co.kr/disclosure/etfisudetail.do?method=searchEtfIsuSummary&strIsurCd=12263), [삼성자산운용 공식 상품 자료](https://m.samsungfund.com/sheet/20230215/2ETF25_20230131.pdf).

별도 보강 참조에서 `official_supplemental_product_match / ETF / excluded`로 기록하고 기존 equity_eligibility.decide의 ETF 제외 정책을 재사용했다. 따라서 **단순 코드 미매칭**과 **식별됐으나 정책상 제외**를 구분한다. AR-1 원문/기존 주식 마스터를 덮어쓰지 않았고 관심도 관측은 남긴다. `122630-product-review.json`에 공식 출처와 검토일이 있다. 이 종목의 차트를 만들기 위해 별도 API를 호출하지 않았다.

## 6. 테스트

- **합성 15개 + 기존 관련 회귀 142개 = 157개 통과**, 실패/오류 0. 기존 C14 chart/context/card/observed/mini/hybrid, leader_tracking/tracking_review/C12 차트 연결을 포함한다.
- **실제 SQLite 검증 8개 통과**: 모든 종목 입력 OHLCV와 수신 시각·코드 직접 대조, 카드 schema/fingerprint, MA 독립 합산, 252고점 직접 비교, PNG 32개 검증, D+ 날짜/가격/돌파/이탈/변화율 독립 계산, 과거 오전 차단, 외부 캘린더 출처/시각, ETF 및 원본 해시 보호.
- **브라우저 검사 4개 통과**: 33개 카드/32개 실제 PNG/보류 안내, 카드 JSON 링크와 미검증 상태, PC·390px 모바일 무가로넘침, JS 오류·외부 요청 0. 대표 차트와 화면 PNG도 시각 확인했다.
- 중단 상태 수정 직후 기존 C14 테스트 25개가 통과했고, 최종 관련 회귀로 확장해 157개를 통과했다. 전체 저장소 회귀는 실행하지 않았다.
- 합성 자료는 입력/증거 검증용이며 실제 가격 검증과 구분했다. 실제 가격 추적도 진단 Anchor라는 점을 구분했다.

## 7. 변경 파일·보호

기존 C14 수정(3개):

- `analyst/observed_daily.py`: 중단된 공통 잠정 계산부 추출 유지 및 연결.
- `analyst/context.py`: 기존 252거래일 고점 계산을 공통 함수로 분리, 중단 상태 미완성 참조 수정. 기존 엄격 검증 게이트 유지.
- `analyst/card.py`: 기존 카드의 unavailable 고점에 한해 출처 명시 잠정 고점을 표현. verified로 승격하지 않음.

신규: `kiwoom_collector/krx_c14.py`, `kiwoom_collector/leader_calendar.py`, `tests/test_krx_c14.py`, `tests/test_leader_calendar.py`, `tests/test_krx_c14_actual.py`, 본 문서. 개발 검증/출력은 `diagnostics/c12-ar2`에 있다.

기존 C14 위 3개 외 보호 대상 **112개 파일** 해시가 모두 같다. KRX 원본 1.88GB, 공식 마스터, 체크포인트, AQ DB, 키움 저장 DB, 원본 테마 DB의 해시를 확인했다. leader_tracking.py는 변경하지 않았다. 병원 Collector, 운영 SQLite, Telegram 설정, 인증키/토큰, 기존 D0 스냅샷에 쓰지 않았다. KRX API 호출/생산 D0 쓰기/주문/발송 **0회**. 금지 Git 작업 없음.

## 8. 미완료와 다음 단계

연결 인터페이스와 독립 검증 결과는 다음 개발에 사용할 수 있다. 실전 대시보드에 **읽기 전용·잠정 상태로 연결하는 개발 착수 가능**하다. 생산 적용 완료로 보지는 않는다.

남은 실제 근거: 생산 D0 선정 스냅샷/동시점 이용 가능 증거, KRX와 D0 원본 가격·거래량 비교 기준 검증, 공식 휴장일/미확정 평일 대조, 수정주가·기업행위 기준. 확인 전 정식 D0 성과를 생성하거나 오전 당시의 실제 주도주 성과로 평가하지 않는다. 추가 API 수집·운영 자동 통합은 수행하지 않았다.
