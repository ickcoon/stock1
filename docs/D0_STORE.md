# Step 7-C4 — 실제 선정 D0의 독립 JSON 보존

`kiwoom_collector.d0_store`는 이미 발생한 **명시적 선정 이벤트와 결속된 평가 결과**를
보존하는 도구다. 순위·과거 일봉만으로 종목을 선정하지 않는다. API·인증정보·운영 DB·
수집기·스케줄러를 사용하지 않으며, 자동 연결·주문·매수 신호 기능은 없다.

## 실행

```powershell
# 읽기 전용: 기존 저장소 점검, 실제 선정 입력이 없으므로 예정/저장 0건
python -m kiwoom_collector.d0_store

# 실제 선정 시점에 만들어진 입력만 미리보기. 이 명령은 파일을 쓰지 않는다.
python -m kiwoom_collector.d0_store --input data/d0-selection/current-event.json

# 미리보기의 근거와 시각이 유효할 때 최초 1회만 저장
python -m kiwoom_collector.d0_store --input data/d0-selection/current-event.json --save
```

기본 저장 위치는 Git 제외된 `data/d0_snapshots/<episode_id>.json`이다. 입력이 없으면
저장 폴더도 만들지 않는다. 한 입력은 한 종목의 한 선정 이벤트이며, 다른 이벤트는
별도 입력으로 실행한다. `--save`만 지정하고 실제 입력이 없으면 저장 0건이다.

`--store-dir`로 테스트용 독립 폴더를 지정할 수 있다. 프로젝트 안에서는
data/d0_snapshots와 하위 폴더만 허용하며, 운영 data 루트·소스·프로젝트 루트는 거부한다.
프로젝트 밖의 명시적 경로는 호출자가 접근권한과 Git 제외를 관리해야 한다.
심볼릭 링크 저장 경로·JSON 파일은 거부한다.

## 입력 계약

UTF-8/BOM JSON 객체에 다음을 전달한다. 실제 선정 엔진 또는 명시적 선정 작업이
먼저 남긴 이벤트를 사용하며, 과거 보고서를 보고 selected_at을 사후로 만들어 넣지 않는다.

| 필드 | 내용 |
| --- | --- |
| selection.event_id | 선정 원천이 발급한 고유 ID. 환경 내 재사용 금지 |
| selection.source | 선정 기록의 원천 |
| selection.status | selected_for_observation |
| selection.stock_code / environment / market | 정확한 코드 문자열·mock/real·KRX |
| selection.selected_at | 실제 선정 시각, timezone-aware; 저장 시 KST 정규화 |
| selection.evaluation_sha256 | 선정 당시 평가 전체의 d0_store.fingerprint(evaluation) |
| selection.reasons | 비어 있지 않은 선정 근거 문자열 목록 |
| evaluation | 기존 Step 6-D eligibility 보고서. rows, sources, capital_integration 포함 |
| d0_bar | 선택 당시 이미 확인된 Observation 필드. 없으면 생략 |
| reference_evidence | d0_bar를 전달한 경우 필요한 실제 확인 근거 |

selection은 위 표의 필드만 허용한다. 평가 fingerprint는 **선정 시점에** 결속해야 하며
저장 직전에 다른 평가로 바꿔 결속하는 방식은 실제 선정의 증거가 아니다.
토큰·키·계좌·인증 헤더 같은 민감 필드는 거부한다. CLI는 원문 평가나 예외 내용을
덤프하지 않고 안전한 선정 요약 및 정적 보류 코드만 출력한다.

## 생성 관문과 소급 방지

현재 저장 정책은 보수적으로 **기존 Step 6-D의 검토 가능 결과**만 받아들인다.
새 점수나 선정 전략을 추가하지 않는다.

- qualification.final_review_allowed=True, reasons=[], sector_co_rise=True 및 기존
  시총·장마감 거래대금·순위 체크가 모두 True여야 한다. 보류 평가를 자동 통과시키지 않는다.
