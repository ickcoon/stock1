# C15-15 최종 배포 사전 점검

**파일·의존성·필수 DB 점검은 통과. 실제 배포 승인은 보류한다.** 운영 경로는 접근 가능하지만 현재 권한으로 실제 Collector 명령행/작업 디렉터리/등록 작업을 확정하지 못했다. 이 항목을 추측해 통과시키지 않았다.

운영 파일/DB/스케줄/키 변경, Collector 중지/재시작/실행, 실전 API 호출, Telegram 발송, commit/push 없음. 운영 Python 인터프리터는 읽기 전용 환경 확인 및 개발 폴더의 격리 테스트 실행에만 사용했다. 런처나 운영 Collector를 실행한 것이 아니다.

## A·B. 현재 PC와 운영 상태

|항목|실제 확인 결과|
|---|---|
|PC|Windows 호스트 **52DKONG**. 개발 경로와 지정 운영 경로가 모두 실제 접근 가능. 전용 개발 PC/운영 PC라는 역할 명칭까지 단정하지 않는다.|
|운영 경로|`C:\Users\dada1\Downloads\stock1-dev\runtime\c12v-real-20261102` 존재·파일 읽기 가능. 별도 원격 PC에 접속한 것이 아니다.|
|프로세스|Get-Process로 PID **3708**의 운영 `.venv\Scripts\python.exe`, PID **4064**의 기존 python-repair 런타임 Python을 관측했다. 관측 시점의 PID이며 변경될 수 있다. Collector 명령인지와 부모/자식 관계는 미확인.|
|명령행/부모 PID|Win32_Process CIM 조회가 access denied, HRESULT 0x80041003. **미확인**.|
|실제 프로세스 작업 디렉터리|**미확인**. 실행 파일 경로나 조회 셸의 Get-Location을 프로세스 CWD로 간주하지 않는다. Win32_Process에도 CWD 필드가 없다.|
|Windows 등록 작업|Get-ScheduledTask도 같은 권한 오류. 작업 없음으로 판단하지 않는다. 이름/트리거/액션/WorkingDirectory **미확인**.|
|읽기 전용 소유자 점검 스크립트|`scripts/check_c15_15_owner.ps1` 준비 및 PowerShell 구문 검사 통과. 현재 세션의 스크립트 실행 정책으로 실행 거부되어 우회하지 않았다.|
|운영 Python|운영 `.venv\Scripts\python.exe`를 직접 조회: **3.11.9, 64bit Windows**. 개발 Python 결과로 대체하지 않았다.|
|필수 패키지|requests 2.32.5, python-dotenv/websocket-client/truststore 등 상세 버전은 file-environment-audit.json. `pip check`: No broken requirements found. pandas는 없지만 C15 전달/재생 경로의 필수 의존성이 아니며 설치하지 않았다.|
|운영 Python 호환성|Python 3.11.9에서 배포 대상 Python 23개 전체 import 및 C15-14 격리 바깥 Workflow 통합 재생 통과. 새 패키지 설치 없음.|

운영 설정 파일의 확인된 값: profile=c12x-23-8, deployment_approved=true, **telegram_send_enabled=false**, text_only=true, automatic_deletion=false, turnover_max_pages=1, **holidays_file=null**. deployment_approved=true는 기존 운영 프로필 값이며 이번 C15 배포 승인이 아니다.

운영 내부 스케줄 소스:

- 0198 C12X: 09:10~09:55의 5분 간격, 10:00~16:00의 30분 간격.
- 아침 분석 슬롯: 09:40/09:45/09:50.
- 현재 0186: 09:40/09:50/10:00/11:00/14:00/14:30/15:00/15:30. **09:45 없음**.
- 시작 지연 허용 MAX_LATENESS_SECONDS=5, 지난 슬롯 미보충.
- 패키지는 0186에 09:45만 추가. Windows 등록 작업 자체는 수정 대상이 아니다.

정적 실행 경로는 `Start-RealCollector.ps1 -Run → .venv\Scripts\python.exe -X utf8 -u -B run_real_collector.py --run`이다. 이는 소스 확인 결과이며 실행 중인 프로세스의 실제 명령행 검증은 아니다. 런처는 중복 프로세스 검사와 전용 File 콘솔 수명 보호를 포함하므로 우회하지 않는다.

Telegram은 `Workflow → c15_delivery.process → 기존 telegram_briefing.deliver → 기존 api_call` 경로다. 보호된 설정, 수신자 allowlist, 예약·실패 기록을 유지한다. 실제 공유 상태 경로는 **C:\Users\dada1\Downloads\stock1-dev\notification_state**이며 runtime 하위라고 가정하지 않는다. 이번 점검은 자격증명을 복호화하거나 수신자에게 연결하지 않았다.

## C. 최종 파일·해시·의존성

