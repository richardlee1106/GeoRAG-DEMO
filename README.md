# GeoRAG-demo 🌐🧠

> 基于 SearXNG 联网搜索 + 本地 LLM 的 RAG Web UI 演示项目。
> Search → Extract → Embed → Rerank → Generate，全流程本地部署。

---

## 架构概览

```
用户浏览器 ──→  GeoRAG-demo (FastAPI :8000)
                    │
        ┌───────────┼───────────┐
        ▼           ▼           ▼
    SearXNG    save-your-token   LLM
    (:8081)    内容提取引擎       Bonsai-8B
    搜索引擎                     (:8080)
                    │
            ┌───────┴───────┐
            ▼               ▼
         Jina v5         BGE v2
         Embedding        Reranker
         (:8082)          (:8083)
```

> **注意**：当前 Web UI 默认 RAG 管线简化为 **搜索 → 提取 → LLM**（跳过了 Embedding 和 Reranker 环节）。
> Embedding 和 Reranker 的服务地址仍在代码中保留，可供需要时启用。

---

## 文件结构

```
GeoRAG-demo/
├── main.py                 # FastAPI 后端（RAG 管线编排、SSE 流式）
├── eco_engine.py           # 内容提取引擎（Trafilatura + Playwright 回退）
├── browser_session.py      # Playwright 浏览器管理器（持久化登录态）
├── templates/
│   └── index.html          # 前端界面
├── requirements.txt        # Python 依赖
├── Dockerfile              # Docker 镜像构建
├── docker-compose.yml      # 完整部署编排（含 GPU 服务）
├── .gitignore
├── .dockerignore
└── README.md
```

---

## 快速开始

### 前置条件

| 组件 | 说明 | 获取方式 |
|------|------|----------|
| **Docker** | 24+，支持 `--gpus all` | 官方安装 |
| **NVIDIA 驱动 + nvidia-container-toolkit** | 容器 GPU 穿透 | 参考下方 |
| **GGUF 模型文件**（三个） | 见下方模型声明 | 自行下载 |
| **SearXNG 搜索引擎** | 自建实例或使用现有 | `docker-compose` 中已编排 |

### NVIDIA 容器 GPU 支持（5060 显卡）

```bash
# 安装 nvidia-container-toolkit
distribution=$(. /etc/os-release;echo $ID$VERSION_ID)
curl -s -L https://nvidia.github.io/nvidia-docker/gpgkey | sudo apt-key add -
curl -s -L https://nvidia.github.io/nvidia-docker/$distribution/nvidia-docker.list | sudo tee /etc/apt/sources.list.d/nvidia-docker.list
sudo apt-get update && sudo apt-get install -y nvidia-container-toolkit

# 配置 Docker
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker

# 验证
docker run --rm --gpus all nvidia/cuda:12.4.1-base-ubuntu22.04 nvidia-smi
```

### 方式一：Docker Compose（推荐，全栈部署）

```bash
# 1. 克隆项目
git clone <your-repo-url> && cd GeoRAG-demo

# 2. 构建 GeoRAG-demo 镜像
docker compose build georag-demo

# 3. 准备 llama-cuda 镜像（用于 GGUF 推理）
#    此镜像需要自行构建，内含 CUDA + llama-server
#    参考 https://github.com/ggml-org/llama.cpp 构建 CUDA 版本

# 4. 检查 docker-compose.yml 中的模型路径是否正确
#    修改 volumes 中的宿主机模型文件路径

# 5. 启动全部服务
docker compose up -d

# 6. 打开浏览器访问 http://localhost:8000
```

### 方式二：仅运行 Web UI（模型服务在宿主机）

```bash
# 构建镜像
docker build -t georag-demo .

# 运行（假设 LLM / SearXNG 已在宿主机 127.0.0.1 上运行）
docker run -d --name georag-demo \
  -p 8000:8000 \
  -e LLM_API=http://host.docker.internal:8080/v1 \
  -e EMBED_API=http://host.docker.internal:8082/v1 \
  -e RERANK_API=http://host.docker.internal:8083/v1 \
  -e SEARXNG_URL=http://host.docker.internal:8081/search \
  georag-demo
```