- candidate_confirmed는 평가 행과 qualification 모두 정확히 False여야 한다.
  True를 False로 덮어 저장하는 처리도 하지 않는다.
- 0198(ka00198), 0186(ka10032), 시총(ka10001)의 환경·코드·출처·관측 시각을 보존·검사한다.
  성공한 비테스트 원천, 알려진 요청/응답 시각과 KRX 문맥이 필요하다.
  fixture/synthetic로 표시된 원천, 미완료 거래대금 페이지, 시총 근거 부재는 거부한다.
- 선정과 기록은 **같은 KST 날짜이며 선정 후 5분 이내**여야 한다. 평가 기준 시각도
  선정과 같은 날·이전 5분 이내여야 하며, 원천 응답은 평가 기준 시각 이하여야 한다.
  이는 신선도·기록 정책이며 수익 전략 기준이 아니다. CLI에는 과거 시각 지정·강제 통과 옵션이 없다.
- 따라서 과거 DB 관측·지난날의 평가·늦게 조회한 일봉으로 D0를 소급 생성할 수 없다.
  장중 검증 보류 후보를 자동 선정하거나, 이 저장 정책을 통과시키기 위해 평가 상태를 바꾸지 않는다.
- 현재 출처 메타데이터가 부족하면 먼저 앞으로의 실제 선정 이벤트에서 필요한 근거를 남겨야 한다.

이 도구는 전달된 선정·평가 문서의 진위를 외부 시스템에 조회해 인증하지 않는다.
일관성과 시점·기존 검증 결과를 검사하는 저장 경계이며, 수동으로 조작된 입력의 진위를
보증하는 서명 시스템은 아니다. 합성 테스트는 이 계약을 모사한 자료를 임시 저장소에만 쓴다.

## 저장 구조와 이벤트 식별

| 저장 필드 | 의미 |
| --- | --- |
| schema_version | d0-store-v1 |
| episode_id | 환경 + 원천 선정 이벤트 ID로 만든 SHA256 |
| legacy_group_id | 기존 Step 7-A의 코드+D0+환경+시장 그룹 ID |
| selection | 명시적 원천 ID·선정 근거·시각·평가 결속 |
| evaluation | 선정 당시 전체 평가와 원본 출처·시각 |
| snapshot | 기존 snapshot_from_review 결과를 보존한 Snapshot 필드 및 selection_event_id |
| reference_price | 기준가격 값·검증 상태·근거. 없으면 value=None, status=unknown |
| candidate_confirmed | 항상 False |
| integrity_sha256 | 위 전체 내용의 canonical JSON SHA256 |

기존 Snapshot의 그룹 ID는 같은 종목·날짜에 하나다. 기존 엔진을 바꾸지 않고
SelectionSnapshot 하위 모델에서 원천 이벤트별 episode_id를 제공했다. 따라서 같은 날
같은 종목의 서로 다른 이벤트를 별도 파일로 보존하고, 같은 환경의 같은 이벤트 ID를
다른 종목·날짜에 붙여 재저장하는 것도 차단한다. mock/real 이벤트는 분리한다.

`snapshot_from_review()`가 입력 평가를 복사하여 보존한다. 후속 입력 수정이나 payload
조회 결과 수정은 저장된 Snapshot에 반영되지 않는다. 기준가격이 없으면 ka10001 현재가나
나중에 확인한 일봉으로 채우지 않는다. 전달된 D0 일봉은 같은 코드·시장·환경이며
선정 전에 확인된 post_market/received 관측이어야 하고 별도 근거 문자열이 필요하다.
일봉 종가 미확인을 해소하기 위해 이 필드를 임의로 만들면 안 된다.

## 원자 저장·중복·손상

