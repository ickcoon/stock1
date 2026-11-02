"""Independent, opt-in Telegram briefing. No operational DB or scheduler access."""
import argparse
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
import hashlib
import json
import os
from pathlib import Path
import re
import socket
import ssl

import requests
from requests.adapters import HTTPAdapter
from urllib3 import exceptions as ue

from . import am_momentum as am
from . import media_policy

PROJECT = Path(__file__).resolve().parent.parent
REPORTS = PROJECT / 'reports'
STATE = PROJECT / 'notification_state'
OBSERVATION_SCHEMA = 'arvis-observation-briefing-v1'
MAX_JSON = 2 * 1024 * 1024
MAX_HTML = 4 * 1024 * 1024  # Deliberately below Telegram's document limit.
TOKEN = re.compile(r'[0-9]{6,15}:[A-Za-z0-9_-]{20,100}', re.ASCII)
CHAT = re.compile(r'[1-9][0-9]{0,19}', re.ASCII)
SECRET_TEXT = re.compile(r'(?i)\b(?:api[ _-]?key|app[ _-]?key|secret|password|authorization|cookie|account|credential|token)\b|계좌|인증정보')
TOP_KEYS = set('schema_version selection_type source_kind trade_date selection_cutoff_at generated_at environment market notice candidate_confirmed d0_storage_allowed policy completeness diagnostics rows evaluation_reference'.split())
ROW_KEYS = set('stock_code market environment selection_type candidate_status candidate_confirmed rank_history top5_appearances top20_appearances top20_observed_ratio rank_change sectors sector_companions evidence reasons missing_evidence d0_storage_allowed'.split())
POLICY_KEYS = set('version minimum_top5_hits latest_observed_rank_max market_cap_min_won closing_turnover_requirement_applied liquidity validated_optimal_strategy'.split())
REASONS = {'top5_repeated': 'TOP5 반복', 'top20_persistent': 'TOP20 지속',
           'observed_price_and_turnover_increase': '관측 가격·거래대금 증가',
           'same_sector_coappearance': '동일 섹터 동반 등장', 'attention_threshold_not_met': '관심도 기준 미충족'}


class BriefingError(ValueError):
    """Only static reason codes may leave this module on failure."""


TELEGRAM_ERRORS = frozenset((
    'telegram_http_failed', 'telegram_api_failed', 'telegram_response_invalid',
    'telegram_recipient_mismatch', 'telegram_transport_failed', 'telegram_dns_failed',
    'telegram_connection_failed', 'telegram_tls_failed', 'telegram_certificate_failed',
    'telegram_connect_timeout', 'telegram_read_timeout', 'telegram_timeout',
))


class TelegramRequestError(BriefingError):
    """Public enums/numbers only; no URL, recipient, description or request data."""
    def __init__(self, reason, method, stage, *, http_status=None, api_error_code=None,
                 exception_type='ResponseValidationError'):
        super().__init__(reason)
        self.diagnostics = dict(method=method, stage=stage, http_status=http_status,
                                api_error_code=api_error_code, exception_type=exception_type)


def telegram_transport_error(error, method, stage, http_status):
    pending, chain = [error], []
    while pending and len(chain) < 24:
        item = pending.pop()
        if not isinstance(item, BaseException) or any(item is seen for seen in chain):
            continue
        chain.append(item)
        pending.extend((item.__cause__, item.__context__, getattr(item, 'reason', None)))
        pending.extend(arg for arg in item.args if isinstance(arg, BaseException))
    categories = (
        (ssl.SSLCertVerificationError,'telegram_certificate_failed','SSLCertVerificationError'),
        (socket.gaierror,'telegram_dns_failed','gaierror'),
        (ue.NameResolutionError,'telegram_dns_failed','NameResolutionError'),
        (requests.exceptions.SSLError,'telegram_tls_failed','SSLError'),
        (ssl.SSLError,'telegram_tls_failed','SSLError'),
        (ue.SSLError,'telegram_tls_failed','SSLError'),
        (requests.exceptions.ConnectTimeout,'telegram_connect_timeout','ConnectTimeout'),
        (requests.exceptions.ReadTimeout,'telegram_read_timeout','ReadTimeout'),
        (ue.ReadTimeoutError,'telegram_read_timeout','ReadTimeoutError'),
        (ue.NewConnectionError,'telegram_connection_failed','NewConnectionError'),
        (ue.ConnectTimeoutError,'telegram_connect_timeout','ConnectTimeoutError'),
        (TimeoutError,'telegram_timeout','TimeoutError'),
        (requests.exceptions.Timeout,'telegram_timeout','Timeout'),
        (requests.exceptions.ConnectionError,'telegram_connection_failed','ConnectionError'),
        (ConnectionError,'telegram_connection_failed','ConnectionError'),
        (requests.exceptions.RequestException,'telegram_transport_failed','RequestException'),
        (RuntimeError,'telegram_transport_failed','RuntimeError'),
    )
    for kind, reason, label in categories:
        if any(isinstance(item, kind) for item in chain):
            return TelegramRequestError(reason,method,stage,http_status=http_status,exception_type=label)
    return TelegramRequestError('telegram_transport_failed',method,stage,http_status=http_status,
                                exception_type='UnknownException')


def require(condition, code='invalid_report'):
    if not condition:
        raise BriefingError(code)


def keys(value, expected):
    require(isinstance(value, dict) and set(value) == set(expected))


