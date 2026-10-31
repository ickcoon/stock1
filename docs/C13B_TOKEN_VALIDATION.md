# C13-B 토큰 자동관리 검증 결과

2026-10-31 KST. 개발 사본: C:/Users/Public/Documents/ESTsoft/CreatorTemp/stock1-c13-dev.
기준 HEAD: 75bc5c8157291470af5d2614c140de1477c356fa.

## 결과

관련 합성/회귀 테스트 263개 PASS(53.682초), 전체 회귀 1회 실행 1,128개 PASS(126.101초).
실패·오류·건너뛰기 없음. 전체 수에 관련 테스트가 포함되므로 두 수를 더하지 않는다.
새 토큰 관리 전용 검사는 26개다. C13-B에서는 제품 코드를 수정하지 않고 App Key별 분리와 실제 expires_dt 보존 테스트 2개를 추가했다.
운영 stock1의 .env/DB/sidecar 등 보호 대상 7개 크기·mtime·SHA-256이 실행 전후 동일하다.
실제 OAuth·키움 API·주문 호출, 운영 DB의 SQLite 연결/쓰기, 운영 수집기/스케줄러 변경, Commit/Push 및 자동 배포는 하지 않았다.

## 검증 항목

| 항목 | 확인한 동작 |
|---|---|
| 최초 발급 | 토큰이 없을 때 기존 authenticate(attempts=1)를 호출 |
| 실제 만료 | 응답 expires_dt를 그대로 보존, 24시간을 임의 계산하지 않음 |
| 10분 경계 | 남은 시간 600초에서 갱신, 유효기간이 충분하면 재사용 |
| 장기 실행/만료 | 변경한 합성 시각에서 만료 후 새 토큰 발급 |
| 갱신 실패 | 일시적 통신 오류 최대 2회, 인증 오류는 즉시 중단, 대기 기간 적용 |
| 이전 토큰 | 실패·인증 거절 후 재사용하지 않음 |
| 복원 | 합성 토큰을 실제 Windows 사용자 DPAPI로 암호화하고 새 관리자 인스턴스로 복원, OAuth 0회 |
| 범위 | mock/real·App Key별 캐시/잠금 분리, Secret 회전의 범위 불일치 거부 |
| 동시 프로세스 | 독립 자식 프로세스 2개에서 발급 callback 합계 1회 |
| 저장 실패/손상 | 평문 fallback이나 무제한 재발급 없이 안전한 오류 반환 |
| OAuthProvider | 기본 단발 발급 계약 유지, auto_refresh=True만 관리자 연결 |
| 0198/0186 | 기존 수집·단발 호출 예산·인증 재조회 제한 및 이전 정상 회차 보존 |
| ka10001 | 승인된 주입 transport만 허용, 주문 endpoint 거부 |

전체 회귀는 개발 사본의 기존 의존성 사본을 가진 테스트 전용 가상환경에서 실행했다.
KIWOOM_* 변수를 제거하고 테스트의 Python 외부 통신과 운영 트리 접근을 차단했다. 브라우저 합성 테스트의 loopback은 허용했다.
실제 사용자 DPAPI에는 합성 값만 사용했고 저장 위치는 임시 테스트 디렉터리다. 실제 토큰/키는 읽거나 출력하지 않았다.
PC 재시작은 관리자·프로세스 재생성으로 가정했다. 실제 Windows 재부팅·서비스 계정 전환·실서버 장시간 검증은 하지 않았다.
로그와 해시 증거는 reports/c13b에만 보존하며 Git 업로드 대상이 아니다.

## 변경 파일 범위

기존 수정: .gitignore, kiwoom_collector/__main__.py, catalog_auth.py, client.py, errors.py, quotes.py, turnover_rank.py, tests/test_turnover_rank.py.
신규: kiwoom_collector/token_manager.py, token_cache.py, managed_info.py, tests/test_token_manager.py, docs/TOKEN_MANAGEMENT_C13.md, 이 보고서.
별도 개발 사본의 변경은 미커밋 상태로 보존한다. 운영 stock1은 기존 미추적 .venv-old/와 HOME 환경 문서 2개를 그대로 유지한다.

## 보호 저장소와 권한 정책

명시적 --protected-token-cache 사용 시 %LOCALAPPDATA%/Stock1/token-cache 아래 환경·앱 식별 hash별 .token-cache.bin을 저장한다.
사용자 DPAPI를 사용하며 LOCAL_MACHINE 옵션과 평문 fallback은 없다. App Key/Secret 원문은 저장하지 않는다.
디렉터리와 파일 ACL은 사용자 프로필의 기본 ACL을 상속한다. 별도 사용자 전용 DACL을 코드로 강제하지는 않는다.
따라서 사용자 지정 root는 같은 보호 수준의 로컬 사용자 디렉터리여야 하며 Git/공유 폴더를 지정하지 않는다.
실행 사용자 프로필이 로드돼 있어야 한다. 관리자 권한 자체가 배포 전제는 아니다.
캐시·관리자를 다른 환경/앱/Secret 범위에서 사용하면 거부한다. 손상/범위 불일치 캐시를 임의 자동 삭제하지 않는다.

## 호환성과 남은 운영 확인

기존 OAuth 발급 파서, Credentials의 환경별 호스트, 단발 OAuthProvider의 승인/발급 예산을 재사용했다. 기존 오류형은 KiwoomError 호환이다.
자동 갱신은 요청 직전 기준이다. 유휴 시점에 정확히 만료 10분 전을 맞추는 백그라운드 스레드/스케줄은 추가하지 않았다.
캐시 기본값은 비활성이다. 장기 collect에서 옵션을 켜야 재시작 복원과 프로세스 간 잠금이 활성화된다.

운영 전 반드시 확인할 사항:

1. 스케줄러 실행 계정의 프로필·실제 cache root ACL·DPAPI 복원과 시스템 시각. 실제 재부팅 후에도 같은 계정/경로로 실행되는지 확인한다.
2. 같은 App Key의 다른 PC/계정과 서버측 토큰 재발급 영향. 로컬 캐시는 PC 간 조율 및 응답을 잃은 발급의 서버측 처리 여부를 보장하지 않는다.
3. 원격 OAuth가 길어지면 다른 프로세스는 2초 후 cache_busy로 실패할 수 있다. 이는 중복 발급을 막는 정책이며 수집 회차 오류율을 운영 전 검증해야 한다. 최초 발급·갱신 실패·재부팅에 대한 실제 호출 검증은 별도 승인 범위다.

## GitHub Commit/Push 판단

테스트 관점에서는 관련 코드·테스트·문서의 커밋 검토가 가능하다. 운영 적용 및 실서버 안정성이 검증됐다는 의미는 아니다.
C13-B 검증 시점의 개발 사본 origin은 GitHub가 아니라 로컬 C:/Users/dada1/Downloads/stock1이었다. 이후 C13-C에서 GitHub 주소를 확인해 개발 사본의 origin만 변경한다. 상세는 HANDOFF_C13_TOKEN_MANAGER.md를 참고한다.
운영 폴더로 코드 복사·merge·자동 배포는 하지 않았다. C13-B 단계에서는 staging/Commit/Push 모두 하지 않았다.
