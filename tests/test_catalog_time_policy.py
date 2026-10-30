"""Synthetic times/configuration only. Never invoke OAuth or real transport."""
from contextlib import redirect_stdout
from datetime import datetime
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock,patch

from kiwoom_collector import catalog_time_policy as tp,stock_catalog as c,master_data as m
from kiwoom_collector.stock_catalog_live import after_close,ListTransport


class CatalogTimePolicyTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name).resolve()
        self.path=self.root/'policy.json'
        self.doc=dict(schema_version='catalog-time-policy-v1',weekday_start='16:00',
                      allow_weekends=True,verified_holidays=[],maintenance_windows=[])

    def policy(self):
        self.path.write_text(json.dumps(self.doc),encoding='utf-8')
        return tp.load(self.path)

    def test_weekday_close_and_previous_2357_failure(self):
        p=self.policy()
        for at in ('2026-10-30T16:00:00+09:00','2026-10-30T23:57:00+09:00','2026-10-30T23:59:59+09:00'):
            after_close(datetime.fromisoformat(at),policy=p)
        for at in ('2026-10-30T00:00:00+09:00','2026-10-30T15:59:59+09:00'):
            with self.assertRaisesRegex(c.CatalogError,'catalog_weekday_after_close_required'):
                after_close(datetime.fromisoformat(at),policy=p)

    def test_weekends_and_timezone_conversion(self):
        p=self.policy()
        for at in ('2026-10-31T00:00:00+09:00','2026-11-01T10:00:00+09:00','2026-10-30T15:00:00+00:00'):
            after_close(datetime.fromisoformat(at),policy=p)
        self.doc['allow_weekends']=False
        with self.assertRaisesRegex(c.CatalogError,'catalog_weekend_disabled'):
            after_close(datetime.fromisoformat('2026-10-31T12:00:00+09:00'),policy=self.policy())

    def test_verified_holiday_is_explicit_not_guessed(self):
        # Synthetic declaration, not a claim that this date is a real KRX holiday.
        at=datetime.fromisoformat('2026-11-02T10:00:00+09:00')
        with self.assertRaises(tp.TimePolicyError):self.policy().check(at)
        self.doc['verified_holidays']=['2026-11-02']
        self.policy().check(at)

    def test_maintenance_priority_cross_midnight_and_exclusive_end(self):
        self.doc['maintenance_windows']=[['23:30','01:00'],['10:00','11:00']]
        p=self.policy()
        for at in ('2026-10-30T23:57:00+09:00','2026-10-31T00:00:00+09:00','2026-11-01T10:00:00+09:00'):
            with self.assertRaisesRegex(tp.TimePolicyError,'catalog_configured_maintenance_window'):
                p.check(datetime.fromisoformat(at))
        p.check(datetime.fromisoformat('2026-10-31T01:00:00+09:00'))

    def test_invalid_config_never_falls_back_to_permissive_defaults(self):
        for key,value in (('weekday_start','09:00'),('weekday_start','24:00'),
                          ('allow_weekends','true'),('verified_holidays',['2026-99-01']),
                          ('maintenance_windows',[['10:00','10:00']]),('unknown',True)):
            with self.subTest(key=key,value=value):
                doc=dict(self.doc);doc[key]=value
                self.path.write_text(json.dumps(doc),encoding='utf-8')
                with self.assertRaisesRegex(tp.TimePolicyError,'invalid_catalog_time_policy'):tp.load(self.path)
        with self.assertRaises(tp.TimePolicyError):tp.load(self.root/'missing.json')
        with self.assertRaisesRegex(c.CatalogError,'aware_timestamp_required'):
            after_close(datetime(2026,10,31),policy=self.policy())

    def test_denied_clock_or_invalid_policy_prevents_oauth_and_query(self):
        with patch.multiple(m,PROJECT=self.root,MASTER_ROOT=self.root/'master'),patch.object(c,'RUN_ROOT',self.root/'runs'):
            db=m.create_master('real',synthetic=False)
            self.policy()
            for mode in ('daytime','bad_config'):
                with self.subTest(mode=mode),patch.object(c,'datetime') as clock, \
                        patch('kiwoom_collector.stock_catalog_live.ListTransport') as transport,redirect_stdout(io.StringIO()) as out:
                    clock.now.return_value=datetime.fromisoformat('2026-10-30T10:00:00+09:00')
                    provider=Mock()
                    result=c.main(['probe','--db',str(db),'--environment','real','--name',mode,
                        '--execute-approved','--quota-reviewed','--time-policy',str(self.path if mode=='daytime' else self.root/'missing')],
                        token_provider_factory=provider)
                    self.assertEqual(result,2);provider.assert_not_called();transport.assert_not_called()
                    audit=json.loads(out.getvalue())
                    self.assertEqual(audit['actual_api_calls'],0);self.assertEqual(audit['oauth_calls'],0)
                    self.assertFalse(audit['database_updated'])

    def test_time_rechecked_before_each_query_without_incrementing_calls(self):
        self.doc['maintenance_windows']=[['10:00','11:00']]
        session=Mock()
        transport=ListTransport('SYNTHETIC',environment='real',approved=True,session=session,time_policy=self.policy())
        with patch('kiwoom_collector.stock_catalog_live.datetime') as clock:
            clock.now.return_value=datetime.fromisoformat('2026-10-31T10:30:00+09:00')
            with self.assertRaisesRegex(c.CatalogError,'catalog_configured_maintenance_window'):
                transport(market='0',cont_yn='',next_key='',remaining_seconds=300)
        session.post.assert_not_called();self.assertEqual(transport.calls,0)
        transport.close()

    def test_prompt_crossing_into_blocked_time_stops_before_oauth(self):
        with patch.multiple(m,PROJECT=self.root,MASTER_ROOT=self.root/'master'),patch.object(c,'RUN_ROOT',self.root/'runs'):
            db=m.create_master('real',synthetic=False)
            provider=Mock(oauth_calls=0)
            with patch('kiwoom_collector.stock_catalog_live.after_close',
                       side_effect=[None,c.CatalogError('catalog_configured_maintenance_window')]), \
                    patch('kiwoom_collector.stock_catalog_live.ListTransport') as transport,redirect_stdout(io.StringIO()) as out:
                result=c.main(['probe','--db',str(db),'--environment','real','--name','prompt_crossing',
                               '--execute-approved','--quota-reviewed'],token_provider_factory=lambda:provider)
            self.assertEqual(result,2);provider.get_token.assert_not_called()
            provider.close.assert_called_once();transport.assert_not_called()
            self.assertEqual(json.loads(out.getvalue())['oauth_calls'],0)
