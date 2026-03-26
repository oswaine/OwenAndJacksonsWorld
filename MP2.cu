#include <stdio.h>
#include <stdlib.h>
#include <math.h>
#include <cuda_runtime.h>
#include <time.h>

#define TOLERANCE 1e-3

void reportDeviceThreadCapacity();
void reportKernelResources(int TILE_WIDTH);
void runResourceReport();

__global__
void TiledMatrixMulKernel(float* M, float* N, float* P, int Width, int TILE_WIDTH)
{
    extern __shared__ float shared[];

    float* Mds = shared;
    float* Nds = &shared[TILE_WIDTH * TILE_WIDTH];

    int tx = threadIdx.x;
    int ty = threadIdx.y;

    int Row = blockIdx.y * blockDim.y + ty;
    int Col = blockIdx.x * blockDim.x + tx;

    float Pvalue = 0.0f;

    int numPhases = (Width + TILE_WIDTH - 1) / TILE_WIDTH;

    for (int ph = 0; ph < numPhases; ph++)
    {
        int mCol = ph * TILE_WIDTH + tx;
        int nRow = ph * TILE_WIDTH + ty;

        if (Row < Width && mCol < Width)
            Mds[ty * TILE_WIDTH + tx] = M[Row * Width + mCol];
        else
            Mds[ty * TILE_WIDTH + tx] = 0.0f;

        if (nRow < Width && Col < Width)
            Nds[ty * TILE_WIDTH + tx] = N[nRow * Width + Col];
        else
            Nds[ty * TILE_WIDTH + tx] = 0.0f;

        __syncthreads();

        for (int k = 0; k < TILE_WIDTH; k++)
            Pvalue += Mds[ty * TILE_WIDTH + k] * Nds[k * TILE_WIDTH + tx];

        __syncthreads();
    }

    if (Row < Width && Col < Width)
        P[Row * Width + Col] = Pvalue;
}

void cpuMatrixMul(float* M, float* N, float* P, int Width)
{
    for (int i = 0; i < Width; i++)
    {
        for (int j = 0; j < Width; j++)
        {
            float sum = 0.0f;
            for (int k = 0; k < Width; k++)
                sum += M[i * Width + k] * N[k * Width + j];
            P[i * Width + j] = sum;
        }
    }
}

int compareMatrices(float* A, float* B, int size)
{
    for (int i = 0; i < size; i++)
    {
        if (fabs(A[i] - B[i]) > TOLERANCE)
        {
            printf("Mismatch at index %d: CPU = %f, GPU = %f\n", i, A[i], B[i]);
            return 0;
        }
    }
    return 1;
}

float timeGPUTiled(float* d_M, float* d_N, float* d_P, int Width, int TILE_WIDTH)
{
    int NumBlocks = (Width + TILE_WIDTH - 1) / TILE_WIDTH;

    dim3 dimGrid(NumBlocks, NumBlocks);
    dim3 dimBlock(TILE_WIDTH, TILE_WIDTH);

    size_t sharedMemSize = 2 * TILE_WIDTH * TILE_WIDTH * sizeof(float);

    cudaEvent_t start, stop;
    cudaEventCreate(&start);
    cudaEventCreate(&stop);

    cudaEventRecord(start);
    TiledMatrixMulKernel<<<dimGrid, dimBlock, sharedMemSize>>>(d_M, d_N, d_P, Width, TILE_WIDTH);
    cudaEventRecord(stop);
    cudaEventSynchronize(stop);

    float ms;
    cudaEventElapsedTime(&ms, start, stop);

    cudaEventDestroy(start);
    cudaEventDestroy(stop);

    return ms;
}

