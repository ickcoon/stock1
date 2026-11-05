"""Import C12-AK's actually checked web evidence into a separate journal fork."""
import hashlib
import json
from pathlib import Path

from kiwoom_collector import theme_research as t
from kiwoom_collector.c12_theme_expansion import prioritize,compare

ROOT=Path(__file__).resolve().parents[1]


def main(at):
    base=json.loads((ROOT/'reports/C12AJ_THEME_AUDIT_20261103_095002.json').read_text(encoding='utf8'))
    before=json.loads((ROOT/'reports/C12AJ_WEB_RESEARCH_20261103.json').read_text(encoding='utf8'))
    obs=json.loads((ROOT/'reports/C12AG_OBSERVATION_20261103_095002.json').read_text(encoding='utf8'))
    priority=prioritize(before,obs,limit=15)
    source=ROOT/'diagnostics/theme_review/c12aj-20261103.jsonl'
    journal=ROOT/'diagnostics/theme_review/c12ak-20261103.jsonl'
    data=source.read_bytes()
    with journal.open('xb') as f:f.write(data)
    t.append(journal,'stage_start',dict(stage='C12-AK',source_journal_sha256=hashlib.sha256(data).hexdigest(),
        baseline_mapped=13,selected_codes=[r['stock_code'] for r in priority['selected']]),at=at,actor='codex_reviewer')
    def e(url,raw,note,basis='market_theme'):
        return dict(url=url,searched_theme_name=raw,note=note,basis=basis,access='full_page',
            checked_date=at[:10],identity_checked=True,theme_supported=True)
    records=[
      ('0147B0','2026년 신규상장',[
        e('https://gongmolab.vercel.app/ipo/01956751','신규상장','코드·신규상장 표기와 11월 3일 일정 본문 확인'),
        e('https://lablup.irpage.co.kr/','IPO','회사 IR의 2026년 상장 일정 대조','business_corroboration')]),
      ('420610','2026년 신규상장',[
        e('https://www.moneytoring.ai/stock/420610','신규상장','코드·신규상장 표기 확인; 반도체 대표주 등 과도한 AI 자동분류는 채택 안 함'),
        e('https://ustock.naver.com/service/ipo/420610','공모주 IPO','코드·2026년 10월 30일 상장 일정 대조','business_corroboration')]),
      ('180640','지주사',[
        e('https://pflow.keywordegg.com/domestic/180640','지주사 관련주','코드·지주사 테마 명시 확인'),
        e('https://kind.krx.co.kr/external/2026/03/18/001584/20260318007340/11011.htm','지주회사','회사 사업보고서의 지주 사업 대조; KRX 업종만으로 확정한 것 아님','business_corroboration')]),
      ('460940','로봇',[
        e('https://alphasquare.co.kr/home/stock-summary?code=460940','로봇','코드·로봇 테마와 보행재활 로봇 사업 확인'),
        e('https://www.jucok.com/ranklab/stock/theme_view_v2.php?id=4&tab=today','로봇','로봇 구성 종목에서 코드 대조; 페이지 가격은 과거 관측에 사용 안 함')]),
      ('443670','인공지능(AI)',[
        e('https://zumap.co.kr/stock/443670','인공지능(AI)','코드·AI 대표 테마 확인'),
        e('https://m.ibks.com/iko/IKO01/download.do?attatchCd=ATTATCH1&menuCode=IKO010201&seq=3564',
          '기업용 코파일럿','IBK투자증권의 AI 솔루션 유통 사업 대조; 딥페이크와는 별개의 확인된 AI 연결','business_corroboration')]),
      ('030530','로봇',[
        e('https://jugamap.keywordegg.com/stock/030530','로봇 관련주','코드·로봇 관련주 연결 확인'),
        e('https://file.myasset.com/sitemanager/upload/2026/0111/221443/20260111221443410_0_ko.pdf',
          '로봇 손','유안타증권 67페이지의 코드·로봇 자회사 사업 대조','business_corroboration')]),
      ('359410','조선기자재',[
        e('https://titanplus.net/stock/359410','조선기자재','코드·조선기자재 태그 확인'),
        e('https://www.newsprime.co.kr/news/article/?no=753199','선박 기자재 모듈',
          '코드·선박 엔진룸 모듈 공급 사업 대조; 기사에 함께 나온 SMR·AI는 확정 안 함','business_corroboration')]),
      ('396470','HBM',[
        e('https://alphasquare.co.kr/home/stock-summary?code=396470','HBM','코드·HBM 태그 및 후공정 장비 사업 확인'),
        e('https://moneyresearch.co.kr/테마/hbm/','HBM(고대역폭메모리)','HBM 구성 목록에서 코드 및 공정제어 역할 대조')]),
      ('003490','우주항공산업',[
        e('https://www.kokstock.com/stock/item.asp?item_cd=003490','우주항공산업','코드·시장 테마 명시 확인'),
        e('https://www.hanaw.com/download/research/FileServer/WEB/strategy/market/2024/08/05/ETF_Issue_Aug.pdf',
          '우주항공','하나증권 2024년 8월 자료 27페이지의 우주항공 테마 구성에 코드 대조; 현재 ETF 비중으로 해석 안 함')]),
      ('376900','3D 프린팅',[
        e('https://pickstock.ai/stock/376900','3D 프린팅','코드·3D 프린팅 테마 확인'),
        e('https://www.eugenefn.com/common/files/amail/20250415_B_jongsun.park_2348.pdf',
          '의료용 3D 바이오프린터','유진투자증권의 코드·바이오프린터·장기재생 사업 대조','business_corroboration')]),
      ('0015S0','정보보안',[
        e('https://alphasquare.co.kr/home/stock-summary?code=0015S0','인터넷 보안','코드·사이버보안 인증 사업을 동반한 보안 테마 확인'),
        e('https://thevc.kr/fescaro','차량 사이버보안','코드·차량 사이버보안 사업 대조; 자동차와 보안은 상호 배타적 분류가 아님','business_corroboration')]),
    ]
    selected={r['stock_code'] for r in priority['selected']}
    names={r['stock_code']:r['name'] for r in before['connections']}
    assert {r[0] for r in records}<=selected
    proposals=[]
    for code,theme,evidence in records:
        t.append(journal,'search',dict(stock_code=code,stock_name=names[code],status='completed',
            provider='Codex web.run actual C12-AK session',checked_date=at[:10],
            evidence_urls=[r['url'] for r in evidence],outcome='cross_checked_theme_and_business',
            scope='selected_unmapped_only_existing_evidence_reviewed_first'),at=at,actor='codex_web_research')
        t.theme_event(base,journal,'theme_create',name=theme,at=at,actor='codex_reviewer',
            reason='기존 명칭 조회 후 중복 생성 방지; 두 본문 출처에 근거한 최소 후보 등록')
        proposal=t.propose(base,journal,dict(stock_code=code,stock_name=names[code],theme=theme,
            evidence=evidence,conflict=False,prior_review_resolution='원문과 기존 검토 이력 보존; 새로 대조한 범위만 확정'),
            at=at,actor='codex_web_research')
        proposals.append(proposal['hash'])
    for key in proposals:
        t.review(base,journal,key,status='confirmed',at=at,actor='codex_reviewer',
                 reason='독립 본문 출처의 종목 정체성과 실제 테마·사업 연관성 대조 완료; 주도·적격성은 별도 보류')
    for code in sorted(selected-{r[0] for r in records}):
        t.append(journal,'scope_assessment',dict(stock_code=code,outcome='local_product_reference_only',
            web_search_performed=False,reason='기존 ETF 상품 참조 자료 재사용; 개별 종목 테마로 강제 연결하지 않음'),
            at=at,actor='codex_reviewer')
    after=t.report(base,t.read_events(journal),at=at,watchlist=obs['observations']['watchlist'])
    result=compare(before,after,obs,priority)
    result.update(stage_web_searched_stock_count=len(records),local_only_reviewed_stock_count=4,
        journal=str(journal),source_journal_preserved=source.read_bytes()==data,
        journal_prefix_preserved=journal.read_bytes().startswith(data),
        source_journal_sha256=hashlib.sha256(data).hexdigest())
    assert result['after']['mapped']==24 and result['newly_mapped_count']==11
    assert result['existing_mapping_preserved'] and result['source_journal_preserved']
    with (ROOT/'reports/C12AK_THEME_EXPANSION_20261103.json').open('x',encoding='utf8') as f:
        json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(before=result['before']['mapped'],after=result['after']['mapped'],
                         mapping_percent=result['after']['mapping_percent'],new=result['newly_mapped_count'])))


if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--at',required=True)
    main(p.parse_args().at)
