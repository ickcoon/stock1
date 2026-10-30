"""Local catalog execution policy, independent of broker service availability."""
from dataclasses import dataclass
from datetime import date, timedelta, timezone
import json
from pathlib import Path
import re

KST=timezone(timedelta(hours=9))
DEFAULT_PATH=Path(__file__).resolve().parent.parent/'config'/'catalog_time_policy.json'


class TimePolicyError(ValueError):
    pass


def minute(value):
    if not isinstance(value,str) or not re.fullmatch(r'(?:[01][0-9]|2[0-3]):[0-5][0-9]',value):
        raise TimePolicyError('invalid_catalog_time_policy')
    hour,minutes=map(int,value.split(':'))
    return hour*60+minutes


@dataclass(frozen=True)
class Policy:
    weekday_start: int
    weekends: bool
    holidays: frozenset
    maintenance: tuple

    def check(self,at):
        if at.tzinfo is None or at.utcoffset() is None:
            raise TimePolicyError('aware_timestamp_required')
        local=at.astimezone(KST)
        current=local.hour*60+local.minute
        for start,end in self.maintenance:
            if (start<=current<end if start<end else current>=start or current<end):
                raise TimePolicyError('catalog_configured_maintenance_window')
        if local.weekday()>=5:
            if not self.weekends:raise TimePolicyError('catalog_weekend_disabled')
        elif local.date() not in self.holidays and current<self.weekday_start:
            raise TimePolicyError('catalog_weekday_after_close_required')


def load(path=DEFAULT_PATH):
    try:
        doc=json.loads(Path(path).read_text(encoding='utf-8-sig'))
        if (set(doc)!={'schema_version','weekday_start','allow_weekends','verified_holidays','maintenance_windows'}
                or doc['schema_version']!='catalog-time-policy-v1'
                or type(doc['allow_weekends']) is not bool
                or not isinstance(doc['verified_holidays'],list)
                or not isinstance(doc['maintenance_windows'],list)):
            raise ValueError()
        start=minute(doc['weekday_start'])
        if start<16*60:raise ValueError()  # Never turn this into a daytime bypass.
        holidays=[]
        for value in doc['verified_holidays']:
            parsed=date.fromisoformat(value)
            if parsed.isoformat()!=value:raise ValueError()
            holidays.append(parsed)
        windows=[]
        for window in doc['maintenance_windows']:
            if not isinstance(window,list) or len(window)!=2:raise ValueError()
            a,b=map(minute,window)
            if a==b:raise ValueError()
            windows.append((a,b))
        return Policy(start,doc['allow_weekends'],frozenset(holidays),tuple(windows))
    except (OSError,ValueError,TypeError,KeyError):
        raise TimePolicyError('invalid_catalog_time_policy') from None
