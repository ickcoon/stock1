from copy import deepcopy

from kiwoom_collector.c12_theme_audit import analyze_themes
from kiwoom_collector.theme_normalization import normalize, union_codes


def sample(rows):
    watch=[dict(stock_code=r['stock_code'],raw_change='+3',ranking_rank=1) for r in rows]
    tags=[dict(stock_code=r['stock_code'],sector_name=name,source='test') for r in rows for name in r['themes']]
    return dict(connections=rows,theme_observations=analyze_themes(watch,tags)),watch


def test_security_overlap_and_independent_detail_reporting():
    report,watch=sample([dict(stock_code='000660',name='A',status='confirmed',
                              themes=['정보보안','양자보안','인증','암호화'])])
    original=deepcopy(report)
    r=normalize(report,watch)
    assert report==original
    assert set(r['connections'][0]['themes'])=={'보안','정보보안','양자보안','인증','암호화'}
    assert r['family_unique_counts'][0]['distinct_stocks']==1
    assert union_codes(r['observations']['themes'],{'보안','양자보안'})==['000660']
    assert not r['confirmed_leading_themes']


def test_generic_pcb_does_not_get_semiconductor_parent():
    report,watch=sample([dict(stock_code='0147B0',name='A',status='confirmed',themes=['PCB'])])
    row=normalize(report,watch)['connections'][0]
    assert row['themes']==['기판·PCB'] and row['parent_themes']==[]


def test_semiconductor_link_requires_confirmed_full_evidence():
    mapping=dict(review={'status':'confirmed'},evidence=[dict(access='full_page',identity_checked=True,
                 theme_supported=True,searched_theme_name='PCB·반도체 기판',url='https://example.com')])
    report,watch=sample([dict(stock_code='353200',name='A',status='confirmed',
                              themes=['PCB·반도체 기판'],confirmed_web_mappings=[mapping])])
    r=normalize(report,watch)
    assert set(r['connections'][0]['themes'])=={'기판·PCB','반도체 소부장'}
    assert r['family_unique_counts'][1]['distinct_stocks']==1
    mapping['review']['status']='needs_review'
    assert normalize(report,watch)['connections'][0]['parent_themes']==[]
