# C12-AC 의존성·실행 연결 점검

2026-11-02 KST. 기존 기능 추가 없이 배포 전 검증만 수행했다. 기준 브랜치 `feat/c14-chart-integrated-20261101`, HEAD `4f0c2c2`. 앞선 구현 변경은 그대로 보존했다. 이번 후속 작업은 실행 Python 코드를 수정하지 않았다.

## 실패 5건의 정확한 원인

| 테스트 | 재현 증상 | 직접 원인 | 분류 |
| --- | --- | --- | --- |
| `test_failed_renderer_keeps_next_candidate` | ERROR | 테스트 fixture의 `card()`에서 `from PIL import Image` 실패 | 선택적 차트 패키지 Pillow 미설치, 기존 테스트 환경 준비 누락 |
| `test_missing_chart_and_bad_card_are_independent` | ERROR | 같은 fixture의 Pillow import 실패 | 위와 동일 |
| `test_saved_card_png_copy_backward_compatibility` | ERROR | 같은 fixture의 Pillow import 실패 | 위와 동일 |
| `test_generates_png_from_existing_daily_adapter_and_card` | FAIL, available 대신 partial | `analyst.mini_chart.render_png`의 matplotlib import 실패로 PNG가 생성되지 않음. bridge가 차트 자료는 있으나 이미지가 없는 partial을 반환 | 선택적 차트 렌더러 matplotlib 미설치, 기존 테스트 환경 준비 누락 |
| `test_existing_loop_one_day_23_rankings_8_turnover_and_restart_no_duplicate` | FAIL, turnover 호출 8회 대신 0회 | `Workflow.__call__`에서 `configure_windows_trust` → `WindowsTrustAdapter` → truststore import 실패 → `ValueError('weekly_windows_trust_dependency_required')`. 가짜 collect_turnover 함수에 도달하기 전에 중단 | C12-X/C12-AC 실행 경로의 실제 필수 의존성 누락. 테스트 환경에도 같은 패키지가 필요 |

5건 모두 설치 후 수정·mock·skip 없이 통과했다. 이 5건에서 실행 코드 자체의 오류는 확인되지 않았다. 차트의 partial 처리는 기존 실패 격리 동작이며, 패키지 부재를 코드 오류로 고치지 않았다.

## requirements와 환경

시작 환경은 requests 2.32.5, python-dotenv 1.2.1, websocket-client 1.9.0 등 기본 패키지만 설치되어 있었다. truststore, matplotlib, Pillow는 없었다.

- `requirements.txt`: 기본 패키지 선언은 설치 환경과 일치.
- `requirements-weekly-windows.txt`: truststore==0.10.5가 이미 선언되어 있으나 weekly 전용 선택 사항이라는 주석은 C12-X 실행 경로에도 필요한 현실과 맞지 않았다. 주석만 수정했다.
- `requirements-chart.txt`: matplotlib==3.10.8이 이미 선언되어 있다. Pillow는 matplotlib의 `pillow>=8` 전이 의존성으로 설치되므로 중복 선언하지 않았다. 텍스트 브리핑만에는 필요하지 않지만 차트 PNG 기능 및 해당 차트 테스트에는 필요하다.
- 신규 `requirements-c12ac-windows.txt`: 기존 기본 requirements와 Windows TLS requirements를 include한다. 기존 버전을 재사용하며 패키지 버전 변경이나 기능 추가는 없다. 차트 설치는 별도 선택으로 남긴다.

로컬 `.venv`에 기존 requirements로 truststore 0.10.5와 matplotlib 3.10.8을 설치했다. 전이 의존성으로 Pillow 13.0.0 등이 설치됐다. pip의 네이티브 truststore 기능을 사용했으며 인증서 검증을 끄지 않았다. 운영 인증정보·TokenManager·시스템 인증서 설정은 수정하지 않았다.

배포 환경 준비 명령(이번에는 병원에서 실행하지 않음):

```powershell
python -m pip install -r requirements-c12ac-windows.txt
# C14 PNG 및 차트 회귀 검사도 사용할 경우
python -m pip install -r requirements-chart.txt
```

## 관련 검증

- 수정 전 환경에서 기존 5건 재현: C12 chart 7개 중 1 FAIL/3 ERROR, C12-X 12개 중 1 FAIL.
- 설치 후 `test_c12*.py`: **31/31 PASS**. C12-AC 12개, C12-X 12개, chart bridge 7개. TLS 설정 mock 없이 원래 테스트를 직접 실행했다. 테스트 자체의 가짜 Collector/전송기 및 소켓 차단은 그대로 유지했다.
- `test_telegram*.py`: **53/53 PASS**.
- `pip check`: PASS. `--dry-run --no-index -r requirements-c12ac-windows.txt`: 이미 설치된 요구 사항 충족.
- `git diff --check`: PASS.
- matplotlib이 기본 사용자 캐시 디렉터리 쓰기 권한 부족으로 임시 캐시를 사용했다는 경고가 있었다. 렌더링 테스트는 통과했으며 패키지 누락 5건과는 별개다. 병원 환경에서는 쓰기 가능한 캐시 위치를 확인해야 한다.

