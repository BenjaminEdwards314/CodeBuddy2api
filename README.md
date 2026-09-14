# CodeBuddy2API

将 CodeBuddy 官方 API 包装成一个功能强大、与 OpenAI API 格式兼容的服务。本项目可以直接调用 CodeBuddy 官方 API，并为所有标准客户端提供统一的接口。

> 快速部署请直接查看 [USAGE.md](./USAGE.md)（Docker Compose 一键部署流程）。

## 🌟 功能特性

- 🔌 **OpenAI 兼容接口**：支持标准的 `/v1/chat/completions` API，无缝对接现有生态。
- 🔄 **智能响应处理**：即使 CodeBuddy 原生仅支持流式响应，本服务也能为客户端智能处理**非流式**请求，并在后端自动完成“流式转非流式”的响应包装。
- ⚡ **高性能**：完全基于 FastAPI 和 `asyncio` 构建，支持高并发异步请求。
- 🔐 **双重认证机制**：
    - **服务访问认证**：通过环境变量设置密码，保护整个代理服务。
    - **CodeBuddy 官方认证**：在后端安全地管理和使用 CodeBuddy 的 `Bearer Token`。
- 🔄 **凭证自动轮换**：支持在 `.codebuddy_creds` 目录中配置多个 CodeBuddy 认证凭证，服务会自动轮换使用，有效提高可用性和分担请求压力。
- 🌐 **Web 管理界面**：内置一个美观、易用的 Web UI，方便用户管理凭证、测试 API 和查看服务状态。

## 🚀 快速开始

### 1. 前置要求

- Python 3.8 或更高版本
- Git

### 2. 下载和安装

首先，克隆本项目到本地：
```bash
git clone https://github.com/BenjaminEdwards314/CodeBuddy2api.git
cd codebuddy2api
```

然后，运行启动脚本。此脚本会自动创建 Python 虚拟环境并安装所有必需的依赖。

**Windows:**
```bash
start.bat
```

**Linux / macOS:**
```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python web.py
```

### 3. 配置环境变量

项目启动需要一些基本配置。请将根目录下的 `.env.example` 文件复制一份并重命名为 `.env`：

```bash
cp .env.example .env
```

然后，用你的文本编辑器打开 `.env` 文件，**至少需要设置以下必需的变量**：

```dotenv
# (必需) API服务的访问密码，客户端连接时需要提供此密码
CODEBUDDY_PASSWORD=your_secret_password_for_this_service

# (推荐) 直接使用 CodeBuddy API Key（中国版请配 internal）
CODEBUDDY_AUTH_MODE=api_key
CODEBUDDY_API_KEY=your_codebuddy_api_key
CODEBUDDY_INTERNET_ENVIRONMENT=internal
```

> 说明：当使用 `CODEBUDDY_AUTH_MODE=api_key` 时，无需再配置 `.codebuddy_creds` 凭证文件。

### 4. 添加 CodeBuddy 认证凭证（仅 Token 模式需要）

为了让服务能够代理请求，你至少需要添加一个有效的 CodeBuddy 认证凭证（仅 `token` 模式需要）。本项目提供了极为便捷的**自动化认证**方式。

**推荐方式：使用 Web 管理界面自动获取**

1.  启动服务后，使用浏览器访问 `http://127.0.0.1:8001` (或你自定义的地址)。
2.  输入你在 `.env` 文件中设置的 `CODEBUDDY_PASSWORD` 登录管理面板。
3.  进入 “**凭证管理**” 标签页。
4.  点击 **自动获取认证** 卡片中的 “**开始认证**” 按钮。
5.  系统会自动生成一个 CodeBuddy 的官方登录链接。请点击 “**打开链接**” 按钮。
6.  在新打开的 CodeBuddy 页面中完成登录授权。
7.  **完成！** 登录成功后，请关闭登录页面。本服务会自动检测到登录状态，并为你获取、解析和保存新的认证凭证。你只需点击 “**刷新列表**” 即可看到新添加的凭证。


### 5. 启动服务

一切准备就绪后，再次运行启动脚本即可启动服务：

