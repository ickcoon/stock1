"""Read-only display/eligibility evidence; never edits source verification flags."""
from decimal import Decimal, localcontext
import re
import sqlite3

from .am_time_policy import instant
from .am_momentum import number
from .capital_filter import capital_band
from .c12_latest import read_db
from .equity_eligibility import decide, EXCLUDED_TYPES, CANDIDATE_VERSION
from .master_data import Queries, digest
from .c15_identity import exact_code

REVIEW_NAME=re.compile(r'ETF|ETN|스팩|리츠$|^(?:KODEX|TIGER|RISE|SOL|PLUS|ACE|HANARO|KOSEF)\s',re.IGNORECASE)


def product_evidence(stock, record, *, as_of, existing_decision=None):
    """An exact, dated official code record can exclude; names only raise review."""
    kind=record.get('product_type','UNKNOWN')
    try:
        verified=(exact_code(record.get('stock_code'))==stock['stock_code'] and
            record.get('name_basis')=='checked_KRX_daily_name_with_dated_official_master_join' and
            bool(record.get('master_body_hash')) and record.get('market') in ('KOSPI','KOSDAQ') and
            record['basis_date']<=instant(as_of).date().isoformat() and instant(record['available_at'])<=instant(as_of))
    except (KeyError,TypeError,ValueError):verified=False
    identity_verified=verified
    verified=verified and kind in EXCLUDED_TYPES | {'common_stock','preferred_stock'}
    # Reuse a confirmed, fingerprint-bound C12 decision when the equity-only
    # KRX master has no product row. Provisional market labels stay pending.
    prior=existing_decision or {};source=prior.get('evidence') or {};raw=source.get('raw') or {}
    prior_kind=next((r.split(':',1)[1] for r in prior.get('reasons',[]) if r.startswith('excluded_type:')),None)
    if 'documented_etf_warning' in prior.get('reasons',[]):prior_kind='ETF'
    try:
        prior_verified=(prior.get('state')=='confirmed_excluded' and prior.get('confidence')=='verified'
            and prior.get('rule_version')==CANDIDATE_VERSION and prior.get('raw_sha256')==digest(raw)
            and raw.get('code')==stock['stock_code'] and source.get('source')=='ka10099' and bool(source.get('run_id'))
            and instant(source['updated_at'])<=instant(source['applied_at'])<=instant(as_of)
            and prior_kind in EXCLUDED_TYPES)
    except (KeyError,TypeError,ValueError):prior_verified=False
    if prior_verified:
        if verified and kind!=prior_kind:
            return dict(stock_code=stock['stock_code'],product_type='UNKNOWN',verified=False,identity_verified=identity_verified,
                excluded=False,status='conflict_pending',name_review_only=False,basis=dict(krx=record,kiwoom=prior))
        if not verified:
            return dict(stock_code=stock['stock_code'],product_type=prior_kind,verified=True,identity_verified=True,
                excluded=True,status='confirmed_excluded',name_review_only=False,basis=prior)
    decision=decide(dict(security_type=kind,type_verified=verified,type_evidence=record.get('name_basis','')))
    excluded=verified and kind in EXCLUDED_TYPES and decision['state']=='excluded'
    return dict(stock_code=stock['stock_code'],product_type=kind if verified else 'UNKNOWN',
                verified=verified,identity_verified=identity_verified,excluded=excluded,decision=decision,
                name_review_only=bool(REVIEW_NAME.search(stock.get('name') or '')),
                basis=record if verified else None,
                status='confirmed_excluded' if excluded else 'reference_type' if verified else 'pending')


