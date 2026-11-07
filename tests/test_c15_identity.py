"""C15-9 synthetic identity/type/time tests. No external requests or source edits."""
import json
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector import c15_identity as identity, c15_leadership as engine
from test_c15_leadership import stock, record, report, POLICY

AT='2026-11-05T09:40:45+09:00'


def official(code='000001', name='정식종목', abbreviation='약칭', kind='common_stock', **extra):
    return dict(stock_code=code, official_name=name, abbreviated_name=abbreviation,
                product_type=kind, market='KOSPI', basis_date='2026-11-03',
                available_at='2026-11-04T14:59:13+09:00', name_basis='synthetic_official_master', **extra)


def analyze(stocks, records, officials):
    r=report(stocks); r['as_of']=AT
    return engine.analyze(r,records,{},day='2026-11-05',policy=POLICY,official_records=officials)


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('socket.socket.connect',side_effect=AssertionError('network forbidden')))

    def test_code_namespace_prefix_digits_and_market(self):
        records=[official(),official('0013Y0')]
        self.assertEqual(identity.exact_code('A000001',records),'000001')
        self.assertEqual(identity.exact_code('A0013Y0',records),'0013Y0')
        self.assertEqual(identity.exact_code('A12345'),'A12345')
        self.assertEqual(identity.exact_code('0013Y0'),'0013Y0')
        self.assertIsNone(identity.exact_code('000001',records,'KOSDAQ'))
        self.assertEqual(identity.exact_code('000001',records,'KOSPI'),'000001')
        for value in ('5930',5930,'A999999','000001_NX','000001_AL','kr0001',' 000001'):
            self.assertIsNone(identity.exact_code(value,records))

    def test_official_full_name_can_bridge_observed_abbreviation(self):
        s=stock('000001','1');s['name']='약칭'
        old=engine.map_themes([s],[record('정식종목','테마')],'2026-11-05')[0]
        new=engine.map_themes([s],[record('정식종목','테마')],'2026-11-05',official_records=[official()],as_of=AT)[0]
        self.assertEqual(old,[])
        self.assertEqual(new[0]['stock_code'],'000001')
        self.assertIn('official_master_name',new[0]['source'])

    def test_priority_code_official_then_existing_name(self):
        stocks=[stock('000001','1'),stock('000002','2'),stock('000003','3')]
        stocks[1]['name']='약칭'
        records=[record('별명','코드','A000001'),record('정식종목','공식'),record(stocks[2]['name'],'보조')]
        v=analyze(stocks,records,[official(name='다른공식명'),official('000002')])
        c=v['mapping']['coverage']
        self.assertEqual((c['code_mapped'],c['official_name_mapped'],c['name_only_mapped']),(1,1,1))

    def test_ambiguous_official_name_blocks_name_fallback(self):
        s=stock('000001','1');s['name']='동일명'
        rows=[official(name='동일명'),official('000002',name='다른명',abbreviation='동일명')]
        self.assertEqual(analyze([s],[record('동일명','테마')],rows)['mapping']['coverage']['theme_linked'],0)

    def test_official_observed_name_conflict_does_not_promote(self):
        s=stock('000001','1');s['name']='불일치'
        self.assertEqual(analyze([s],[record('정식종목','테마')],[official()])['mapping']['coverage']['theme_linked'],0)

    def test_future_official_and_same_day_undated_theme_are_not_used(self):
        s=stock('000001','1');s['name']='약칭'
        future=official();future['available_at']='2026-11-05T10:00:00+09:00'
        v=analyze([s],[record('정식종목','테마')],[future])
        self.assertEqual(v['mapping']['coverage']['theme_linked'],0)
        self.assertEqual(v['stocks'][0]['product_status'],'UNKNOWN')
        v=analyze([s],[record('약칭','당일',day='2026-11-05')],[official()])
        self.assertEqual(v['mapping']['coverage']['theme_linked'],0)

    def test_all_explicit_excluded_types_preferred_and_unknown(self):
        kinds=['ETF','ETN','REIT','SPAC','preferred_stock','UNKNOWN','common_stock']
        stocks=[stock(f'{i:06}','2') for i in range(1,8)]
        rows=[official(s['stock_code'],name=s['name'],abbreviation=s['name'],kind=k) for s,k in zip(stocks,kinds)]
        v=analyze(stocks,[record(s['name'],'동반') for s in stocks],rows)
        self.assertEqual(len(v['excluded_stocks']),4)
        self.assertEqual(len(v['preferred_stocks']),1)
        self.assertEqual(len(v['unknown_type_stocks']),1)
        self.assertEqual(v['analysis']['sectors'][0]['rising_count'],2)
        self.assertEqual(v['mapping']['coverage']['total'],7)
        self.assertEqual(v['stocks'][5]['eligibility_status'],'pending')

    def test_official_multitheme_and_duplicate_tags_score_not_inflated(self):
        stocks=[stock('000001','2'),stock('000002','3')]
        rows=[official(s['stock_code'],name=s['name'],abbreviation=s['name']) for s in stocks]
        links=[record(s['name'],t) for s in stocks for t in ('A','B')]
        a=analyze(stocks,links,rows);b=analyze(stocks,links+links,rows)
        self.assertEqual(a['analysis'],b['analysis'])
        self.assertEqual(a['analysis']['sectors'][0]['rising_count'],2)
        self.assertEqual(a['analysis']['sectors'][0]['components']['cross'],'1.0')

    def test_partial_scope_missing_turnover_not_negative_evidence(self):
        stocks=[stock('000001','1'),stock('000002','2')]
        r=report(stocks);r['as_of']=AT;r['snapshot_selection']['selected']['0186']['snapshot']['rows']=[]
        v=engine.analyze(r,[record(s['name'],'테마') for s in stocks],{},day='2026-11-05',policy=POLICY,official_records=[])
        self.assertEqual(v['analysis']['sectors'],[])
        self.assertTrue(all(s.get('turnover_rank') is None for s in v['stocks']))