def scan(value, secrets=()):
    am.privacy(value)
    if isinstance(value, dict):
        for item in value.values():
            scan(item, secrets)
    elif isinstance(value, list):
        for item in value:
            scan(item, secrets)
    elif isinstance(value, str):
        require(not TOKEN.search(value) and not SECRET_TEXT.search(value), 'sensitive_content_rejected')
        require(not any(secret and secret in value for secret in secrets), 'sensitive_content_rejected')


def digest(value):
    return hashlib.sha256(am.canonical(value).encode('utf-8')).hexdigest()


def validate(report, secrets=()):
    scan(report, secrets)
    if isinstance(report, dict) and report.get('schema_version') == OBSERVATION_SCHEMA:
        validate_observation(report)
        return report
    if isinstance(report, dict) and report.get('schema_version') == 'am-sector-briefing-v1':
        from . import am_briefing
        am_briefing.validate(report)
        return report
    window = am.timing.from_document(report)
    keys(report, TOP_KEYS | ({'observation_policy','decision_deadline_at'} if window else set()))
    require(report['schema_version'] == am.SCHEMA and report['selection_type'] == 'AM_MOMENTUM')
    require(report['source_kind'] in ('fixture', 'readonly_adapter'), 'unsupported_source_kind')
    require(report['environment'] in ('mock', 'real') and report['market'] in ('KRX', 'NXT', 'SOR'))
    require(report['environment'] != 'mock' or report['market'] == 'KRX')
    require(report['candidate_confirmed'] is False and report['d0_storage_allowed'] is False, 'unconfirmed_report_required')
    cutoff = am.instant(report['selection_cutoff_at'])
    deadline = window.deadline(cutoff) if window else cutoff
    if window:
        require(report['decision_deadline_at']==deadline.isoformat())
    require(date.fromisoformat(report['trade_date']) == cutoff.date())
    require(am.instant(report['generated_at']) >= deadline)
    require('09:20' <= cutoff.strftime('%H:%M') <= '09:50')
    require(report['notice'] == (am.NOTICE if report['source_kind'] == 'fixture' else am.ADAPTER_NOTICE))
    keys(report['policy'], POLICY_KEYS)
    require(report['policy'] == dict(version='exploratory-v1', minimum_top5_hits=2, latest_observed_rank_max=5,
                                   market_cap_min_won='300000000000', closing_turnover_requirement_applied=False,
                                   liquidity='positive_intraday_value_with_explicit_verified_basis', validated_optimal_strategy=False))
    coverage = report['completeness']
    keys(coverage, ('expected_slots','accepted_slots','missing_slots','top20_complete'))
    expected = [s for s in am.SLOTS if am.instant(report['trade_date']+'T'+s+':00+09:00') <= cutoff]
    accepted = coverage['accepted_slots']
    require(coverage['expected_slots'] == expected and isinstance(accepted, list))
    require(accepted == sorted(set(accepted)) and all(s in expected for s in accepted))
    require(coverage['missing_slots'] == [s for s in expected if s not in accepted])
    require(type(coverage['top20_complete']) is bool)
    for diagnostic in report['diagnostics']:
        keys(diagnostic, ('input_index','reason'))
        require(type(diagnostic['input_index']) is int and diagnostic['input_index'] >= 0)
        require(diagnostic['reason'] in ('ranking_time_missing_or_outside_cutoff','invalid_or_late_slot','invalid_ranking_contract'))
    require(isinstance(report['rows'], list) and len(report['rows']) <= 80)
    codes = []
    def times(item):
        observed, received = am.instant(item['observed_at']), am.instant(item['received_at'])
        require(am.timing.available(window,observed,received,cutoff))
    for row in report['rows']:
        keys(row, ROW_KEYS)
        require(isinstance(row['stock_code'], str) and am.CODE.fullmatch(row['stock_code']))
        codes.append(row['stock_code'])
        require(all(row[k] == report[k] for k in ('market','environment','selection_type')))
        require(row['candidate_confirmed'] is False and row['d0_storage_allowed'] is False)
        require(row['candidate_status'] in ('attention_leader','momentum_candidate','insufficient_evidence'))
        require(isinstance(row['rank_history'], list) and [h['slot'] for h in row['rank_history']] == accepted)
        ranks = []
        for h in row['rank_history']:
            keys(h, ('slot','observed_at','received_at','rank','top20_complete')); times(h)
            anchor = am.instant(report['trade_date']+'T'+h['slot']+':00+09:00')
            require(am.timing.near(window,h['observed_at'],h['received_at'],anchor) if window
                    else am.instant(h['observed_at']).strftime('%H:%M') == h['slot'])
            require(type(h['top20_complete']) is bool)
            require(h['rank'] is None or type(h['rank']) is int and 1 <= h['rank'] <= 20)
            if h['rank'] is not None: ranks.append(h['rank'])
        require(row['top5_appearances'] == sum(r <= 5 for r in ranks) and row['top20_appearances'] == len(ranks))
        require(row['rank_change'] == (ranks[0]-ranks[-1] if len(ranks) >= 2 else None))
        require(row['top20_observed_ratio'] == str(am.Decimal(len(ranks))/am.Decimal(len(accepted))) if accepted else not ranks)
        if row['candidate_status'] != 'insufficient_evidence':
            require(sum(r <= 5 for r in ranks) >= 2 and row['rank_history'][-1]['rank'] is not None and row['rank_history'][-1]['rank'] <= 5)
        for tag in row['sectors']:
            keys(tag, ('name','basis','observed_at','received_at')); times(tag)
            am.label(tag['name']); am.label(tag['basis'])
        require(len({t['name'] for t in row['sectors']}) == len(row['sectors']))
        require(len({p['sector'] for p in row['sector_companions']}) == len(row['sector_companions']))
        for peer in row['sector_companions']:
            keys(peer, ('sector','stock_codes','count')); am.label(peer['sector'])
            require(peer['count'] == len(set(peer['stock_codes'])) == len(peer['stock_codes']))
            require(row['stock_code'] not in peer['stock_codes'])
            require(peer['sector'] in {t['name'] for t in row['sectors']})
        keys(row['evidence'], ('price','turnover','market_cap','liquidity'))
        for kind, metric in row['evidence'].items():
            keys(metric, ('available','observations','change','change_per_second'))
            require(type(metric['available']) is bool and metric['available'] == bool(metric['observations']))
            previous = None
            for observation in metric['observations']:
                keys(observation, ('value','unit','verified','basis','observed_at','received_at')); times(observation)
                amount = am.number(observation['value'])
                require(amount is not None and amount >= 0 and observation['unit'] == 'KRW' and observation['verified'] is True)
                require(previous is None or previous < am.instant(observation['observed_at']))
                previous = am.instant(observation['observed_at']); am.label(observation['basis'])
            observations = metric['observations']
            change = str(am.Decimal(observations[-1]['value'])-am.Decimal(observations[0]['value'])) if len(observations)>=2 else None
            rate = str(am.Decimal(change)/am.Decimal(str((am.instant(observations[-1]['observed_at'])-am.instant(observations[0]['observed_at'])).total_seconds()))) if change is not None else None
            require(metric['change'] == change and metric['change_per_second'] == rate)
        require(all(reason in REASONS for reason in row['reasons']))
        require(all(isinstance(m, str) and re.fullmatch(r'[a-z0-9_]{1,80}', m) for m in row['missing_evidence']))
    require(len(set(codes)) == len(codes))
    for row in report['rows']:
        require(all(set(p['stock_codes']) <= set(codes) for p in row['sector_companions']))
    actual_full = accepted == expected and bool(accepted) and all(
        len([r for r in report['rows'] if r['rank_history'][i]['rank'] is not None]) == 20 and
        len({r['rank_history'][i]['rank'] for r in report['rows'] if r['rank_history'][i]['rank'] is not None}) == 20 and
        all(r['rank_history'][i]['top20_complete'] for r in report['rows']) for i in range(len(accepted)))
    require(coverage['top20_complete'] == actual_full)
    unsigned = {k:v for k,v in report.items() if k != 'evaluation_reference'}
    require(report['evaluation_reference'] == digest(unsigned), 'report_fingerprint_mismatch')
    return report