Telegram 실전 전송, Kiwoom 실전 API 호출 및 실제 scheduler 실행은 하지 않았다. 패키지 설치 통신은 패키지 저장소에 한정됐다. 외부 서비스 실전 연결 성공을 검증한 것이 아니다.

## 09:50 호출 경로 — Git 코드 기준

1. `operating_workflow.run` (207행): Workflow 객체 생성 후 기존 `schedule.collect_schedule(..., slots=C12X_SLOTS, after_slot=workflow)` 호출.
2. `schedule.C12X_SLOTS` (16행): 09:10부터 09:55까지 5분 간격을 포함하므로 09:50 존재. 총 0198 23회, `TURNOVER_SLOTS` 0186 8회는 변경 없음.
3. `schedule._collect_schedule` (79행): 해당 슬롯의 지연이 5초 이내이고 `store.begin`이 신규 회차를 반환하면 `_attempt` 수행 후 `after_slot(...)` (105행) 호출.
4. `Workflow.__call__` (26행): 09:50은 0186 슬롯이므로 기존 turnover 수집 경로를 거친 후 오전 분석 슬롯 조건으로 `self.review` 호출.
5. `Workflow.review` (57행): 오전 슬롯이면 `morning_review` (123행)로 분기.
6. `morning_review`: 기존 수신 대기·스냅샷·adapter 검증 후 `morning_briefing.process` (152행) 호출. 입력 준비/검증 실패는 빈 보류 입력으로 전환.
7. `morning_briefing.process` (30행): 기존 `am_briefing.analyze`와 `session_review.morning_transition` 사용, JSON/TXT 저장. 09:40/45는 미리보기만 생성. 09:50이고 `telegram_send_enabled is True`일 때만 `tg.deliver` (71행) 호출. 기본값 false.

이 경로는 **operating_workflow.run이 실제로 호출된다는 조건 아래 존재하는 코드 경로**다. 저장소의 Python·PowerShell·batch 호출부 검색에서는 실제 운영 런처가 이 run을 호출하는 지점을 확인하지 못했다. 병원 C12-AB 런처 전체가 Git에 없으므로 실제 런처 연결을 확인했다고 주장할 수 없다.

## 배포 전 남은 차단 요소와 한계

- 병원 런처의 실제 진입점, 실행 Python/venv, 설정 전달, opt-in Workflow 호출 여부가 미확인이다. 이번 requirements 설치는 집 PC에만 적용됐다.
- 스케줄러는 누락 슬롯을 소급 실행하지 않는다. 지연 5초 초과 또는 이미 claim된 슬롯에서는 후속 Workflow hook도 실행되지 않는다. 재시작하면 항상 09:50 보고가 생긴다는 보장은 없다.
- `Workflow.__call__`의 0186 초기화/수집 구간은 review 예외 처리 앞에 있다. 여기서 예외가 전파되면 scheduler가 후속 hook 실패로 기록하며 오전 보고 생성까지 진행하지 못할 수 있다. truststore 누락으로 실제 테스트에서 관측한 경로이며, 이번에는 패키지 설치로 원인을 해소했다. 모든 수집 단계 실패에 대한 보고 생성 보장은 별도 검토 사항이다. 동작 범위 확대를 위해 코드를 바꾸지는 않았다.
- 기존 45초 수신 대기 때문에 통상 09:50:45 이후 생성될 수 있고, C12-AC는 09:50:00 이후 관측/수신 자료를 제외한다. 실제 수집 시각 계약과 보류 빈도를 확인해야 한다.
- 병원 0198/0186 DB, 검증 manifest, 시총/섹터/적격성 근거, 공유 notification_state 경로 및 수신자 설정을 실제 환경에서 확인해야 한다. 이번에 운영 DB/인증정보를 읽거나 수정하지 않았다.
- 성공·실패·불명확 예약의 중복 방지는 기존 발송기에 의존한다. 실패 후 자동 재전송을 활성화하거나 예약을 삭제하지 않았다.

## 이번 후속 변경 파일

`requirements-c12ac-windows.txt` 신규, `requirements-weekly-windows.txt` 주석 수정, 이 문서 신규. 나머지 기존 미커밋 C12-AC 변경과 사용자 미추적 파일은 보존했다. Commit·Push·Merge, main 변경, 런처/운영 스케줄러/인증/DB/음성/주문 변경은 없다.
