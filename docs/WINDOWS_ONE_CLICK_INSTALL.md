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
- BIOS/UEFI 中没有开启虚拟化。
- 客户公司的终端安全软件拦截 Docker Desktop。
- 客户公司策略不允许使用 Docker Desktop。

建议先重启一次 Windows，手动打开 Docker Desktop，确认 Docker Desktop 运行正常后，再重新执行 `install-windows.cmd`。

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