**Windows:**
```bash
start.bat
```

**直接运行:**
```bash
# 确保你已在虚拟环境中 (source venv/bin/activate)
python web.py
```

服务启动后，你就可以开始使用了！

## 🖥️ 桌面应用模式（推荐）

如果不想使用命令行，可以直接以桌面应用方式启动。桌面版会打开一个原生窗口，
内置了服务进程，并提供一个图形化配置界面来设置**端口**、**API Key** 和**访问密码**。

**macOS / Linux:**
```bash
./start_desktop.sh
```

**Windows:**
```bash
start_desktop.bat
```

**直接运行:**
```bash
python desktop.py
```

启动脚本会自动创建虚拟环境并安装依赖（含 `pywebview`），首次运行需要几分钟。

### 桌面端功能

- **图形化配置**：首次启动若未完成配置，会直接进入配置页面；填写端口、访问密码、
  CodeBuddy API Key 后点击「保存并应用」即可。
- **改端口自动重启**：修改端口后服务会在新端口上自动重启，窗口自动跳转到新地址，
  无需手动操作。
- **配置持久化**：保存的配置写入 `config/config.json`，下次启动自动生效。
- **控制面板**：配置完成后可点击「进入控制面板」使用原有的管理界面；
  在控制面板中点击「桌面配置」标签可随时返回配置页。
- **状态显示**：窗口底部实时显示服务运行状态与当前访问地址。

> 若端口被占用，保存时会直接提示并拒绝，不会中断正在运行的服务。

### 打包成 macOS 独立应用（.app）

仓库已包含 PyInstaller 配置，可打包成不依赖本机 Python 的独立应用：

```bash
pip install pyinstaller
pyinstaller packaging/CodeBuddy2API.spec --noconfirm
cp -R dist/CodeBuddy2API.app /Applications/
```

产物为 `dist/CodeBuddy2API.app`，双击即可运行。

**说明：**

- PyInstaller 不支持交叉编译：macOS 的 `.app` 只能在 macOS 上打包，Windows 的 `.exe`
  需要在 Windows 机器上执行同样的命令。
- 应用未做 Apple 签名与公证。首次打开若被 Gatekeeper 拦截，请右键点击应用 →
  选择「打开」，或执行 `xattr -cr /Applications/CodeBuddy2API.app`。
- 数据目录位于 `~/Library/Application Support/CodeBuddy2API/`
  （含 `config/config.json` 与 `.codebuddy_creds/`），与源码方式运行的配置相互独立。
- 首次启动若默认端口 8001 已被占用，应用会自动改用下一个可用端口。

### 检查更新

配置页底部会显示当前版本（版本号 · commit · 打包日期），点击「检查更新」按钮可
对比上游仓库的最新提交：

- **有新版本**：提示上游最新 commit 与日期，并给出「查看更新内容」链接
  （仅在浏览器打开，不会自动下载或安装）。
- **已是最新 / 比上游更新**：明确提示，无需操作。
- **无法判断**：当本地 commit 未发布到任何远端时，会如实说明无法比较，
  而不会误报有新版本。

上游仓库目前没有发布任何 release 或 tag，因此更新判断基于默认分支的最新提交。

**更新应用需要重新打包**（`.app` 内的代码是打包好的，不会自动跟随源码变化）：

```bash
git pull                      # 或 git fetch upstream && git merge upstream/main
pyinstaller packaging/Server.spec --noconfirm          # 多开所需的无界面服务端
pyinstaller packaging/CodeBuddy2API.spec --noconfirm   # 桌面应用本体
cp -R dist/CodeBuddy2API.app /Applications/
```

配置保存在 `~/Library/Application Support/CodeBuddy2API/`，重新安装不会丢失。

> 注意：从 CodeBuddy 官方新增的模型**不需要**更新代码，模型列表是运行时从服务端获取的。

## 🧩 多开实例（工作台）

管理面板 → **工作台** 标签页可以创建多个相互独立的代理实例，每个实例：

