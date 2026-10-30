# Step 7-C11-F — 시장 검증 및 제한된 진단

## 결론

실제 병원 실패 원인은 아직 미확정이다. 당시 원본 marketCode가 없으며 실제 재호출은 하지 않았다.
2026-10-30 공식 명세 재확인 결과는 C11-E와 같다:
[키움 공식 명세 JSON](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json).
mrkt_tp는 필수 문자열 0=코스피/10=코스닥이다. marketCode는 선택 문자열이며
응답 코드표·요청값 대응 규칙이 명시되어 있지 않다. 요청 0/응답 10 예제의 불일치를
근거로 두 값을 같은 시장으로 취급하지 않는다.

## 검증 규칙

정규화는 현재 항등 변환만 허용한다. 요청/응답의 정확한 문자열 일치를 유지한다.
공백 제거, 숫자형 문자열 변환, 0-padding 제거, 시장명 기반 우회는 하지 않는다.
명세상 선택 필드여도 시장을 확인할 수 없는 누락/빈 값은 게시하지 않는다.
이 변경은 당시 시장 오류를 해결했다고 주장하지 않으며, 원인 확인에 필요한 안전한
진단 경로를 보완한다. 응답 코드를 숫자형으로 바꾸는 서버 동작도 허용하지 않는다.

## 실패 진단

행 검증 실패 시 기존 manifest의 market_diagnostic에 다음 네 항목만 추가한다:

- request_market: 요청 문자열 0 또는 10
- response_market_counts: 실패한 페이지 전체의 코드별/고정 분류별 행 수
- page_no: 시장별 1부터 시작하는 페이지 번호
- failure_stage: 고정 문자열 row_validation

정확한 0/10은 그대로 집계한다. 그 밖의 값은 missing, empty, non_string,
ascii_whitespace, zero_padded, other, invalid_row 중 하나로 집계한다.
ascii_whitespace는 ASCII 공백/탭/CR/LF 제거 시 0/10이 되는 값,
zero_padded는 00/000/010만 분류한다. 이는 진단용 분류이며 통과시키는 정규화가 아니다.
알 수 없는 코드 문자열도 그대로 저장하지 않으므로 필드에 비밀정보가 들어 있어도
진단으로 출력되지 않는다. 원본 행·종목코드·이름·업종·시장명·키·토큰·헤더·연속키는
새 진단에 포함하지 않는다. 기존 manifest의 실행 메타데이터는 유지한다.
페이지 단위 집계이며 앞 페이지 누적이나 전체 시장 통계가 아니다.
HTTP/API/봉투 검증 실패는 기존 reason으로 보고하며 행 시장 집계를 생성하지 않는다.

두 시장 수집과 dataset staging이 끝난 후 한 트랜잭션으로 게시하는 정책은 유지한다.
어느 페이지의 행 검증 실패든 publish에 도달하지 않는다. 기존 마스터 데이터는 보존한다.
진단 manifest 저장 실패도 성공으로 처리하지 않으며 재시도하지 않는다.

## 승인 후 단일 호출 진단 (이번에는 실행하지 않음)

프로젝트 루트의 PowerShell에서, 별도 승인 후 다음 명령을 한 번 실행한다:

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.catalog_real --diagnose-once --execute-approved --oauth-approved --quota-reviewed
```

이 모드는 OAuth 최대 1회, ka10099 KOSPI 첫 페이지 최대 1회, 자동 재시도 0회다.
cont-yn=Y여도 다음 페이지를 조회하지 않으며 KOSDAQ도 호출하지 않는다.
원본 응답/dataset 저장 및 publish는 수행하지 않는다. 이 명령을 일반 collect로 바꾸면
두 시장 연속조회가 가능하므로 단일 호출 검증에는 반드시 --diagnose-once를 사용한다.

사전 조건: 한국시간 16:00 이상 23:50 미만, 기존 개발용 diagnostics/master/c10-master.db가
유효하고 synthetic=False일 것, 기존 운영 mock 키 확인 경로가 유효할 것,
별도 실전 App Key/Secret Key를 숨김 입력할 수 있을 것. 키/DB를 자동 생성하거나
보호 조건을 우회하지 않는다. 경로가 없는 경우 API 호출 전에 중단하는 것이 정상이다.
기존 자원 잠금과 고유 실행 폴더/manifest 생성은 수행하지만 DB 게시/내용 수정은 없다.

검증 조건: oauth_calls<=1, query_attempts=actual_api_calls=1(요청까지 도달한 경우),
automatic_retries=0, database_updated=false. 성공은 diagnostic_complete이며 첫 페이지의
검증 성공만 뜻한다. 전체 목록 완전성/DB 갱신 성공을 뜻하지 않는다.
실패하면 reason 및 위 네 진단 항목만 검토하고 자동 재실행하지 않는다.
실행 전후 개발 DB SHA-256을 별도로 비교한다. 진단값이 부족하더라도 원문 출력이나
허용 목록 확장은 별도 검토 없이 하지 않는다.

## 오프라인 검증

관련 catalog 테스트 56개 PASS. 합성 fixture로 정상 두 시장, 불일치, 누락,
혼합 페이지, 타입/공백/padding, 비밀 문자열 배제, 나중 시장 실패 시 기존 DB 바이트
보존, 단일 조회 제한과 연속조회 차단, 진단 저장 실패를 검증했다.
전체 회귀 1,055개 PASS (116.066초, 프로세스 종료코드 0).
로그: 로컬 CreatorTemp/stock1-c11f-regression.log (Git 제외).
실제 OAuth/ka10099 호출은 수행하지 않았으며 테스트의 실전 경로는 가짜 provider/transport로 검증했다.
