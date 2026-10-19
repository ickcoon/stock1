"""Read-only authentication, snapshots, scheduled collection and local history."""
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

from .client import KiwoomClient, KiwoomError
from .config import load_credentials


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="키움 조회순위 수집 및 SQLite 이력 조회")
    parser.add_argument("command", choices=("check-config", "auth", "once", "collect", "status", "latest", "sector"), nargs="?", default="check-config")
    parser.add_argument("--qry-tp", choices=("1", "2", "3", "4", "5"), default="1", help="1:1분, 2:10분, 3:1시간, 4:당일 누적, 5:30초")
    parser.add_argument("--interval", type=int, default=300, metavar="SECONDS", help="수집 간격(초), 기본 300, 최소 60")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB, help="SQLite 파일 경로")
    parser.add_argument("--test-now", action="store_true", help="collect 전용: 시간 제한 없이 조회 1회 저장 후 종료")
    parser.add_argument("sector_args", nargs="*", help="sector 하위 명령 및 인자")
    parser.add_argument("--source", help="섹터 분류 출처 (기본 수동, CSV 가져오기는 CSV)")
    parser.add_argument("--dry-run", action="store_true", help="sector import: 검증만, 태그 저장 안 함")
    parser.add_argument("--output", type=Path, help="sector export-untagged: 새 CSV 출력 경로 (덮어쓰기 금지)")
    args = parser.parse_args(argv)
    if args.output is not None and not (args.command == "sector" and args.sector_args == ["export-untagged"]):
        parser.error('--output은 sector export-untagged 전용입니다.')
    if args.command == "sector" and args.sector_args == ["export-untagged"] and args.output is None:
        parser.error('sector export-untagged에는 --output CSV경로가 필요합니다.')
    if args.command != "sector" and (args.sector_args or args.source is not None or args.dry_run):
        parser.error("섹터 인자와 --source/--dry-run은 sector 명령 전용입니다.")
    if args.test_now and args.command != "collect":
        parser.error("--test-now는 collect 명령에서만 사용할 수 있습니다.")
    if args.command == "collect" and not args.test_now and args.interval < 60:
        parser.error("--interval은 60초 이상이어야 합니다.")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    client = None
    store = None
    try:
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
        client = KiwoomClient(credentials)
        if args.command == "collect":
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
        if client is not None:
            client.close()
        if store is not None:
            store.close()


if __name__ == "__main__":
    raise SystemExit(main())
