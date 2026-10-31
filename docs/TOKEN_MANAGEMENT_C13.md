# C13 토큰 자동관리 — 개발 사본 구현

2026-10-31. 기준 커밋 75bc5c8157291470af5d2614c140de1477c356fa.
작업 위치는 C:/Users/Public/Documents/ESTsoft/CreatorTemp/stock1-c13-dev이며 운영 stock1 트리를 수정하지 않았다.
실제 OAuth/키움 API/주문 호출·운영 DB 접근/수정·수집기/스케줄러 중단·전체 회귀 테스트·Commit/Push는 수행하지 않았다.
원본 stock1 HEAD는 75bc5c8이고 기존 미추적 3개만 남아 있다. 기존 C12 wrap-up의 보호 파일 7개(.env/DB/sidecar) 기록과 크기·mtime·SHA-256이 동일함을 읽기 전용으로 확인했다. 운영 DB에 SQLite 연결은 열지 않았다.

## 기존 구현과 재사용

기존 KiwoomClient.authenticate는 OAuth 응답의 expires_dt를 KST 시각으로 해석하며 Token.value는 repr에서 숨긴다.
0198은 만료 30초 전 갱신하고 HTTP 401/기존 인증 오류 코드에 한해 발급 후 1회 재조회하는 기능이 이미 있었다. 토큰은 메모리에만 저장됐다.
catalog_auth.OAuthProvider는 의도적으로 발급 1회/재시도 없음인 독립 수집 도구다. 기본 계약을 유지하고 auto_refresh=True일 때만 공통 관리자를 사용하게 했다.
Credentials의 mock/real 호스트 선택도 그대로다. 새로운 OAuth 발급 요청이나 expires_dt 파서를 중복 구현하지 않았다.

## 변경 파일

- token_manager.py(신규): 만료 검사·갱신·제한 재시도·대기·토큰 폐기.
- token_cache.py(신규): 사용자 DPAPI 암호화·원자적 저장·프로세스 잠금. 기존 api_resource 잠금을 재사용한다.
- client.py/errors.py: 공통 ensure_token과 안전한 실패 분류, 0198/장기 수집 연결.
- catalog_auth.py: 기존 OAuthProvider에 선택적 자동관리 연결 및 범위 검사.
- quotes.py/turnover_rank.py: 공통 ensure_token 재사용, 401 등 인증 거절 시 토큰 폐기. 단발 조회의 재조회·발급 예산은 늘리지 않는다.
- managed_info.py(신규): 기존 market_cap.IndependentClient에 주입 가능한 ka10001 전용 transport. 기존 오프라인 파서와 CLI는 그대로다.
- __main__.py: collect 전용 --protected-token-cache. test-now와 함께 사용하면 인증 전에 거부한다.
- .gitignore: 보호 캐시 파일 제외. tests/test_token_manager.py 및 test_turnover_rank.py에 관련 검증을 추가했다.

## 갱신 정책

장기 0198 수집은 각 요청 전에 검사하며 남은 시간이 10분 이하면 갱신한다. 만료됐거나 토큰이 없을 때도 같은 경로로 발급한다.
실제 기준은 응답 expires_dt다. 발급 시각+24시간을 임의 계산하거나 짧은 만료 응답을 연장하지 않는다.
백그라운드 타이머나 장외 OAuth 호출을 추가하지 않았다. 유휴 상태에서는 발급하지 않고 다음 요청 직전에 갱신한다.
유효한 토큰은 재사용하며, 갱신 후에도 요청에 필요한 유효기간이 없으면 실패한다.
수동 authenticate는 원래대로 명시적 발급 동작이다. 자동 재사용이 필요한 경로는 ensure_token 또는 managed OAuthProvider를 사용한다.

