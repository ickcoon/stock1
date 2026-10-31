# C12-B 읽기 전용 분류 미리보기

```powershell
.\.venv\Scripts\python.exe -B -m kiwoom_collector.classification_preview --db diagnostics/master/c10-master.db --at "2026-10-31T12:00:00+09:00"
```

합성 DB에는 `--synthetic`을 추가한다. DB의 synthetic 값과 다르면 중단한다.
시각은 시간대가 있는 명시적 기준 시각이어야 한다. 결과는 JSON 표준 출력만 사용한다.
DB 경로를 만들거나 마이그레이션하지 않는다. 기존 read_master의 mode=ro,
immutable, query_only, 해시 전후 비교를 재사용하며 WAL/SHM/journal이 있으면 중단한다.
API·수집·게시 경로는 호출하지 않는다.

집계 모집단은 기준 시각에 종목 이력이 있는 stock_master 고유 코드다.
catalog present=0도 포함하므로 현재 상장 종목 수 또는 공식 종목 총수로 해석하지 않는다.
이력이 없거나 동일 시각 이력이 충돌한 코드는 excluded_identity_count로 별도 보고한다.

markets.UNKNOWN, product_types.unknown, sector_states.unclassified는 독립 차원이다.
상품 유형별 verified/unverified는 저장된 검토 상태이며 새로운 검증을 의미하지 않는다.
섹터 confirmed는 유효한 verified 태그와 기존 C11 대표 매핑의 일관성을 뜻한다.
미검토 proposed 태그는 후보만 표시한다. 미분류는 15개 섹터에 임의 배분하지 않고
sector_states.unclassified에 따로 센다. 기타로 자동 배정하지 않는다.
대표 태그 중복은 conflict로 보고한다. multiple_tags_count에는 proposed도 포함하며
records.tags에 상태·출처·근거를 구분한다. analysis_classification_state는 기존
Queries.classification 결과로 시장·유형에 의한 활성 입력 제외를 함께 확인할 수 있다.

C10 기본 16개, C11 목표 15개, DB 저장 업종 및 양방향 이름 차이를 보고한다.
대표 연결은 아래 C12-D 규칙을 읽기 전용으로 적용한다. DB 태그는 변경하지 않는다.
기존 classification.py의 관심도 변화 판정은 종목 분류와 목적이 달라 호출하지 않는다.

소규모 합성 검증:

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -p test_classification_preview.py -v
```

실제 집 PC에서는 안정적으로 닫힌 개발 마스터에 대해 실행하여 시장별 수,
상품 유형 근거, UNKNOWN 보존, 대표 중복, 수동 태그 및 원본 해시 불변을 검증한다.
실제 DB 검증 전에는 문서에 기록된 4,314/1,571 등의 재현을 보장하지 않는다.

## C12-D 대표 섹터 연결

기준은 기존 C11 SECTORS의 15개이며 새로운 업종을 추가하지 않는다.

| C10 원래 업종 | C11 대표 섹터 | 처리 |
|---|---|---|
| 반도체, 자동차, 로봇, 조선, 2차전지, 제약·바이오, 금융 | 같은 이름 | 정확 일치 |
| 원전, 전력 | 원전·전력 | 명확한 하위 업종 연결 |
| 방산, 우주항공 | 방산·우주 | 명확한 하위 업종 연결 |
| 화학 | 화학·소재 | 명확한 하위 업종 연결 |
| 철강 | 철강·기계 | 명확한 하위 업종 연결 |
| 건설 | 건설·인프라 | 명확한 하위 업종 연결 |
| 통신, 인터넷·게임 | 없음 | 해석 필요, 자동 연결 보류 |

IT·AI, 소비재·유통, 기타는 C10의 모호하거나 미지원인 업종을 자동 수용하지 않는다.
원래 industry 이름에만 정확 일치하며 테마 태그를 대표 업종으로 승격하지 않는다.
confirmed는 기존 유효 verified 태그에 출처와 근거가 있고 대표 선택이 모호하지
않을 때만 허용한다. proposed는 연결돼도 proposed다. C11 catalog 판정은 유지하며
기존 미검토 C11 매핑을 C10 연결로 우회하지 않는다.
기존 DB의 이력은 그대로 보존하며 미리보기의 유효 태그에도 원래 이름, 상태, 출처,
근거, 원본 시각, 유효기간, 해시를 출력한다. sector_links는 표시용 관계다.

한 종목은 대표 섹터 한 곳에만 집계한다. 두 대표가 같은 대분류로 연결되더라도
중복 대표 충돌은 해소하지 않는다. 복수 보조 태그는 유지한다.
거래대금 경로는 변경하지 않으며 기존 aggregate_turnover의 검증된 업종 대표 1개,
테마 중복 제외, 종목 관측 중복 거절 규칙을 유지한다. 미리보기 결과는 활성 분석에
자동 게시하지 않는다. 실제 연결 전 집 PC DB의 분류 근거와 활성 상태를 검증한다.

## 집 PC 실데이터 검증 명령

최신 main을 받은 집 PC 개발 저장소에서 실행한다. 수집이 끝나고 DB에 쓰는 프로세스가
없는 상태여야 한다. sidecar가 있으면 명령은 중단하며 삭제·체크포인트하지 않는다.

```powershell
Set-Location C:\Users\dada1\Downloads\stock1
$previewAt = (Get-Date).ToString('o')
.\.venv\Scripts\python.exe -B -m kiwoom_collector.classification_preview --db diagnostics/master/c10-master.db --at $previewAt
```

`--synthetic`은 사용하지 않는다. `unique_stocks`, `markets`, `product_types`,
`sector_states`, `duplicate_primary_count`, `multiple_tags_count`, `excluded_identity_count`,
`provenance.hash_unchanged`를 확인한다. 4,314종목과 UNKNOWN 1,571건은 이전 집 PC
실수집 결과이며 이후 수집 이력에 따라 달라질 수 있다. 분류 근거의 사실성, 공식 종목
총수 대비 완전성, 통신·인터넷·게임 연결은 아직 검증되지 않았다.
