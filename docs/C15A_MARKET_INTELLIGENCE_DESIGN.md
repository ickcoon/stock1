# C15-A — ARVIS Market Intelligence 최소 설계

조사 기준: 2026-10-31 KST. 공개 공식 문서와 개발 사본만 조사했다. 실제 API/OAuth 호출, 인증정보 사용, DB 연결, 운영 폴더·수집기·스케줄러 변경, 테스트, Commit/Push는 수행하지 않았다. 문서에 적은 수집·실행 주기는 제안이며 활성화하지 않는다.

## 1. 결론과 기존 기능 재사용

국내 시장 폭 후속 조사: [C15 국내 시장 폭·ADR 상세 설계](C15_DOMESTIC_BREADTH_ADR_DESIGN.md). 키움 ka20001/ka20003/ka20009에 직접 건수 필드가 확인되었으며, 원본 시장 통계와 일반주 재집계 계열을 구분한다. ADR5/10/20 설계 및 모집단·상하한가 검증 관문은 후속 문서를 따른다.

시장 시황은 `market_intelligence` 독립 실행으로 만들고, 0198/0186 및 AM_MOMENTUM의 입력·점수·선정 결과를 변경하지 않는다. 먼저 이용권한이 확보된 자료의 오프라인 입력과 공통 JSON/템플릿을 구현한다. 모든 소스의 자동 수집이 가능하다고 전제하지 않는다.

조사한 개발 사본: `C:\Users\Public\Documents\ESTsoft\CreatorTemp\stock1-c14-dev`, HEAD `7d200cff1b29fb18a8982f9f571c0c9624e0fcf1`. 기존 C14-A 코드·테스트·설계 미추적 파일을 보존했다. 운영 stock1은 조사·수정 대상으로 삼지 않았다.

| 기존 기능 | 재사용 범위와 한계 |
|---|---|
| `stock_catalog.SECTORS`, `sector_taxonomy` | 기존 15개 명칭과 보수적 연결 정책. 글로벌 섹터 연결은 별도 근거이며 개별 국내 기업의 confirmed 분류를 바꾸지 않는다. |
| C12 후보 필터, AM_MOMENTUM | 그대로 독립 유지. 장전 관심 섹터를 실제 수급 확인 또는 주도주 확정으로 취급하지 않는다. |
| `analyst/chart.py`, `daily_adapter.py` | 종목 차트 결과를 보고서 참조로 결합 가능. 시장 시황 모듈에서 MA·매물대 계산을 재구현하지 않는다. |
| `analyst`의 `arvis-news-analysis-v1` 확장 자리 | 현재 뉴스 분석은 placeholder다. 기존 뉴스 수집기가 완성됐다고 가정하지 않는다. C14-B와 C15가 사용할 공통 뉴스 입력을 한 번만 설계한다. |
| `am_briefing`, `telegram_briefing` | 결과 fingerprint, 한국어 템플릿, 안전한 렌더링·전송 중복 방지 구조 참고. 기존 Telegram 스키마 검증기는 AM 보고서 전용이므로 새 validator/adapter가 필요하다. 기존 허용 필드를 느슨하게 늘리지 않는다. |
| C13 TokenManager | 키움 인증이 필요한 기존 경로만 유지. KRX·뉴스 사업자 인증을 키움 토큰 캐시에 섞지 않는다. 이번 설계는 키움 호출을 요구하지 않는다. |

기존 15개 섹터: 반도체, 자동차, 로봇, 원전·전력, 조선, 방산·우주, 2차전지, 제약·바이오, IT·AI, 금융, 화학·소재, 철강·기계, 건설·인프라, 소비재·유통, 기타.

## 2. 지정 정보원 조사

아래의 주기는 ARVIS의 향후 제안값이다. 제공자의 갱신 주기·계약상 호출 한도·실제 데이터 지연을 뜻하지 않는다. 공식 문서가 있어도 실측 가용성이나 SLA를 검증한 것은 아니다.

