# C12-AC 집 PC 개발·검증 결과

2026-11-02 KST. 기준 브랜치 `feat/c14-chart-integrated-20261101`, HEAD/upstream `4f0c2c2c790b3e045a7f4a17504054a168da3106`. Commit·Push·Merge 및 병원 PC 배포 없음.

## 기존 기능과 새 연결

기존 `am_input_adapter.adapt`로 0198/0186·검증 manifest·마스터·시총 자료를 연결하고, `am_briefing.analyze`의 섹터·종목 순위를 사용한다. 적격성은 기존 candidate_filter를 따른다. 마스터 적격성 결과가 없으면 기존 `am_candidate_adapter.join`의 미매칭 처리로 선정 보류한다.

`session_review.morning_transition`의 단일 오전 상태와 후보 유지·변경·철회 규칙을 수정하지 않았다. 신규 `morning_briefing.process`는 이 상태의 send/revision 판단을 발송 일정으로 사용하지 않고, 09:40/45는 미리보기만 저장하며 09:50에만 최종 전송을 요청한다. 원래 상태 전이의 중요 변경은 상태 JSON에 남는다.

`telegram_briefing.deliver`의 동일 날짜/보고 유형/스키마/환경/시장/수신자 예약을 그대로 사용한다. 오전 revision별 추가 발송 키는 사용하지 않는다. 이전 버전의 발송·실패·예약 기록도 같은 키로 차단한다. 발송 성공 시에만 기존 발송기가 sent를 기록하며 실패·불명확 결과는 성공으로 기록하지 않는다. 실패 후 재실행도 기존 예약으로 차단한다. 중복 위험 때문에 자동 재전송은 구현하지 않았다.

## 파일

- `kiwoom_collector/morning_briefing.py`: 기준시각 자료 동결, 마스터 부재 보류, 단일 상태 재사용, 보고 저장 및 최종 전송 조건.
- `kiwoom_collector/operating_workflow.py`: 기존 오전 hook에서 새 연결 호출, 스냅샷/입력 검증 실패 시 빈 보고 생성. 기존 Step6-D/C14 산출 경로를 공통 `review_artifacts`로 추출하여 재사용한다. 부가 산출물 실패는 텍스트 브리핑을 막지 않는다. 오후 판정·전송 경로는 기존 함수를 유지한다.
- `kiwoom_collector/am_briefing.py`: 모바일 최종 포맷. 잠정/보류, 관측 종목, 누락 근거, 0198/0186 관측·수신 시각 표시.
- `kiwoom_collector/telegram_briefing.py`: 새 연결의 입력 포맷 표식이 있는 보고만 모바일 포맷으로 렌더링. 기존 보고 포맷과 발송기는 유지.
- `tests/test_c12ac_morning.py`: 네트워크 차단 및 fake transport 기반 신규 통합 테스트 12개.
- `docs/C12AC_SYNTHETIC_MESSAGE.txt`: 실제 포맷 함수로 만든 합성 메시지 예시. 전송하지 않음.
- 이 문서. 앞선 `C12_AC_HOME_READINESS_20261102.md`는 최초 조사/동기화 기록으로 보존.

## 생성·발송 흐름과 실행 연결 상태

기존 C12-X scheduler의 `Workflow.review` 호출 → 오전 분기 → 스냅샷·adapter → 동결·기존 분석 → 기존 단일 상태 갱신 → `reports/c12ac-YYYYMMDD-HHMM/telegram.json`, `telegram.txt`, `review.json` 저장 → 09:50이고 `telegram_send_enabled`가 명시적으로 true일 때만 기존 발송기 호출.

기본값은 false다. 비활성 상태에서는 인증정보 읽기나 발송기를 호출하지 않는다. JSON/TXT 미리보기 생성은 전송과 독립적이다. 재실행은 저장된 JSON을 검증해 재사용하며 최신 DB 자료로 기존 보고를 덮어쓰지 않는다. 실패 보고에도 유효한 빈 분석을 사용하고 오류 문자열 대신 예외 유형만 기록한다.

소스 코드상 기존 opt-in Workflow에는 연결됐다. 이 집 PC에서 Collector·런처·스케줄러를 실행하거나 서비스 등록하지 않았다. 병원 C12-AB 런처/전체 런타임은 Git에 없으므로 수정·재구현하지 않았다. 따라서 매일 자동 실행·실제 Telegram 도착은 미확인이다.

## 시간과 판정의 의미

