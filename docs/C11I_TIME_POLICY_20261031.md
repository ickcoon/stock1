# C11-I — 종목 마스터 단발 실행 시간 정책

## 원인과 범위

기존 stock_catalog_live.after_close는 날짜와 무관하게 KST 16:00~23:49만 허용했다.
2026-10-30 23:57 차단은 OAuth/조회 전에 적용된 이 로컬 정책의 결과다.
DB 오류나 키움 서버가 반환한 서비스 불가 응답이 아니다.
이 가드는 운영 수집기의 장중 부하와 겹치지 않도록 보수적으로 만든 것이며,
거래소 달력이나 REST 서버 점검표를 구현한 것이 아니었다.

## 기본 정책과 설정

catalog_time_policy.py와 config/catalog_time_policy.json으로 분리했다.

- 평일: KST 16:00 이상 자정 전까지. 23:57도 허용한다.
- 토/일: 기본 하루 종일 허용. allow_weekends=false로 별도 차단 가능하다.
- 평일 휴장일: verified_holidays에 사용자가 확인한 YYYY-MM-DD를 명시한 날짜만 종일 허용.
  비어 있는 기본값에서 공휴일/임시휴장/대체휴일을 추측하지 않는다.
- maintenance_windows: 매일 반복되는 KST [시작,종료) 차단 구간. 날짜/요일 허용보다 우선한다.
  ["23:30","01:00"]처럼 자정을 넘길 수 있다. 시작=종료는 설정 오류다.
- weekday_start는 16:00 이후로만 조정 가능하다. 알 수 없는 키, 잘못된 날짜·시간·타입,
  읽을 수 없는 설정은 invalid_catalog_time_policy로 차단하며 느슨한 기본값으로 대체하지 않는다.

기본 verified_holidays와 maintenance_windows는 비어 있다. 이는 확인된 휴장·점검
일정이 등록되지 않았다는 뜻이며, 공식적으로 점검이 없거나 서비스가 상시 가능하다는 뜻이 아니다.
특별장 일정이나 공식 공지 확인 결과는 별도 검토한 설정 파일에 반영한다.
--time-policy PATH로 파일을 선택할 수 있다. 현재 시각은 실제 시스템 UTC에서 읽어
KST로 변환한다. --now 또는 시간 검증 무시 옵션은 없다.

설정 예시(점검 시간과 휴장일은 아래 예제에 넣지 않음):

```json
{
  "schema_version": "catalog-time-policy-v1",
  "weekday_start": "16:00",
  "allow_weekends": true,
  "verified_holidays": [],
  "maintenance_windows": []
}
```

## 공식 서비스 시간과의 구분

2026-10-31 [키움 REST 공식 포털](https://openapi.kiwoom.com/main/home),
[공식 REST 소개](https://openapi.kiwoom.com/intro?dummyVal=0),
[공식 ka10099 명세 JSON](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json)을
검토했다. 이번에 확인 가능한 자료로는 ka10099/OAuth의 확정적인 주말 가동 보장이나
고정된 REST 점검 구간을 확인하지 못했다. 따라서 임의의 정기 점검 시간을 하드코딩하지 않는다.
[키움의 일반 서비스 안내](https://bbn.kiwoom.com/bbs/VBbsBoardNMMWZView)에 있는
HTS/WTS 점검과 원장 조회 시간도 REST ka10099의 점검표로 전용하지 않는다.

로컬 허용은 서버 이용 가능 보장이 아니다. 실행 전 최신 REST 공지를 확인하고 알려진
점검 구간을 설정한다. 원격 서버 장애·임시 점검은 오프라인 시각 검사로 확정할 수 없다.
원격 HTTP/API 실패는 기존 http_failed/api_failed_or_missing_code 등으로 보고하며
근거 없이 시간 정책 오류로 바꾸거나 자동 재호출하지 않는다.

## 재검사와 보존한 보호 조건

인증 전 최초 정책 검사, 키 입력 후 OAuth 직전 재검사, 실제 각 조회 직전 재검사를 한다.
키 입력 대기나 연속조회 도중 차단 구간에 진입하면 추가 요청을 중단한다.
알려진 점검은 catalog_configured_maintenance_window, 평일 장중은
catalog_weekday_after_close_required, 주말 설정 차단은 catalog_weekend_disabled다.
인증 전 차단은 oauth_calls=actual_api_calls=0이며 DB 게시도 하지 않는다.
OAuth 이후 차단이면 OAuth 1회/조회 0회일 수 있다.

실전 키와 운영 mock 키 분리, 숨김 입력, 명시적 승인, 자원 잠금, OAuth 최대 1회,
자동 재시도 0회, 시장별 최대 50페이지, 조회 시작 간격 1.2초, 수집 단계 300초,
중복·누락 검증과 전체 완료 후 원자적 게시를 유지한다. 스케줄러 시간 정책은 바꾸지 않았다.
기존 수집 날짜 경계 검증도 유지하므로 자정을 넘긴 수집은 collection_crosses_day로
중단될 수 있다. 늦은 밤 시작이 당일 내 완료를 보장하지 않는다.

## 주말 실행 명령 — 별도 실제 호출 승인 후에만

기존 유효한 개발 DB와 별도 실전 키가 준비되어 있고 REST 점검 공지가 없는 경우,
기본 설정으로 토/일 낮 시간에 다음 전체 수집 명령을 사용할 수 있다:

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.catalog_real --execute-approved --oauth-approved --quota-reviewed
```

이는 연속조회 후 개발 DB에 게시하는 명령이다. 첫 페이지 1회만 진단하려면
--diagnose-once를 추가한다(게시 없음). --time-policy를 생략하면 기본 정책을 읽는다.
실패 시 자동 재실행하지 않는다. 이번 개발에서는 어떤 실제 OAuth/ka10099 호출도 하지 않았다.

## 합성 검증

평일 15:59/16:00/23:57/23:59 경계, 토·일 및 UTC→KST 변환, 명시적 합성 휴장일,
자정 경유 점검과 종료 경계, 잘못된 설정 차단, 인증 전 0회 차단,
키 입력 대기 후 OAuth 차단, 조회 직전 차단과 호출 수 보존을 테스트한다.

전체 회귀 1,073개 PASS (113.872초, 종료코드 0).
초기 회귀에서 실제 오늘이 토요일로 바뀌며 기존 스케줄러 테스트 3개가 실패했다.
이 테스트의 가짜 실행 시각을 명시적인 합성 월요일로 고정했고 스케줄러 구현은 변경하지 않았다.
최종 로그는 로컬 CreatorTemp/stock1-c11i-regression-final.log에 보관하며 Git에 포함하지 않는다.
