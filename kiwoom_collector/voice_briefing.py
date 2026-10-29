"""Offline Korean script, pluggable TTS and report-bound MP3 artifacts."""
import argparse
from dataclasses import asdict, dataclass, field
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Protocol

from . import telegram_briefing as tg

DIGITS = ('공','일','이','삼','사','오','육','칠','팔','구')
LETTERS = dict(zip('ABCDEFGHIJKLMNOPQRSTUVWXYZ',('에이','비','씨','디','이','에프','지','에이치','아이','제이','케이','엘','엠','엔','오','피','큐','알','에스','티','유','브이','더블유','엑스','와이','지')))
MAX_AUDIO = 4 * 1024 * 1024


def safe(value):
    tg.scan(value)
    text = tg.am.canonical(value)
    tg.require(not re.search(r'\bsk-[A-Za-z0-9_-]{10,}', text), 'sensitive_content_rejected')


def korean_number(value):
    number = tg.am.number(str(value))
    tg.require(number is not None, 'invalid_spoken_number')
    if number < 0: return '마이너스 '+korean_number(str(-number))
    integer, _, fraction = format(number,'f').partition('.')
    units = ('','만','억','조','경','해','자','양','구','간')
    groups=[]
    while integer:
        groups.append(int(integer[-4:]));integer=integer[:-4]
    tg.require(len(groups)<=len(units), 'spoken_number_too_large')
    spoken=[]
    for index,group in reversed(list(enumerate(groups))):
        if not group: continue
        part=''
        for power,unit in ((3,'천'),(2,'백'),(1,'십'),(0,'')):
            digit=group//(10**power)%10
            if digit: part+=('' if digit==1 and power else DIGITS[digit])+unit
        spoken.append(part+units[index])
    result=' '.join(spoken) or '영'
    if fraction: result+=' 점 '+' '.join(DIGITS[int(d)] for d in fraction)
    return result


def pronounce_label(value):
    tg.am.label(value)
    return re.sub(r'[A-Z]{2,6}',lambda m:' '.join(LETTERS[c] for c in m[0])+' ',value).strip()


def pronounce_code(code):
    tg.require(bool(tg.am.CODE.fullmatch(code)), 'invalid_code')
    return ' '.join(DIGITS[int(c)] if c.isdigit() else LETTERS.get(c,c) for c in code.replace('_',' '))


def pronounce_price(value):
    return korean_number(value)+' 원'


def pronounce_time(value):
    at=tg.am.instant(value)
    hours=('열두','한','두','세','네','다섯','여섯','일곱','여덟','아홉','열','열한')
    return ('오전 ' if at.hour<12 else '오후 ')+hours[at.hour%12]+' 시'+(' '+korean_number(str(at.minute))+' 분' if at.minute else '')+(' '+korean_number(str(at.second))+' 초' if at.second else '')


@dataclass(frozen=True)
class SpeechScript:
    schema_version: str
    report_schema_version: str
    report_reference: str
    trade_date: str
    selection_cutoff_at: str
    environment: str
    text: str
    script_sha256: str
    estimated_seconds: int
    candidate_confirmed: bool = False


