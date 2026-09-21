import time,math
import numpy as np
from scipy.optimize import linprog
from feasibility_gp import probabilities
from dual_policy_v2 import geometry

def plan_cover(mask,S,L,U,n,mu,status,q,cf,cs,epsilon,M):
    N=mask.shape[1];pos=np.maximum(mask-mask[S],0);neg=np.maximum(mask[S]-mask,0)
    demand=pos@U-neg@L-epsilon*N
    active=np.flatnonzero((demand>1e-10)&(status!=0));available=np.flatnonzero(n<M)
    if not len(active):return 0.,None,None
    A=(pos[active][:,available]*(1-mu[available])+neg[active][:,available]*mu[available])/M
    unknown=np.flatnonzero(status[active]==-1);extra=np.zeros((len(active),len(unknown)))
    if len(unknown):extra[unknown,np.arange(len(unknown))]=demand[active[unknown]]
    costs=np.concatenate([np.full(len(available),cf),cs/np.maximum(.05,1-q[active[unknown]])])
    bounds=[(0.,float(M-n[i])) for i in available]+[(0.,1.) for _ in unknown]
    result=linprog(costs,A_ub=-np.concatenate([A,extra],axis=1),b_ub=-demand[active],bounds=bounds,method='highs')
    if not result.success:return float('inf'),None,None
    x=np.zeros(mask.shape[1]);x[available]=result.x[:len(available)]
    z=np.zeros(mask.shape[0]);z[active[unknown]]=result.x[len(available):]
    return float(result.fun),x,z

