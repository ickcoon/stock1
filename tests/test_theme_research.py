import json
import os
from pathlib import Path
import tempfile

from kiwoom_collector import theme_research as t

AT='2026-11-03T11:33:00+09:00'


def base():
    return dict(as_of='2026-11-03T09:50:02+09:00',classification_source={'tags':[{'sector_name':'로봇'}]},
        connections=[dict(stock_code='000660',name='A',themes=['로봇']),
                     dict(stock_code='0147B0',name='B',themes=[])])


def evidence(domain,basis='market_theme'):
    return dict(url=f'https://{domain}/stock/0147B0',checked_date='2026-11-03',
                note='test fixture',access='full_page',identity_checked=True,theme_supported=True,basis=basis)


def test_research_gates_cache_and_time():
    t.ROOT.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=t.ROOT) as d:
        p=Path(d)/'journal.jsonl';b=base()
        assert t.journal_path(os.path.relpath(p))==p.resolve()
        try:t.journal_path(t.ROOT/'..'/'outside.jsonl')
        except ValueError:pass
        else:raise AssertionError('outside journal path accepted')
        assert [x['stock_code'] for x in t.plan(b,[],at=AT)]==['0147B0']
        proposal=dict(stock_code='0147B0',stock_name='B',theme='로봇',evidence=[evidence('one.example')])
        e=t.propose(b,p,proposal,at=AT,actor='test')
        try:t.review(b,p,e['hash'],status='confirmed',at=AT,actor='test',reason='weak')
        except ValueError:pass
        else:raise AssertionError('single source accepted')
        proposal['evidence'].append(evidence('two.example'))
        e=t.propose(b,p,proposal,at=AT,actor='test')
        t.review(b,p,e['hash'],status='confirmed',at=AT,actor='test',reason='two sources checked')
        assert not t.plan(b,t.read_events(p),at=AT)
        assert len(t.plan(b,t.read_events(p),at='2026-11-03T09:50:02+09:00'))==1
        try:t.propose(b,p,dict(proposal,stock_code='000660',stock_name='A'),at=AT,actor='test')
        except ValueError:pass
        else:raise AssertionError('classified stock researched')


def test_industry_conflict_and_journal_tamper():
    with tempfile.TemporaryDirectory(dir=t.ROOT) as d:
        p=Path(d)/'journal.jsonl';b=base()
        for conflict,basis in ((False,'official_industry'),(True,'market_theme')):
            e=t.propose(b,p,dict(stock_code='0147B0',stock_name='B',theme='로봇',conflict=conflict,
                evidence=[evidence('one.example',basis),evidence('two.example',basis)]),at=AT,actor='test')
            try:t.review(b,p,e['hash'],status='confirmed',at=AT,actor='test',reason='invalid')
            except ValueError:pass
            else:raise AssertionError('weak/conflicting evidence accepted')
        p.write_text(p.read_text(encoding='utf8').replace('test','tampered'),encoding='utf8')
        try:t.read_events(p)
        except ValueError:pass
        else:raise AssertionError('tampered chain accepted')


def test_theme_history_and_provider_unavailable():
    with tempfile.TemporaryDirectory(dir=t.ROOT) as d:
        p=Path(d)/'journal.jsonl';b=base()
        t.theme_event(b,p,'theme_create',name='휴머노이드',at=AT,actor='test',reason='test evidence')
        t.theme_event(b,p,'theme_rename',old='휴머노이드',new='인간형로봇',at=AT,actor='test',reason='review')
        t.theme_event(b,p,'theme_merge',old='인간형로봇',new='로봇',at=AT,actor='test',reason='review')
        assert t.state(b,t.read_events(p),at=AT)['canonical']('휴머노이드')=='로봇'
        t.search_pending(b,p,at=AT)
        assert t.read_events(p)[-1]['payload']['status']=='unavailable'
        assert not t.read_events(p)[-1]['payload']['evidence_urls']
