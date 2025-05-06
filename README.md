# gummy-translator

## 项目描述

- gummy-translator 是一个实时翻译字幕工具，修改自阿里云官方例子，通过调用百炼平台的 Gummy 实时语音翻译模型和 SiliconFlow CosyVoice 流式语音合成，实现低延迟、实时的同声传译和实时双语字幕。

## 主要功能

- 实时语音识别和翻译
- 语音合成
- 浮动字幕窗口

## 准备工作

- 开通**阿里云账号**及**阿里云百炼模型服务**、创建阿里云百炼**API\_KEY**并进行必要的**环境配置**，以及安装阿里云百炼**DashScope SDK**，有关步骤的向导请参见[运行示例代码的前提条件](./PREREQUISITES.md)。

## 使用方法

#### 克隆项目

```bash
git clone https://github.com/ConstantinopleMayor/gummy-translator.git
```
- 或者通过[`Download Zip`](https://github.com/ConstantinopleMayor/gummy-translator/archive/refs/heads/master.zip)下载源代码，并在本地解压到文件。

#### 安装依赖

- cd 到 `项目目录` 下，执行以下命令来安装依赖：

```bash
pip install -r requirements.txt
```

#### 配置APIkey

- 在 `gummy_tanslator.py` 文件中设置SiliconFlow的api-key及voice。

#### 运行程序

```bash
python gummy_tanslator.py
```
#### 功能（快捷键）

- Alt+T/t 切换颜色模式（默认浅色模式）
- Alt+up/down 增减亮度
- Alt+s 启用/禁用tts（默认禁用）

## 许可证

- 本项目遵循MIT许可证，参考使用了以下项目：
- [aliyun/alibabacloud-bailian-speech-demo]  
  - Source: [alibabacloud-bailian-speech-demo](https://github.com/aliyun/alibabacloud-bailian-speech-demo)
  - License: MIT  
  - Copyright (c) [2024] [Alibaba Cloud]
