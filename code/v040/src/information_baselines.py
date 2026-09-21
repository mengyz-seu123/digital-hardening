import numpy as np
from scipy.special import xlogy

def posterior(mask,n,f,status,observations,service,rng,M=32,draws=128):
    sizes=mask.sum(axis=1);scale=max(1.,service.nominal_tmr_bits)
    distance=(sizes[:,None]+sizes[None,:]-2*mask@mask.T)/scale
    kernel=.01*(.5*np.exp(-.5*((sizes[:,None]-sizes[None,:])/scale)**2)+.5*np.exp(-.5*np.maximum(0,distance)))
    ids=np.array([r[0] for r in observations]);area=np.array([r[1] for r in observations])
    prior=sizes*service.area_prior_per_bit;gram=kernel[np.ix_(ids,ids)]+np.eye(len(ids))*1e-8
    chol=np.linalg.cholesky(gram);cross=kernel[:,ids]
    alpha=np.linalg.solve(chol.T,np.linalg.solve(chol,area-prior[ids]));mean=prior+cross@alpha
    v=np.linalg.solve(chol,cross.T);cov=kernel-v.T@v;cov=(cov+cov.T)/2+np.eye(len(mask))*1e-8
    areas=mean+rng.normal(size=(draws,len(mask)))@np.linalg.cholesky(cov).T
    possible=areas<=.2;possible[:,status==1]=True;possible[:,status==0]=False
    theta=rng.beta(f+1,n-f+1,size=(draws,len(n)))
    missing=rng.binomial(M-n,theta);p=(f+missing)/M
    label_p=np.divide(missing,M-n,out=np.zeros_like(p),where=(M-n)>0)
    values=p@mask.T/mask.shape[1]
    winner=np.argmax(np.where(possible,values,-1),axis=1)
    return label_p,possible.astype(float),values,winner

def binary_entropy(p):
    p=np.clip(p,1e-12,1-1e-12)
    return -xlogy(p,p)-xlogy(1-p,1-p)

def information_scores(kind,mask,n,f,status,observations,service,rng):
    theta,feas,values,winner=posterior(mask,n,f,status,observations,service,rng)
    probabilities=np.concatenate([theta,feas],axis=1);mu=probabilities.mean(axis=0)
    if kind=='MC-Entropy':
        conditional=np.zeros(probabilities.shape[1])
        for j in np.unique(winner):
            rows=winner==j;conditional+=rows.mean()*binary_entropy(probabilities[rows].mean(axis=0))
        score=np.maximum(0,binary_entropy(mu)-conditional)
    else:
        reward=feas*values;base=reward.mean(axis=0);cross=reward.T@probabilities/len(theta)
        plus=cross/np.maximum(mu,1e-12);minus=(base[:,None]-cross)/np.maximum(1-mu,1e-12)
        score=np.maximum(0,mu*plus.max(axis=0)+(1-mu)*minus.max(axis=0)-base.max())
    return score[:len(n)],score[len(n):]
