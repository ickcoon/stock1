"""Offline selection and C12 regression contracts; no real transports."""
from copy import deepcopy
from pathlib import Path
import sqlite3
import tempfile
import unittest

from kiwoom_collector import c12_latest as c


def stamp(clock):return '2026-11-03T'+clock+'+09:00'


def snapshot(api='0198', clock='09:50:00', end='09:50:01', identifier='r', **updates):
    rows=([dict(rank=i,stk_cd=f'{i:06}',stk_nm='Stock '+str(i)) for i in range(1,21)] if api=='0198'
          else [dict(stock_code='000001',turnover_rank=1,validation_status='syntax_valid',raw_json='{"stk_cd":"000001","trde_prica":"123","flu_rt":"2","cur_prc":"100"}')])
    s=dict(api=api,id=identifier,environment='real',status='success',test_mode=0,
        api_id='ka00198' if api=='0198' else 'ka10032',qry_tp='1',source_kind='live_test',
        profile=dict(mrkt_tp='000',mang_stk_incls='0',stex_tp='1'),
        collection_started_at=stamp(clock),request_started_at=stamp(clock),response_received_at=stamp(end),
        db_committed_by=stamp(end),rows=rows,market_asof=None)
    s.update(updates);s['snapshot_sha256']=c.digest(s);return s