### 方式三：本地运行（无 Docker）

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 安装 Playwright 浏览器（用于反爬回退）
python3 -m playwright install firefox

# 3. 启动 Web UI
uvicorn main:app --host 0.0.0.0 --port 8000 --log-level info
```

---

## 环境变量

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_API` | `http://llm:8080/v1` | LLM API 地址（兼容 llama.cpp OpenAI API） |
| `EMBED_API` | `http://embed:8082/v1` | Embedding 模型 API 地址 |
| `RERANK_API` | `http://rerank:8083/v1` | Reranker 模型 API 地址 |
| `SEARXNG_URL` | `http://searxng:8081/search` | SearXNG 搜索 API 地址 |

---

## 模型声明（GGUF）

> ⚠️ GGUF 模型文件体积巨大（单个 1~2 GB），**不打包在项目中**。
> 请自行下载后通过卷挂载或环境变量指定路径。

本项目需要以下三个 GGUF 模型：

### 1. LLM — Bonsai-8B / Qwen2.5-7B-Instruct 等

| 参数 | 值 |
|------|-----|
| 推荐模型 | [Qwen2.5-7B-Instruct-GGUF](https://huggingface.co/Qwen) 或 Bonsai-8B-GGUF |
| 文件大小 | ~4.7 GB（Q8_0） |
| 启动端口 | 8080 |
| 启动命令 | `llama-server -m <model>.gguf --host 0.0.0.0 --port 8080 -ngl 99` |

### 2. Embedding — jina-embeddings-v5-small

| 参数 | 值 |
|------|-----|
| 下载地址 | [HuggingFace: jinaai/jina-embeddings-v5-small](https://huggingface.co/jinaai/jina-embeddings-v5-small-retrieval) |
| 文件 | `v5-small-retrieval-F16.gguf` |
| 启动端口 | 8082 |
| 启动命令 | `llama-server -m jina.gguf --host 0.0.0.0 --port 8082 --embeddings -ngl 99` |

### 3. Reranker — BGE-Reranker-v2-m3

| 参数 | 值 |
|------|-----|
| 下载地址 | [HuggingFace: BAAI/bge-reranker-v2-m3-GGUF](https://huggingface.co/ChristianAzinn/bge-reranker-v2-m3-GGUF) |
| 文件 | `bge-reranker-v2-m3-Q8_0.gguf` |
| 启动端口 | 8083 |
| 启动命令 | `llama-server -m bge.gguf --host 0.0.0.0 --port 8083 --reranking --ubatch-size 4096 -ngl 99` |

---

## 引擎组件

### save-your-token（内容提取引擎）

`eco_engine.py` 来自 [save-your-token](https://github.com/your-fork/save-your-token) 项目，提供网页内容提取能力：

- **主引擎**：Trafilatura（轻量快速）
- **降级回退**：Playwright 浏览器渲染（处理 SPA / 反爬页面）
- **可选深度解析**：MarkItDown（低密度内容补充）

### SearXNG（搜索引擎）

使用自建的 [SearXNG](https://github.com/searxng/searxng) 实例作为元搜索引擎，聚合百度、Bing、Google 等结果。
`docker-compose.yml` 中已内置部署编排。

### Playwright 浏览器登录态

对于需要登录态访问的网站，可使用 `browser_session.py` 登录并保存 cookie：

```bash
# 打开有头浏览器手动登录
python3 browser_session.py

# 登录完成后按 Ctrl+C，状态自动保存到 ~/.pw-bonsai-session/
# 后续内容提取会自动复用登录态
```

---

## 开发说明

### 测试健康状态

```bash
curl http://localhost:8000/api/health
# 返回: {"llm":"ok","embedding":"ok","reranker":"ok","searxng":"ok"}
```

### 配置文件

服务地址通过环境变量配置（见上方表格），无需硬编码修改代码。

---

## License

MIT