- 입력의 observed_at 또는 received_at이 기준시각을 넘으면 제외한다. 09:50:01 관측/수신 자료로 09:50:00 보고를 재구성하지 않는다.
- 기존 C12 관측 정책과 분석 규칙은 변경하지 않는다. 기존 45초 수신 대기 때문에 실제 생성·전송 요청은 통상 09:50:45 이후가 될 수 있다. 다만 그 대기 동안 도착한 기준시각 이후 자료는 이번 브리핑에서는 제외한다. 09:50 정각 전송을 검증한 것은 아니다.
- 09:45 자료는 adapter로 읽지만 기존 C12 반복 관측 시점은 유지된다. 09:45 순위를 새로운 반복 횟수로 추가하지 않는다. 기존 정책상 유효한 지표·검증 변화로 후보 유지/변경을 재평가한다.
- 잠정은 기존 엔진의 분석 우선 후보라는 뜻이다. 검증된 실전 추천이나 매매 확정을 새로 선언하지 않는다. candidate_confirmed=false 및 d0_storage_allowed=false를 유지한다. 자료 부재·시총/섹터/거래대금 미검증은 보류로 표현한다.

## 검증 결과

| 검사 | 결과 |
| --- | --- |
| 신규 C12-AC 통합 | 12/12 PASS |
| 기존 `test_am*.py` | 101/101 PASS |
| 기존 `test_telegram*.py` | 53/53 PASS |
| 기존 C12-X + C12 chart 직접 실행 | 19개 중 14 PASS, 2 FAIL, 3 ERROR |
| 기존 C12-X, Windows TLS 설정만 mock한 네트워크 없는 실행 | 12/12 PASS |
| `git diff --check` | PASS |

신규 12개는 정상 Top3·각 Top3, 09:40→50 유지, 상태 변경, 실제 합성 지표 변화로 09:45 기존 대장 후보 제외, 거래대금 누락, 동반상승 부족, 09:50 이후 자료 제외, 날짜별 중복, 발송 실패 후 재시도 차단, 비활성 외부 전송 없음, 스냅샷 실패, 중복 입력 보류를 검증한다. 정상 연결 테스트는 Workflow.review → process → 가짜 발송 호출까지 진행한다.

직접 실행 실패 5건은 truststore 및 Pillow 부재 때문이다. 수정 전 HEAD의 operating_workflow를 메모리에 로드한 같은 테스트에서도 동일한 2 FAIL/3 ERROR를 재현했다. 파일을 되돌리거나 Git을 변경하지 않았다. 누락 의존성을 감추기 위해 실제 테스트 코드를 수정하지 않았다. TLS 설정 mock 결과는 네이티브 TLS 검증 성공을 의미하지 않는다.

기존 `requirements-weekly-windows.txt` 설치를 시도했으나 인증서 검증 오류로 실패했고 네이티브 truststore 사용 재시도도 완료되지 않아 중단했다. TLS 검증을 끄거나 인증 설정을 변경하지 않았다. 전체 저장소 테스트는 실행하지 않았다. 실전 자료·실전 API·Telegram 네트워크 요청은 검증에 사용하지 않았다.

## 병원 배포 전 남은 일

1. 병원 런처와 전체 런타임의 실제 호출 경로를 확보하고 opt-in Workflow 사용 여부·설정 파일 위치를 확인한다. 없는 런처를 추정 구현하지 않는다.
2. 기존 requirements에 맞는 truststore/Pillow와 인증서 신뢰 환경을 준비하고 직접 실행 실패 5건을 포함한 관련 회귀 검사를 다시 실행한다.
3. 병원의 실제 0198/0186 수집·수신 시각, manifest, 시총 캐시, 섹터·적격성 근거를 읽기 전용으로 확인한다. 정각 이후 도착 자료가 제외되어 최종 보고가 보류될 수 있으므로 실제 타임스탬프 계약을 검토한다.
4. 명시적으로 준비된 기존 공유 notification_state 경로와 과거 예약 상태를 확인한다. 발송 실패/불명확 기록을 자동 삭제하거나 새 revision으로 우회하지 않는다.
5. 우선 전송 비활성 상태로 09:40/45/50 생성 결과를 검토한다. 실전 배포·전송 활성화는 별도 결정으로 남긴다.

기존 0198 23회/0186 8회 시간표, schedule.py, Kiwoom 인증·TokenManager, 운영 DB, Windows 작업 스케줄러, 음성, 주문 및 main 브랜치는 변경하지 않았다. 기존 미추적 사용자 파일은 보존했다.
