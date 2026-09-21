import numpy as np
from scipy.special import ndtr


def probabilities(masks,observations,nominal_per_bit,nominal_count):
    sizes=masks.sum(axis=1);scale=max(1.,nominal_count)
    cardinal=(sizes[:,None]-sizes[None,:])/scale
    hamming=np.maximum(sizes[:,None]+sizes[None,:]-2*(masks@masks.T),0)/scale
    kernel=.01*(.5*np.exp(-.5*cardinal**2)+.5*np.exp(-.5*hamming))
    ids=np.array([r[0] for r in observations],int)
    area=np.array([r[1] for r in observations]);prior=sizes*nominal_per_bit
    gram=kernel[np.ix_(ids,ids)]+np.eye(len(ids))*1e-8
    cross=kernel[:,ids]
    chol=np.linalg.cholesky(gram)
    alpha=np.linalg.solve(chol.T,np.linalg.solve(chol,area-prior[ids]))
    mean=prior+cross@alpha
    v=np.linalg.solve(chol,cross.T)
    variance=np.maximum(np.diag(kernel)-np.sum(v*v,axis=0),1e-8)
    q=ndtr((.2-mean)/np.sqrt(variance))
    timing_factor=(1+sum(bool(r[2]) for r in observations))/(1+len(observations))
    return np.clip(q*timing_factor,.001,.999)
