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

本系统已安装：`tornado 6.5.4`、`ptyprocess 0.7.0`。

## 启动

```bash
cd /home/gumy/桌面/web-terminal
python3 server.py
```

默认监听 `127.0.0.1:8765`。打开浏览器访问 <http://127.0.0.1:8765/>。

### 环境变量

- `PORT`：监听端口，默认 `8765`。
- `HOST`：监听地址，默认 `127.0.0.1`；如需局域网访问可设为 `0.0.0.0`（注意安全）。
- `SHELL`：启动的 shell，默认 `$SHELL` 或 `/bin/bash`。
- `MAX_BUFFER`：会话重连时回放的最近输出字节数，默认 `100000`。
- `TOKEN`：可选访问令牌。设置后所有 HTTP 和 WebSocket 请求都需要在 URL 中携带 `?token=<TOKEN>` 或 `X-Token` 请求头。
- `MAX_SESSIONS`：最大并发会话数，默认 `0`（不限制）。达到上限后新建会话会返回错误。

## 使用说明

- 每个标签页有一个独立的 `session id`，显示在工具栏。
- 复制当前 URL 到另一个浏览器标签页，可以同时连接同一个终端。
- 关闭页面后，终端仍在后台运行；重新打开相同 URL 即可恢复。
- 点击「Close Terminal」会结束该会话的 shell 进程。
- 双击标签页标题可重命名。
- 快捷键：`Ctrl+Shift+T` 新建终端，`Ctrl+Shift+W` 关闭当前终端，`Ctrl+Shift+F` 搜索终端缓冲，`F3` / `Shift+F3` 查找下一个/上一个。
- 所有 xterm.js、xterm-addon-fit、xterm-addon-search 资源都托管在本地 `static/`，无需联网即可使用。

## 测试

```bash
cd /home/gumy/桌面/web-terminal
python3 test_client.py
```

测试用例覆盖：基本输入输出、断开后进程保留、点击关闭后端 PTY 被终止。

## 安全提示

- 默认仅监听 `127.0.0.1`，不要直接暴露到公网。
- 如需远程使用，请加反向代理（nginx/traefik）并配置身份验证（OAuth、Basic Auth 等）。
- 也可以设置 `TOKEN` 环境变量开启简单令牌认证（通过 URL `?token=<TOKEN>` 或 `X-Token` 头传递）。
- 服务端以当前用户身份运行 shell，拥有当前用户的全部权限。

## 已知限制

- 服务端重启后所有会话会丢失。如需跨服务端重启保持会话，可改用 `tmux`/`screen` 作为中间层。