def run(method,mask,service,seed,epsilon=.02,M=32,batch=8,quota=1.,max_actions=30000):
    mask=np.asarray(mask,float);K,N=mask.shape;n=np.zeros(N,int);f=np.zeros(N,int)
    status=np.full(K,-1,int);status[0]=1;proven={0};rng=np.random.default_rng(seed)
    observations=[(0,0.,True)];algorithm=0.;since=0.;trace=[];count=0
    eager=method.startswith('Eager');directed='Directed' in method or method=='Cardinality-First'
    cache=None;last_plan_step=-100;cs0=service.screen_cost_estimate
    def screen(j):
        nonlocal since,count,cache
        r=service.screen(int(j));status[j]=1 if r['passed'] else 0
        observations.append((int(j),r['area_ratio'],r['timing']));since=0.;count+=1;cache=None
    if eager:
        for j in range(1,K):screen(j)
    for step in range(max_actions):
        begin=time.perf_counter();L=f/M;U=(f+M-n)/M;mu=(f+4*(f.sum()+1)/(n.sum()+2))/(n+4)
        S,G,gaps,potential,live=geometry(mask,L,U,status,epsilon,mu)
        feasible=np.flatnonzero(status==1);values=mask@mu
        done=feasible[np.max(gaps[feasible][:,live],axis=1)<=epsilon+1e-12]
        if len(done):
            S=int(done[np.argmax(values[done])]);G=float(gaps[S,live].max())
            algorithm+=time.perf_counter()-begin
            if S not in proven:
                proof=service.prove(S)
                if not proof['passed']:status[S]=0;continue
                proven.add(S)
            result=dict(method=method,seed=seed,status='certified',incumbent=S,gap=G,
                epsilon=epsilon,n=n.tolist(),f=f.tolist(),status_vector=status.tolist(),
                controller_seconds=algorithm,**service.totals())
            result['total_seconds']=result['service_seconds']+algorithm
            return result,trace
        candidates=np.flatnonzero((status==-1)&(gaps[S]>epsilon+1e-12))
        available=np.flatnonzero(n<M);challenger=int(live[np.argmax(gaps[S,live])])
        diff=np.flatnonzero((mask[challenger]!=mask[S])&(n<M))
        if not len(diff):diff=available
        js=int(candidates[np.argmax(values[candidates])]) if len(candidates) else None
        action='fault';bit=None;reason=method
        cf=max(1e-8,service.fault_cost_estimate);cs=max(1e-8,service.screen_cost_estimate)
        q=None
        if method in ['Dual','Cover','Cover-LP','GP-Lazy','Directed-GP-Lazy']:
            q=probabilities(mask,observations,service.area_prior_per_bit,service.nominal_tmr_bits)
        if method in ['GP-Lazy','Directed-GP-Lazy'] and js is not None:
            js=int(candidates[np.argmax(q[candidates]*gaps[S,candidates])])
        if method=='Cardinality-First':
            if js is not None:js=int(candidates[np.argmax(mask[candidates].sum(axis=1))])
            if js is not None and (len(feasible)==1 or values[js]>values[S]+epsilon*N or not len(available)):action='screen'
        elif method in ['Dual','Cover','Cover-LP']:
            excess=np.maximum(gaps[S]-epsilon,0.);excess[status==0]=0
            pos=np.maximum(mask-mask[S],0);neg=np.maximum(mask[S]-mask,0)
            contraction=(pos*(1-mu)+neg*mu)/(M*N)
            if method=='Dual':gains=2*excess@contraction
            else:gains=np.minimum(excess[:,None],batch*contraction).sum(axis=0)/batch
            gains[n>=M]=-1.;bit=int(np.argmax(gains)) if len(available) else None
            fscore=max(0,float(gains[bit]))/cf if bit is not None else 0.
            sscore=-1.;pick=None
            for j in candidates:
                if method=='Dual':drop=excess[j]**2;improve=max(0.,float(potential[S]-potential[j]))
                else:
                    phi=lambda k:float(np.maximum(gaps[k,live]-epsilon,0.).sum())
                    drop=excess[j];improve=max(0.,phi(S)-phi(j))
                value=((1-q[j])*drop+q[j]*improve)/cs
                if value>sscore:sscore=value;pick=int(j)
            if pick is not None and (sscore>=fscore or not len(available)):action='screen';js=pick
            if method=='Cover-LP':
                if cache is None or step-last_plan_step>=4:
                    pool=list(feasible)+sorted(candidates,key=lambda j:-q[j]*values[j])[:4]
                    plans=[]
                    for j in pool:
                        st=status.copy();st[j]=1
                        effort,x,z=plan_cover(mask,int(j),L,U,n,mu,st,q,cf,cs,epsilon,M)
                        total=(effort+(cs if status[j]==-1 else 0.))/max(.01,q[j] if status[j]==-1 else 1.)
                        plans.append((total,int(j),x,z))
                    cache=min(plans,key=lambda a:(a[0],-values[a[1]]));last_plan_step=step
                effort,target,x,z=cache
                if np.isfinite(effort):
                    if status[target]==-1:action='screen';js=target;reason='minimum_expected_certificate_route'
                    elif x is not None:
                        S=target;excess=np.maximum(gaps[S]-epsilon,0.);excess[status==0]=0
                        pos=np.maximum(mask-mask[S],0);neg=np.maximum(mask[S]-mask,0)
                        contraction=(pos*(1-mu)+neg*mu)/(M*N)
                        gains=np.minimum(excess[:,None],batch*contraction).sum(axis=0)/batch
                        act=np.flatnonzero((x>1e-8)&(n<M))
                        if len(act):bit=int(act[np.argmax(gains[act])]);action='fault';reason='fractional_cover_label'
                        reject=np.flatnonzero((z>1e-8)&(status==-1))
                        if len(reject):
                            j=int(reject[np.argmax(z[reject]*excess[reject])])
                            if not len(act) or (1-q[j])*excess[j]/cs>gains[bit]/cf:
                                action='screen';js=j;reason='fractional_cover_screen'
        elif method in ['Alternating','Directed-Alternating']:
            if js is not None and (count==0 or since>0 or not len(available)):action='screen'
        elif 'Lazy' in method:
            if js is not None and (count==0 or since>=quota*cs or not len(available)):action='screen'
        if not len(available) and js is not None:action='screen'
        if action=='screen':
            algorithm+=time.perf_counter()-begin;screen(js)
            trace.append(dict(step=step,action=action,candidate=js,reason=reason,gap=G));continue
        if not len(available):raise RuntimeError('No available action before certification')
        if method=='Eager-Uniform':
            active=live[gaps[S,live]>epsilon+1e-12]
            relevant=np.flatnonzero(np.any(mask[active]!=mask[S],axis=0)&(n<M))
            if not len(relevant):relevant=available
            ids=relevant[n[relevant]==n[relevant].min()];bit=int(rng.choice(ids))
        elif method=='Cardinality-First':
            ex=np.maximum(gaps[S]-epsilon,0.);ex[status==0]=0
            a=(np.maximum(mask-mask[S],0)*(1-mu)+np.maximum(mask[S]-mask,0)*mu)/(M*N)
            g=np.minimum(ex[:,None],batch*a).sum(axis=0);g[n>=M]=-1
            bit=int(np.argmax(g))
        elif method not in ['Dual','Cover','Cover-LP']:
            if directed:
                impact=np.where(mask[S,diff]>mask[challenger,diff],mu[diff],1-mu[diff])
                ids=diff[np.isclose(impact,impact.max())]
            else:ids=diff[n[diff]==n[diff].min()]
            bit=int(rng.choice(ids))
        assert bit is not None and n[bit]<M
        b=min(batch,M-int(n[bit]));algorithm+=time.perf_counter()-begin
        labels,cost=service.fault(bit,int(n[bit]),b);f[bit]+=sum(labels);n[bit]+=b;since+=cost
        trace.append(dict(step=step,action='fault',bit=bit,count=b,failures=sum(labels),reason=reason,gap=G))
    raise RuntimeError('Action guard exhausted')
