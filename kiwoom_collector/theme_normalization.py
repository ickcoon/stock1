"""Non-destructive trading label normalization and evidence-gated parent links."""
import argparse
from copy import deepcopy
import json
from pathlib import Path

from .c12_latest import digest
from .c12_theme_audit import analyze_themes

VERSION='c12-aj-simple-theme-links-v1'
SECURITY_DETAILS=frozenset(('정보보안','양자보안','인증','암호화'))
BOARD_LABELS=frozenset(('PCB·반도체 기판','반도체 기판(FC-BGA/PCB/MLB 등)',
                        'PCB(FPCB 등)','PCB','기판·PCB'))


def union_codes(themes,names):
    """Use this union, never sum parent/detail member counts."""
    return sorted({code for theme in themes if theme['name'] in names
                   for code in theme['stock_codes']})


def semiconductor_evidence(row):
    # Explicit existing semiconductor-component assignment is usable evidence.
    if '반도체 소부장' in row['themes']:
        return [dict(basis='existing_explicit_semiconductor_components_tag')]
    result=[]
    for mapping in row.get('confirmed_web_mappings',[]):
        if mapping.get('review',{}).get('status')!='confirmed':continue
        for evidence in mapping.get('evidence',[]):
            if (evidence.get('access')=='full_page' and evidence.get('identity_checked') is True
                    and evidence.get('theme_supported') is True
                    and '반도체' in (evidence.get('searched_theme_name') or '')):
                result.append(deepcopy(evidence))
    return result


def normalize(report,watchlist):
    tags=[];connections=[]
    for original in report['connections']:
        details=set();parents=set();links=[];names=set()
        evidence=semiconductor_evidence(original)
        for raw in original['themes']:
            canonical='기판·PCB' if raw in BOARD_LABELS else raw
            names.add(canonical)
            if canonical in SECURITY_DETAILS:
                details.add(canonical);parents.add('보안')
                links.append(dict(original_theme=raw,detail=canonical,parent='보안',
                                  basis='explicit_user_trading_theme_rule'))
            elif canonical=='기판·PCB':
                details.add(canonical)
                parent='반도체 소부장' if evidence else None
                if parent:parents.add(parent)
                links.append(dict(original_theme=raw,detail=canonical,parent=parent,
                    basis='confirmed_semiconductor_association' if parent else 'semiconductor_association_unconfirmed',
                    supporting_evidence=evidence))
        names.update(parents)
        connections.append(dict(stock_code=original['stock_code'],name=original['name'],
            status=original['status'],original_themes=list(original['themes']),
            themes=sorted(names),parent_themes=sorted(parents),detail_themes=sorted(details),
            links=links,original_connection=deepcopy(original)))
        tags.extend(dict(stock_code=original['stock_code'],sector_name=name,
                         source=VERSION,original_themes=list(original['themes'])) for name in names)
    observation=analyze_themes(watchlist,tags)
    before=report['theme_observations']
    before_counts={t['name']:t['distinct_stocks'] for t in before['themes']}
    after_counts={t['name']:t['distinct_stocks'] for t in observation['themes']}
    # Expose unique family totals so parent/detail rows cannot inflate breadth.
    families=[dict(name='보안',members=sorted({'보안'}|SECURITY_DETAILS)),
              dict(name='반도체 소부장·기판',members=['반도체 소부장','기판·PCB'])]
    for family in families:
        family['stock_codes']=union_codes(observation['themes'],family['members'])
        family['distinct_stocks']=len(family['stock_codes'])
    return dict(policy_version=VERSION,source_reference=digest(report),
        original_evidence_preserved=True,connections=connections,
        before_coverage=before['coverage'],after_coverage=observation['coverage'],
        theme_count_comparison=[dict(name=name,before=before_counts.get(name,0),after=after_counts.get(name,0))
                                for name in sorted(before_counts.keys()|after_counts.keys())],
        observations=observation,family_unique_counts=families,
        detail_reporting_policy='select_parent_or_detail_from_observed_attention_turnover_and_multiple_risers',
        leadership_not_inferred_from_membership=True,
        reporting_examples=[dict(name='보안',use_when='해킹·개인정보 유출 이슈로 관련 종목 동반 상승'),
                            dict(name='양자보안',use_when='양자암호 관련 종목으로 수급 집중'),
                            dict(name='기판·PCB',use_when='기판 관련 종목으로 수급 집중')],
        event_context_verified=False,confirmed_leading_themes=[])


def main():
    p=argparse.ArgumentParser(description=__doc__)
    for name in ('source','observations','output'):p.add_argument('--'+name,required=True,type=Path)
    a=p.parse_args()
    source=json.loads(a.source.read_text(encoding='utf8'))
    watchlist=json.loads(a.observations.read_text(encoding='utf8'))['observations']['watchlist']
    result=normalize(source,watchlist)
    with a.output.open('x',encoding='utf8') as f:json.dump(result,f,ensure_ascii=False,indent=2)
    print(json.dumps(dict(before=result['before_coverage'],after=result['after_coverage'])))


if __name__=='__main__':main()