| 정보원 | 이용 가능 항목·공식 경로·인증 | 주기·지연·안정성 | 약관·라이선스 및 채택 판단 |
|---|---|---|---|
| Investing.com | 지수·선물·주식·원자재·환율·채권·경제 일정의 웹 참고. 공식 지원 문서는 공개 API를 제공하지 않는다고 명시한다. | 자동 수집 주기 미설정. 지연은 상품·원제공자별로 다르며 실시간을 보장하지 않는다. HTML 의존 수집은 구조 변경에 취약하다. | 공급자 계약상 공개 API 제공 불가. 위젯은 별도 이용조건의 화면 삽입 수단이며 데이터 추출 허가가 아니다. 기본 수집원에서 제외하고 계약된 피드 또는 수동 원문 확인으로 대체한다. [공식 안내](https://pro.investing-support.com/hc/en-us/articles/4408847632017-Do-You-Offer-API-Access-at-Investing-com) |
| KRX 정보데이터시스템 / Open API | 공식 목록에 KOSPI·KOSDAQ 지수/주식 일별시세가 있다. 회원·인증키 신청 및 서비스별 승인 절차 확인이 필요하다. API는 AUTH_KEY 방식. 시장별 상승·하락·보합을 직접 반환하는 사용 가능 서비스는 이번 조사에서 확정하지 않았다. | 직전 거래일 자료를 1일 1회 확보하고 08:30 재확인 제안. 일별 API를 실시간 피드로 간주하지 않는다. 게시 시각·장전 가용성은 미검증. 키당 일 10,000회, 키 이용기간 1년 및 연장 정책. 장애·누락 가능. | 비상업적 이용, 출처 표시 및 제3자 제공 제한이 있다. 원자료 조회 승인과 파생 분석·Telegram/HTML 전달 권한을 별도 검토한다. 공개 배포는 기본 비활성화. [목록](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd), [약관](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp) |
| adrinfo.kr | 지정 도메인은 조사 시 HTTP 502. 공식 API·ADR 산식·자료 범위·인증은 확인하지 못했다. | 주기·지연·서비스 보장 미확인. 일시 접속 오류를 영구 폐쇄로 단정하지 않는다. | 수집·저장·재전송 권한도 미확인. 자동 수집 보류. 허용된 시장별 원자료로 산식이 명시된 자체 ADR을 계산하는 경로를 우선한다. [지정 사이트](https://adrinfo.kr/) |
| esignal.co.kr | 국내 야간선물, S&P500/Nasdaq100 선물, 금·원유 등 화면이 있다. 문서화된 공개 API는 확인하지 못했다. | 자동 수집 주기 미설정. 갱신 간격·지연·SLA 미확인. 공식 거래소 원시 체결 피드로 간주하지 않는다. | 사이트는 시세가 추정 도출값(Derived Data)임을 명시한다. 실제 야간선물 가격과 혼합 금지. 자동 이용·재배포 허락 전에는 참고 링크만 제공한다. [사이트](https://esignal.co.kr/), [이용안내](https://esignal.co.kr/usage/) |
| 네이버 뉴스 검색 | 공식 REST 검색은 제목·원문 URL·요약 패시지·pubDate 제공. 기존 개발자센터는 Client ID/Secret 인증. 신규는 NAVER API HUB 경로를 검토한다. | 권한 확보 후 장전 10~15분 간격의 제한 조회 제안. 검색 색인 지연·누락 가능. 기존 문서의 일 25,000회 한도를 HUB 요금제/한도로 그대로 적용하지 않는다. | 2026년 개정 검색 약관은 AI 입력·활용, 가공 표시, 보관·제3자 제공에 제한을 둔다. 검색 결과를 AI 브리핑 입력/영구 뉴스 저장소/Telegram 원천으로 기본 사용하지 않는다. 별도 허가 또는 이용 가능한 직접 원문 공급 계약이 필요하다. [API](https://developers.naver.com/docs/serviceapi/search/news/news.md), [개발자센터 약관 개정](https://developers.naver.com/notice/article/33400), [HUB 약관 개정](https://www.ncloud.com/support/notice/all/2243) |
| 네이버 카페 | 공개 색인 카페글 검색 API는 있다. 지정 게시판 전체 읽기 API와는 다르다. 문서의 요청 조건에는 게시판 ID가 없고, 결과에 정확한 게시 시각이 없다. 비공개/회원 글 접근권을 주지 않는다. | 장전 특정 글의 완전성·작성 시각 보장 불가. 권한 미확인 상태에서는 자동 주기 없음. | 검색 API 결과에는 위 검색 약관 적용. 카페 작성자의 저작권도 별개다. 지정 카페/게시판 URL·공개 여부·작성자 허락을 확인한 뒤 승인된 수동 입력 또는 제공자 피드만 검토한다. 로그인 쿠키·비공개 엔드포인트로 우회하지 않는다. [검색 명세](https://developers.naver.com/docs/serviceapi/search/cafearticle/cafearticle.md) |

네이버 이관 일정: 개발자센터 Search API 신규 신청은 2026-07-30 24:00 종료, 기존 신청자는 2027-06-30 24:00까지 기존 경로 이용 가능. 신규 서비스는 HUB의 인증·가격·약관을 확인해야 한다. HUB도 2026-09-20 시행 검색 약관에 AI 입력 금지가 있어, 이관으로 제한이 해소되지 않는다. [이관 공지](https://developers.naver.com/notice/article/32973), [HUB 개정 공지](https://www.ncloud.com/support/notice/all/2243).

## 3. 공식 API 우선 경로와 대체 정책

| 데이터 묶음 | 최소 우선 경로 | 대체·미확인 사항 |
|---|---|---|
| 미국 지수·주식·업종·미국 시장 폭 | 거래소/지수 제공권을 포함한 공식 계약형 데이터 사업자 API 한 곳부터 선정. S&P500, Nasdaq(Composite와 Nasdaq100 구분), Dow, SOX, Russell2000 및 NVDA/TSLA/AAPL/MU 권한·심볼·정규장 종가를 계약표에서 확인한다. | 이번에는 특정 사업자의 전체 커버리지/재배포 허가를 확정하지 않았다. SOX를 NVDA로, Russell2000을 ETF로 자동 대체하지 않는다. 미국 상승/하락은 NYSE/Nasdaq 등 모집단 명시. 승인 피드가 없으면 해당 항목 누락. |
| 미국 10년 금리 | 미 재무부 공식 일별 금리 CSV/XML 또는 FRED의 허용된 시계열. | 재무부 CMT는 수익률 곡선에서 산출한 일별 값이며 실시간 on-the-run 체결수익률이 아니다. FRED는 API 키 및 시계열 원저작권 확인 필요. 거래일·공표/수집 시점 표시. [재무부 설명](https://home.treasury.gov/policy-issues/financing-the-government/interest-rate-statistics), [FRED API](https://fred.stlouisfed.org/docs/api/fred/), [이용조건](https://fred.stlouisfed.org/legal/) |
| DXY·WTI·USDKRW·미국 지수선물 | 권한 있는 동일 시세 사업자의 상품별 API. 선물은 거래소·월물·최종거래/정산 기준을 명시한다. | 달러 광의지수는 ICE DXY와 다르고, WTI 현물은 근월 선물과 다르다. ECOS 같은 공식 일별 환율은 장전 현물/NDF의 대체 실시간값이 아니다. 동일하지 않은 대체 자료는 이름/지연을 바꾸어 별도 시리즈로만 제공. [ECOS 공식 입구](https://ecos.bok.or.kr/api/)의 상세 시리즈·권한은 후속 확인. |
| 국내 지수·시장 폭·ADR | 키움 공식 시장 집계 필드의 의미·완료 시점 검증 후 원본 통계 계열로 사용. KRX 일별 종목 자료는 과거 이력·교차 검증·별도 일반주 집계 경로로 검토. | 두 계열의 모집단을 섞지 않는다. 상장폐지·신규·거래정지·결측·페이지 누락을 따로 계수하고, 불완전한 부분표본을 전체 시장 폭이라고 표시하지 않는다. 상세 정책은 후속 ADR 문서 참조. |
| 국내 야간선물 | 실제 거래소 야간계약을 제공하는 승인된 증권사/시세 사업자 공식 경로. | 월물·세션·비교 기준 종가·정산가와 이용권한 확인이 선행한다. esignal 파생값이나 미국 선물을 실제 KRX 야간선물로 대체하지 않는다. 없으면 `unavailable`. |
| 경제 일정·정책 | Fed/BLS 등 기관의 공식 일정·발표 문서, 국내 기관 공식 일정. | 발표 예정시각은 실제 발표시각과 구분. 컨센서스 예상치는 별도 제공권 필요. 공공 문서도 출처별 이용조건 확인. 일정은 하루 1회 및 브리핑 직전 변경 확인 제안. [Fed 일정](https://www.federalreserve.gov/newsevents/calendar.htm), [BLS 개발자 안내](https://www.bls.gov/developers/) |
| 기업 실적·수주·산업/경제 뉴스 | AI 요약·보관·전달이 허용된 언론사 계약 피드, 기관/기업의 허용된 공식 발표. | RSS 공개만으로 재배포/AI 이용 허가를 추정하지 않는다. 허용된 수동 근거 입력을 첫 경로로 지원. 네이버 검색 제한을 원문 스크래핑으로 우회하지 않는다. |

장애 처리: 소스별 타임아웃, 일시 오류에 최대 2회 재시도와 지수 백오프(제공자 Retry-After 우선), 인증/권한 실패는 즉시 중단한다. 소스 장애가 다른 수집기를 막지 않는다. 허용 보관 기간 안의 이전 자료만 `stale`과 거래일/경과시간을 명시하여 제공한다. 동일 시리즈 대체 소스가 충돌하면 양쪽 근거를 남기고 보류한다. 결측값을 0·보합으로 채우거나 mock 값을 실자료로 채우지 않는다.

## 4. 시간·품질·분석 계약

- 실행 제안: 한국 거래일 08:30 준비 → 08:35 기준시각 고정 → 08:40까지 한 건 생성. 스케줄러는 이번 단계에서 수정하지 않는다. 미국 휴장·조기폐장 및 DST는 `America/New_York`, 국내 일정은 `Asia/Seoul` 세션 달력으로 계산한다.
- `briefing_date`, `us_trade_date`, `kr_trade_date`, `night_session_date`를 따로 둔다. 장전 국내 지수/시장 폭은 **직전 완료 국내 거래일**이다. 야간선물·현재 환율·미국 현물 종가의 서로 다른 시점을 숨기지 않는다.
- 원자료 계약: source URL, provider/series ID, 단위·통화·거래소·월물, 시장 시각, 거래일, 공표 시각, 수집 시각, 이용 가능 시각, 지연/상태, 기준값과 출처, 라이선스 버전. 기준시각 이후에 알려진 자료는 당시 브리핑/재현에 사용하지 않는다. 공표 시각 미확인과 실시간 여부 미확인은 그대로 남긴다.
- 지수·주식 등락률은 동일 세션/동일 가격기준의 직전 종가 대비. 금리는 % 수준과 bp 변화를 분리한다. USDKRW는 KRW per USD, WTI는 현물/월물과 USD/bbl, DXY는 정확한 지수 명칭을 명시한다.
- 시장 폭은 `advances/declines/unchanged/unknown`, 시장·모집단·기대 종목 수·수신 수·완전성으로 저장한다. 일반주 분석 후보 2,556개로 계산하면 시장 전체가 아닌 **후보군 시장 폭**이라고 표시한다.
- ADR은 일별 A/D와 구분한다. 자체 산식은 `100 × N개 완료 거래일 상승종목 수 합 / 하락종목 수 합`, N=5·10·20으로 설계한다. adrinfo의 산식이라고 주장하지 않는다. N일 완전성·시장별 모집단·상하한가 중복 여부를 검증하고 분모 0/자료 부족은 null. 과열/침체 수치 임계값을 임의 확정하지 않는다. adrinfo는 핵심 의존성에서 제외한다.
- 미국 강세·약세 업종은 허가된 동일 세션 업종 지수 또는 정의된 ETF 바스켓의 수익률 순서로 제시한다. ETF는 업종 대용 지표임을 표시한다. 대표주 네 종목이나 Nasdaq 등락만으로 전체 섹터 강약을 단정하지 않는다.
- 글로벌→국내 연결은 버전 있는 다대다 근거표로 둔다. 예: 반도체→반도체, 소프트웨어/AI→IT·AI는 검토 가능한 연결 후보지만 미국 산업재 전체를 조선/방산/로봇으로 일괄 분류하지 않는다. 동일 근거를 복수 연결해도 시장 수익률/거래대금을 중복 합산하지 않는다.
- 주목 섹터는 최대 3개(0개 가능). 최신 섹터 관측과 관련 사건 근거, 매핑 근거가 있는 항목부터 선정하고 반대 근거·누락도 함께 표시한다. 고정 가중치·가산점·국내 수급 강도 점수를 새로 만들지 않는다. `domestic_flow_confirmed=false`, `role=watch_only`를 유지한다.

## 5. 공통 뉴스 입력과 재탕 판별

향후 C14-B와 C15가 같은 `news_evidence` 입력을 사용하고 provider/query/window 기준 수집 요청을 합친다. 종목·시장 분석은 동일 item/event ID를 참조한다. 뉴스 공급·요약 AI 구현은 이번 단계에 포함하지 않는다.

- 항목: 원문 URL, 제공자 article ID, 언어, 출처/작성자, 제목/허용 본문 범위, `published_at`, `updated_at`, `event_at`, `event_time_status`, `first_seen_at`, `available_at`, 기업·산업·사건 유형, 권한, 근거 reference.
- NAVER `pubDate`는 네이버 제공 시각(미제공 기사는 원문 제공 시각)이지 사건 발생 시각이 아니다. 검색 `lastBuildDate`도 기사 작성 시각이 아니다. 카페 검색 결과는 정확한 게시 시각을 제공하지 않으므로 날짜순 검색만으로 당일 장전 글이라고 확정하지 않는다.
- 허용된 데이터만 canonical URL/article ID로 1차 중복 제거, 허용 본문 fingerprint와 기업·사건 유형·사건일로 2차 사건 묶음. 제목 유사도만으로 합치지 않는다. URL 정규화는 원문 URL 보존과 별개 내부 키이며 제공자 표시 URL 변조에 사용하지 않는다.
- 기사 재게재/수정/오래된 사건의 새 기사를 구분한다. 사건일 불명은 `unknown`; 최신 게시일로 사건의 신선도를 승격하지 않는다. 주요 뉴스 최대 3건은 서로 다른 사건 우선, 과거 사건이면 그 사실을 명시한다.
- 외부 글은 지시가 아닌 분석 자료로 다룬다. 본문에 포함된 명령·매매 유도문을 실행하지 않는다. 요약 문장마다 허용된 근거 ID를 연결하고 내용 없는 출처로 사건을 만들어내지 않는다.

## 6. 최소 모듈 구성과 저장 정책

아래는 향후 모듈 경계 제안이며 이번에 파일/기능을 구현하지 않는다.

1. `contracts.py`: Observation / NewsEvidence / SectorContext / BriefingResult 및 엄격한 validator.
2. `sources.py`: 승인된 소스 어댑터만 등록, 시간·단위 정규화, 권한·완전성 검사. 첫 단계는 오프라인 fixture/승인된 수동 입력.
3. 공통 `news_evidence`: C14-B/C15 공용 공급·중복/사건 ID 관리. 별도 뉴스 크롤러를 두 벌 만들지 않는다.
4. `analyze.py`: 순수 계산으로 시장 폭·섹터 비교·최대 3개 관심 섹터·위험 요인 생성. 결과에 정책/매핑 버전 기록.
5. `render.py`: 하나의 고정된 결과 JSON에서 한국어 Telegram 텍스트와 HTML용 데이터를 생성. 전송은 기존 전송 계층과 별도 명시적 어댑터로 연결한다.

기존 DB에는 테이블을 추가하지 않는다. 향후 별도 아티팩트 저장이 필요하면 승인된 경로/보관기간을 먼저 정한다. 모든 소스에 `can_store`, `can_derive`, `can_ai_input`, `can_telegram`, `can_html`, `expires_at`, `license_reference`를 둔다. 미확인은 false다. 원문·파생물·결과 JSON에도 권한과 만료를 전파하며, fingerprint를 이유로 금지된 원문/파생물을 영구 보존하지 않는다. HTML 공개 배포와 개인 Telegram도 동일한 이용권한이라고 가정하지 않는다.

초기 한국어 브리핑은 템플릿으로 충분하다. AI 문장 생성은 필수가 아니며, 나중에 추가할 때 허용된 근거만 입력한다. 자동 매매/매수 비중/주도주 확정은 결과 계약에 두지 않는다.

## 7. 브리핑 JSON 계약 초안

버전 `arvis-market-intelligence-v1`. 아래는 결측을 표현하는 **스키마 형태 예시**이며 실제 시장 자료가 아니다. 모든 관측 항목은 같은 Observation 계약을 사용한다. 뉴스/섹터 빈 배열은 허용된다.

```json
{
  "schema_version": "arvis-market-intelligence-v1",
  "source_kind": "design_example",
  "report_id": null,
  "briefing_date": null,
  "as_of": null,
  "generated_at": null,
  "timezone": "Asia/Seoul",
  "status": "held",
  "sessions": {"us_trade_date": null, "kr_trade_date": null, "night_session_date": null},
  "policy": {"sector_mapping_version": null, "domestic_flow_confirmed": false, "role": "watch_only"},
  "us_summary": {"text": "자료 미확보", "evidence_ids": []},
  "us_indices": [],
  "us_stocks": [],
  "us_sector_performance": [],
  "macro": [],
  "kr_indices": [],
  "breadth": [],
  "night_futures": [],
  "economic_events": [],
  "news": [],
  "watch_sectors": [],
  "risks": [{"code": "missing_data", "text": "필수 시장 자료 미확보", "evidence_ids": []}],
  "coverage": {"expected": 0, "usable": 0, "gaps": ["design_only"]},
  "sources": [],
  "delivery": {"telegram_allowed": false, "html_allowed": false},
  "analysis_fingerprint": null
}
```

구조와 검증 규칙은 다음과 같다.

| 자료형 | 필수 항목·검증 |
|---|---|
| 공통 시각 | ISO8601 offset 포함. 거래일은 YYYY-MM-DD. 미확인은 null과 사유로 표현. 알려진 자료라도 이용 가능 시각이 as_of 이후이면 제외. |
| Observation | `id, series_id, instrument_name, value, unit, change_pct, change_bp, reference_value, reference_kind, reference_evidence_id, trade_date, market_at, published_at, fetched_at, available_at, delay_seconds, status, reason, source_id`. 수치는 유한 Decimal 문자열/null, 상태는 ok/stale/unavailable/conflict. |
| Breadth | Observation 공통 근거 + `market, universe_id, advances, declines, unchanged, unknown, expected_count, observed_count, complete, adr_window, adr_value, formula_version`. 건수는 음이 아닌 정수. 완전성이 없으면 전체 시장이라고 단정하지 않음. |
| News | `id, event_id, headline, summary, url, source_id, published_at, updated_at, event_at, event_time_status, available_at, novelty_status, evidence_ids`. 최대 3건. 이용권한 검사 필수. |
| EconomicEvent | `id, name, jurisdiction, scheduled_at, released_at, status, source_id, url`. 예정과 실제 발표 시각 분리. |
| WatchSector | `sector_name, mapping_version, rationale, evidence_ids, counter_evidence_ids, domestic_flow_confirmed=false, role=watch_only`. sector_name은 기존 15개 값, 중복 불가, 최대 3개. |
| Source | `id, provider, url, license_reference, license_checked_at, can_store, can_derive, can_ai_input, can_telegram, can_html, expires_at`. 인증 헤더/비밀값은 포함하지 않음. |
| Report | `status=complete/partial/held`. 모든 근거 참조 ID가 존재해야 함. 배포 권한은 포함한 자료 전체에서 산출. risks는 화면에 1~2건, 나머지는 coverage/진단에 보존. fingerprint는 자신의 필드를 제외한 허용된 canonical 분석 결과로 계산. |

Telegram/HTML의 표시 순서는 공통으로 ①미국 시장 1줄 요약, ②지수·대표 종목, ③야간선물·환율·시장 폭, ④주요 뉴스 최대 3건, ⑤국내 주목 섹터 최대 3개, ⑥핵심 위험 1~2개다. 근거 부족은 해당 칸에 미확보/보류로 표시하며 호재로 빈칸을 채우지 않는다.

## 8. 최소 구현 순서와 미해결 위험

1. **계약과 오프라인 표시**: 위 자료형을 확정하고 수동/합성 입력→순수 분석→공통 JSON/템플릿을 구현. 후속 테스트는 DST/휴장, 서로 다른 거래일, 미래 자료, 결측≠0, ADR 분모 0, 단위, 중복 사건, 권한 제한, 최대 3건을 대상으로 한다.
2. **권한이 확정된 최소 소스 연결**: KRX 일별 자료＋공식 일정/금리＋허용된 뉴스 입력으로 시작. 미국 지수/업종/시장 폭과 국내 야간선물의 사업자·범위·지연·배포 권한을 결정하고 미확인 항목은 결측 유지. 실제 연결은 별도 단계의 승인 범위에서 검증한다.
3. **배포와 보조 연계**: 실자료의 시각/품질 검증 후 새 스키마 전용 Telegram adapter와 HTML 카드 추가. C14-B 공통 뉴스 ID 결합. 기존 AM_MOMENTUM 점수·수집 일정은 변경하지 않는다.

주요 위험은 (a) 네이버 검색의 AI/가공/저장 제한과 KRX·거래소의 재배포 권한, (b) 무료 자료만으로 SOX/Russell/미국 전체 시장 폭/실제 야간선물을 확보한다는 보장이 없는 점, (c) 08:35까지 일별 공표값이 준비된다는 보장이 없는 점, (d) 미국 업종과 국내 15개 분류의 의미 차이다. 권한 미확인 수집이나 근거 없는 값 보완으로 해결하지 않는다.

이번 성과물은 본 설계 문서뿐이다. 기능 구현·운영 설정·DB 변경·API 호출·테스트·Commit/Push는 수행하지 않았다.
