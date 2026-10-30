# Step 7-C9-B — 제한 시간 있는 운영 SQLite 분석용 스냅샷

## 범위 및 현재 상태

별도 online_snapshot 모듈을 추가했다. 기존 C8 cross_adapter.readonly_database 및 C9 분석 코드는 수정하지 않는다. 합성 WAL DB에서만 실행했다. 운영 수집기/DB/스케줄러/인증정보는 변경하거나 사용하지 않았다. **운영 DB 접근은 사용자 승인 전이며 아직 실행하지 않았다.**

## 구현

- 명시한 로컬 기존 파일만 source로 받는다. 자동 운영 폴더 탐색, 네트워크/API/Store/자격증명 로드 없음.
- source는 mode=ro&cache=private, timeout=100ms, query_only 및 읽기 SQL authorizer 사용. immutable 사용 금지. source에 SQL 쓰기·저널 설정 변경·checkpoint·VACUUM·ATTACH를 실행하지 않는다.
- Windows 파일 사전 확인도 GENERIC_READ만 요청하며 FILE_SHARE_READ|WRITE|DELETE로 작성자를 배제하지 않는다. 공유 충돌은 Win32 코드 32로 기록하고 중단한다. SQLite가 반환한 오류는 sqlite_errorcode/sqlite_errorname으로 기록한다. 원문 예외는 로그에 복제하지 않는다.
- Python sqlite3 Online Backup API를 새 자식 프로세스에서 1회 사용한다. 기본 64페이지/step, 한도 5초, callback에서 BUSY/LOCKED 즉시 중단. 외부 자동 재시도 없음.
- 콜백이 실행되지 않는 open/IO 정체도 부모 watchdog으로 제한한다. 5초 예산은 프로세스 기동 시간을 포함한다. 초과 시 **이 도구가 만든 자식만** terminate하고 최대 1초 기다린 뒤 필요하면 kill 후 최대 1초 확인한다. OS 호출/종료 확인 지연까지 하드 실시간 5초를 보증하지 않으며 종료 미확인은 실패로 기록한다. 운영 PID를 조회·종료하는 코드는 없다.
- 백업 직후 source를 닫고 자식이 종료한 뒤 목적지에만 journal_mode=DELETE, quick_check를 적용한다. 목적지 sidecar가 없을 때 해시/크기를 기록하고 snapshot.db로 게시한다. 실패/중단 파일은 성공 snapshot으로 게시하지 않는다.
- 출력은 stock1-dev/diagnostics/online_snapshots/<name>/로 고정한다. 기존 이름·결과는 덮어쓰지 않는다. 출력 루트의 symlink/junction 리디렉션을 거절한다.
- manifest.json을 먼저 pending으로 기록하고 success/failed/cancelled로 종료한다. 원본 절대 경로, 실행 구간 KST, 개발 SQLite 버전, 시간/페이지 설정, 원본 연결 해제 확인, 목적지 경로·SHA-256·크기·quick_check와 오류 숫자를 기록한다. 원본 경로가 있는 manifest는 로컬 진단 자료로 관리한다.
- 단순 파일 복사는 없다. 원본 DB/WAL/SHM에 대한 직접 write/delete/copy는 없다. 부분 산출물 정리는 새 진단 run 폴더의 고정 목적지 파일 및 그 sidecar에만 한정한다.

## 영향과 불변성의 정확한 의미

mode=ro는 논리 DB 쓰기를 막지만 SQLite 읽기 잠금·SHM 참여를 없애지 않는다. 읽는 동안 WAL checkpoint 진행이 제한되거나 쓰기가 일시 지연될 수 있다. 긴 read transaction을 별도로 고정하지 않고 작은 backup step과 전체 시간 제한을 둔다. 소스가 자주 갱신되면 backup이 다시 시작될 수 있으며 제한 시간 초과 시 재시도 없이 보류한다.

합성 테스트에서 작성자가 조용한 동안 원본 DB/WAL 해시가 백업 전후 동일함을 확인했다. **SHM 바이트 불변은 주장하지 않는다.** 동시 작성자의 정상 커밋/자동 checkpoint로 운영 원본 해시가 변하는 것도 도구의 SQL 쓰기와 구별해야 한다. 운영 원본 파일 불변성은 운영에서 실행하지 않았으므로 아직 실측 판정하지 않았다. 모든 파일·SHM·잠금 변화가 0이어야 한다는 정책에는 이 온라인 방법이 맞지 않는다.

## 합성 검증

tests/test_online_snapshot.py는 임시 합성 DB만 만든다. 합성 자료를 병원 실관측처럼 표시하지 않는다.

1. WAL writer 연결 유지 상태에서 신규 WAL 커밋 포함, source DB/WAL 해시 동일, 목적지 quick_check/C8 읽기 통과.
2. 페이지 백업 중 별도 writer가 모든 행의 버전을 갱신·커밋: 목적지의 행 버전 혼합 없이 일관성 유지.
3. source 연결의 INSERT, 저널 모드 변경, ATTACH 거절.
4. rollback DB의 EXCLUSIVE writer 잠금: 즉시 실패, 성공 파일 없음, writer 계속 사용 가능.
5. callback deadline 및 별도 프로세스 watchdog timeout; 실패 감사 기록과 자식 종료.
6. 사용자 중단 처리, 잘못된 DB, 목적지 검증 실패, 기존 출력 보존, 없는 source 생성 금지.
7. Windows 공유 오류 32 보존.
8. 스냅샷 → 변경하지 않은 C8 진단 연결: 합성 4슬롯 저장 완전성만 확인하고 검증 manifest 없이는 검증 완료/브리핑 가능으로 승격하지 않음.

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_online_snapshot.py -v
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

