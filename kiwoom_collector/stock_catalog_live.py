"""Future approved ka10099 only. Existing token injection; no OAuth or retries."""
import json
import time
from datetime import timezone,timedelta

from .stock_catalog import API_ID,API_PATH,MARKETS,CatalogError


def after_close(at):
    # Operational guard, not an exchange holiday/special-session calendar.
    if at.tzinfo is None: raise CatalogError('aware_timestamp_required')
    local=at.astimezone(timezone(timedelta(hours=9)))
    if not 16<=local.hour<=23 or (local.hour==23 and local.minute>=50):
        raise CatalogError('after_close_window_required')


class ListTransport:
    def __init__(self,token,*,environment,approved=False,session=None):
        if approved is not True or environment not in ('mock','real'):
            raise CatalogError('explicit_live_approval_required')
        if not isinstance(token,str) or not token or len(token)>4096 or any(c.isspace() for c in token):
            raise CatalogError('valid_injected_token_required')
        if session is None:
            import requests
            session=requests.Session()
        self._session=session;self._token=token
        self._host={'mock':'https://mockapi.kiwoom.com','real':'https://api.kiwoom.com'}[environment]
        self.calls=0;self.last_http_status=None

    def __call__(self,*,market,cont_yn,next_key,remaining_seconds):
        if market not in MARKETS or cont_yn not in ('','Y') or not isinstance(next_key,str) or len(next_key)>50:
            raise CatalogError('disallowed_request')
        if bool(next_key)!=(cont_yn=='Y') or remaining_seconds<=0:
            raise CatalogError('invalid_request_chain')
        from .stock_catalog import now
        headers={'api-id':API_ID,'authorization':'Bearer '+self._token,'Content-Type':'application/json;charset=UTF-8'}
        if cont_yn: headers.update({'cont-yn':cont_yn,'next-key':next_key})
        started=now();deadline=time.monotonic()+remaining_seconds;response=None
        try:
            self.calls+=1
            response=self._session.post(self._host+API_PATH,json={'mrkt_tp':market},headers=headers,
                timeout=(min(5,remaining_seconds),min(20,remaining_seconds)),allow_redirects=False,verify=True,stream=True)
            self.last_http_status=response.status_code
            data={};size=0;parts=[]
            if response.status_code==200:
                for part in response.iter_content(chunk_size=65536):
                    if time.monotonic()>=deadline: raise CatalogError('collection_deadline')
                    size+=len(part)
                    if size>5*1024*1024: raise CatalogError('response_size_exceeded')
                    parts.append(part)
                data=json.loads(b''.join(parts))
            return dict(http_status=response.status_code,body=data,
                headers={str(k).lower():v for k,v in response.headers.items() if str(k).lower() in ('cont-yn','next-key')},
                started_at=started,received_at=now())
        except CatalogError: raise
        except Exception: raise CatalogError('transport_or_json_failed') from None
        finally:
            if response is not None: response.close()

    def close(self):
        self._token=''
        self._session.close()
