/* Exact 0/1 knapsack, costs already rounded conservatively by the caller. */
#include <stdlib.h>
#include <string.h>
#include <math.h>
int solve_knapsack(int n, int capacity, int batches, const int *cost,
                   const double *values, unsigned char *selected, double *objectives) {
    if(n<1 || n>10000 || capacity<0 || capacity>100000 || batches<1) return 1;
    size_t width=(size_t)capacity+1;
    double *dp=(double*)calloc(width,sizeof(double));
    unsigned char *take=(unsigned char*)calloc((size_t)n*width,1);
    if(!dp || !take) {free(dp);free(take);return 2;}
    for(int i=0;i<n;i++) if(cost[i]<1) {free(dp);free(take);return 3;}
    for(int b=0;b<batches;b++) {
        memset(dp,0,width*sizeof(double));memset(take,0,(size_t)n*width);
        memset(selected+(size_t)b*n,0,n);
        for(int i=0;i<n;i++) {
            double value=values[(size_t)b*n+i];
            if(!isfinite(value)) {free(dp);free(take);return 4;}
            for(int c=capacity;c>=cost[i];c--) {
                double next=dp[c-cost[i]]+value;
                if(next>dp[c]+1e-12) {dp[c]=next;take[(size_t)i*width+c]=1;}
            }
        }
        objectives[b]=dp[capacity];int c=capacity;
        for(int i=n-1;i>=0;i--) if(take[(size_t)i*width+c]) {
            selected[(size_t)b*n+i]=1;c-=cost[i];
        }
    }
    free(dp);free(take);return 0;
}