- 有**自己的端口**和**访问密码**（客户端用对应端口 + 密码连接）
- 可独立选择**认证方式**：`API Key` 或 `Token 轮换`
- Token 模式下可**多选凭证**，只有选中的凭证会被复制到该实例的独立目录
- 可同时启动、独立停止，互不影响

实例配置存放在 `~/Library/Application Support/CodeBuddy2API/instances/`，
重启应用后实例列表保留，但进程不会自动拉起（需要手动点「启动」）。

### 客户端连接示例

```bash
# 实例运行在 8891，密码 tok_pw
curl http://127.0.0.1:8891/codebuddy/v1/chat/completions \
  -H "Authorization: Bearer tok_pw" \
  -H "Content-Type: application/json" \
  -d '{"model":"deepseek-v3","messages":[{"role":"user","content":"hi"}]}'
```

在 Cherry Studio / OpenWebUI 等客户端里，把 **Base URL** 填成
`http://127.0.0.1:<实例端口>/v1`，**API Key** 填该实例的访问密码即可。

### 实现说明

每个实例是一个**独立进程**，不是同进程内的多个服务。原因是 `config.py` 与凭证管理器
都把状态放在模块级全局变量里，同进程多实例会互相覆盖配置。独立进程可以让每个实例
读自己的 `config/config.json`，同时完全复用现有服务端代码。

打包后的 `.app` 里没有 Python 解释器也没有 `web.py`，所以多开所需的子进程由
`packaging/Server.spec` 单独编译成 `CodeBuddy2API-server` 并放进应用包内——
这也是上面打包步骤有两条命令的原因。

## ⚙️ API 使用

### 认证

所有对本服务的 API 请求，都需要在 HTTP 请求头中包含你在 `.env` 文件里设置的 `CODEBUDDY_PASSWORD` 作为 Bearer Token。

`Authorization: Bearer your_secret_password_for_this_service`

### 客户端集成示例

你可以将任何支持 OpenAI API 的客户端指向本服务。

**Python 客户端:**
```python
import openai

client = openai.OpenAI(
    api_key="your_secret_password_for_this_service",
    base_url="http://127.0.0.1:8001/codebuddy/v1"
)

# 非流式请求
response = client.chat.completions.create(
    model="auto-chat",
    messages=[
        {"role": "user", "content": "你好，2+2等于几？"}
    ]
)
print(response.choices[0].message.content)

# 流式请求
stream = client.chat.completions.create(
    model="auto-chat",
    messages=[
        {"role": "user", "content": "写一个Python的Hello World脚本"}
    ],
    stream=True
)
for chunk in stream:
    print(chunk.choices[0].delta.content or "", end="")

```

**curl 命令行示例:**
```bash
# 非流式请求
curl -X POST "http://127.0.0.1:8001/codebuddy/v1/chat/completions" \
  -H "Authorization: Bearer your_secret_password_for_this_service" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto-chat",
    "messages": [
      {"role": "user", "content": "Hello, what is 2+2?"}
    ]
  }'

# 流式请求
curl -X POST "http://127.0.0.1:8001/codebuddy/v1/chat/completions" \
  -H "Authorization: Bearer your_secret_password_for_this_service" \
  -H "Content-Type: application/json" \
  -d '{
    "model": "auto-chat",
    "messages": [
      {"role": "user", "content": "Write a Python hello world script"}
    ],
    "stream": true
  }'
```

## 📝 API 端点

- `POST /codebuddy/v1/chat/completions`: 核心接口，用于发送聊天请求。
- `GET /codebuddy/v1/models`: 获取在 `.env` 文件中配置的模型列表。
- `GET /codebuddy/v1/credentials`: （需要认证）在 Web UI 中用于列出所有凭证。
- `POST /codebuddy/v1/credentials`: （需要认证）在 Web UI 中用于添加新凭证。
- `GET /health`: 服务的健康检查端点。

## 🔧 项目结构