기본은 메모리 관리다. 장기 collect에서 --protected-token-cache를 명시한 경우에만 디스크 캐시를 사용한다.
0186는 기존 독립 live-test의 1회 발급/조회 무재시도 규칙을 유지한다. 프로그램에서 동일 범위의 TokenManager를 주입하거나 enable_token_cache를 명시할 수 있다. 페이지 시작 후 토큰이 부족해지면 추가 발급으로 예산을 우회하지 않고 실패한다.
ka10001은 승인된 외부 실행기에서 ManagedInfoTransport를 기존 IndependentClient에 주입한다. 요청은 ka10001 + /api/dostk/stkinfo + stk_cd만 허용하며 주문 endpoint와 조회 재시도는 제공하지 않는다.

## 재시작·보호 저장·동시 실행

기본 위치: %LOCALAPPDATA%/Stock1/token-cache. 환경·앱 식별 hash별 .token-cache.bin 파일과 잠금 파일을 사용한다.
JSON 전체를 Windows CryptProtectData로 암호화한 뒤 디스크에 쓰며, LOCAL_MACHINE 옵션이나 평문 fallback은 없다. App Key/Secret 자체는 저장하지 않는다.
환경·앱·Secret에 대한 binding을 암호문 안에서 검증한다. 캐시/관리자를 다른 Credentials 범위에 연결하면 거부한다.
같은 Windows 사용자·사용자 프로필에서 재시작하면 캐시를 복원한다. PC/사용자 변경 또는 Secret 회전 후의 범위 불일치는 자동 덮어쓰기 대신 실패한다. 운영 전 해당 사용자 소유의 이전 캐시 처리 절차를 별도로 확인해야 한다.
다른 사용자/PC 또는 다른 cache root 사이에는 잠금을 공유하지 않는다. 같은 앱의 모든 장기 프로세스는 같은 사용자와 기본 cache root를 사용해야 한다.
보호 캐시를 켠 경우 OS 파일 잠금 안에서 다시 읽고 필요한 경우에만 발급/저장한다. 대기 한도 2초이며 잠금 파일은 OS가 프로세스 종료 시 해제한다. 오래된 lock 파일을 임의 삭제하지 않는다.
발급 전 60초 in-flight 표식을 암호화해 기록한다. 발급 직후 프로세스가 종료돼도 즉시 연속 발급하지 않게 한다.
오래된 요청의 인증 실패가 다른 프로세스에서 발급한 최신 토큰을 지우지 않도록 토큰값 일치 조건으로 폐기한다.
암호화된 임시 파일을 flush/fsync 후 replace하며, 손상·복호화 실패·저장 실패는 원문 없이 고정 오류로 중단한다.
표준 사용자 DPAPI면 충분하며 관리자 권한을 배포 조건으로 삼지 않는다. 실행 계정의 사용자 프로필이 로드돼 있어야 한다.

## 실패 처리와 데이터 보존

- 네트워크/시간 초과·일시적 서버/호출 제한: raw 발급 시도 최대 2회, 중간 대기 기본 1초. Retry-After 숫자 최대 60초를 존중하며 그보다 긴 대기 요구는 즉시 중단한다.
- TLS·인증 거절·응답 형식/만료 오류: 무작정 재시도하지 않는다. 예외/서버 메시지/인증 헤더 원문은 로그에 넣지 않는다.
- 최종 실패 시 토큰을 사용하지 않고 대기 상태를 기록한다. 일반 통신 장애는 60초, 인증 등 장애는 300초 후 다음 요청에서 다시 판단한다. 수명/저장 검증 도중 중단되면 in-flight 표식의 60초 대기를 유지한다.
- 0198 조회의 인증 거절은 기존대로 새 토큰으로 한 번만 재조회한다. 또 실패하면 300초 대기하며 같은 토큰으로 반복 요청하지 않는다.
- 0186/시세/ka10001의 단발 경로는 인증 거절 시 토큰만 폐기하고 조회를 재실행하지 않는다.
- 기존 collector._attempt의 실패 기록을 재사용한다. 이전 정상 회차는 바꾸지 않으며 현재 실패 회차는 error로 남긴다. 실패한 불완전 페이지를 성공 자료로 저장하지 않는다.
- 현재 API pacing은 기존 클라이언트 단위다. 캐시 잠금은 OAuth 중복 방지이며 전체 업무 API의 프로세스 간 호출량 제한을 대신하지 않는다.

