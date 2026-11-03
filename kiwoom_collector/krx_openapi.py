"""C12-AN independent KRX preflight/probe and provisional offline contracts.

No Collector, OAuth, SQLite writer, scheduler or Telegram imports. Service page
verification is separate from full specification/runtime verification.
"""
import argparse
from dataclasses import dataclass,field
from datetime import datetime,timezone
from decimal import Decimal,InvalidOperation
import json
import os
from pathlib import Path
import re

from dotenv import dotenv_values
import requests

from .daily_chart import day,fingerprint
from .master_data import code as exact_code

SERVICES={
    'stk_isu_base_info':('KOSPI','master','PiwgMdTwmsenXhmqqxuj'),
    'ksq_isu_base_info':('KOSDAQ','master','CifLHplnUFMgpHIMMPXs'),
    'stk_bydd_trd':('KOSPI','daily','JvJFzlAENzZlPBDNGAWC'),
    'ksq_bydd_trd':('KOSDAQ','daily','hZjGpkllgCBCWqeTsYFj'),
    'krx_dd_trd':('KRX','index','SsgXTEspyJESKvyXZtCU'),
    'kospi_dd_trd':('KOSPI','index','EREKZauXnMmxyIlqzeDN'),
    'kosdaq_dd_trd':('KOSDAQ','index','nimebcamqFNIPNcRrHoO'),
}
INDEX_FIELDS=('BAS_DD','IDX_NM','CLSPRC_IDX','CMPPREVDD_IDX','FLUC_RT',
              'OPNPRC_IDX','HGPRC_IDX','LWPRC_IDX','ACC_TRDVOL','ACC_TRDVAL','MKTCAP')
BASE_FIELDS=('ISU_CD','ISU_SRT_CD','ISU_NM','ISU_ABBRV','ISU_ENG_NM','LIST_DD',
             'MKT_TP_NM','SECUGRP_NM','SECT_TP_NM','KIND_STKCERT_TP_NM','PARVAL','LIST_SHRS')
DAILY_FIELDS=('BAS_DD','ISU_CD','ISU_NM','MKT_NM','SECT_TP_NM','TDD_CLSPRC','CMPPREVDD_PRC',
              'FLUC_RT','TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','ACC_TRDVOL','ACC_TRDVAL','MKTCAP','LIST_SHRS')
BASE_URL='https://data-dbg.krx.co.kr/svc/apis/sto/'
PROJECT=Path(__file__).resolve().parents[3]
OUTPUT_ROOT=PROJECT/'diagnostics'/'krx-an'
NUM=re.compile(r'[+-]?(?:[0-9]+|[0-9]{1,3}(?:,[0-9]{3})+)(?:\.[0-9]+)?',re.ASCII)


class KrxError(ValueError):
    """Only fixed non-sensitive reason codes cross the transport boundary."""


@dataclass(frozen=True)
class Credential:
    value:str=field(repr=False)

    def __repr__(self):return 'Credential(<redacted>)'


def credential(*,env_file=None):
    if env_file is None:value=os.environ.get('KRX_AUTH_KEY','')
    else:
        path=Path(env_file).resolve()
        if path.name!='.env.krx.local' or not path.is_relative_to(PROJECT):
            raise KrxError('ignored_development_krx_env_file_required')
        if not path.is_file():return None
        value=dotenv_values(path,encoding='utf-8-sig',interpolate=False).get('KRX_AUTH_KEY','')
    if not isinstance(value,str) or not value.strip():return None
    value=value.strip()
    if any(c in value for c in ('\r','\n','${')):raise KrxError('invalid_krx_key')
    return Credential(value)


def preflight(*,env_file=None,review=None):
    key=credential(env_file=env_file)
    review=review or {}
    entries=review.get('services',{})
    services={name:dict(approval='locally_declared_approved' if entries.get(name,{}).get('approved') is True else 'unverified',
        contract='locally_reviewed' if entries.get(name,{}).get('contract_verified') is True else 'provisional_fixture_only',
        runtime_connected=False) for name in SERVICES}
    return dict(stage='C12-AN',key_present=key is not None,services=services,actual_api_calls=0,
        official_portal_account_status='not_independently_verified',credential_values_exported=False,
        source_kind='local_preflight',daily_database_rows=0,adr20_implemented=False,
        setup=None if key else 'Set KRX_AUTH_KEY locally or use --env-file pointing to a development .env.krx.local file. Never paste the key into chat.')


