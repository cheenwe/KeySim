# KeySim

一个带可视化窗口的本地 HTTP API 服务：接收 `1`、`2`、`3`… 等按键指令，
在当前获得焦点的窗口里模拟真实按键。

## 功能

- 本地 HTTP API：GET / POST 均可发送按键指令，支持单键和按键序列
- 真实按键模拟：Windows 使用 `SendInput`，macOS 使用 `CGEvent`
- 可视化窗口：1~6 快捷按钮、本地测试输入框、实时指令日志（含来源 IP、失败原因）
- 端口可配置、窗口置顶、启动/停止服务
- 零第三方依赖（仅标准库 + tkinter），可用 PyInstaller 打包为单文件 exe / app

## 快速开始

要求 Python 3.8+（自带 tkinter）。

```bash
python3 keyboard_api.py
```

- Windows：双击 `start.bat`
- macOS：双击 `启动.command`
- 启动后窗口默认监听 `0.0.0.0:8765`，本机与局域网均可访问

### macOS 权限（必须）

系统会静默丢弃未授权的模拟按键，需在
**系统设置 → 隐私与安全性 → 辅助功能** 中勾选运行本程序的终端（如 Terminal、iTerm、Trae），
然后重启程序。窗口底部会显示授权状态，也可点「打开辅助功能设置」直接跳转。

### Windows 权限

如果目标窗口以管理员权限运行，本程序也需要「以管理员身份运行」，否则会报错误码 5。

## API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/status` | 服务状态、平台、是否可模拟、macOS 辅助功能授权状态 |
| GET | `/press?key=1` | 按下单个按键 |
| GET | `/press?keys=1,2,3` | 依次按下多个按键 |
| POST | `/press` | JSON：`{"key": "1"}` 或 `{"keys": ["1", "enter"]}` |

示例：

```bash
curl "http://127.0.0.1:8765/press?key=1"
curl "http://127.0.0.1:8765/press?keys=1,2,3"
curl -X POST http://127.0.0.1:8765/press -H "Content-Type: application/json" -d "{\"key\":\"6\"}"
curl http://127.0.0.1:8765/status
```

返回示例：

```json
{"ok": true, "pressed": ["1"], "failed": [], "invalid": [], "errors": {}, "simulated": true}
```

- `failed`：按键名有效但模拟失败，`errors` 里给出具体原因（权限、Win32 错误码等）
- `invalid`：不支持的按键名

## 支持的按键

- 数字 `0`~`9`、字母 `a`~`z`
- `enter` `space` `tab` `esc` `backspace` `delete` `home` `end` `pageup` `pagedown`
- 方向键 `up` `down` `left` `right`
- 功能键 `f1`~`f12`
- 修饰键 `ctrl` `alt` `shift`（Windows 另有 `win`，macOS 为 `cmd`）

## 打包

Windows（在 Windows 上双击）：

```bat
build.bat
```

产物：`dist\KeySim.exe`。也可直接使用 GitHub Actions 的构建产物。

macOS：

```bash
python3 -m pip install pyinstaller
python3 -m PyInstaller --noconfirm --clean --onefile --windowed -n KeySim keyboard_api.py
```

产物：`dist/KeySim.app`（首次运行需在辅助功能中授权该 app）。

## GitHub Actions

`.github/workflows/build.yml` 会在打 tag（`v*`）或手动触发时，分别在
Windows 与 macOS runner 上打包，并把产物上传为 Artifact：

- `KeySim-Windows`：`KeySim.exe`
- `KeySim-macOS`：`KeySim-macos.zip`（含 `KeySim.app`）

如果是打 tag 触发的，构建成功后还会自动创建对应的 GitHub Release，
并挂上 `KeySim.exe` 与 `KeySim-macos.zip` 两个附件。

## 目录结构

```
keyboard_api.py       # 主程序：API 服务 + 窗口 + 按键模拟
start.bat / 启动.command  # Windows / macOS 双击启动
build.bat             # Windows 一键打包 exe
.github/workflows/build.yml  # CI：双平台打包
```

## 常见问题

**日志显示「模拟失败」？**

- macOS：未授予辅助功能权限，按上文授权后重启程序
- Windows：目标程序以管理员权限运行（错误码 5），请以管理员身份运行本程序
- 按键进入了错误的窗口：模拟按键发送给当前焦点窗口，本程序窗口默认置顶但不抢焦点，
  请先点击目标窗口再发送指令

**局域网访问不了？** 检查本机防火墙是否放行 8765 端口，并确认使用的是本机局域网 IP。
