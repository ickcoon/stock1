"""Record the web research actually performed in the 2026-11-03 Codex session.

This is a reproducible evidence import, NOT a search engine or live collector.
Full pages, search snippets and fetch failures are deliberately distinguished.
"""
import json
from pathlib import Path
from kiwoom_collector import theme_research as t

AT='2026-11-03T11:33:23+09:00'
ROOT=Path(__file__).resolve().parents[3]
BASE=ROOT/'reports/C12AJ_THEME_AUDIT_20261103_095002.json'
JOURNAL=ROOT/'diagnostics/theme_review/c12aj-20261103.jsonl'


def evidence(url,note,access='full_page',basis='market_theme',raw_label=None):
    return dict(url=url,note=note,access=access,basis=basis,checked_date='2026-11-03',
                identity_checked=access=='full_page',theme_supported=access!='fetch_failed',
                searched_theme_name=raw_label)


def main():
    if JOURNAL.exists():raise ValueError('existing_journal_preserved')
    base=json.loads(BASE.read_text(encoding='utf8'))
    # Each tuple: code, proposed label, reviewer outcome, actually accessed evidence.
    records=[
      ('263860','정보보안','confirmed',[
        evidence('https://www.kokstock.com/stock/item.asp?item_cd=263860','코드와 보안주 태그 확인',raw_label='보안주(정보)'),
        evidence('https://kpinfo.kr/stock/263860','코드와 정보보안 관련주 태그 대조',raw_label='정보보안 관련주')]),
      ('053800','정보보안','confirmed',[
        evidence('https://www.kokstock.com/stock/item.asp?item_cd=053800','코드와 보안주 태그 확인',raw_label='보안주(정보)'),
        evidence('https://kpinfo.kr/stock/053800','코드와 정보보안 관련주 태그 대조',raw_label='정보보안 관련주')]),
      ('356680','양자보안','confirmed',[
        evidence('https://eureka.hankyung.com/invest/detail?board_id=19722&expanded=','보안·양자 테마의 실제 시장 언급 확인',raw_label='보안·양자'),
        evidence('https://money2.daishin.com/PDF/Out/intranet_data/Product/ResearchCenter/Report/2025/06/54021_IPOD_Axgate_v1.pdf','코드와 양자보안 제품 사업 대조',basis='business_corroboration',raw_label='양자암호 보안 솔루션')]),
      ('005490','2차전지','confirmed',[
        evidence('https://www.kokstock.com/stock/item.asp?item_cd=005490','코드와 2차전지 테마 확인',raw_label='2차전지'),
        evidence('https://www.bosoop.com/stocks/005490/','코드와 2차전지 키워드 대조',raw_label='2차전지')]),
      ('353200','PCB·반도체 기판','confirmed',[
        evidence('https://pickstock.ai/stock/353200','코드와 PCB·반도체 기판 테마 확인',raw_label='PCB·반도체 기판'),
        evidence('https://titanplus.net/stock/353200','코드와 반도체 기판·PCB 테마 대조',raw_label='반도체 기판(FC-BGA/PCB/MLB 등)')]),
      ('180640','지주사','needs_review',[
        evidence('https://www.kokstock.com/stock/item.asp?item_cd=180640','지주사 태그 1개 출처 확인; 경영권 분쟁으로 확대 확정하지 않음',raw_label='지주사')]),
      ('0147B0','2026년 신규상장','needs_review',[
        evidence('https://www.kokstock.com/stock/item.asp?item_cd=0147B0','신규상장 태그 검색 요약 확인; 본문 접근 실패','search_snippet',raw_label='2026년 신규상장'),
        evidence('https://lablup.irpage.co.kr/','상장 일정 확인; 시장 테마 본문 근거는 아님',basis='business_corroboration')]),
      ('420610','AI CCTV','needs_review',[
        evidence('https://ustock.naver.com/service/ipo/420610','코드와 영상분석 사업 설명 검색 요약; 시장 태그 검증 부족','search_snippet',basis='business_corroboration',raw_label='AI 기반 영상·열화상 분석')]),
      ('376900','재생의료','needs_review',[
        evidence('https://www.news2day.co.kr/article/20261016500005?site_preference=normal','재생 플랫폼 사업 및 테마 기대에 대한 비판 보도; 단일 출처',raw_label='장기 재생 및 피부 재생')]),
      ('460940','의료/재활 로봇','needs_review',[
        evidence('https://www.tunet.co.kr/stocks/460940','코드·재활로봇 확장 설명 확인; 단일 출처',raw_label='의료/재활 로봇')]),
      ('443670','인공지능(AI)','needs_review',[
        evidence('https://zumap.co.kr/stock/443670','AI 및 AI 에이전트 테마 확인; 다른 검색 결과는 딥페이크도 언급',raw_label='인공지능(AI)'),
        evidence('https://moneyresearch.co.kr/테마/딥페이크/에스피소프트/','다른 테마 검색 요약; 단순 복수 테마인지 의미 충돌인지 검토','search_snippet',raw_label='딥페이크')]),
      ('003490','우주항공산업','needs_review',[
        evidence('https://www.kokstock.com/stock/item.asp?item_cd=003490','코드와 우주항공산업 태그 확인; 단일 출처',raw_label='우주항공산업')]),
      ('0015S0','사이버보안','needs_review',[
        evidence('https://themeknow.com/stock-detail.php?market=KR&ticker=0015S0','AI 분류 검색 요약만 확인; 본문 실패, 자동차 분류와 관계 검토','search_snippet',raw_label='보안/사이버보안')]),
      ('030530','로봇','needs_review',[
        evidence('https://file.myasset.com/sitemanager/upload/2026/0111/221443/20260111221443410_0_ko.pdf','67페이지 코드와 로봇 자회사·휴머노이드 사업 확인',basis='business_corroboration',raw_label='휴머노이드 로봇'),
        evidence('https://zumap.co.kr/stock/030530','로봇·산업자동화 검색 요약 확인, 본문 시간 초과','search_snippet',raw_label='로봇·산업자동화')]),
      ('359410','조선기자재','needs_review',[
        evidence('https://titanplus.net/stock/359410','코드와 조선기자재 태그 확인; 단일 본문 출처',raw_label='조선기자재'),
        evidence('https://gaemizip.com/heatmap?id=94&name=조선기자재','테마 구성에 코드 포함 검색 요약만 확인','search_snippet',raw_label='조선기자재')]),
      ('396470','HBM','needs_review',[
        evidence('https://moneyresearch.co.kr/테마/hbm/','HBM 테마 페이지에서 코드와 공정제어 역할 확인',raw_label='HBM(고대역폭메모리)'),
        evidence('https://titanplus.net/themes/536','HBM 구성 종목 검색 요약; 본문 가져오기 실패','search_snippet',raw_label='HBM(고대역폭메모리)')]),
      ('005935','반도체','needs_review',[
        evidence('https://zumap.co.kr/stock/005935','우선주와 반도체 태그 검색 요약; 본문 검증 미실시','search_snippet',raw_label='반도체')]),
    ]
    products={
      '069500':'https://file.shinhansec.com/filedoc/clause/k_34.pdf',
      '252670':'https://www.k-etf.com/etf/252670',
      '122630':'https://kind.krx.co.kr/disclosure/etfisudetail.do?method=searchEtfIsuSummary&strIsurCd=12263',
      '114800':'https://file.shinhansec.com/filedoc/clause/k_34.pdf',
      '102110':None,
      '459580':'https://m.samsungfund.com/sheet/20240604/2ETFJ8_20240531.pdf',
      '360750':'https://kind.krx.co.kr/disclosure/etfisudetail.do?isuCd=KR7360750004&menuIndex=0&method=searchEftIsuDetail',
      '379800':'https://www.samsungfund.com/etf/lounge/notice-view.do?no=64894',
      '133690':'https://www.tigeretf.com/upload/etf/20250611092528001913.pdf',
      '233740':'https://www.quantus.kr/signal/en/stock/A233740',
      '379810':'https://www.samsungfund.com/upload/kodex/newsroom/20250722174437650.pdf',
      '229200':'https://m.samsungfund.com/sheet/20220607/2ETF54_20220531.pdf',
      '396500':'https://www.tigeretf.com/upload/etf/20250516040232001654.pdf',
      '488080':'https://www.tigeretf.com/upload/etf/20241226044327005591.pdf',
      '148020':'https://etfro.com/etf/148020',
      '494310':'https://m.samsungfund.com/sheet/20250512/2ETFO8_20250430.pdf',
      '0167A0':'https://bbn.kiwoom.com/rfTP733',
      '475630':'https://www.tigeretf.com/upload/etf/20241007064830005296.pdf',
      '091160':'https://www.trackinsight.com/en/fund/091160',
      '498400':'https://m.samsungfund.com/sheet/20250707/2ETFP4_20250630.pdf',
      '395160':'https://www.samsungfund.com/upload/kodex/newsroom/20260211093328479.pdf',
      '0193T0':'https://bbn.kiwoom.com/rfTP758',
    }
    initial=t.plan(base,[],at=AT)
    by_code={r[0]:r for r in records}
    assert len(initial)==39 and len(products)==22 and len(records)==17
    for item in initial:
        code=item['stock_code']
        urls=[e['url'] for e in by_code[code][3]] if code in by_code else ([products[code]] if products[code] else [])
        t.append(JOURNAL,'search',dict(stock_code=code,stock_name=item['stock_name'],
            status='completed',provider='Codex web.run actual session',checked_date='2026-11-03',
            query_scope='code/name/theme searches for companies; code/ETF searches for product identity',
            evidence_urls=urls,
            outcome='theme_candidates' if code in by_code else 'product_reference_only' if urls else 'no_relevant_result',
            note='Actual search; listed URLs are references, not automatic theme confirmation.'),at=AT,actor='codex_web_research')
    proposed=[]
    names={r['stock_code']:r['name'] for r in base['connections']}
    for code,theme,status,sources in records:
        t.theme_event(base,JOURNAL,'theme_create',name=theme,at=AT,actor='codex_reviewer',
                      reason='기존 테마명 우선 조회 후 실제 웹 근거가 있는 후보 명칭 등록; 종목 연결 확정과 별개')
        e=t.propose(base,JOURNAL,dict(stock_code=code,stock_name=names[code],theme=theme,
            evidence=sources,conflict=code in ('443670','0015S0')),
            at=AT,actor='codex_web_research')
        proposed.append((e['hash'],status))
    for key,status in proposed:
        t.review(base,JOURNAL,key,status=status,at=AT,actor='codex_reviewer',
            reason='서로 다른 본문 출처의 코드·명칭·테마 의미를 대조; 수급 강세나 적격성 확정 아님'
                   if status=='confirmed' else '단일 본문·검색 요약·다른 명칭·사업 설명 중 추가 대조 필요')
    watchlist=json.loads((ROOT/'reports/C12AG_OBSERVATION_20261103_095002.json').read_text(encoding='utf8'))['observations']['watchlist']
    result=t.report(base,t.read_events(JOURNAL),at=AT,watchlist=watchlist)
    assert result['counts']==dict(confirmed=13,needs_review=12,unclassified=22)
    assert result['searched_count']==39
    assert len(t.plan(base,t.read_events(JOURNAL),at=AT))==34
    with (ROOT/'reports/C12AJ_WEB_RESEARCH_20261103.json').open('x',encoding='utf8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(counts=result['counts'],mapping_percent=result['mapping_percent'],searched_count=result['searched_count'])))


if __name__=='__main__':main()