완성 JSON을 같은 저장 폴더의 임시 파일에 기록하고 flush/fsync한 뒤 `os.link`로
최종 이름을 원자적으로 공개한다. 이미 존재하는 최종 이름을 교체하지 않는다.
경쟁 저장도 한 파일만 성공하며 다른 호출은 중복 오류로 끝난다. hard link를 지원하지
않는 파일시스템에서는 실패하고 덮어쓰기 방식으로 우회하지 않는다.

현재 호출이 만든 임시 파일만 정리한다. 중간 실패 시 미완성 파일을 최종 JSON으로
노출하지 않으며, 이미 공개한 원본을 갱신·삭제하는 API는 제공하지 않는다.
파일 내용·모델·선정 근거·checksum·파일명 ID를 로딩 때 다시 검사한다.
손상되거나 읽을 수 없는 JSON이 하나라도 있으면 기본 점검·미리보기·새 저장을 중단한다.
자동 복구·원본 교체는 하지 않는다. SHA256은 내용 일관성 검사이며 디지털 서명이 아니다.
사용자에 의한 파일 편집 자체를 OS 수준에서 막는 기능은 아니다.

주요 보류 코드는 actual_selection_event_missing, evaluation_not_ready,
evaluation_binding_mismatch, backdated_or_stale_selection_forbidden,
source_identity_missing, evaluation_source_identity_mismatch,
candidate_must_remain_unconfirmed, duplicate_selection_event,
corrupt_or_unreadable_d0_record 등이다. 알 수 없는 입력·I/O 예외는 민감 내용을 생략한다.

## Step 7-C5에서 읽기

```python
from kiwoom_collector.d0_store import load_snapshot
from kiwoom_collector.tracking_review import load_report, review_reports

snapshot = load_snapshot(existing_d0_json_path)
result = review_reports(
    [load_report(existing_daily_report_path)], snapshot=snapshot,
    episode_id=snapshot.episode_id, calendar=reviewed_calendar,
    as_of=analysis_cutoff_kst, basis=reviewed_basis, evidence=(),
)
```

과거에 정상 저장한 파일은 나중에 읽을 수 있다. 신규 기록의 시점 검사는 저장 시 적용하며,
읽기 시에는 원래 기록 시각으로 구조·결속을 재검증한다. load_snapshot은 원본을 수정하지 않는다.
실제 D0 1건이 앞으로 생성되어야 실제 연결 검증을 진행할 수 있다. 공식 캘린더·D+1/D+2
관측·종가/단위/세션 근거는 여전히 별도다. C2의 eligible_for_tracking=false 정책도 유지한다.

## 테스트와 실제 저장 건수

```powershell
python -m unittest discover -s tests -p test_d0_store.py -v
python -m unittest discover -s tests
```

실제 운영 DB·인증정보·API를 사용하지 않는다. fixtures/d0_selection_synthetic.json은
실제 선정이 아닌 테스트 계약을 모사한 자료이며 임시 폴더에서만 사용한다.
정상 생성·무근거 차단·중복·동일일 다른 이벤트·원본 불변성·C2 호환·원자 저장 실패·
손상·미래 시각·환경 분리·CLI 기본 미저장을 검증한다.

구현 완료 시점의 실제 저장 D0는 **0건**이다. 기본 CLI 점검은 예정 0·저장 0이며
실제 선정 이벤트를 만들거나 기본 저장 폴더를 생성하지 않았다.

저장 전용 테스트는 35개다. 전체 검증 중 기존 예약 시세 테스트에서 새벽 실제 시각과
가짜 09:20 장중 시각의 토큰 만료가 어긋나는 사례가 발견되어, 해당 테스트의 가짜
토큰 만료만 두 시각 이후로 맞췄다. 운영 인증·스케줄러 코드는 수정하지 않았다.

2026-10-29 KST 최종 전체 회귀: **757개 통과, 실패 0·오류 0·스킵 0,
87.355초, 종료 코드 0**. 운영 DB 파일은 전체 테스트 전후 크기·수정 시각·SHA256이
모두 동일했다. 실제 D0는 0건이며 OAuth·일봉 API 호출은 수행하지 않았다.