def capital_reference(record, *, stock_code, as_of, contract, minimum_won):
    result=dict(status='unverified',won=None,basis_date=None,eligibility_status='pending',
                intraday_cap_verified=False,api_unit_explicitly_documented=False)
    cap=record.get('daily_capital') or {}
    try:
        at=instant(as_of)
        if (exact_code(record.get('stock_code'))!=stock_code
                or record.get('name_basis')!='checked_KRX_daily_name_with_dated_official_master_join'
                or not record.get('master_body_hash') or record.get('market') not in ('KOSPI','KOSDAQ')
                or cap['basis_date']!=record['basis_date'] or cap['basis_date']>=at.date().isoformat()
                or instant(record['available_at'])>at or instant(cap['collected_at'])>at
                or cap.get('raw_fields_match') is not True
                or contract.get('capital_currency')!='KRW'
                or not contract.get('price_unit_source') or not contract.get('capital_formula_source')):
            return dict(result,reason='identity_time_or_unit_evidence_unavailable',raw=cap)
        amount,close,shares=(number(cap.get(k)) for k in ('raw_market_cap','raw_close','raw_listed_shares'))
        with localcontext() as context:
            context.prec=100
            valid=(None not in (amount,close,shares) and min(amount,close,shares)>0
                   and shares==shares.to_integral_value() and amount==close*shares)
        if not valid:return dict(result,reason='market_cap_close_shares_mismatch_or_missing',raw=cap)
        if cap.get('stored_market_cap_won') is not None and number(cap['stored_market_cap_won'])!=amount:
            return dict(result,reason='stored_normalization_conflict',raw=cap)
        return dict(result,status='prior_close_unit_corroborated',won=str(amount),basis_date=cap['basis_date'],
                    eligibility_status='above_minimum_reference' if amount>Decimal(minimum_won) else 'below_or_equal_minimum_reference',
                    available_at=record['available_at'],collected_at=cap['collected_at'],raw=cap,
                    source_field='MKTCAP',multiplier_to_won='1',
                    unit_basis='official_KRW_stock_price_and_capital_formula_plus_exact_saved_row_identity',
                    display_only_unit_corroboration=True,unit_contract=contract,
                    capital_band=capital_band(str(amount),verified=False))
    except (KeyError,TypeError,ValueError):return dict(result,reason='incomplete_reference',raw=cap)


def kiwoom_capitals(path, codes, *, as_of):
    """Reuse the existing verified ka10001 cache contract, no raw-value guesses."""
    result={};info=dict(status='unavailable',cache_rows=0)
    if not path:return result,info
    try:
        with read_db(path) as db:
            info=dict(status='inspected',cache_rows=db.execute('SELECT count(*) FROM market_cap_cache').fetchone()[0])
            reader=Queries(db)
            for code in codes:
                value=reader.capital(code,environment='real',at=as_of)
                if value['state']=='hit':
                    r=value['record']
                    result[code]=dict(status='verified_ka10001_cache',won=r['normalized_won'],basis_date=r['trade_date'],
                        available_at=r['registered_at'],collected_at=r['response_received_at'],raw=r,
                        source_field='mac',multiplier_to_won='100000000',intraday_cap_verified=True,
                        capital_band=capital_band(r['normalized_won'],verified=True))
    except (OSError,ValueError,KeyError,TypeError,sqlite3.Error) as error:
        info=dict(status='unverified',error_type=type(error).__name__)
    return result,info


def format_capital(won):
    value=number(won)
    if value is None or value<0:return '시총 미확인'
    return f'시총 {value/Decimal(1000000000000):,.2f}조' if value>=Decimal(1000000000000) else f'시총 {value/Decimal(100000000):,.0f}억'


PRACTICAL_NAME=re.compile(r'^(?:KODEX|TIGER|RISE|SOL|PLUS|ACE|HANARO|KOSEF|KIWOOM|KBSTAR|ARIRANG|KINDEX)\s|(?:^|\s)(?:ETF|ETN)(?:\s|$)|(?:스팩|리츠)$',re.IGNORECASE)


def practical_product(stock, decision):
    """User-authorized conservative candidate filter, not an official type claim."""
    if decision.get('verified'):
        return decision
    match=PRACTICAL_NAME.search(stock.get('name') or '')
    if not match:return decision
    return dict(decision,excluded=True,status='conservative_name_excluded',
                exclusion_basis=dict(policy='c15-12-practical-products-v1',name=stock['name'],
                                     matched=match.group(),pattern=PRACTICAL_NAME.pattern))


def recent_capital(cap, as_of):
    # Reuse existing evidence; no new collection or receipt-time machinery.
    if cap.get('won') is None:return cap
    from datetime import date, timedelta
    day=instant(as_of).date();basis=date.fromisoformat(cap['basis_date']);count=0
    cursor=basis
    while cursor<day and count<=2:
        cursor+=timedelta(days=1)
        if cursor.weekday()<5:count+=1
    if not 1<=count<=2 or basis>=day:
        return dict(cap,won=None,status='stale_reference',eligibility_status='pending')
    return dict(cap,reference_weekdays_old=count)