2026-10-30 합성 전용 테스트 13개 PASS. 전체 회귀 972개 PASS(177.382초, 종료 코드 0). 잠금 오류 숫자 보완 후 합성 13개도 재통과했다. 전체 로그는 로컬 reports/step7c9b-regression-20261030.txt에 보존한다. 코드 수정으로 기존 C8의 오프라인 정책을 완화하지 않았다.

## 승인 후 운영에서 단 한 번 실행할 명령 — 이번에는 미실행

stock1-dev 작업 폴더에서 사용자 승인 후에만 실행한다. 실패해도 자동 재실행하지 않는다. 실패 원인을 보고하고 새로운 실행을 별도로 판단한다. 기존 name이 있으면 이름 충돌로 중단한다.

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.online_snapshot `
  --source 'C:\Users\dada1\Downloads\stock1\data\stock1.db' `
  --name hospital-c9b-20261030-once --seconds 5 --pages 64
```

종료 코드: 0 성공, 2 실행/백업/검증 실패, 130 사용자 중단, 1 실행 범위·이름/파일 오류. 성공 판단에는 exit 0과 manifest의 success=true/status=success, source_connection_released=true, quick_check=ok, snapshot.db SHA-256 일치를 함께 요구한다. pending/failed/cancelled 산출물을 분석하지 않는다.

원본 읽기 권한·개발 출력 쓰기 권한이 필요하다. 관리자 권한으로 C8/파일 공유 충돌을 무시하는 설계가 아니다. 실패 시 권한이나 보호 조건을 자동 변경하지 않는다.

## 승인 후 C9 절차 — 원본을 다시 열지 않음

백업 완료 직후 source는 이미 닫혀 있다. 이후 무결성/분석은 목적지만 사용한다. 동일 스냅샷 한 개로 3일×mock/real 6회 진단한다. 스냅샷 시각은 백업 시작/완료 구간으로 표현하며 특정 커밋 시각으로 추정하지 않는다.

```powershell
$snapshotPath = Join-Path (Get-Location) 'diagnostics\online_snapshots\hospital-c9b-20261030-once\snapshot.db'
foreach ($tradeDate in '2026-10-28','2026-10-29','2026-10-30') {
  foreach ($marketEnvironment in 'mock','real') {
    .\.venv\Scripts\python.exe -B -m kiwoom_collector.am_data_diagnostic `
      --ranking-db $snapshotPath --date $tradeDate `
      --cutoff "${tradeDate}T09:50:00+09:00" --environment $marketEnvironment `
      --name "c9b-$tradeDate-$marketEnvironment"
    # 2는 근거 부족으로 보류, 1은 진단 자체 실패이므로 중단한다.
    if ($LASTEXITCODE -notin 0,2) { throw 'C9 diagnostic failed; preserve outputs' }
  }
}
```

0186/시총 소스를 생략하면 미제공이다. 같은 스냅샷에 turnover 테이블이 있는지 먼저 오프라인으로 확인하고, 실제 구조가 있을 때만 --turnover-db에 그 스냅샷 경로를 명시한다. 별도 온라인 DB가 필요하다면 이 단발 운영 승인에 자동으로 포함하지 않는다. 시총은 실제 collected export와 독립 검토 manifest가 준비된 경우에만 명시하며 예전 날짜 자료/fixture를 이식하지 않는다.

결과는 오전 09:20~09:50의 회차/실제 행 수, TOP20 중복·완전성, TOP5 반복과 순위 변화, 같은 코드·환경·시점의 시세/거래대금/시총/섹터를 분리한다. 연결률은 분자/분모를 표시하며 조회 불가는 0%로 바꾸지 않는다. 요청 시작·저장 종료를 실제 수신 시각으로 복제하지 않는다. cutoff 이후의 실제 수신을 포함하려면 사용자가 의도한 cutoff로 새 보고서를 생성해야 한다.

C7의 원본 해시/지문에 결합된 독립 검토 근거가 없으면 C6의 verified 후보·브리핑은 계속 보류될 수 있다. mock 결과는 실전 주도주가 아니다. 반복 관심도는 수급 유입의 확정 증거가 아니다. 외부 API/Telegram/OpenAI 호출이나 과거 슬롯의 현재 데이터 보충은 하지 않는다.

## 승인 경계

이번 요청은 구현·합성 검증·커밋/푸시까지다. **운영 source에 대한 첫 mode=ro 연결 및 단 한 번의 snapshot 실행은 다음 사용자 승인 후에만 진행한다.** 승인 범위는 위 원본 경로를 5초 예산/64페이지로 1회 백업하고, 성공 목적지에서만 C9를 수행하는 절차다.
