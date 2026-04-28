# Windows 一键安装说明

本文用于在客户公司的 Windows 笔记本电脑上安装本系统。可以假设目标电脑没有开发环境依赖。

## 推荐机器环境

- Windows 10/11 64 位。
- 首次安装需要管理员权限。
- BIOS/UEFI 中已开启 CPU 虚拟化。
- 推荐 16 GB 或以上内存。
- 推荐 30 GB 或以上可用磁盘空间。
- 首次安装需要联网下载 Docker 镜像和依赖包。
- Docker Desktop 是否可在客户公司环境中使用，需要客户 IT 或合规负责人确认。

## 一键安装

1. 将项目文件夹放到 Windows 电脑上。
2. 在项目根目录双击 `install-windows.cmd`。
3. 如果 Windows 弹出管理员权限确认，选择允许。
4. 如果脚本首次安装了 Docker Desktop 或启用了 WSL2，按提示重启 Windows，然后再次双击 `install-windows.cmd`。
5. 安装完成后打开：

```text
http://127.0.0.1:3000/projects
```

安装脚本会启动生产模式 Docker Compose 服务：

- Postgres
- Redis
- Qdrant
- MinIO
- Gateway
- Backend
- Frontend

脚本会在启动后端和前端前自动执行 Alembic 数据库迁移。

## LLM 配置

如果项目根目录没有 `.env`，脚本会从 `.env.example` 自动创建。

自动创建的 `.env` 默认使用 mock LLM，系统可以启动，但不能用于真实生成。客户试用前需要编辑 `.env`，填入真实 Qwen/OpenAI-compatible 配置：

```env
LLM_PROVIDER_BACKEND=qwen
QWEN_API_KEY=replace-with-real-key
QWEN_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
QWEN_MODEL=qwen-plus
OPENAI_API_STYLE=auto

VISION_LLM_API_KEY=replace-with-real-vision-key
VISION_LLM_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
VISION_LLM_MODEL=replace-with-vision-model
VISION_LLM_API_STYLE=auto
```

首次安装生成的 `.env` 会把 `EMBEDDING_LOCAL_FILES_ONLY=false`，用于允许容器首次下载离线 embedding 模型。客户网络无法访问模型源时，需要提前准备模型缓存或企业内网镜像，否则导入文档时可能失败。

修改 `.env` 后，从项目根目录执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows-install.ps1 -Action restart
```

## 可选交付数据包

如果需要随安装包交付已有方案库，不要把真实运行数据提交到 git。按下面目录放入交付包：

```text
install-assets/
  case_library/
    outline_library.json
    block_library.json
    case_library_summary.md
```

也可以打成 zip：

```text
install-assets/
  case_library.zip
```

安装脚本会在构建 backend 镜像前，把 `install-assets/case_library/` 或 `install-assets/case_library.zip` 导入到：

```text
backend/data/case_library/
```

这样客户机 fresh clone 或 zip 解压后，也能带着已有 case library 构建。

如果客户环境无法访问 Docker Hub，也可以额外准备：

```text
install-assets/docker-images.tar
```

安装脚本会先执行 `docker load -i install-assets/docker-images.tar`，再继续启动服务。这个文件通常会很大，只有完全离线或企业网络限制很强时才建议使用。

如果客户有企业内网镜像仓库，可以在 `.env` 里覆盖镜像名：

```env
PYTHON_BASE_IMAGE=registry.company.com/library/python:3.11-slim
NODE_BASE_IMAGE=registry.company.com/library/node:20-alpine
POSTGRES_IMAGE=registry.company.com/library/postgres:16-alpine
REDIS_IMAGE=registry.company.com/library/redis:7-alpine
QDRANT_IMAGE=registry.company.com/qdrant/qdrant:v1.12.4
MINIO_IMAGE=registry.company.com/minio/minio:latest
```

如果只是 Docker Hub 访问慢，优先在 Docker Desktop 里配置国内镜像加速器；如果公司网络完全禁止 Docker Hub，再使用企业内网镜像仓库或离线镜像包。

## Backend Python 依赖体积

默认生产安装使用：

```env
BACKEND_EXTRAS=parsing,embeddings
INSTALL_CPU_TORCH=true
TORCH_CPU_INDEX_URL=https://download.pytorch.org/whl/cpu
FORMULA_OCR_BACKEND=none
```

这里有两个目的：

- 保留文档解析和本地 embedding 能力。
- 排除 `pix2tex` 公式 OCR，避免在默认安装里拉取额外的大型深度学习依赖。
- 先安装 CPU-only torch，避免 pip 默认拉取 CUDA / cuDNN / cuSPARSE 等 GPU 运行包。

如果仍看到类似下面的大包下载：

```text
nvidia_cudnn_cu13
nvidia_cusparselt_cu13
cuda_toolkit
```

说明当前 `.env` 仍在使用旧配置，或者 Docker 缓存/构建参数没有更新。确认 `.env`：

```env
BACKEND_EXTRAS=parsing,embeddings
INSTALL_CPU_TORCH=true
```

然后重新构建：

```powershell
docker compose --project-name rag_test_customer --env-file .env -f docker-compose.prod.yml build --no-cache backend
```

如果客户网络无法访问 `download.pytorch.org`，可以把 `TORCH_CPU_INDEX_URL` 改成客户可访问的 PyTorch CPU wheel 镜像源，或改用离线镜像包。

## 常用运维命令

以下命令都在项目根目录执行：

```powershell
# 安装、启动或修复当前安装
powershell -ExecutionPolicy Bypass -File scripts\windows-install.ps1 -Action install

