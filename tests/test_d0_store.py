"""D0 selection persistence tests; all writes are synthetic and temporary."""
from copy import deepcopy
from dataclasses import FrozenInstanceError
from datetime import timedelta
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from kiwoom_collector import d0_store as store, leader_tracking as tracking, tracking_review

FIXTURE=json.loads((Path(__file__).parent/'fixtures/d0_selection_synthetic.json').read_text(encoding='utf-8'))
NOW=tracking.timestamp('2026-10-28T15:30:03+09:00')


def document():
    result=deepcopy(FIXTURE)
    result['selection']['evaluation_sha256']=store.fingerprint(result['evaluation'])
    return result


def rebind(doc):
    doc['selection']['evaluation_sha256']=store.fingerprint(doc['evaluation'])
    return doc


class D0StoreTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)/'store'

    def save(self,doc=None):
        record=store.build_record(document() if doc is None else doc,at=NOW)
        return store.save_record(record,root=self.root,at=NOW)

    def cli(self,args):
        out=io.StringIO()
        with patch.object(store,'now',return_value=NOW),patch('sys.stdout',out):
            code=store.main(['--store-dir',str(self.root),*args])
        return code,json.loads(out.getvalue())

    def input_file(self,doc=None):
        path=Path(self.temp.name)/'input.json'
        path.write_text(json.dumps(document() if doc is None else doc),encoding='utf-8-sig')
        return path

    def test_normal_record_reuses_snapshot_and_preserves_evaluation(self):
        doc=document();record=store.build_record(doc,at=NOW)
        self.assertEqual(record['evaluation'],doc['evaluation'])
        self.assertEqual(record['snapshot']['stock_code'],'005930')
        self.assertEqual(record['snapshot']['d0'],'2026-10-28')
        self.assertEqual(record['snapshot']['captured_at'],doc['selection']['selected_at'])
        self.assertFalse(record['candidate_confirmed'])
        self.assertEqual(record['reference_price']['status'],'unknown')
        store.validate_record(record)

    def test_no_selection_event_cannot_be_inferred_from_review(self):
        with self.assertRaisesRegex(store.D0Error,'actual_selection_event_missing'):
            store.build_record({'evaluation':document()['evaluation']},at=NOW)

    def test_missing_selection_reasons_blocked(self):
        doc=document();doc['selection']['reasons']=[]
        with self.assertRaisesRegex(store.D0Error,'selection_reasons_missing'):store.build_record(doc,at=NOW)

    def test_stock_code_is_exact_string_not_numeric_or_padded(self):
        for value in (5930,'5930','005930 ','005930_NX'):
            doc=document();doc['selection']['stock_code']=value
            with self.subTest(value=value),self.assertRaisesRegex(store.D0Error,'stock_code_string_required'):
                store.build_record(doc,at=NOW)

    def test_unready_or_held_review_does_not_select_candidate(self):
        for mode in ('allowed','checks','reasons','sector'):
            doc=document();q=doc['evaluation']['rows'][0]['qualification']
            if mode=='allowed':q['final_review_allowed']=False
            if mode=='checks':q['checks']['closing_turnover_1000_eok']=None
            if mode=='reasons':q['reasons']=['unknown_evidence']
            if mode=='sector':q['sector_co_rise']=None
            with self.subTest(mode=mode),self.assertRaisesRegex(store.D0Error,'evaluation_not_ready'):
                store.build_record(rebind(doc),at=NOW)

    def test_candidate_confirmed_never_upgraded_or_silently_reset(self):
        for target in ('row','qualification'):
            doc=document();row=doc['evaluation']['rows'][0]
            (row if target=='row' else row['qualification'])['candidate_confirmed']=True
            with self.assertRaisesRegex(store.D0Error,'unconfirmed'):store.build_record(rebind(doc),at=NOW)

    def test_bound_evaluation_cannot_change_after_selection(self):
        doc=document();doc['evaluation']['rows'][0]['ranking_0198']=9
        with self.assertRaisesRegex(store.D0Error,'binding_mismatch'):store.build_record(doc,at=NOW)

    def test_duplicate_event_does_not_overwrite_original(self):
        path=self.save();before=path.read_bytes()
        with self.assertRaisesRegex(store.D0Error,'duplicate_selection_event'):self.save()
        self.assertEqual(path.read_bytes(),before)
        self.assertEqual(len(store.records(self.root)),1)

    def test_same_stock_same_date_distinct_events_preserved(self):
        first=self.save();doc=document();doc['selection']['event_id']='SYNTHETIC-TWO'
        second=self.save(doc)
        a,b=store.load_snapshot(first),store.load_snapshot(second)
        self.assertNotEqual(a.episode_id,b.episode_id)
        self.assertEqual(a.legacy_group_id,b.legacy_group_id)
        repo=tracking.MemoryRepository();repo.add_snapshot(a);repo.add_snapshot(b)
        self.assertEqual(len(store.records(self.root)),2)

    def test_same_event_id_cannot_be_reattached_to_another_stock(self):
        self.save();doc=document();doc['selection']['stock_code']='000660'
        row=doc['evaluation']['rows'][0];row['stock_code']='000660'
        row['capital_reference']['requested_code']=row['capital_reference']['raw']['stk_cd']='000660'
        with self.assertRaisesRegex(store.D0Error,'duplicate_selection_event'):self.save(rebind(doc))

    def test_roundtrip_frozen_snapshot_and_input_immutability(self):
        doc=document();before=deepcopy(doc);path=self.save(doc);snap=store.load_snapshot(path)
        self.assertEqual(doc,before)
        snap.payload['candidate_confirmed']=True
        self.assertFalse(snap.payload['candidate_confirmed'])
        with self.assertRaises(FrozenInstanceError):snap.stock_code='000660'
        doc['evaluation']['rows'][0]['stock_name']='later'
        self.assertNotEqual(store.load_snapshot(path).payload['stock_name'],'later')

    def test_c2_accepts_per_event_episode_id_without_engine_changes(self):
        path=self.save();snap=store.load_snapshot(path)
        result=tracking_review.review_reports([],snapshot=snap,episode_id=snap.episode_id,as_of=NOW)
        self.assertEqual(result['episode_id'],path.stem)
        self.assertEqual(result['status'],'calendar_unknown')
        self.assertFalse(result['eligible_for_tracking'])

    def test_atomic_publication_never_exposes_partial_json(self):
        original=store.os.link;checked=[]
        def publish(pending,target):
            self.assertFalse(Path(target).exists())
            store.validate_record(json.loads(Path(pending).read_text(encoding='utf-8')))
            checked.append(True);return original(pending,target)
        with patch.object(store.os,'link',side_effect=publish):path=self.save()
        self.assertEqual(checked,[True]);self.assertTrue(path.is_file())
        self.assertEqual(list(self.root.glob('.pending-*')),[])

    def test_link_failure_leaves_no_committed_or_temporary_file(self):
        with patch.object(store.os,'link',side_effect=OSError('synthetic failure')):
            with self.assertRaises(OSError):self.save()
        self.assertEqual(list(self.root.iterdir()),[])

    def test_flush_failure_leaves_no_committed_file(self):
        with patch.object(store.os,'fsync',side_effect=OSError('synthetic flush failure')):
            with self.assertRaises(OSError):self.save()
        self.assertEqual(list(self.root.iterdir()),[])

    def test_racing_create_does_not_overwrite_winner(self):
        original=store.os.link
        def race(pending,target):
            original(pending,target)
            raise FileExistsError('synthetic racing winner')
        with patch.object(store.os,'link',side_effect=race):
            with self.assertRaisesRegex(store.D0Error,'duplicate_selection_event'):self.save()
        self.assertEqual(len(store.records(self.root)),1)

    def test_corrupt_json_blocks_new_writes(self):
        self.root.mkdir();(self.root/'broken.json').write_text('{',encoding='utf-8')
        with self.assertRaisesRegex(store.D0Error,'corrupt'):self.save()
        self.assertEqual(list(self.root.glob('*.json')),[self.root/'broken.json'])

    def test_tampered_snapshot_rejected_even_with_recomputed_checksum(self):
        path=self.save();record=json.loads(path.read_text(encoding='utf-8'))
        record['snapshot']['stock_code']='000660'
        record['integrity_sha256']=store.fingerprint({k:v for k,v in record.items() if k!='integrity_sha256'})
        path.write_text(json.dumps(record),encoding='utf-8')
        with self.assertRaisesRegex(store.D0Error,'corrupt'):store.load_snapshot(path)

    def test_checksum_and_filename_tampering_rejected(self):
        path=self.save();record=json.loads(path.read_text(encoding='utf-8'))
        record['integrity_sha256']='wrong';path.write_text(json.dumps(record),encoding='utf-8')
        with self.assertRaisesRegex(store.D0Error,'corrupt'):store.records(self.root)

    def test_future_and_old_selection_dates_rejected(self):
        for offset in (timedelta(days=1),timedelta(days=-1),timedelta(minutes=-10)):
            doc=document();doc['selection']['selected_at']=(NOW+offset).isoformat()
            with self.assertRaisesRegex(store.D0Error,'backdated_or_stale'):store.build_record(doc,at=NOW)

    def test_save_does_not_accept_stale_previously_built_record(self):
        record=store.build_record(document(),at=NOW)
        with self.assertRaisesRegex(store.D0Error,'backdated_or_stale'):
            store.save_record(record,root=self.root,at=NOW+timedelta(days=1))

    def test_future_source_data_blocked(self):
        doc=document();doc['evaluation']['sources']['turnover']['response_received_at']='2026-10-28T15:31:00+09:00'
        with self.assertRaisesRegex(store.D0Error,'chronology'):store.build_record(rebind(doc),at=NOW)

    def test_unknown_source_receipt_does_not_use_collection_time(self):
        doc=document();doc['evaluation']['sources']['ranking'].pop('response_received_at')
        with self.assertRaises(ValueError):store.build_record(rebind(doc),at=NOW)

    def test_fixture_and_test_source_not_actual_selection(self):
        for mode in ('kind','test'):
            doc=document();source=doc['evaluation']['sources']['ranking']
            if mode=='kind':source['source_kind']='fixture'
            else:source['test_mode']=True
            with self.assertRaises(store.D0Error):store.build_record(rebind(doc),at=NOW)

    def test_mixed_environment_and_market_rejected(self):
        for mode in ('environment','market'):
            doc=document()
            if mode=='environment':doc['evaluation']['sources']['turnover']['environment']='real'
            else:doc['selection']['market']='NXT'
            with self.assertRaises(store.D0Error):store.build_record(rebind(doc),at=NOW)

    def test_mock_and_real_records_keep_distinct_identity(self):
        first=self.save();doc=document();doc['selection']['environment']='real'
        for s in doc['evaluation']['sources'].values():s['environment']='real'
        doc['evaluation']['rows'][0]['capital_reference']['environment']='real'
        second=self.save(rebind(doc))
        self.assertNotEqual(first,second)
        self.assertEqual({r['snapshot']['environment'] for r in store.records(self.root)},{'mock','real'})

    def test_verified_d0_bar_requires_evidence_and_is_not_inferred_from_quote(self):
        doc=document()
        record=store.build_record(doc,at=NOW)
        self.assertIsNone(record['reference_price']['value'])
        doc['d0_bar']=dict(stock_code='005930',environment='mock',market='KRX',trade_date='2026-10-28',
            market_at='2026-10-28T15:30:00+09:00',available_at='2026-10-28T15:30:01+09:00',
            phase='post_market',source='SYNTHETIC confirmed daily bar',close='100',high='105',low='95',volume='1000')
        with self.assertRaisesRegex(store.D0Error,'reference_evidence_required'):store.build_record(doc,at=NOW)
        doc['reference_evidence']='SYNTHETIC reviewed closing evidence'
        path=self.save(doc)
        self.assertEqual(store.load_snapshot(path).d0_bar.close,100)
        self.assertEqual(store.load_record(path)['reference_price']['status'],'verified_d0_close')

    def test_future_d0_bar_not_attached_to_selection(self):
        doc=document();doc['d0_bar']=dict(stock_code='005930',environment='mock',market='KRX',trade_date='2026-10-28',
            market_at='2026-10-28T15:30:00+09:00',available_at='2026-10-28T16:00:00+09:00',phase='post_market',source='SYNTHETIC',close='100')
        with self.assertRaises(ValueError):store.build_record(doc,at=NOW)

    def test_sensitive_input_rejected_and_not_printed(self):
        doc=document();doc['evaluation']['authorization']='Bearer SYNTHETIC_CREDENTIAL'
        path=self.input_file(doc);code,result=self.cli(['--input',str(path),'--save'])
        self.assertEqual(code,1);self.assertEqual(result['saved'],0)
        self.assertNotIn('SYNTHETIC_CREDENTIAL',json.dumps(result))
        self.assertFalse(self.root.exists())

    def test_default_and_preview_are_read_only(self):
        code,result=self.cli([])
        self.assertEqual(code,0);self.assertEqual(result['planned'],0)
        self.assertFalse(self.root.exists())
        code,result=self.cli(['--input',str(self.input_file())])
        self.assertEqual(code,0);self.assertEqual(result['planned'],1);self.assertEqual(result['saved'],0)
        self.assertFalse(self.root.exists())

    def test_save_flag_writes_one_and_duplicate_is_blocked(self):
        path=self.input_file()
        code,result=self.cli(['--input',str(path),'--save'])
        self.assertEqual(code,0);self.assertEqual(result['saved'],1)
        code,result=self.cli(['--input',str(path),'--save'])
        self.assertEqual(code,1);self.assertEqual(result['reason'],'duplicate_selection_event')

    def test_missing_actual_selection_save_zero(self):
        code,result=self.cli(['--input',str(self.input_file({'evaluation':document()['evaluation']})),'--save'])
        self.assertEqual(code,1);self.assertEqual(result['saved'],0)
        self.assertFalse(self.root.exists())

    def test_operational_paths_and_fake_db_left_unchanged(self):
        db=Path(self.temp.name)/'operating.db';db.write_bytes(b'SYNTHETIC do not touch')
        before=db.read_bytes();self.save()
        self.assertEqual(db.read_bytes(),before)
        for root in (store.PROJECT/'data',store.PROJECT/'kiwoom_collector',store.PROJECT):
            with self.assertRaises(store.D0Error):store.store_root(root)
        self.assertEqual(store.store_root(store.DEFAULT_ROOT),store.DEFAULT_ROOT)

    def test_historical_record_can_be_loaded_without_recreating_event(self):
        path=self.save()
        with patch.object(store,'now',return_value=NOW+timedelta(days=30)):
            self.assertEqual(store.load_snapshot(path).selection_event_id,'SYNTHETIC-ONE')

    def test_corrupt_store_blocks_cli_preview_too(self):
        self.root.mkdir();(self.root/'broken.json').write_text('null',encoding='utf-8')
        code,result=self.cli([])
        self.assertEqual(code,1);self.assertEqual(result['saved'],0)