def validate_observation(report):
    """Incomplete live observations only; never promotes rows to C12 leaders."""
    from . import am_briefing
    pm=report.get('selection_type')=='PM_CLOSING_REVIEW'
    keys(report, set('schema_version selection_type source_kind trade_date selection_cutoff_at generated_at environment market candidate_confirmed d0_storage_allowed analysis observation step6d charts evaluation_reference'.split()) | ({'pm_candidates','pm_reviewable'} if pm else set()))
    require(report['selection_type'] in ('OBSERVATION_REVIEW','PM_CLOSING_REVIEW') and report['source_kind'] == 'readonly_adapter')
    require(report['environment'] == 'real' and report['market'] == 'KRX')
    require(report['candidate_confirmed'] is False and report['d0_storage_allowed'] is False)
    at = am.instant(report['selection_cutoff_at'])
    require(at.date().isoformat() == report['trade_date'] and am.instant(report['generated_at']) >= at)
    am_briefing.validate(report['analysis'])
    analysis = report['analysis']
    require(analysis['source_kind'] == 'readonly_adapter' and analysis['environment'] == 'real'
            and analysis['market'] == 'KRX' and analysis['trade_date'] == report['trade_date'])
    require(pm or not analysis['sectors'], 'observation_report_requires_held_selection')
    require(am.instant(analysis['selection_cutoff_at']) <= at)
    data = report['observation']
    keys(data, set('ranking_at turnover_received_at turnover_rows pagination_complete stocks'.split()))
    require(data['ranking_at'] in (None,report['selection_cutoff_at']))
    if data['turnover_received_at'] is not None:
        received = am.instant(data['turnover_received_at'])
        require(received.date() == at.date() and received <= am.instant(report['generated_at']))
    else:require(data['turnover_rows']==0)
    require(type(data['turnover_rows']) is int and 0 <= data['turnover_rows'] <= 100)
    require(type(data['pagination_complete']) is bool)
    rows = data['stocks']
    require(isinstance(rows, list) and 0 <= len(rows) <= 20)
    require((data['ranking_at'] is not None) or not rows)
    codes, ranks = set(), set()
    for row in rows:
        keys(row, set('stock_code stock_name rank change_percent turnover_million_krw'.split()))
        require(isinstance(row['stock_code'], str) and re.fullmatch(r'[0-9A-Z]{6}', row['stock_code']))
        require(type(row['rank']) is int and 1 <= row['rank'] <= 20)
        require(row['stock_code'] not in codes and row['rank'] not in ranks)
        codes.add(row['stock_code']); ranks.add(row['rank'])
        am.label(row['stock_name'])
        for key in ('change_percent', 'turnover_million_krw'):
            value = row[key]
            require(value is None or (isinstance(value, str) and am.number(value) is not None))
        require(row['turnover_million_krw'] is None or am.number(row['turnover_million_krw']) >= 0)
    if pm:
        candidates=report['pm_candidates']
        require(isinstance(candidates,list) and len(candidates)<=5)
        reviewed=report['pm_reviewable']
        require(isinstance(reviewed,list) and len(reviewed)<=len(rows) and candidates==reviewed[:5])
        selected=set()
        for item in reviewed:
            keys(item,{'stock_code','qualification'})
            require(item['stock_code'] in codes and item['stock_code'] not in selected)
            selected.add(item['stock_code'])
            q=item['qualification']
            require(q.get('final_review_allowed') is True and q.get('review_status')=='검토 가능'
                    and q.get('candidate_confirmed') is False and bool(q.get('checks'))
                    and all(v is True for v in q['checks'].values()),'pm_evidence_incomplete')
        require(report['step6d']==dict(verified=len(reviewed),held=len(rows)-len(reviewed)))
    else:
        require(report['step6d'] == dict(verified=0, held=len(rows)), 'observation_eligibility_must_remain_held')
    require(report['charts'] == dict(available=0, unavailable=len(rows)), 'observation_chart_data_unavailable')
    require(report['evaluation_reference'] == digest({k:v for k,v in report.items() if k != 'evaluation_reference'}), 'report_fingerprint_mismatch')


