from pathlib import Path
from contextlib import closing
import sqlite3
import tempfile

from kiwoom_collector.external_theme_master import inspect,project,identify,calendar_day


def test_missing_never_creates_database():
    with tempfile.TemporaryDirectory() as d:
        path=Path(d)/'missing.sqlite'
        assert inspect(path)['state']=='missing'
        assert not path.exists()


def test_reviewed_projection_deduplicates_and_preserves_raw():
    with tempfile.TemporaryDirectory() as d:
        path=Path(d)/'synthetic-test.sqlite'
        with closing(sqlite3.connect(path)) as db, db:
            db.execute('CREATE TABLE observations(day TEXT,theme TEXT,name TEXT,code TEXT,source TEXT)')
            db.executemany('INSERT INTO observations VALUES(?,?,?,?,?)',
                [('2026-11-02','일부 로봇','한화에어','012450',source)
                 for source in ('synthetic_fixture_A','synthetic_fixture_B')])
        before=path.read_bytes()
        assert inspect(path)['quick_check']==['ok']
        result=project(path,dict(table='observations',columns=dict(trade_date='day',raw_theme='theme',
                    raw_stock_name='name',raw_stock_code='code',source='source')))
        assert result['unique_count']==1 and result['duplicate_count']==1
        assert result['records'][0]['raw_stock_name']=='한화에어'
        assert len(result['records'][0]['raw_records'])==2
        assert path.read_bytes()==before


def test_identity_date_alias_and_preferred_conflicts():
    master=[dict(stock_code='000880',stock_name='한화',verified=True,valid_from='2020-01-01',source='fixture'),
            dict(stock_code='000885',stock_name='한화우',verified=True,valid_from='2020-01-01',source='fixture')]
    record=dict(trade_date='2026-11-02',raw_stock_name='한화우',raw_stock_code='000880')
    assert identify(record,master,as_of='2026-11-03T09:50:02+09:00')['state']=='unconfirmed'
    record['raw_stock_code']='000885'
    assert identify(record,master,as_of='2026-11-03T09:50:02+09:00')['stock_code']=='000885'
    record['trade_date']='2026-11-04'
    assert identify(record,master,as_of='2026-11-03T09:50:02+09:00')['reason']=='after_cutoff'
    assert calendar_day('2026-11-03')['external_trader_close']['absence_means_no_theme'] is False
    assert len(calendar_day('2026-11-03')['arvis_selection']['missing_slots'])==4


def test_alias_needs_dated_review_and_original_is_preserved():
    record=dict(trade_date='2026-11-02',raw_stock_name='약칭',raw_stock_code=None)
    master=[dict(stock_code='012450',stock_name='정식명',verified=True,valid_from='2020-01-01',source='fixture')]
    alias=dict(raw_name='약칭',canonical_name='정식명',stock_code='012450',verified=True,
               evidence_url='https://example.com',valid_from='2026-01-01')
    result=identify(record,master,as_of='2026-11-03T09:50:02+09:00',aliases=[alias])
    assert result['state']=='identified' and result['raw_name']=='약칭'
    alias['verified']=False
    assert identify(record,master,as_of='2026-11-03T09:50:02+09:00',aliases=[alias])['state']=='unconfirmed'
