"""Offline C12-AD replay. No scheduler, transport, credentials or delivery calls."""
import argparse
from copy import deepcopy
from datetime import date
import hashlib
import json
from pathlib import Path
import sqlite3

from . import am_briefing, am_momentum as am, am_input_adapter, session_review
from .am_candidate_adapter import join
from .am_time_policy import ObservationPolicy, from_document
from .morning_briefing import freeze

SLOTS = ('09:40', '09:45', '09:50')


def load(path):
    raw = Path(path).read_bytes()
    value = json.loads(raw.decode('utf-8-sig'))
    am.privacy(value)
    if value.get('schema_version') == am_briefing.SCHEMA:
        am_briefing.validate(value)
        documents = [value['input_document']]
    elif value.get('schema_version') == 'c12-replay-input-v1':
        documents = value['documents']
    else:
        documents = [value]
    return documents, hashlib.sha256(raw).hexdigest()


def prepare(document, day, slot):
    doc = deepcopy(document)
    if doc['trade_date'] != day:
        raise ValueError('replay_date_mismatch')
    cutoff = am.instant(day+'T'+slot+':00+09:00')
    doc['selection_cutoff_at'] = cutoff.isoformat()
    records = doc['rankings'] + [v for sec in doc['securities'].values()
                                 for values in sec.values() if isinstance(values,list) for v in values]
    late_receipts = sum(bool(r.get('observed_at') and r.get('received_at') and
                             am.instant(r['observed_at']) <= cutoff < am.instant(r['received_at']))
                        for r in records)
    before = len(doc['rankings']) + sum(len(v) for sec in doc['securities'].values()
                                      for v in sec.values() if isinstance(v, list))
    doc = freeze(doc)
    after = len(doc['rankings']) + sum(len(v) for sec in doc['securities'].values()
                                     for v in sec.values() if isinstance(v, list))
    # A master result evaluated at another instant is not point-in-time proof.
    filters = doc.get('candidate_filter')
    if filters and am.instant(filters['at']) != cutoff:
        del doc['candidate_filter']
    if not doc.get('candidate_filter'):
        doc = join(doc, dict(at=cutoff.isoformat(), synthetic=doc['source_kind']=='fixture',
                             records=[], provenance={}))
    policy = from_document(doc)
    generated = policy.deadline(cutoff) if policy else cutoff
    return doc, generated.isoformat(), before-after, late_receipts


def diagnose(report):
    candidates = {c['stock_code']: c for c in report['candidates']}
    rows = []
    for row in report['analysis']['rows']:
        code = row['stock_code']
        candidate = candidates.get(code)
        def metric(kind):
            return am_briefing.latest(report['input_document'],code,kind,
                                      am.instant(report['selection_cutoff_at'])) or '검증 불가'
        eligibility = report['candidate_filter']['decisions'].get(code)
        status = ('잠정' if candidate and candidate['status']=='priority' else
                  '제외' if eligibility and eligibility['state']=='confirmed_excluded' else '보류')
        reasons = sorted(set((candidate['missing_evidence'] if candidate else row['missing_evidence']) +
                             (eligibility.get('reasons',[]) if eligibility and eligibility['state']!='analysis_candidate' else [])))
        name=metric('names')
        rows.append(dict(stock_code=code, name=name['value'] if isinstance(name,dict) else '검증 불가',
            status=status, reasons=reasons, eligibility=eligibility or '검증 불가',
            ranking=dict(history=row['rank_history'], top5_appearances=row['top5_appearances'],
                         top20_appearances=row['top20_appearances'], rank_change=row['rank_change']),
            turnover=metric('turnover'), market_cap=metric('market_cap'),
            sector_tags=row['sectors'] or '검증 불가',
            sector_companions=row['sector_companions'] or '검증 불가'))
    return rows


def compare(previous, current):
    if previous is None:
        return dict(available=False, reason='previous_valid_phase_unavailable')
    old_sectors = {s['name']: s for s in previous['sectors']}
    new_sectors = {s['name']: s for s in current['sectors']}
    old = {r['stock_code']: r['status'] for r in previous['candidates']}
    new = {r['stock_code']: r['status'] for r in current['candidates']}
    old_selected = {c for c,s in old.items() if s=='잠정'}
    new_selected = {c for c,s in new.items() if s=='잠정'}
    return dict(available=True, from_slot=previous['slot'], to_slot=current['slot'],
        sector_ranks=[dict(name=name, before=old_sectors.get(name,{}).get('rank'),
                          after=new_sectors.get(name,{}).get('rank'))
                      for name in sorted(old_sectors.keys() | new_sectors.keys())],
        leader_changes=[dict(sector=name,
                            before=old_sectors.get(name,{}).get('leaders',[{}])[0].get('stock_code'),
                            after=new_sectors.get(name,{}).get('leaders',[{}])[0].get('stock_code'))
                        for name in sorted(old_sectors.keys() | new_sectors.keys())
                        if old_sectors.get(name,{}).get('leaders',[{}])[0].get('stock_code') != new_sectors.get(name,{}).get('leaders',[{}])[0].get('stock_code')],
        entered=sorted(new_selected-old_selected), exited=sorted(old_selected-new_selected),
        observed_entered=sorted(new.keys()-old.keys()), observed_exited=sorted(old.keys()-new.keys()),
        status_changes=[dict(stock_code=c,before=old.get(c),after=new.get(c))
                        for c in sorted(old.keys() | new.keys()) if old.get(c)!=new.get(c)],
        missing_slots=dict(before=previous['completeness']['missing_slots'],after=current['completeness']['missing_slots']),
        excluded_future_records=dict(before=previous['excluded_future_records'],after=current['excluded_future_records']))


