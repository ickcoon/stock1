# C14-C 저장 일봉 → 독립 차트 분석

기존 `daily_diagnostic`의 `step7-b2-a-v1` JSON을 읽고, 저장된 response_projection을 기존 `daily_chart.parse_response`로 다시 파싱한다. 저장된 파생 rows·검증 플래그를 계산 근거로 신뢰하지 않는다. 이후 `from_daily_pages` → C14-B `analyze_context`를 그대로 사용한다. API·인증·운영 DB 쓰기·새 수집 경로는 없다.

```powershell
python -m kiwoom_collector.analyst --diagnostic-json saved-report.json --as-of 2026-10-28T21:30:25+09:00 --output-dir new-output
```

`--diagnostic-json`은 여러 번 지정할 수 있다. 결과는 종목·환경·API·기준일·수정 여부별 `arvis-chart-context-v1` JSON과 provenance/통계를 담은 manifest.json이다. 출력 디렉터리는 새 경로만 허용한다. 원본 SHA-256을 계산하고 분석 후 재확인한다. JSON과 생성 결과는 로컬 자료이며 Git에 넣지 않는다.

라이브 진단 파일은 기록된 환경에 맞는 기존 data_origin을 확인하고 `environment=mock/real`, `data_origin=api_response`로 분리한다. mock 서버 실응답은 합성 fixture가 아니지만 실전 서버 자료도 아니다. 합성 입력은 mode/data_origin을 모두 synthetic_fixture로 명시해야 한다. 자료 종류를 환경명만으로 추정하지 않는다.

외부 검토 파일은 `--reviews-json`으로 전달하는 JSON 배열이다. 각 항목의 계약:

- `scope`: api_id, stock_code, environment, base_dt, adjustment, data_origin.
- `page_fingerprints`: 검토 대상 파서 페이지 fingerprint 전체. 요청·수신 시각·원문에 결합된다.
- `context_id`, `stock_name`, `calendar`, `price_basis`, 선택적인 `price`/`selection`.
- `evidence`: 기존 `ClosingEvidence`의 필드 사전 배열. page/row fingerprint, episode_id, market_at, verified_at, source, session_basis, 단위와 status를 명시한다.

검토자는 기존 `diagnostic_pages(document)`로 파싱 결과를 읽을 수 있다. 이 기능은 증거를 만들거나 검증 완료로 승격하지 않는다. 알려지지 않은 페이지·행·환경·자료 종류의 검토는 거부하며, 미래/미완료/미검증 근거는 기존 어댑터에서 격리한다. 검토 파일이 없으면 캘린더·종가 확정성·기업행위 비교·선정 근거를 생성하지 않고 지표를 unavailable/null로 출력한다. 봉 개수가 충분하다는 사실은 근거를 대신하지 않는다.

`--c12-json`은 기존 C12 `candidate_filter`가 포함된 저장 AM 입력을 받는다. 기존 normalize_code를 재사용하고 결정 사전을 종목코드로 결합한다. 환경·시장·fixture/readonly_adapter 출처와 cutoff를 검사한다. 중복 정규화 코드는 거부하고 미매칭은 unmatched, 제외/보류는 그대로 보존한다. C12 원래 data_origin은 별도로 보존하며 unverified를 api_response로 승격하지 않는다. 후보·순위·주문은 변경하지 않는다.

이번 로컬 실자료 확인(2026-11-01 KST): mock 서버 진단의 005930/000660 각각 600행, 2024-05-08~2026-10-27. 운영 수집 DB 두 개와 마스터 DB에는 일봉 테이블이 없고 저장 단위는 JSON 진단 파일이다. 확정 종가·검증 캘린더·가격 기준 검토 자료는 확인되지 않았다. 따라서 API 실응답 2종목을 파싱했지만 검증된 지표 산출 가능 종목은 0개다. 실제 결과와 합성 검토 fixture의 계산 테스트를 구분한다. 실제 JSON·로그·DB는 이 문서에 포함하지 않는다.
