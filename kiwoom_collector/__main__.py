"""Read-only authentication, snapshots, scheduled collection and local history."""
from datetime import datetime
from contextlib import ExitStack
import argparse
import json
import logging
import sys
import sqlite3
from pathlib import Path

from .collector import collect, test_now
from .storage import Store, DEFAULT_DB
from .sectors import sector_command
from .summary import sector_summary
from .schedule import collect_schedule, load_holidays, SLOTS
from .timeline import sector_timeline
from .dashboard import export_dashboard
from .radar import export_radar
from .conditions import ConditionClient, fetch_conditions, condition_report
from .external_tags import external_command, is_external_csv
from .workbench import export_workbench
from .quotes import QuoteClient, BatchQuoteClient, quote_test
from .top20_quotes import dry_run as top20_dry_run, execute as top20_execute
from .api_resource import api_resource
from .integrated_test import integrated_test, fresh_database
import math

from .client import KST, KiwoomClient, KiwoomError, DEFAULT_REQUEST_SPACING
from .config import load_credentials


def validate_cli(parser, args):
    """One option policy for every command, before any file/DB/network access."""
    arities = {
        'sector add': 1, 'sector tag': 2, 'sector untag': 2, 'sector show': 1,
        'sector list': 0, 'sector untagged': 0, 'sector import': 1,
        'sector export-untagged': 0, 'sector summary': 0, 'sector timeline': 0,
        'sector radar': 0, 'sector dashboard': 0, 'sector export-priority': 0, 'sector provenance': 1,
        'sector workbench': 0, 'sector import-report': 1, 'sector alias add': 2, 'sector alias list': 0,
        'condition list': 0, 'condition fetch': 0, 'condition report': 0,
    }
    key = args.command
    values = args.sector_args
    if args.command in {'sector', 'condition'}:
        prefix = 2 if args.command == 'sector' and values[:1] == ['alias'] else 1
        key = ' '.join([args.command, *values[:prefix]])
        if key not in arities or len(values[prefix:]) != arities[key]:
            parser.error('하위 명령/인자 수를 확인하세요. sector: add/tag/untag/show/list/untagged/import/'
                         'export-untagged/export-priority/provenance/import-report/workbench/alias add/alias list/'
                         'summary/timeline/dashboard/radar; condition: list/fetch/report')
    elif values:
        parser.error('추가 위치 인자는 sector/condition 하위 명령에서만 사용합니다.')
    policy = {
        'with_top20_quotes': {'collect'},
        'environment': {'quote-integrated-test', 'sector radar', 'sector timeline', 'sector dashboard', 'sector export-priority',
                        'sector import', 'sector workbench', 'condition report', 'quote-test', 'quote-batch-test', 'quote-top20-test'},
        'collection_id': {'quote-test', 'quote-batch-test', 'quote-top20-test'}, 'limit': {'quote-test', 'quote-batch-test'},
        'request_spacing': {'quote-top20-test', 'quote-integrated-test'}, 'recollect': {'quote-top20-test'},
        'output': {'sector radar', 'sector export-untagged', 'sector export-priority', 'sector dashboard', 'sector workbench', 'sector import-report'},
        'date': {'sector radar', 'sector timeline', 'sector dashboard'},
        'source': {'sector tag', 'sector untag', 'sector import', 'sector alias add'},
        'dry_run': {'sector import', 'quote-top20-test'},
        'observed_at': {'sector import'}, 'terms_url': {'sector import'},
        'terms_note': {'sector import'}, 'terms_checked_at': {'sector import'},
        'interval': {'collect'}, 'schedule': {'collect'}, 'test_now': {'collect'},
        'holidays': {'collect'}, 'qry_tp': {'quote-integrated-test', 'once', 'collect', 'sector timeline', 'sector dashboard'},
    }
    for field, allowed in policy.items():
        value = getattr(args, field)
        if value is not None and value is not False and key not in allowed:
            parser.error('--' + field.replace('_', '-') + ' 지원 명령: ' + ', '.join(sorted(allowed)))
    if key == 'quote-integrated-test' and (args.environment != 'mock' or args.qry_tp not in (None, '1')):
        parser.error('quote-integrated-test는 --environment mock과 --qry-tp 1만 허용합니다.')
    if key in {'sector radar', 'sector export-untagged', 'sector export-priority', 'sector dashboard', 'sector workbench'} and args.output is None:
        parser.error(key + '에는 --output이 필요합니다.')
    if args.holidays is not None and not args.schedule:
        parser.error('--holidays는 collect --schedule과 함께 사용하세요.')
    if args.with_top20_quotes and not args.schedule:
        parser.error('--with-top20-quotes는 collect --schedule과 함께 사용하세요.')
    if args.schedule and (args.test_now or args.interval is not None):
        parser.error('--schedule은 --test-now/--interval과 함께 사용할 수 없습니다.')
    if args.interval is not None and args.interval < 60:
        parser.error('--interval은 60초 이상이어야 합니다.')
    args.interval = 300 if args.interval is None else args.interval
    args.qry_tp = args.qry_tp or '1'
    if key in ('quote-test', 'quote-batch-test', 'quote-top20-test') and not args.collection_id:
        parser.error('시세 테스트에는 --collection-id가 필요합니다.')
    if key == 'quote-batch-test' and args.limit not in (None, 2):
        parser.error('quote-batch-test는 --limit 2만 지원합니다.')
    if args.request_spacing is not None and (not math.isfinite(args.request_spacing) or args.request_spacing < 1.1):
        parser.error('--request-spacing은 유한한 1.1초 이상이어야 합니다.')
    return key


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="키움 조회순위 수집 및 SQLite 이력 조회",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="sector: add, tag, untag, show, list, untagged, import, export-untagged,\n"
               "        export-priority, workbench, provenance, import-report,\n"
               "        alias add, alias list, summary, timeline, dashboard, radar\n"
               "condition: list, fetch, report")
    parser.add_argument("command", choices=("check-config", "auth", "once", "collect", "status", "latest", "sector", "condition", "quote-test", "quote-batch-test", "quote-top20-test", "quote-integrated-test"), nargs="?", default="check-config")
    parser.add_argument('--request-spacing', type=float, default=None, help='TOP20/통합 테스트 요청 시작 간격(초), 기본 1.2, 최소 1.1')
    parser.add_argument('--recollect', choices=('never','failed','all'), default=None, help='quote-top20-test: 기존 시세 재수집 정책, 기본 never; 과거 행 보존')
    parser.add_argument('--collection-id', help='quote-test: 기존 성공 ka00198 회차 UUID')
    parser.add_argument('--limit', type=int, choices=(1, 2), default=None, help='quote-test: 순위 상위 1~2종목, 기본 1')
    parser.add_argument("--qry-tp", choices=("1", "2", "3", "4", "5"), default=None, help="1:1분, 2:10분, 3:1시간, 4:당일 누적, 5:30초")
    parser.add_argument("--interval", type=int, default=None, metavar="SECONDS", help="수집 간격(초), 기본 300, 최소 60")
    parser.add_argument("--db", type=Path, default=None, help="SQLite 파일 경로 (통합 테스트는 신규 경로 필수)")
    parser.add_argument("--test-now", action="store_true", help="collect 전용: 시간 제한 없이 조회 1회 저장 후 종료")
    parser.add_argument("sector_args", nargs="*", help="sector 또는 condition 하위 명령 및 인자")
    parser.add_argument("--source", help="섹터 분류 출처 (기본 수동, CSV 가져오기는 CSV)")
    parser.add_argument("--dry-run", action="store_true", help="sector import: 검증만, 태그 저장 안 함")
    parser.add_argument("--output", type=Path, help="sector export-untagged/export-priority/workbench/dashboard/import-report: 새 CSV/HTML/JSON 경로")
    parser.add_argument('--schedule', action='store_true', help='collect: KST 하루 10회 고정 시간표')
    parser.add_argument('--with-top20-quotes', action='store_true', help='collect --schedule: 신규 성공 회차에만 ka10095 후속 시세 수집 (기본 꺼짐)')
    parser.add_argument('--holidays', type=Path, help='고정 일정용 KRX 휴장일 목록 파일')
    parser.add_argument('--date', help='sector timeline/dashboard/radar: YYYY-MM-DD, 기본 KST 오늘')
    parser.add_argument('--environment', choices=('mock', 'real'), default=None, help='분석 환경 또는 quote-test 요청 환경, 기본 mock; API 서버는 KIWOOM_ENV와 일치해야 함')
    parser.add_argument('--observed-at', help='외부 테마 확인일 YYYY-MM-DD')
    parser.add_argument('--terms-url', help='사용자가 확인한 출처 이용조건 URL (접속 안 함)')
    parser.add_argument('--terms-note', help='이용조건 확인 메모')
    parser.add_argument('--terms-checked-at', help='이용조건 확인일 YYYY-MM-DD')
    # Without subparsers, intermixing supports options before/between/after positionals.
    args = parser.parse_intermixed_args(argv)
    if args.command == 'quote-integrated-test' and args.db is None:
        parser.error('quote-integrated-test에는 --db가 필요합니다.')
    args.db = args.db if args.db is not None else DEFAULT_DB
    command_key = validate_cli(parser, args)
    external_options = any(v is not None for v in (args.observed_at, args.terms_url, args.terms_note, args.terms_checked_at))
    external_action = command_key in {'sector export-priority', 'sector alias add', 'sector alias list',
                                      'sector provenance', 'sector import-report'}
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    condition_client = None
    client = None
    store = None
    resources = ExitStack()
    try:
        if args.command == 'quote-integrated-test':
            fresh_database(args.db)  # Fail before credentials, Store, or HTTP client.
            report, code = integrated_test(args.db, load_credentials(), environment=args.environment,
                                          spacing=args.request_spacing if args.request_spacing is not None else DEFAULT_REQUEST_SPACING)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return code
        if args.command == 'quote-top20-test':
            options = dict(environment=args.environment or 'mock', spacing=args.request_spacing if args.request_spacing is not None else DEFAULT_REQUEST_SPACING,
                           recollect=args.recollect or 'never')
            if args.dry_run:
                # No credentials, HTTP client, common Store or migration in this branch.
                report = top20_dry_run(args.db,args.collection_id,**options)
                print(json.dumps(report,ensure_ascii=False,indent=2))
                return 0
            credentials = load_credentials()
            resources.enter_context(api_resource(args.db, credentials.environment))
            if credentials.environment != options['environment']:
                raise ValueError('시세 요청 환경과 KIWOOM_ENV가 다릅니다.')
            client = BatchQuoteClient(credentials)
            report, code = top20_execute(args.db,client,args.collection_id,**options)
            print(json.dumps(report,ensure_ascii=False,indent=2))
            return code
        if args.command in ('quote-test', 'quote-batch-test'):
            credentials = load_credentials()
            resources.enter_context(api_resource(args.db, credentials.environment))
            environment = args.environment or 'mock'
            if credentials.environment != environment:
                raise ValueError('시세 환경과 KIWOOM_ENV가 다릅니다. 자동 전환하지 않습니다.')
            batch = args.command == 'quote-batch-test'
            client = BatchQuoteClient(credentials) if batch else QuoteClient(credentials)
            report, code = quote_test(args.db, client, args.collection_id,
                                      environment=environment, limit=2 if batch else args.limit or 1,
                                      api_id='ka10095' if batch else 'ka10007')
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return code
        if command_key == 'sector radar':
            print(json.dumps(export_radar(args.db, args.date or datetime.now(KST).date().isoformat(),
                                          args.environment or 'mock', args.output), ensure_ascii=False, indent=2))
            return 0
        if command_key == 'sector workbench':
            print(json.dumps(export_workbench(args.db, args.environment or 'mock', args.output), ensure_ascii=False, indent=2))
            return 0
        external_import = args.command == 'sector' and args.sector_args[:1] == ['import'] and (
            external_options or args.environment is not None or is_external_csv(args.sector_args[1]))
        if external_action or external_import:
            report, code = external_command(args.db,args.sector_args,source=args.source,
                observed_at=args.observed_at,terms_link=args.terms_url,terms_note=args.terms_note,
                terms_checked_at=args.terms_checked_at,dry_run=args.dry_run,
                environment=args.environment or 'mock',output=args.output)
            print(json.dumps(report,ensure_ascii=False,indent=2))
            return code
        if args.command == 'condition':
            if args.sector_args not in (['list'], ['fetch'], ['report']):
                raise ValueError('condition list/fetch/report 중 하나를 지정하세요.')
            if args.source is not None or args.dry_run or args.date is not None:
                raise ValueError('condition은 --source/--dry-run/--date를 사용하지 않습니다.')
            if args.sector_args == ['report']:
                print(json.dumps(condition_report(args.db, args.environment or 'mock'), ensure_ascii=False, indent=2))
                return 0
            credentials = load_credentials()
            condition_client = ConditionClient(credentials)
            condition_client.open()
            if args.sector_args == ['list']:
                print(json.dumps({'environment': credentials.environment, 'conditions': condition_client.scrub(condition_client.definitions())}, ensure_ascii=False, indent=2))
                return 0
            batch_id, failed = fetch_conditions(args.db, condition_client)
            print(json.dumps(condition_report(args.db, credentials.environment, batch_id), ensure_ascii=False, indent=2))
            return 1 if failed else 0
        if args.command == 'sector' and args.sector_args[:1] == ['dashboard']:
            if args.sector_args != ['dashboard'] or args.source is not None or args.dry_run:
                raise ValueError('sector dashboard 인자를 확인하세요.')
            output = export_dashboard(args.db, args.date or datetime.now(KST).date().isoformat(), args.environment or 'mock', args.qry_tp, args.output)
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return 0
        if args.command == 'sector' and args.sector_args[:1] == ['timeline']:
            if args.sector_args != ['timeline'] or args.source is not None or args.dry_run:
                raise ValueError('sector timeline 인자를 확인하세요. --date/--environment/--qry-tp/--db만 사용합니다.')
            report = sector_timeline(args.db, args.date or datetime.now(KST).date().isoformat(), args.environment or 'mock', args.qry_tp)
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 0
        # Route before Store construction: summary must never initialize/migrate a DB.
        if args.command == "sector" and args.sector_args == ["summary"]:
            if args.source is not None or args.dry_run:
                raise ValueError('sector summary는 --source/--dry-run을 사용하지 않습니다.')
            print(json.dumps(sector_summary(args.db), ensure_ascii=False, indent=2))
            return 0
        if args.command == "sector" and args.sector_args[:1] == ["summary"]:
            raise ValueError('sector summary에는 추가 위치 인자를 지정할 수 없습니다.')
        if args.command in {"status", "latest", "collect", "sector"}:
            store = Store(args.db)
        if args.command == "sector":
            output, exit_code = sector_command(store, args.sector_args, args.source, args.dry_run, args.output)
            print(json.dumps(output, ensure_ascii=False, indent=2))
            return exit_code
        if args.command == "status":
            print(f"DB: {store.path}")
            state = store.status()
            print(f"최근 수집 시도(KST): {state['last_attempt'] or '없음'}")
            print(f"최근 데이터 수집 성공(KST): {state['last_success'] or '없음'}")
            print(f"누적 수집 시도: {state['attempts']} / 누적 기록: {state['records']}")
            for key, label in (("successes", "성공"), ("empty", "빈 결과"), ("errors", "오류"),
                               ("interrupted", "중단"), ("running", "진행/미완료"), ("tests", "테스트 회차(전체에 포함)")):
                print(f"{label}: {state[key] or 0}")
            failure = store.last_failure()
            if failure:
                print(f"최근 오류: {failure['collected_at']} / id={failure['id']} / stage={failure['error_stage'] or 'legacy_unknown'}")
                print(f"원인: {failure['error']}")
                if failure['diagnostic_json']:
                    print(f"안전 진단: {failure['diagnostic_json']}")
            return 0
        if args.command == "latest":
            run, rows = store.latest()
            if run is None:
                print("완료된 수집 이력이 없습니다.")
            else:
                print(f"예정 시각: {run.get('scheduled_at') or '간격/단일 수집'}")
                print(f"test_mode={bool(run['test_mode'])}")
                print(f"최근 수집: {run['collected_at']} / {run['environment']} / {run['status']}")
                for row in rows:
                    print(json.dumps(row, ensure_ascii=False))
            return 0
        credentials = load_credentials()
        print(f"API 환경: {credentials.environment} ({credentials.base_url})")
        if args.command == "check-config":
            print("APP KEY / SECRET KEY 설정 확인 완료. API 호출은 수행하지 않았습니다.")
            return 0
        resources.enter_context(api_resource(args.db, credentials.environment))
        client = BatchQuoteClient(credentials) if args.with_top20_quotes else KiwoomClient(credentials)
        if args.command == "collect":
            if args.schedule:
                holidays = load_holidays(args.holidays)
                print(f"고정 일정 KST 하루 10회: {', '.join(SLOTS)}; 휴장일 파일: {'사용' if args.holidays else '없음 (주말만 자동 제외)'}")
                if args.with_top20_quotes:
                    collect_schedule(client, store, credentials.environment, args.qry_tp, holidays, with_quotes=True)
                else:
                    collect_schedule(client, store, credentials.environment, args.qry_tp, holidays)
                return 0
            if args.test_now:
                print(f"DB: {store.path}; test_mode=true, 시간 제한 무시, 조회 1회 후 종료")
                return test_now(client, store, credentials.environment, args.qry_tp)
            print(f"DB: {store.path}; 평일 KST 09:00~15:30 (15:30 제외), {args.interval}초 간격")
            collect(client, store, credentials.environment, args.qry_tp, args.interval)
            return 0
        if args.command == "auth":
            token = client.authenticate()
            print(f"인증 성공. 만료 시각(한국): {token.expires_at.isoformat()} (토큰 값 출력 안 함)")
            return 0
        snapshot = client.fetch_once(args.qry_tp)
        print(f"수집 시각(한국): {snapshot.collected_at.isoformat()}")
        print(f"API 순위 1~20 해당 결과: {len(snapshot.records)}건")
        if not snapshot.records:
            print("조회 결과 없음. 정상 데이터 수집 여부는 확인되지 않았습니다.")
        elif len(snapshot.records) < 20:
            print("주의: 20건 미만입니다. 없는 종목/순위를 생성하지 않았습니다.")
        fields = ("bigd_rank", "stk_cd", "stk_nm", "base_comp_chgr", "prev_base_chgr", "dt", "tm")
        for row in snapshot.records:
            # JSON escaping also prevents server text from injecting terminal control sequences.
            print(json.dumps({key: row[key] for key in fields if key in row}, ensure_ascii=False))
        return 0
    except (ValueError, KiwoomError) as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
    except sqlite3.Error:
        print("DB 오류: 파일 권한·디스크 공간·다른 프로세스 잠금을 확인하세요. 안전을 위해 중단합니다.", file=sys.stderr)
        return 1
    except OSError:
        print("파일 접근 오류: DB/CSV 경로와 폴더 권한을 확인하세요.", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("사용자가 중단했습니다.", file=sys.stderr)
        return 130
    finally:
        if condition_client is not None:
            condition_client.close()
        if client is not None:
            client.close()
        if store is not None:
            store.close()
        resources.close()


if __name__ == "__main__":
    raise SystemExit(main())
