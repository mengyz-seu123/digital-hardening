import ctypes, itertools, json, math, time
from pathlib import Path
import numpy as np
import hardware as h


class Oracle:
    def __init__(self, costs, budget, quantum=0.1):
        self.raw=np.asarray(costs,dtype=float);self.quantum=quantum;self.budget=budget
        self.cost=np.maximum(1,np.ceil(self.raw/quantum-1e-10)).astype(np.int32)
        self.capacity=int(math.floor(budget/quantum+1e-10));self.n=len(costs)
        self.lib=ctypes.CDLL(str(Path(__file__).with_name('knapsack.so')))
        self.fn=self.lib.solve_knapsack
        self.fn.argtypes=[ctypes.c_int]*3+[ctypes.c_void_p]*4;self.fn.restype=ctypes.c_int
        self.calls=0;self.seconds=0.

    def batch(self, values):
        v=np.ascontiguousarray(values,dtype=np.float64).reshape(-1,self.n)
        selected=np.zeros(v.shape,dtype=np.uint8);objectives=np.zeros(len(v),dtype=np.float64)
        begin=time.perf_counter()
        rc=self.fn(self.n,self.capacity,len(v),self.cost.ctypes.data,v.ctypes.data,
            selected.ctypes.data,objectives.ctypes.data)
        self.seconds+=time.perf_counter()-begin;self.calls+=len(v)
        if rc:raise RuntimeError('knapsack failure %d'%rc)
        assert np.all(selected@self.cost<=self.capacity)
        assert np.all(selected@self.raw<=self.budget+1e-8)
        return selected.astype(bool),objectives

    def __call__(self, values):
        selected,obj=self.batch(values);return selected[0],float(obj[0])


def self_test(path):
    rng=np.random.default_rng(20260910);cases=0
    for _ in range(100):
        n=int(rng.integers(2,10));cost=rng.integers(1,10,n);budget=int(rng.integers(1,25))
        values=rng.normal(0.3,0.5,(5,n));oracle=Oracle(cost,budget,1)
        masks=np.array(list(itertools.product([False,True],repeat=n)))
        valid=masks@(cost)<=budget
        best=(masks[valid]@values.T).max(axis=0)
        selected,obj=oracle.batch(values)
        assert np.allclose(obj,best,atol=1e-10),(cost,budget,obj,best)
        assert np.allclose(np.sum(selected*values,axis=1),best,atol=1e-10)
        cases+=len(values)
    h.dump(path,dict(status='passed',exhaustive_comparisons=cases,negative_scores_tested=True,
        source_sha256=h.sha(Path(__file__).with_name('knapsack.c')),binary_sha256=h.sha(Path(__file__).with_name('knapsack.so'))))
    print('ORACLE_EXHAUSTIVE_PASS',cases,flush=True)


if __name__=='__main__':self_test(h.PROJECT/'runs/pilot-20260910-01/oracle_tests.json')
