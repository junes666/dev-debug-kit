# 开发调试 · Dev Debug Kit

一个**离线、轻量**的一体化开发调试工具箱，基于 Python + PySide6 桌面原生界面（非网页）。装一次依赖后即可**完全断网运行**。

集成六大模块：

| 模块 | 功能 |
|------|------|
| 🌐 **HTTP 调试** | 地址栏 / 请求方法 / 超时 / 请求头 / 请求体(text·JSON·form·multipart) / **图片上传** / 响应体(文本·图片预览·格式化) / 响应头 / 状态码·耗时·大小 / 历史记录 / cURL 导入导出。桌面直连，无浏览器跨域限制 |
| ⚡ **JS 调试** | 内置 **QuickJS** 纯 ECMAScript 引擎 + 浏览器模拟环境；代码格式化 / 压缩；**变量树**(函数·类·对象逐层展开)；**执行函数框**(如 `a('a','w',1)` 自定义调用)；执行日志与 `console.log` 输出捕获；死循环超时保护 |
| 🌲 **JSON 解析** | 格式化 / 压缩 / 校验 / 转义 / 反转义 / 复制 / 粘贴 / 清空；解析成可折叠**树**；点击节点显示 **JSONPath 路径**与值；值为图片 URL 时**直接预览图片** |
| 🔀 **JSON 对比** | 两侧独立格式化/压缩；递归**逐键对比**，高亮新增 / 删除 / 修改 / 类型差异；差异清单(路径+左右值)+汇总统计 |
| 🔐 **编码加密** | Base64 / URL / HTML 实体 / Unicode / Hex / 码点 互转；MD5·SHA1·SHA256·SHA512·HMAC；AES·DES·3DES·RC4 加解密；JWT 解析；**时间戳 ↔ 日期**互转 |
| ▦ **二维码** | 文本/URL **生成二维码**(纠错等级·尺寸·前景背景色·导出 PNG·复制图片)；上传图片**解析二维码**内容。全程离线 |
| 🌍 **翻译** | **离线中英互译**（ctranslate2 神经翻译）；**粘贴翻译**；**图片翻译**(OCR 识别后翻译)；翻译/OCR 在独立子进程运行、崩溃不影响主程序 |
| 🔤 **正则** | 正则表达式**实时测试**（匹配/分组/位置）+ **替换**；内置 20+ **常用正则库**(手机号·邮箱·URL·IP·身份证·日期…) |

> JS 变量树与 JSON 树支持 `+/−` 展开折叠；HTTP 响应体支持多字符集解码修复乱码、响应头文本框展示。

## 下载（Windows 64 位，免安装，解压即用）

见 [Releases](https://github.com/junes666/dev-debug-kit/releases)，提供两个版本：

| 版本 | 体积 | 说明 |
|------|------|------|
| **精简版** `开发调试-精简版-win64.zip` | ~35 MB | 秒启动。7 个核心功能全部内置离线；**翻译**首次点「下载翻译组件」自动下载(约180MB)后离线；**图片OCR翻译需全离线版** |
| **全离线版** `开发调试-全离线版-win64.zip` | ~250 MB | **翻译 + 图片OCR翻译全内置**，开箱即用、全程不联网 |

> 都是 onedir 文件夹（秒启动、无需装 Python）；解压后双击 `开发调试.exe`。首次运行被 SmartScreen 拦截时选「仍要运行」。

## 安装与运行

需要 Python 3.9+（推荐 3.10+）。

```bash
# 1. 创建虚拟环境（推荐）
python3 -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate

# 2. 安装依赖（联网装一次，之后离线运行）
pip install -r requirements.txt

# 3. 运行
python main.py
```

或直接用附带的启动脚本：

- Linux / macOS：`./run.sh`
- Windows：双击 `run.bat`

首次运行脚本会自动创建 venv 并安装依赖。

## 依赖说明

**必需**（`requirements.txt`）：

- `PySide6` —— 原生桌面界面（Qt6，非 HTML）
- `quickjs` —— 内置 JS 引擎，负责 JS 调试 / 代码格式化(beautify) / 压缩(terser)
- `segno` —— 二维码生成（纯 Python）
- `pycryptodome` —— AES/DES/3DES/RC4 对称加解密

**可选**（仅“二维码解析”需要，不装则该功能提示安装，其余不受影响）：

- `opencv-python-headless`、`numpy`

> 所有第三方库仅在**首次安装**时联网下载；安装完成后本工具**运行期不联网**（HTTP 调试模块除外——它本就是用来发你指定的请求的）。
> `lib/` 下的 `beautify.js`、`terser.min.js` 为随仓库内置的离线 JS 库，供 JS 引擎调用。

## 打包成可执行文件（exe）

无需装 Python 也能运行。直接去 [Releases](https://github.com/junes666/dev-debug-kit/releases) 下载打好的 Windows 免安装版，或自行打包：

```bat
:: Windows：双击 build.bat，或手动执行
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --clean devdebug.spec
:: 产物：dist\开发调试\ 文件夹（onedir 模式），双击其中的 开发调试.exe 运行
```

> 采用 **onedir（文件夹）模式**：启动无需每次解包，**秒启动**；整个 `dist\开发调试\` 文件夹一起分发（可压缩成 zip）。
> Linux / macOS 打包本平台版本：`./build.sh`。备用打包方案见 `setup_cxfreeze.py`。

> Releases 页提供已打好的 Windows 版下载（免安装，解压即用）。

## 目录结构

```
开发调试/
├── main.py                 # 入口：主窗口 + 侧栏导航 + 主题切换
├── requirements.txt
├── run.sh / run.bat        # 一键启动
├── app/
│   ├── theme.py            # 暗/亮双主题 QSS 设计系统
│   ├── widgets.py          # 共享控件(Card / 代码编辑器 / Toast / 按钮…)
│   ├── jsengine.py         # QuickJS 封装(运行 / 格式化 / 压缩 / 变量解析)
│   ├── jsonkit.py          # JSON 解析 / 树 / JSONPath / 深度对比
│   └── modules/            # 六大功能模块
│       ├── http_tool.py    ├── js_tool.py     ├── json_tool.py
│       ├── jsondiff_tool.py ├── codec_tool.py  └── qrcode_tool.py
└── lib/                    # 内置离线 JS 库(beautify / terser)
```

## 特性

- 🌙 内置深色 / 浅色主题一键切换
- 📴 安装后完全离线，隐私安全
- 🧩 模块化：每个工具独立解耦，容错加载（单个模块出错不影响整体）

---

MIT License · 仅供学习与开发调试使用
