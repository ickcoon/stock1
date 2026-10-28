# Step 6-D 주도주 매매 적격성 검토

`kiwoom_collector.eligibility_dashboard`는 저장 JSON만 읽어 독립 HTML을 만든다. DB 연결·인증정보 로딩·OAuth/API·주문·스케줄 기능이 없다. 기존 Bubble Radar의 버블 면적·색상·섹터 순위·타임라인 계산을 그대로 사용한다. 기존 수집기와 분석 모듈은 수정하지 않는다.

## 화면

상단 주도섹터 TOP3는 기존 관심도 기준이다. 주가 상승이 확인됐다는 뜻은 아니다. 섹터별 TOP20 포함 수와 TOP5 핵심 종목을 표시하고 기존 버블/섹터 선택과 연결한다. TOP5 반복 출현을 우선 관찰 순서에 사용하며 점수 합산을 하지 않는다. 거래대금 필터 미달이어도 TOP5 카드를 숨기지 않는다. TOP20 나머지는 보조 관찰 펼침에 남긴다.

카드 기본 정보는 0198 순위·TOP5 반복·0186 순위·거래대금 억원·시총 억원·종합 상태다. 세부 펼침에는 TOP20 진입/유지, 섹터, 당일 등락률, mac/현재가/flo_stk 원문, 거래대금 원문, 기준별 3상태, 시총 구간, 동반 상승 확인, 보류 사유, API별 출처와 관측 시각이 있다. PC와 모바일 모두 색상 외에 텍스트를 표시한다. 모바일은 세부 정보를 기본 접는다.

## 판정

- 기존 `capital_filter.integrate`와 시총 구간 함수를 재사용한다. 명시된 외부 검토 증거가 특정 응답 fingerprint와 교차 context에 결속돼야 한다. JSON에 true를 입력하는 것만으로 증거의 진위를 자동 인증하지 않는다.
- 일반 주식 시총 3,000억원 이상, 검증된 장마감 누적 거래대금 1,000억원 이상, 0186 순위 40위 이내, 동반 상승 확인과 시점/시장/단위 근거가 모두 있어야 **검토 가능**이다.
- 신뢰된 숫자 기준이 false이면 **기준 미달**, 근거 누락·불일치가 있으면 **검증 보류**다. 장중 금액을 장마감 기준 미달로 해석하지 않는다. 거래대금 기준은 시총 미수집 때에도 독립 증거가 있으면 따로 표시한다.
- 시총 3,000억원 미만은 기본 하한 미달, 3,000억원~5조원은 핵심 탐색, 5조 초과~10조원은 확장 탐색, 10조 초과는 초대형 주도주다. 5조·10조 초과는 자동 제외하지 않는다.
- 확인된 구간과 미검증 참고 구간을 분리한다. 거래대금 원문 백만원은 Decimal로 억원 환산하고 mac 원문 억원도 보존한다. 가격×주식수 검산을 단위 검증으로 승격하지 않는다.
- ETF·ETN 등은 별도 유형 심사다. 유형 미확인도 일반 주식으로 추정하지 않는다. 모든 상태에서 candidate_confirmed=false, 자동 매수 승인·주문 기능 없음.

## 무결성 및 시점

mock/real 혼합 입력은 거절한다. 정확한 종목코드만 결합하고 중복 시총·원문 코드 불일치를 격리한다. 소스별 환경·시장·회차·테스트 구분·조회/수신 시각을 표시한다. 시장 조건이 없거나 다르면 검증 보류하며 시총 KRX와 다른 거래소의 결합도 보류한다. 불완전 페이지·잘못된 행이 있는 소스는 최종 심사로 승격하지 않는다.

새 패널은 기존 잠정 시차 300초에 더해 **선택 0198 관측 시각까지 응답이 완료된 동일 날짜의 금융 관측만** 표시한다. 예를 들어 15:00 선택 화면에 16:46 mac·조회시각은 표시하지 않는다. 원본 입력에는 남겨도 HTML의 해당 과거 프레임에는 넣지 않는다. 요청/수신 시각을 시장 기준시각·거래일로 자동 대입하지 않는다. 교차 자료가 결속되지 않은 과거 프레임은 관심도만 표시한다. 과거 회차에 다른 회차의 금융 자료를 자동 재사용하지 않는다.

이 보수적인 규칙 때문에 0198 직후 조회한 금융값도 그 0198 프레임에서는 숨겨질 수 있다. 늦게 수집한 값을 당시 확인된 사실처럼 보여주지 않기 위한 의도된 정책이다. 향후 별도 평가 시각을 지원하려면 출처와 시점 계약을 먼저 확장해야 한다.

## 무통신 실행

```powershell
.\.venv-home\Scripts\python.exe -B -m kiwoom_collector.eligibility_dashboard `
  --input-json data/step6d-dashboard-20261028/input.json `
  --capital-json data/step6d-dashboard-20261028/caps.json `
  --output data/step6d-dashboard-20261028/review-new.html
