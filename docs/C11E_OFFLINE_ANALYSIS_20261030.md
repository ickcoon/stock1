# Step 7-C11-E 오프라인 분석 (2026-10-30, 집 PC)

## 동기화

- main: 43f97ba84687d1a185853b9ba986ce234e335a58 → 0799194f335c394b388e3eb5a266b6056d029444.
- 추적 파일 로컬 변경 없음, 원격 5개 커밋을 fast-forward로 반영.
- 미추적 .venv-old/, docs/HOME_ENV_STEP6D_PREPARATION.md,
  docs/HOME_MINIMAL_API_PREFLIGHT.md와 기존 로컬 환경은 덮어쓰거나 삭제하지 않았다.
- 인증정보·운영 DB·스케줄러를 변경하지 않았고 실제 API를 호출하지 않았다.

## 코드에서 확인한 사실

stock_catalog_live.ListTransport는 요청 JSON에 mrkt_tp 문자열을 그대로 전달한다.
collect는 '0'(KOSPI), '10'(KOSDAQ) 순서로 수집한다.
parse_row는 허용 필드를 문자열(최대 500자)로 확인한 후 marketCode를 요청값과
정확히 비교한다. strip, 숫자 변환, 0-padding 제거, 코드 매핑은 없다.

- 비문자열 marketCode: invalid_field_type_or_length. 따라서 인계된 mismatch만으로
  숫자형 응답을 원인으로 주장할 수 없다.
- 누락된 필드는 빈 문자열이 된다. 누락·빈 값·다른 코드·공백·padding 차이는
  모두 response_market_mismatch가 될 수 있다.
- 종목코드 및 종목명이 먼저 검증된다. mismatch는 이 선행 검사를 통과한 행이다.
- marketName은 보존하지만 marketCode와 교차 검증하지 않는다.
- upName/companyClassName은 시장 비교 뒤 단계에 사용되며 시장 동일성의 증거가 아니다.
- 한 행에서 예외가 나면 전체 수집이 중단되고 dataset 저장과 publish에 도달하지 않는다.
  실패 manifest에는 원래 행이나 시장 필드가 없어 당시 값을 복원할 수 없다.

## 공식 문서 확인

2026-10-30 확인:

- [키움 공식 명세 JSON](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json), 종목정보 리스트(ka10099).
- [키움 공식 Python 예제](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/main/examples/국내주식/종목정보/list_domestic_stocks.py).

요청 mrkt_tp는 필수 String(길이 2), 0=코스피, 10=코스닥으로 정의된다.
응답 marketCode는 선택 String(길이 20), 시장구분코드이며 코드표/대응 규칙은 비어 있다.
marketName도 선택 String(길이 20)이다. 필드가 선택이라는 사실이 안전한 게시를 위해
필요한 시장 확인을 생략할 근거는 아니다.

명세의 요청 예제는 0이지만 응답 예제는 10/코스닥이며 동일한 종목코드가 반복된다.
예제 자체에 일관성 문제가 있으므로 다른 코드 체계, 0→10 매핑, 실제 시장 혼입,
서버 오류 중 어느 것도 이 예제로 확정하지 않는다. 공식 Python 예제는 mrkt_tp를
그대로 전달하고 응답을 표로 모으며 시장 동일성을 검증하거나 별도로 매핑하지 않는다.

## 결론과 수정 방향

당시 실제 반환값이 없으므로 원인은 미확정이다. 현재 확인한 공식 근거만으로는
정규화나 매핑을 도입할 수 없다. 검증 조건과 운영 코드는 이번 분석에서 유지한다.

다음 구현은 오류 진단 보존을 우선한다. 허용 목록 기반으로 요청 mrkt_tp,
시장별 페이지 번호, 실패 행 인덱스, marketCode의 존재 여부·타입·원본 문자열,
marketName, upName, companyClassName만 제한된 길이로 기록하는 방안을 권고한다.
행 전체·헤더·인증정보·토큰·연속키·임의 오류 문자열은 기록하지 않는다.
길이 초과/비문자열 값은 타입·길이 등 메타데이터로만 나타내고 원본 객체를 직렬화하지 않는다.
진단 저장 실패가 DB 게시나 재시도로 이어지지 않도록 해야 한다.

그 뒤 승인된 실제 실행에서 확보한 값과 명세 근거를 비교하여 정확한 허용 매핑 및
시장명 교차 검증 여부를 결정한다. 시장명이나 종목 이름 추정만으로 통과시키지 않는다.
추가 합성 검증은 잘못된 시장 혼입 시 전체 게시 차단, 진단 민감정보 배제,
진단 IO 실패 시 미게시·무재시도, 기존 호출 횟수 보존을 포함해야 한다.

## 이번 검증

tests/test_catalog_market_contract.py에 합성 특성 확인 테스트 4개를 추가했다.
정상 두 요청 코드·원본 필드 보존, 다른 시장/누락/빈 값/padding/공백 거절,
비문자열 선행 거절, 현재 시장명 미교차검증을 확인한다. 실제 응답 재현 자료가 아니다.
신규 4개 테스트 PASS. 기존 전체 회귀 1,047개도 123.293초에 OK.
전체 회귀 로그는 OS 임시 폴더 stock1-c11e-baseline.log에 보관했다.
PowerShell의 stderr 리다이렉션은 NativeCommandError/종료코드 1로 표시되었지만,
unittest 최종 결과는 Ran 1047 tests / OK임을 로그에서 확인했다.
catalog_real --help도 정상 종료했다. 실제 수집 승인 플래그는 사용하지 않았다.
