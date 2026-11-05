from kiwoom_collector.c12_theme_audit import analyze_themes


def test_overlapping_and_detail_themes_are_independent():
    stocks=[dict(stock_code='000660',ranking_rank=1,raw_change='+3'),
            dict(stock_code='0147B0',ranking_rank=2,raw_change='+2')]
    tags=[dict(stock_code=c,sector_name=t,source='review') for c,t in
          [('000660','반도체'),('000660','HBM'),('000660','HBM'),('0147B0','HBM')]]
    r=analyze_themes(stocks,tags)
    index={t['name']:t for t in r['themes']}
    assert r['coverage']['theme_linked']==2
    assert index['HBM']['distinct_stocks']==2
    assert index['반도체']['reported_positive_count']==1
    assert index['반도체']['simultaneous_rise_status']=='insufficient_multiple_risers'
    assert not r['confirmed_leading_themes']
    detail=analyze_themes(stocks,tags,selected_themes={'HBM'})
    assert [t['name'] for t in detail['themes']]==['HBM']


def test_no_evidence_means_unclassified_and_missing_change_stays_unknown():
    stocks=[dict(stock_code='005930',name='robot'),dict(stock_code='000660')]
    tags=[dict(stock_code='005930',sector_name='robot',source='')]
    assert analyze_themes(stocks,tags)['coverage']['unclassified']==2
    tags[0]['source']='review'
    theme=analyze_themes(stocks,tags)['themes'][0]
    assert theme['change_available_count']==0 and theme['reported_positive_count']==0
