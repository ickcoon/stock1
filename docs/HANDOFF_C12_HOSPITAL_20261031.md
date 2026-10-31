# C12 중간 인계 — 집 PC → 병원 PC

작성: 2026-10-31 KST. 기준 개발 커밋 ce399ce 이후 C12-F~I 코드·테스트와 C12-J/K 조사를 정리한 인계다. 실제 API 호출 및 운영 설정 변경은 하지 않았다.

## 구현 상태

- equity_eligibility: strict included/excluded/pending과 별도 실용 후보 판정을 제공한다. 보통주 verified와 analysis_candidate는 다르다.
- 현재 마스터 4,314개: 분석 후보 2,556, 확정 제외 1, 잠정 제외 1,570, 판단 보류 187. 원본 DB verified를 변경하지 않았다.
- am_candidate_adapter 및 am_input_adapter --candidate-filter --master-db: 0198(ka00198)·0186(ka10032)을 기준 시각의 마스터에 연결한다.
- 원본 순위/반복 관측/시총/거래대금은 보존한다. 제외·보류·미매칭은 별도 목록이며 섹터 동반 상승·리더 선정에 사용하지 않는다.
- AM_MOMENTUM의 반복 TOP20·섹터 동반 상승·시총 정책과 거래대금 절대 하한 없음 정책을 유지한다. C12 제안 섹터를 자동 확정하지 않는다.
- 상세: EQUITY_ELIGIBILITY.md, C12G_PRODUCT_EVIDENCE_DESIGN.md, C12H_PRACTICAL_CANDIDATES.md, C12I_AM_CANDIDATE_INTEGRATION.md.

## C12-J/K 실제 자료 조사

- 집 PC data/stock1.db: 0198은 전부 mock. 2026-10-20 test_mode=0 10회/200행, 10월 19일 test_mode=1 1회/20행.
- data/stock1-before-top20.db는 같은 관측 자료의 백업이므로 중복 집계하지 않는다.
- 집 PC에는 0186 저장 DB 및 real 0198 자료가 없다. 10월 28일 dashboard input은 fixture이며 실제 자료로 사용하지 않는다.
- diagnostics/master/c10-master.db는 real/API 원본 기반이며 2026-10-31에 등록됐다. 병원 운영 DB 경로와 real/mock 자료 존재 여부는 미확인이다.
- mock 10월 20일 09:50 TOP20과 현재 마스터의 날짜 간 코드 매칭은 20→19개. 제외 122630 KODEX 레버리지는 8/ETF 원본 쌍에 따른 잠정 제외다.
- 오전 4회차(09:20/30/40/50) 80행, 고유 34개 중 현재 기준 후보 33개/잠정 제외 1개. confirmed 섹터 0, proposed 1(TS트릴리온→화학·소재), unclassified 32개.
- 10월 20일 당시 기준 마스터 identity는 0개다. 현재 마스터로 당시 오전 판정을 재현했다고 주장하지 않는다.
- raw dt/tm은 각 수집 시각보다 1분 전이다. 수집 시각을 시장 관측 시각으로 덮지 않는다. 같은 날의 검증된 거래대금·시총도 확보되지 않아 실제 주도주 선정은 미검증이다.
- 상세 JSON/원본 목록/실행 로그는 집 PC reports 아래 로컬 자료로만 보존한다. Git에 포함하지 않는다.

## 병원 PC 다음 작업 — 순서대로