`diagnostics/c15-15-preflight/deployment-files.csv`에 **28개 전부**의 용도, 상대 의존 모듈, 현재 운영/예상 이전/패키지 SHA256, LF 정규화 해시, 충돌 여부를 기록했다. package의 review.patch/manifest/settings-overlay 해시도 file-environment-audit.json에 고정했다.

- 기존 3개: operating_workflow.py는 C15 정상 수신 진입 연결만, telegram_briefing.py는 C15 계약/예약 분기만, schedule.py는 0186 09:45만 변경.
- 신규 Python 20개: 기존 C15 엔진·단위/분류·매핑·읽기 전용 모듈과 그 기존 의존성. 기존 함수 재사용을 위해 필요한 모듈 전체를 포함했으며 새 Collector/Telegram 클라이언트는 없다.
- 신규 설정 5개: 표시/대금/점수 계약과 preview/참조 경로. workflow-config.json의 C15 키 병합은 별도 설정 변경이다.
- 개발 테스트, 문서, 진단 DB, 점검 스크립트, stage의 기존 복사 파일은 **배포 목록이 아니다**. 개발 폴더나 stage 전체를 복사하지 않는다.

기존 수정 파일의 원본 해시 모두 일치, 신규 25개 모두 운영에 없고 파일 충돌 없음. 보존 대상 런처/공통 운영 코드와 기존 설정 해시도 일치했다. 운영 Python에서 모든 배포 Python 모듈을 import했고 누락 의존성은 발견하지 못했다. 인증/재시도/수집 구현은 교체하지 않는다. 스케줄·진입·메시지 계약의 의도된 변경만 패치로 검토한다.

## D. 참조 DB 상태

R=`C:\Users\dada1\Downloads\stock1-dev\runtime\c12v-real-20261102`

D=`C:\Users\dada1\Documents\ARVIS\stock1-dev`

|자료|실제 경로|점검·누락 정책|
|---|---|---|
|0198 필수|R\data\stock1-real.db|읽기/스키마 통과. 없거나 해당 수집 검증 실패 시 배포/브리핑 준비 실패.|
|테마 필수|D\diagnostics\lightweight-20261105\theme-master.sqlite|지원 스키마 읽기 통과. 없으면 준비 실패.|
|0186 선택|R\data\turnover\2026-11-05\0940.db 및 0950.db|실데이터 재생 통과. 0945.db는 없음. 09:45는 09:40 대체 검증. 10분 초과·미래·다른 날짜는 사용하지 않음.|
|시총 선택|D\diagnostics\lightweight-20261105\daily-120.db|읽기 통과. 최근 1~2평일 참고값 재사용. 누락/미확인은 추정하지 않고 적격성 보류.|
|ADR20 선택|D\diagnostics\lightweight-20261105\adr-60.db|읽기 통과. 누락 시 ADR20 미확인, 직접 점수 필수조건 아님.|
|신원 연결 선택|D\diagnostics\krx-as\progress.db|읽기 통과. 없으면 검증된 매핑/유형·시총 연결 보류 가능.|
|ka10001 캐시 선택|C:\Users\dada1\Downloads\stock1-dev\diagnostics\master\c10-master.db|읽기 통과. 현재 유효 시총 캐시 0행, 새 API 호출 없음.|

모든 C15 SQLite 연결은 mode=ro와 query_only다. 데이터가 새로 갱신되었다거나 미래 거래일에도 충분할 것이라고 판단하지 않았다. 대용량 DB 복제 없음. 개발 진단 위치의 경량 자료를 운영 참조 설정에서 명시적으로 재사용하므로 이 경로들은 정리/삭제 대상에서 제외해야 한다. 참조 경로 이동 시 설정·패키지 해시를 다시 점검한다.

## E. 배포 전 위험과 승인 판단

1. **Collector 프로세스의 실제 명령행과 등록 작업 미확인**. 파일 교체 중 작업이 실행되지 않는 조건, 기존 재개 명령을 확정할 수 없어 현재 배포 승인 권고는 HOLD다.
2. **holidays_file=null**. 주말/기존 API MarketClosed/테스트 주입 달력 보호는 있지만 실제 명시 휴장 달력이 연결된 상태는 아니다. 이를 전면 휴장 검증 완료로 보고하지 않는다. 운영자의 기존 휴장 운영 방침 확인이 필요하다.
3. **설정은 시작 시 읽는다.** workflow-config.json을 고쳤다고 현재 실행 프로세스가 즉시 반영한다고 가정하지 않는다. 승인된 기존 수명 관리 절차가 필요하며 지금 중지/재시작하지 않았다.
4. **generic Telegram `--send`는 C15의 현재 시각/수신 조건을 재검증하지 않는다.** 그 조건은 c15_delivery.process에 있다. 과거 보고서를 generic CLI로 보내면 안 된다. 이번 단계는 사전 점검이므로 전송 코드를 수정하지 않았다.
5. **global telegram_send_enabled는 공유 스위치**다. 시험·정기 발송에서 true로 바꿀 때 다른 Workflow 경로 영향도 승인해야 한다. 별도 c15_send_enabled 기본 false를 유지한다.
6. 원시 가격 시각 미검증, 0186 부분/대체 자료, 사후 ADR, 시총/유동성 보류는 기존 잠정 관측 한계로 남는다.

