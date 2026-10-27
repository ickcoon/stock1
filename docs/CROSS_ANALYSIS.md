# Step 5-D — 0198 × 0186 교차 분석

`kiwoom_collector.cross_analysis`는 JSON 스냅샷을 비교하는 순수 분석 모듈이다. DB 연결, OAuth, 네트워크, 키 로딩, 주문, 기존 수집 엔진 호출은 없다. 기존 Radar/Bubble의 TOP20 섹터 탐지 기능은 유지하고 종목별 교차 검토 근거를 추가한다.

## 입력 계약

예제: `tests/fixtures/cross_analysis_synthetic.json`. 실제 응답이 아닌 합성 자료이며 검증 완료를 뜻하지 않는다.

- `ranking`: id, api_id=ka00198, environment, boolean test_mode, collected_at(타임존 필수), status, qry_tp, market, rows. rows는 stk_cd/stk_nm/rank를 가진 TOP20 스냅샷이며 순위는 1~20으로 제한한다.
- `turnover`: id, api_id=ka10032, environment, test_mode, collected_at, status, market, rows. rows는 API의 stk_cd/stk_nm/now_rank/trde_prica/cur_prc/flu_rt/now_trde_qty 원문 필드다. 기존 독립 turnover DB의 raw_json을 이 계약으로 변환할 수 있지만 이번 모듈에는 DB 로더를 넣지 않았다.
- `market`: mrkt_tp/mang_stk_incls/stex_tp. 두 소스에서 실제로 확인된 조건을 넣는다. ka00198의 조건을 다른 API 요청에서 추측해 채우지 않는다. 모르면 market=null로 두어 검증 보류시킨다. 모의 비교는 KRX만 허용한다.
- `history`: 선택한 ranking보다 앞선 관측. 같은 환경·테스트 구분·KST 날짜·시장 조건·qry_tp이며 성공한 유효 회차만 반복 횟수에 포함한다. 미래·다른 날짜·실패·중복 ID·중복 종목/순위 회차는 제외하고 이유를 기록한다. 현재 성공 회차를 포함한 TOP5 출현 횟수이며 분모는 실제 채택한 성공 회차 수다.
- `securities`: 정확한 6자리 코드별 sectors 목록과 security_type. common_stock, preferred_stock, ETF, ETN, SPAC, REIT, other, unknown을 구분한다. 이름으로 유형이나 섹터를 추측하지 않는다. 다중 태그는 합치지 않고 그대로 보존한다. 현재 태그를 과거 관측에도 적용하므로 과거 분류가 검증된 것은 아니다.

코드는 숫자로 변환·0 채우기·접미어 제거·종목명 매칭하지 않는다. `005930`과 `5930`은 같은 종목으로 자동 취급하지 않는다. 누락 금액은 0으로 바꾸지 않고 0186 표본 밖 종목을 저유동성으로 판단하지 않는다. 중복 코드는 합산·첫 행 선택 없이 제외하고 별도 진단에 남긴다. 중복 순위도 모호한 자료로 보류한다.

## 검증 증거와 판정

`verification`은 외부에서 검증한 근거를 받는 계약이며 분석 엔진이 스스로 검증을 만들어내지 않는다. ranking_id/turnover_id/environment가 현재 쌍과 일치해야 한다. time/unit/coverage/quotes 각각 `verified=true`와 비어 있지 않은 evidence가 있어야 한다. unit은 million_KRW, coverage는 검증 범위(scope)를 명시한다. time에는 현재 두 collected_at 값도 일치하게 기록해야 한다. 이 evidence 문자열은 신뢰할 수 있는 별도 검토 절차에서 공급해야 하며 문자열이 있다는 것만으로 그 근거의 진위를 인증하는 기능은 아니다.

