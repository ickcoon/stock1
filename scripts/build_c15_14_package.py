"""Extend the hash-bound C15-13 package with guarded observations; no runtime writes."""
import difflib
import json
from pathlib import Path
import build_c15_13_package as base

ROOT=base.ROOT
OUT=ROOT/'diagnostics/c15-14-package'


def main():
    base.OUT=OUT;base.build()
    manifest=json.loads((OUT/'manifest.json').read_text(encoding='utf8'));stage=OUT/'stage'
    rel='kiwoom_collector/c15_runtime.py'
    (stage/rel).write_bytes((ROOT/rel).read_bytes())
    manifest['files'][rel]=dict(action='add',before_sha256=None,after_sha256=base.sha(stage/rel))
    rel='kiwoom_collector/operating_workflow.py';s=(stage/rel).read_text(encoding='utf8')
    old="""        if due.strftime('%H:%M') in schedule.AM_DECISION_SLOTS and self.settings.get('c15_briefing_enabled') is True:
            from . import c15_delivery
            return c15_delivery.process(self,due)
"""
    new="""        if due.strftime('%H:%M') in schedule.AM_DECISION_SLOTS and self.settings.get('c15_briefing_enabled') is True:
            from . import c15_delivery, c15_runtime
            live=c15_runtime.context(store.path,self._c15_collection_id,due,self.settings,now=self.clock()) if hasattr(self,'_c15_collection_id') else None
            return c15_delivery.process(self,due,live_context=live)
"""
    assert old in s;s=s.replace(old,new)
    anchor="                if ledger['market_data_verification']['recommendation_allowed']:\n"
    assert anchor in s
    replacement="""                c15_live=None
                if slot in schedule.AM_DECISION_SLOTS and self.settings.get('c15_briefing_enabled') is True:
                    from . import c15_runtime
                    c15_live=c15_runtime.context(store.path,identifier,due,self.settings,now=self.clock())
                    ledger['c15_observation_context']=c15_live
                if ledger['market_data_verification']['recommendation_allowed'] or (c15_live or {}).get('allowed'):
                    self._c15_collection_id=identifier
"""
    s=s.replace(anchor,replacement)
    # An expired/closed C15 context may not fall through a direct review call.
    s=s.replace("            return c15_delivery.process(self,due,live_context=live)","            if live is not None and not live['allowed']:\n                return dict(status='held_c15_context',reason=live['reason'],delivery=dict(status='disabled'))\n            return c15_delivery.process(self,due,live_context=live)")
    (stage/rel).write_text(s,encoding='utf8');manifest['files'][rel]['after_sha256']=base.sha(stage/rel)
    manifest.update(blockers=[],ready_for_live=False,
        technical_ready_for_deployment=False,operator_approval_required=True,
        readiness='guarded_provisional_observation_route_requires_operator_approval',
        recommendation_allowed=False,market_asof_verified=False)
    # Regenerate only the review artifact; no apply or process operations.
    patch=[]
    for rel,entry in manifest['files'].items():
        before=(base.RUNTIME/rel).read_text(encoding='utf8').splitlines(True) if entry['action']=='patch' else []
        after=(stage/rel).read_text(encoding='utf8').splitlines(True)
        for lines in (before,after):
            if lines and not lines[-1].endswith('\n'):lines[-1]+='\n'
        patch.extend(difflib.unified_diff(before,after,fromfile='a/'+rel if before else '/dev/null',tofile='b/'+rel))
    (OUT/'review.patch').write_text(''.join(patch),encoding='utf8',newline=chr(10))
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf8')
    (OUT/'file-list.csv').write_text('path,action\n'+'\n'.join(k+','+v['action'] for k,v in manifest['files'].items())+'\n',encoding='utf8')


if __name__=='__main__':main()