소유자 확인 뒤 운영 파일 반영 승인을 별도로 받을 수 있다. 실제 Telegram 시험과 정기 활성화는 각각 다른 승인 단계다. 점검 스크립트 결과만으로 세 승인을 합치지 않는다.

## F. 백업·롤백

`backup-plan.json`에 변경 전 코드/설정/런처 7개와 SHA256을 기록했다. 백업은 아직 생성하지 않았다. 대상은 기존 패치 파일 3개, workflow-config.json, run_real_collector.py, Start-RealCollector.ps1, CollectorConsole.ps1이다. 등록 작업 정의는 확인 후 해당 작업만 Export-ScheduledTask로 백업한다. 이름을 추측해 모든 작업을 수정하거나 새 작업을 만들지 않는다.

DB 내용 변경이 없는 코드 배포이므로 대용량 운영 DB 복제는 필요하지 않다. 기존 DB 백업 정책을 유지하고 필요 시 별도 승인된 SQLite 온라인 백업을 사용한다. WAL DB 단순 복제를 정상 백업으로 취급하지 않는다.

롤백은 승인된 점검 시간에 원본 코드 3개와 설정을 바이트 그대로 복원하고 기존 실행 절차로 재개한다. 새 파일은 add 목록만 별도 보관하거나 그대로 둔다. notification_state/reports/DB/키를 복원 시점으로 되돌리거나 예약을 삭제하지 않는다. 당일 기존 경로로 복귀할 때도 이미 발송된 슬롯을 확인한다.

## G. 정확한 명령과 단계별 승인

### 지금 실행 가능한 읽기 전용 확인

```powershell
$Dev = 'C:\Users\dada1\Documents\ARVIS\stock1-dev'
$Runtime = 'C:\Users\dada1\Downloads\stock1-dev\runtime\c12v-real-20261102'
& "$Runtime\.venv\Scripts\python.exe" -I -B "$Dev\scripts\check_c15_15_files.py"
& "$Runtime\.venv\Scripts\python.exe" -I -B -m pip check
```

소유자 세션에서 실행 정책이 허용하면 아래 읽기 전용 스크립트를 실행한다. 실행 정책을 자동 변경하거나 우회하지 않는다. 현재 세션에서는 거부됐다.

```powershell
powershell.exe -NoProfile -File "$Dev\scripts\check_c15_15_owner.ps1"
```

스크립트 실행이 제한되어도 운영 소유자 콘솔에서 다음 조회를 직접 수행할 수 있다. 명령행에는 비밀 값이 포함될 수 있으므로 원문 전체를 공유하지 않는다.

```powershell
Get-CimInstance Win32_Process | Where-Object {
  $_.Name -match '^python(w)?\.exe$' -and $_.CommandLine -match 'run_real_collector\.py|kiwoom_collector.*collect.*--schedule'
} | Select-Object ProcessId,ParentProcessId,ExecutablePath,
  @{Name='RuntimeMatch';Expression={$_.CommandLine -like "*$Runtime*"}},
  @{Name='RunSwitch';Expression={$_.CommandLine -match '(?:^|\s)--run(?:\s|$)'}}
Get-ScheduledTask | Where-Object {
  $_.TaskName -match 'stock|arvis|collector' -or
  (($_.Actions | ForEach-Object { $_.Execute+' '+$_.Arguments }) -join ' ') -match 'run_real_collector|Start-RealCollector|c12v-real-20261102'
} | Select-Object TaskName,TaskPath,State,Actions,Triggers
```

Actions/Arguments에 비밀 값이 있는지 먼저 검토한 뒤 공유한다. 실제 CWD는 조회 결과에서 추정하지 말고 운영자의 원래 실행 콘솔/등록 Action.WorkingDirectory/실행 기록으로 확인한다. 이 확인이 불가능한 항목은 계속 미확인으로 남긴다.

### 배포 승인 후에만 사용할 파일 반영 절차

1. 소유자가 프로세스·작업·휴장 방침을 확인하고 별도 배포 승인을 받는다. 파일 교체 중 실행되지 않도록 하는 **기존 운영 관리 절차**를 확정한다. 지금은 중지·재시작하지 않는다.
2. 위 audit를 다시 실행하여 해시가 동일한지 확인한다. 다르면 중단한다. 소유자 계정에서 승인된 백업 폴더를 만들고 backup-plan의 파일만 복사한다.