수집 시각 차이는 **0186 수집시각 − 0198 수집시각**의 부호 있는 초다. 같은 시각이거나 몇 초 이내라는 이유로 정합성을 추론하지 않으며 임의 허용 간격을 두지 않는다. 같은 KST 날짜이고 특정 스냅샷 쌍에 결합된 외부 정합성 근거가 있어야 time 검증을 통과한다. 수집 시각은 시장 데이터의 기준 시각을 대신하지 않는다.

거래대금 공식 선언 단위는 백만원이며 ×1,000,000의 Decimal 원 환산은 진단값으로만 표시한다. 단위 미검증 상태에서 환산값을 검증된 거래대금으로 승격하지 않는다. 현재가 원문 부호와 선행 0, 등락률 %, 거래량 주를 보존하며 임의 절댓값 변환은 하지 않는다. 실측 시세·부호·시점 확인은 별도 quotes 근거로 요구한다. coverage_scope가 일부 교집합이라면 시장 전체 검증을 의미하지 않는다.

판정은 다음과 같다.

| 구분 | 의미 |
| --- | --- |
| 검증 보류 | 시간·단위·커버리지·시세·시장·자료 완전성 중 하나라도 미확인, 연결 누락, 중복/잘못된 값, 미분류 또는 유형 미확인/별도 유형 검토 필요 |
| 후보 검토 | 위 교차 검증 근거가 모두 있고 TOP5에 속한 일반 주식의 우선 검토. 자동 매매 후보 아님 |
| 관찰 | 위 근거가 모두 있고 TOP5 밖의 TOP20 일반 주식. 섹터 탐지 표본에서 유지 |

어느 경우에도 candidate_confirmed=false다. 거래대금/상승률/반복 횟수 합성 점수, 가중치, 시장 상위권 임의 경계, 주문 규모 판단, 매수 신호·주문 기능은 없다. ETF/ETN 등은 별도 유형 검토 대상으로 보류한다. 이번 합성 샘플은 근거가 없어 모두 보류이며 실제 거래대금·시장 일치 검증을 완료했다고 주장하지 않는다.

## 실행

```powershell
python -B -m kiwoom_collector.cross_analysis --fixture tests/fixtures/cross_analysis_synthetic.json --format markdown
python -B -m kiwoom_collector.cross_analysis --fixture tests/fixtures/cross_analysis_synthetic.json --format json
```

실행 가능한 프로젝트 Python을 사용한다. 이 작업에서 검증한 작업용 Python은 `C:/Users/Public/Documents/ESTsoft/CreatorTemp/stock1-python-3.11.9/python.exe`다. 기본 출력은 stdout이며 입력은 수정하지 않는다. JSON 이외 확장자는 읽기 전에 거부한다. Python API `analyze(document)`는 메모리 자료만 받고 입력을 변경하지 않는다.

## 향후 Radar 연결

1. 별도 승인된 내보내기 과정에서 기존 Radar의 선택 회차 TOP20/동일 범위 성공 이력/현재 섹터 메타데이터와 독립 ka10032 테스트 결과를 JSON 계약으로 만든다. 이번에는 운영 DB 내보내기나 DB 직접 연결을 하지 않는다.
2. 실제로 확인된 각 API 조회 조건, 스냅샷 ID, 환경, 운영/테스트 구분, 원문 수집 시각을 보존한다. 모의와 실전을 혼합하지 않는다.
3. `analyze`의 rows를 종목코드로 기존 상세 패널에 연결한다. 기존 TOP20·TOP5 필터, Bubble 면적/색상/정렬, 섹터 TOP3 로직은 변경하지 않는다.
4. 단위·커버리지·시점 근거, 시차, 보류 사유를 함께 표시한다. 후보 검토를 매매 후보 확정으로 표시하지 않는다. 향후 진짜 후보 분류에는 별도 시세/유동성/주문 규모 검증 설계가 필요하다.

테스트는 순수 fixture 및 기존 테스트의 임시 DB만 사용한다. 이번 전체 회귀는 소스 복사본에서 운영 DB/환경 파일 접근과 외부 네트워크를 차단해 수행한다. 브라우저 UI 회귀에 필요한 localhost 통신만 허용한다.
