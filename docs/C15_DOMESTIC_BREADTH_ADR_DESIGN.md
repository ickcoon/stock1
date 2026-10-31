# C15 — 국내 시장 폭·ADR 원본 조사 및 설계

2026-10-31 KST. 공개 공식 문서만 조사했다. 실제 키움/KRX 데이터 API·OAuth 호출, 인증정보 사용, DB 연결·쓰기, 운영 수집기/스케줄러 변경, 기능 구현, 테스트, Commit/Push는 수행하지 않았다. 공식 GitHub의 정적 명세와 샘플 소스는 문서로 읽었으며 실행하지 않았다.

## 1. 확인 결과

**키움 REST는 상승·하락·보합 건수를 직접 제공한다.** ka20001/ka20009의 최상위 응답과 ka20003의 업종별 행에 관련 필드가 있다. ka20002는 종목별 등락 부호를 제공한다. 다만 HTS 화면과 모집단·상하한가 중복·거래일·확정 시점까지 일치하는지는 실제 자료 없이 확정할 수 없다.

- KOSPI 요청 조합: `mrkt_tp="0", inds_cd="001"`.
- KOSDAQ 요청 조합: `mrkt_tp="1", inds_cd="101"`.
- ka20003은 mrkt_tp 없이 inds_cd만 요청한다. `001`/`101` 종합 행을 선택하고 하위 업종 행을 더하지 않는다. 응답의 stk_cd는 여기서는 업종 코드이므로 개별 종목 코드 정규화기를 적용하지 않는다.
- 네 API는 모두 `POST /api/dostk/sect`, 인증 토큰과 api-id 헤더를 사용하는 공식 경로다. 경로 확인만 했으며 요청하지 않았다.

