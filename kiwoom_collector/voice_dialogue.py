"""Passive voice-question and evidence contracts. No receiver, STT/GPT calls or tools."""
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date
import re
from typing import Protocol

from . import telegram_briefing as tg
from .voice_briefing import safe, pronounce_code, pronounce_price


@dataclass(frozen=True, repr=False)
class IncomingVoice:
    chat_id: str
    message_id: int
    file_id: str
    received_at: str
    mime_type: str
    duration_seconds: int

    @property
    def binding(self):
        return tg.digest([self.chat_id,self.message_id,self.file_id,self.received_at])


def accept_voice(update,allowed_chat_ids,received_at):
    """Accept a provided Telegram update; never fetch updates, URLs or files."""
    try:
        message=update['message'];chat=message['chat'];chat_id=str(chat['id'])
        if chat.get('type')!='private' or not tg.CHAT.fullmatch(chat_id) or chat_id not in allowed_chat_ids:return None
        if str(message.get('from',{}).get('id'))!=chat_id:return None
        voice=message['voice'];file_id=voice['file_id'];mime=voice.get('mime_type')
        tg.require(type(message['message_id']) is int and message['message_id']>0)
        tg.require(isinstance(file_id,str) and re.fullmatch(r'[A-Za-z0-9_-]{1,256}',file_id),'invalid_voice_reference')
        tg.require(type(voice['duration']) is int and 0<voice['duration']<=180)
        tg.require(type(voice.get('file_size')) is int and 0<voice['file_size']<=4*1024*1024)
        tg.require(mime in ('audio/ogg','audio/mpeg','audio/mp4'))
        at=tg.am.instant(received_at)
        return IncomingVoice(chat_id,message['message_id'],file_id,at.isoformat(),mime,voice['duration'])
    except (KeyError,TypeError,ValueError,AttributeError):return None


@dataclass(frozen=True, repr=False)
class Transcript:
    message_binding: str
    text: str
    language: str = 'ko'


class STTProvider(Protocol):
    def transcribe(self,voice: IncomingVoice,audio: bytes) -> Transcript: ...


@dataclass(frozen=True)
class ReportKey:
    trade_date: str
    environment: str
    selection_cutoff_at: str
    schema_version: str = tg.am.SCHEMA
    selection_type: str = 'AM_MOMENTUM'
    market: str = 'KRX'

    def __post_init__(self):
        day=date.fromisoformat(self.trade_date)
        tg.require(self.environment in ('mock','real') and self.schema_version==tg.am.SCHEMA and self.selection_type=='AM_MOMENTUM')
        tg.require(tg.am.instant(self.selection_cutoff_at).date()==day)
        tg.require(self.market in ('KRX','NXT','SOR') and (self.environment!='mock' or self.market=='KRX'))


class ReportRepository(Protocol):
    def get(self,key: ReportKey) -> dict | None: ...


class MemoryReports:
    """Explicit report objects only; no external question can become a file path."""
    def __init__(self,reports):
        self._reports={}
        for report in reports:
            tg.validate(report);safe(report)
            key=ReportKey(report['trade_date'],report['environment'],report['selection_cutoff_at'],market=report['market'])
            tg.require(key not in self._reports,'ambiguous_report_version')
            self._reports[key]=deepcopy(report)

    def get(self,key):
        report=self._reports.get(key)
        return deepcopy(report) if report is not None else None


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    json_pointer: str
    value_json: str
    spoken_fact: str
    observed_at: str | None


@dataclass(frozen=True)
class AnswerContext:
    report_reference: str
    report_key: ReportKey
    question: str = field(repr=False)
    evidence: tuple[Evidence,...]
    disposition: str
    missing_evidence: tuple[str,...]
    candidate_confirmed: bool = False
    allowed_actions: tuple = ('read_supplied_report_only',)


class AnswerProvider(Protocol):
    # Future GPT may select evidence IDs, never execute tools or supply unchecked facts.
    def select_evidence(self,context: AnswerContext) -> tuple[str,...]: ...


