"""Saved-record integration only; no DB/network or automatic candidate decision."""
from collections import Counter
from copy import deepcopy
from decimal import Decimal,localcontext
import hashlib,json
from . import market_cap as mc


def capital_band(won, *, verified=False):
    """Exploration metadata only: no upper exclusion or weighted score."""
    value=mc.numeric(won)
    band='unknown'
    if value is not None and value>=0:
        if value<Decimal('300000000000'):band='below_floor'
        elif value<=Decimal('5000000000000'):band='primary_exploration'
        elif value<=Decimal('10000000000000'):band='extended_exploration'
        else:band='mega_cap_leader'
    return dict(band=band,verified=verified is True and band!='unknown',
        strategy_status='provisional_hypothesis',upper_cap_exclusion=False,
        sector_detection_excluded=False,top5_observation_excluded=False)


def unit_diagnostic(cap):
    raw=cap.get('raw',{})
    mac,price,shares=(mc.numeric(raw.get(k)) for k in ('mac','cur_prc','flo_stk'))
    result=dict(raw=deepcopy(raw),rest_spec_flo_unit='thousand_KRW',official_plugin_flo_unit='thousand_shares',
        official_plugin_source='https://raw.githubusercontent.com/Kiwoom-Securities/Kiwoom-Plugin/main/kw_runtime/normalize.py',
        unit_status='official_implementation_corroborated_spec_conflict',unit_runtime_verified=False,
        promoted_to_eligibility=False)
    if mac is None or price is None or shares is None or min(mac,price,shares)<=0:
        return dict(result,calculation_status='invalid_inputs')
    with localcontext() as ctx:
        ctx.prec=60
        api=mac*Decimal(100000000)
        reconstructed=price*shares*Decimal(1000)
        delta=api-reconstructed
        result.update(calculation_status='conditional_diagnostic',shares_if_thousand=str(shares*1000),
            api_mac_won=str(api),price_times_thousand_shares_won=str(reconstructed),
            difference_won=str(delta),difference_eok=str(delta/Decimal(100000000)),
            relative_difference_percent=str(delta/api*100))
    return result


def context_key(report,row):
    payload=dict(environment=report['sources']['ranking']['environment'],
        ranking_id=report['sources']['ranking']['id'],turnover_id=report['sources']['turnover']['id'],
        stock_code=row['stock_code'],turnover_raw=row.get('turnover_raw'),turnover_rank=row.get('turnover_rank'),
        ranking_observed_at=report['sources']['ranking'].get('collected_at'),
        turnover_request_at=report['sources']['turnover'].get('request_started_at'))
    return hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()


