"""Manual C14 mini chart and ADR20 analysis from the configured lightweight DB."""
import argparse
from contextlib import closing
from datetime import datetime
import json
import os
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
os.environ.setdefault('MPLCONFIGDIR',str(ROOT/'.venv/mplconfig'))
sys.path.insert(0,str(ROOT))
from kiwoom_collector import krx_c14, krx_adr
from kiwoom_collector.client import KST


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--code',default='005930')
    args=parser.parse_args()
    settings=json.loads((ROOT/'config/analysis-paths.json').read_text(encoding='utf8'))
    source=(ROOT/settings['analysis_daily_db']).resolve(strict=True)
    checkpoint=(ROOT/settings['collection_checkpoint']).resolve(strict=True)
    if not source.is_relative_to(ROOT/'diagnostics'):
        raise ValueError('development_analysis_source_required')
    now=datetime.now(KST)
    tag='lightweight-'+now.strftime('%Y%m%d-%H%M%S-%f')
    with closing(krx_adr.readonly(source)) as db:
        row=db.execute('SELECT name FROM daily_latest WHERE stock_code=? ORDER BY day DESC LIMIT 1',(args.code,)).fetchone()
        if row is None:
            raise ValueError('saved_stock_data_missing')
    chart=krx_c14.ROOT/tag
    adr=krx_adr.ROOT/tag
    bundle,_,_=krx_c14.export_charts([args.code],{args.code:row['name']},chart,as_of=now.isoformat(),source=source)
    payload=krx_adr.build(source,checkpoint,adr,incremental=True,lightweight=True)
    result=dict(source=str(source),chart=str(chart/args.code/'mini.png'),
        chart_result=bundle['items'],adr_html=str(adr/'adr20.html'),
        latest_adr=payload['report']['latest']['COMBINED']['common'],api_calls=0)
    (source.parent/'analysis-latest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':
    main()