def build_script(report):
    tg.validate(report);safe(report)
    cutoff=tg.am.instant(report['selection_cutoff_at']);day=cutoff.date()
    lines=['안녕하세요. 아르비스의 오전 관심도 브리핑입니다. 실제 음성을 연결하면 인공지능이 생성한 음성으로 안내합니다.',
           korean_number(str(day.year))+' 년 '+korean_number(str(day.month))+' 월 '+korean_number(str(day.day))+' 일, 분석 기준은 '+pronounce_time(report['selection_cutoff_at'])+'입니다.',
           '데이터 환경은 '+('모의, mock' if report['environment']=='mock' else '실전 표시, real')+'입니다. 다만 이 보고서의 출처는 합성 픽스처이며 실측 결과가 아닙니다.',
           '이번 내용은 매매 확정 신호가 아닙니다. 모든 후보는 미확정이며 확정 선정이나 주문으로 연결하지 않습니다.']
    full=report['completeness']['top20_complete'] and report['completeness']['accepted_slots']==list(tg.am.SLOTS)
    rows=[r for r in report['rows'] if r['candidate_status']!='insufficient_evidence'] if full else []
    rows.sort(key=lambda r:(-r['top5_appearances'],-r['top20_appearances'],r['rank_history'][-1]['rank'],r['stock_code']))
    if not rows:
        lines+=['오전 관측 또는 관심도 근거가 부족합니다. 임의로 상위 세 종목을 만들지 않고 판단을 보류합니다.',
                '관측이 없는 회차를 보간하거나 이후 시세로 채우지 않았습니다. 가격과 거래대금, 시가총액이 없거나 검증되지 않았다면 충족했다고 말할 수 없습니다.']
    else:
        lines+=['종목명은 보고서에 없으므로 종목 코드로 읽겠습니다. 관심도 순서이며 수익률 순위가 아닙니다.']
        for row in rows[:3]:
            words='종목 코드 '+pronounce_code(row['stock_code'])+'입니다. 상위 다섯 순위에 '+korean_number(str(row['top5_appearances']))+' 회, 상위 스무 순위에 '+korean_number(str(row['top20_appearances']))+' 회 등장했습니다.'
            words+=' 근거는 '+', '.join(tg.REASONS[r] for r in row['reasons'][:2])+'입니다.'
            price=row['evidence']['price']['observations']
            if price: words+=' 마지막 검증 관측 가격은 '+pronounce_price(price[-1]['value'])+'이며 '+pronounce_time(price[-1]['observed_at'])+' 관측입니다.'
            missing=[name for key,name in [('price','가격'),('turnover','거래대금'),('market_cap','시가총액'),('liquidity','유동성')] if not row['evidence'][key]['available']]
            if missing: words+=' '+', '.join(missing)+' 정보는 누락되었거나 검증되지 않았습니다.'
            elif row['missing_evidence']: words+=' 추가 검증이 부족한 항목은 상세 보고서에 남겨 두었습니다.'
            lines.append(words)
        sectors=sorted({p['sector'] for row in rows for p in row['sector_companions'] if p['count']>0})[:3]
        lines.append('관심도 동반 섹터는 '+', '.join(pronounce_label(s) for s in sectors)+'입니다. 시세 주도력 확정은 아닙니다.' if sectors else '동반 섹터를 판단할 근거는 부족합니다.')
    lines+=['관측 마감 뒤에 들어온 데이터는 이 판단에 사용하지 않았습니다. 조회 관심도의 반복만으로 실제 자금 유입이나 다음 날 상승을 단정하지 않습니다.',
            '이 분석만으로 주도주와 후속주의 인과관계를 확정할 수 없습니다. 거래대금 증가도 확인된 관측 구간의 변화일 뿐, 하루 전체 흐름이나 주문 체결 가능성을 보장하지 않습니다.',
            '세부 순위 이력, 관측 시각, 가격과 단위, 부족한 근거는 연결된 동일 버전의 제이슨 보고서와 에이치 티 엠 엘 보고서에서 확인할 수 있습니다. 모르는 정보는 추정하지 않겠습니다. 이상 아르비스였습니다.']
    text='\n'.join(lines);safe(text)
    # Reading-speed estimate only; actual duration is measured from generated frames.
    seconds=round(len(re.sub(r'\s','',text))/7)
    return SpeechScript(
        'arvis-speech-v1',report['schema_version'],report['evaluation_reference'],report['trade_date'],report['selection_cutoff_at'],report['environment'],text,hashlib.sha256(text.encode()).hexdigest(),seconds)


@dataclass(frozen=True)
class AudioArtifact:
    report_schema_version: str
    report_reference: str
    script_sha256: str
    provider: str
    data: bytes = field(repr=False)
    is_test: bool = True
    format: str = 'mp3'


class TTSProvider(Protocol):
    def synthesize(self, script: SpeechScript) -> AudioArtifact: ...


class DisabledTTS:
    def synthesize(self, script):
        raise tg.BriefingError('tts_not_configured')


class SilenceFixtureTTS:
    """Valid silent MP3 frames for transport tests, never a spoken briefing."""
    def synthesize(self, script):
        return AudioArtifact(script.report_schema_version,script.report_reference,script.script_sha256,'silent-fixture',
                             (b'\xff\xfb\x90\x00'+bytes(413))*40,True)