class OfficialLoaderTests(unittest.TestCase):
    def setUp(self):
        self.tmp=self.enterContext(tempfile.TemporaryDirectory());self.daily=Path(self.tmp)/'daily.db';self.check=Path(self.tmp)/'checkpoint.db'
        with closing(sqlite3.connect(self.daily)) as db, db:
            db.execute('''CREATE TABLE daily_latest(stock_code,name,market,day,standard_code,product_type,
                master_basis_date,master_body_hash,collected_at,validation_status,source_kind)''')
            db.execute("INSERT INTO daily_latest VALUES('000001','공식','KOSPI','2026-11-03','KR7000001000','REIT','2026-11-03','old-KOSPI','2026-11-04T05:59:00+00:00','schema_and_dated_identity_checked','krx_live')")
        with closing(sqlite3.connect(self.check)) as db, db:
            db.execute('CREATE TABLE dates(day PRIMARY KEY,details_json)')
            for day,received,prefix in [('2026-11-04','2026-11-05T03:00:00+00:00','future'),('2026-11-03','2026-11-04T06:00:00+00:00','old')]:
                entries={m:dict(master=dict(basis_date=day,source_kind='krx_live',body_sha256=prefix+'-'+m,
                    service='stk_isu_base_info' if m=='KOSPI' else 'ksq_isu_base_info',
                    validation_status='response_schema_and_identity_uniqueness_checked',requested_at=received,received_at=received)) for m in ('KOSPI','KOSDAQ')}
                db.execute('INSERT INTO dates VALUES(?,?)',(day,json.dumps(dict(markets=entries))))

    def test_both_master_and_daily_receipt_cutoffs_and_hash_binding(self):
        rows,info=identity.load_official(self.daily,self.check,as_of=AT)
        self.assertEqual(info['basis_date'],'2026-11-03');self.assertEqual(len(rows),1)
        self.assertEqual(info['rejected_basis_dates'],['2026-11-04'])
        with closing(sqlite3.connect(self.daily)) as db, db: db.execute("UPDATE daily_latest SET master_body_hash='wrong'")
        self.assertEqual(identity.load_official(self.daily,self.check,as_of=AT)[0],[])

    def test_daily_collected_after_cutoff_is_rejected(self):
        with closing(sqlite3.connect(self.daily)) as db, db: db.execute("UPDATE daily_latest SET collected_at='2026-11-05T10:00:00+09:00'")
        self.assertEqual(identity.load_official(self.daily,self.check,as_of=AT)[0],[])

    def test_no_historical_master_available_is_explicit(self):
        rows,info=identity.load_official(self.daily,self.check,as_of='2026-11-02T09:40:45+09:00')
        self.assertEqual(rows,[]);self.assertEqual(info['status'],'unavailable_at_cutoff')

    def test_master_loader_is_readonly(self):
        connect=sqlite3.connect
        def checked(path,*args,**kwargs):
            self.assertIn('?mode=ro',str(path));self.assertTrue(kwargs.get('uri'))
            return connect(path,*args,**kwargs)
        with patch('sqlite3.connect',side_effect=checked): identity.load_official(self.daily,self.check,as_of=AT)


if __name__=='__main__':unittest.main()
