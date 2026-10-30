#include <tl_templates/maca/gemm.h>
#include <tl_templates/maca/copy.h>
#include <tl_templates/maca/reduce.h>
#include <tl_templates/maca/intrin.h>
#include <tl_templates/maca/atomic.h>
#include <tl_templates/maca/threadblock_swizzle.h>
#include <tl_templates/maca/debug.h>

extern "C" __global__ void main_kernel(const half_t* __restrict__ K, float* __restrict__ Output_partial, const half_t* __restrict__ Q, const half_t* __restrict__ V, const int* __restrict__ block_indices, const int* __restrict__ block_table, const int* __restrict__ cache_seqlens, float* __restrict__ glse, int max_num_blocks_per_seq, int num_blocks, int num_split);
extern "C" __global__ void main_kernel_1(half_t* __restrict__ Output, const float* __restrict__ Output_partial, const float* __restrict__ glse, int num_split);
extern "C" __global__ void __launch_bounds__(128, 1) main_kernel(const half_t* __restrict__ K, float* __restrict__ Output_partial, const half_t* __restrict__ Q, const half_t* __restrict__ V, const int* __restrict__ block_indices, const int* __restrict__ block_table, const int* __restrict__ cache_seqlens, float* __restrict__ glse, int max_num_blocks_per_seq, int num_blocks, int num_split) {
  extern __shared__ __align__(1024) uchar buf_dyn_shmem[];
  float acc_o[64];
  float logsum[2];
  float scores_max[2];
  signed char has_valid_block = (signed char)0;
  float acc_s[8];
  float scores_max_prev[2];
  float scores_scale[2];
  float scores_sum[2];
  half_t acc_s_cast[8];
  float scores_max_clear[2];
  #pragma unroll
  for (int i = 0; i < 8; ++i) {
    half_t broadcast_var = half_t(0x0p+0f/*0.000000e+00*/);
    uint4 condval;
    if ((((((((int)threadIdx.x) >> 6) + ((int)blockIdx.y)) >> 1) + i) < 4)) {
      condval = *(uint4*)(Q + (((i * 1024) + (((int)blockIdx.y) * 512)) + (((int)threadIdx.x) * 8)));
    } else {
      condval = make_uint4(__pack_half2(broadcast_var, broadcast_var), __pack_half2(broadcast_var, broadcast_var), __pack_half2(broadcast_var, broadcast_var), __pack_half2(broadcast_var, broadcast_var));
    }
    *(uint4*)(((half_t*)buf_dyn_shmem) + ((((((((((int)threadIdx.x) & 15) >> 3) * 4096) + (i * 512)) + ((((int)threadIdx.x) >> 4) * 64)) + ((((((int)threadIdx.x) >> 6) + ((((int)threadIdx.x) & 7) >> 2)) & 1) * 32)) + (((((((int)threadIdx.x) & 63) >> 5) + ((((int)threadIdx.x) & 3) >> 1)) & 1) * 16)) + (((((((int)threadIdx.x) & 31) >> 4) + (((int)threadIdx.x) & 1)) & 1) * 8))) = condval;
  }
  #pragma unroll
  for (int i_1 = 0; i_1 < 16; ++i_1) {
    float broadcast_var_1 = 0x0p+0f/*0.000000e+00*/;
    *(float4*)(acc_o + (i_1 * 4)) = make_float4(broadcast_var_1, broadcast_var_1, broadcast_var_1, broadcast_var_1);
  }
  float broadcast_var_2 = 0x0p+0f/*0.000000e+00*/;
  *(float2*)(logsum + 0) = make_float2(broadcast_var_2, broadcast_var_2);
  float broadcast_var_3 = -MACART_INF_F;
  *(float2*)(scores_max + 0) = make_float2(broadcast_var_3, broadcast_var_3);
  has_valid_block = (signed char)0;
  int condval_1;
  if ((((int)blockIdx.z) < (num_blocks % num_split))) {
    condval_1 = 1;
  } else {
    condval_1 = 0;
  }
  for (int k = 0; k < ((num_blocks / num_split) + condval_1); ++k) {
    int condval_2;
    if ((((min(((int)blockIdx.z), (num_blocks % num_split)) + ((num_blocks / num_split) * ((int)blockIdx.z))) + k) < num_blocks)) {
      condval_2 = block_indices[(((min(((int64_t)((int)blockIdx.z)), (((int64_t)num_blocks) % ((int64_t)num_split))) + (((int64_t)((int)blockIdx.y)) * ((int64_t)num_blocks))) + ((((int64_t)num_blocks) / ((int64_t)num_split)) * ((int64_t)((int)blockIdx.z)))) + ((int64_t)k))];
    } else {
      condval_2 = 0;
    }
    int block_table_idx = condval_2;
    if (0 <= block_table_idx) {
      has_valid_block = (signed char)1;
      int condval_3;
      if ((block_table_idx < max_num_blocks_per_seq)) {
        condval_3 = block_table[((int64_t)block_table_idx)];
      } else {
        condval_3 = 0;
      }
      int physical_block_idx = condval_3;
      #pragma unroll
      for (int i_2 = 0; i_2 < 2; ++i_2) {
        half_t broadcast_var_4 = half_t(0x0p+0f/*0.000000e+00*/);
        uint4 condval_4;
        if (((0 <= physical_block_idx) && (physical_block_idx < 8))) {
          condval_4 = *(uint4*)(K + (((((((int64_t)physical_block_idx) * (int64_t)16384) + (((int64_t)i_2) * (int64_t)8192)) + ((((int64_t)((int)threadIdx.x)) >> (int64_t)4) * (int64_t)1024)) + (((int64_t)((int)blockIdx.y)) * (int64_t)128)) + ((((int64_t)((int)threadIdx.x)) & (int64_t)15) * (int64_t)8)));
        } else {
          condval_4 = make_uint4(__pack_half2(broadcast_var_4, broadcast_var_4), __pack_half2(broadcast_var_4, broadcast_var_4), __pack_half2(broadcast_var_4, broadcast_var_4), __pack_half2(broadcast_var_4, broadcast_var_4));
        }
        *(uint4*)(((half_t*)buf_dyn_shmem) + (((((((((((int)threadIdx.x) & 15) >> 3) * 1024) + (i_2 * 512)) + ((((int)threadIdx.x) >> 4) * 64)) + ((((((int)threadIdx.x) >> 6) + ((((int)threadIdx.x) & 7) >> 2)) & 1) * 32)) + (((((((int)threadIdx.x) & 63) >> 5) + ((((int)threadIdx.x) & 3) >> 1)) & 1) * 16)) + (((((((int)threadIdx.x) & 31) >> 4) + (((int)threadIdx.x) & 1)) & 1) * 8)) + 8192)) = condval_4;
      }
      #pragma unroll
      for (int i_3 = 0; i_3 < 2; ++i_3) {
        float broadcast_var_5 = 0x0p+0f/*0.000000e+00*/;
        *(float4*)(acc_s + (i_3 * 4)) = make_float4(broadcast_var_5, broadcast_var_5, broadcast_var_5, broadcast_var_5);
      }
      half_t A_local[8];
      half_t B_local[4];
      __syncthreads();
      for (int ki = 0; ki < 8; ++ki) {
        for (int i_4 = 0; i_4 < 2; ++i_4) {
          *(uint2*)(A_local + (i_4 * 4)) = *(uint2*)(((half_t*)buf_dyn_shmem) + (((((((((ki >> 2) * 4096) + ((((int)threadIdx.x) >> 6) * 2048)) + (i_4 * 1024)) + ((((int)threadIdx.x) & 15) * 64)) + (((((((int)threadIdx.x) & 7) >> 2) + ((ki & 3) >> 1)) & 1) * 32)) + (((((((int)threadIdx.x) & 3) >> 1) + (ki & 1)) & 1) * 16)) + (((((((int)threadIdx.x) & 63) >> 5) + (((int)threadIdx.x) & 1)) & 1) * 8)) + (((((int)threadIdx.x) & 31) >> 4) * 4)));
        }
        *(uint2*)(B_local + 0) = *(uint2*)(((half_t*)buf_dyn_shmem) + ((((((((ki >> 2) * 1024) + ((((int)threadIdx.x) & 15) * 64)) + (((((((int)threadIdx.x) & 7) >> 2) + ((ki & 3) >> 1)) & 1) * 32)) + (((((((int)threadIdx.x) & 3) >> 1) + (ki & 1)) & 1) * 16)) + (((((((int)threadIdx.x) & 63) >> 5) + (((int)threadIdx.x) & 1)) & 1) * 8)) + (((((int)threadIdx.x) & 31) >> 4) * 4)) + 8192));
        for (int i_5 = 0; i_5 < 2; ++i_5) {
          {
      *(((float32x4*)acc_s) + i_5) = __builtin_mxc_mma_16x16x16f16(*(((float16x4*)B_local) + 0),
                    *(((float16x4*)A_local) + i_5),
                    *(((float32x4*)acc_s) + i_5));
    };
        }
      }
      if (k == 0) {
        #pragma unroll
        for (int i_6 = 0; i_6 < 2; ++i_6) {
          for (int vec_s = 0; vec_s < 4; ++vec_s) {
            float condval_5;
            if ((cache_seqlens[0] <= (((block_table_idx * 16) + (((((int)threadIdx.x) & 63) >> 4) * 4)) + vec_s))) {
              condval_5 = -MACART_INF_F;
            } else {
              condval_5 = acc_s[((i_6 * 4) + vec_s)];
            }
            acc_s[((i_6 * 4) + vec_s)] = condval_5;
          }
        }
      }
      *(float2*)(scores_max_prev + 0) = *(float2*)(scores_max + 0);
      float broadcast_var_6 = -MACART_INF_F;
      *(float2*)(scores_max + 0) = make_float2(broadcast_var_6, broadcast_var_6);
      __syncthreads();
      #pragma unroll
      for (int i_7 = 0; i_7 < 2; ++i_7) {
        scores_max_clear[i_7] = -MACART_INF_F;
        #pragma unroll
        for (int rv = 0; rv < 4; ++rv) {
          scores_max_clear[i_7] = max(scores_max_clear[i_7], acc_s[((i_7 * 4) + rv)]);
        }
        scores_max_clear[i_7] = tl::AllReduce<tl::MaxOp, 64, 16, 0>::run(scores_max_clear[i_7], (&(((float*)buf_dyn_shmem)[6272])));
        scores_max[i_7] = max(scores_max[i_7], scores_max_clear[i_7]);
      }
      #pragma unroll
      for (int i_8 = 0; i_8 < 2; ++i_8) {
        scores_max[i_8] = max(scores_max[i_8], scores_max_prev[i_8]);
        scores_scale[i_8] = exp2f(((scores_max_prev[i_8] * 0x1.0527dbd5cafffp-3f/*1.275174e-01*/) - (scores_max[i_8] * 0x1.0527dbd5cafffp-3f/*1.275174e-01*/)));
      }
      #pragma unroll
      for (int i_9 = 0; i_9 < 8; ++i_9) {
        acc_s[i_9] = exp2f(((acc_s[i_9] * 0x1.0527dbd5cafffp-3f/*1.275174e-01*/) - (scores_max[(i_9 >> 2)] * 0x1.0527dbd5cafffp-3f/*1.275174e-01*/)));
      }
      __syncthreads();
      #pragma unroll
      for (int i_10 = 0; i_10 < 2; ++i_10) {
        scores_sum[i_10] = 0x0p+0f/*0.000000e+00*/;
        #pragma unroll
        for (int rv_1 = 0; rv_1 < 4; ++rv_1) {
          scores_sum[i_10] = (scores_sum[i_10] + acc_s[((i_10 * 4) + rv_1)]);
        }
        scores_sum[i_10] = tl::AllReduce<tl::SumOp, 64, 16, 0>::run(scores_sum[i_10], (&(((float*)buf_dyn_shmem)[6144])));
      }
      #pragma unroll
      for (int i_11 = 0; i_11 < 2; ++i_11) {
        logsum[i_11] = ((logsum[i_11] * scores_scale[i_11]) + scores_sum[i_11]);
      }
      #pragma unroll
      for (int i_12 = 0; i_12 < 2; ++i_12) {
        uint2 __1;
        float4 v_ = *(float4*)(acc_s + (i_12 * 4));
        ((half2*)(&__1))[0] = __float22half2_rn(((float2*)(&v_))[0]);
        ((half2*)(&__1))[1] = __float22half2_rn(((float2*)(&v_))[1]);
        *(uint2*)(acc_s_cast + (i_12 * 4)) = __1;
      }
      #pragma unroll
      for (int i_13 = 0; i_13 < 64; ++i_13) {
        acc_o[i_13] = (acc_o[i_13] * scores_scale[(i_13 >> 5)]);
      }
      __syncthreads();
      #pragma unroll
      for (int i_14 = 0; i_14 < 2; ++i_14) {
        half_t broadcast_var_7 = half_t(0x0p+0f/*0.000000e+00*/);
        uint4 condval_6;
        if (((0 <= physical_block_idx) && (physical_block_idx < 8))) {
          condval_6 = *(uint4*)(V + (((((((int64_t)physical_block_idx) * (int64_t)16384) + (((int64_t)i_14) * (int64_t)8192)) + ((((int64_t)((int)threadIdx.x)) >> (int64_t)4) * (int64_t)1024)) + (((int64_t)((int)blockIdx.y)) * (int64_t)128)) + ((((int64_t)((int)threadIdx.x)) & (int64_t)15) * (int64_t)8)));
        } else {
          condval_6 = make_uint4(__pack_half2(broadcast_var_7, broadcast_var_7), __pack_half2(broadcast_var_7, broadcast_var_7), __pack_half2(broadcast_var_7, broadcast_var_7), __pack_half2(broadcast_var_7, broadcast_var_7));
        }
        *(uint4*)(((half_t*)buf_dyn_shmem) + (((((((((((int)threadIdx.x) & 15) >> 3) * 1024) + (i_14 * 512)) + ((((int)threadIdx.x) >> 4) * 64)) + ((((((int)threadIdx.x) >> 6) + ((((int)threadIdx.x) & 7) >> 2)) & 1) * 32)) + (((((((int)threadIdx.x) & 63) >> 5) + ((((int)threadIdx.x) & 3) >> 1)) & 1) * 16)) + (((((((int)threadIdx.x) & 31) >> 4) + (((int)threadIdx.x) & 1)) & 1) * 8)) + 10240)) = condval_6;
      }
      half_t B_local_1[32];
      __syncthreads();
      for (int j = 0; j < 8; ++j) {
        for (int local_id = 0; local_id < 4; ++local_id) {
          B_local_1[((j * 4) + local_id)] = ((half_t*)buf_dyn_shmem)[(((((((((j >> 2) * 1024) + (((((int)threadIdx.x) & 63) >> 4) * 256)) + (local_id * 64)) + (((((((int)threadIdx.x) & 31) >> 4) + ((j & 3) >> 1)) & 1) * 32)) + ((((local_id >> 1) + (j & 1)) & 1) * 16)) + (((((((int)threadIdx.x) & 15) >> 3) + (local_id & 1)) & 1) * 8)) + (((int)threadIdx.x) & 7)) + 10240)];
        }
      }
      for (int i_15 = 0; i_15 < 2; ++i_15) {
        for (int j_1 = 0; j_1 < 8; ++j_1) {
          {
      *(((float32x4*)acc_o) + ((i_15 * 8) + j_1)) = __builtin_mxc_mma_16x16x16f16(*(((float16x4*)B_local_1) + j_1),
                    *(((float16x4*)acc_s_cast) + i_15),
                    *(((float32x4*)acc_o) + ((i_15 * 8) + j_1)));
    };
        }
      }
    }
  }
  if ((bool)has_valid_block) {
    #pragma unroll
    for (int i_16 = 0; i_16 < 64; ++i_16) {
      acc_o[i_16] = (acc_o[i_16] / logsum[(i_16 >> 5)]);
    }
    #pragma unroll
    for (int i_17 = 0; i_17 < 2; ++i_17) {
      logsum[i_17] = (log2f(logsum[i_17]) + (scores_max[i_17] * 0x1.0527dbd5cafffp-3f/*1.275174e-01*/));
    }
  }
  if (((((int)threadIdx.x) & 63) >> 4) == 0) {
    #pragma unroll
    for (int i_18 = 0; i_18 < 2; ++i_18) {
      if (((((((int)threadIdx.x) >> 6) * 8) + (i_18 * 4)) + ((((int)threadIdx.x) & 15) >> 2)) < 1) {
        glse[(((((((((int64_t)((int)threadIdx.x)) >> (int64_t)6) * (int64_t)32) + (((int64_t)i_18) * (int64_t)16)) + (((int64_t)((int)blockIdx.y)) * (int64_t)4)) + (((int64_t)((int)threadIdx.x)) & (int64_t)15)) * ((int64_t)num_split)) + ((int64_t)((int)blockIdx.z)))] = logsum[i_18];
      }
    }
  }
  #pragma unroll
  for (int i_19 = 0; i_19 < 16; ++i_19) {
    if (((((((int)threadIdx.x) >> 6) * 8) + ((i_19 >> 3) * 4)) + ((((int)threadIdx.x) & 15) >> 2)) < 1) {
      *(float4*)(Output_partial + ((((((((((((int64_t)((int)threadIdx.x)) >> (int64_t)6) * (int64_t)32) + ((((int64_t)i_19) >> (int64_t)3) * (int64_t)16)) + (((int64_t)((int)blockIdx.y)) * (int64_t)4)) + (((int64_t)((int)threadIdx.x)) & (int64_t)15)) * ((int64_t)num_split)) * (int64_t)128) + (((int64_t)((int)blockIdx.z)) * (int64_t)128)) + ((((int64_t)i_19) & (int64_t)7) * (int64_t)16)) + (((((int64_t)((int)threadIdx.x)) & (int64_t)63) >> (int64_t)4) * (int64_t)4))) = *(float4*)(acc_o + (i_19 * 4));
    }
  }
}

