# GPU 활용률 저조 원인 분석 및 개선 방안

**작성일**: 2026-01-21
**환경**: RTX 4090 (24GB VRAM), PaddleOCR, Python 3.10

---

## 현재 상황

- GPU 활용률: **0~5%** (거의 유휴 상태)
- GPU 메모리: **2MiB / 24564MiB** 사용
- 배치 크기: 8장
- 병렬 워커: 4개

배치 처리를 적용했음에도 GPU가 거의 활용되지 않는 상황.

---

## 원인 분석

### 1. PaddleOCR 내부 파이프라인 구조

PaddleOCR은 단일 이미지에 대해서도 **3단계 순차 처리**:

```
[Text Detection] → [Direction Classification] → [Text Recognition]
     GPU              GPU (optional)                 GPU
```

- 각 단계가 **동기적**으로 실행됨
- 배치로 이미지를 넣어도 내부적으로 순차 처리될 수 있음
- Detection 결과(bounding boxes)가 Recognition 입력이 되므로 파이프라인 병렬화 어려움

### 2. OCR 모델 특성

```
모델 크기 vs 연산량:
- PaddleOCR 모델: ~10-50MB (경량)
- 추론 시간: 이미지당 50-200ms
- GPU 점유 시간: 실제로는 수 ms
```

**문제**: 모델이 가벼워서 GPU 연산 자체가 빠르게 끝남. 대부분의 시간은:
- CPU에서 이미지 전처리
- GPU↔CPU 메모리 전송
- Python 오버헤드

### 3. I/O 바운드 워크로드

```
시간 분배 (추정):
├── PDF 읽기 + 페이지 렌더링: 60-70%  (CPU, Disk I/O)
├── 이미지 전처리: 15-20%             (CPU)
├── GPU 추론: 5-10%                   (GPU) ← 여기만 GPU
└── 후처리 + 결과 저장: 10-15%        (CPU)
```

GPU가 일하는 시간이 전체의 **5-10%**에 불과.

### 4. Python GIL 제약

```python
# ThreadPoolExecutor 사용 시
# → I/O 병렬화는 되지만, CPU 연산은 여전히 순차

# 현재 구조
Thread 1: [PDF 로드] [전처리] [OCR] [후처리]
Thread 2:           [PDF 로드] [전처리] [OCR] [후처리]
                    ↑ GIL 경합으로 실제론 순차
```

### 5. 동기적 배치 처리

```python
# 현재 (추정)
batch_images = load_batch()      # CPU 대기
results = ocr.ocr(batch_images)  # GPU 실행
save_results(results)            # CPU 대기
# → GPU는 ocr() 호출 중에만 활성화
```

GPU가 일하는 동안 다음 배치를 준비하지 않음.

---

## 근본적 한계

### OCR 워크로드의 특성

| 작업 | 자원 | 비중 |
|------|------|------|
| PDF 파싱 | CPU + Disk | 높음 |
| 이미지 렌더링 | CPU + Memory | 높음 |
| 전처리 (resize, normalize) | CPU | 중간 |
| **텍스트 검출** | **GPU** | **낮음** |
| **텍스트 인식** | **GPU** | **낮음** |
| 후처리 | CPU | 낮음 |

**결론**: OCR은 본질적으로 **I/O + CPU 바운드** 워크로드.
GPU는 "가속기" 역할일 뿐, 전체 파이프라인의 병목이 아님.

### vs. 다른 GPU 워크로드 비교

| 워크로드 | GPU 활용률 | 이유 |
|----------|------------|------|
| LLM 추론 | 80-100% | 대규모 행렬 연산, 긴 시퀀스 |
| 이미지 생성 (SD) | 70-95% | 반복적 diffusion step |
| 비디오 인코딩 | 60-90% | 연속 프레임 처리 |
| **OCR** | **5-20%** | 작은 모델, I/O 병목 |

---

