# 병원 PC 개발 인수인계 — 2026-11-05

- 저장소: https://github.com/ickcoon/stock1.git
- 개발 브랜치: `integration/home-hospital-c12-20261103`
- 최종 커밋: 이전 폴더의 `COMMIT.txt` 확인. 이 문서를 포함하는 커밋이다.
- DB 기준: 2026-11-04 / 264거래일 / 730,970행.
- 운영 Collector, 운영 DB, 기존 테마 DB와 분리된 새 개발 폴더에서 실행한다.

## 1. 코드 받기

병원 PC PowerShell에서 개발 상위 폴더를 선택한다. 아래 `stock1-dev`가 이미 있으면 덮어쓰지 말고 다른 빈 폴더명을 사용한다.

```powershell
git clone --branch integration/home-hospital-c12-20261103 --single-branch https://github.com/ickcoon/stock1.git stock1-dev
Set-Location stock1-dev
git rev-parse HEAD
code .
```

출력 커밋을 `COMMIT.txt`와 비교한다. main과 병원 운영 브랜치는 변경하지 않는다.

## 2. DB 복원

집 PC의 `C:\Users\dada1\Documents\ARVIS\hospital-transfer` 폴더 전체를 USB 또는 보안 파일 전송으로 병원 PC에 옮긴다. GitHub에는 DB가 없다.

개발 폴더에서 실행한다. 경로는 병원에 옮긴 위치로 바꾼다.

```powershell
$transferRoot = 'D:\hospital-transfer'
if (Test-Path .\diagnostics) { throw '기존 diagnostics 발견: 덮어쓰지 말고 새 개발 폴더를 사용하세요.' }
Copy-Item -LiteralPath (Join-Path $transferRoot 'diagnostics') -Destination .\diagnostics -Recurse
```

`manifest.json`에 각 파일의 SHA256과 크기가 있다. `Get-FileHash -Algorithm SHA256`으로 복원본을 확인한다.

필수 파일:

- `diagnostics/krx-daily/as-incremental.db`: 최신 증분 데이터.
- `diagnostics/krx-as/progress.db`: 증분 진행 상태.
- `diagnostics/krx-daily/ap2-20251001-20261030.db`: 기존 bootstrap seed.
- `diagnostics/krx-ap2/20251001-20261030/progress.db`: 기존 bootstrap 진행 상태.
- `diagnostics/krx-an/an3-20261103/review.json`: 실행 시 필요한 기존 API 검토 설정. 병원 인증키의 서비스 이용 권한을 보장하지 않는다.

최신 ADR20 DB·JSON·HTML과 포인터도 포함된다. 포인터 내부 경로는 집 PC 기준이므로 첫 실행에서 `-RebuildAdr`로 병원 경로에 재생성한다. 기존 DB 내부 provenance 경로는 과거 집 PC 경로를 보존한다. 과거 원문 receipt 전체와 테마 DB는 이번 이전 범위에 포함하지 않는다.

## 3. Python·인증키

Python 3.11을 설치한 뒤 개발 폴더에서 실행한다.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-krx-as-windows.txt
$privateRoot = Join-Path (Split-Path -Parent (Get-Location).Path) ((Split-Path -Leaf (Get-Location).Path) + '-private')
New-Item -ItemType Directory -Path $privateRoot -Force | Out-Null
$keyFile = Join-Path $privateRoot '.env.krx.local'
if (-not (Test-Path -LiteralPath $keyFile)) { Copy-Item -LiteralPath .\config\krx.env.example -Destination $keyFile }
notepad $keyFile
```

메모장에서 `KRX_AUTH_KEY=` 뒤에 병원에서 사용할 인증키를 직접 입력한다. 키를 채팅·Git·이전 문서에 넣지 않는다. 기존 키 파일은 덮어쓰지 않는다. 이 환경은 KRX 독립 실행용이며 전체 Collector 개발 의존성 설치는 별도다.

## 4. 다음 개발 시작

```powershell
# 읽기 전용 실행 계획 확인
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_krx_incremental.ps1
# 먼저 이전 기준일까지 검증하고 ADR·HTML을 새 경로에 생성
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_krx_incremental.ps1 -Execute -End 2026-11-04 -RebuildAdr
# 이후 최신 확정일까지 증분 수집 (기본: 한국시간 어제)
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\scripts\run_krx_incremental.ps1 -Execute
```

실행 옵션은 해당 PowerShell 프로세스에만 적용한다. 시스템 실행 정책은 바꾸지 않는다.
`diagnostics/krx-as/latest-run.json`의 `exit_code`, `dates`, `last_complete_after`를 확인한다. `latest-adr.json`의 출력 폴더에서 HTML을 연다.

집 PC에서 최소 검증: 경로·증분 단위 테스트 20개 통과. 기존 실제 DB 테스트 일부는 11/03 날짜를 고정하므로 최신 11/04 결과에는 그대로 사용하지 않는다. ADR은 종목 범위·단위 관련 기존 provisional 정책을 유지한다.