def integrate(report,capital_records,*,reference_at=None,proofs=None):
    """Bind exact codes and source fingerprints; never infer closing evidence.

    Proof records are externally reviewed input, not authenticated by this engine.
    Default saved measurements remain diagnostic and do not fill historical caps.
    """
    env=report['sources']['ranking']['environment']
    if env!=report['sources']['turnover']['environment']:raise ValueError('Mixed cross environments')
    if any(c.get('environment')!=env for c in capital_records):raise ValueError('Mixed capital environments')
    result=deepcopy(report)
    counts=Counter(c.get('requested_code') for c in capital_records if isinstance(c.get('requested_code'),str))
    caps={c['requested_code']:c for c in capital_records if isinstance(c.get('requested_code'),str) and counts[c['requested_code']]==1}
    at=reference_at or report['sources']['ranking']['collected_at']
    proofs=proofs or {}
    for row in result['rows']:
        code=row['stock_code'];cap=caps.get(code);witness=proofs.get(code,{})
        reason=[];proof_bound=witness.get('cross_context')==context_key(report,row)
        if not report.get('timing',{}).get('collection_window_provisional'):reason.append('collection_window_outside_or_unknown')
        if report.get('market_match')=='mismatch':reason.append('cross_market_mismatch')
        raw_identity=bool(cap and cap.get('raw',{}).get('stk_cd')==code)
        if counts[code]>1:reason.append('duplicate_capital_records')
        elif cap is None:reason.append('market_cap_missing')
        elif not raw_identity:reason.append('market_cap_code_mismatch')
        result_cap=None
        if cap:
            result_cap=dict(requested_code=code,raw=deepcopy(cap.get('raw',{})),
                retrieved_at=cap.get('response_received_at'),market_asof=cap.get('market_asof'),
                source_fingerprint=cap.get('fingerprint'),won_diagnostic=cap.get('market_cap_won_diagnostic'),
                usage='reference_only_not_historical_qualification')
        cash_proof=witness.get('turnover',{}) if proof_bound else {}
        cash=dict(stock_code=code,environment=env,turnover_won=row.get('turnover_won_diagnostic'),
            turnover_rank=row.get('turnover_rank'),market_asof=cash_proof.get('market_asof'),trade_date=cash_proof.get('trade_date'),
            unit_runtime_verified=cash_proof.get('unit_runtime_verified') is True,
            basis=cash_proof.get('basis','intraday_provisional'),closing_basis_verified=cash_proof.get('closing_basis_verified') is True,
            market_conditions_verified=cash_proof.get('market_conditions_verified') is True,evidence=cash_proof.get('evidence'),
            sector_co_rise=cash_proof.get('sector_co_rise'),sector_co_rise_verified=cash_proof.get('sector_co_rise_verified') is True)
        raw_cash=mc.numeric(row.get('turnover_raw'));converted=mc.numeric(row.get('turnover_won_diagnostic'))
        with localcontext() as ctx:
            ctx.prec=80
            if raw_cash is None or converted is None or converted!=raw_cash*1000000:
                cash['unit_runtime_verified']=False;reason.append('turnover_conversion_unverified')
        if cap and raw_identity and not cap.get('issues'):
            decision=mc.eligibility(cap,cash,at=at,evidence=witness.get('capital') if proof_bound else None,
                ranking_0198=row.get('ranking_0198'),top5_appearances=row.get('top5_appearances',0))
        else:
            decision=dict(checks=dict(market_cap_3000_eok=None,closing_turnover_1000_eok=None,turnover_rank_40=None),
                status='검증 보류',reasons=['market_cap_unavailable_or_invalid'],sector_detection_excluded=False,
                priority_review=type(row.get('ranking_0198')) is int and 1<=row['ranking_0198']<=5,sector_co_rise=None)
        if not proof_bound:reason.append('cross_context_evidence_missing')
        co_rise=cash.get('sector_co_rise') if cash['sector_co_rise_verified'] else None
        if co_rise is not True:reason.append('sector_co_rise_not_confirmed')
        reason.extend(decision['reasons'])
        closing_review=decision['status']=='장마감 필수조건 충족' and not reason
        decision.update(reasons=list(dict.fromkeys(reason)),sector_co_rise=co_rise,
            final_review_allowed=closing_review,candidate_confirmed=False,
            status='장마감 최종 검토 가능' if closing_review else '대상 제외' if decision['status']=='대상 제외' else '검증 보류',
            phase='closing_verified_review' if closing_review else 'intraday_provisional_or_unknown')
        row['capital_reference']=result_cap
        row['qualification']=decision
        row['candidate_confirmed']=False
        row['market_cap_won_verified']=cap['market_cap_won_diagnostic'] if cap and decision['checks']['market_cap_3000_eok'] is not None else None
        row['capital_band']=capital_band(row['market_cap_won_verified'],verified=row['market_cap_won_verified'] is not None)
        row['capital_band_reference']=capital_band(cap.get('market_cap_won_diagnostic') if cap else None)
    by_code={r['stock_code']:r for r in result['rows']}
    result['common_rows']=[deepcopy(by_code[r['stock_code']]) for r in report.get('common_rows',[]) if r['stock_code'] in by_code]
    result['capital_integration']=dict(reference_at=at,duplicate_codes=sorted(c for c,n in counts.items() if n>1),
        unused_capital_codes=sorted(set(caps)-set(by_code)),
        cap_reference_count=sum(r['capital_reference'] is not None for r in result['rows']),
        missing_cap_count=sum(r['capital_reference'] is None for r in result['rows']),
        final_review_count=sum(r['qualification']['final_review_allowed'] for r in result['rows']),candidate_confirmed=False)
    result['capital_band_comparison']=[dict(band=band,
        row_count=sum(r['capital_band']['band']==band for r in result['rows']),
        review_count=sum(r['capital_band']['band']==band and r['qualification']['final_review_allowed'] for r in result['rows']),
        observations=[dict(stock_code=r['stock_code'],ranking_0198=r.get('ranking_0198'),
            turnover_raw=r.get('turnover_raw'),turnover_won_diagnostic=r.get('turnover_won_diagnostic'),
            change_percent_raw=r.get('change_percent_raw'),candidate_confirmed=False)
            for r in result['rows'] if r['capital_band']['band']==band])
        for band in ('below_floor','primary_exploration','extended_exploration','mega_cap_leader','unknown')]
    return result