## 개선 방안 (효과순)

### 1. CPU 병목 해소가 우선 (★★★)

GPU를 더 쓰기보다 **CPU 파이프라인 최적화**가 효과적:

```python
# 멀티프로세싱으로 PDF→이미지 변환 병렬화
from multiprocessing import Pool

def pdf_to_images(pdf_path):
    # fitz로 모든 페이지 이미지화
    return images

with Pool(8) as p:  # CPU 코어 활용
    all_images = p.map(pdf_to_images, pdf_files)
```

### 2. 비동기 파이프라인 (★★★)

```
[Loader Process] → [Image Queue] → [OCR Process] → [Result Queue] → [Saver Process]
     CPU×4              ↓               GPU              ↓              CPU×2
                   버퍼 32장                          버퍼 32장
```

- GPU가 처리하는 동안 다음 배치 미리 로딩
- 결과 저장도 비동기로

### 3. 배치 크기 증가 (★★☆)

```python
# 8 → 32 또는 64
# VRAM 24GB면 충분히 가능
batch_size = 32
```

단, 효과 제한적 (이미 배치해도 GPU 노는 상황)

### 4. 이미지 해상도 조정 (★★☆)

```python
# DPI 300 → 200으로 낮추면
# - 이미지 크기 44% 감소
# - 처리 속도 향상
# - 품질은 OCR에 충분
```

### 5. 다중 GPU 스트림 (★☆☆)

```python
# PaddlePaddle CUDA 스트림 활용
# 효과 제한적 - 모델이 작아서
```

### 6. C++ 백엔드 직접 호출 (★☆☆)

```python
# Python 오버헤드 제거
# 구현 복잡도 높음
```

---

## 현실적 권장사항

### 단기 (즉시 적용 가능)

1. **DPI 200으로 낮추기** - 품질 유지하면서 속도 향상
2. **멀티프로세싱으로 이미지 로딩** - GIL 우회
3. **더 큰 배치 (32장)** - VRAM 여유 있음

### 중기 (리팩토링 필요)

4. **Producer-Consumer 패턴** - 비동기 파이프라인
5. **메모리 매핑** - 대용량 PDF 효율적 처리

### 수용해야 할 현실

> **GPU 활용률 20-30%가 OCR 워크로드의 현실적 상한선.**
>
> 100% 활용을 목표로 하기보다, **전체 처리량(throughput)**에 집중하는 것이 합리적.

---

## 벤치마크 제안

현재 상태와 개선 후를 비교하려면:

```bash
# 측정 지표
1. 문서당 처리 시간 (초/문서)
2. 페이지당 처리 시간 (초/페이지)
3. GPU 활용률 평균 (%)
4. CPU 활용률 평균 (%)
5. 메모리 사용량 피크 (GB)
```

```python
# 프로파일링 코드
import time
import psutil
import GPUtil

def benchmark_pipeline(input_dir, output_dir, batch_size):
    start = time.time()

    # ... 파이프라인 실행 ...

    elapsed = time.time() - start
    gpu = GPUtil.getGPUs()[0]

    return {
        "total_time": elapsed,
        "docs_per_sec": num_docs / elapsed,
        "gpu_util_avg": gpu.load * 100,
        "gpu_mem_used": gpu.memoryUsed,
    }
```

---

## 결론

| 관점 | 현재 | 개선 후 (예상) |
|------|------|--------------|
| GPU 활용률 | 0-5% | 15-25% |
| 처리 속도 | baseline | 1.5-2x |
| 병목 | I/O, CPU | 여전히 I/O, CPU |

**핵심 인사이트**:
- GPU를 100% 쓰는 것이 목표가 아님
- **처리량 최대화**가 진짜 목표
- CPU/I/O 최적화가 GPU 최적화보다 효과적

RTX 4090의 진정한 가치는 OCR보다 **LLM 추론**(구조화 단계)에서 발휘될 것.
