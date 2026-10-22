# 공식 GitHub에서 확인한 명세와 확인 대기 항목

## ka10095 2종목 검증 확장 (2026-10-22)

공식 명세의 요청 구분자·응답 목록·시세 필드를 확인하여 별도 `quote-batch-test`를 추가했습니다. 사용자가 Windows 병원 PC에서 2종목 모의 조회·저장 및 TOP20의 나머지 18종목 9회 요청 성공(미전송 0건, issues 없음)을 보고했습니다. 공식 최대 종목 수와 가격 부호의 구체적 의미는 미확인입니다. 출처·정규화 정책·미확인 사항은 [복수 시세 검증 기록](BATCH_QUOTES.md)을 참고하세요. ka10007은 유지하며 정기 일정에는 연결하지 않습니다.

## ka10007 시세 테스트 확장 (2026-10-22)

독립 실행·스키마·검증 범위는 [시세 테스트 설명](QUOTES.md)을 참고하세요. 기존 순위 명세와 수집 경로는 유지합니다.

같은 아래 공식 명세 커밋의 `시세표성정보요청(ka10007)`을 확인했습니다. `POST /api/dostk/mrkcond`, 본문 `stk_cd`, 헤더 `api-id: ka10007` 및 Bearer 인증입니다. 응답은 최상위 객체이며 `stk_cd`, `stk_nm`, `smbol`, `cur_prc`, `flu_rt`, `trde_qty`, `trde_prica`, `open_pric`, `high_pric`, `low_pric`, `date`, `tm`의 문자열 필드를 보존합니다. 응답 시장 필드는 선택 항목입니다. 가격 단위는 원, 등락률 %, 거래량 주, 거래대금 백만원, 날짜 YYYYMMDD, 시간 HHmmss입니다. 사용자가 모의 조회 성공을 보고했고 저장 원본을 읽기 전용으로 확인했습니다. 실전 응답은 검증하지 않았습니다.

확인일: 2026-10-19 (한국 시간). 기준 커밋: `953e5dbff123f437ab4d11a78a95191a685eb51f`.

## 근거 파일

- [전체 API 명세 JSON](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/953e5dbff123f437ab4d11a78a95191a685eb51f/kiwoom/_data/kiwoom_api_spec.json): `apis`의 `접근토큰 발급(au10001)`, `실시간종목조회순위(ka00198)` 항목.
- [인증 샘플](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/953e5dbff123f437ab4d11a78a95191a685eb51f/examples/OAuth%20인증/접근토큰발급/create_access_token.py)
- [인증 HTTP 구현](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/953e5dbff123f437ab4d11a78a95191a685eb51f/kiwoom/core/auth.py)
- [조회순위 샘플](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/953e5dbff123f437ab4d11a78a95191a685eb51f/examples/국내주식/종목정보/get_domestic_realtime_stock_rank.py)
- [오류 분류 구현](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/953e5dbff123f437ab4d11a78a95191a685eb51f/kiwoom/core/errors.py)
- [인증 복구 구현](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/953e5dbff123f437ab4d11a78a95191a685eb51f/kiwoom/core/client.py)

## [0198] 대응 여부

국내주식 `실시간종목조회순위(ka00198)`가 존재함을 확인했습니다. 다만 저장소에서 **HTS 화면 [0198]과 동일하다고 직접 설명한 문구는 찾지 못했습니다.** 따라서 동일 기능의 후보 API로 사용하며, HTS와 집계 범위·시점·순위가 동일한지는 실제 응답과 HTS 비교 또는 키움 확인이 필요합니다. API 번호만으로 동일성을 단정하지 않습니다. 이후 사용자가 Windows PC의 모의투자 테스트에서 HTS [0198]과 데이터 일치를 확인했다고 보고했습니다. 공식 문서의 직접적인 대응 선언과 사용자의 실증 결과를 구분합니다.

## 인증

