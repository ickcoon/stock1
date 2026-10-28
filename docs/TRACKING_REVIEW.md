# Step 7-C2 — 저장 일봉과 독립 D0·D+1·D+2 검토

`kiwoom_collector.tracking_review`는 저장된 진단 보고서를 기존 B1 파서·어댑터와
Step 7-A 엔진에 연결한다. HTTP·OAuth·인증정보·운영 DB·수집기·스케줄러에 접근하지
않는다. 기존 파서·추적 엔진·어댑터와 성과 계산 코드는 변경하지 않았다.

## 입력과 실행

```python
from kiwoom_collector.tracking_review import load_report, review_reports

report = load_report('diagnostics/daily_chart/mock/EXISTING_RUN/report.json')

# 실제 선정 당시 보존한 Snapshot과 출처가 명확한 캘린더를 별도로 제공한다.
# snapshot/calendar/basis를 보고서에서 임의 생성하지 않는다.
result = review_reports(
    [report], snapshot=existing_snapshot, episode_id=existing_snapshot.episode_id,
    calendar=reviewed_calendar, as_of=analysis_cutoff_kst,
    basis=reviewed_d0_basis, evidence=(),
)
```

`load_report(path)`는 지정 JSON만 UTF-8 BOM을 허용하여 읽는다. 폴더 검색·최신 파일
자동 선택·출력 저장·운영 DB 조회는 없다. JSON dict를 직접 `review_reports`에 전달할
수도 있다. 기준 시각 `as_of`는 timezone-aware 입력을 요구한다.

실제 D0 기록이 없을 때의 오프라인 검토:

```python
result = review_reports([report], as_of=analysis_cutoff_kst)
assert result['status'] == 'd0_snapshot_missing'
assert result['observations'] == ()
assert result['eligible_for_tracking'] is False
```

### 검토 입력

| 인자 | 계약 |
| --- | --- |
| reports | 기존 일봉 진단 JSON dict들의 iterable. 한 종목·환경·시장·API·수정 기준만 허용 |
| snapshot | 실제 선정 때 보존한 불변 Step 7-A Snapshot. 없으면 관측 생성 보류 |
| episode_id | snapshot.episode_id와 정확히 일치해야 함. ID만으로 D0를 만들 수 없음 |
| calendar | 기존 TradingCalendar. 누락 시 모든 거래일 미확인 |
| as_of | 분석 기준 KST 시각. Snapshot 포착 이전이면 거부 |
| basis | 외부에서 검토한 D0 비교 기준. 마감 확정 증거를 대체하지 않음 |
| evidence | 기존 ClosingEvidence tuple. 기본은 빈 tuple이며 자동 생성하지 않음 |

`basis`는 아래 키를 사용한다. 실제 근거를 확인하지 않고 이 값들을 참으로 채워서는 안 된다.

```python
reviewed_d0_basis = {
    'price': 'KRW', 'volume': 'share', 'turnover': 'million_KRW',
    'adjustment': '0', 'session_basis': 'chart_session', 'd0_compatible': True,
}
```

ka11081을 사용한다면 session_basis는 regular_session이어야 한다. API 이름에 따른
라벨 일치만으로 실제 시간외 포함·집계 범위가 검증되는 것은 아니며, D0 기준과의
호환성은 근거 제공자가 확인한다. 기준 미제공·단위 오류·수정 기준 혼합은 추정 보정하지 않는다.

## 연결 및 검증 절차

1. `parse_report(document)`는 documented_fields_only 정책이 명시된
   `response_projection`만 받아 기존 B1으로 재파싱한다. 캐시된 rows, b1_validated,
   eligible_for_tracking 같은 파생 플래그를 신뢰하지 않는다. 비문서 필드·중첩 행 값은
   거부한다. HTTP/API 실패·구조 불일치·누락 시각도 정상 입력으로 보정하지 않는다.
2. Snapshot ID·종목코드·환경·시장과 API·수정 기준을 검사한다. 다른 종목이나
   mock/real, 원주가/수정주가, 일반/정규장 차트 혼합은 ValueError로 중단한다.
3. 각 조회를 기존 `daily_tracking_adapter.to_tracking`에 **개별 전달**한다.
   기준일이 다른 D+1·D+2 조회를 하나의 페이지 묶음으로 합치지 않는다.
4. 어댑터를 통과한 Observation만 기존 `leader_tracking.analyze`에 전달한다.
   근거가 없는 후보 가격은 별도 provisional 영역에만 남긴다.
5. 같은 projection의 중복 수입은 한 번으로 처리한다. 동일 거래일·동일 수신 시각에
   서로 다른 원문 값이 있으면 임의 선택하지 않고 중단한다. 늦게 수신한 같은 날짜의
   정정 자료는 새로운 이벤트로 유지하며, 원본 이벤트와 D0 Snapshot을 덮어쓰지 않는다.

`projection_id`와 source_kind=redacted_api_projection은 정제된 투영의 식별자이다.
전체 API 원본 응답의 fingerprint나 인증된 원천 증명으로 주장하지 않는다.
`parse_report`가 반환하는 SavedPage의 page fingerprint와 row fingerprint를 기준으로
검토 근거를 결속할 수 있다. 근거의 진위는 이 모듈이 인증하지 않는다.

## 출력

