# C11-D 최초 종목 수집 최소 운영 절차

작성: 2026-10-30 KST. 절차 검토만 수행했으며 아래 중단·시작·API 명령은 실행하지 않았다.

## 확인한 상태와 근거

- 운영 프로세스 27380 → 21896, 실행 파일은 운영 stock1 Python이다. 부모 4396은 explorer다.
  두 PID는 18:36 KST 재조회에서도 존재했다. PID 존재는 최근 API 수집 성공의 증거가 아니다.
- 예약 작업 STOCK1 Daily Collector는 Ready, 마지막 결과 1, 다음 실행은 2026-11-02 09:10이다.
  IgnoreNew, RestartCount=0, StartWhenAvailable=False, AllowHardTerminate=True다.
- 기존 작업 실행 파일은 `C:\Users\dada1\Downloads\stock1\.venv\Scripts\python.exe`,
  인수는 `-m kiwoom_collector collect --schedule`, 작업 폴더는 운영 stock1이다.
- 운영 config는 기존 .env를 읽되 상속 환경변수를 우선한다. 신규 설정·키 파일을 만들지 않는다.
- 운영 KiwoomClient는 최초 token=None, 발급 응답을 인스턴스 메모리에 저장한다.
  조회 시 유효 토큰을 재사용하고 만료 임박·인증 오류에는 재발급할 수 있다.
  디스크 공유 토큰 캐시는 없으며 close는 HTTP session을 닫고 토큰 폐기를 호출하지 않는다.
- __main__.py는 KeyboardInterrupt에 종료 코드 130을 반환하고 finally에서 client/store/잠금을 정리한다.
  Stop-Process, taskkill, 예약 작업 끝내기는 이 정상 종료 처리를 보장하지 않는다.