class LatestTests(unittest.TestCase):
    def select(self,rows,at='09:50:02',**kwargs):
        return c.select(rows,as_of=stamp(at),environment='real',**kwargs)['selected']

    def test_independent_latest(self):
        result=self.select([snapshot(),snapshot('0186','09:40:00','09:40:02')])
        self.assertEqual([v['status'] for v in result.values()],['fresh','fresh'])
        self.assertEqual(result['0186']['snapshot']['collection_started_at'],stamp('09:40:00'))

    def test_unfinished_and_future_fallback(self):
        old=snapshot(clock='09:45:00',end='09:45:01',identifier='old')
        for bad in [snapshot(db_committed_by=None),snapshot(end='09:50:03'),snapshot(status='running'),snapshot(status='error')]:
            self.assertEqual(self.select([old,bad])['0198']['snapshot']['id'],'old')

    def test_staleness_and_config(self):
        r=snapshot(clock='09:40:00',end='09:40:01')
        self.assertEqual(self.select([r])['0198']['status'],'stale')
        self.assertEqual(self.select([r],policy=c.Policy(1000,900))['0198']['status'],'fresh')
        self.assertEqual(self.select([snapshot('0186','09:30:00','09:30:01')])['0186']['status'],'stale')
        with self.assertRaises(ValueError):c.Policy(True,900)

    def test_mixed_missing_invalid(self):
        for bad in [snapshot(environment='mock'),snapshot(test_mode=1),snapshot(api_id='other'),snapshot(rows=[]),snapshot(collection_started_at=stamp('09:51:00'))]:
            self.assertEqual(self.select([bad])['0198']['status'],'missing')
        self.assertEqual(self.select([])['0186']['status'],'missing')

    def test_late_commit_does_not_rejuvenate(self):
        r=snapshot(clock='09:20:00',end='09:20:01',db_committed_by=stamp('09:50:01'))
        self.assertEqual(self.select([r])['0198']['status'],'stale')

    def test_tie_and_duplicates(self):
        self.assertEqual(self.select([snapshot(identifier='a'),snapshot(identifier='b')])['0198']['status'],'ambiguous')
        r=snapshot();r['rows'][1]['stk_cd']='000001'
        self.assertEqual(self.select([r])['0198']['status'],'missing')

    def test_0950_after_completion_and_hold_report(self):
        rows=[snapshot(clock='09:'+m+':00',end='09:'+m+':01',identifier=m) for m in ['20','30','40','50']]
        rows+=[snapshot('0186','09:40:00','09:40:02')]
        report=c.analyze(rows,as_of=stamp('09:50:02'),environment='real')
        self.assertEqual(report['collection_completed_by']['0198'],stamp('09:50:01'))
        self.assertEqual(len(report['analysis']['rows']),20)
        self.assertEqual(report['status'],'observation_ready')
        self.assertEqual(report['candidate_selection_status'],'held')
        self.assertFalse(report['telegram_send_enabled'])
        self.assertFalse(report['candidate_confirmed'])
        self.assertTrue(report['analysis']['completeness']['top20_complete'])
        with self.assertRaises(ValueError):c.analyze(rows,as_of=stamp('09:50:02'),generated_at=stamp('09:50:01'),environment='real')
        before=c.analyze(rows,as_of=stamp('09:50:00'),environment='real')
        self.assertNotEqual(before['snapshot_selection']['selected']['0198']['snapshot']['id'],'50')

    def test_missing_still_reports(self):
        report=c.analyze([],as_of=stamp('09:50:02'),environment='real')
        self.assertEqual(report['status'],'data_unavailable')
        self.assertEqual(report['snapshot_selection']['selected']['0198']['status'],'missing')
        self.assertFalse(report['completeness']['top20_complete'])

    def test_post_cycle_hook(self):
        with self.assertRaises(ValueError):
            c.briefing_0950([],cycle_completed_at=stamp('09:49:59'),environment='real')
        report=c.briefing_0950([],cycle_completed_at=stamp('09:50:03'),environment='real')
        self.assertEqual(report['as_of'],stamp('09:50:03'))
        self.assertEqual(report['status'],'data_unavailable')
        from kiwoom_collector import telegram_briefing as tg
        with self.assertRaises(tg.BriefingError):tg.validate(report['analysis'])

    def test_units_bound_to_snapshot_and_never_inferred(self):
        rows=[snapshot(),snapshot('0186','09:40:00','09:40:02')]
        report=c.analyze(rows,as_of=stamp('09:50:02'),environment='real')
        self.assertNotIn('turnover',report['input_document']['securities']['000001'])
        proof=dict(environment='real',unit_runtime_verified=True,evidence_reference='reviewed_fixture',
            units=dict(trde_prica='million_KRW',flu_rt='percent'),turnover_period='intraday_cumulative')
        evidence=dict(snapshots={rows[1]['snapshot_sha256']:proof})
        report=c.analyze(rows,as_of=stamp('09:50:02'),environment='real',unit_evidence=evidence)
        metric=report['input_document']['securities']['000001']['turnover'][0]
        self.assertEqual(metric['value'],'123000000')
        self.assertIsNone(__import__('kiwoom_collector.am_briefing',fromlist=['latest']).latest(
            dict(report['input_document'], latest_selection=c.select(rows,as_of=stamp('09:58:00'),environment='real')),
            '000001','turnover',c.instant(stamp('09:58:00'))))
        proof['unit_runtime_verified']=False
        report=c.analyze(rows,as_of=stamp('09:50:02'),environment='real',unit_evidence=evidence)
        self.assertNotIn('turnover',report['input_document']['securities']['000001'])

    def test_read_only_wal_and_log_upper_bound(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'live.db';writer=sqlite3.connect(path)
            try:
                writer.execute('pragma journal_mode=wal');writer.execute('create table t(x)');writer.execute('insert into t values(1)');writer.commit()
                with c.read_db(path) as db:
                    self.assertEqual(db.execute('select x from t').fetchone()[0],1)
                    with self.assertRaises(sqlite3.OperationalError):db.execute('delete from t')
                log=Path(folder)/'collector.log'
                log.write_text('2026-11-03 09:50:00,431 INFO collect 11111111-1111-1111-1111-111111111111: 20 saved(success)\n',encoding='utf-8')
                bound=next(iter(c.log_completions(log).values()))
                self.assertEqual(bound['db_committed_by'],stamp('09:50:00.432000'))
            finally:writer.close()

    def test_existing_priority_policy_still_requires_cap_sector_and_eligibility(self):
        from kiwoom_collector import am_briefing as b
        rows=[snapshot(clock='09:'+m+':00',end='09:'+m+':01',identifier=m) for m in ['20','30','40','50']]
        turn=snapshot('0186','09:40:00','09:40:02',original_status='partial')
        turn['rows'].append(dict(turn['rows'][0],stock_code='000002',raw_json=turn['rows'][0]['raw_json'].replace('000001','000002')))
        turn['snapshot_sha256']=c.digest({k:v for k,v in turn.items() if k!='snapshot_sha256'})
        rows.append(turn)
        proof=dict(environment='real',unit_runtime_verified=True,evidence_reference='reviewed_fixture',
            units=dict(trde_prica='million_KRW',flu_rt='percent'),turnover_period='intraday_cumulative')
        r=c.analyze(rows,as_of=stamp('09:50:02'),environment='real',unit_evidence=dict(snapshots={turn['snapshot_sha256']:proof}))
        doc=deepcopy(r['input_document'])
        scope=dict(source_kind='readonly_adapter',environment='real',market='KRX')
        for code in ['000001','000002']:
            doc['candidate_filter']['decisions'][code]=dict(state='analysis_candidate')
            doc['securities'][code]['sectors']=[dict(scope,name='Sector',basis='reviewed_fixture',observed_at=stamp('09:20:00'),received_at=stamp('09:20:00'))]
            doc['securities'][code]['market_cap']=[dict(scope,value='400000000000',unit='KRW',verified=True,basis='reviewed_fixture',observed_at=stamp('09:50:02'),received_at=stamp('09:50:02'))]
        full=b.analyze(doc,generated_at=stamp('09:50:02'))
        self.assertEqual(len(full['sectors']),1)
        self.assertTrue(all(s['status']=='priority' for s in full['candidates']))
        self.assertFalse(full['candidate_confirmed'])
        doc['securities']['000001']['market_cap']=[]
        held=b.analyze(doc,generated_at=stamp('09:50:02'))
        self.assertIn('market_cap_unverified',held['candidates'][0]['missing_evidence'])
        self.assertFalse(held['sectors'])

    def test_pending_0950_uses_previous_and_preserves_reports(self):
        rows=[snapshot(clock='09:45:00',end='09:45:01',identifier='previous'),snapshot(end='09:50:03')]
        r=c.briefing_0950(rows,as_of=stamp('09:50:00'),environment='real')
        self.assertEqual(r['status'],'observation_ready')
        self.assertEqual(r['snapshot_selection']['selected']['0198']['snapshot']['id'],'previous')
        self.assertEqual(r['collection_cycle_status'],'pending_using_previous_completed_data')
        self.assertIsNone(r['collection_cycle_completed_at'])
        with tempfile.TemporaryDirectory() as folder:
            p=Path(folder)/'first.json';c.save_report(r,p)
            before=p.read_bytes()
            with self.assertRaises(FileExistsError):c.save_report(r,p)
            c.save_report(r,Path(folder)/'second.json')
            self.assertEqual(p.read_bytes(),before)

    def test_partial_unverified_units_do_not_block_observation(self):
        turn=snapshot('0186','09:40:00','09:40:02',original_status='partial')
        rows=[snapshot(),turn]
        r=c.analyze(rows,as_of=stamp('09:50:02'),environment='real')
        self.assertEqual(r['status'],'observation_ready')
        stock=r['observations']['watchlist'][0]
        self.assertEqual(stock['raw_turnover']['value'],'123')
        self.assertIsNone(stock['raw_turnover']['unit'])
        self.assertIsNone(stock['raw_turnover']['confirmed_amount_won'])
        self.assertEqual(r['candidate_selection_status'],'held')
        self.assertNotIn('turnover_partial_coverage',r['limitations'])
        # Sector observation still exists without eligibility/capital/units;
        # missing classifications never become invented sector names.
        selection=c.select(rows,as_of=stamp('09:58:00'),environment='real')
        observed=c.observe(selection,[],{'000001':{'sectors':[{'name':'Sector'}]}})
        self.assertEqual(observed['sectors'][0]['name'],'Sector')
        self.assertEqual(observed['sectors'][0]['observation_rank'],1)
        self.assertTrue(observed['data_warnings'])
        self.assertEqual(observed['status'],'available')

    def test_turnover_scope_top40_and_master_unavailable(self):
        turn=snapshot('0186','09:40:00','09:40:02')
        turn['rows']=[dict(turn['rows'][0],stock_code=f'{i:06}',turnover_rank=i,
            raw_json=turn['rows'][0]['raw_json'].replace('000001',f'{i:06}')) for i in range(1,42)]
        turn['snapshot_sha256']=c.digest({k:v for k,v in turn.items() if k!='snapshot_sha256'})
        r=c.analyze([turn],as_of=stamp('09:50:02'),environment='real',master_db=Path('missing-master.db'))
        self.assertEqual(len(r['observations']['watchlist']),40)
        self.assertEqual(r['status'],'observation_ready')
        self.assertEqual(r['candidate_selection_status'],'held')
        self.assertIsNotNone(r['master_evidence_error'])


if __name__=='__main__':unittest.main()