extern "C" __global__ void __launch_bounds__(128, 1) main_kernel_1(half_t* __restrict__ Output, const float* __restrict__ Output_partial, const float* __restrict__ glse, int num_split) {
  float lse_logsum_local = 0x0p+0f/*0.000000e+00*/;
  float o_accum_local[1];
  float lse_max_local = 0x0p+0f/*0.000000e+00*/;
  float lse_local_split = 0x0p+0f/*0.000000e+00*/;
  int max_split = 0;
  float po_local[1];
  float scale_local = 0x0p+0f/*0.000000e+00*/;
  lse_logsum_local = 0x0p+0f/*0.000000e+00*/;
  o_accum_local[0] = 0x0p+0f/*0.000000e+00*/;
  lse_max_local = -MACART_INF_F;
  for (int k = 0; k < num_split; ++k) {
    lse_local_split = glse[((((int64_t)((int)blockIdx.x)) * ((int64_t)num_split)) + ((int64_t)k))];
    if (lse_local_split != 0x0p+0f/*0.000000e+00*/) {
      max_split = k;
      lse_max_local = max(lse_max_local, glse[((((int64_t)((int)blockIdx.x)) * ((int64_t)num_split)) + ((int64_t)k))]);
    }
  }
  for (int k_1 = 0; k_1 < num_split; ++k_1) {
    if (k_1 <= max_split) {
      lse_local_split = glse[((((int64_t)((int)blockIdx.x)) * ((int64_t)num_split)) + ((int64_t)k_1))];
      lse_logsum_local = (lse_logsum_local + exp2f((lse_local_split - lse_max_local)));
    }
  }
  lse_logsum_local = (log2f(lse_logsum_local) + lse_max_local);
  for (int k_2 = 0; k_2 < num_split; ++k_2) {
    if (k_2 <= max_split) {
      po_local[0] = Output_partial[((((((int64_t)((int)blockIdx.x)) * ((int64_t)num_split)) * (int64_t)128) + (((int64_t)k_2) * (int64_t)128)) + ((int64_t)((int)threadIdx.x)))];
      lse_local_split = glse[((((int64_t)((int)blockIdx.x)) * ((int64_t)num_split)) + ((int64_t)k_2))];
      scale_local = exp2f((lse_local_split - lse_logsum_local));
      o_accum_local[0] = (o_accum_local[0] + (po_local[0] * scale_local));
    }
  }
  Output[((((int)blockIdx.x) * 128) + ((int)threadIdx.x))] = ((half_t)o_accum_local[0]);
}