# 修改 .env 或代码后完整重启
powershell -ExecutionPolicy Bypass -File scripts\windows-install.ps1 -Action restart

# 停止所有容器
powershell -ExecutionPolicy Bypass -File scripts\windows-install.ps1 -Action stop

# 查看容器状态
powershell -ExecutionPolicy Bypass -File scripts\windows-install.ps1 -Action status

# 查看运行日志
powershell -ExecutionPolicy Bypass -File scripts\windows-install.ps1 -Action logs
```

## 常见问题

### Docker 一直没有 ready

常见原因：

- Windows 刚启用 WSL2 相关特性，需要重启。
- Docker Desktop 已安装，但还没有完成首次启动、许可确认或初始化。
- Docker Desktop 没有切到 Linux containers。
- BIOS/UEFI 中没有开启虚拟化。
- 客户公司的终端安全软件拦截 Docker Desktop。
- 客户公司策略不允许使用 Docker Desktop。

建议先重启一次 Windows，手动打开 Docker Desktop，确认 Docker Desktop 运行正常后，再重新执行 `install-windows.cmd`。

如果看到类似下面的错误：

```text
open //./pipe/dockerDesktopLinuxEngine: The system cannot find the file specified
```

这说明 Docker CLI 已安装，但 Docker Desktop 的 Linux engine 还没有启动成功。按下面顺序处理：

1. 重启 Windows。
2. 手动打开 Docker Desktop。
3. 完成首次启动向导、许可确认、WSL2 初始化。
4. 等 Docker Desktop 显示 running。
5. 如果托盘菜单里有 `Switch to Linux containers...`，切换到 Linux containers。
6. 在 PowerShell 里确认：

```powershell
docker info
wsl --status
wsl -l -v
Get-Service com.docker.service
```

`docker info` 可以正常输出后，再重新执行 `install-windows.cmd`。

### 端口被占用

如果 `3000` 或 `8000` 被占用，修改 `.env`：

```env
FRONTEND_PORT=3001
BACKEND_PORT=8002
NEXT_PUBLIC_API_BASE_URL=http://localhost:8002/api/v1
```

然后执行：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\windows-install.ps1 -Action restart
```

### 首次安装很慢

首次安装需要构建 Docker 镜像，并在容器里下载 Python/Node 依赖。客户网络受限时耗时会明显增加。

如果客户环境完全离线，需要后续单独准备 `docker save` / `docker load` 的离线镜像包。本脚本当前默认目标电脑可以访问外网或企业代理。

### backend 镜像构建时报 `data/case_library` not found

如果看到：

```text
COPY data/case_library ./data/case_library
failed to calculate checksum ... "/data/case_library": not found
```

说明当前代码包缺少空的 `backend/data/case_library` 目录。更新到包含 Windows 安装修复的最新代码后重试；如果需要交付已有方案库，把方案库文件放到 `install-assets/case_library/`。
