# Step 5-B — ka10032 독립 거래대금상위 테스트 수집기

`kiwoom_collector.turnover_rank`는 기존 0198·ka10095·Radar·스케줄러와 분리된 모듈이다. 기본 사용은 오프라인 fixture 재생이다. 새 테스트 SQLite 파일만 만들며 기존 DB·파일을 재사용하거나 마이그레이션하지 않는다. 운영 DB·수집 엔진·인증정보·API 설정·정기 일정을 변경하지 않는다. 0181(ka10027)과 매매 후보 자동 확정은 구현하지 않는다.

## 공식 계약과 구현 범위

근거: [ka10032 공식 포털](https://openapi.kiwoom.com/m/guide/apiguide/05/ka10032), [프로젝트 기준 공식 JSON 명세](https://github.com/Kiwoom-Securities/Kiwoom-REST-API/blob/953e5dbff123f437ab4d11a78a95191a685eb51f/kiwoom/_data/kiwoom_api_spec.json), [Step 5-A 대조 기록](RANKING_0186_0181_RESEARCH.md). 포털의 단위·필드와 5-A에서 보존한 명세를 기준으로 구현했다. 샘플은 이 계약으로 작성한 **가상 fixture**이며 시장 실응답이나 단위 실측 증거가 아니다.

- `POST /api/dostk/rkinfo`, `api-id: ka10032`, JSON·Bearer 인증. 실전/모의 도메인은 기존 Credentials의 고정 두 호스트만 사용한다.
- 필수 본문 3개: `mrkt_tp`(000 전체/001 코스피/101 코스닥), `mang_stk_incls`(0 관리 제외/1 포함), `stex_tp`(1 KRX/2 NXT/3 통합). 초기 계획 기본값은 전체·관리 제외·KRX다.
- 응답 목록 `trde_prica_upper`와 13개 행 필드를 허용 목록으로 보존한다: `stk_cd`, `now_rank`, `pred_rank`, `stk_nm`, `cur_prc`, `pred_pre_sig`, `pred_pre`, `flu_rt`, `sel_bid`, `buy_bid`, `now_trde_qty`, `pred_trde_qty`, `trde_prica`.
- `now_rank`는 거래대금 순위, `pred_rank`는 전일 거래대금 순위다. 행 위치로 순위를 만들거나 0198 조회순위 필드로 저장하지 않는다.
- 거래대금은 백만원, 가격·호가는 원, 등락률은 %, 거래량은 주라는 공식 선언을 기록한다. 값·가격 부호 의미·실제 단위·누적 범위·거래일/체결 시각은 별도 검증 대상이다.
- ETF·ETN·우선주·SPAC·정리매매를 개별 제외하는 API 인자는 없다. 관리 제외만으로 다른 유형이 제외됐다고 판단하지 않는다. 현재는 사후 필터나 종목 분류 API도 추가하지 않으며 '다른 유형은 개별 필터하지 않음'을 기록한다.

응답에 날짜/시각 필드가 없으므로 서버 기준 시각과 거래일은 null로 둔다. 요청 시작·응답 완료는 KST 오프셋과 함께 기록하지만 이를 체결 시각으로 대체하지 않는다. 장중 누적 거래대금의 시작·리셋·장 구간·통합 범위는 `unverified_cumulative_scope`이며 순간/구간 거래대금으로 사용하지 않는다. mock·real과 fixture·live_test는 각각 분리해 저장한다.

## 오프라인 사용 — API·OAuth·인증정보 로드 없음

```powershell
# 요청 계획만 출력: DB 생성·인증·네트워크 없음
python -B -m kiwoom_collector.turnover_rank plan --environment mock --market 000 --managed 0 --venue 1

# 아래 DB 경로는 반드시 존재하지 않는 새 파일명으로 지정
python -B -m kiwoom_collector.turnover_rank offline --fixture tests/fixtures/ka10032_synthetic.json --db data/ka10032-offline-review-new.db

# 전용 테스트 DB에만 읽기 전용 무결성 검사
python -B -m kiwoom_collector.turnover_rank inspect --db data/ka10032-offline-review-new.db
```

fixture는 API·환경·요청 조건·페이지 목록과 페이지별 HTTP 상태·요청 시작/완료·본문·연속 헤더를 포함한다. 조건이 다르면 재생을 거부한다. 저장된 실제 응답을 사용하는 경우에도 이 명령의 출처는 항상 fixture이며 live 수집·실측 검증으로 승격하지 않는다. 파일 내 토큰·원문 서버 메시지·인증 헤더·알 수 없는 응답 필드는 저장하지 않는다.

`offline`·`plan`·`inspect`는 자격 증명을 읽거나 `load_credentials`를 호출하지 않는다. 출력 경로는 모든 기존 파일·DB·심볼릭 링크를 거부하고 운영 `data/stock1.db` 경로도 거부한다. 새 파일을 배타적으로 예약한 뒤 전용 테이블을 생성하며 출력 파일을 자동 삭제하지 않는다. `inspect`는 전용 application_id를 확인하고 mode=ro + query_only로 접근한다.

## 정규화와 검증 상태

| 저장 항목 | 처리 |
| --- | --- |
| `raw_json` | 허용 필드의 원본 문자열. 코드/숫자 20자·이름 40자의 명세 길이 초과와 비문자열은 사유만 기록한다. 원문 인증/오류 메시지는 저장하지 않는다. |
| 종목코드 | 선행 0·영숫자·_NX/_AL 접미어를 보존한다. 지원 구문 검사만 하며 상장/거래소 적합성 검증 완료를 뜻하지 않는다. 접미어를 제거해 다른 거래소와 자동 병합하지 않는다. 명백한 요청 거래소 불일치는 오류로 기록한다. |
| 거래대금 | Decimal·정확한 백만 배 환산을 `turnover_won_diagnostic`에 저장한다. `unit_spec_verified=true`는 공식 단위 선언 확인이며 `unit_runtime_verified=false`, `turnover_won_verified=null`을 유지한다. |
| 현재가 | 원본 부호를 보존한다. 숫자 해석은 `signed_price_diagnostic`일 뿐 `current_price_won=null`, 가격 부호 미검증을 유지한다. 임의 절댓값 변환 없음. |
| 등락률·거래량 | 부호 있는 Decimal % 문자열·음수 아닌 정수 주 문자열. 숫자 구문만 검증하며 시점 검증 완료로 보지 않는다. |
| 미제공/잘못된 값 | null과 사유를 기록한다. 누락을 0으로 채우지 않는다. NaN·Infinity·지수 표기·쉼표/유니코드 숫자는 거부한다. 금액/거래량의 음수와 음수 0도 정상값으로 처리하지 않는다. |
| 후보 판정 | 항상 `candidate_confirmed=false`. 단위·시세·시각·커버리지와 주문 규모 대비 유동성 검증을 대신하지 않는다. |

run의 `success`와 row의 `syntax_valid`는 수집 계약·필수 관찰 필드의 구문 상태다. 실전 값이나 단위·유동성 검증 완료가 아니다. 핵심 관찰 필드(코드·이름·현재 순위·금액·현재가·등락률·거래량)가 누락/오류이면 정상 성공으로 처리하지 않는다. 전일 순위·전일 거래량·호가 등 선택 필드 부재는 허용하되 제공된 값이 잘못되면 사유를 남긴다.

## 전용 DB와 실패 처리

| 테이블 | 역할 |
| --- | --- |
| `turnover_runs` | API·명세 버전·환경·출처·test_mode=1, 요청 조건/해시, 실행/요청 시각, 상태·페이지 완료·행 수·오류 사유 |
| `turnover_pages` | 페이지 순서·요청 시작/완료·HTTP/API 코드·연속 여부·키 해시, 허용 목록 응답·행 수·상태 |
| `turnover_rows` | 페이지/행 위치, 원본 및 정규화 JSON, 코드·거래대금 순위·행 검증 상태·사유 |

기존 collections·ranking_records·quote_* 테이블을 만들거나 참조하지 않는다. FK·전용 application_id를 사용하며 운영 DB import 기능은 없다. 요청 전에 running을 커밋하고 각 페이지를 저장한다. 오류·중단 이전의 관측은 보존하되 정상 완료로 표시하지 않는다. 강제 프로세스 종료로 남은 running은 재전송하거나 복구하지 않는다. 다음 검증은 새 DB로 실행한다.

- `success`: 반환된 페이지/행의 계약 검증을 통과하고 추가 조회 신호가 종료됨. 시장 전체/원자적 스냅샷 보장 아님.
- `empty`: 유효한 0건 응답. success 데이터와 별도이며 CLI 종료코드 3.
- `partial`: 일부 행 오류, 중복, 페이지 상한/반복 키/후속 페이지 실패 등. 정상 수집으로 사용하지 않으며 종료코드 2.
- `error`: 정상 행 없이 실패. 종료코드 2. `interrupted`는 중단·종료코드 130.
- 인증·429·API 제한 코드·잘못된 JSON/목록·누락 응답은 0건 정상 데이터로 바꾸지 않는다. 쿼리 자동 재시도·인증 재발급 후 재전송은 없다.
- 코드 또는 현재 순위가 중복되면 모든 충돌 행을 invalid로 표시하고 원본을 남긴다. 첫 행/마지막 행을 임의 채택하지 않는다.
- 페이지 상한은 기본 1·최대 10이라는 로컬 검증 예산이다. 공식 최대 페이지·결과 수가 아니다. `cont-yn=Y`일 때 next-key를 전달하며 키 원문은 메모리에서만 사용한다. 반복/누락 키·빈 연속 페이지는 중단한다.
- `pagination_complete`는 추가 조회 신호가 끝난 상태일 뿐이며 행 오류나 전체 유니버스 완전성은 별도다. 페이지 요청 구간 역전·겹침·일자 변경은 보류한다. 여러 페이지가 동일 시점의 고정 순위라는 보장은 없다.

## 향후 0198 TOP20/TOP5 연결 — 읽기 전용 설계만

1. 기존 성공 0198 회차를 동일 날짜·환경·qry_tp=1·운영 흐름으로 고정한다. 현재 TOP20과 그중 조회순위 1~5위의 고유 코드를 추출한다. 이 집합·순위는 거래대금 결과로 수정하지 않는다.
2. ka10032 전용 DB를 읽기 전용으로 열어 별도의 source_kind·test_mode·요청 프로필·시각을 확인한다. 현재 fixture와 live_test는 모두 테스트 증거이므로 운영 매매 후보 근거로 혼합하지 않는다.
3. 검증된 코드/거래소 대응표가 있는 경우에만 연결한다. 이름이나 접미어 제거로 강제 조인하지 않는다. TOP5의 거래대금상위 존재 여부와 원래 거래대금 순위·요청 범위를 나란히 표시한다. 목록 밖·미수집·partial·필터 제외는 구분하며 없다고 0원·저유동성으로 확정하지 않는다.
4. 요청 시차와 서버 기준 시각을 분리한다. 현재 ka10032에는 서버 시각이 없으므로 가까운 요청 구간도 잠정 연결이다. 선택 시각 이후 완료된 관측을 과거 TOP5의 당시 증거로 사용하지 않는다. 기존 v2의 ±300초 검증을 이 TR의 시각 검증으로 자동 전용하지 않는다.
5. 단위·시세·시각·커버리지·주문 규모 대비 유동성 검증 후 후보 분류를 검토한다. 이번에는 자동 매매 후보/대장주 확정·순위 합산 점수·실제 DB 조인을 구현하지 않는다. 상세 모델은 [5-A 설계](RANKING_0186_0181_RESEARCH.md)와 [공통 정책](RADAR_ANALYSIS_POLICY.md)을 따른다.

## 후속 실제 API 검증 — 별도 승인 후에만 실행

이번 개발에서는 아래 live 명령과 OAuth를 실행하지 않았다. 먼저 테스트 환경/거래소/조건·허용 페이지·호출 예산을 승인받고 기존 수집 작업과 API 유량이 겹치지 않는 검증 시간을 정한다. 기존 수집기를 종료하거나 스케줄을 수정하는 절차는 아니다.

```powershell
# 무통신 계획 검토
python -B -m kiwoom_collector.turnover_rank plan --environment mock --market 000 --managed 0 --venue 1 --max-pages 1

# 승인 후 실행할 mock 실제 요청: 기존 .env의 환경도 mock이어야 함
python -B -m kiwoom_collector.turnover_rank live-test --environment mock --market 000 --managed 0 --venue 1 --max-pages 1 --db data/ka10032-approved-mock-new.db --network-approved

# 위 실행의 전용 테스트 DB 확인
python -B -m kiwoom_collector.turnover_rank inspect --db data/ka10032-approved-mock-new.db
```

`--network-approved`가 없으면 인증정보를 로드하거나 DB를 생성하지 않고 거부한다. 기존 출력 경로도 인증 전에 거부한다. 이 옵션은 사용자 승인 기록을 자동 확인하는 기능이 아니며 실제 승인 없이 실행해서는 안 된다. mock/real 자동 전환은 없고 자격 증명 환경과 요청 환경이 다르면 거부한다. real 테스트는 별도 승인된 real 키/환경에서 같은 명령의 명시적 real 인자·새 DB로만 수행하며 이번 작업에서 설정을 바꾸지 않는다.

첫 실제 검증은 인증 최대 1시도와 쿼리 최대 1페이지다. 추가 페이지는 별도 예산 승인 후 실행한다. 토큰이 페이지 도중 만료되거나 인증 오류가 나면 멈추며 자동 재인증/재조회하지 않는다. 인증 실패의 시각을 순위 요청 시각으로 기록하지 않는다. 기존 클라이언트의 TLS·리다이렉트 차단·시작 간격 기본 1.2초/최소 1.1초를 재사용한다. 이는 이 클라이언트 안의 간격이며 다른 프로세스의 계좌별 합산 유량은 조정하지 못한다. 기존 DB별 잠금이 서로 다른 테스트 DB의 API 호출까지 보호한다고 주장하지 않는다.

[공식 서비스 안내](https://openapi2.kiwoom.com/intro?dummyVal=0)의 국내 조회 계좌별/토큰별 초당 5회·모의 동일 TR 초당 1회와 실제 작업의 합산 예산을 확인해야 한다. 현재 환경에서 NXT/통합 지원, 페이지당 결과 수, 누적 기준·리셋과 거래일, HTS 같은 조건의 순위/금액/등락률, ka10095 시세와의 시간 차이를 검증한다. 가격×거래량을 거래대금의 대조값으로 만들지 않는다. 실측 증거 등록/검증 상태 승격 기능은 후속 범위이며 DB 값을 수동 verified로 바꾸는 절차를 제공하지 않는다.

## 오프라인 회귀

```powershell
python -B -m unittest discover -s tests -p test_turnover_rank.py -v
python -B -m unittest discover -s tests -v
git diff --check
```

공식 계약 대조·정규화·거부 경로·페이지·중복·오류·DB FK/무결성·실제 CLI를 가상 데이터로 검증한다. live 전송 클래스의 HTTP 계약도 requests.Session을 대체한 가짜 응답으로만 검사한다. 기존 전체 테스트·설정 파일 보존·운영 DB 파일 무변경 증거는 로컬 검증 산출물에 남긴다. 생성 DB·로그·임시 검증 파일은 커밋 대상이 아니다.
