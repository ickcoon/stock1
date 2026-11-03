from copy import deepcopy

from kiwoom_collector.c12_theme_audit import analyze_themes
from kiwoom_collector.c12_theme_expansion import prioritize, attention_view
from kiwoom_collector.theme_normalization import normalize


def fixtures():
    rows=[dict(stock_code='000660',name='A',status='needs_review',themes=[],pending_web_mappings=[]),
          dict(stock_code='0147B0',name='B',status='unclassified',themes=[],pending_web_mappings=[])]
    watch=[dict(stock_code='000660',name='A',ranking_rank=5,turnover_rank=40,raw_change='-5'),
           dict(stock_code='0147B0',name='B',ranking_rank=None,turnover_rank=1,raw_change='+20')]
    base=dict(connections=rows,theme_observations=analyze_themes(watch,[]))
    obs=dict(as_of='2026-11-03T09:50:02+09:00',observations={'watchlist':watch},analysis={'rows':[
        dict(stock_code='000660',rank_history=[dict(slot='09:50',rank=5,top20_complete=True,
             observed_at='2026-11-03T09:50:00+09:00',received_at='2026-11-03T09:50:01+09:00')])]})
    return base,obs


def test_priority_uses_frequency_before_turnover_and_never_amounts():
    base,obs=fixtures();original=deepcopy(obs)
    r=prioritize(base,obs)
    assert r['selected'][0]['stock_code']=='000660'
    assert r['selected'][0]['top5_appearances']==1
    assert obs==original
    for w in obs['observations']['watchlist']:w['raw_turnover']={'value':'9999999999999999'}
    assert prioritize(base,obs)['selected']==r['selected']


def test_future_history_not_used():
    base,obs=fixtures()
    obs['analysis']['rows'][0]['rank_history'][0]['received_at']='2026-11-03T10:00:00+09:00'
    assert prioritize(base,obs)['selected'][0]['top5_appearances']==0
    try:prioritize(base,obs,limit=16)
    except ValueError:pass
    else:raise AssertionError('scope wider than fifteen accepted')


def test_complete_snapshot_absence_is_not_an_appearance():
    base,obs=fixtures()
    obs['analysis']['rows'][0]['rank_history'][0]['rank']=None
    r=prioritize(base,obs)
    assert r['selected'][0]['top5_appearances']==0
    assert r['selected'][0]['top20_appearances']==0


def test_parent_preference_does_not_remove_independent_detail_report():
    watch=[dict(stock_code='000660',ranking_rank=1)]
    base=dict(connections=[dict(stock_code='000660',name='A',status='confirmed',themes=['정보보안'])],
        theme_observations=analyze_themes(watch,[dict(stock_code='000660',sector_name='정보보안',source='test')]))
    normalized=normalize(base,watch)
    assert [x['name'] for x in attention_view(normalized)['ranking']]==['보안']
    assert len(attention_view(normalized,prefer_parents=False)['ranking'])==2