- [키움 공식 인증 구현](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/core/auth.py)과
  [공식 안내](https://openapi.kiwoom.com/m/guide/index?dummyVal=0)를 조사했으나
  동일 App Key 재발급 시 이전 토큰 유지/무효화에 대한 명시적 공식 보장은 찾지 못했다.
  별도 revoke API가 있다는 사실만으로 재발급이 무해하다고 추론하지 않는다.

## 승인 후 순서

1. 장 종료 후 16:00~23:49 KST, 다음 트리거와 충분히 떨어진 유지보수 시간을 선택한다.
   같은 App Key를 사용하는 다른 프로그램도 없음을 확인한다. 이번 절차는 스케줄러 설정을 바꾸지 않는다.
2. 운영자가 원래 수집기 실행 콘솔을 확인하여 Ctrl+C를 1회 보낸다. 종료 메시지와 프로세스 소멸을 확인한다.
   콘솔을 찾을 수 없거나 Ctrl+C로 종료되지 않으면 수집을 보류한다. 강제 종료는 이 절차에 포함하지 않는다.
   현재 프로세스와 예약 작업의 소유 관계는 확정되지 않았으므로 스케줄러 '끝내기'를 대신 사용하지 않는다.
3. 기존 두 PID의 소멸뿐 아니라 운영 stock1의 collect 실행이 다른 PID로 남거나 재생성되지 않았는지 확인한다.
   PID는 실행 시 다시 확인한다. 잠금 파일이 남아 있어도 삭제하지 않는다. 예약 작업이 Running이면 진행하지 않는다.
   같은 키를 쓰는 다른 클라이언트나 자동 재시작 경로를 배제하지 못하면 보류한다.
4. stock1-dev에서 아래 무통신 설정 점검 후 수집 명령을 딱 한 번 실행한다.
   기존 모의 .env를 읽고 별도 메모리 토큰을 최대 1회 발급한다. 종료된 운영 인스턴스의 토큰은 재사용하지 않는다.
5. 성공/실패와 무관하게 CLI가 종료되고 네트워크 연결이 닫혔는지 확인한다.
   manifest, 종료 코드, 개발 DB의 게시 결과를 확인한다. 실패 시 자동 재시도·실전 전환은 하지 않는다.
6. 운영 collect가 0개이고 종목 수집도 종료된 뒤 아래 기존 예약 작업 시작 명령을 한 번 실행한다.
   수동 Python을 병행하지 않는다. 실패하면 반복 기동하지 않고 오류를 보고한다.
7. Running, 작업 실행 계정·환경, 새 프로세스 트리를 확인한다. Python 실행 래퍼와 자식 2개는
   수집기 2개가 아니므로 단일 collect 인스턴스 여부로 판단한다.
   다음 거래일 09:20의 실제 예정/시작 시각과 success, TOP20 실제 행 수를 기존 로그나 승인된
   읽기 전용 진단으로 확인한다. 예약 작업 Running이나 09:10 기동만으로 수집 성공을 선언하지 않는다.

## 승인 이후 사용할 명령 — 현재 실행하지 않음

```powershell
Set-Location C:\Users\dada1\Downloads\stock1-dev
.\.venv\Scripts\python.exe -B -m kiwoom_collector.stock_catalog auth-check --credentials-env-file C:\Users\dada1\Downloads\stock1\.env --environment mock
# 앞선 정상 종료 및 완전 종료 확인을 모두 마친 경우에만:
$catalogRunName = 'c11d-mock-' + (Get-Date -Format 'yyyyMMdd-HHmmss')
.\.venv\Scripts\python.exe -B -m kiwoom_collector.stock_catalog collect --db C:\Users\dada1\Downloads\stock1-dev\diagnostics\master\c10-master.db --environment mock --credentials-env-file C:\Users\dada1\Downloads\stock1\.env --name $catalogRunName --execute-approved --quota-reviewed --oauth-approved --token-impact-reviewed --max-pages 50 --seconds 300
$catalogExitCode = $LASTEXITCODE
# 성공/실패를 기록하고 CLI 종료를 확인한 뒤 반드시 운영 복구 단계 수행:
Start-ScheduledTask -TaskName 'STOCK1 Daily Collector'
Get-ScheduledTask -TaskName 'STOCK1 Daily Collector' | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName 'STOCK1 Daily Collector' | Select-Object LastRunTime,LastTaskResult,NextRunTime
```

명령은 단계별로 실행한다. 전체 블록을 일괄 실행하거나 승인 플래그만으로 종료 확인을 대체하지 않는다.
계정·작업 권한에 따라 작업 시작에 관리자 권한이 필요할 수 있다. 설정 변경 권한은 요구하지 않는다.

현재 --max-pages는 **시장별** 한도다. 전체 100페이지 상한을 지키기 위해 50을 사용한다.
한 시장이 50페이지를 넘으면 실패하고 기존 개발 마스터를 보존한다. 자동 재시도 0회,
OAuth 최대 1회, ka10099 조회 최대 100회, 다른 조회·주문·토큰 폐기 호출은 없다.
--seconds 300은 수집 단계 제한이며 OAuth·DB 게시까지 포함한 전체 프로세스 300초를
보장하지 않는다. 전체 실행 300초가 반드시 필요하면 현 CLI로 충족할 수 없으므로 실행 보류한다.
시간 초과를 이유로 운영/개발 DB에 접근하는 프로세스를 임의 강제 종료하지 않는다.

개발 마스터만 갱신한다. 운영 수집기의 정상 재시작 자체는 기존 설계대로 운영 DB를 열고
다음 예정 시각에 정상 수집 데이터를 저장한다. 운영 DB에 대한 수동 변경은 하지 않는다.

## 결과 확인과 복구 조건

- diagnostics/catalog_runs/<실행이름>/manifest.json, dataset.json, changes.json에서 실제 환경 mock,
  data_origin=api_response, 호출 횟수, 종료 상태, 두 시장별 수·전체 고유 수·상품 유형·분류/미분류를 확인한다.
- 두 시장 전체 페이지 및 검증 성공 후 단일 트랜잭션으로 게시한다. 검증 실패 시 기존 마스터가 유지된다.
  보고서 쓰기 실패 상태는 DB 게시 성공 여부와 분리해 판단한다.
- 중복 코드·시장 불일치·응답 오류·기존 수작업 태그 보존은 게시 검증과 changes에서 확인한다.
  API 성공만으로 거래소 전체 상장목록 대비 무누락을 입증하지는 않는다.
- 종목 수집 토큰이 서버에 남아도 사용 프로세스는 종료되어 있다. 새 운영 프로세스는 token=None으로
  시작하여 첫 실제 조회 시 인증한다. 별도 auth 명령은 다른 프로세스의 메모리 토큰을 준비하지 못하므로 생략한다.
- 2026-11-02 09:20은 현재 코드의 주말 제외 기준 다음 슬롯이다. 거래소 휴장·특별 일정은 실행 전 확인한다.
  과거 09:20~09:50 슬롯을 재생하거나 --test-now/once로 채우지 않는다.
- 기존 작업은 Interactive이며 로그인 의존성·전원/절전 설정 문제는 이번 일회성 절차로 해결되지 않는다.
  다음 거래일에는 dada1 로그인 유지·절전 방지 상태를 확인한다. 설정 변경은 별도 승인 범위다.

## 모의 미지원 시 실전 사용 조건

일반 인증 실패·호출 제한을 미지원으로 단정하지 않는다. 명세/고객지원으로 미지원이 확인된 뒤,
기존 실전 설정 파일을 지정하고 환경 real·별도 실행 이름을 사용한다. 키를 새 파일에 복사하지 않는다.
실전 계좌 주문은 하지 않고 같은 ka10099 두 시장만 조회한다. 실전 사용과 추가 OAuth·조회는
별도 승인이 필요하다. 이때도 운영 복구 전에 단독 수집을 마치거나 같은 종료 절차를 다시 거친다.

이번 승인 요청 범위: 정상 종료가 가능한 원래 콘솔 확인 후 운영 수집기 일시 중단,
위 한도 내 mock 최초 수집, 성공/실패와 무관한 기존 예약 작업을 통한 복구.
정상 종료 수단을 찾지 못하면 중단하지 않고 보고한다. 실제 절차는 승인 전 수행하지 않는다.
