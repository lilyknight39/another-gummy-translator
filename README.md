# Another Gummy Translator

这是对[原始Gummy Translator](https://github.com/original-author/gummy-translator)的一个分支(Fork)，进行了重构和增强。感谢原项目作者创建的原始项目。

一个现代化、跨平台的实时语音翻译字幕工具，支持多语言翻译、文本朗读和自定义界面。

## 特性

- ✨ **实时翻译**：将语音实时转换为文字并翻译成多种语言
- 🌐 **多语言支持**：支持多种语言之间的互译
- 🎨 **现代化界面**：支持浅色/深色主题，可调整透明度
- 📌 **浮动字幕**：窗口可置顶，便于在观看视频或会议时使用
- 🔊 **文本朗读**：支持将翻译结果转换为语音（TTS）
- 🔒 **安全存储**：API密钥加密存储
- 🖥️ **跨平台兼容**：支持Windows、macOS和Linux

## 安装

### 从发布版安装

- todo

### 从源码安装

```bash
# 克隆仓库
git clone https://github.com/lilyknight39/gummy-translator.git
cd gummy-translator

# 安装依赖
pip install -r requirements.txt

# 运行程序
python gummy.py
```

## 快速开始

1. 首次运行时，会显示设置向导
2. 输入API密钥（支持阿里云灵积模型服务）
3. 选择源语言和目标语言
4. 开始使用！程序会自动捕获系统音频并进行翻译

## 使用说明

### 主界面

- **顶部工具栏**：包含设置、置顶、暂停、清空和帮助按钮
- **左侧面板**：显示原始语言文本
- **右侧面板**：显示翻译后的文本
- **状态栏**：显示当前状态和错误信息

### 快捷键

- `Ctrl+S`：打开设置
- `Ctrl+P`：切换置顶状态
- `Ctrl+Space`：暂停/继续翻译
- `Ctrl+L`：清空文本
- `Esc`：隐藏窗口（可通过系统托盘图标重新显示）

### 系统托盘

程序最小化后会在系统托盘显示图标，可以：
- 左键点击：显示/隐藏主窗口
- 右键点击：显示菜单（设置、置顶、暂停等）

## 配置选项

### 外观设置

- **主题**：浅色、深色或跟随系统
- **背景透明度**：调整窗口透明度（30%-100%）
- **字体大小**：调整文本大小（8-24pt）
- **显示窗口标题栏**：开启或关闭窗口标题栏

### 功能设置

- **启用文本朗读**：将翻译结果转换为语音
- **API设置**：配置API密钥和区域

### 语言设置

- **源语言**：选择需要翻译的语言
- **目标语言**：选择翻译目标语言

## API支持

本程序使用阿里云灵积模型服务API进行语音识别和翻译。您需要：

1. 注册[阿里云账号](https://www.aliyun.com/)
2. 开通[灵积模型服务](https://help.aliyun.com/product/342501.html)
3. 创建API密钥并在程序设置中配置

## 系统要求
- todo

## 常见问题
- todo

## 致谢

本项目基于原始的 [Gummy Translator](https://github.com/original-author/gummy-translator) 项目进行了重构和增强。感谢原项目作者提供的灵感和基础代码结构。主要改进包括：

- 现代化UI界面，支持深色/浅色主题
- 跨平台兼容性优化
- 添加系统托盘支持
- 增强的设置管理
- API密钥安全存储
- 文本朗读功能
- 性能优化

同时感谢以下开源项目的支持：
- [wxPython](https://www.wxpython.org/) - 跨平台GUI框架
- [dashscope](https://www.dashscope.com/) - 阿里云灵积模型服务SDK
- [pyaudio](https://people.csail.mit.edu/hubert/pyaudio/) - 音频处理库
- [cryptography](https://cryptography.io/) - 加密库

## 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。