- 운영 도메인: `https://api.kiwoom.com` (`KIWOOM_ENV=real`)
- 모의투자 도메인: `https://mockapi.kiwoom.com` (`KIWOOM_ENV=mock`, 기본값)
- API ID: `au10001`, `POST /oauth2/token`
- JSON 본문: `grant_type="client_credentials"`, `appkey`, `secretkey`
- 응답: `token`, `token_type`, `expires_dt` (공식 구현은 `YYYYMMDDHHMMSS`, 한국 시간으로 해석)
- 명세의 인증 헤더 표에는 공통 authorization 등이 있지만, 실제 공식 `core/auth.py`는 Content-Type만 지정하여 토큰을 발급합니다. 이 프로그램도 해당 구현을 따릅니다.
- 토큰 값은 메모리에서만 사용하고 출력하거나 파일에 저장하지 않습니다.

## 단일 조회

- `POST /api/dostk/stkinfo`: 인증과 조회 모두 `KIWOOM_ENV`가 선택한 같은 서버 사용
- 헤더: `Content-Type: application/json;charset=UTF-8`, `api-id: ka00198`, `authorization: Bearer <token>`
- 본문: `{"qry_tp":"1"}`
- `qry_tp`: `1` 1분, `2` 10분, `3` 1시간, `4` 당일 누적, `5` 30초. **조회 집계 구분이며 반복 실행 간격이 아닙니다.**
- 응답 헤더 `cont-yn=Y`일 때 `cont-yn`, `next-key`를 다음 요청에 전달합니다. 한 번 실행해도 인증·연속조회·오류 재시도 때문에 HTTP 요청은 여러 번 발생할 수 있습니다.

응답 목록은 `item_inq_rank`입니다. 각 항목의 공식 필드는 다음과 같습니다. 모두 선택 항목이므로 누락 필드는 만들어 넣지 않습니다.

| 필드 | 공식 의미 |
| --- | --- |
| `stk_cd` | 종목코드 |
| `stk_nm` | 종목명 |
| `bigd_rank` | 빅데이터 순위 |
| `rank_chg` | 순위 등락 |
| `rank_chg_sign` | 순위 등락 부호 |
| `past_curr_prc` | 과거 현재가 / 기준 시점 주가 |
| `base_comp_sign` | 기준가 대비 부호 |
| `base_comp_chgr` | 기준가 대비 등락율 (%) |
| `prev_base_sign` | 직전 기준 대비 부호 |
| `prev_base_chgr` | 직전 기준 대비 등락율 (%) |
| `dt` | 기준 일자 YYYYMMDD |
| `tm` | 기준 시간 HHmmss |

`base_comp_chgr`를 일반적인 전일 대비 등락률로 재해석하지 않습니다. API `dt`/`tm`과 프로그램이 기록한 수집 시각은 서로 다릅니다.

## 모호한 부분의 처리

- `bigd_rank`는 ‘빅데이터 순위’라고 명시되고 샘플 값은 `1`, `2`이지만 설명에 `0: 순위 변동 없음`이라는 문구도 있습니다. 프로그램은 양의 정수일 때만 실제 API 값 기준 1~20위를 선택합니다. 0·누락·잘못된 값·중복 순위는 의미 확인 오류로 중단합니다. 응답 배열의 순서로 순위를 만들지 않습니다.
- `rank_chg`/`rank_chg_sign`의 설명과 샘플에도 빈값·`N` 등 차이가 있어 값의 의미를 추가 추정하지 않습니다.
- 명세 JSON 내 응답 예시 문자열은 일부 쉼표가 잘못되어 그대로 JSON 파싱할 수 없습니다. 테스트에는 문법을 갖춘 가상 응답을 사용했으며, 실제 API 응답이라고 주장하지 않습니다.
- 모의투자/실전 도메인을 모두 지원합니다. 사용자가 모의투자 키로 인증·단일 조회 성공과 HTS [0198] 데이터 일치를 확인했습니다. 이것은 해당 테스트의 관찰 결과이며 모든 집계 구분과 시점의 일치까지 보장하지 않습니다.