def observation_summary(report):
    validate(report)
    data = report['observation']
    pm=report['selection_type']=='PM_CLOSING_REVIEW'
    lines = ['ARVIS 종가매매 후보 검토 · provisional' if pm else 'ARVIS 실전 관측 연결 테스트 · provisional',
             report['trade_date'] + ' | 0198 수집 ' + (am.instant(data['ranking_at']).strftime('%H:%M:%S')+' KST' if data['ranking_at'] else '미확보'),
             '0186 수신 ' + (am.instant(data['turnover_received_at']).strftime('%H:%M:%S')+' KST' if data['turnover_received_at'] else '미확인') + ' | real / KRX 요청(0186)',
             f"0198 {len(data['stocks'])}종목 / 0186 {data['turnover_rows']}종목 · " + ('자료 없음' if not data['turnover_rows'] else '페이지 종료 확인' if data['pagination_complete'] else '첫 페이지 partial'),
             '주도섹터 Top3: 미확인 · 섹터별 핵심 종목: 미확인',
             ('오후 핵심 후보는 아래 Step 6-D 검토 결과로 구분' if pm else 'C12 후보 Top5: 보류 · 반복 관측/시총/섹터/검증 근거 부족'),
             '0198 조회순위 상위 관측 (C12 선정 결과 아님):']
    for row in sorted(data['stocks'], key=lambda r:r['rank'])[:5]:
        change = row['change_percent'] + '%' if row['change_percent'] is not None else '미확인'
        amount = row['turnover_million_krw'] + ' 백만원(명세 기준)' if row['turnover_million_krw'] is not None else '미확인'
        lines.append(f"{row['rank']}. {row['stock_name']} {row['stock_code']} | 등락률 {change} | 거래대금 {amount}")
    if pm:
        selected=report['pm_candidates']
        label='15:00 핵심 검토 후보' if report['selection_cutoff_at'][11:16]>='15:00' else '14:30 사전 검토'
        lines.append(label+': '+(', '.join(r['stock_code'] for r in selected) if selected else '근거 부족으로 보류'))
    lines += [f"Step 6-D: {report['step6d']['verified']}종목 검토 가능 / {report['step6d']['held']}종목 보류 · 매매 확정 없음",
              'C14: 저장 일봉/주봉 미확보 · 차트 미확보',
              '위험: HTS 단위 대조/누적 범위/시장 시각/0198 시장 범위 미검증. 미확인 값을 추정하지 않음.',
              '오전 분석 기준 ' + am.instant(report['analysis']['selection_cutoff_at']).strftime('%H:%M') + ' 이후 관측을 과거 슬롯에 소급하지 않음.',
              '매매 확정·주문 신호 아님 · '+('원본 관측 보존' if pm else '정기 자동 보고 미활성')]
    text = '\n'.join(lines)
    require(len(text.encode('utf-16-le'))//2 <= 4096, 'message_too_long')
    return text


def summary(report, *, attached=False):
    if report.get('schema_version') == OBSERVATION_SCHEMA:
        return observation_summary(report)
    if report.get('schema_version') == 'am-sector-briefing-v1':
        from . import am_briefing
        if report.get('input_document', {}).get('briefing_format') == 'c12ac-final-v1':
            return am_briefing.format_mobile(report)
        return am_briefing.format_telegram(report)
    validate(report)
    lines = ['아르비스 오전 브리핑', report['trade_date']+' | 기준 '+am.instant(report['selection_cutoff_at']).strftime('%H:%M:%S')+' KST',
             '환경: '+report['environment']+' / '+report['market']+' / '+('합성 fixture' if report['source_kind'] == 'fixture' else '읽기 전용 수집 자료'), '잠정 분석 · candidate_confirmed=false · 매매 확정 아님']
    full = report['completeness']['top20_complete'] and report['completeness']['accepted_slots'] == list(am.SLOTS)
    rows = [r for r in report['rows'] if r['candidate_status'] != 'insufficient_evidence'] if full else []
    rows.sort(key=lambda r: (-r['top5_appearances'], -r['top20_appearances'], r['rank_history'][-1]['rank'], r['stock_code']))
    if not rows:
        lines += ['자료 부족: 오전 TOP20 관측 또는 관심도 기준 부족. TOP3를 생성하지 않았습니다.',
                  '동반 섹터: 판단 보류 / 가격·거래대금·시총 검증 근거를 상세 보고서에서 확인하세요.']
    else:
        sectors = Counter(name for r in rows for name in {p['sector'] for p in r['sector_companions'] if p['count'] > 0})
        leaders = [name+'('+str(count)+'종목)' for name,count in sorted(sectors.items(),key=lambda x:(-x[1],x[0])) if count >= 2][:3]
        lines += ['관심도 동반 섹터: '+(', '.join(leaders) if leaders else '확인 근거 부족')+' (시세 주도력 미확정)',
                  '관심도 상위 '+str(min(3,len(rows)))+'개:']
        for i,row in enumerate(rows[:3],1):
            seen = row['rank_history'][-1]['observed_at']
            missing = []
            for kind,name in [('price','가격'),('turnover','거래대금'),('market_cap','시총'),('liquidity','유동성')]:
                if not row['evidence'][kind]['available']: missing.append(name+' 미검증/누락')
            if row['missing_evidence'] and not missing: missing.append('추가 검증 부족 '+str(len(row['missing_evidence']))+'건')
            basis = ', '.join(REASONS[r] for r in row['reasons'][:3]) or '근거 부족'
            lines += [str(i)+'. '+row['stock_code']+' | TOP5 '+str(row['top5_appearances'])+'회 | 관측 '+am.instant(seen).strftime('%H:%M:%S')+' KST',
                      '  '+basis, '  '+('; '.join(missing) if missing else '합성 검증 근거 있음 — 실측 검증 아님')]
    lines.append('상세 HTML: '+('문서 첨부. 다운로드 후 브라우저로 열어 보세요.' if attached else '미첨부 (--html로 같은 분석 문서 지정)'))
    text = '\n'.join(lines)
    require(len(text.encode('utf-16-le'))//2 <= 4096, 'message_too_long')
    return text


def render_report(report):
    if report.get('schema_version') == OBSERVATION_SCHEMA:
        from html import escape
        return '<!doctype html><html lang="ko"><meta charset="utf-8"><title>ARVIS 실전 관측</title><pre>'+escape(observation_summary(report))+'</pre></html>'
    if report.get('schema_version') == 'am-sector-briefing-v1':
        from . import am_briefing
        return am_briefing.render(report)
    return am.render(report)


def delivery_schema(report):
    # Keep the pre-existing day/recipient reservation namespace across formats.
    # An old reserved, failed or sent briefing must still block a second send.
    return am.SCHEMA if report['schema_version'] == 'am-sector-briefing-v1' else report['schema_version']


def read_report(path, suffix, limit):
    root = REPORTS.resolve()
    require(root == PROJECT.resolve()/'reports', 'invalid_report_root')
    resolved = Path(path).resolve()
    require(resolved.is_relative_to(root) and resolved.suffix == suffix, 'report_path_rejected')
    with resolved.open('rb') as stream:
        raw = stream.read(limit+1)
    require(len(raw) <= limit, 'report_too_large')
    return raw.decode('utf-8')


def unique_object(pairs):
    result = {}
    for key,value in pairs:
        require(key not in result, 'duplicate_json_key')
        result[key] = value
    return result


def prepare(report_path, html_path=None, *, secrets=()):
    report = json.loads(read_report(report_path,'.json',MAX_JSON), object_pairs_hook=unique_object,
                        parse_constant=lambda _: require(False, 'nonfinite_json'))
    validate(report, secrets)
    html = None
    if html_path is not None:
        html = read_report(html_path,'.html',MAX_HTML)
        require(html == render_report(report), 'html_analysis_mismatch')
        scan(html, secrets)
    text = summary(report, attached=html is not None)
    scan(text, secrets)
    return report, text, html


@dataclass(frozen=True, repr=False)
class Configuration:
    token: str = field(repr=False)
    chat_id: str = field(repr=False)
    allowed: tuple = field(repr=False)

    @staticmethod
    def protected_path():
        base=os.environ.get('LOCALAPPDATA')
        require(bool(base),'configuration_required')
        return Path(base)/'Stock1/credentials/telegram-config.dpapi'

    def save_protected(self):
        """Reuse C13's current-user DPAPI; never write a plaintext fallback."""
        from .token_cache import WindowsDPAPI
        path=self.protected_path()
        require(path.parent.is_dir() and path.parent.resolve()==path.parent.absolute()
                and not path.is_symlink(),'secure_configuration_unreadable')
        require(not path.exists(),'existing_secure_configuration_preserved')
        # Check the same shape the environment loader accepts.
        require(TOKEN.fullmatch(self.token) and CHAT.fullmatch(self.chat_id)
                and self.chat_id in self.allowed and all(CHAT.fullmatch(v) for v in self.allowed))
        encrypted=WindowsDPAPI().protect(json.dumps(dict(version=1,kind='arvis-telegram-config-v1',
            token=self.token,chat_id=self.chat_id,allowed=list(self.allowed))).encode('utf-8'))
        with path.open('xb') as stream:stream.write(encrypted);stream.flush();os.fsync(stream.fileno())

    @classmethod
    def from_environment(cls):
        token = os.environ.get('ARVIS_TELEGRAM_BOT_TOKEN','').strip()
        chat = os.environ.get('ARVIS_TELEGRAM_CHAT_ID','').strip()
        allowed = tuple(v.strip() for v in os.environ.get('ARVIS_TELEGRAM_ALLOWED_CHAT_IDS','').split(',') if v.strip())
        if not any((token,chat,allowed)) and os.environ.get('LOCALAPPDATA'):
            path=cls.protected_path()
            if path.exists():
                try:
                    from .token_cache import WindowsDPAPI
                    require(not path.is_symlink() and path.parent.resolve()==path.parent.absolute()
                            and path.stat().st_size<=65536)
                    data=json.loads(WindowsDPAPI().unprotect(path.read_bytes()).decode('utf-8'),object_pairs_hook=unique_object)
                    keys(data,{'version','kind','token','chat_id','allowed'})
                    require(data['version']==1 and data['kind']=='arvis-telegram-config-v1')
                    token=data['token'].strip();chat=data['chat_id'].strip()
                    require(isinstance(data['allowed'],list))
                    allowed=tuple(v.strip() for v in data['allowed'])
                except Exception:
                    raise BriefingError('secure_configuration_unreadable') from None
        require(bool(TOKEN.fullmatch(token)), 'configuration_required')
        require(bool(CHAT.fullmatch(chat)) and bool(allowed) and all(CHAT.fullmatch(v) for v in allowed), 'private_chat_allowlist_required')
        require(chat in allowed, 'recipient_not_allowed')
        return cls(token,chat,allowed)


def api_call(session, config, method, *, text=None, html=None, audio=None):
    if method in ('sendVoice', 'sendAudio'):
        media_policy.require_legacy_media()
    require(method in ('sendMessage','sendDocument','sendVoice','sendAudio'))
    data = dict(chat_id=config.chat_id)
    kwargs = dict(timeout=(5,20), allow_redirects=False, verify=True)
    if method == 'sendMessage':
        data['text'] = text
        kwargs['json'] = data
    elif method == 'sendDocument':
        data['caption'] = '아르비스 상세 HTML — 다운로드 후 브라우저로 열기 · 잠정 분석'
        kwargs['data'] = data
        kwargs['files'] = {'document': ('arvis-am-review.html', html.encode('utf-8'), 'text/html')}
    else:
        from .voice_briefing import mp3_duration
        mp3_duration(audio)
        data['caption'] = '아르비스 합성 테스트 데이터 기반 AI 음성 · 잠정 분석 · 매매 확정 아님'
        kwargs['data'] = data
        kwargs['files'] = {('voice' if method == 'sendVoice' else 'audio'): ('arvis-am-briefing.mp3',audio,'audio/mpeg')}
    stage = 'upload_or_response' if 'files' in kwargs else 'request_response'
    http_status = None
    api_error_code = None
    try:
        response = session.post('https://api.telegram.org/bot'+config.token+'/'+method, **kwargs)
        http_status = response.status_code
        stage = 'response_json'
        try:
            content = getattr(response, 'content', None)
            if isinstance(content, bytes) and len(content)>16384:
                raise ValueError('bounded_response')
            body = response.json()
        except (ValueError, TypeError):
            raise TelegramRequestError('telegram_http_failed' if http_status != 200 else 'telegram_response_invalid',
                                       method,stage,http_status=http_status,exception_type='JSONDecodeError') from None
        if isinstance(body, dict):
            code = body.get('error_code')
            if type(code) is int and code in (400,401,403,404,409,413,429,500,502,503,504):
                api_error_code = code
        if http_status != 200:
            raise TelegramRequestError('telegram_http_failed',method,'http_response',
                                       http_status=http_status,api_error_code=api_error_code,exception_type='HTTPError')
        stage = 'api_response'
        require(isinstance(body,dict) and body.get('ok') is True, 'telegram_api_failed')
        result = body.get('result',{})
        require(isinstance(result,dict) and type(result.get('message_id')) is int, 'telegram_response_invalid')
        chat = result.get('chat',{})
        stage = 'recipient_validation'
        require(isinstance(chat,dict) and chat.get('type') == 'private' and str(chat.get('id')) == config.chat_id, 'telegram_recipient_mismatch')
    except TelegramRequestError:
        raise
    except BriefingError as error:
        reason = str(error) if str(error) in TELEGRAM_ERRORS else 'telegram_response_invalid'
        raise TelegramRequestError(reason,method,stage,http_status=http_status,
                                   api_error_code=api_error_code) from None
    except Exception as error:
        raise telegram_transport_error(error,method,stage,http_status) from None


def reservation_root(shared=None):
    if shared is None:
        root = STATE.resolve()
        require(root == PROJECT.resolve()/'notification_state', 'invalid_state_root')
        return root
    supplied = Path(shared)
    require(supplied.is_absolute() and not str(supplied).startswith('\\\\'), 'invalid_state_root')
    require(supplied.name == 'notification_state' and supplied.is_dir(), 'invalid_state_root')
    require(not any(p.is_symlink() for p in (supplied,*supplied.parents)), 'invalid_state_root')
    return supplied.resolve()


def deliver(report, text, html, config, *, session_factory=None, voice=None, voice_method='sendVoice', voice_only=False, allow_readonly_report=False, windows_trust_store=False, state_root=None, decision_revision=None):
    """Explicit-send only entry point. Reservation survives all failures; no retries."""
    if voice is not None or voice_only:
        media_policy.require_legacy_media()
    require(type(allow_readonly_report) is bool, 'invalid_readonly_delivery_option')
    require(type(windows_trust_store) is bool, 'invalid_windows_trust_option')
    require(report.get('source_kind') != 'readonly_adapter' or allow_readonly_report, 'readonly_report_not_deliverable')
    require(TOKEN.fullmatch(config.token) and CHAT.fullmatch(config.chat_id) and config.chat_id in config.allowed, 'recipient_not_allowed')
    require(type(voice_only) is bool and (not voice_only or (voice is not None and html is None)), 'invalid_voice_only_request')
    validate(report, (config.token,config.chat_id))
    require(text == summary(report,attached=html is not None), 'message_analysis_mismatch')
    require(html is None or html == render_report(report), 'html_analysis_mismatch')
    scan(text,(config.token,config.chat_id))
    if html is not None: scan(html,(config.token,config.chat_id))
    voice_status='not_requested'
    require(voice_method in ('sendVoice','sendAudio'),'invalid_voice_method')
    if voice is not None:
        from .voice_briefing import validate_audio, scan_audio
        require(isinstance(voice,tuple) and len(voice)==2,'invalid_voice_bundle')
        validate_audio(report,*voice)
        scan_audio(voice[1].data,(config.token,config.chat_id))
        if voice[1].is_test:
            require(not voice_only, 'test_audio_not_deliverable')
            voice=None;voice_status='test_audio_text_only'
        else:
            voice_status='ready'
    root = reservation_root(state_root)
    root.mkdir(exist_ok=True)
    scope = [report['trade_date'], report['selection_type'], delivery_schema(report), report['environment'],
             report['market'], hashlib.sha256(config.chat_id.encode()).hexdigest()]
    decision_signature=None
    revision_scope=scope
    if decision_revision is not None:
        require(type(decision_revision) is int and 1<=decision_revision<=3 and not voice_only and voice is None)
        require(report['schema_version']=='am-sector-briefing-v1' and report['source_kind']=='readonly_adapter')
        from .c12_chart_bridge import published_top
        selected=published_top(report)
        decision_signature=digest(dict(codes=sorted(r['stock_code'] for r in selected),
            sector=report['sectors'][0]['name'] if report['sectors'] else None))
        if decision_revision>1:
            previous_scope=scope if decision_revision==2 else scope+['am-revision',decision_revision-1]
            previous_path=root/(digest(previous_scope)+'.json')
            require(previous_path.is_file() and not previous_path.is_symlink(),'previous_delivery_unconfirmed')
            with previous_path.open(encoding='utf-8') as stream:previous=json.loads(stream.read(4097))
            require(previous.get('status')=='sent' and previous.get('decision_revision')==decision_revision-1,
                    'previous_delivery_unconfirmed')
            require(previous.get('decision_signature') and previous['decision_signature']!=decision_signature,
                    'important_selection_change_required')
            revision_scope=scope+['am-revision',decision_revision]
    if voice_only:
        legacy = root/(digest(scope)+'.json')
        require(not legacy.is_symlink(), 'invalid_state_file')
        if legacy.exists():
            with legacy.open('r', encoding='utf-8') as stream:
                raw = stream.read(4097)
            require(len(raw) <= 4096, 'invalid_state_file')
            previous = json.loads(raw, object_pairs_hook=unique_object)
            require(isinstance(previous, dict), 'invalid_state_file')
            # A completed text-only send may be followed by its separately approved voice.
            # Never duplicate a prior bundled voice or an ambiguous/in-flight delivery.
            if previous.get('status') != 'sent' or previous.get('voice_sent', False) is not False:
                return dict(status='duplicate_blocked', candidate_confirmed=False)
    # An independently approved voice-only delivery has its own one-shot reservation.
    # Preserve the existing text/bundle key, including its historical reservations.
    key = digest(scope + ['voice-only-v1'] if voice_only else revision_scope)
    path = root/(key+'.json')
    require(not path.is_symlink(), 'invalid_state_file')
    state = dict(version='telegram-delivery-v1', status='reserved', message_sent=False, document_sent=False,voice_sent=False)
    if decision_revision is not None:
        state.update(decision_revision=decision_revision,decision_signature=decision_signature)
    try:
        stream = path.open('x+',encoding='utf-8')
    except FileExistsError:
        return dict(status='duplicate_blocked',candidate_confirmed=False)
    # Keep the exclusive descriptor: updates never follow a newly substituted path.
    try:
        with stream:
            def save():
                stream.seek(0); json.dump(state,stream); stream.truncate(); stream.flush(); os.fsync(stream.fileno())
            save()  # Persist the reservation before any network call.
            def call(method, **kwargs):
                try:
                    api_call(session,config,method,**kwargs)
                except TelegramRequestError as error:
                    state.update(status='failed_or_unknown',reason=str(error),failure=error.diagnostics)
                    save()  # Preserve safe diagnostics on the exclusive descriptor.
                    raise
            with (session_factory or requests.Session)() as session:
                session.trust_env=False
                session.mount('https://',HTTPAdapter(max_retries=0))
                if windows_trust_store:
                    from .weekly_tls import configure_windows_trust
                    configure_windows_trust(session)
                # urllib3 debug records include request URLs, so suppress HTTP wire logs locally.
                import logging
                import http.client
                names=('urllib3.connectionpool','requests.packages.urllib3.connectionpool')
                loggers=[logging.getLogger(name) for name in names]
                previous=[logger.disabled for logger in loggers]; debug=http.client.HTTPConnection.debuglevel
                try:
                    for logger in loggers: logger.disabled=True
                    http.client.HTTPConnection.debuglevel=0
                    if not voice_only:
                        call('sendMessage',text=text)
                        state.update(status='message_sent',message_sent=True); save()
                    if html is not None:
                        call('sendDocument',html=html)
                        state['document_sent']=True
                    if voice is not None:
                        call(voice_method,audio=voice[1].data)
                        state['voice_sent']=True
                    state['status']='sent'; save()
                finally:
                    for logger,disabled in zip(loggers,previous): logger.disabled=disabled
                    http.client.HTTPConnection.debuglevel=debug
    except Exception as error:
        # Even an unknown delivery result must retain its reservation to avoid duplicate sends.
        result = dict(status='failed_or_unknown', reason=str(error) if isinstance(error,BriefingError) and str(error) in TELEGRAM_ERRORS else 'delivery_failed',
                      message_sent=state['message_sent'],document_sent=state['document_sent'],voice_sent=state['voice_sent'],candidate_confirmed=False)
        if isinstance(error,TelegramRequestError):
            result.update(error.diagnostics)
        return result
    return dict(status='sent',message_sent=state['message_sent'],document_sent=html is not None,voice_sent=state['voice_sent'],voice_status='sent' if state['voice_sent'] else voice_status,candidate_confirmed=False)


def preflight(report_path, html_path=None, *, state_root=None):
    """Read-only checks: no HTTP, reservation creation or credential output."""
    stage = 'configuration'
    safe_codes = {
        'configuration_required', 'private_chat_allowlist_required', 'recipient_not_allowed',
        'invalid_report', 'unsupported_source_kind', 'unconfirmed_report_required',
        'report_fingerprint_mismatch', 'sensitive_content_rejected', 'message_too_long',
        'invalid_report_root', 'report_path_rejected', 'report_too_large',
        'duplicate_json_key', 'nonfinite_json', 'html_analysis_mismatch',
        'invalid_state_root', 'invalid_state_file',
    }
    try:
        config = Configuration.from_environment()
        secrets = (config.token, config.chat_id)
        stage = 'json_report'
        report, text, _ = prepare(report_path, secrets=secrets)
        stage = 'html_attachment'
        if html_path is not None:
            prepare(report_path, html_path, secrets=secrets)
        stage = 'delivery_reservation'
        root = reservation_root(state_root)
        key = digest([report['trade_date'], report['selection_type'], delivery_schema(report),
                      report['environment'], report['market'], hashlib.sha256(config.chat_id.encode()).hexdigest()])
        path = root/(key+'.json')
        require(not path.is_symlink(), 'invalid_state_file')
        return dict(status='duplicate_blocked' if path.exists() else 'preflight_ok',
                    configuration_valid=True, json_validated=True,
                    html_validated=html_path is not None, environment=report['environment'],
                    candidate_confirmed=report['candidate_confirmed'], network_called=False)
    except (ValueError, TypeError, KeyError, OSError, AttributeError, ArithmeticError) as error:
        code = str(error) if isinstance(error, BriefingError) and str(error) in safe_codes else 'validation_failed'
        return dict(status='preflight_rejected', stage=stage, reason=code, network_called=False)


def main(argv=None):
    parser=argparse.ArgumentParser(description='Arvis AM briefing; default dry-run, no scheduler integration')
    parser.add_argument('--report',type=Path,required=True)
    parser.add_argument('--html',type=Path)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--send',action='store_true',help='Actually send only after user approval')
    mode.add_argument('--check',action='store_true',help='Validate configuration/report without network or state writes')
    parser.add_argument('--allow-readonly-report',action='store_true',help='Explicitly permit validated live readonly reports with --send')
    parser.add_argument('--windows-trust-store',action='store_true',help='Use the existing session-scoped Windows trust adapter')
    parser.add_argument('--state-root',type=Path,help='Existing local notification_state directory shared across deployments')
    args=parser.parse_args(argv)
    if args.allow_readonly_report and not args.send:
        parser.error('--allow-readonly-report requires --send')
    if args.windows_trust_store and not args.send:
        parser.error('--windows-trust-store requires --send')
    if args.check:
        result = preflight(args.report, args.html,state_root=args.state_root)
        print(json.dumps(result))
        return 0 if result['status']=='preflight_ok' else 1
    try:
        config=Configuration.from_environment() if args.send else None
        secrets=(config.token,config.chat_id) if config is not None else tuple(os.environ.get(k,'') for k in ('ARVIS_TELEGRAM_BOT_TOKEN','ARVIS_TELEGRAM_CHAT_ID'))
        report,text,html=prepare(args.report,args.html,secrets=secrets)
        if not args.send:
            print(json.dumps(dict(status='dry_run',message=text,html_validated=html is not None,candidate_confirmed=False),ensure_ascii=True))
            return 0
        result=deliver(report,text,html,config,allow_readonly_report=args.allow_readonly_report,windows_trust_store=args.windows_trust_store,state_root=args.state_root)
        print(json.dumps(result))
        return 1 if result['status']=='failed_or_unknown' else 0
    except (ValueError,TypeError,KeyError,OSError,AttributeError,ArithmeticError):
        print(json.dumps(dict(status='rejected',reason='configuration_or_report_validation_failed')))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
