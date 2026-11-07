"""Live receipt-context guard for provisional C15 observations, not price-time proof."""
import json
from datetime import datetime

from .am_time_policy import instant, KST
from .c12_latest import read_db
from . import schedule


def context(path,identifier,due,settings,*,now=None):
    # now is supplied only by Workflow's existing clock (injected in tests).
    now=instant(now or datetime.now(KST));due=instant(due)
    result=dict(allowed=False,recommendation_allowed=False,market_asof_verified=False,
                market_trade_date_verified=False,mode='provisional_receipt_observation',reason='invalid_context')
    if due.strftime('%H:%M') not in schedule.AM_DECISION_SLOTS or due.second or due.microsecond:return result
    try:holidays=schedule.load_holidays(settings.get('holidays_file'))
    except (OSError,ValueError):return dict(result,reason='holiday_calendar_invalid')
    if due.weekday()>=5 or due.date() in holidays:return dict(result,reason='market_closed_calendar')
    if now.date()!=due.date() or not 0<=(now-due).total_seconds()<=120:
        return dict(result,reason='outside_live_window')
    try:
        with read_db(path) as db:
            row=db.execute('SELECT * FROM collections WHERE id=?',(identifier,)).fetchone()
            if row is None:return dict(result,reason='collection_missing')
            row=dict(row)
            rows=list(db.execute('SELECT rank,stk_cd,raw_json FROM ranking_records WHERE collection_id=? ORDER BY rank',(identifier,)))
        if (row['environment']!='real' or row['api_id']!='ka00198' or row['status']!='success'
            or row.get('test_mode')!=0 or instant(row['scheduled_at'])!=due
            or not due<=instant(row['collected_at'])<=min(now,due.replace(second=5))
            or row['record_count']!=20 or len(rows)!=20
            or [r['rank'] for r in rows]!=list(range(1,21))
            or len({r['stk_cd'] for r in rows})!=20):
            return dict(result,reason='incomplete_or_nonlive_collection')
        for r in rows:
            raw=json.loads(r['raw_json']);date=raw.get('dt')
            if isinstance(date,str) and len(date)==8 and date.isascii() and date.isdigit():
                if datetime.strptime(date,'%Y%m%d').date()!=due.date():return dict(result,reason='response_date_mismatch')
    except (OSError,ValueError,KeyError,TypeError) as error:
        return dict(result,reason='collection_evidence_invalid',error_type=type(error).__name__)
    except Exception as error:
        # SQLite failures must hold without disclosing source values.
        import sqlite3
        if not isinstance(error,sqlite3.Error):raise
        return dict(result,reason='collection_evidence_invalid',error_type=type(error).__name__)
    return dict(result,allowed=True,reason='timely_completed_scheduled_receipt',
                collection_id=identifier,scheduled_at=due.isoformat(),checked_at=now.isoformat())
