"""AP-2 streaming quality audit and separate, reproducible ADR preparation. No network."""
from collections import defaultdict, deque
from decimal import Decimal
import argparse
import csv
import hashlib
import json
from pathlib import Path
import sqlite3

from . import krx_daily as daily


def rolling(counts):
    """Ratio of 20 session sums, never an average of daily ratios."""
    window = deque(maxlen=20)
    for day, up, down, flat in counts:
        window.append((up, down))
        a, d = (sum(x[i] for x in window) for i in (0, 1))
        yield (day, up, down, flat, len(window), a, d,
               str(Decimal(100)*a/d) if len(window)==20 and d else None)


def prepare(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if source.parent != daily.ROOT.resolve() or output.parent != source.parent or source == output:
        raise ValueError('separate_development_adr_database_required')
    # DailyStore checks schema and provenance without changing the source.
    daily.DailyStore(source)
    c = sqlite3.connect(source.as_uri()+'?mode=ro', uri=True)
    c.row_factory = sqlite3.Row
    groups = {}
    previous = {}
    days = [r[0] for r in c.execute('SELECT day FROM market_days WHERE current_snapshot IS NOT NULL GROUP BY day HAVING COUNT(*)=2 ORDER BY day')]
    prior_day = {d:days[i-1] if i else None for i,d in enumerate(days)}
    anomalies = []
    for r in c.execute('SELECT * FROM daily_latest ORDER BY day,market,official_code_raw'):
        key = r['day'], r['market']
        g = groups.setdefault(key, dict(day=r['day'],market=r['market'],rows=0,ohlcv_missing=0,
            turnover_missing=0,cap_missing=0,zero_volume=0,flat=0,up=0,down=0,missing=0,
            unknown=0,price_unformed=0,identity_missing=0,eligible_up=0,eligible_down=0,eligible_flat=0,
            cross_reference_mismatch=0,issues=0,converted_amounts=0,negative_nonnegative=0,ohlc_contradictions=0))
        g['rows'] += 1
        g['ohlcv_missing'] += any(r[k] is None for k in ('open','high','low','close','volume'))
        g['turnover_missing'] += r['turnover_raw'] is None
        g['cap_missing'] += r['market_cap_raw'] is None
        g['zero_volume'] += r['volume'] is not None and Decimal(r['volume'])==0
        g[r['direction']] += 1
        g['unknown'] += r['product_type']=='UNKNOWN'
        g['identity_missing'] += r['stock_code'] is None
        formed = all(r[k] is not None and Decimal(r[k])>0 for k in ('open','high','low','close'))
        g['price_unformed'] += not formed
        g['converted_amounts'] += r['turnover_won'] is not None or r['market_cap_won'] is not None
        g['negative_nonnegative'] += any(r[k] is not None and Decimal(r[k])<0 for k in ('open','high','low','close','volume','turnover_raw','market_cap_raw','listed_shares'))
        g['ohlc_contradictions'] += formed and not (Decimal(r['low'])<=min(Decimal(r['open']),Decimal(r['close']))<=max(Decimal(r['open']),Decimal(r['close']))<=Decimal(r['high']))
        issues = json.loads(r['issues_json'])
        g['issues'] += bool(issues)
        old = previous.get((r['market'],r['stock_code'])) if r['stock_code'] else None
        cross = bool(old and old['day']==prior_day.get(r['day']) and old['standard_code']==r['standard_code']
            and all(v is not None for v in (r['close'],r['change_price'],old['close']))
            and Decimal(r['close'])-Decimal(old['close']) != Decimal(r['change_price']))
        g['cross_reference_mismatch'] += cross
        if cross:
            anomalies.append(dict(day=r['day'],market=r['market'],stock_code=r['stock_code'],prior_day=old['day'],prior_close=old['close'],close=r['close'],change_price=r['change_price']))
        eligible = (r['day'] in prior_day and r['product_type']=='common_stock' and r['stock_code'] is not None
            and formed and r['trading_state']=='traded' and not r['is_new_listing'] and r['direction']!='missing' and not issues and not cross)
        if eligible:
            g['eligible_'+r['direction']] += 1
        if r['stock_code']:
            previous[(r['market'],r['stock_code'])] = dict(r)
    integrity = [r[0] for r in c.execute('PRAGMA integrity_check')]
    foreign = [tuple(r) for r in c.execute('PRAGMA foreign_key_check')]
    duplicates = c.execute('SELECT COUNT(*) FROM (SELECT day,market,official_code_raw FROM daily_latest GROUP BY 1,2,3 HAVING COUNT(*)>1)').fetchone()[0]
    totals = {t:c.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('bars','raw_rows','snapshots','attempts')}
    invalid = c.execute('SELECT COUNT(*) FROM raw_rows WHERE error IS NOT NULL').fetchone()[0]
    c.close()
    ordered = [groups[k] for k in sorted(groups)]
    report = dict(sessions=len(days),date_range=[days[0],days[-1]] if days else [],records=sum(g['rows'] for g in ordered),
        table_rows=totals,integrity_check=integrity,foreign_key_violations=foreign,duplicates=duplicates,
        invalid_raw_rows=invalid,market_days=ordered,previous_close_anomalies=anomalies,
        amount_units='unverified_no_conversion',calendar_status='nonempty_both_market_responses; empty_dates_not_classified_as_holidays',
        adr_policy='common_stock; formed OHLC; positive volume; verified dated identity; exclude new listing, issues and reference mismatch',
        first_day_prior_close='unavailable',adr_values='provisional_until_calendar_and_product_policy_review')
    assert integrity==['ok'] and not foreign and not duplicates and not invalid
    assert all(not g[k] for g in ordered for k in ('ohlcv_missing','turnover_missing','cap_missing','negative_nonnegative','ohlc_contradictions','converted_amounts'))
    if output.exists():
        with sqlite3.connect(output.as_uri()+'?mode=ro',uri=True) as check:
            if check.execute("SELECT value FROM adr_meta WHERE key='policy_version'").fetchone() != ('1',):
                raise ValueError('adr_output_schema_mismatch')
    with sqlite3.connect(output) as out, out:
        out.executescript('''CREATE TABLE IF NOT EXISTS adr_meta(key TEXT PRIMARY KEY,value TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS breadth_daily(day TEXT,market TEXT,universe TEXT,up INTEGER,down INTEGER,flat INTEGER,total_rows INTEGER,excluded INTEGER,PRIMARY KEY(day,market,universe));
        CREATE TABLE IF NOT EXISTS adr20(day TEXT,market TEXT,universe TEXT,window_sessions INTEGER,up_sum INTEGER,down_sum INTEGER,value TEXT,status TEXT,PRIMARY KEY(day,market,universe));
        DELETE FROM breadth_daily; DELETE FROM adr20; DELETE FROM adr_meta;''')
        for universe, prefix in (('all_reported',''),('common_reviewed','eligible_')):
            for market in ('KOSPI','KOSDAQ','COMBINED'):
                counts=[]
                for d in days:
                    gs=[groups[(d,m)] for m in ('KOSPI','KOSDAQ') if market in (m,'COMBINED')]
                    up,down,flat = [sum(g[prefix+k] for g in gs) for k in ('up','down','flat')]
                    n=sum(g['rows'] for g in gs)
                    out.execute('INSERT INTO breadth_daily VALUES(?,?,?,?,?,?,?,?)',(d,market,universe,up,down,flat,n,n-up-down-flat))
                    counts.append((d,up,down,flat))
                for d,up,down,flat,n,a,b,value in rolling(counts):
                    out.execute('INSERT INTO adr20 VALUES(?,?,?,?,?,?,?,?)',(d,market,universe,n,a,b,value,'provisional' if value is not None else 'insufficient_window_or_zero_denominator'))
        for key,value in dict(source=str(source),report=report,policy_version=1).items():
            out.execute('INSERT INTO adr_meta VALUES(?,?)',(key,json.dumps(value,ensure_ascii=False)))
        assert out.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    report_path=output.with_suffix('.quality.json')
    report_path.write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    with output.with_suffix('.daily.csv').open('w',encoding='utf-8-sig',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(ordered[0]))
        writer.writeheader();writer.writerows(ordered)
    return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--source',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();r=prepare(a.source,a.output)
    print(json.dumps({k:r[k] for k in ('sessions','date_range','records','integrity_check','duplicates')}))

if __name__=='__main__':
    main()