def mp3_duration(data):
    tg.require(isinstance(data,bytes) and 8<=len(data)<=MAX_AUDIO,'invalid_audio')
    position=0;seconds=0;frames=0
    if data[:3]==b'ID3':
        tg.require(len(data)>=10 and all(v<128 for v in data[6:10]),'invalid_audio')
        position=10+sum(v<<(7*(3-i)) for i,v in enumerate(data[6:10]))
        if data[5]&16:position+=10
    rates1=(0,32,40,48,56,64,80,96,112,128,160,192,224,256,320)
    rates2=(0,8,16,24,32,40,48,56,64,80,96,112,128,144,160)
    while position<len(data):
        if len(data)-position==128 and data[position:position+3]==b'TAG':break
        tg.require(position+4<=len(data),'invalid_audio')
        h=int.from_bytes(data[position:position+4],'big');version=(h>>19)&3;layer=(h>>17)&3;rate=(h>>12)&15;sample=(h>>10)&3
        tg.require(h>>21==2047 and version in (0,2,3) and layer==1 and 0<rate<15 and sample<3,'invalid_audio')
        hz=(44100,48000,32000)[sample]//(1 if version==3 else 2 if version==2 else 4)
        size=(144000 if version==3 else 72000)*(rates1 if version==3 else rates2)[rate]//hz+((h>>9)&1)
        tg.require(size>=4 and position+size<=len(data),'invalid_audio')
        seconds+=(1152 if version==3 else 576)/hz;frames+=1;position+=size
    tg.require(frames>=2 and 0<seconds<=180,'invalid_audio')
    return round(seconds,3)


def validate_audio(report,script,audio):
    tg.require(script==build_script(report),'speech_report_mismatch')
    tg.require(isinstance(audio,AudioArtifact) and type(audio.is_test) is bool and audio.format=='mp3','invalid_audio')
    tg.require((audio.report_schema_version,audio.report_reference,audio.script_sha256)==(script.report_schema_version,script.report_reference,script.script_sha256),'audio_report_mismatch')
    tg.am.label(audio.provider);safe(audio.provider)
    safe(audio.data.decode('utf-8',errors='ignore'))
    return mp3_duration(audio.data)


def synthesize(report,provider=None):
    script=build_script(report)
    try:
        audio=(provider or DisabledTTS()).synthesize(script)
        validate_audio(report,script,audio)
        return script,audio,None
    except Exception:
        return script,None,'tts_unavailable_text_preserved'


def write_artifacts(report,script,audio,name):
    tg.require(bool(re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}',name)),'invalid_name')
    tg.require(script==build_script(report),'speech_report_mismatch')
    duration=validate_audio(report,script,audio) if audio else None
    root=tg.REPORTS.resolve();tg.require(root==tg.PROJECT.resolve()/'reports','invalid_report_root');root.mkdir(exist_ok=True)
    metadata=dict(script=asdict(script),audio=None if audio is None else dict(provider=audio.provider,is_test=audio.is_test,format='mp3',duration_seconds=duration,sha256=hashlib.sha256(audio.data).hexdigest()))
    safe(metadata)
    paths=[(root/(name+'.speech.json'),json.dumps(metadata,ensure_ascii=False,indent=2).encode('utf-8'))]
    if audio: paths.append((root/(name+'.mp3'),audio.data))
    tg.require(not any(p.exists() or p.is_symlink() for p,_ in paths),'existing_artifact_preserved')
    created=[]
    try:
        for path,data in paths:
            with path.open('xb') as stream:created.append(path);stream.write(data)
    except BaseException:
        for path in created:path.unlink()
        raise
    return [p for p,_ in paths]


def main(argv=None):
    parser=argparse.ArgumentParser(description='Offline voice foundation; no external TTS provider wired')
    parser.add_argument('--report',type=Path,required=True);parser.add_argument('--html',type=Path)
    parser.add_argument('--provider',choices=('disabled','fixture'),default='disabled')
    parser.add_argument('--name');parser.add_argument('--send',action='store_true')
    parser.add_argument('--method',choices=('sendVoice','sendAudio'),default='sendVoice')
    args=parser.parse_args(argv)
    try:
        secrets=tuple(os.environ.get(k,'').strip() for k in ('ARVIS_TELEGRAM_BOT_TOKEN','ARVIS_TELEGRAM_CHAT_ID','OPENAI_API_KEY'))
        report,text,html=tg.prepare(args.report,args.html,secrets=secrets);safe(report)
        script,audio,error=synthesize(report,SilenceFixtureTTS() if args.provider=='fixture' else None)
        if args.name:write_artifacts(report,script,audio,args.name)
        if not args.send:
            print(json.dumps(dict(status='dry_run',script=asdict(script),audio_ready=audio is not None,test_audio=audio.is_test if audio else False,fallback=error,candidate_confirmed=False)))
            return 0
        config=tg.Configuration.from_environment()
        result=tg.deliver(report,text,html,config,voice=(script,audio) if audio else None,voice_method=args.method)
        print(json.dumps(result));return 1 if result['status']=='failed_or_unknown' else 0
    except Exception:
        print(json.dumps(dict(status='rejected',reason='voice_validation_failed')));return 1


if __name__=='__main__':raise SystemExit(main())