## 합성 검증

최종 관련 테스트 261개 PASS(53.942초). 새 C13 테스트 24개를 포함한다. 전체 회귀 테스트는 실행하지 않았다.
최초 발급/유효 재사용/10분 경계/만료 후 갱신/실패·대기/재시작 복원/환경·Secret 범위/실제 두 자식 프로세스 동시 발급 1회/DPAPI 암호문/캐시 손상/이전 정상 회차 보존/주문 endpoint 거부를 확인했다.
실제 사용자 DPAPI에는 합성 값만 사용했고 모든 캐시는 TemporaryDirectory에 한정했다. 제한 실행 계정에서 DPAPI 오류 2가 나 정상 사용자 실행 환경에서 같은 합성 테스트를 검증했다. 실제 자격증명·토큰은 사용하지 않았다.
기존 0186 오류 테스트는 독립 사례 간 401로 폐기된 토큰을 다시 재사용하지 않도록 사례별 초기 상태를 분리했다. 추가 검사로 인증 거절 후 재요청이 0회인 것을 확인한다.
개발용 가상환경은 기존 설치 패키지의 사본으로 구성했다. 인터넷 패키지 설치를 하지 않았다.
실행 로그/테스트 실행기는 개발 사본 reports/c13에만 남기며 Git 대상이 아니다.

## 운영 적용 전 확인

1. 별도 단계에서 전체 회귀 테스트와 코드 검토를 완료한다. 운영 stock1에 이번 코드를 자동 덮어쓰지 않는다.
2. 병원 수집기 실행 사용자와 작업 스케줄러의 사용자 프로필, DPAPI 저장/복원 권한, 시스템 시각(KST/동기화)을 확인한다.
3. 같은 App Key의 새 토큰 발급이 다른 PC/프로세스 토큰에 미치는 서버 정책은 로컬 캐시로 보장할 수 없다. 별도 앱/키 또는 명시적 계정 운영 정책을 확인한다. 기존 수집 중인 프로세스는 메모리 토큰만 가지므로 새 캐시 사용 프로세스를 병행 투입하지 않는다.
4. 그 이후 실제 OAuth/API 검증은 별도 승인 범위로 제한한다. 이번 단계는 실서버 24시간 지속성·서버측 폐기 동작·재부팅 후 스케줄러 계정 복원을 실측한 것이 아니다.

## 최소 배포 절차 — 이번에는 실행하지 않음

검토·전체 회귀를 마친 커밋을 배포 대상으로 확정한다. 기존 DB·.env·스케줄 설정을 보존하고 계획된 배포 창에서 코드만 적용한다. 실행 중 수집기를 중단하거나 파일을 덮는 작업은 이번 범위가 아니다.
정상 실행 사용자에서 합성 DPAPI 검증을 먼저 하고, 실제 서버 검증 승인 후 장기 collect 실행 명령에만 --protected-token-cache를 적용한다. 기존 --schedule/DB 인수/수집 시각은 유지한다.
0186와 ka10001 실행기가 있는 경우 같은 Credentials 범위·캐시 root를 사용하는 관리자를 명시적으로 연결한다. 단발 테스트 CLI에 자동 재시도나 숨은 OAuth를 추가하지 않는다.
점검 시에는 토큰값 대신 환경·만료 시각·갱신 성공/실패 category·수집 성공률만 확인한다. 캐시·키·로그·DB를 Git이나 다른 PC로 복사하지 않는다.

공식 근거: [키움 OAuth 명세](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json), [Windows DPAPI](https://learn.microsoft.com/en-us/windows/win32/api/dpapi/nf-dpapi-cryptprotectdata).