def answer_context(voice,transcript,key,repository,allowed_chat_ids):
    tg.require(isinstance(voice,IncomingVoice) and bool(tg.CHAT.fullmatch(voice.chat_id)) and voice.chat_id in allowed_chat_ids,'recipient_not_allowed')
    tg.require(isinstance(transcript,Transcript) and transcript.message_binding==voice.binding and transcript.language=='ko','transcript_binding_mismatch')
    tg.require(isinstance(transcript.text,str) and 0<len(transcript.text.strip())<=1000,'invalid_question')
    safe(transcript.text)
    tg.require(isinstance(key,ReportKey),'invalid_report_key')
    report=repository.get(key)
    tg.require(report is not None,'report_unavailable')
    tg.validate(report);safe(report)
    tg.require(ReportKey(report['trade_date'],report['environment'],report['selection_cutoff_at'],market=report['market'])==key,'report_scope_mismatch')
    tg.require(tg.am.instant(report['generated_at'])<=tg.am.instant(voice.received_at),'report_not_available_when_asked')
    question=transcript.text.strip()
    blocked=bool(re.search(r'주문|매수|매도|매매|수집기|스케줄러|데이터베이스|\bDB\b|실행|삭제|변경|명령|ignore|system|powershell|cmd|[\\/]',question,re.I))
    prediction=bool(re.search(r'내일|다음\s*날|예측|미래|오를까|수익\s*보장',question))
    disposition='refused' if blocked else 'unknown' if prediction else 'evidence_only'
    evidence=[Evidence('report.cutoff','/selection_cutoff_at',tg.am.canonical(report['selection_cutoff_at']),
                       '분석 기준은 '+report['selection_cutoff_at']+'입니다.',report['selection_cutoff_at'])]
    missing=set()
    for i,row in enumerate(report['rows']):
        code=row['stock_code'];name='종목 코드 '+pronounce_code(code)
        evidence.append(Evidence(code+'.top5',f'/rows/{i}/top5_appearances',tg.am.canonical(row['top5_appearances']),
                                 name+'의 상위 다섯 순위 반복 횟수는 '+str(row['top5_appearances'])+' 회입니다.',row['rank_history'][-1]['observed_at'] if row['rank_history'] else None))
        for kind,label in [('price','가격'),('turnover','거래대금'),('market_cap','시가총액')]:
            values=row['evidence'][kind]['observations']
            if values:
                last=values[-1];pointer=f'/rows/{i}/evidence/{kind}/observations/{len(values)-1}/value'
                evidence.append(Evidence(code+'.'+kind,pointer,tg.am.canonical(last['value']),name+'의 보고서상 마지막 검증 '+label+'은 '+pronounce_price(last['value'])+'입니다.',last['observed_at']))
        missing.update(row['missing_evidence'])
    return AnswerContext(report['evaluation_reference'],key,question,tuple(evidence),disposition,tuple(sorted(missing)))


@dataclass(frozen=True)
class GroundedAnswer:
    report_reference: str
    report_key: ReportKey
    text: str
    citations: tuple[Evidence,...]
    candidate_confirmed: bool = False


def grounded_answer(context,evidence_ids,report_reference):
    """Render exact supplied facts; unchecked model prose is intentionally unsupported."""
    tg.require(isinstance(context,AnswerContext) and report_reference==context.report_reference,'answer_report_mismatch')
    ledger={item.evidence_id:item for item in context.evidence}
    tg.require(isinstance(evidence_ids,tuple) and len(evidence_ids)<=8 and len(set(evidence_ids))==len(evidence_ids),'invalid_citations')
    tg.require(all(isinstance(key,str) and key in ledger for key in evidence_ids),'unsupported_claim')
    if context.disposition=='refused':
        text='음성으로 주문이나 운영 시스템 변경을 실행하지 않습니다. 제공된 보고서의 사실 조회만 가능합니다.';selected=()
    elif context.disposition=='unknown' or not evidence_ids:
        text='해당 기준 시각의 보고서만으로는 알 수 없습니다. 미래 시세나 누락된 정보를 추정하지 않겠습니다.';selected=()
    else:
        selected=tuple(ledger[key] for key in evidence_ids)
        text=' '.join(item.spoken_fact for item in selected)+' 이 자료는 합성 보고서의 잠정 분석이며 매매 확정 신호가 아닙니다.'
    safe(text)
    return GroundedAnswer(context.report_reference,context.report_key,text,selected)
