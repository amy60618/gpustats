import numpy as np

import gpustats.kernels as kernels
import gpustats.codegen as codegen
import gpustats.util as util
import pycuda.driver as drv
from pycuda.gpuarray import GPUArray, to_gpu
from pycuda.gpuarray import empty as gpu_empty
from pycuda.curandom import rand as curand

# reload(kernels)
# reload(codegen)

cu_module = codegen.get_full_cuda_module()

def sample_discrete(densities, logged=False,
                        return_gpuarray=False):

    """
    Takes a categorical sample from the unnormalized univariate
    densities defined in the rows of 'densities'

    Parameters
    ---------
    densities : ndarray or gpuarray (n, k)
    logged: boolean indicating whether densities is on the
    log scale ...

    Returns
    -------
    indices : ndarray or gpuarray (if return_gpuarray=True)
    of length n and dtype = int32
    """

    from gpustats.util import info

    n, k = densities.shape
    # prep data
    if isinstance(densities, GPUArray):
        if densities.flags.f_contiguous:
            gpu_densities = util.transpose(densities)
        else:
            gpu_densities = densities
    else:
        densities = util.prep_ndarray(densities)
        gpu_densities = to_gpu(densities)

    # get gpu function
    cu_func = cu_module.get_function('sample_discrete')

    # setup GPU data
    gpu_random = to_gpu(np.asarray(np.random.rand(n), dtype=np.float32))
    gpu_dest = gpu_empty(n, dtype=np.int32)
    dims = np.array([n,k,logged],dtype=np.int32)

    if info.max_block_threads<1024:
        x_block_dim = 16
    else:
        x_block_dim = 32

    y_block_dim = 16
    # setup GPU call
    block_design = (x_block_dim, y_block_dim, 1)
    grid_design = (int(n/y_block_dim) + 1, 1)

    shared_mem = 4 * ( (x_block_dim+1)*y_block_dim +  
                     2 * y_block_dim )  

    cu_func(gpu_densities, gpu_random, gpu_dest, 
            dims[0], dims[1], dims[2], 
            block=block_design, grid=grid_design, shared=shared_mem)

    gpu_random.gpudata.free()
    if return_gpuarray:
        return gpu_dest
    else:
        res = gpu_dest.get()
        gpu_dest.gpudata.free()
        return res


## depreciated 
def sample_discrete_old(in_densities, logged=False, pad=False,
                    return_gpuarray=False):
    """
    Takes a categorical sample from the unnormalized univariate
    densities defined in the rows of 'densities'

    Parameters
    ---------
    densities : ndarray or gpuarray (n, k)
    logged: boolean indicating whether densities is on the
    log scale ...

    Returns
    -------
    indices : ndarray or gpuarray (if return_gpuarray=True)
    of length n and dtype = int32
    """

    if pad:
        if logged:
            densities = util.pad_data_mult16(in_densities, fill=1)
        else:
            densities = util.pad_data_mult16(in_densities, fill=0)

    else:
        densities = in_densities

    n, k = densities.shape

    if logged:
        cu_func = cu_module.get_function('sample_discrete_logged_old')
    else:
        cu_func = cu_module.get_function('sample_discrete_old')

    if isinstance(densities, GPUArray):
        if densities.flags.f_contiguous:
            gpu_densities = util.transpose(densities)
        else:
            gpu_densities = densities
    else:
        densities = util.prep_ndarray(densities)
        gpu_densities = to_gpu(densities)

    # setup GPU data
    #gpu_random = curand(n)
    gpu_random = to_gpu(np.asarray(np.random.rand(n), dtype=np.float32))
    #gpu_dest = to_gpu(np.zeros(n, dtype=np.float32))
    gpu_dest = gpu_empty(n, dtype=np.float32)
    stride = gpu_densities.shape[1]
    if stride % 2 == 0:
        stride += 1
    dims = np.array([n,k, gpu_densities.shape[1], stride],dtype=np.int32)


    # optimize design ...
    grid_design, block_design = _tune_sfm(n, stride, cu_func.num_regs)

    shared_mem = 4 * (block_design[0] * stride + 
                     1 * block_design[0])

    cu_func(gpu_densities, gpu_random, gpu_dest, 
            dims[0], dims[1], dims[2], dims[3],
            block=block_design, grid=grid_design, shared=shared_mem)

    gpu_random.gpudata.free()
    if return_gpuarray:
        return gpu_dest
    else:
        res = gpu_dest.get()
        gpu_dest.gpudata.free()
        return res

def _tune_sfm(n, stride, func_regs):
    """
    Outputs the 'opimal' block and grid configuration
    for the sample discrete kernel.
    """
    from gpustats.util import info

    #info = DeviceInfo()
    comp_cap = info.compute_cap
    max_smem = info.shared_mem * 0.8
    max_threads = int(info.max_block_threads * 0.5)
    max_regs = 0.9 * info.max_registers

    # We want smallest dim possible in x dimsension while
    # still reading mem correctly

    if comp_cap[0] == 1:
        xdim = 16
    else:
        xdim = 32


    def sfm_config_ok(xdim, ydim, stride, func_regs, max_regs, max_smem, max_threads):
        ok = 4*(xdim*stride + 1*xdim) < max_smem and func_regs*ydim*xdim < max_regs
        return ok and xdim*ydim <= max_threads

    ydim = 2
    while sfm_config_ok(xdim, ydim, stride, func_regs, max_regs, max_smem, max_threads):
        ydim += 1

    ydim -= 1

    nblocks = int(n/xdim) + 1

    return (nblocks,1), (xdim,ydim,1)

if __name__ == '__main__':

    n = 100
    k = 5
    dens = np.log(np.abs(np.random.randn(k))) - 200
    densities = [dens.copy() for _ in range(n)]
    dens = np.exp(dens + 200)
    densities = np.asarray(densities)

    labels = sample_discrete(densities, logged=True)
    mu = np.dot(dens / dens.sum(), np.arange(k))
    print mu, labels.mean()