## 오류·호출 제한 처리의 근거

공식 `errors.py`의 인증 복구 코드 `8005`, `8031`, `8103`, 본문 메시지의 `[코드:...]`/`CODE=...` 형식과 `client.py`의 HTTP 401 재발급 방식을 참고했습니다. 인증 복구는 페이지당 한 번까지만 합니다. 호출 제한 코드 `1700`, `1701`, `1702`도 공식 오류 분류에 근거합니다.

초당 허용 횟수나 장시간 누적 제한 수치는 이 조사에서 확정하지 않았습니다. 요청 시작 간 최소 1초는 **이 프로그램의 보수적인 정책**이며 공식 제한 보장을 뜻하지 않습니다. 연결/시간 초과, HTTP 429 및 일시적 5xx, 명시된 호출 제한 오류는 최대 3회 시도합니다. 재시도 대기는 지수 증가하며 숫자형 Retry-After도 반영합니다. 60초를 넘는 대기 요청은 중단합니다. TLS 검증은 유지하며 리다이렉트는 따라가지 않습니다.

최초 개발 시 실제 API 호출은 보류했습니다. 이후 사용자가 Windows PC에서 모의투자 키와 모의투자 도메인으로 `auth`·`once` 성공 및 HTS [0198] 데이터 일치를 확인했다고 보고했습니다(2026-10-19). 이번 환경 선택 변경은 오프라인 모의 HTTP 테스트로 검증했으며 개발 환경에서 실제 키나 토큰을 사용하지 않았습니다.

## HTS 조건검색 일반 조회 (1단계)

동일 공식 명세 커밋의 `조건검색 목록조회(ka10171)`, `조건검색 요청 일반(ka10172)`와 [공식 WebSocket 클라이언트](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/953e5dbff123f437ab4d11a78a95191a685eb51f/kiwoom/core/ws_client.py)를 근거로 합니다.

- `/api/dostk/websocket`, 실전 `wss://api.kiwoom.com:10000`, 모의 `wss://mockapi.kiwoom.com:10000`.
- `LOGIN` 프레임의 `token`으로 인증하며 `PING`을 그대로 반환합니다. 로그인 프레임은 저장하지 않습니다.
- `ka10171`: `{"trnm":"CNSRLST"}`. 공식 예제의 `data`는 `[["0","조건1"], ...]`; 항목 표의 `seq`/`name` 객체 표현도 검증하여 처리합니다.
- `ka10172`: `{"trnm":"CNSRREQ","seq":"서버 식별자","search_type":"0","stex_tp":"K","cont_yn":"N","next_key":""}`. `K`는 KRX입니다. `cont_yn=Y`이면 본문의 `next_key`로 연속조회합니다. 반복/누락 키나 잘못된 `seq`는 실패입니다. `data` 누락을 정상 빈 목록으로 가정하지 않습니다.
- 문자열 원본 필드: `9001`(A 주식/J ELW/Q ETN 접두어+6자리 코드), `302`(종목명), `10`(현재가), `25`(전일대비기호), `11`(전일대비), `12`(등락률), `13`(누적거래량), `16`/`17`/`18`(시가/고가/저가). `12`는 특수 자리수 표기이므로 이번 단계는 원본만 저장합니다. 거래대금 순위·120일 신고가 증명 필드를 임의로 만들지 않습니다.
- 공통 오류 `8104`는 모의 미지원, `1700`/`1701`/`1702`는 요청 제한입니다. 모의 서버 주소 존재만으로 조건검색 지원을 보증하지 않습니다. 공식 동시 연결·조회 제한 수치와 실제 HTS 일치 여부는 추가 확인 대상입니다.
- `ka10173` 실시간 검색과 `ka10174` 해제는 이번 구현에 사용하지 않습니다.