1. 운영 수집기의 실행 상태·현재 작업 경로·DB 인수를 읽기 전용으로 확인한다. 프로세스 중단/재시작 및 스케줄러 수정은 하지 않는다. 명령줄을 공유할 때 인증정보는 노출하지 않는다.
2. 실제 0198 DB와 별도 0186 DB의 경로·존재 여부를 확인한다. 기본 후보는 운영 프로젝트 data/stock1.db지만 실제 실행 인수를 우선한다. 0186가 별도 테스트 DB에만 있거나 아예 없을 수 있으므로 추정하지 않는다.
3. 환경(mock/real), 출처(fixture/live_test/API 응답), test_mode, 거래일, 수집/수신/원본 시장 시각, 페이지 완전성, 자료 수를 구분한다. test_mode=1이라는 이유만으로 API 실응답을 합성으로 단정하지 않고 source_kind와 함께 확인한다.
4. 기존 실제 DB가 있고 이번에 명시한 범위에서 SQLite Online Backup으로 별도 스냅샷을 만든다. 기존 online_snapshot 모듈을 재사용한다. 신규 목적지만 쓰며 원본 SQL 쓰기·checkpoint·저널 변경·직접 파일 복사·자동 재시도는 금지한다. 상세는 ONLINE_SNAPSHOT.md.
5. success=true/status=success, source_connection_released=true, quick_check=ok, 목적지 SHA-256/크기 및 자료 수를 확인한다. 목적지에서 추가 integrity_check/foreign_key_check와 날짜·환경별 0198/0186 건수를 확인한다. 실패·partial 산출물은 성공 자료로 사용하지 않는다.
6. **별도 승인 전까지 집 PC 전송은 하지 않는다.** 병원에 스냅샷과 manifest를 보존하고 경로·자료 수·해시·환경·날짜 요약만 인계한다. 인증정보·운영 DB·스냅샷·로그를 Git에 올리지 않는다.

## 스냅샷 방식과 경계

수집 중 .db만 복사하면 WAL 누락·시점 혼합 위험이 있다. README의 일반 백업 절차는 수집 종료를 전제로 한다. 무중단 확인에는 기존 online_snapshot의 64페이지/step·5초 예산을 사용한다.
읽기 전용 source는 논리 DB 쓰기를 막지만 SHM 참여와 짧은 잠금/지연 가능성까지 없애지는 않는다. BUSY/LOCKED·시간 초과 시 중단하며 운영 프로세스는 건드리지 않는다.
0198·0186가 별도 DB이면 각각의 백업은 일관되지만 두 DB의 동일 시점을 보장하지 않는다. 관측 시각과 환경을 대조해 연결한다.
이번 중간 wrap-up에서는 병원 접속·스냅샷 생성·전송을 실행하지 않았다. 과거 ONLINE_SNAPSHOT.md의 미승인 기록은 당시 상태다. 이번 사용자가 지정한 다음 병원 작업 범위는 위 1~5이며 전송은 여전히 별도 승인 대상이다.

## 실제 자료 확보 후 최소 분석

추가 필터 개발은 중단한다. 기존 후보 필터와 AM_MOMENTUM을 사용한다.
실제 TOP20·거래대금 상위의 합집합 중 상품 후보를 통과한 종목만 우선 분류하고, 반복 TOP5와 양쪽 공통 종목부터 검토한다. 기존 15개 대표 섹터는 유지한다.
ka10099 원본 upName은 catalog_items.raw_industry 및 raw_json에 저장돼 있다(2,732개 비어 있지 않음, 1,582개 빈 값). upSizeName은 규모, companyClassName은 회사분류이며 산업과 다르다. ka10100도 upName을 제공하지만 신규 호출은 이번 범위가 아니다.
전기/전자 전체를 반도체로 확정하는 등 넓은 업종의 일괄 승격은 하지 않는다. 종목코드별 출처·근거·검토 시각·유효기간을 남기고 모호하면 proposed/pending이다. 미래의 검토 이력을 과거 오전에 소급 적용하지 않는다.

## 검증

- C12 및 관련 경로 테스트 129개 PASS(7.231초).
- 전체 회귀 테스트 1,101개 PASS(134.378초), 실패/오류/건너뛰기 없음.
- 최초 전체 실행은 CLI 자식 프로세스가 PYTHONPATH를 재설정하면서 requests를 찾지 못해 실패했다(29 failures/8 errors). 제품 코드는 변경하지 않고 로컬 테스트 전용 가상환경에 기존 의존성 사본을 배치했다. 해당 CLI 검사 9개 PASS 후 전체 회귀를 재실행해 위 결과를 확인했다.
- 격리 소스 사본에서 실행했고 커밋 대상 Python 9개 파일과 동일함을 확인했다. 기존 인증환경 변수를 제거하고 Python 외부 통신 및 원본 data/diagnostics/.env 접근을 차단했다. 브라우저 합성 테스트의 loopback만 허용했다.
- 원본 .env 및 기존 DB/sidecar의 크기·mtime·SHA-256 전후 동일. 테스트 로그·최초 실패 기록·보호 파일 검증은 reports/c12-wrapup-20261031/에만 보존하며 Git에 올리지 않는다.