```

`--input-json`은 기존 Step 5-E JSON 계약이다. `--capital-json`은 기존 market_cap 파싱 결과의 목록 또는 단일 객체다. 선택 인자 `--proofs-json`은 기존 capital_filter의 코드별 검토 증거 계약이다. 실제 검토된 증거만 사용한다. 위 예시는 증거 없이 보류 상태를 검토한다. 합성 검증 true fixture는 실측 근거로 재사용하지 않는다.

입력은 `.json`만 허용하고 출력은 존재하지 않는 새 `.html`만 배타적으로 생성한다. 기본 DB나 기본 인증 경로는 없다. 자료가 없으면 미확인 상태다.

## 검증

독립 fixture `tests/fixtures/eligibility_dashboard_synthetic.json`은 요청한 시총/거래대금 경계·유형·코드·환경·미래값 사례를 포함한다. 추가로 시장/거래소/날짜/응답시각 누락·불완전 페이지·중복·입력 불변·기존 geometry/focus 보존·기존 파일 덮어쓰기 거부를 확인한다.

```powershell
.\.venv-home\Scripts\python.exe -B -m unittest discover -s tests -p test_eligibility_dashboard.py -v
```

headless 브라우저에서 1360×900과 390×844의 섹터 선택·세부 펼침·타임라인 전환·가로 넘침을 검사한다. 전체 회귀는 소스 사본, 임시 DB, 외부 Python 연결 및 원본 `.env`/data 접근 차단 환경에서 실행한다. 실제 병원 DB/0198·0186 데이터의 Step 6-D 검증은 하지 않는다.

## 2026-10-28 집 PC 최종 결과

- 전체 **591개 통과**, 실패·오류·건너뛰기 없음, 87.906초, 종료 코드 0. 기존 578개에 Step 6-D 독립 테스트 13개를 추가했다. 표 기반 fixture는 13개 하위 사례로 요청한 경계값·유형 사례를 포함한다.
- 1360×900 / 390×844 headless 브라우저에서 섹터 선택·세부 펼침·타임라인 전환·가로 넘침 차단을 통과했다. 두 해상도의 캡처를 직접 검토했다. 긴 섹터명 스트레스 사례도 브라우저 테스트에 포함한다.
- 저장 JSON만 받는 실제 `python -m kiwoom_collector.eligibility_dashboard` 진입점도 증거 미제공 보류 상태로 정상 실행했다.
- 격리 전체 회귀 중 `.env`·기존 DB와 sidecar·기존 CA PEM·client/collector/turnover_rank/schedule/scheduled_quotes/config 소스의 해시·크기·mtime 모두 불변이다. `git diff --check` 통과. 운영 DB 연결·추가 OAuth/API·스케줄 변경·커밋·push 없음.
- 회귀 로그·보존 증거: `data/step6d-final-20261028/unittest.log`, `verification.json`.
- 검토용 합성 화면: `data/step6d-dashboard-20261028/synthetic-review.html`. 합성 증거를 넣어 '검토 가능' 상태를 시험한 화면이며 실측 인증을 뜻하지 않는다. `unverified-review-new.html`은 증거 없이 보류하는 화면이다.
- 캡처: 같은 폴더의 `preview-1360.png`, `preview-390.png`, `preview-390-details.png`. 실제 데이터·장마감 단위/기준·시장 시각·섹터 동반 상승에 대한 추가 실측은 하지 않았다.

이번 변경 파일은 README.md, 이 문서, eligibility_dashboard.py, test_eligibility_dashboard.py, eligibility_dashboard_synthetic.json이다. 기존 HOME_* 보고서와 미추적 가상환경 백업은 보존했다.

## GitHub 보존 전 재검증

사용자 승인에 따라 기능 변경 없이 위 5개 파일만 보존 대상으로 선별했다. 격리 사본에서 전체 591개를 재실행해 모두 통과했다(87.540초, 실패·오류·건너뛰기 없음). Step 6-D 13개 테스트와 PC 1360×900 / 모바일 390×844 브라우저 검증이 포함된다. mock/real 혼합·미래 관측·시총 미확인·장중 금액·ETF/ETN 심사·candidate_confirmed=false 계약을 재확인했다.

원본 `.env`·운영 DB/sidecar·CA·수집 관련 소스의 해시·크기·수정 시각은 재검증 전후 동일하다. 추가 OAuth/API와 스케줄 변경은 없다. 인증정보·절대 로컬 경로·비공개 산출물 검사를 수행하고 staged 파일을 위 5개로 제한했다. 로그·해시 증거·로컬 경로·생성 HTML·실측 JSON·HOME_* 보고서는 커밋 대상에서 제외했다. 로컬 재검증 증거는 `data/step6d-preserve-20261028/`에 보존한다.
