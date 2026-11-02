"""C12-AC: frozen final-only briefing using existing analysis and delivery."""
from copy import deepcopy
import json

from . import am_briefing, am_momentum as am, session_review, telegram_briefing as tg


def freeze(document):
    """Exclude evidence observed OR received after the advertised cutoff."""
    document = deepcopy(document)
    cutoff = am.instant(document['selection_cutoff_at'])

    def known(item):
        return all(am.instant(item[key]) <= cutoff for key in ('observed_at', 'received_at') if item.get(key))

    document['rankings'] = [r for r in document['rankings'] if known(r)]
    for security in document['securities'].values():
        for kind, values in security.items():
            if isinstance(values, list):
                security[kind] = [v for v in values if known(v)]
    return document


def empty_document(due):
    return dict(schema_version=am.ADAPTER_INPUT_SCHEMA, source_kind='readonly_adapter',
                trade_date=due.date().isoformat(), selection_cutoff_at=due.isoformat(),
                environment='real', market='KRX', rankings=[], securities={})


def process(workflow, due, document, *, reason=None):
    """Persist preview before optional delivery. Replays use the frozen report."""
    from .operating_workflow import write_new
    day, slot = due.date().isoformat(), due.strftime('%H:%M')
    if slot not in ('09:40','09:45','09:50') or am.instant(document['selection_cutoff_at']) != due:
        raise ValueError('morning_briefing_scope_mismatch')
    folder = workflow.root/'reports'/('c12ac-'+due.strftime('%Y%m%d-%H%M'))
    path = folder/'telegram.json'
    if path.exists():
        report = json.loads(path.read_text(encoding='utf-8'))
        am_briefing.validate(report)
        if am.instant(report['selection_cutoff_at']) != due:
            raise ValueError('frozen_briefing_scope_mismatch')
    else:
        document = freeze(document)
        if not document.get('candidate_filter'):
            from .am_candidate_adapter import join
            document = join(document, dict(at=due.isoformat(), synthetic=document['source_kind']=='fixture',
                                          records=[], provenance={}))
        if slot == '09:50':
            document['briefing_format'] = 'c12ac-final-v1'
        report = am_briefing.analyze(document, generated_at=workflow.clock().isoformat())
        previous = sorted((workflow.root/'reports/decisions'/day).glob('09*.json'))
        state = json.loads(previous[-1].read_text(encoding='utf-8')) if previous else None
        if report.get('candidate_filter'):
            state, decision = session_review.morning_transition(report, slot, state)
            state_path = workflow.root/'reports/decisions'/day/(slot.replace(':','')+'.json')
            if not state_path.exists():
                write_new(state_path, state)
        else:
            decision = dict(action='held_missing_master', send=False)
        write_new(folder/'review.json', dict(decision=decision, reason=reason,
                  final=slot == '09:50', execution_signal=False))
        write_new(path, report)
    text = tg.summary(report)
    preview = folder/'telegram.txt'
    if not preview.exists():
        with preview.open('x', encoding='utf-8') as stream:
            stream.write(text)
    delivery = dict(status='disabled' if slot == '09:50' else 'preview_only')
    if slot == '09:50' and workflow.settings.get('telegram_send_enabled', False) is True:
        delivery = tg.deliver(report, text, None, tg.Configuration.from_environment(),
            allow_readonly_report=True, windows_trust_store=True,
            state_root=workflow.settings['notification_state'])
    return dict(status='reviewed', report=str(folder), delivery=delivery, final=slot == '09:50')
