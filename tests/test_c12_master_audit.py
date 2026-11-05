from kiwoom_collector.c12_master_audit import match


def test_leading_zero_and_alphanumeric_identity():
    for code in ('000660','0147B0'):
        r={'stock_code':code,'stock_name':'name'}
        assert match({'stock_code':code,'name':'name'},[r])['record']==r


def test_code_name_conflicts_never_fallback():
    rows=[{'stock_code':'000660','stock_name':'A'},
          {'stock_code':'005930','stock_name':'B'}]
    assert match({'stock_code':'000660','name':'B'},rows)['state']=='conflict'
    assert match({'stock_code':'999999','name':'B'},rows)['state']=='name_code_conflict'


def test_unique_name_and_ambiguity():
    rows=[{'stock_code':'000660','stock_name':'A'}]
    assert match({'stock_code':'','name':'A'},rows)['state']=='unique_name'
    assert match({'stock_code':'','name':'A'},rows*2)['state']=='ambiguous_name'