```powershell
$Repo = 'C:\Users\dada1\Downloads\stock1-dev'
$Package = "$Dev\diagnostics\c15-14-package"
$Plan = Get-Content -LiteralPath "$Dev\diagnostics\c15-15-preflight\backup-plan.json" -Raw | ConvertFrom-Json
$Backup = Join-Path $Repo ('backups\c15-15-'+(Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Path $Backup -ErrorAction Stop > $null
foreach ($item in $Plan.files) {
  $source = Join-Path $Runtime $item.path
  if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLowerInvariant() -ne $item.sha256) { throw 'backup_source_drift' }
  $target = Join-Path $Backup $item.path
  New-Item -ItemType Directory -Force -Path (Split-Path $target) > $null
  Copy-Item -LiteralPath $source -Destination $target -ErrorAction Stop
}
git -C $Repo apply --check --ignore-space-change --directory=runtime/c12v-real-20261102 "$Package\review.patch"
if ($LASTEXITCODE -ne 0) { throw 'patch_check_failed' }
# 배포 승인·기존 실행 관리·백업을 완료했을 때에만:
git -C $Repo apply --ignore-space-change --directory=runtime/c12v-real-20261102 "$Package\review.patch"
if ($LASTEXITCODE -ne 0) { throw 'patch_apply_failed' }
```

3. workflow-config에 C15 overlay 키만 병합한다. 처음은 c15_briefing_enabled=true, **c15_send_enabled=false**, global telegram_send_enabled=false 유지. 원래 프로필/수신자 상태 경로/보존/페이지 수를 덮어쓰지 않는다. 전체 개발 디렉터리나 stage 복사 금지.
4. 기존 Collector 정상 재개는 **확인된 원래 운영 명령**으로만 수행한다. 정적 소스상 전용 콘솔 진입은 아래와 같지만, 실제 등록 방식 확인 전에는 실행하지 않는다. 두 번째 Collector를 띄우지 않는다.

```powershell
# 별도 운영 수명 관리 승인 후, 기존 인스턴스가 없음을 확인한 경우만:
powershell.exe -NoProfile -File "$Runtime\Start-RealCollector.ps1" -Run
```

5. 0198 세 슬롯/0186 09:45 단일 추가 수집, 기존 나머지 슬롯, API 간격/페이지 수/오류 기록을 확인한다. C15 reports의 references/live-context/diagnostics/telegram 파일을 확인한다. 09:40/09:50도 disabled, 09:45는 기록만이어야 한다. 운영 적용 후 원본 해시 audit는 drift가 정상이며 **배포 전 원본 검사와 배포 후 결과 검사를 혼동하지 않는다**.
6. **별도 시험 발송 승인** 후에만 기존 Workflow의 당일 정상 수신 경로를 사용한다. generic Telegram CLI에 `--send`를 붙여 과거 미리보기를 전송하지 않는다. 승인된 미래 슬롯 하나만 c15_send_slots에 지정하고, 공유 스위치 영향과 설정 재로딩 절차를 먼저 확정한다. 첫 모의 보고서/기존 동결 보고서는 재사용 발송하지 않는다. 해당 신규 슬롯의 정상 수집 후 기존 예약에 의해 한 건만 발송되었는지 확인하고 다음 날 반복되지 않도록 시험 종료·발송 OFF를 운영 절차로 확정한다. 설정 hot reload가 없으므로 파일만 되돌리고 시험이 끝났다고 간주하면 안 된다. **현재는 이 수명 관리 조건이 미확인이라 안전한 시험 발송 명령을 확정하지 않았다.** 새 시험 발송 기능도 만들지 않았다.
7. **별도 정기 발송 승인** 후 c15_send_slots=[09:40,09:50]와 send 스위치/설정 로딩을 반영한다. 09:45는 분석·기록만 유지한다. 중복 예약·120초·휴장 보호는 끄지 않는다.
8. 승인된 다음 거래일의 실제 09:40/09:50을 확인한다. 날짜를 임의 지정하거나 가짜 시계로 운영 발송하지 않는다.

## H. 승인 가능 여부

**현재 권고: 배포 승인 보류.** 파일/의존성/필수 참조와 운영 Python 호환성은 통과했다. 남은 조건은 소유자 권한의 실제 프로세스·등록 작업·실행 디렉터리 확인, 휴장 운영 방침 확인, 설정 로딩/시험 종료 수명 관리 확정이다. 이 조건이 해결되기 전에는 운영 파일 변경이나 실제 발송을 시작하지 않는다.

증거: `diagnostics/c15-15-preflight/`의 file-environment-audit.json, deployment-files.csv, backup-plan.json, owner-inventory.json, visible-python-processes.json, readiness.json. C15-14의 기존 통합 검증도 이번에는 실제 운영 Python으로 다시 통과했다.
