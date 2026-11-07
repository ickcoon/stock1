"""Focused feature-news query, cutoff, cache and compact UI checks."""
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from unittest.mock import patch,MagicMock
from kiwoom_collector import c16_news as news

AT='2026-11-05T09:50:00+09:00'
NAME='OCI홀딩스'


def article(title='[특징주] OCI홀딩스, 친환경 정책 수혜 기대',**extra):
    return dict(title=title,url='https://example.com/a',published_at='2026-11-05T09:30:00+09:00',**extra)


class HubNewsTests(unittest.TestCase):
    def setUp(self):
        root=Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(patch.object(news,'SEARCH_CACHE',root/'news.json'))
        self.enterContext(patch.object(news,'CACHE',root/'absent.json'))

    def test_identical_exact_queries_without_sector_or_date_expansion(self):
        self.assertEqual(news.query_for('무시',[NAME],AT),NAME+' 특징주')
        with self.assertRaises(ValueError):news.query_for('',[NAME,'현대차'],AT)
        response=MagicMock()
        response.__enter__.return_value.read.return_value=json.dumps(dict(items=[])).encode()
        with patch.object(news,'hub_credentials',return_value=('synthetic-id','synthetic-key')),patch.object(news,'urlopen',return_value=response) as request:
            news.search_hub('신재생에너지',[NAME],AT)
            hub_query=parse_qs(urlparse(request.call_args.args[0].full_url).query)['query'][0]
        response.__enter__.return_value.read.return_value=b'<rss><channel/></rss>'
        with patch.object(news,'urlopen',return_value=response) as request:
            news.search_public('신재생에너지',[NAME],AT)
            rss_query=parse_qs(urlparse(request.call_args.args[0]).query)['q'][0]
        self.assertEqual(hub_query,rss_query);self.assertEqual(hub_query,NAME+' 특징주')

    def test_both_providers_once_and_shared_company_cache(self):
        with patch.object(news,'search_hub',return_value=dict(status='searched',articles=[article()])) as hub,patch.object(news,'search_public',return_value=dict(status='searched',articles=[])) as google:
            first=news.search_news('신재생에너지',[NAME,NAME],AT)
            second=news.search_news('다른섹터',[NAME],AT)
        self.assertEqual(hub.call_count,1);self.assertEqual(google.call_count,1)
        self.assertEqual(hub.call_args.args[1],[NAME]);self.assertEqual(google.call_args.args[1],[NAME])
        self.assertTrue(second['cache_hit']);self.assertEqual(len(first['articles']),1)

    def test_cutoff_unknown_time_old_news_and_stock_relevance(self):
        raw=[article(),dict(article(),published_at='2026-11-05T09:51:00+09:00',url='https://example.com/late'),
             dict(article(),published_at='2026-11-04T09:00:00+09:00',url='https://example.com/old',title='[특징주] OCI홀딩스 정책 수혜 기대'),
             dict(article(),published_at='unknown'),article('[특징주] 다른종목 공급계약 체결')]
        accepted=news.relevant_articles(raw,'신재생에너지',[NAME],AT)
        self.assertEqual(len(accepted),2)
        self.assertTrue(all(news.instant(a['published_at'])<=news.instant(AT) for a in accepted))
        self.assertEqual(accepted[0]['published_at'],'2026-11-05T09:30:00+09:00')
        self.assertEqual(news.relevant_articles([article('기아 퇴치 국제 지원')],'자동차',['기아'],AT),[])

    def test_cross_provider_dedup_and_tracking_url(self):
        a=article(source='매체')
        b=dict(a,title=a['title']+' - 매체',provider='Google_News_RSS',url='https://news.google.com/rss/articles/one')
        c=dict(a,title='다른 제목 OCI홀딩스 특징주',url=a['url']+'?utm_source=naver')
        self.assertEqual(len(news.relevant_articles([a,b,c],'',[NAME],AT)),1)

    def test_relevant_title_precedes_passing_mention(self):
        incidental=dict(article('[특징주] 다른종목 급등'),description='OCI홀딩스도 언급',published_at='2026-11-05T09:45:00+09:00')
        direct=dict(article(),published_at='2026-11-04T09:00:00+09:00',url='https://example.com/direct')
        rows=news.relevant_articles([incidental,direct],'',[NAME],AT)
        self.assertEqual(rows[0]['published_at'],direct['published_at'])
        self.assertEqual(len(rows),1)
        self.assertEqual(news.company_summary(rows,NAME)['summary'],'친환경 정책 수혜 기대')

    def test_common_material_reuses_company_articles(self):
        a=dict(article('[특징주] 현대차·기아 관세 완화 기대'),matched_names=['현대차','기아'])
        leaders=[dict(name='현대차',stock_code='005380'),dict(name='기아',stock_code='000270')]
        self.assertEqual(news.common_articles([a],leaders),[a])
        self.assertEqual(news.common_articles([dict(a,matched_names=['현대차'])],leaders),[])

    def test_independent_links_collapsed_toggle_and_empty_messages(self):
        from kiwoom_collector.c16_ui import company_news,sector_news
        a=article(matched_names=[NAME]);b=dict(a,title='[특징주] OCI홀딩스, 1000억원 공급계약',url='https://example.com/b')
        html=company_news(dict(articles=[a,b]))
        self.assertIn('<summary>친환경 정책 수혜 기대</summary>',html)
        self.assertIn('href="https://example.com/a"',html);self.assertIn('href="https://example.com/b"',html)
        self.assertNotIn(' open',html)
        self.assertIn('관련 상승 재료 기사 없음',company_news(None))
        self.assertIn('공통 특징주 재료 없음',sector_news(dict(articles=[a]),[dict(name=NAME,stock_code='010060')]))
        for forbidden in ('재료 확인','관련 뉴스만 확인','기사 상세'):
            self.assertNotIn(forbidden,html)

    def test_negative_news_is_reported_without_causal_verdict(self):
        a=article('[특징주] OCI홀딩스 계약 취소',matched_names=[NAME])
        result=news.company_summary([a],NAME)
        self.assertIn('취소',result['summary']);self.assertNotIn('status',result)

    def test_company_query_cache_does_not_leak_incidental_other_stocks(self):
        row=article('[특징주] SK이터닉스 급등',description='OCI홀딩스도 언급',query=NAME+' 특징주')
        selected=news.relevant_articles([row],'',[NAME,'SK이터닉스'],AT)
        self.assertEqual(selected,[])
        self.assertEqual(news.company_summary(selected,'SK이터닉스')['articles'],[])

    def test_cached_later_search_is_refiltered_for_0940(self):
        late=dict(article(),published_at='2026-11-05T09:45:00+09:00')
        with patch.object(news,'search_hub',return_value=dict(status='searched',articles=[late])) as hub,patch.object(news,'search_public',return_value=dict(status='searched',articles=[])) as google:
            self.assertEqual(len(news.search_company(NAME,AT)['articles']),1)
            earlier=news.search_company(NAME,'2026-11-05T09:40:00+09:00')
        self.assertTrue(earlier['cache_hit']);self.assertEqual(earlier['articles'],[])
        self.assertEqual(hub.call_count,1);self.assertEqual(google.call_count,1)

    def test_72_hour_boundaries_and_stage_counts(self):
        a=article()
        duplicate=dict(a,url=a['url']+'?utm_source=naver')
        boundary=dict(article('[특징주] OCI홀딩스 공급계약 발표'),url='https://example.com/boundary',published_at='2026-11-02T09:50:00+09:00')
        old=dict(article('[특징주] OCI홀딩스 오래된 기사'),url='https://example.com/old',published_at='2026-11-02T09:49:59+09:00')
        future=dict(article('[특징주] OCI홀딩스 미래 기사'),url='https://example.com/future',published_at='2026-11-05T09:50:01+09:00')
        other=dict(article('[특징주] 다른종목 기사'),url='https://example.com/other')
        unknown=dict(article('[특징주] OCI홀딩스 미확인 시각'),url='https://example.com/unknown',published_at='unknown')
        counts=news.article_counts([a,duplicate,boundary,old,future,other,unknown],NAME,AT)
        self.assertEqual(counts['deduplicated_count'],6)
        self.assertEqual(counts['time_pass_count'],3)
        self.assertEqual(counts['related_count'],2)
        self.assertEqual(counts['rejected_reasons'],dict(published_before_3day_window=1,published_after_analysis=1,publication_time_unverified=1))

    def test_successful_empty_cache_is_not_kept_forever(self):
        old=dict(article(),published_at='2026-10-01T09:00:00+09:00')
        with patch.object(news,'search_hub',return_value=dict(status='searched',articles=[old])) as hub,patch.object(news,'search_public',return_value=dict(status='searched',articles=[])) as google:
            news.search_company(NAME,AT)
            records=news.search_records();records[-1]['searched_at']='2026-11-05T00:00:00+00:00'
            news.SEARCH_CACHE.write_text(json.dumps(dict(records=records)),encoding='utf8')
            result=news.search_company(NAME,AT)
        self.assertFalse(result['cache_hit'])
        self.assertEqual(hub.call_count,2);self.assertEqual(google.call_count,2)

    def test_missing_cache_is_nonfatal_and_snapshot_deduplicates(self):
        result=news.cached_summary('신재생에너지',[dict(name=NAME,stock_code='010060')],AT)
        self.assertEqual(result['companies']['010060']['summary'],'관련 상승 재료 기사 없음')
        snapshot=dict(reference_at=AT,analysis=dict(sectors=[dict(name='A',leaders=[dict(name=NAME,stock_code='010060')]*3),dict(name='B',leaders=[dict(name=NAME,stock_code='010060')])]))
        with patch.object(news,'search_news',return_value={}) as search:news.search_snapshot(snapshot)
        self.assertEqual(search.call_args_list[0].args[1],[NAME]);self.assertEqual(search.call_args_list[1].args[1],[])

    def test_display_dedup_prefers_original_and_keeps_comment_and_cache(self):
        from copy import deepcopy
        from kiwoom_collector.c16_ui import display_articles,company_news
        a=article('[특징주] OCI홀딩스 친환경 정책 수혜 기대',matched_names=[NAME],provider='NAVER_API_HUB')
        b=dict(a,url='https://news.google.com/rss/articles/id?oc=5',provider='Google_News_RSS',published_at='2026-11-05T09:31:00+09:00')
        c=dict(a,url=a['url']+'?utm_source=naver')
        rows=[a,b,c];before=deepcopy(rows)
        self.assertEqual(display_articles(rows),[a]);self.assertEqual(rows,before)
        html=company_news(dict(summary='원래 코멘트 유지',articles=rows))
        self.assertIn('<summary>원래 코멘트 유지</summary>',html)
        self.assertEqual(html.count('<a '),1)

    def test_display_paraphrases_same_policy_without_stock_exceptions(self):
        from kiwoom_collector.c16_ui import display_articles
        a=article('[특징주] 트럼프 車 정책 후퇴 기대감',matched_names=[NAME])
        b=dict(a,title='[특징주] 美 관세 완화 기대에 상승',url='https://other.example/b',published_at='2026-11-05T09:35:00+09:00')
        self.assertEqual(display_articles([a,b]),[b])

    def test_display_keeps_new_dates_amounts_and_clinical_information(self):
        from kiwoom_collector.c16_ui import display_articles
        a=article('[특징주] OCI홀딩스 공급계약 발표',description='1000억원 공급계약',matched_names=[NAME])
        new_amount=dict(a,url='https://example.com/new',description='2000억원 공급계약')
        new_day=dict(a,url='https://example.com/day',published_at='2026-11-04T09:30:00+09:00')
        self.assertEqual(len(display_articles([a,new_amount,new_day])),3)
        phase_a=dict(a,title='임상 결과 발표',description='임상 2상 성공')
        phase_b=dict(a,title='임상 결과 발표',description='임상 3상 실패',url='https://example.com/phase')
        self.assertEqual(len(display_articles([phase_a,phase_b])),2)

    def test_positive_subject_and_five_link_limit(self):
        from kiwoom_collector.c16_ui import company_news
        for title in ('[특징주] 현대차 약세, 기아 강세','[특징주] 현대차 실적 부진에 하락','[특징주] 대동, 현대차와 로봇 사업 확대 기대'):
            self.assertFalse(news.rising_article(article(title),'현대차'))
        for title in ('[특징주] 현대차그룹 정책 수혜 기대','[특징주] 현대차·기아 강세, 삼성전자는 약세','특징주, 현대차-신제품 기대감에 상승'):
            self.assertTrue(news.rising_article(article(title),'현대차'))
        comment=news.material_comment(article('[특징주] 현대차·기아 강세, 삼성전자는 약세',matched_names=['현대차']))
        self.assertNotIn('삼성전자',comment)
        rows=[article('[특징주] OCI홀딩스 '+str(i*1000)+'억원 공급계약',matched_names=[NAME]) for i in range(1,8)]
        for i,row in enumerate(rows):row['url']='https://example.com/'+str(i)
        html=company_news(dict(summary='원래 코멘트',articles=rows))
        self.assertEqual(html.count('<a '),5)
        self.assertIn('<summary>원래 코멘트</summary>',html)