void runMP2()
{
    int sizes[] = {300, 750, 1500, 3000, 4500};
    int tileWidths[] = {2, 5, 10, 15, 25};

    printf("Matrix Size, Tile Width, Kernel Time (ms)\n");

    for (int s = 0; s < 5; s++)
    {
        int Width = sizes[s];
        int numElements = Width * Width;
        int size = numElements * sizeof(float);

        float* h_M = (float*)malloc(size);
        float* h_N = (float*)malloc(size);
        float* h_P_gpu = (float*)malloc(size);
        float* h_P_cpu = (float*)malloc(size);

        for (int i = 0; i < numElements; i++)
        {
            h_M[i] = (float)rand() / RAND_MAX;
            h_N[i] = (float)rand() / RAND_MAX;
        }

        float *d_M, *d_N, *d_P;
        cudaMalloc((void**)&d_M, size);
        cudaMalloc((void**)&d_N, size);
        cudaMalloc((void**)&d_P, size);

        cudaMemcpy(d_M, h_M, size, cudaMemcpyHostToDevice);
        cudaMemcpy(d_N, h_N, size, cudaMemcpyHostToDevice);

        cpuMatrixMul(h_M, h_N, h_P_cpu, Width);

        for (int t = 0; t < 5; t++)
        {
            int TILE_WIDTH = tileWidths[t];

            float kernelTime = timeGPUTiled(d_M, d_N, d_P, Width, TILE_WIDTH);
            cudaMemcpy(h_P_gpu, d_P, size, cudaMemcpyDeviceToHost);

            int pass = compareMatrices(h_P_cpu, h_P_gpu, numElements);

            printf("%d x %d, %d, %.4f", Width, Width, TILE_WIDTH, kernelTime);
            if (pass)
                printf(", Test PASSED\n");
            else
                printf(", Test FAILED\n");
        }

        cudaFree(d_M);
        cudaFree(d_N);
        cudaFree(d_P);

        free(h_M);
        free(h_N);
        free(h_P_gpu);
        free(h_P_cpu);
    }
}

void reportDeviceThreadCapacity()
{
    cudaDeviceProp dp;
    cudaGetDeviceProperties(&dp, 0);

    int maxThreadsPerSM = dp.maxThreadsPerMultiProcessor;
    int totalMaxThreads = dp.multiProcessorCount * maxThreadsPerSM;

    printf("\nPart (a)\n");
    printf("GPU Name: %s\n", dp.name);
    printf("Streaming Multiprocessors (SMs): %d\n", dp.multiProcessorCount);
    printf("Max Threads per SM: %d\n", maxThreadsPerSM);
    printf("Max Threads Simultaneously Scheduled on Device: %d\n", totalMaxThreads);
}

void reportKernelResources(int TILE_WIDTH)
{
    cudaDeviceProp dp;
    cudaGetDeviceProperties(&dp, 0);

    cudaFuncAttributes attr;
    cudaFuncGetAttributes(&attr, TiledMatrixMulKernel);

    int threadsPerBlock = TILE_WIDTH * TILE_WIDTH;
    size_t dynamicSharedMem = 2 * TILE_WIDTH * TILE_WIDTH * sizeof(float);

    int blocksPerSM = 0;
    cudaOccupancyMaxActiveBlocksPerMultiprocessor(
        &blocksPerSM,
        TiledMatrixMulKernel,
        threadsPerBlock,
        dynamicSharedMem
    );

    int threadsPerSMForKernel = blocksPerSM * threadsPerBlock;
    int totalThreadsOnDeviceForKernel = threadsPerSMForKernel * dp.multiProcessorCount;

    printf("\nTILE_WIDTH = %d\n", TILE_WIDTH);
    printf("Threads per Block: %d\n", threadsPerBlock);
    printf("Registers per Thread: %d\n", attr.numRegs);
    printf("Static Shared Memory per Block: %zu bytes\n", attr.sharedSizeBytes);
    printf("Dynamic Shared Memory per Block: %zu bytes\n", dynamicSharedMem);
    printf("Total Shared Memory per Block: %zu bytes\n",
           attr.sharedSizeBytes + dynamicSharedMem);
    printf("Active Blocks per SM: %d\n", blocksPerSM);
    printf("Max Threads Simultaneously Scheduled per SM for this Kernel: %d\n",
           threadsPerSMForKernel);
    printf("Max Threads Simultaneously Scheduled on Device for this Kernel: %d\n",
           totalThreadsOnDeviceForKernel);
}

void runResourceReport()
{
    int tileWidths[] = {2, 5, 10, 15, 25};

    reportDeviceThreadCapacity();

    printf("\nPart (b)\n");
    for (int i = 0; i < 5; i++)
    {
        reportKernelResources(tileWidths[i]);
    }
}

int main()
{
    srand(0);
    runMP2();
    runResourceReport();
    return 0;
}