"""Offline saved daily diagnostics -> per-stock C14-B JSON. Never collect data."""
import argparse
import json
from pathlib import Path

from .saved import analyze_saved, read_json


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--diagnostic-json',type=Path,action='append',required=True)
    parser.add_argument('--as-of',required=True)
    parser.add_argument('--reviews-json',type=Path)
    parser.add_argument('--c12-json',type=Path)
    parser.add_argument('--output-dir',type=Path,required=True,help='New directory only')
    args=parser.parse_args(argv)
    try:
        reviews=read_json(args.reviews_json)[0] if args.reviews_json else []
        candidate=read_json(args.c12_json)[0] if args.c12_json else None
        batch=analyze_saved(args.diagnostic_json,as_of=args.as_of,reviews=reviews,candidate_document=candidate)
        args.output_dir.mkdir(parents=True,exist_ok=False)
        for item in batch['reports']:
            r=item['request']
            name=f"{r['environment']}-{r['stock_code']}-{r['api_id']}-{r['base_dt']}-{r['adjustment']}.json"
            with (args.output_dir/name).open('x',encoding='utf-8') as target:
                json.dump(item['report'],target,ensure_ascii=False,indent=2,allow_nan=False)
        with (args.output_dir/'manifest.json').open('x',encoding='utf-8') as target:
            json.dump(batch,target,ensure_ascii=False,indent=2,allow_nan=False)
    except (OSError,ValueError,TypeError,KeyError):
        parser.error('Saved daily input/review invalid or output directory already exists; no collection attempted')
    print(json.dumps(dict(output_dir=str(args.output_dir),**batch['summary']),ensure_ascii=False))
    return 0


if __name__=='__main__':raise SystemExit(main())
