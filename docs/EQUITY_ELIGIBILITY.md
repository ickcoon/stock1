# C12-F 일반 개별주 분석 대상 정책

`kiwoom_collector.equity_eligibility`는 C12 preview 위에 붙이는 읽기 전용 정책이다.
원본 시장·상품 유형·검증 이력·섹터를 변경하지 않는다. 기존 운영 분석 경로에는 자동 적용하지 않는다.

## 결정 규칙

- `included`: 검증된 common_stock, KOSPI/KOSDAQ, 검증된 상장 identity, listed, catalog present, 확인된 원본 출처.
- `excluded`: 검증된 ETF/ETN/REIT/SPAC/mutual_fund/infrastructure_product.
- 검증된 preferred_stock은 기본 제외. `include_preferred=True` 또는 CLI `--include-preferred`로 허용하되 나머지 포함 조건은 동일하다.
- 유형 미검증, 검증 근거 없음, unknown/other/미지원 유형, identity 충돌은 pending이다. 이름이나 marketName으로 유형을 승격하지 않는다.
- 검증된 제외 상품은 시장이 UNKNOWN이어도 상품 유형만으로 제외할 수 있다. 미검증 상품 힌트는 시장과 무관하게 pending이다.
- 포함 가능한 유형이라도 시장·현재 catalog 존재·상장 상태·원본 출처가 불확실하면 pending이다.
- 이 정책의 included는 상품/시장 범위 통과를 뜻한다. 섹터 확정이나 시가총액·시세 적격성을 대신하지 않는다.

각 종목에 별도 `eligibility.state/reasons`를 부착한다. 원본 기록은 유지하며 DB에는 기록하지 않는다.
집계는 before_count/after_count와 included/excluded/pending 및 reason_counts를 반환한다.
사유가 여러 개인 종목이 있어 reason_counts 합계는 종목 수와 다를 수 있다.

## 거래대금

`sector_turnover(observations, classification, include_preferred=False)`를 명시적으로 호출한다.
입력 classification은 동일 기준 시각의 C12 preview 결과여야 한다.
eligibility는 함수 안에서 재계산하며 included + confirmed + 중복 대표 태그 없음 + 기존 15개 섹터의 대표 섹터인 종목만 합산한다.
proposed/conflict/미분류·제외·pending·분류 기록 누락은 합산하지 않고 사유를 반환한다.
기존 C11 aggregate_turnover를 재사용하여 종목별 관측 중복을 거부하고 대표 섹터 한 곳에만 거래대금을 합산한다.
테마별 합계는 가산하지 않는다. 실제 거래대금 DB는 이번 단계에서 읽거나 변경하지 않았다.

## 실행

```powershell
python -B -m kiwoom_collector.equity_eligibility --db diagnostics/master/c10-master.db --at "2026-10-31T18:30:00+09:00"
```

기존 의존성이 설치된 Python 환경을 사용한다. `--synthetic`은 합성 DB에만 사용한다.
API·인증정보 읽기·수집·DB migration/쓰기 경로는 호출하지 않는다.

## 집 PC 실데이터 결과 (2026-10-31)

| 항목 | 종목 수 |
|---|---:|
| 필터 전 | 4,314 |
| 포함 / 필터 후 | 0 |
| 제외 | 0 |
| pending: security_type_unverified | 4,314 |

제외 사유별 ETF/ETN/REIT/SPAC/mutual_fund/infrastructure_product/preferred_stock_disabled는 모두 0개다.
실제 상품이 없다는 뜻이 아니다. 현재 전 종목 type_verified=false이므로 확정 제외하지 않았다.
기존 C12-E 원본 기준 ETF 1,186개, ETN 계열 359개, 리츠 23개, 인프라투자금융 2개, 뮤추얼펀드 1개가 있으며 스팩 힌트 64개가 있다.
이 원본 단서는 상품 유형 검증 전까지 pending으로 유지한다. 일반 보통주·우선주도 별도 근거 검증이 필요하다.
DB는 mode=ro/immutable/query_only 및 Windows 쓰기 공유 차단으로 접근했고 SHA-256 전후 동일:
`9da603a0ab476866a2f86dd3f8185c384093b72c70a921366f2a6d9bff357066`.
상세 결과는 reports/c12f-home-eligibility-20261031.json에 저장했다.

## 검증과 다음 최소 범위

새 합성 정책 테스트 6개와 기존 C12 preview 테스트 8개 통과. 전체 회귀 테스트는 실행하지 않았다.
합성 DB에서 기존 유형 검증 이력 재사용과 DB 해시 불변, 상품 제외·미확인 보류·우선주 설정·거래대금 제외 및 중복 거부를 확인했다.
Commit/Push는 수행하지 않았다.

다음 단계는 원본 근거를 검토하여 상품 유형 review를 준비하는 것이다.
현재 C11 review 입력은 mutual_fund/infrastructure_product를 지원하지 않으므로 해당 유형의 저장 계약 확장은 별도 구현이 필요하다.
이후 한 개의 섹터 분석 진입점에 이 집계 함수를 연결하고 동일 기준 시각의 관측만 전달한다.
우선주 설정은 이번 함수/CLI 옵션으로 제공하며 운영 설정 파일·스케줄러 연동은 다음 범위다.