def replay(documents, *, trade_date, slots=SLOTS):
    date.fromisoformat(trade_date)
    if not documents or not slots or tuple(slots) != tuple(sorted(set(slots))) or any(s not in SLOTS for s in slots):
        raise ValueError('unsupported_replay_scope')
    indexed = {}
    for document in documents:
        cutoff = document.get('selection_cutoff_at',document['trade_date']+'T09:50:00+09:00')
        key = am.instant(cutoff).strftime('%H:%M')
        if key in indexed:
            raise ValueError('duplicate_phase_input')
        indexed[key] = document
    if len(documents)>1 and any(s not in indexed for s in slots):
        raise ValueError('missing_phase_input')
    phases=[]; state=None; previous=None
    for slot in slots:
        source = documents[0] if len(documents)==1 else indexed[slot]
        try:
            doc, generated, excluded, late = prepare(source, trade_date, slot)
            report = am_briefing.analyze(doc, generated_at=generated)
            state, event = session_review.morning_transition(report,slot,state)
            rows = diagnose(report)
            phase = dict(slot=slot,status='잠정' if report['sectors'] else '보류',
                excluded_future_records=excluded,late_receipts=late,sectors=report['sectors'],held_sectors=report['held_sectors'],
                candidates=rows,completeness=report['completeness'],input_diagnostics=report['analysis']['diagnostics'],
                session=deepcopy(state),session_action=event['action'],analysis_report=report,
                telegram_preview=am_briefing.format_mobile(report))
            phase['changes']=compare(previous,phase)
            previous=phase
        except (ValueError, KeyError, TypeError, ArithmeticError) as error:
            allowed={'duplicate_slot','ambiguous_ranking_rows','ambiguous_briefing_metric','replay_date_mismatch'}
            phase=dict(slot=slot,status='검증 불가',
                       error=str(error) if str(error) in allowed else type(error).__name__,
                       changes=dict(available=False,reason='phase_analysis_failed'))
        phases.append(phase)
    result=dict(schema_version='arvis-c12-replay-v1',trade_date=trade_date,phases=phases,
                input_reference=hashlib.sha256(am.canonical(documents).encode('utf-8')).hexdigest(),
                external_calls=0,delivery_attempted=False,order_signal=False)
    return result


def summary(result):
    lines=['[ARVIS 오전 리플레이]', '날짜: '+result['trade_date']]
    for phase in result['phases']:
        sectors=phase.get('sectors',[])
        leader=sectors[0]['name']+' 1위' if sectors else '섹터 선정 보류'
        lines.append(f"{phase['slot']} {leader} / {phase['status']}")
        if phase.get('error'):lines.append('  진단: '+phase['error'])
        if phase.get('completeness',{}).get('missing_slots'):
            lines.append('  0198 관측 누락: '+', '.join(phase['completeness']['missing_slots']))
        if phase.get('late_receipts'):
            lines.append(f"  기준시각 이후 수신 자료 제외: {phase['late_receipts']}건")
        labels=dict(intraday_turnover_unverified='0186 최신 거래대금 검증 불가',
                    market_cap_unverified='시총 검증 불가',sector_missing_or_ambiguous='섹터 검증 불가',
                    master_code_unmatched='종목 적격성 검증 불가',
                    fewer_than_two_verified_companions='검증된 섹터 동반상승 종목 부족')
        reasons={r for c in phase.get('candidates',[]) if c['status']!='잠정' for r in c['reasons']}
        reasons.update(s['reason'] for s in phase.get('held_sectors',[]))
        notes=[labels[r] for r in sorted(reasons) if r in labels]
        if notes:lines.append('  보류 종목·섹터 근거: '+' / '.join(notes))
    return '\n'.join(lines)


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    inputs=parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--input',type=Path,help='existing fixture, saved C12 report, or per-phase bundle JSON')
    inputs.add_argument('--ranking-db',type=Path,help='explicit synthetic test DB only in this development stage')
    parser.add_argument('--synthetic-db',action='store_true')
    parser.add_argument('--turnover-db',type=Path)
    parser.add_argument('--evidence',type=Path)
    parser.add_argument('--capital-json',type=Path)
    parser.add_argument('--date',required=True)
    parser.add_argument('--slots',nargs='+',choices=SLOTS,default=list(SLOTS))
    parser.add_argument('--output',required=True,type=Path)
    args=parser.parse_args(argv)
    try:
        if args.input:
            documents,_=load(args.input)
        else:
            if not args.synthetic_db:raise ValueError('synthetic_database_scope_required')
            evidence=json.loads(args.evidence.read_text(encoding='utf-8')) if args.evidence else None
            documents=[am_input_adapter.adapt(args.ranking_db,trade_date=args.date,
                cutoff_at=args.date+'T'+slot+':00+09:00',environment='mock',synthetic=True,
                turnover_db=args.turnover_db,evidence=evidence,capital_json=args.capital_json,
                observation_policy=ObservationPolicy().document()) for slot in args.slots]
        result=replay(documents,trade_date=args.date,slots=tuple(args.slots))
        with args.output.open('x',encoding='utf-8') as stream:
            json.dump(result,stream,ensure_ascii=False,sort_keys=True,indent=2,allow_nan=False)
        print(summary(result))
        return 1 if any(p['status']=='검증 불가' for p in result['phases']) else 0
    except (ValueError,KeyError,TypeError,OSError,ArithmeticError,sqlite3.Error):
        print('리플레이 실패: 입력·날짜·검증 근거 또는 신규 출력 경로를 확인하세요.')
        return 1


if __name__=='__main__':
    raise SystemExit(main())
