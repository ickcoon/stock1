# C14-D 독립 일봉 공급 경로

실행점: `python -m kiwoom_collector.daily_supply_cli`. 기존 운영 collector·DB·스케줄·인증/캐시 모듈은 변경하지 않는다. 기본값은 plan_only이며 자격증명·HTTP·저장소에 접근하지 않는다. 합성 replay와 향후 승인된 HTTP를 같은 bounded 수집 엔진에 주입한다.

## 공식 필드와 검증 한계

[키움 공식 ka10081 명세](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json)를 2026-11-01 재확인했다. `/api/dostk/chart`, `dt`(YYYYMMDD), `open_pric/high_pric/low_pric/cur_prc`(원), `trde_qty`(주), `stk_dt_pole_chart_qry`를 기존 B1 파서로 처리한다. `cur_prc`는 명세상 현재가이며 자동으로 확정 종가를 선언하지 않는다. 공식 포털 본문은 이번 조회 도구에서 접근 불가였으며 실서버 실측은 하지 않았다.

응답 `cont-yn=Y`이면 같은 본문에 응답의 cont-yn/next-key를 전달한다. 커서는 호출 내부에서만 사용하며 로그에 출력하지 않는다. 첫 253개 구조 검사 통과 완료일 봉 또는 증분 겹침 구간에 도달하면 중단한다. N인데 부족하면 insufficient_history, 연속 여부 불명·페이지/호출/시간 예산 초과·반복 커서·진행 없는 페이지는 실패다. 재시도·OAuth·토큰 갱신은 0회다.

공식 예제는 날짜 내림차순이다. 실제 응답은 기존 파서로 방향을 진단하며 오름차순도 날짜 키로 정규화하지만 무질서/중복일 응답은 거부한다. 가격 부호·단위는 기존 파서를 재사용하고 거래량/누락 봉은 역산하지 않는다. 수정 1은 권리발생일 이후 base_dt가 필요하다는 공식 규칙을 따른다. 수정주가·기업행위 비교가 검토되지 않은 자료를 검증 봉으로 승격하지 않는다.

## 초기·증분·품질·저장

- C12 `candidate_filter`의 analysis_candidate 코드만 기존 normalize_code로 연결한다. 환경·KRX·자료 종류·cutoff·종목 수 예산을 검사한다. 제외/보류·미매칭은 조회하지 않는다.
- 초기: 구조 검사 통과 253봉을 목표로 연속조회. 현재 세션은 명시한 `completed_through` 뒤의 봉으로 구분해 held 처리한다. 실제 완료 여부·휴장일·확정성은 날짜만으로 만들지 않는다.
- 증분: 기존 253봉 이상이면 최근 저장 5일을 겹쳐 조회하고 신규 날짜만 추가한다. 동일 응답 반복은 멱등적이다. 수정 1 증분은 아래 continuity 근거가 없으면 호출 전 중단한다. 수정 0도 C14 가격 비교에는 별도 기업행위 검증이 필요하다.
- namespace: 환경/합성 여부/API/코드/수정 여부/basis_id. namespace+거래일 PRIMARY KEY로 중복 방지. 잘못된 날짜/OHLC/거래량·0거래량(정지 가능)·미완료 세션은 held 또는 격리하고 채우지 않는다.
- 과거 OHLCV가 바뀌면 기존 view를 보존하고 historical_revision_requires_full_refresh로 중단한다. 명시적 `--full-refresh` 또는 새 basis_id로 재확보해야 한다. 원본 run/page 이력은 남고 전체 재조회 성공 시에만 해당 namespace의 현재 view가 교체된다.
- 전용 새 root의 daily-history.db만 소유 표식·스키마 확인 후 사용한다. 기존 임의 디렉터리/DB·data/·.git/·token-cache/·리다이렉트 경로를 거부한다. 트랜잭션과 기존 API 자원 잠금을 재사용하며 실패한 run의 부분 봉은 게시하지 않는다.
- runs/pages는 요청·응답 시각, 모듈·환경·출처·데이터 기준 시각, 호출/실패/품질/게시 상태를 저장한다. 로그·DB·원본 응답·검토 JSON은 Git 대상이 아니다. API 키·인증 헤더·토큰·서버 메시지는 보관하지 않는다.