| 필드 | 의미 |
| --- | --- |
| status | d0_snapshot_missing / calendar_unknown / reviewed. reviewed는 승인이나 추적 완료가 아님 |
| episode_id, as_of, trade_dates | 연결한 에피소드·분석 시각·D0/D+1/D+2 실제 거래일 또는 None |
| observations | 외부 근거와 기존 어댑터를 통과한 Observation tuple |
| tracking | 기존 analyze 결과. D0가 없으면 None; metrics·patterns·완료 상태의 기존 의미 유지 |
| provisional | 확정되지 않은 후보 가격·거래량·거래대금과 별도 잠정 변화율 |
| holds | projection_id, row_fingerprint, trade_date 및 보류 사유. 미래 보고서는 가격·행을 노출하지 않음 |
| inputs | 사용 시점에 가시적인 조회의 출처·코드·환경·시장·기준일·수신 시각·페이지 상태 |
| duplicate_imports_ignored | 같은 projection의 재수입을 무시한 횟수 |
| eligible_for_tracking, candidate_confirmed | 항상 False. 분석 성공으로 자동 승인하지 않음 |

결과에는 불변 Observation 객체와 Decimal이 포함될 수 있다. 메모리 검토 결과로
사용하며 새 영속 저장 포맷은 도입하지 않았다. Decimal·모델의 명시적 직렬화가 필요한
호출자는 원본 객체를 바꾸지 않고 자체 출력 경계에서 처리한다.

### 잠정값과 확정값

잠정 변화율은 `provisional[*]['provisional']['change_d0_percent']`에만 둔다.
원주가·단위·세션·D0 호환성이 명시되고 거래일이 확인되며, 일봉 검증 사유가 없고
Snapshot에 확인된 D0 종가가 있을 때에만 `(후보가격 / D0종가 - 1) * 100`을 계산한다.
이는 체결 기반 투자 수익률이 아니다. 기준가격이 없으면 None이며 과거 일봉으로
Snapshot을 채우지 않는다. 늦게 확인된 D0 종가는 외부 근거가 있는 별도 관측으로
확정 엔진에 전달할 수 있지만 Snapshot은 그대로다.

현재 단순 정책에서는 종가·거래량·거래대금 누락 등 일봉 검증 사유가 있으면 잠정
변화율도 보류한다. 후보 숫자가 보여도 reasons를 함께 확인해야 하며, 단위 오류를
자동 환산해 해소하지 않는다. VWAP는 None이고 새로운 수익 성과 지표·점수·매수 신호는 없다.

## 거래일 및 시간 정책

`calendar.offset(D0, 0/1/2)`만 사용한다. 달력 날짜를 더하거나 일봉 날짜 배열로
거래일을 추정하지 않는다. 미확인 날짜가 경로에 있으면 calendar_unknown이며,
필요한 D0..D2가 모두 확인되지 않은 상태에서는 잠정 변화율도 보류한다.

trade_date, response_received_at, verified_at은 다른 정보이다. 수신 시각이 as_of보다
늦은 보고서는 후보 가격·확정 관측·입력 상세에서 제외하고 보류만 반환한다. 거래일이
기준일 이후이거나 미래인 행도 후보 가격에 노출하지 않는다. 확정 관측의 가용 시각은
기존 어댑터의 verified_at을 사용한다. 과거 거래일·15:30·파일 수정 시각으로 수신 또는
검증 시각을 만들어 넣지 않는다. 검증되지 않은 후보의 market_at/verified_at은 None이다.

보류 사유 예: d0_snapshot_missing, d0_basis_unverified, d0_reference_price_missing,
trading_day_unverified, response_not_yet_available, close_not_verified,
closing_evidence_missing, closing_chronology_unverified, basis_unit_mismatch:price,
report_unit_mismatch:volume, adjusted_history_not_d0_observed_prices.

## 독립 테스트와 기존 실측 자료

```powershell
python -m unittest discover -s tests -p test_tracking_review.py -v
python -m unittest discover -s tests
```

새 fixture의 가격·캘린더·마감 근거는 전부 SYNTHETIC이다. fixture의 2026-10-27
휴장은 합성 설정이며 실제 시장 휴장 선언이 아니다. 정상 CI는 이 fixture만 사용하며
로컬 보고서·인증서·환경 파일에 의존하지 않는다.

실측 보고서는 Git 제외 경로에서 `load_report`로 명시적으로 읽어 로컬 검증한다.
삼성전자·SK하이닉스는 서로 다른 에피소드이므로 각각 검토한다. 기존 두 보고서를
오프라인 검토했을 때 각 600행을 파싱했으며 D0·캘린더·근거를 만들지 않아
d0_snapshot_missing, 확정 관측 0개, 변화율 계산 0개, eligible_for_tracking=false였다.
18:00 KST 기준 삼성전자 후보는 보이지만 21:30에 수신한 SK하이닉스 후보는 0개였다.
전체 실측 응답·인증정보·CA 번들은 fixture나 Git에 복사하지 않았다.

Step 7-C3에서는 실제 D0 선정 Snapshot, 출처가 확인된 캘린더, 원주가·단위·세션·마감
확정 및 D0 호환 근거를 먼저 준비해야 한다. 모의 자료의 실전 원천 동일성·HTS 대조는
미검증이다. C2는 독립 검토 연결이며 운영 DB·수집기·스케줄러·주문 연동이 아니다.

검증 결과: 독립 연결 테스트 **34개 통과**. 전체는 기존 688개를 포함한 **722개 통과,
실패 0·오류 0·스킵 0, 91.732초, 종료 코드 0**이다. 기존 Step 7-A 엔진과 B1
파서·어댑터·진단 CLI 및 운영 코드는 변경하지 않았고 실제 OAuth·일봉 호출은 하지 않았다.