근거: [키움 공식 API 가이드](https://openapi.kiwoom.com/m/guide/apiguide), [공식 정적 전체 명세](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json). 조사 시 명세 파일 SHA-256: `42a7b3912c9d9588c83bdc2db7779c8d2e038703a2b5562e54ef46ae905cba79`. main URL은 변경 가능하므로 향후 구현 시 명세 버전도 관리한다.

## 2. API별 공식 응답 필드

아래는 조사한 명세의 body 필드다. 기본 응답의 성공 코드·메시지와 연속조회 헤더도 별도 검사해야 한다. 응답 필드는 대부분 선택적 문자열이므로 빈 문자열/누락을 0으로 바꾸지 않는다.

### ka20001 — 업종현재가요청

최상위:

`cur_prc, pred_pre_sig, pred_pre, flu_rt, trde_qty, trde_prica, trde_frmatn_stk_num, trde_frmatn_rt, open_pric, high_pric, low_pric, upl, rising, stdns, fall, lst, 52wk_hgst_pric, 52wk_hgst_pric_dt, 52wk_hgst_pric_pre_rt, 52wk_lwst_pric, 52wk_lwst_pric_dt, 52wk_lwst_pric_pre_rt`.

`inds_cur_prc_tm[]`: `tm_n, cur_prc_n, pred_pre_sig_n, pred_pre_n, flu_rt_n, trde_qty_n, acc_trde_qty_n`.

직접 집계 필드: `upl` 상한, `rising` 상승, `stdns` 보합, `fall` 하락, `lst` 하한. `trde_frmatn_stk_num`은 거래형성종목수이며 전체 상장 수와 같다고 가정하지 않는다. 반복부는 시간별 지수·거래량이며 시간별 시장 폭이 아니다. [공식 샘플](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/국내주식/업종/get_domestic_sector_price.py)

### ka20002 — 업종별주가요청

`inds_stkpc[]`: `stk_cd, stk_nm, cur_prc, pred_pre_sig, pred_pre, flu_rt, now_trde_qty, sel_bid, buy_bid, open_pric, high_pric, low_pric`.

집계 건수는 직접 제공하지 않는다. 종목별 pred_pre_sig의 명세는 1=상한, 2=상승, 3=보합, 4=하한, 5=하락이다. 재집계 시 1/2를 상승, 4/5를 하락으로 한 종목당 한 번만 센다. 추가 요청 stex_tp는 1=KRX, 2=NXT, 3=통합이므로 HTS/KRX 비교의 기본 설계는 KRX로 고정한다. 전체 페이지와 모집단 확보 여부를 확인해야 한다. [공식 샘플](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/국내주식/업종/get_domestic_sector_stock_price.py)

### ka20003 — 전업종지수요청

`all_inds_idex[]`: `stk_cd, stk_nm, cur_prc, pre_sig, pred_pre, flu_rt, trde_qty, wght, trde_prica, upl, rising, stdns, fall, lst, flo_stk_num`.

행별로 시장 폭 다섯 필드와 상장종목수 flo_stk_num이 있다. 종합 행을 골라 시장·업종 코드와 함께 보존한다. 종합/대형/중형 등 행의 중복 합산 금지. [공식 샘플](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/국내주식/업종/get_domestic_all_sector_indices.py)

### ka20009 — 업종현재가일별요청

최상위 필드는 ka20001과 동일하다. 반복 목록은 `inds_cur_prc_daly_rept[]`: `dt_n, cur_prc_n, pred_pre_sig_n, pred_pre_n, flu_rt_n, acc_trde_qty_n`이다.

**과거 일자별 반복부에는 상승·하락·보합 건수가 없다.** 최상위 건수를 각 dt_n에 복사하면 잘못된 ADR 이력이 된다. 과거 지수의 상승/하락 일수를 종목 수 ADR로 대신해서도 안 된다. [공식 샘플](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/국내주식/업종/get_domestic_sector_daily_price.py)

## 3. 확정할 수 없는 의미와 검증 관문

공식 필드 이름만으로 다음을 확정하지 않는다.

1. **상하한가 포함 관계**: rising/fall이 각각 upl/lst를 이미 포함하는지 명시적 설명이 부족하다. `limits_included / limits_separate / unverified` 상태로 저장한다. 포함이면 A=rising, D=fall; 별도이면 A=rising+upl, D=fall+lst. 미확인이면 정규화 A/D와 ADR은 보류한다. 원본 다섯 값은 그대로 표시할 수 있다.
2. **상품 모집단**: ETF/ETN/리츠/스팩/우선주 등이 종합 행에 포함되는지 명세만으로 확정하지 않는다. 원본 통계에서 추정 개수를 빼지 않는다.
3. **거래일과 완료 여부**: ka20001 최상위에 명시적 거래일이 없고 ka20009의 과거 날짜는 최상위 건수의 날짜 근거가 아니다. 수집 날짜를 거래일로 대입하지 않는다. 장전 초기화/전일 잔존값·실제 종료 시점 확인이 필요하다.
4. **숫자 합계**: 거래형성 수와 상장 수는 다른 개념이다. 공식 예시는 실측 정합성 보증 자료가 아니며, 그 숫자의 합만으로 포함 관계나 거래정지 처리 정책을 역추정하지 않는다.

향후 별도 승인된 실자료 검증에서는 같은 시장·시점·거래소의 HTS 표시, 종합 행, 완전한 종목별 등락을 비교한다. 상한·하한 발생일과 거래정지/비일반 상품이 있는 사례를 포함한다. 일치 여부와 확인 근거를 `semantics_version`으로 기록한다. 그 전에는 'HTS와 동일한 확정 ADR'로 표시하지 않는다.

## 4. KRX 공식 대체·과거 이력 경로

[공식 서비스 목록](https://openapi.krx.co.kr/contents/OPP/INFO/service/OPPINFO004.cmd)에 유가증권/코스닥 일별매매정보와 시장별 종목기본정보가 있다. ETF/ETN/ELW는 별도 상품 서비스로 나뉜다. 주식 일별 자료는 2010년 이후 범위를 제공한다고 안내한다.

- [유가증권 일별매매정보](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=JvJFzlAENzZlPBDNGAWC), [코스닥 일별매매정보](https://openapi.krx.co.kr/contents/OPP/USES/service/OPPUSES002_S2.cmd?BO_ID=hZjGpkllgCBCWqeTsYFj).
- AUTH_KEY 신청·서비스 이용 승인 후 일별 종목 자료로 직접 집계하는 경로를 우선 검토한다. 이번 공개 페이지에서는 출력 필드 표가 로드되지 않아 정확한 등락/기준가 필드명은 확정하지 않았다. 다운로드 명세의 필드·형식·기업행위 기준을 후속 구현 전에 확인한다. 미공개 데이터 요청을 보내거나 웹 다운로드 내부 경로를 추측하지 않았다.
- 제공자가 정의한 당일 대비/등락 구분과 검증된 기준가를 사용한다. 권리락·분할 등으로 오늘 종가와 어제 종가의 단순 차이가 공식 등락과 다를 수 있다. 장기 수정주가를 혼합하지 않는다. 신규 상장/가격 미형성/거래정지의 처리도 근거를 확인한다.
- 과거 모집단은 해당 거래일의 종목 마스터·유형 근거로 재현한다. 현재 4,314종목 또는 현재 후보 2,556종목을 과거에 소급 적용하지 않는다. 과거 유형 근거가 없으면 일반주 계열의 완전성을 인정하지 않는다.
- 키움 직접 집계가 있더라도, 과거 건수 초기 확보와 교차 검증에는 KRX 경로가 유용하다. 단 두 소스의 모집단이 같다고 확인되기 전에는 ADR 창 안에서 자동 연결하지 않는다.

KRX는 비상업적 이용·출처 표시·제3자 제공 제한과 키당 일 10,000회 정책이 있다. 원자료 저장·파생 계산·Telegram/HTML 전달 권한을 각각 확인한다. API 상시 접속 가능과 최신 일별자료의 공표 시각은 별개다. 장전 가용성을 보장하지 않는다. [공식 약관](https://openapi.krx.co.kr/contents/OPP/INFO/OPPINFO002.jsp)

## 5. 상품 포함 기준 — 서로 다른 두 계열

| 계열 | 포함·제외 규칙 | 표시와 사용 |
|---|---|---|
| `provider_market_v1` | 키움의 001/101 종합 통계를 그대로 보존. 상품별 포함 범위는 확인 전 unknown. ETF/ETN 등을 자체 차감하지 않음. | '키움 제공 시장 통계'. 보통주 전용 지표라고 부르지 않음. 의미/거래일 검증 후 이 계열 전용 ADR 계산. |
| `arvis_common_v1` | KOSPI/KOSDAQ **검증된 일반 보통주** 포함. ETF·ETN·ELW·리츠·스팩·뮤추얼펀드·인프라상품 제외, 우선주 기본 제외. 미확인/충돌은 pending. | 종목별 원본과 C12 분류 근거를 이용해 별도 집계. 시장 전체/HTS와 같다고 주장하지 않음. pending이 있으면 부분집계이며 정식 ADR은 보류. |

C12의 '분석 후보'는 보통주 verified가 아니다. 따라서 후보 2,556개를 전부 일반 보통주로 승격하지 않는다. 후보군 시장 폭이 별도로 필요하면 별도 universe_id를 부여하는 후속 사항으로 두며 이번 최소 설계에는 추가하지 않는다.

일별 정책은 고정하고 버전을 붙인다. 유가증권/코스닥과 주식 종류별 증권 코드를 문자열로 보존하고 동일 증권·거래일을 한 번만 센다. 다른 주식 종류를 회사명으로 합치지 않는다. 시장 이전은 해당 거래일 소속 시장에만 반영한다. 거래정지를 자동 보합으로 바꾸지 않는다. 공급자 정책상 보합 포함이면 그 근거를 기록하고, 자체 일반주 계열은 미거래/정지를 별도 집계하여 A/D에서 제외한다. 가격 자료 누락과 확인된 미거래는 구분한다.

## 6. 일별 저장 계약

실제 저장 구현/파일 생성/DB 테이블 추가는 하지 않는다. 향후 독립 아티팩트 저장소의 JSONL 또는 동등한 논리 모델을 제안한다. 운영 DB를 읽거나 migration할 필요가 없다.

| 기록 | 필수 항목 |
|---|---|
| 원본 관측 | observation_id, provider, api_id, market, inds_cd, venue_scope, source_url, spec_hash, raw_counts, listed_count, traded_count, fetched_at, source_at, available_at, trade_date, trade_date_evidence, sample_kind, completion_evidence, source_kind(real/mock), license_reference |
| 일별 정규화 | series_id, market, trade_date, universe_id/version, semantics_version, revision_id, snapshot_id, raw_observation_ids, advances, declines, unchanged, suspended, untraded, pending_type, excluded_by_reason, missing_price, unknown, expected_count, received_count, coverage_status, final_status, available_at |
| ADR 결과 | series_id, end_trade_date, window_sessions(5/10/20), start_trade_date, expected_dates, used_daily_revision_ids, sum_advances, sum_declines, adr_pct, formula_version, as_of, status, reason |

- raw_counts의 키는 upl/rising/stdns/fall/lst 그대로. 정규화와 원본을 분리한다. unknown 수를 알 수 없으면 null로 둔다.
- 집계 입력의 증권 목록/제외·보류 사유/마스터 버전을 허용된 경우 보존한다. 이를 snapshot_id로 참조한다. 같은 universe_id의 일별 구성은 신규/폐지로 바뀔 수 있으나 선정 규칙은 같아야 한다.
- 일별 기준 키는 `(series_id, market, trade_date, universe_version, semantics_version)`이다. 정정은 새 revision을 추가하고 supersedes 연결. 재수집 중복은 원본 hash/관측 키로 식별하고 합산하지 않는다. 원본과 기존 계산 결과를 덮어쓰지 않는다.
- 장중 관측과 일별 완료 자료는 sample_kind로 구분. 정식 일별 기록은 장 마감·공표와 거래일 근거를 확인한 자료만 선택한다. 미확인 장전 잔존값은 provisional 관측이며 일별 확정 기록으로 사용하지 않는다.
- 08:30~08:40 브리핑은 직전 완료 국내 거래일을 사용한다. 늦은 정정자료는 그 자료를 알기 전의 과거 브리핑에 소급 누출하지 않는다. 보관·파생·배포 권한과 만료는 C15 Source 계약을 그대로 전파한다.

## 7. ADR5·ADR10·ADR20 계산

여기서 ADR은 시장 폭의 Advance/Decline Ratio다. 가격 변동폭이나 상승 일수/하락 일수 비율과 다르다.

`ADR_N(t) = 100 × Σ A_d / Σ D_d`, N은 5·10·20, d는 t까지 **연속된 N개 완료 거래일**이다. A는 상한가를 포함한 상승 종목 수, D는 하한가를 포함한 하락 종목 수이며 한 종목은 하루 한 번만 센다. 보합은 분자/분모에서 제외하되 별도 표시한다. 일별 A/D 비율의 산술평균이 아니다.

- KOSPI와 KOSDAQ을 각각 계산한다. 같은 창에는 같은 소스 계열·모집단 규칙·상하한가 의미 버전만 사용한다.
- 휴장일은 거래일 달력에서 제외하지만 누락된 거래일을 건너뛰고 더 오래된 값으로 N을 채우지 않는다.
- 5일만 충족하면 ADR5만 제공한다. 10/20일은 insufficient_history. 중간 누락·불완전·의미 미확정·소스 충돌은 해당 창을 held 처리한다.
- ΣD=0은 null과 zero_declines. A/D 모두 0이어도 0이나 100으로 채우지 않는다. Decimal로 계산하고 표시 단계에서만 반올림한다.
- 100은 창 내 상승/하락 누적 수가 같다는 산술 기준이다. 80/120 등 매매·과열 임계값을 이번에 확정하지 않는다. 산식은 ARVIS 자체 정의이며 adrinfo와 동일하다고 주장하지 않는다.

합성 설명: 5일 ΣA=3,000, ΣD=2,000이면 ADR5=150. 이는 계산 예시이며 시장 관측값이 아니다. 일별 건수에 시총·거래대금 가중치는 부여하지 않는다.

## 8. 독립 모듈과 다음 최소 단계

흐름: 승인된 원본 입력 → `breadth_adapter` → 원본/정규화 관측 → 순수 `adr_calculator` → C15 공통 JSON의 breadth 항목 → 기존 제안 렌더러. 운영 0198/0186·AM_MOMENTUM·수집 스케줄과 분리한다. 상품 판정은 기존 C12 결과를 읽기 전용 입력으로 받으며 재분류하거나 DB에 쓰지 않는다.

후속 구현의 최소 범위는 (1) 합성 입력의 정규화·5/10/20일 계산 및 JSON 출력, (2) 별도 승인 후 HTS/REST 모집단·상하한가·완료 시점 대조와 KRX 과거자료 명세 검증이다. 테스트 설계는 상하한가 중복, 시장 분리, ETF/스팩/우선주/pending, 거래정지와 결측, 페이지/날짜 누락, 중복 revision, 기업행위, 미래 정정, 분모 0을 포함한다. 이번에는 실행하지 않는다.

adrinfo.kr은 **참고 링크만 유지**한다. 핵심 수집·정규화·ADR 계산·오류 복구·완전성 판단은 해당 사이트의 가용성이나 값에 의존하지 않는다.