def decimal_value(value):
    if value in (None,'','-'):return None
    if not isinstance(value,str) or not NUM.fullmatch(value.strip()):raise KrxError('invalid_numeric_field')
    try:return Decimal(value.strip().replace(',',''))
    except InvalidOperation:raise KrxError('invalid_numeric_field') from None


def iso_day(value):
    if not isinstance(value,str):raise KrxError('invalid_date_field')
    if not re.fullmatch(r'(?:[0-9]{8}|[0-9]{4}/[0-9]{2}/[0-9]{2}|[0-9]{4}-[0-9]{2}-[0-9]{2})',value,re.ASCII):
        raise KrxError('invalid_date_field')
    for fmt in ('%Y%m%d','%Y/%m/%d','%Y-%m-%d'):
        try:return datetime.strptime(value,fmt).date().isoformat()
        except ValueError:pass
    raise KrxError('invalid_date_field')


def parse(body,*,service,bas_dd,source_kind,base_records=()):
    if service not in SERVICES:raise KrxError('unknown_service')
    if source_kind not in ('synthetic_fixture','krx_live'):raise KrxError('explicit_data_origin_required')
    requested=day(bas_dd).isoformat();market,kind,_=SERVICES[service]
    if not isinstance(body,dict) or not isinstance(body.get('OutBlock_1'),list):raise KrxError('outblock_contract_mismatch')
    if set(body)-{'OutBlock_1'}:raise KrxError('unexpected_top_level_fields')
    if len(body['OutBlock_1'])>10000:raise KrxError('response_row_limit')
    records=[];seen=set();seen_standard=set()
    by_short={r['stock_code']:r for r in base_records if r.get('stock_code')}
    by_standard={r['standard_code']:r for r in base_records if r.get('standard_code')}
    if len(by_short)!=len(base_records) or len(by_standard)!=len(base_records):
        raise KrxError('ambiguous_master_identity')
    for row in body['OutBlock_1']:
        fields=BASE_FIELDS if kind=='master' else INDEX_FIELDS if kind=='index' else DAILY_FIELDS
        if not isinstance(row,dict) or not set(fields)<=row.keys():raise KrxError('required_fields_missing')
        if any(not isinstance(row[f],str) for f in fields):raise KrxError('response_string_fields_required')
        raw=dict(row);issues=[]
        if kind=='index':
            if iso_day(row['BAS_DD'])!=requested:raise KrxError('index_basis_date_mismatch')
            if not row['IDX_NM'].strip():raise KrxError('index_name_required')
            numbers={f:decimal_value(row[f]) for f in INDEX_FIELDS[2:]}
            if any(v is not None and v<0 for f,v in numbers.items() if f not in ('CMPPREVDD_IDX','FLUC_RT')):
                raise KrxError('negative_unsigned_field')
            o,h,l,c=(numbers[f] for f in ('OPNPRC_IDX','HGPRC_IDX','LWPRC_IDX','CLSPRC_IDX'))
            if all(v is not None and v>0 for v in (o,h,l,c)):
                if not l<=min(o,c)<=max(o,c)<=h:raise KrxError('ohlc_inconsistent')
            else:issues.append('price_unformed_or_missing')
            result=dict(index_name_raw=row['IDX_NM'],index_series=market,basis_date=requested,
                source_kind=source_kind,raw=raw,issues=issues,
                numeric_diagnostics={f:str(v) if v is not None else None for f,v in numbers.items()},
                index_identity_verified=False,adr_breadth_source=False)
        elif kind=='master':
            if not row['ISU_NM'].strip():raise KrxError('stock_name_required')
            try:stock_code=exact_code(row['ISU_SRT_CD'])
            except ValueError:raise KrxError('invalid_short_code') from None
            if not re.fullmatch(r'KR[A-Z0-9]{10}',row['ISU_CD'],re.ASCII):raise KrxError('invalid_standard_code')
            if row['ISU_CD'] in seen_standard:raise KrxError('duplicate_standard_code')
            seen_standard.add(row['ISU_CD'])
            if row['MKT_TP_NM']!=market:raise KrxError('master_market_mismatch')
            listing=iso_day(row['LIST_DD'])
            if listing>requested:raise KrxError('listing_after_requested_date')
            shares=decimal_value(row['LIST_SHRS'])
            if shares is None or shares<0 or shares!=shares.to_integral_value():raise KrxError('invalid_listed_shares')
            result=dict(stock_code=stock_code,standard_code=row['ISU_CD'],official_name=row['ISU_NM'],
                abbreviated_name=row['ISU_ABBRV'],listing_date=listing,market=market,
                security_group_raw=row['SECUGRP_NM'],stock_type_raw=row['KIND_STKCERT_TP_NM'],
                basis_date=requested,date_basis='request_basDd_no_response_date_field',
                identity_verified=False,source_kind=source_kind,raw=raw)
        else:
            if not row['ISU_NM'].strip():raise KrxError('stock_name_required')
            if iso_day(row['BAS_DD'])!=requested:raise KrxError('daily_basis_date_mismatch')
            if row['MKT_NM']!=market:raise KrxError('daily_market_mismatch')
            supplied=row['ISU_CD'];matched=None;stock_code=None
            if re.fullmatch(r'[0-9A-Z]{6}',supplied,re.ASCII):
                stock_code=supplied;matched=by_short.get(supplied)
                namespace='six_character_daily_identifier'
            elif re.fullmatch(r'KR[A-Z0-9]{10}',supplied,re.ASCII):
                matched=by_standard.get(supplied);stock_code=matched['stock_code'] if matched else None
                namespace='standard_code_requires_master_join'
            else:raise KrxError('daily_identifier_namespace_unknown')
            identity_ok=bool(matched and matched['market']==market and matched['basis_date']==requested
                and row['ISU_NM'] in (matched['official_name'],matched['abbreviated_name']))
            if not identity_ok:issues.append('daily_identity_master_join_unverified')
            numbers={field:decimal_value(row[field]) for field in DAILY_FIELDS[5:]}
            unsigned=set(numbers)-{'CMPPREVDD_PRC','FLUC_RT'}
            if any(numbers[f] is not None and numbers[f]<0 for f in unsigned):raise KrxError('negative_unsigned_field')
            for f in ('ACC_TRDVOL','ACC_TRDVAL','MKTCAP','LIST_SHRS'):
                if numbers[f] is not None and numbers[f]!=numbers[f].to_integral_value():raise KrxError('noninteger_count_or_amount')
            o,h,l,c=(numbers[f] for f in ('TDD_OPNPRC','TDD_HGPRC','TDD_LWPRC','TDD_CLSPRC'))
            if all(v is not None and v>0 for v in (o,h,l,c)):
                if not l<=min(o,c)<=max(o,c)<=h:raise KrxError('ohlc_inconsistent')
            else:issues.append('price_unformed_or_missing')
            diff,rate=numbers['CMPPREVDD_PRC'],numbers['FLUC_RT']
            if diff is not None and rate is not None and (diff>0)!=(rate>0) and diff!=0 and rate!=0:
                issues.append('change_sign_conflict')
            result=dict(stock_code=stock_code,standard_code=matched['standard_code'] if matched else None,
                daily_identifier_raw=supplied,daily_identifier_namespace=namespace,
                name=row['ISU_NM'],market=market,basis_date=requested,raw=raw,
                numeric_diagnostics={f:str(v) if v is not None else None for f,v in numbers.items()},
                identity_master_join_matches=identity_ok,identity_verified=False,
                is_new_listing=(matched['listing_date']==requested) if matched else None,
                source_kind=source_kind,issues=issues)
        identity=row['IDX_NM'] if kind=='index' else result.get('stock_code') or row['ISU_CD']
        if identity in seen:raise KrxError('duplicate_stock_in_service_date')
        seen.add(identity);records.append(result)
    return dict(schema_version='c12-an-krx-provisional-v1',service=service,market=market,kind=kind,
        basis_date=requested,source_kind=source_kind,synthetic=source_kind=='synthetic_fixture',records=records,
        row_count=len(records),status='parsed_unverified' if records else 'empty_response_unclassified',
        empty_reason=None if records else 'holiday_or_unpublished_or_no_rows_not_distinguishable',
        full_official_spec_verified=False,units_runtime_verified=False,price_adjustment='unverified',
        update_time_verified=False,daily_database_rows_written=0,candidate_eligibility_promoted=False)