```
codebuddy2api/
├── src/                           # 源代码目录
│   ├── auth.py                    # 服务访问认证模块
│   ├── codebuddy_api_client.py    # 封装了与CodeBuddy官方API的通信
│   ├── codebuddy_auth_router.py   # CodeBuddy OAuth2 认证路由
│   ├── codebuddy_token_manager.py # CodeBuddy凭证加载与轮换管理器
│   ├── codebuddy_router.py        # 核心API路由 (v1) - 已重构优化
│   ├── frontend_router.py         # Web管理界面的路由
│   ├── settings_router.py         # 设置管理路由
│   ├── usage_stats_manager.py     # 使用统计管理器
│   └── keyword_replacer.py        # 关键词替换模块
├── frontend/
│   └── admin.html                 # Web管理界面的前端页面
├── .codebuddy_creds/              # 存放CodeBuddy凭证的目录 (Git会忽略其中的文件)
├── web.py                         # FastAPI服务主入口
├── config.py                      # 环境变量配置管理
├── requirements.txt               # Python依赖列表
├── .env.example                   # 环境变量示例文件
├── start.bat                      # Windows一键启动脚本
├── docker-compose.yml             # Docker Compose 配置
├── Dockerfile                     # Docker 镜像构建文件
├── entrypoint.sh                  # Docker 容器入口脚本
└── README.md                      # 本文档
```

## ⚙️ 配置选项

所有配置均通过 `.env` 文件或环境变量进行管理。

| 环境变量 | 默认值 | 说明 |
| ---------------------- | --------------------- | ---------------------------------------------------------- |
| `CODEBUDDY_PASSWORD` | - | **(必需)** 访问此API服务的密码。 |
| `CODEBUDDY_AUTH_MODE` | `auto` | 认证模式：`auto` / `api_key` / `token`。`auto` 下优先用 `CODEBUDDY_API_KEY`，否则使用 `.codebuddy_creds`。 |
| `CODEBUDDY_API_KEY` | - | API Key（推荐）。 |
| `CODEBUDDY_INTERNET_ENVIRONMENT` | `""` | 网络环境，`internal`/`ioa`/`public`。 |
| `CODEBUDDY_HOST` | `127.0.0.1` | 服务监听的主机地址。 |
| `CODEBUDDY_PORT` | `8001` | 服务监听的端口。 |
| `CODEBUDDY_API_ENDPOINT` | `""` | CodeBuddy 官方 API 端点。留空时会按 `CODEBUDDY_INTERNET_ENVIRONMENT` 自动选择（`internal/ioa -> https://copilot.tencent.com`，否则 `https://www.codebuddy.ai`）。 |
| `CODEBUDDY_CREDS_DIR` | `.codebuddy_creds` | 存放 CodeBuddy 认证凭证的目录。 |
| `CODEBUDDY_LOG_LEVEL` | `INFO` | 日志级别，可选 `DEBUG`, `INFO`, `WARNING`, `ERROR`。 |
| `CODEBUDDY_MODELS` | (列表) | 向客户端报告的可用模型列表，用逗号分隔。 |
| `CODEBUDDY_SSL_VERIFY` | `false` | SSL验证开关，设置为 `true` 启用SSL验证。 |
| `CODEBUDDY_ROTATION_COUNT` | `10` | 凭证轮换计数，每N次请求后切换凭证。 |

## 🐛 故障排除

- **"No valid CodeBuddy credentials found"**:
  - 确保你已经在 `.codebuddy_creds` 目录下添加了至少一个有效的凭证 JSON 文件。
  - 推荐使用 Web UI 添加，以确保格式正确。

- **"API error: 401" / "API error: 403" (来自 CodeBuddy)**:
  - 这通常意味着你的 CodeBuddy `Bearer Token` 无效或已过期。请通过官网重新获取一个新的 Token，并在 Web UI 中更新。

- **"Invalid password"**:
  - 这意味着你访问本服务时，请求头中提供的 Bearer Token 与你在 `.env` 文件中设置的 `CODEBUDDY_PASSWORD` 不匹配。

- **需要查看详细日志**:
  - 在 `.env` 文件中设置 `CODEBUDDY_LOG_LEVEL=DEBUG`，然后重启服务。
