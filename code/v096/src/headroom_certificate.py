from __future__ import annotations
import numpy as np


def bounds(n, f, M=32):
    n=np.asarray(n,float);f=np.asarray(f,float)
    return f/M,(f+M-n)/M


def pair_upper_gap(mask,S,T,L,U):
    """Worst-case V(T)-V(S) over the remaining finite-pool labels."""
    N=mask.shape[1]
    pos=np.maximum(mask[T]-mask[S],0.)
    neg=np.maximum(mask[S]-mask[T],0.)
    return float((pos@U-neg@L)/N)


def risk_gap(mask,S,L,U,status):
    live=np.flatnonzero(np.asarray(status)!=0)
    if not len(live):return 0.
    return max(0.,max(pair_upper_gap(mask,S,int(T),L,U) for T in live))


def witness_lower_gap(mask,R,T,L,U):
    """Guaranteed V(R)-V(T) over every completion consistent with [L,U]."""
    N=mask.shape[1]
    pos=np.maximum(mask[R]-mask[T],0.)
    neg=np.maximum(mask[T]-mask[R],0.)
    return float((pos@L-neg@U)/N)


def best_witness(mask,T,L,U,proven):
    proven=sorted(set(int(x) for x in proven))
    if not proven:return -float('inf'),None
    vals=[(witness_lower_gap(mask,R,T,L,U),R) for R in proven]
    return max(vals,key=lambda x:(x[0],-x[1]))


def certificate_state(mask,S,L,U,status,areas,proven,epsilon=.02):
    """Return exact risk/headroom certificate obligations for incumbent S.

    A live candidate is discharged if it is known infeasible, has measured area
    no smaller than S, or has a proven feasible witness that beats it by more
    than epsilon for every unseen-label completion.
    """
    status=np.asarray(status,int);areaS=float(areas[S]);rows=[]
    for T in range(mask.shape[0]):
        if T==S:continue
        if status[T]==0:
            rows.append(dict(candidate=T,discharged=True,reason='hardware_infeasible',witness=None,witness_lb=None));continue
        lb,R=best_witness(mask,T,L,U,proven)
        if lb>epsilon+1e-12:
            rows.append(dict(candidate=T,discharged=True,reason='risk_witness',witness=R,witness_lb=lb));continue
        if status[T]==1 and T in areas and float(areas[T])>=areaS-1e-12:
            rows.append(dict(candidate=T,discharged=True,reason='area_not_smaller',witness=R,witness_lb=lb));continue
        rows.append(dict(candidate=T,discharged=False,reason='unresolved',witness=R,witness_lb=lb))
    rg=risk_gap(mask,S,L,U,status)
    threats=[r for r in rows if not r['discharged']]
    return dict(risk_gap=rg,risk_certified=bool(rg<=epsilon+1e-12),headroom_certified=bool(rg<=epsilon+1e-12 and not threats),
                threats=threats,rows=rows,incumbent=int(S),incumbent_area=areaS)


def full_information_summary(data,epsilon=.02,area_budget=.2,epsilon_grid=None):
    cat,obs,stages,mask,cf,cs,paths=data;M=len(obs[0]);N=cat['N']
    truth=np.array([sum(float(r['y']) for r in arm)/M for arm in obs])
    feasible=np.array([bool(s['record']['feasible']) for s in stages])
    areas=np.array([float(s['record']['area_ratio']) for s in stages])
    values=mask@truth/N;vstar=float(values[feasible].max());regret=vstar-values
    eq=np.flatnonzero(feasible&(regret<=epsilon+1e-12));assert len(eq)
    best=int(min(eq,key=lambda j:(areas[j],regret[j],cat['plans'][j]['plan_id'])))
    exact=np.flatnonzero(feasible&np.isclose(regret,0.,atol=1e-12,rtol=0.))
    exact_best=int(min(exact,key=lambda j:(areas[j],cat['plans'][j]['plan_id'])))
    rows=[]
    for j in np.flatnonzero(feasible):
        rows.append(dict(index=int(j),plan_id=cat['plans'][j]['plan_id'],selected=cat['plans'][j]['selected'],area=float(areas[j]),
                         value=float(values[j]),regret=float(regret[j]),epsilon_optimal=bool(j in set(eq)),headroom=float(area_budget-areas[j])))
    if epsilon_grid is None:epsilon_grid=[0.,.001,.002,.0023,.005,.01,.015,.02,.022,.025,.03]
    sensitivity=[]
    for e in epsilon_grid:
        ids=np.flatnonzero(feasible&(regret<=e+1e-12))
        if not len(ids):continue
        b=int(min(ids,key=lambda j:(areas[j],regret[j],cat['plans'][j]['plan_id'])))
        sensitivity.append(dict(epsilon=float(e),equivalent_candidates=int(len(ids)),plan_id=cat['plans'][b]['plan_id'],selected=cat['plans'][b]['selected'],
                                area=float(areas[b]),regret=float(regret[b]),headroom=float(area_budget-areas[b])))
    return dict(N=int(N),M=int(M),feasible_candidates=int(feasible.sum()),epsilon=float(epsilon),epsilon_equivalent_candidates=int(len(eq)),
                vstar=vstar,headroom_plan_id=cat['plans'][best]['plan_id'],headroom_selected=cat['plans'][best]['selected'],
                headroom_area=float(areas[best]),headroom_regret=float(regret[best]),headroom=float(area_budget-areas[best]),
                exact_plan_id=cat['plans'][exact_best]['plan_id'],exact_selected=cat['plans'][exact_best]['selected'],
                exact_area=float(areas[exact_best]),exact_headroom=float(area_budget-areas[exact_best]),
                epsilon_area_span=float(areas[eq].max()-areas[eq].min()),rows=rows,sensitivity=sensitivity)


def audit_headroom_output(data,result,epsilon=.02):
    summary=full_information_summary(data,epsilon=epsilon)
    pid=result['plan_id']
    assert pid==summary['headroom_plan_id'],(pid,summary['headroom_plan_id'])
    assert result['headroom_certified']
    return dict(status='passed',plan_id=pid,full_information_plan_id=summary['headroom_plan_id'],
                area=summary['headroom_area'],regret=summary['headroom_regret'])