def redact(value,key):
    if isinstance(value,str):return value.replace(key.value,'[REDACTED]')
    if isinstance(value,list):return [redact(v,key) for v in value]
    if isinstance(value,dict):return {redact(k,key):('[REDACTED]' if str(k).lower() in
        ('auth_key','authorization','access_token','refresh_token','api_key') else redact(v,key)) for k,v in value.items()}
    return value


class ProbeClient:
    """One request per service/date per client, no automatic retries or redirects."""
    def __init__(self,key,review,*,session=None,ca_bundle=None,synthetic_transport=False):
        self._key=key;self.review=review;self._session=session;self._owned=session is None
        self.ca_bundle=ca_bundle;self.synthetic_transport=synthetic_transport
        self.actual_api_calls=0;self.transport_calls=0;self.seen=set()
        if synthetic_transport and session is None:raise KrxError('synthetic_transport_must_be_injected')

    def __repr__(self):return 'ProbeClient(<credentials redacted>)'

    def probe(self,service,bas_dd,*,base_records=()):
        if service not in SERVICES:raise KrxError('unknown_service')
        day(bas_dd)
        entry=self.review.get('services',{}).get(service,{})
        if not isinstance(self._key,Credential):raise KrxError('krx_key_missing')
        if self.review.get('synthetic') is True and not self.synthetic_transport:raise KrxError('fixture_approval_cannot_authorize_live')
        # AN-2 explicitly permits a read-only availability probe when portal approval
        # is unknown. That authorization must never relabel the portal as approved.
        availability_probe=entry.get('read_only_probe_authorized') is True
        if entry.get('approved') is not True and not availability_probe:raise KrxError('service_approval_unverified')
        if (entry.get('contract_verified') is not True or entry.get('method')!='GET'
                or not entry.get('official_spec_evidence') or not (entry.get('approval_evidence') or availability_probe)):
            raise KrxError('full_contract_and_approval_evidence_required')
        if (service,bas_dd) in self.seen:raise KrxError('already_attempted_service_date')
        self.seen.add((service,bas_dd))
        if self._session is None:
            self._session=requests.Session();self._session.trust_env=False
            self._session.mount('https://',requests.adapters.HTTPAdapter(max_retries=0))
        started=datetime.now(timezone.utc).isoformat()
        receipt=dict(service=service,basis_date=day(bas_dd).isoformat(),
            source_kind='synthetic_fixture' if self.synthetic_transport else 'krx_live',
            request_started_at=started,http_status=None,raw_response=None,normal_data_rows=0,
            observed_response_fields=[],krx_error_fields={},records=[],
            portal_approval='unverified',authentication='unverified',service_available='unverified',
            actual_api_calls=0,transport_calls=0,daily_database_rows_written=0)
        def finish(reason):
            finished=datetime.now(timezone.utc).isoformat()
            receipt.update(status=reason,attempt_finished_at=finished,
                response_received_at=finished if receipt['http_status'] is not None else None,
                actual_api_calls=0 if self.synthetic_transport else 1,transport_calls=1)
            return receipt
        try:
            self.transport_calls+=1
            if not self.synthetic_transport:self.actual_api_calls+=1
            endpoint=BASE_URL.replace('/sto/','/idx/') if SERVICES[service][1]=='index' else BASE_URL
            response=self._session.get(endpoint+service,params={'basDd':bas_dd},
                headers={'AUTH_KEY':self._key.value},timeout=(5,20),allow_redirects=False,
                verify=str(self.ca_bundle) if self.ca_bundle else True)
            receipt['http_status']=response.status_code
            if len(response.content)>10000000:return finish('response_size_limit')
            try:body=redact(response.json(),self._key)
            except (ValueError,TypeError):
                return finish('http_error_non_json' if response.status_code!=200 else 'invalid_json_response')
            receipt['raw_response']=body
            if isinstance(body,dict):
                receipt['krx_error_fields']={k:v for k,v in body.items() if k.lower() in
                    ('resp_code','resp_msg','error_code','error_message','code','message','return_code','return_msg')}
                rows=body.get('OutBlock_1')
                if isinstance(rows,list):receipt['observed_response_fields']=sorted({k for r in rows if isinstance(r,dict) for k in r})
            if response.status_code!=200:
                return finish({401:'authorization_denied',403:'authorization_denied',429:'quota_limited'}.get(response.status_code,'http_error'))
        except requests.exceptions.SSLError:return finish('tls_verification_failed')
        except requests.RequestException:return finish('transport_error')
        received=datetime.now(timezone.utc).isoformat()
        try:
            result=parse(body,service=service,bas_dd=bas_dd,
                source_kind='synthetic_fixture' if self.synthetic_transport else 'krx_live',base_records=base_records)
        except KrxError as error:
            receipt['validation_reason']=str(error)
            return finish('krx_error_response' if receipt['krx_error_fields'] else 'response_contract_mismatch')
        receipt.update(result)
        receipt.update(normal_data_rows=result['row_count'],authentication='response_data_received' if result['row_count'] else 'unverified',
            service_available='response_data_received' if result['row_count'] else 'unverified')
        result=receipt
        result.update(request_started_at=started,response_received_at=received,
            actual_api_calls=0 if self.synthetic_transport else 1,transport_calls=1,
            approval_status='fixture_only' if self.synthetic_transport else 'portal_approval_unverified',
            response_fingerprint=fingerprint(body))
        return result

    def close(self):
        if self._owned and self._session is not None:self._session.close()


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--fixture',type=Path);p.add_argument('--env-file',type=Path)
    p.add_argument('--review',type=Path);p.add_argument('--execute',action='store_true')
    p.add_argument('--bas-dd');p.add_argument('--output-dir',type=Path);p.add_argument('--ca-bundle',type=Path)
    a=p.parse_args(argv)
    if a.fixture:
        if a.execute:raise KrxError('fixture_cannot_execute_live')
        document=json.loads(a.fixture.read_text(encoding='utf8'))
        if document.get('synthetic') is not True:raise KrxError('explicit_synthetic_fixture_required')
        results=[];masters={}
        for service in SERVICES:
            result=parse(document['responses'][service],service=service,bas_dd=document['basDd'],
                         source_kind='synthetic_fixture',base_records=masters.get(SERVICES[service][0],()))
            if SERVICES[service][1]=='master':masters[SERVICES[service][0]]=result['records']
            results.append(result)
        print(json.dumps(dict(source_kind='synthetic_fixture',actual_api_calls=0,results=results),ensure_ascii=False,indent=2))
        return 0
    review=json.loads(a.review.read_text(encoding='utf8')) if a.review else {}
    check=preflight(env_file=a.env_file,review=review)
    if not a.execute:
        print(json.dumps(check,ensure_ascii=False,indent=2));return 0
    key=credential(env_file=a.env_file)
    if key is None:
        check.update(status='not_executed_key_missing',setup='Set KRX_AUTH_KEY locally, or use --env-file with a development .env.krx.local file. Never paste the key into chat.')
        print(json.dumps(check,ensure_ascii=False,indent=2));return 0
    if not a.bas_dd or not a.output_dir:raise KrxError('probe_date_and_development_output_required')
    out=Path(os.path.abspath(a.output_dir))
    if not out.resolve().is_relative_to(OUTPUT_ROOT.resolve()) or out!=out.resolve():raise KrxError('development_output_required')
    day(a.bas_dd)
    client=ProbeClient(key,review,ca_bundle=a.ca_bundle)
    results=[];masters={}
    out.mkdir(parents=True,exist_ok=True)
    try:
        for service in SERVICES:
            target=out/(service+'_'+a.bas_dd+'.json')
            if target.exists():
                result=json.loads(target.read_text(encoding='utf8'))
                if (result.get('service')!=service or result.get('source_kind')!='krx_live'
                        or result.get('basis_date')!=day(a.bas_dd).isoformat()):raise KrxError('cached_probe_scope_mismatch')
                results.append(dict(service=service,status='cached_no_recall'))
            else:
                # Reserve before transport; failures also leave a receipt, no silent re-call.
                with target.open('x',encoding='utf8') as f:
                    previous_calls=client.actual_api_calls
                    try:result=client.probe(service,a.bas_dd,base_records=masters.get(SERVICES[service][0],()))
                    except KrxError as error:
                        result=dict(service=service,basis_date=day(a.bas_dd).isoformat(),source_kind='krx_live',
                            status='blocked_or_failed',reason=str(error),actual_api_calls=client.actual_api_calls-previous_calls)
                    json.dump(result,f,ensure_ascii=False,indent=2)
                results.append(dict(service=service,status=result['status']))
            if SERVICES[service][1]=='master' and result.get('records'):masters[SERVICES[service][0]]=result['records']
        print(json.dumps(dict(results=results,actual_api_calls=client.actual_api_calls,daily_database_rows_written=0)))
    finally:client.close()
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except (KrxError,OSError,json.JSONDecodeError):
        # No raw exception, file contents, headers, response body or key in CLI failures.
        print(json.dumps(dict(status='failed',reason='krx_preflight_or_contract_failed')))
        raise SystemExit(2)
