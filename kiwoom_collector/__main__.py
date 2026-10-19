"""Explicit opt-in authentication / one snapshot; no scheduling or order operations."""
import argparse
import json
import logging
import sys

from .client import KiwoomClient, KiwoomError
from .config import load_credentials


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="키움 인증 및 조회순위 단일 조회")
    parser.add_argument("command", choices=("check-config", "auth", "once"), nargs="?", default="check-config")
    parser.add_argument("--qry-tp", choices=("1", "2", "3", "4", "5"), default="1", help="1:1분, 2:10분, 3:1시간, 4:당일 누적, 5:30초")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
    client = None
    try:
        credentials = load_credentials()
        print(f"API 환경: {credentials.environment} ({credentials.base_url})")
        if args.command == "check-config":
            print("APP KEY / SECRET KEY 설정 확인 완료. API 호출은 수행하지 않았습니다.")
            return 0
        client = KiwoomClient(credentials)
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
    except KeyboardInterrupt:
        print("사용자가 중단했습니다.", file=sys.stderr)
        return 130
    finally:
        if client is not None:
            client.close()


if __name__ == "__main__":
    raise SystemExit(main())