`success`는 수집/staging 목표 달성이지 가격 확정 검증이 아니다. collector의 verified_bars는 항상 0이다. C14 전달에는 기존 C14-C ClosingEvidence, 실제 완료 세션 캘린더, 동일 basis_id의 가격 비교 근거가 필요하다. 근거가 없으면 JSON은 unavailable/null이다. 초기·증분의 서로 다른 요청/수신 시각을 원래 page/row fingerprint에 결합해 합치고, held 봉은 전달하지 않는다.

## 실행 절차

```powershell
# 1. 무호출 계획: 검토된 C12 subset과 명시적인 날짜·가격 기준 버전 사용
python -m kiwoom_collector.daily_supply_cli --c12-json candidates.json --base-date 20261027 --completed-through 2026-10-27 --environment mock --basis-id reviewed-price-v1

# 2. 합성 응답 실행: 공식 응답 모양의 replay fixture, 신규 전용 디렉터리
python -m kiwoom_collector.daily_supply_cli --c12-json demo-c12.json --base-date 20261027 --completed-through 2026-10-27 --environment mock --basis-id synthetic-adjusted --as-of 2026-10-28T10:00:00+09:00 --replay-json demo-replay.json --store-root new-demo-store --reviews-json demo-reviews.json
```

Replay schema는 `daily-supply-replay-v1`, responses 배열 각 항목은 request(api_id/stock_code/environment/base_dt/adjustment), continuation, reply(body/headers/http_status/request_started_at/response_received_at)다. 합성은 항상 synthetic_fixture이며 실제 서버 자료로 전환되지 않는다. 테스트의 source/replay helper가 재현 가능한 합성 입력을 만든다.

최초 **실수집은 이번에 실행하지 않았다**. 별도 승인 후 C12 subset 1종목·최대 2페이지/2호출부터 확인한다. 위 계획에 `--live --execute-approved --quota-reviewed --credentials-env-file <검토된 파일> --store-root <새 전용 경로> --max-stocks 1 --max-pages 2 --max-calls 2`를 사용한다. replay/과거 as-of를 함께 주지 않는다. 기존 유효 `KIWOOM_ACCESS_TOKEN`은 안전한 환경 주입만 허용하며 CLI 인자·로그·DB로 전달하지 않는다. 토큰이 없거나 401이면 중단하고, 이 도구는 OAuth를 발급하지 않는다. 공통 KiwoomClient의 pacing/timeout/redirect 금지를 재사용하고 이 전용 Session만 trust_env=False, TLS 검증 유지, HTTP 재시도 0으로 구성한다. 다른 모듈/PC와의 계정 전체 호출량은 사용자가 승인 창에서 조정해야 한다.

성공 후 호출 수·600/253 여부·날짜 방향·중복·정지/누락·수정 기준과 실제 수신 시각을 검토하고, 독립 근거를 확보한 후 C14 계산을 확인한다. 이 단계 전에는 숫자를 검증 완료 값으로 사용하지 않는다.

reviews JSON은 코드→검토 사전이다. C14-C 필드 외 scope는 environment/data_origin/api_id/stock_code/adjustment/basis_id를 정확히 결합하고 price_basis.id도 일치해야 한다. 수정 1 증분의 `continuity`는 source, available_at, basis_id, stock_code, environment, data_origin, api_id, adjustment, base_dt, previous_latest_day, corporate_actions_unchanged=true를 요구한다. 이 외부 검토를 자동 생성하지 않으며 불확실하면 전체 재조회/새 basis_id를 사용한다. 저장된 페이지는 SupplyStore.archives(records)로 읽어 기존 파서의 page/row fingerprint를 검토할 수 있다.

합성 검증은 `python -m unittest discover -s tests -p test_daily_supply.py`로 실행한다. 자동 주문·수집 스케줄 등록·운영 배포·main 병합은 포함하지 않는다.
