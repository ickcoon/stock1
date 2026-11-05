from copy import deepcopy

from kiwoom_collector.c12_external_theme_bridge import reference_match,label_view,calendar


def test_exact_name_reference_never_becomes_verified_code():
    watch=[dict(stock_code='000660',name='SK하이닉스'),dict(stock_code='005935',name='삼성전자우')]
    row=dict(raw_stock_name='SK하이닉스',raw_stock_code=None)
    original=deepcopy(row)
    result=reference_match(row,watch)
    assert result['watchlist_stock_code']=='000660' and result['code_verified'] is False
    assert row==original
    assert reference_match(dict(raw_stock_name='하이닉스',raw_stock_code=None),watch)['state']=='unconfirmed'
    assert reference_match(dict(raw_stock_name='삼성전자우',raw_stock_code='005930'),watch)['state']=='unconfirmed'
    assert reference_match(dict(raw_stock_name='SK하이닉스',raw_stock_code=660),watch)['state']=='unconfirmed'


def test_composite_labels_not_automatically_split():
    assert label_view('반도체 관련주')['name']=='반도체'
    assert label_view('로봇 관련주')['name']=='로봇'
    assert label_view('전선/전력설비')['name']=='전선·전력설비'
    for name in ('원전/로봇 등','2차전지/전력설비 등','반도체 관련주 2'):
        result=label_view(name)
        assert result['name']==name and result['state']=='composite_or_numbered_label_needs_review'


def test_calendar_keeps_theme_without_stocks_and_missing_dates_unknown():
    loaded=dict(records=[],theme_records=[dict(trade_date='2026-04-22',raw_theme='로봇',source='fixture')],
        metadata={'missing_capture_dates':[dict(trade_date='2026-04-23',status='request_capture')]})
    r=calendar(loaded,'2026-04-22')
    assert r['external_trader_close']['distinct_raw_themes']==1
    assert r['external_trader_close']['coverage']=='partial_transcription'
    missing=calendar(loaded,'2026-04-23')
    assert missing['external_trader_close']['explicit_missing_capture']
    assert missing['external_trader_close']['absence_means_no_theme'] is False
    assert not missing['arvis_selection']['records']
