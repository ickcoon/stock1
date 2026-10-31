# C12-H 실용적 분석 후보 필터

2026-10-31 집 PC 실데이터 읽기 전용 검증. C12-F의 엄격한 eligibility는 그대로 유지하고 equity_eligibility.py에 별도 candidate_decision/apply_candidates와 CLI --candidates를 추가했다.

## 판정과 근거

| state | confidence | 의미 |
|---|---|---|
| confirmed_excluded | verified | 기존 검증된 제외 유형 또는 문서로 뒷받침된 ETF warning |
| provisional_excluded | provisional | 원본 marketCode/marketName의 정확한 상품군 조합 일치 |
| analysis_candidate | provisional | KOSPI/KOSDAQ 시장·상장 identity가 확인되고 제외 단서가 없는 탐색 대상 |
| pending | pending | 출처·시장·유형 충돌, 미확인, 스팩/우선주 검토 단서 등 |

확정 제외는 기존 검증 유형과 근거가 있거나 orderWarning=1 + 8/ETF가 일치할 때다. [키움 공식 명세](https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-REST-API/main/kiwoom/_data/kiwoom_api_spec.json)는 warning 1을 ETF 전용이라고 설명한다(C12-G 조사 재사용).

잠정 제외 조합은 8/ETF, 60/ETN, 70/ETN(손실제한), 90/ETN(변동성), 6/리츠, 2/인프라투자금융, 4/뮤추얼펀드다.
코드와 명칭은 서로 다른 원본 필드지만 같은 시장 분류 체계에 속한다. 완전히 독립된 외부 증거 두 개로 간주하지 않으며, 이 한계 때문에 verified가 아니라 provisional이다. 코드만 또는 명칭만 맞는 경우는 잠정 제외하지 않는다.

스팩 회사분류 또는 미검증 SPAC 힌트는 pending이다. 우선주 미검증 힌트와 이름 끝의 우/우B/우C/우선주 등은 pending 검토 단서로만 사용한다.
이름의 스팩/ETF/ETN 또는 끝의 리츠도 검토 단서다. 이름만으로 제외나 verified 판정을 하지 않는다.
단순 리츠 부분문자열은 메리츠금융지주·블리츠웨이엔터테인먼트 등을 잘못 보류할 수 있어 사용하지 않는다. 이 사례도 합성 테스트에 포함했다.
이름 규칙은 완전한 우선주·상품 식별을 보장하지 않으므로 미검출 가능성이 남는다.
검증된 우선주는 기본 확정 제외하고 --include-preferred로 후보에 허용하되 미검증 우선주 단서는 설정과 무관하게 pending이다.

원본 출처·종목코드/이름·시각·목록 존재 여부·유형 근거 충돌을 먼저 점검한다. 충돌이면 기존 verified 주장도 자동 우선하지 않고 pending 처리한다.
product_source에는 raw 필드와 source/run_id/applied_at/updated_at을 담고, 판정에는 confidence/reasons/rule_version/raw_sha256을 보존한다.
C12 preview의 DB provenance와 기존 type_verified/type_evidence/시장/섹터/태그 이력도 그대로 출력한다.

## 실제 결과

| 구분 | 종목 수 |
|---|---:|
| 필터 전 | 4,314 |
| 확정 제외 | 1 |
| 잠정 제외 | 1,570 |
| 분석 후보 / 필터 후 | 2,556 |
| 판단 보류 | 187 |

확정 제외: ETF 265690 1개(orderWarning=1 및 8/ETF).
잠정 제외: ETF 1,185, ETN 359, REIT 23, 인프라투자상품 2, 뮤추얼펀드 1개.
보류: 스팩 원본/힌트 64, 우선주 검토 단서 117, 이름의 스팩 단서 6개.
마지막 6개도 확정 SPAC로 집계하지 않았다.
기존 1,635개 후보 중 스팩 64개는 잠정 제외가 아닌 보류다.
최초 검증 과정에서 단순 리츠 부분문자열 때문에 보류됐던 2개는 수정 후 분석 후보로 복귀했다.

분석 후보는 제외 단서가 없는 탐색 범위이며 검증된 일반 보통주가 아니다.
실제 DB의 type_verified는 전부 false이며 strict eligibility는 여전히 포함 0/제외 0/pending 4,314다.
대표 섹터 confirmed/proposed는 변경하지 않았다. 기존 sector_turnover는 strict eligibility 및 confirmed 섹터만 사용하는 계약을 유지한다.

## 실행 및 최소 연결 방법

```powershell
python -B -m kiwoom_collector.equity_eligibility --db diagnostics/master/c10-master.db --at "2026-10-31T19:00:00+09:00" --candidates
```

기존 의존성이 있는 Python 환경을 사용한다. 합성 DB에는 --synthetic을 추가한다.
프로그램에서는 동일 기준 시각으로 얻은 preview를 apply_candidates에 전달한다.
TOP20/거래대금 상위 관측과 stock_code로 조인하여 candidate_eligibility.state == analysis_candidate인 종목만 탐색 대상으로 표시한다.
순위·관측시각·거래대금·원래 행은 보존하고 excluded/pending 이유도 별도 표시한다. 분류 누락은 pending으로 처리한다.
TOP20만 가진 경우 필터링 후 20개 미만이어도 후순위 자료 없이 임의 보충하지 않는다.
후보 상태로 strict eligibility/type_verified를 덮거나 섹터 미분류를 자동 배정하지 않는다.
이번에는 운영 TOP20·거래대금 경로에 연결하거나 실제 관측 DB를 조회하지 않았다.

## 검증 및 남은 문제

- 관련 합성 테스트 20개 PASS: 후보 6 + 기존 eligibility 6 + preview 8. source/시각/유형 충돌, 기존 strict 계약, 이름 오탐, 원본 불변을 검증했다.
- 전체 회귀 테스트·Commit/Push·API 호출·운영 변경 없음.
- 실제 DB mode=ro/immutable/query_only, Windows 쓰기 공유 차단 및 전후 해시 불변 확인. 기존 DB와 인증정보·환경은 수정하지 않았다.
- DB SHA-256: 9da603a0ab476866a2f86dd3f8185c384093b72c70a921366f2a6d9bff357066.
- 상세 결과: reports/c12h-home-candidates-20261031.json.

다음 최소 작업은 TOP20 또는 거래대금 상위의 한 진입점에서 후보 결과를 조인해 표시하는 것이다.
미해결: 우선주/스팩 코드별 독립 근거, 잠정 상품군의 응답 코드 계약 검증, 일반 보통주 확정, 이름 기반 검토 단서의 오탐·미탐, 시점에 맞는 후보 판정 연결, 15개 섹터의 미확정 문제다.
