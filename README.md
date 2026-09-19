# Web Terminal

一个基于真实 PTY 的 Web 终端，页面关闭后终端进程仍然保留，只有点击「Close Terminal」才会真正结束终端。

## 功能

- 通过 `node-pty` 风格的 `ptyprocess` 在服务端运行真实的伪终端（bash/sh）。
- 前端使用 `xterm.js`，支持 VT100/ANSI 转义、颜色、光标、resize 等，与本地终端体验接近。
- 关闭浏览器标签页只会断开 WebSocket，后端的 shell 进程继续运行。
- 用相同的 `?session=<id>` 重新打开页面即可恢复到同一个终端会话。
- 点击「Close Terminal」按钮向服务端发送 `close` 消息，真正终止该 PTY 及其子进程。

## 运行环境

- Python 3.7+
- `tornado`（Web 服务器 + WebSocket）
- `ptyprocess`（PTY）

依赖安装：`pip3 install -r requirements.txt`。
本系统已安装：`tornado 6.5.4`、`ptyprocess 0.7.0`。

## 启动

```bash
cd /home/gumy/桌面/web-terminal
python3 server.py
```

默认监听 `127.0.0.1:8765`。打开浏览器访问 <http://127.0.0.1:8765/>。

### 环境变量

- `PORT`：监听端口，默认 `8765`。
- `HOST`：监听地址，默认 `127.0.0.1`；如需局域网访问可设为 `0.0.0.0`（当前部署即为 `0.0.0.0`，**务必设置 `TOKEN`**）。
- `SHELL`：启动的 shell，默认 `$SHELL` 或 `/bin/bash`。
- `CWD`：新会话的工作目录，默认用户主目录。
- `TERMINAL_ROWS` / `TERMINAL_COLS`：新建 PTY 的默认尺寸，默认 `24` / `80`。
- `WEB_TERMINAL_TERM` / `WEB_TERMINAL_COLORTERM`：子 shell 的 `TERM` / `COLORTERM`，默认 `xterm-256color` / `truecolor`。
- `MAX_BUFFER`：会话重连时回放的最近输出字节数，默认 `100000`。
- `TOKEN`：访问口令。**设置后启用认证**：浏览器访问会先跳转到 `/login` 登录页，输入口令后获得 `HttpOnly` cookie（30 天有效，服务端重启后失效）；脚本请使用 `X-Token` 请求头，也可用 URL `?token=<TOKEN>`（浏览器访问会自动换成 cookie 并从地址栏移除）。访问 `/logout` 可登出。口令不会传给子 shell（`env` 中不可见），日志中的 `?token=` 会被脱敏为 `[redacted]`。
- `MAX_SESSIONS`：最大并发会话数，默认 `32`（`0` 表示不限制）。达到上限后新建会话会返回错误。
- `MAX_WS_PENDING`：单个客户端最多缓冲的出站字节数，默认 `8388608`（8 MB）。停止读取的客户端会被断开，防止输出在内存中堆积。
- `XHEADERS`：置 `1` 时信任 `X-Real-Ip` / `X-Forwarded-For`，只在可信反向代理后面开启（否则客户端可伪造 IP 绕过限速）。

## 使用说明

- 每个标签页有一个独立的 `session id`，显示在工具栏。
- 复制当前 URL 到另一个浏览器标签页，可以同时连接同一个终端。
- 关闭页面后，终端仍在后台运行；重新打开相同 URL 即可恢复。
- 点击「Close Terminal」会结束该会话的 shell 进程。
- 双击标签页标题可重命名。
- 快捷键：`Alt+N` 新建终端，`Alt+W` 关闭当前终端，`Ctrl+Shift+F` 搜索终端缓冲，`F3` / `Shift+F3` 查找下一个/上一个。（`Ctrl+Shift+T`/`Ctrl+Shift+W` 在 Chrome/Firefox 中被浏览器保留，页面收不到，故用 Alt 组合；这两个键仍保留为 PWA 等场景下的兼容入口。）
- 手机端自适应：触控设备会自动切换为 `mobile` 样式，按钮和标签更紧凑，底部增加常用快捷键栏（Ctrl+C、Esc、方向键、Tab、|、~ 等）。
- 手机与电脑配置独立保存（基于触控检测使用不同的 `localStorage` 键）。
- 所有 xterm.js、xterm-addon-fit、xterm-addon-search 资源都托管在本地 `static/`，无需联网即可使用。

## 测试

```bash
cd /home/gumy/桌面/web-terminal
TOKEN=<服务口令> python3 test_client.py   # 服务启用 TOKEN 时需要
```

测试用例覆盖：基本输入输出、断开后进程保留、点击关闭后端 PTY 被终止。

## 安全提示

- 当前部署监听 `0.0.0.0`（局域网可达），**已启用 `TOKEN` 认证**：未登录访问一律跳转到登录页；`/api/*`、`/static/*`、`/ws` 全部需要认证。
- 认证失败会按 IP 做指数退避限速（第 4 次失败起延迟 2s/4s/8s... 封顶 30s）。
- 口令校验使用 `hmac.compare_digest` 常量时间比较；cookie 为 `HttpOnly` + `SameSite=Lax`（顺带阻止跨站 WebSocket 携带凭据）。
- 不要暴露到公网。如需远程使用，建议 Tailscale/WireGuard 或反向代理（nginx/traefik）+ HTTPS。
- 服务端以当前用户身份运行 shell，拥有当前用户的全部权限。

## 已知限制

- 服务端重启后所有会话会丢失。如需跨服务端重启保持会话，可改用 `tmux`/`screen` 作为中间层。
- 「Close Terminal」会结束 shell 及其会话内的进程（与真实终端挂断语义一致）；但 `nohup`、`setsid` 或自行脱离会话的守护进程会存活，与关闭本地终端窗口的行为相同。
- PTY 输入写入走每会话专用线程：即使子 shell 停止读取输入，也不会阻塞其他会话。
