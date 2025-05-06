#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import time
import json
import ctypes
import threading
import tempfile
import configparser
import webbrowser
import subprocess
from datetime import datetime

import wx
import wx.adv
import wx.richtext as rt
from wx.lib.newevent import NewEvent

import dashscope
from dashscope.audio.asr import TranslationRecognizerRealtime, TranscriptionResult, TranslationResult, TranslationRecognizerCallback
import pyaudio
import queue
import requests

from threading import RLock
from collections import deque

import logging

# 配置日志
def setup_logger():
    """设置日志记录器"""
    logger = logging.getLogger('TranslationSubtitle')
    logger.setLevel(logging.DEBUG)
    
    # 确定日志文件路径
    if getattr(sys, 'frozen', False):
        # PyInstaller打包后的路径
        log_dir = os.path.dirname(sys.executable)
    else:
        # 开发环境路径
        log_dir = os.path.dirname(os.path.abspath(__file__))
    
    log_file = os.path.join(log_dir, 'subtitle.log')
    
    # 文件处理器
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)
    
    # 控制台处理器
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    
    # 格式化器
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)
    
    # 添加处理器
    logger.addHandler(file_handler)
    logger.addHandler(console_handler)
    
    return logger

# 在程序入口处调用
logger = setup_logger()

# 创建自定义事件
UpdateTextEvent, EVT_UPDATE_TEXT = NewEvent()
UpdateTranslationEvent, EVT_UPDATE_TRANSLATION = NewEvent()
UpdateStatusEvent, EVT_UPDATE_STATUS = NewEvent()

# 平台辅助类 - 处理跨平台兼容性
class PlatformHelper:
    """处理不同平台特定功能的辅助类"""
    
    @staticmethod
    def is_windows():
        return wx.Platform == "__WXMSW__"
    
    @staticmethod
    def is_macos():
        return wx.Platform == "__WXMAC__"
    
    @staticmethod
    def is_linux():
        return wx.Platform == "__WXGTK__"
    
    @staticmethod
    def setup_dpi_awareness():
        """设置DPI感知"""
        if PlatformHelper.is_windows():
            try:
                # Windows 10及以上版本
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                try:
                    # Windows 8.1及以下版本
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass  # 不支持DPI感知
        # macOS和Linux自动处理DPI
    
    @staticmethod
    def set_window_transparency(window, alpha):
        """设置窗口透明度"""
        if PlatformHelper.is_windows():
            # Windows平台使用Win32 API
            hwnd = window.GetHandle()
            ex_style = ctypes.windll.user32.GetWindowLongW(hwnd, -20)  # GWL_EXSTYLE
            if not (ex_style & 0x80000):  # WS_EX_LAYERED
                ctypes.windll.user32.SetWindowLongW(hwnd, -20, ex_style | 0x80000)
            ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, alpha, 0x02)
        else:
            # macOS和Linux使用wxPython原生方法
            window.SetTransparent(alpha)
    
    @staticmethod
    def get_system_font():
        """获取系统默认字体"""
        if PlatformHelper.is_windows():
            return "Segoe UI"
        elif PlatformHelper.is_macos():
            return "SF Pro"
        else:  # Linux
            return "Noto Sans"
    
    @staticmethod
    def toggle_window_style(window, has_titlebar):
        """切换窗口样式（有/无标题栏）- 保留以兼容现有代码"""
        return PlatformHelper.set_window_style(window, has_titlebar)

    @staticmethod
    def set_window_style(window, has_titlebar):
        """设置窗口样式（有/无标题栏）"""
        if PlatformHelper.is_windows():
            if has_titlebar:
                new_style = window.GetWindowStyle() | wx.CAPTION
            else:
                new_style = window.GetWindowStyle() & ~wx.CAPTION
        elif PlatformHelper.is_macos():
            if has_titlebar:
                new_style = wx.DEFAULT_FRAME_STYLE | wx.STAY_ON_TOP
            else:
                # macOS无标题栏样式
                new_style = wx.FRAME_NO_TASKBAR | wx.STAY_ON_TOP | wx.BORDER_NONE
        else:  # Linux
            if has_titlebar:
                new_style = window.GetWindowStyle() | wx.CAPTION
            else:
                new_style = window.GetWindowStyle() & ~wx.CAPTION
        
        window.SetWindowStyle(new_style)
        return new_style

# 配置管理类
class ConfigManager:
    def __init__(self, config_path):
        self.config_path = config_path
        self.config = configparser.ConfigParser()
        self.load()
    
    def load(self):
        if os.path.exists(self.config_path):
            self.config.read(self.config_path, encoding='utf-8')
        
        # 确保所有必要的配置节存在
        if 'UI' not in self.config:
            self.config['UI'] = {}
        if 'Features' not in self.config:
            self.config['Features'] = {}
        if 'API' not in self.config:
            self.config['API'] = {}
        if 'Language' not in self.config:
            self.config['Language'] = {
                'source_language': 'zh-CN',
                'target_language': 'en-US'
            }
    
    def save(self):
        with open(self.config_path, 'w', encoding='utf-8') as config_file:
            self.config.write(config_file)
    
    def get(self, section, key, fallback=None):
        return self.config.get(section, key, fallback=fallback)
    
    def getboolean(self, section, key, fallback=None):
        return self.config.getboolean(section, key, fallback=fallback)
    
    def set(self, section, key, value):
        self.config.set(section, key, str(value))
    
    def save_api_key(self, api_key, key_type='api_key'):
        """加密并保存API密钥
        
        Args:
            api_key (str): 要保存的API密钥
            key_type (str): 密钥类型，默认为'api_key'，也可以是'tts_api_key'等
        """
        if not api_key:
            self.set('API', key_type, '')
            self.set('API', f"{key_type}_encoded", '')
            return
            
        try:
            # 使用更安全的加密方法
            import base64
            import os
            from cryptography.fernet import Fernet
            from cryptography.hazmat.primitives import hashes
            from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
            
            # 使用机器标识作为密码盐
            salt = (os.getenv('COMPUTERNAME', '') + os.getenv('USER', '') + 
                    os.getenv('HOSTNAME', '')).encode()
            if not salt:
                salt = b'TranslationSubtitleApp'
            
            # 生成密钥
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=100000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(b"TranslationSubtitleAppKey"))
            cipher = Fernet(key)
            
            # 加密API密钥
            encrypted_key = cipher.encrypt(api_key.encode()).decode()
            self.set('API', f"{key_type}_encoded", encrypted_key)
            # 清除明文密钥
            self.set('API', key_type, '')
        except ImportError:
            # 如果缺少加密库，回退到基本的编码
            import base64
            logger.warning("缺少高级加密库，使用基本编码方式存储API密钥")
            encoded_key = base64.b64encode(api_key.encode()).decode()
            self.set('API', f"{key_type}_encoded", encoded_key)
            self.set('API', key_type, '')

    def load_api_key(self, key_type='api_key'):
        """加载并解密API密钥
        
        Args:
            key_type (str): 密钥类型，默认为'api_key'，也可以是'tts_api_key'等
            
        Returns:
            str: 解密后的API密钥，如果没有则返回空字符串
        """
        # 先尝试获取加密的密钥
        encoded_key = self.get('API', f"{key_type}_encoded", fallback='')
        
        if encoded_key:
            try:
                # 尝试使用高级解密方法
                import base64
                from cryptography.fernet import Fernet
                from cryptography.hazmat.primitives import hashes
                from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
                import os
                
                # 使用与加密相同的盐
                salt = (os.getenv('COMPUTERNAME', '') + os.getenv('USER', '') + 
                        os.getenv('HOSTNAME', '')).encode()
                if not salt:
                    salt = b'TranslationSubtitleApp'
                
                kdf = PBKDF2HMAC(
                    algorithm=hashes.SHA256(),
                    length=32,
                    salt=salt,
                    iterations=100000,
                )
                key = base64.urlsafe_b64encode(kdf.derive(b"TranslationSubtitleAppKey"))
                cipher = Fernet(key)
                
                # 解密API密钥
                return cipher.decrypt(encoded_key.encode()).decode()
            except ImportError:
                # 如果缺少解密库，尝试基本解码
                import base64
                try:
                    return base64.b64decode(encoded_key.encode()).decode()
                except Exception:
                    return ''
            except Exception as e:
                logger.error(f"解密API密钥失败: {e}", exc_info=True)
                return ''
        
        # 如果没有加密密钥，返回明文密钥（向后兼容）
        return self.get('API', key_type, fallback='')

# 现代化UI组件
class ModernTextPanel(wx.Panel):
    """现代化文本面板，支持圆角和自定义样式"""
    
    def __init__(self, parent, bg_color, text_color, accent_color):
        super().__init__(parent)
        self.bg_color = bg_color
        self.text_color = text_color
        self.accent_color = accent_color
        self.corner_radius = 10
        self.setup_ui()
    
    def setup_ui(self):
        """设置UI组件"""
        self.SetBackgroundColour(self.bg_color)
        self.Bind(wx.EVT_PAINT, self.on_paint)
        
        # 创建富文本控件
        self.text_box = rt.RichTextCtrl(
            self,
            style=wx.NO_BORDER | rt.RE_READONLY | rt.RE_MULTILINE
        )
        
        # 设置文本框样式
        self.text_box.SetBackgroundColour(self.bg_color)
        
        # 设置文本样式
        attr = rt.RichTextAttr()
        attr.SetTextColour(self.text_color)
        attr.SetLineSpacing(16)
        attr.SetAlignment(wx.TEXT_ALIGNMENT_LEFT)
        self.text_box.SetDefaultStyle(attr)
        
        # 设置字体
        font_name = PlatformHelper.get_system_font()
        font = wx.Font(
            wx.FontInfo(14)
            .FaceName(font_name)
            .AntiAliased(True)
        )
        self.text_box.SetFont(font)
        
        # 布局
        sizer = wx.BoxSizer(wx.VERTICAL)
        sizer.Add(self.text_box, 1, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(sizer)
    
    def on_paint(self, event):
        """绘制圆角面板"""
        dc = wx.PaintDC(self)
        gc = wx.GraphicsContext.Create(dc)
        
        if gc:
            width, height = self.GetSize()
            
            # 创建圆角矩形路径
            path = gc.CreatePath()
            path.AddRoundedRectangle(0, 0, width, height, self.corner_radius)
            
            # 设置画刷（填充）
            brush = gc.CreateBrush(wx.Brush(self.bg_color))
            gc.SetBrush(brush)
            
            # 设置画笔（边框）
            border_color = wx.Colour(self.bg_color.Red() + 10, 
                                     self.bg_color.Green() + 10, 
                                     self.bg_color.Blue() + 10)
            pen = gc.CreatePen(wx.Pen(border_color, 1))
            gc.SetPen(pen)
            
            # 绘制圆角矩形
            gc.DrawPath(path)
        
        event.Skip()
    
    def set_colors(self, bg_color, text_color, accent_color):
        """更新面板颜色"""
        self.bg_color = bg_color
        self.text_color = text_color
        self.accent_color = accent_color
        
        self.SetBackgroundColour(bg_color)
        self.text_box.SetBackgroundColour(bg_color)
        
        attr = rt.RichTextAttr()
        attr.SetTextColour(text_color)
        self.text_box.SetDefaultStyle(attr)
        
        self.Refresh()
    
    def set_font_size(self, size):
        """设置字体大小"""
        font_name = PlatformHelper.get_system_font()
        font = wx.Font(
            wx.FontInfo(size)
            .FaceName(font_name)
            .AntiAliased(True)
        )
        self.text_box.SetFont(font)
        self.Refresh()

class ModernSettingsDialog(wx.Dialog):
    """现代化设置对话框"""
    def __init__(self, parent, config):
        super().__init__(
            parent, 
            title="设置",
            style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER
        )
        
        self.config = config
        self.parent = parent
        self.setup_ui()
        self.Center()
    
    def setup_ui(self):
        """设置UI组件"""
        panel = wx.Panel(self)
        
        # 获取当前系统主题是否为深色
        is_dark = self.parent.is_dark_mode
        
        # 根据主题设置颜色
        if is_dark:
            bg_color = wx.Colour(40, 44, 52)
            text_color = wx.Colour(220, 223, 228)
        else:
            bg_color = wx.Colour(248, 249, 250)
            text_color = wx.Colour(33, 37, 43)
        
        panel.SetBackgroundColour(bg_color)
        
        # 创建字体
        font_name = PlatformHelper.get_system_font()
        font = wx.Font(
            wx.FontInfo(12)
            .FaceName(font_name)
            .AntiAliased(True)
        )
        
        # 创建标题
        title = wx.StaticText(panel, label="翻译字幕设置")
        title.SetFont(
            wx.Font(
                wx.FontInfo(16)
                .FaceName(font_name)
                .Bold()
                .AntiAliased(True)
            )
        )
        title.SetForegroundColour(text_color)
        
        # 创建设置项
        # 1. 外观设置
        appearance_box = wx.StaticBox(panel, label="外观设置")
        appearance_box.SetFont(font)
        appearance_box.SetForegroundColour(text_color)
        
        # 主题选择
        theme_label = wx.StaticText(panel, label="主题:")
        theme_label.SetFont(font)
        theme_label.SetForegroundColour(text_color)
        
        self.theme_choice = wx.Choice(panel, choices=["浅色", "深色", "跟随系统"])
        self.theme_choice.SetFont(font)
        
        # 根据当前设置选择
        theme_setting = self.config.get('UI', 'theme', fallback='system')
        if theme_setting == 'light':
            self.theme_choice.SetSelection(0)
        elif theme_setting == 'dark':
            self.theme_choice.SetSelection(1)
        else:
            self.theme_choice.SetSelection(2)
        
        # 透明度设置
        opacity_label = wx.StaticText(panel, label="背景透明度:")
        opacity_label.SetFont(font)
        opacity_label.SetForegroundColour(text_color)
        
        opacity = int(self.config.get('UI', 'opacity', fallback='80'))
        self.opacity_slider = wx.Slider(
            panel, 
            value=opacity,
            minValue=30,
            maxValue=100,
            style=wx.SL_HORIZONTAL | wx.SL_LABELS
        )
        
        # 字体大小设置
        font_size_label = wx.StaticText(panel, label="字体大小:")
        font_size_label.SetFont(font)
        font_size_label.SetForegroundColour(text_color)
        
        font_size = int(self.config.get('UI', 'font_size', fallback='14'))
        self.font_size_spinner = wx.SpinCtrl(
            panel,
            min=8,
            max=24,
            initial=font_size
        )
        self.font_size_spinner.SetFont(font)
        
        # 2. 功能设置
        function_box = wx.StaticBox(panel, label="功能设置")
        function_box.SetFont(font)
        function_box.SetForegroundColour(text_color)

        # TTS开关
        self.enable_tts = wx.CheckBox(panel, label="启用文本朗读 (TTS)")
        self.enable_tts.SetFont(font)
        self.enable_tts.SetForegroundColour(text_color)
        
        tts_enabled = self.config.getboolean('Features', 'enable_tts', fallback=False)
        self.enable_tts.SetValue(tts_enabled)
        
        # 标题栏开关
        self.show_titlebar = wx.CheckBox(panel, label="显示窗口标题栏")
        self.show_titlebar.SetFont(font)
        self.show_titlebar.SetForegroundColour(text_color)
        
        titlebar_enabled = self.config.getboolean('UI', 'show_titlebar', fallback=True)
        self.show_titlebar.SetValue(titlebar_enabled)
        
        # 3. API设置
        api_box = wx.StaticBox(panel, label="API设置")
        api_box.SetFont(font)
        api_box.SetForegroundColour(text_color)
        
        # API密钥
        api_key_label = wx.StaticText(panel, label="API密钥:")
        api_key_label.SetFont(font)
        api_key_label.SetForegroundColour(text_color)
        
        api_key = self.config.get('API', 'api_key', fallback='')
        self.api_key_text = wx.TextCtrl(panel, value=api_key, style=wx.TE_PASSWORD)
        self.api_key_text.SetFont(font)

        # TTS API密钥
        tts_api_key_label = wx.StaticText(panel, label="TTS API密钥:")
        tts_api_key_label.SetFont(font)
        tts_api_key_label.SetForegroundColour(text_color)

        tts_api_key = self.config.get('API', 'tts_api_key', fallback='')
        self.tts_api_key_text = wx.TextCtrl(panel, value=tts_api_key, style=wx.TE_PASSWORD)
        self.tts_api_key_text.SetFont(font)
        
        # API区域
        api_region_label = wx.StaticText(panel, label="API区域:")
        api_region_label.SetFont(font)
        api_region_label.SetForegroundColour(text_color)
        
        api_region = self.config.get('API', 'region', fallback='eastasia')
        self.api_region_text = wx.TextCtrl(panel, value=api_region)
        self.api_region_text.SetFont(font)
        
        # 4. 语言设置
        language_box = wx.StaticBox(panel, label="语言设置")
        language_box.SetFont(font)
        language_box.SetForegroundColour(text_color)
        
        # 源语言选择
        source_lang_label = wx.StaticText(panel, label="源语言:")
        source_lang_label.SetFont(font)
        source_lang_label.SetForegroundColour(text_color)
        
        # 获取支持的语言列表
        supported_langs = list(TranslationService.SUPPORTED_LANGUAGES.items())
        lang_choices = [f"{code} ({name})" for code, name in supported_langs]
        
        # 源语言下拉框
        self.source_lang_choice = wx.Choice(panel, choices=lang_choices)
        self.source_lang_choice.SetFont(font)
        
        # 设置当前选择
        current_source = self.config.get('Language', 'source_language', fallback='zh-CN')
        for i, (code, _) in enumerate(supported_langs):
            if code == current_source:
                self.source_lang_choice.SetSelection(i)
                break
        
        # 目标语言选择
        target_lang_label = wx.StaticText(panel, label="目标语言:")
        target_lang_label.SetFont(font)
        target_lang_label.SetForegroundColour(text_color)
        
        # 目标语言下拉框
        self.target_lang_choice = wx.Choice(panel, choices=lang_choices)
        self.target_lang_choice.SetFont(font)
        
        # 设置当前选择
        current_target = self.config.get('Language', 'target_language', fallback='en-US')
        for i, (code, _) in enumerate(supported_langs):
            if code == current_target:
                self.target_lang_choice.SetSelection(i)
                break

        # 按钮
        button_sizer = wx.StdDialogButtonSizer()
        
        self.ok_button = wx.Button(panel, wx.ID_OK, "确定")
        self.ok_button.SetFont(font)
        button_sizer.AddButton(self.ok_button)
        
        self.cancel_button = wx.Button(panel, wx.ID_CANCEL, "取消")
        self.cancel_button.SetFont(font)
        button_sizer.AddButton(self.cancel_button)
        
        button_sizer.Realize()
        
        # 布局
        # 外观设置布局
        appearance_sizer = wx.StaticBoxSizer(appearance_box, wx.VERTICAL)
        
        theme_sizer = wx.BoxSizer(wx.HORIZONTAL)
        theme_sizer.Add(theme_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        theme_sizer.Add(self.theme_choice, 1, wx.EXPAND)
        appearance_sizer.Add(theme_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        opacity_sizer = wx.BoxSizer(wx.HORIZONTAL)
        opacity_sizer.Add(opacity_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        opacity_sizer.Add(self.opacity_slider, 1, wx.EXPAND)
        appearance_sizer.Add(opacity_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        font_size_sizer = wx.BoxSizer(wx.HORIZONTAL)
        font_size_sizer.Add(font_size_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        font_size_sizer.Add(self.font_size_spinner, 0)
        appearance_sizer.Add(font_size_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        # 功能设置布局
        function_sizer = wx.StaticBoxSizer(function_box, wx.VERTICAL)
        function_sizer.Add(self.enable_tts, 0, wx.ALL, 5)
        function_sizer.Add(self.show_titlebar, 0, wx.ALL, 5)
        
        # API设置布局
        api_sizer = wx.StaticBoxSizer(api_box, wx.VERTICAL)
        
        api_key_sizer = wx.BoxSizer(wx.HORIZONTAL)
        api_key_sizer.Add(api_key_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        api_key_sizer.Add(self.api_key_text, 1, wx.EXPAND)
        api_sizer.Add(api_key_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        api_region_sizer = wx.BoxSizer(wx.HORIZONTAL)
        api_region_sizer.Add(api_region_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        api_region_sizer.Add(self.api_region_text, 1, wx.EXPAND)
        api_sizer.Add(api_region_sizer, 0, wx.EXPAND | wx.ALL, 5)

        api_sizer.Add(tts_api_key_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        api_sizer.Add(self.tts_api_key_text, 1, wx.EXPAND)
        
        # 语言设置布局
        language_sizer = wx.StaticBoxSizer(language_box, wx.VERTICAL)
        
        source_lang_sizer = wx.BoxSizer(wx.HORIZONTAL)
        source_lang_sizer.Add(source_lang_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        source_lang_sizer.Add(self.source_lang_choice, 1, wx.EXPAND)
        language_sizer.Add(source_lang_sizer, 0, wx.EXPAND | wx.ALL, 5)
        
        target_lang_sizer = wx.BoxSizer(wx.HORIZONTAL)
        target_lang_sizer.Add(target_lang_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        target_lang_sizer.Add(self.target_lang_choice, 1, wx.EXPAND)
        language_sizer.Add(target_lang_sizer, 0, wx.EXPAND | wx.ALL, 5)

        # 主布局
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        main_sizer.Add(title, 0, wx.ALL | wx.CENTER, 10)
        main_sizer.Add(appearance_sizer, 0, wx.EXPAND | wx.ALL, 10)
        main_sizer.Add(function_sizer, 0, wx.EXPAND | wx.ALL, 10)
        main_sizer.Add(api_sizer, 0, wx.EXPAND | wx.ALL, 10)
        main_sizer.Add(button_sizer, 0, wx.EXPAND | wx.ALL, 10)
        main_sizer.Add(language_sizer, 0, wx.EXPAND | wx.ALL, 10)
        
        panel.SetSizer(main_sizer)
        
        # 调整对话框大小
        main_sizer.Fit(self)
        self.SetMinSize(wx.Size(400, -1))
        
        # 绑定事件
        self.ok_button.Bind(wx.EVT_BUTTON, self.on_ok)
    
    def on_ok(self, event):
        """保存设置"""
        # 保存主题设置
        theme_selection = self.theme_choice.GetSelection()
        if theme_selection == 0:
            self.config.set('UI', 'theme', 'light')
        elif theme_selection == 1:
            self.config.set('UI', 'theme', 'dark')
        else:
            self.config.set('UI', 'theme', 'system')
        
        # 保存语言设置
        supported_langs = list(TranslationService.SUPPORTED_LANGUAGES.items())
        source_idx = self.source_lang_choice.GetSelection()
        target_idx = self.target_lang_choice.GetSelection()
        
        if source_idx != wx.NOT_FOUND:
            source_lang = supported_langs[source_idx][0]
            self.config.set('Language', 'source_language', source_lang)
        
        if target_idx != wx.NOT_FOUND:
            target_lang = supported_langs[target_idx][0]
            self.config.set('Language', 'target_language', target_lang)

        # 保存透明度设置
        opacity = str(self.opacity_slider.GetValue())
        self.config.set('UI', 'opacity', opacity)
        
        # 保存字体大小
        font_size = str(self.font_size_spinner.GetValue())
        self.config.set('UI', 'font_size', font_size)
        
        # 保存TTS设置
        tts_enabled = str(self.enable_tts.GetValue())
        self.config.set('Features', 'enable_tts', tts_enabled)
        
        # 保存标题栏设置
        titlebar_enabled = str(self.show_titlebar.GetValue())
        self.config.set('UI', 'show_titlebar', titlebar_enabled)
        
        # 保存API设置（使用加密方法）
        api_key = self.api_key_text.GetValue()
        self.config.save_api_key(api_key, 'api_key')
        
        api_region = self.api_region_text.GetValue()
        self.config.set('API', 'region', api_region)

        # 保存TTS API设置（使用加密方法）
        tts_api_key = self.tts_api_key_text.GetValue()
        self.config.save_api_key(tts_api_key, 'tts_api_key')
        
        # 保存配置
        self.config.save()
        
        # 关闭对话框
        self.EndModal(wx.ID_OK)

class ModernFloatingSubtitleWindow(wx.Frame):
    """现代化浮动字幕窗口，支持跨平台"""
    def show_first_run_wizard(self):
        """显示首次运行向导"""
        # 检查是否是首次运行
        is_first_run = self.config_manager.getboolean('App', 'first_run', fallback=True)
        
        if is_first_run:
            # 创建向导对话框
            wizard = wx.adv.Wizard(self, -1, "实时翻译字幕设置向导")
            page1 = WizardPageOne(wizard)
            page2 = WizardPageTwo(wizard, self.config_manager)
            
            # 设置页面顺序
            wx.adv.WizardPageSimple.Chain(page1, page2)
            
            # 运行向导
            if wizard.RunWizard(page1):
                # 用户完成了向导
                self.config_manager.set('App', 'first_run', 'False')
                self.config_manager.save()
                self.apply_settings()
            
            wizard.Destroy()

    def __init__(self):
        """初始化窗口"""
        # 设置DPI感知
        PlatformHelper.setup_dpi_awareness()
        
        # 创建窗口
        super().__init__(
            None,
            title="实时翻译字幕",
            style=wx.DEFAULT_FRAME_STYLE | wx.STAY_ON_TOP
        )
        
        # 初始化变量
        self.is_dragging = False
        self.drag_start_pos = None
        self.has_titlebar = True
        self.is_dark_mode = self._detect_system_theme()
        
        # 初始化文本缓冲区
        MAX_BUFFER_SIZE = 20
        self.chinese_text_buffer = deque([['', '']], maxlen=MAX_BUFFER_SIZE)  # 源语言文本缓冲区
        self.target_language_text_buffer = deque([['', '']], maxlen=MAX_BUFFER_SIZE)  # 目标语言文本缓冲区

        # 设置定时器用于更新文本
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
        self.timer.Start(200)  # 每200毫秒更新一次

        # 添加服务监控定时器
        self.monitor_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_monitor_timer, self.monitor_timer)
        self.monitor_timer.Start(30000)  # 每30秒检查一次

        # 加载配置
        self.load_config()
        
        # 设置窗口属性
        self.setup_window()
        
        # 创建UI组件
        self.create_ui()
        
        # 绑定事件
        self.bind_events()
        
        # 创建系统托盘图标
        self.create_tray_icon()
        
        # 启动自动保存配置的定时器
        self.auto_save_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_auto_save, self.auto_save_timer)
        self.auto_save_timer.Start(60000)  # 每分钟保存一次配置
        
        # 显示首次运行向导
        self.show_first_run_wizard()

        # 显示窗口
        self.Center()
        self.Show()
    
    def on_monitor_timer(self, event):
        """定期监控服务状态"""
        if hasattr(self, 'translation_service'):
            self.translation_service.check_and_restart()
        event.Skip()
    
    def on_timer(self, event):
        try:
            updates = self.translation_service.process_queue_safely(10)
            if updates:
                for transcription_result, translation_result in updates:
                    self.update_text(transcription_result, translation_result)
                self.Refresh()
        except Exception as e:
            logger.error(f"定时器更新出错: {e}", exc_info=True)
        event.Skip()

    def _detect_system_theme(self):
        """检测系统主题是否为深色模式"""
        if PlatformHelper.is_windows():
            try:
                # Windows 10及以上版本
                import winreg
                registry = winreg.ConnectRegistry(None, winreg.HKEY_CURRENT_USER)
                key = winreg.OpenKey(registry, r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
                return value == 0
            except Exception:
                return False
        elif PlatformHelper.is_macos():
            try:
                # macOS深色模式检测
                result = subprocess.run(
                    ["defaults", "read", "-g", "AppleInterfaceStyle"],
                    capture_output=True,
                    text=True
                )
                return "Dark" in result.stdout
            except Exception:
                return False
        else:
            # Linux深色模式检测（GNOME）
            try:
                result = subprocess.run(
                    ["gsettings", "get", "org.gnome.desktop.interface", "gtk-theme"],
                    capture_output=True,
                    text=True
                )
                return "dark" in result.stdout.lower()
            except Exception:
                return False
    
    def load_config(self):
        """加载配置"""
        # 确定配置文件路径
        if getattr(sys, 'frozen', False):
            # PyInstaller打包后的路径
            app_dir = os.path.dirname(sys.executable)
        else:
            # 开发环境路径
            app_dir = os.path.dirname(os.path.abspath(__file__))
        
        self.config_path = os.path.join(app_dir, 'config.ini')
        
        # 使用配置管理类
        self.config_manager = ConfigManager(self.config_path)
        self.config = self.config_manager.config  # 保持兼容性
        
        # 应用主题设置
        theme_setting = self.config_manager.get('UI', 'theme', fallback='system')
        if theme_setting == 'light':
            self.is_dark_mode = False
        elif theme_setting == 'dark':
            self.is_dark_mode = True
        # 如果是'system'，则使用之前检测到的系统主题
        
        # 加载其他UI设置
        self.opacity = int(self.config_manager.get('UI', 'opacity', fallback='80'))
        self.font_size = int(self.config_manager.get('UI', 'font_size', fallback='14'))
        self.has_titlebar = self.config_manager.getboolean('UI', 'show_titlebar', fallback=True)
        
        # 设置颜色方案
        self.update_color_scheme()
    
    def update_color_scheme(self):
        """根据主题更新颜色方案"""
        if self.is_dark_mode:
            self.bg_color = wx.Colour(40, 44, 52)
            self.text_color = wx.Colour(220, 223, 228)
            self.accent_color = wx.Colour(97, 175, 239)
        else:
            self.bg_color = wx.Colour(248, 249, 250)
            self.text_color = wx.Colour(33, 37, 43)
            self.accent_color = wx.Colour(66, 139, 202)
    
    def setup_window(self):
        """设置窗口属性"""
        # 设置窗口大小
        self.SetSize(wx.Size(600, 200))
        
        # 设置窗口样式
        if not self.has_titlebar:
            PlatformHelper.set_window_style(self, False)
        
        # 设置窗口透明度
        alpha = int(255 * (self.opacity / 100))
        PlatformHelper.set_window_transparency(self, alpha)
        
        # 设置窗口图标
        if getattr(sys, 'frozen', False):
            # PyInstaller打包后的路径
            icon_path = os.path.join(os.path.dirname(sys.executable), 'icon.ico')
        else:
            # 开发环境路径
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')
        
        if os.path.exists(icon_path):
            self.SetIcon(wx.Icon(icon_path))
    
    def create_ui(self):
        """创建UI组件"""
        # 创建主面板
        self.panel = wx.Panel(self)
        self.panel.SetBackgroundColour(self.bg_color)
        
        # 创建中文面板
        self.chinese_panel = ModernTextPanel(
            self.panel, 
            self.bg_color, 
            self.text_color,
            self.accent_color
        )
        self.chinese_text_box = self.chinese_panel.text_box
        
        # 创建目标语言面板
        self.target_panel = ModernTextPanel(
            self.panel, 
            self.bg_color, 
            self.text_color,
            self.accent_color
        )
        self.target_text_box = self.target_panel.text_box
        
        # 设置字体大小
        self.chinese_panel.set_font_size(self.font_size)
        self.target_panel.set_font_size(self.font_size)
        
        # 创建状态栏
        self.status_bar = wx.StatusBar(self.panel)
        self.status_bar.SetBackgroundColour(self.bg_color)
        self.status_bar.SetForegroundColour(self.text_color)
        
        # 创建工具栏
        self.toolbar = self.create_toolbar()
        
        # 布局
        main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # 文本面板布局
        text_sizer = wx.BoxSizer(wx.HORIZONTAL)
        text_sizer.Add(self.chinese_panel, 1, wx.EXPAND | wx.RIGHT, 5)
        text_sizer.Add(self.target_panel, 1, wx.EXPAND | wx.LEFT, 5)
        
        main_sizer.Add(self.toolbar, 0, wx.EXPAND)
        main_sizer.Add(text_sizer, 1, wx.EXPAND | wx.ALL, 10)
        main_sizer.Add(self.status_bar, 0, wx.EXPAND)
        
        self.panel.SetSizer(main_sizer)
    
    def create_toolbar(self):
        """创建工具栏"""
        toolbar = wx.ToolBar(self.panel)
        toolbar.SetBackgroundColour(self.bg_color)
        
        # 设置按钮
        settings_tool = toolbar.AddTool(
            wx.ID_ANY, 
            "设置", 
            wx.ArtProvider.GetBitmap(wx.ART_INFORMATION, wx.ART_TOOLBAR),
            "打开设置"
        )
        
        # 置顶按钮
        self.pin_tool = toolbar.AddCheckTool(
            wx.ID_ANY,
            "置顶",
            wx.ArtProvider.GetBitmap(wx.ART_GO_UP, wx.ART_TOOLBAR),
            wx.NullBitmap,
            "切换窗口置顶"
        )
        toolbar.ToggleTool(self.pin_tool.GetId(), True)
        
        # 暂停按钮
        self.pause_tool = toolbar.AddCheckTool(
            wx.ID_ANY,
            "暂停",
            wx.ArtProvider.GetBitmap(wx.ART_TICK_MARK, wx.ART_TOOLBAR),
            wx.NullBitmap,
            "暂停/继续翻译"
        )
        
        # 清空按钮
        clear_tool = toolbar.AddTool(
            wx.ID_ANY,
            "清空",
            wx.ArtProvider.GetBitmap(wx.ART_DELETE, wx.ART_TOOLBAR),
            "清空文本"
        )
        
        # 帮助按钮
        help_tool = toolbar.AddTool(
            wx.ID_ANY,
            "帮助",
            wx.ArtProvider.GetBitmap(wx.ART_HELP, wx.ART_TOOLBAR),
            "帮助"
        )
        
        toolbar.Realize()
        
        # 绑定事件
        self.Bind(wx.EVT_TOOL, self.on_settings, settings_tool)
        self.Bind(wx.EVT_TOOL, self.on_toggle_pin, self.pin_tool)
        self.Bind(wx.EVT_TOOL, self.on_toggle_pause, self.pause_tool)
        self.Bind(wx.EVT_TOOL, self.on_clear, clear_tool)
        self.Bind(wx.EVT_TOOL, self.on_help, help_tool)
        
        return toolbar
    
    def bind_events(self):
        """绑定事件处理函数"""
        # 窗口事件
        self.Bind(wx.EVT_CLOSE, self.on_close)
        
        # 鼠标事件（用于无标题栏时的拖动）
        self.panel.Bind(wx.EVT_LEFT_DOWN, self.on_left_down)
        self.panel.Bind(wx.EVT_LEFT_UP, self.on_left_up)
        self.panel.Bind(wx.EVT_MOTION, self.on_mouse_move)
        
        # 右键菜单
        self.panel.Bind(wx.EVT_RIGHT_DOWN, self.on_right_down)
        
        # 自定义事件
        self.Bind(EVT_UPDATE_TEXT, self.on_update_text)
        self.Bind(EVT_UPDATE_TRANSLATION, self.on_update_translation)
        self.Bind(EVT_UPDATE_STATUS, self.on_update_status)

        # 添加键盘快捷键
        self.accel_table = []
        
        # Ctrl+S 打开设置
        id_settings = wx.ID_ANY
        self.Bind(wx.EVT_MENU, self.on_settings, id=id_settings)
        self.accel_table.append((wx.ACCEL_CTRL, ord('S'), id_settings))
        
        # Ctrl+P 切换置顶
        id_pin = wx.ID_ANY
        self.Bind(wx.EVT_MENU, self.on_toggle_pin, id=id_pin)
        self.accel_table.append((wx.ACCEL_CTRL, ord('P'), id_pin))
        
        # Ctrl+Space 暂停/继续
        id_pause = wx.ID_ANY
        self.Bind(wx.EVT_MENU, self.on_toggle_pause, id=id_pause)
        self.accel_table.append((wx.ACCEL_CTRL, wx.WXK_SPACE, id_pause))
        
        # Ctrl+L 清空文本
        id_clear = wx.ID_ANY
        self.Bind(wx.EVT_MENU, self.on_clear, id=id_clear)
        self.accel_table.append((wx.ACCEL_CTRL, ord('L'), id_clear))
        
        # Esc 隐藏窗口
        id_hide = wx.ID_ANY
        self.Bind(wx.EVT_MENU, lambda e: self.Hide(), id=id_hide)
        self.accel_table.append((wx.ACCEL_NORMAL, wx.WXK_ESCAPE, id_hide))
        
        # 设置快捷键表
        accel = wx.AcceleratorTable(self.accel_table)
        self.SetAcceleratorTable(accel)
    
    def create_tray_icon(self):
        """创建系统托盘图标"""
        self.tray_icon = wx.adv.TaskBarIcon()
        
        # 设置图标
        if getattr(sys, 'frozen', False):
            # PyInstaller打包后的路径
            icon_path = os.path.join(os.path.dirname(sys.executable), 'icon.ico')
        else:
            # 开发环境路径
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'icon.ico')
        
        if os.path.exists(icon_path):
            self.tray_icon.SetIcon(wx.Icon(icon_path), "实时翻译字幕")
        
        # 绑定事件
        self.tray_icon.Bind(wx.adv.EVT_TASKBAR_LEFT_DOWN, self.on_tray_left_click)
        self.tray_icon.Bind(wx.adv.EVT_TASKBAR_RIGHT_DOWN, self.on_tray_right_click)
    
    def create_menu(self, is_tray=False):
        """创建菜单（托盘菜单和上下文菜单）"""
        menu = wx.Menu()
        # 显示/隐藏（仅在托盘菜单中）
        if is_tray:
            show_item = menu.Append(wx.ID_ANY, "显示" if not self.IsShown() else "隐藏")
            menu.Bind(wx.EVT_MENU, self.on_toggle_show, show_item)
        
        # 设置
        settings_item = menu.Append(wx.ID_ANY, "设置")
        menu.Bind(wx.EVT_MENU, self.on_settings, settings_item)
        
        # 置顶
        pin_item = menu.Append(wx.ID_ANY, "取消置顶" if self.GetWindowStyle() & wx.STAY_ON_TOP else "置顶")
        menu.Bind(wx.EVT_MENU, self.on_toggle_pin, pin_item)
        
        # 暂停/继续
        pause_item = menu.Append(wx.ID_ANY, "继续翻译" if self.pause_tool.IsToggled() else "暂停翻译")
        menu.Bind(wx.EVT_MENU, self.on_toggle_pause, pause_item)
        
        # 清空
        clear_item = menu.Append(wx.ID_ANY, "清空文本")
        menu.Bind(wx.EVT_MENU, self.on_clear, clear_item)
        
        menu.AppendSeparator()
        
        # 退出
        exit_item = menu.Append(wx.ID_ANY, "退出")
        menu.Bind(wx.EVT_MENU, self.on_exit, exit_item)
        
        return menu
    
    def create_context_menu(self):
        menu = self.create_menu(is_tray=False)
        return menu
    
    def create_tray_menu(self):
        menu = self.create_menu(is_tray=True)
        return menu

    def on_left_down(self, event):
        """鼠标左键按下事件处理"""
        if not self.has_titlebar:
            self.is_dragging = True
            self.drag_start_pos = event.GetPosition()
            self.CaptureMouse()
        event.Skip()
    
    def on_left_up(self, event):
        """鼠标左键释放事件处理"""
        if self.is_dragging:
            self.is_dragging = False
            if self.HasCapture():
                self.ReleaseMouse()
        event.Skip()
    
    def on_mouse_move(self, event):
        """鼠标移动事件处理"""
        if self.is_dragging and event.Dragging() and event.LeftIsDown():
            # 计算位置差
            current_pos = event.GetPosition()
            dx = current_pos.x - self.drag_start_pos.x
            dy = current_pos.y - self.drag_start_pos.y
            
            # 移动窗口
            window_pos = self.GetPosition()
            new_pos = (window_pos.x + dx, window_pos.y + dy)
            self.Move(new_pos)
        
        event.Skip()
    
    def on_right_down(self, event):
        """鼠标右键按下事件处理"""
        # 使用统一的菜单创建方法
        menu = self.create_context_menu()
        
        # 显示菜单
        self.PopupMenu(menu)
        menu.Destroy()

    
    def on_toggle_show(self, event):
        """切换窗口显示/隐藏"""
        if self.IsShown():
            self.Hide()
        else:
            self.Show()
            self.Raise()
    
    def on_settings(self, event):
        """打开设置对话框"""
        dialog = ModernSettingsDialog(self, self.config)
        result = dialog.ShowModal()
        
        if result == wx.ID_OK:
            # 应用设置
            self.apply_settings()
        
        dialog.Destroy()
    
    def apply_settings(self):
        """应用设置"""
        # 批量更新UI，减少刷新次数
        updates_needed = False

        # 应用语言设置
        source_lang = self.config.get('Language', 'source_language', fallback='zh-CN')
        target_lang = self.config.get('Language', 'target_language', fallback='en-US')
        
        # 如果语言设置已更改，更新翻译服务
        if (hasattr(self, 'translation_service') and 
            (self.translation_service.source_language != source_lang or 
            self.translation_service.target_language != target_lang)):
            self.translation_service.set_languages(source_lang, target_lang)
            
            # 更新UI显示
            source_name = TranslationService.SUPPORTED_LANGUAGES.get(source_lang, '未知')
            target_name = TranslationService.SUPPORTED_LANGUAGES.get(target_lang, '未知')
            self.update_status(f"语言已更改: {source_name} → {target_name}")
            
            # 清空文本缓冲区
            self.chinese_text_buffer = [['', '']]
            self.target_language_text_buffer = [['', '']]
            self.chinese_text_box.Clear()
            self.target_text_box.Clear()

        # 应用主题
        theme_setting = self.config.get('UI', 'theme', fallback='system')
        old_is_dark = self.is_dark_mode

        if theme_setting == 'light':
            self.is_dark_mode = False
        elif theme_setting == 'dark':
            self.is_dark_mode = True
        else:
            # 系统主题
            self.is_dark_mode = self._detect_system_theme()

        if old_is_dark != self.is_dark_mode:
            self.update_color_scheme()
            updates_needed = True
        
        # 更新颜色方案
        self.update_color_scheme()
        
        # 应用透明度
        self.opacity = int(self.config.get('UI', 'opacity', fallback='80'))
        alpha = int(255 * (self.opacity / 100))
        PlatformHelper.set_window_transparency(self, alpha)
        
        # 应用字体大小
        self.font_size = int(self.config.get('UI', 'font_size', fallback='14'))
        self.chinese_panel.set_font_size(self.font_size)
        self.target_panel.set_font_size(self.font_size)
        
        # 应用标题栏设置
        show_titlebar = self.config.getboolean('UI', 'show_titlebar', fallback=True)
        if show_titlebar != self.has_titlebar:
            self.has_titlebar = show_titlebar
            PlatformHelper.set_window_style(self, show_titlebar)
        
        # 更新UI颜色
        self.panel.SetBackgroundColour(self.bg_color)
        self.status_bar.SetBackgroundColour(self.bg_color)
        self.status_bar.SetForegroundColour(self.text_color)
        self.toolbar.SetBackgroundColour(self.bg_color)
        
        self.chinese_panel.set_colors(self.bg_color, self.text_color, self.accent_color)
        self.target_panel.set_colors(self.bg_color, self.text_color, self.accent_color)
        
        # 一次性刷新UI
        if updates_needed:
            self.panel.Refresh()
    
    def on_toggle_pin(self, event):
        """切换窗口置顶状态"""
        current_style = self.GetWindowStyle()
        if current_style & wx.STAY_ON_TOP:
            # 取消置顶
            self.SetWindowStyle(current_style & ~wx.STAY_ON_TOP)
            if hasattr(self, 'pin_tool'):
                self.toolbar.ToggleTool(self.pin_tool.GetId(), False)
        else:
            # 置顶
            self.SetWindowStyle(current_style | wx.STAY_ON_TOP)
            if hasattr(self, 'pin_tool'):
                self.toolbar.ToggleTool(self.pin_tool.GetId(), True)
    
    def on_toggle_pause(self, event):
        """切换暂停/继续状态"""
        is_paused = self.pause_tool.IsToggled()
        # 这里可以添加暂停/继续翻译的逻辑
        self.update_status("翻译已" + ("暂停" if is_paused else "继续"))
    
    def on_clear(self, event):
        """清空文本"""
        self.chinese_text_box.Clear()
        self.target_text_box.Clear()
        self.update_status("文本已清空")
    
    def on_help(self, event):
        """显示帮助"""
        help_text = (
            "实时翻译字幕使用帮助：\n\n"
            "1. 右键菜单可以访问常用功能\n"
            "2. 托盘图标可以快速隐藏/显示窗口\n"
            "3. 无标题栏模式下可以通过拖动窗口移动位置\n"
            "4. 在设置中可以调整主题、透明度和字体大小\n"
            "5. 使用API功能需要在设置中配置API密钥\n\n"
            "更多帮助请访问项目主页。"
        )
        
        dlg = wx.MessageDialog(
            self,
            help_text,
            "帮助",
            wx.OK | wx.ICON_INFORMATION
        )
        dlg.ShowModal()
        dlg.Destroy()
    
    def on_tray_left_click(self, event):
        """托盘图标左键点击事件"""
        self.on_toggle_show(event)
    
    def on_tray_right_click(self, event):
        """托盘图标右键点击事件"""
        menu = self.create_tray_menu()
        self.tray_icon.PopupMenu(menu)
        menu.Destroy()
    
    def on_exit(self, event):
        """退出应用"""
        self.Close()
    
    def on_close(self, event):
        """窗口关闭事件"""
        # 释放翻译服务资源
        if hasattr(self, 'translation_service'):
            self.translation_service.release_resources()

        # 保存配置
        self.save_config()
        
        # 移除托盘图标
        if hasattr(self, 'tray_icon'):
            self.tray_icon.Destroy()
        
        # 关闭窗口
        self.Destroy()
    
    def on_auto_save(self, event):
        """自动保存配置"""
        self.save_config()
    
    def save_config(self):
        """保存配置"""
        # 保存窗口位置和大小
        pos = self.GetPosition()
        size = self.GetSize()
        
        self.config_manager.set('UI', 'pos_x', str(pos.x))
        self.config_manager.set('UI', 'pos_y', str(pos.y))
        self.config_manager.set('UI', 'width', str(size.width))
        self.config_manager.set('UI', 'height', str(size.height))
        
        # 写入配置文件
        self.config_manager.save()

    
    def on_update_text(self, event):
        """更新原文文本"""
        text = event.text
        self.chinese_text_box.SetValue(text)
        self.chinese_text_box.ShowPosition(self.chinese_text_box.GetLastPosition())

    def on_update_translation(self, event):
        """更新翻译文本"""
        text = event.text
        self.target_text_box.SetValue(text)
        self.target_text_box.ShowPosition(self.target_text_box.GetLastPosition())

    def on_update_status(self, event):
        """更新状态栏文本"""
        self.update_status(event.text)
    
    def update_status(self, text):
        """更新状态栏文本"""
        if hasattr(self, 'status_bar'):
            self.status_bar.SetStatusText(text)


    def update_text(self, transcription_result, translation_result):
        """处理识别和翻译结果"""
        # 限制缓冲区大小，保留最近的20句话
        MAX_BUFFER_SIZE = 20
        if len(self.target_language_text_buffer) > MAX_BUFFER_SIZE:
            self.target_language_text_buffer = self.target_language_text_buffer[-MAX_BUFFER_SIZE:]
            
        def process_result(result, text_buffer, text_box):
            if not result:
                return
                
            # 使用字符串连接而非多次追加
            fixed_text = ''.join(word.text for word in result.words if word.fixed)
            unfixed_text = ''.join(word.text for word in result.words if not word.fixed)
            
            # 更新缓冲区
            text_buffer[-1] = [fixed_text, unfixed_text]
            
            if result.is_sentence_end:
                text_buffer.append(['', ''])
            
            # 批量更新文本框
            text_box.Freeze()  # 冻结UI更新，提高性能
            try:
                text_box.Clear()
                
                # 设置样式
                attr = rt.RichTextAttr()
                attr.SetTextColour(self.text_color)
                attr.SetLineSpacing(16)
                text_box.SetDefaultStyle(attr)
                
                # 构建完整文本，减少写入操作
                full_text = ''.join([x[0] + x[1] for x in list(text_buffer)[:-1]])
                if full_text:
                    text_box.WriteText(full_text)
                
                # 写入最后一行（当前行）
                text_box.WriteText(text_buffer[-1][0] + text_buffer[-1][1])
                
                # 自动滚动到底部
                text_box.ShowPosition(text_box.GetLastPosition())
            finally:
                text_box.Thaw()  # 解冻UI更新
        
        # 处理中文识别结果
        if transcription_result:
            process_result(transcription_result, self.chinese_text_buffer, self.chinese_text_box)
        
        # 处理翻译结果
        if translation_result:
            target_lang = self.translation_service.target_language.split('-')[0].lower()
            translation = translation_result.get_translation(target_lang)
            if translation:
                process_result(translation, self.target_language_text_buffer, self.target_text_box)
    
    def update_translation(self, text):
        """更新翻译文本（从其他线程调用）"""
        evt = UpdateTranslationEvent(text=text)
        wx.PostEvent(self, evt)
    
    def post_status_update(self, text):
        """发送状态更新事件（从其他线程调用）"""
        evt = UpdateStatusEvent(text=text)
        wx.PostEvent(self, evt)

# 向导页面类
class WizardPageOne(wx.adv.WizardPageSimple):
    def __init__(self, parent):
        super().__init__(parent)
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(self, -1, "欢迎使用实时翻译字幕")
        title.SetFont(wx.Font(14, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        
        desc = wx.StaticText(self, -1, "这个向导将帮助您完成初始设置。\n\n"
                   "您需要准备以下内容：\n"
                   "1. DashScope API密钥\n"
                   "2. （可选）TTS API密钥\n\n"
                   "点击\"下一步\"继续。")
        
        sizer.Add(title, 0, wx.ALL, 10)
        sizer.Add(desc, 0, wx.ALL, 10)
        self.SetSizer(sizer)

class WizardPageTwo(wx.adv.WizardPageSimple):
    def __init__(self, parent, config):
        super().__init__(parent)
        self.config = config
        
        sizer = wx.BoxSizer(wx.VERTICAL)
        title = wx.StaticText(self, -1, "API设置")
        title.SetFont(wx.Font(14, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL, wx.FONTWEIGHT_BOLD))
        
        # API密钥输入
        api_label = wx.StaticText(self, -1, "DashScope API密钥:")
        self.api_key = wx.TextCtrl(self, -1, "", style=wx.TE_PASSWORD)
        
        # TTS API密钥输入
        tts_label = wx.StaticText(self, -1, "TTS API密钥 (可选):")
        self.tts_api_key = wx.TextCtrl(self, -1, "", style=wx.TE_PASSWORD)
        
        # 语言选择
        lang_label = wx.StaticText(self, -1, "默认语言:")
        langs = [(code, name) for code, name in TranslationService.SUPPORTED_LANGUAGES.items()]
        lang_choices = [f"{code} ({name})" for code, name in langs]
        
        source_label = wx.StaticText(self, -1, "源语言:")
        self.source_lang = wx.Choice(self, -1, choices=lang_choices)
        self.source_lang.SetSelection(0)  # 默认选择第一个
        
        target_label = wx.StaticText(self, -1, "目标语言:")
        self.target_lang = wx.Choice(self, -1, choices=lang_choices)
        self.target_lang.SetSelection(1)  # 默认选择第二个
        
        # 布局
        sizer.Add(title, 0, wx.ALL, 10)
        
        api_sizer = wx.BoxSizer(wx.HORIZONTAL)
        api_sizer.Add(api_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        api_sizer.Add(self.api_key, 1, wx.EXPAND)
        sizer.Add(api_sizer, 0, wx.EXPAND | wx.ALL, 10)
        
        tts_sizer = wx.BoxSizer(wx.HORIZONTAL)
        tts_sizer.Add(tts_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        tts_sizer.Add(self.tts_api_key, 1, wx.EXPAND)
        sizer.Add(tts_sizer, 0, wx.EXPAND | wx.ALL, 10)
        
        sizer.Add(lang_label, 0, wx.ALL, 10)
        
        source_sizer = wx.BoxSizer(wx.HORIZONTAL)
        source_sizer.Add(source_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        source_sizer.Add(self.source_lang, 1, wx.EXPAND)
        sizer.Add(source_sizer, 0, wx.EXPAND | wx.ALL, 10)
        
        target_sizer = wx.BoxSizer(wx.HORIZONTAL)
        target_sizer.Add(target_label, 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 10)
        target_sizer.Add(self.target_lang, 1, wx.EXPAND)
        sizer.Add(target_sizer, 0, wx.EXPAND | wx.ALL, 10)
        
        note = wx.StaticText(self, -1, "完成设置后，点击'完成'按钮。")
        sizer.Add(note, 0, wx.ALL, 10)
        
        self.SetSizer(sizer)
        
        # 绑定事件
        self.Bind(wx.adv.EVT_WIZARD_PAGE_CHANGING, self.on_wizard_finishing)
    
    def on_wizard_finishing(self, event):
        """向导完成时保存设置"""
        if event.GetDirection():  # 向前移动（完成向导）
            # 保存API密钥
            api_key = self.api_key.GetValue()
            if api_key:
                self.config.save_api_key(api_key, 'api_key')
            
            tts_api_key = self.tts_api_key.GetValue()
            if tts_api_key:
                self.config.save_api_key(tts_api_key, 'tts_api_key')
                self.config.set('Features', 'enable_tts', 'True')
            
            # 保存语言设置
            langs = list(TranslationService.SUPPORTED_LANGUAGES.items())
            source_idx = self.source_lang.GetSelection()
            target_idx = self.target_lang.GetSelection()
            
            if source_idx != wx.NOT_FOUND:
                source_lang = langs[source_idx][0]
                self.config.set('Language', 'source_language', source_lang)
            
            if target_idx != wx.NOT_FOUND:
                target_lang = langs[target_idx][0]
                self.config.set('Language', 'target_language', target_lang)
            
            # 保存配置
            self.config.save()

class TranslationService:
    """翻译服务类，处理API调用和翻译逻辑"""
    
    # 支持的语言列表
    SUPPORTED_LANGUAGES = {
        'zh-CN': '中文',
        'en-US': '英文',
        'ja-JP': '日语',
        'ko-KR': '韩语',
        'fr-FR': '法语',
        'de-DE': '德语',
        'es-ES': '西班牙语',
        'ru-RU': '俄语',
        'it-IT': '意大利语',
        'pt-PT': '葡萄牙语'
    }

    def process_queue_safely(self, max_items=10):
        """以线程安全的方式处理队列中的项目"""
        processed = []
        for _ in range(max_items):
            try:
                item = self.wx_text_queue.get_nowait()
                processed.append(item)
                self.wx_text_queue.task_done()  # 标记任务已完成
            except queue.Empty:
                break
        return processed
    
    def close_audio_resources(self):
        """安全关闭音频资源"""
        with self.pyaudio_lock:
            if self.audio_stream:
                try:
                    self.audio_stream.stop_stream()
                    self.audio_stream.close()
                except Exception as e:
                    logger.error(f"关闭音频流错误: {e}", exc_info=True)
                finally:
                    self.audio_stream = None
            
            if self.mic:
                try:
                    self.mic.terminate()
                except Exception as e:
                    logger.error(f"终止PyAudio错误: {e}", exc_info=True)
                finally:
                    self.mic = None
    
    def check_and_restart(self):
        """检查服务状态并在需要时重启"""
        if not self.is_running:
            return
            
        # 检查线程是否还活着
        threads_alive = True
        if hasattr(self, 'asr_thread'):
            threads_alive = threads_alive and self.asr_thread.is_alive()
        
        if hasattr(self, 'tts_thread') and self.enable_tts:
            threads_alive = threads_alive and self.tts_thread.is_alive()
        
        # 如果线程已死但服务应该运行，尝试重启
        if not threads_alive and self.is_running:
            logger.warning("检测到服务线程已停止，尝试重启服务")
            self.ui.post_status_update("服务已中断，正在尝试重新连接...")
            self.stop()  # 确保资源被释放
            time.sleep(1)
            self.start()  # 重启服务


    def __init__(self, config, ui):
        """初始化翻译服务"""
        self.config = config
        self.ui = ui
        self._running_lock = RLock()
        self._is_running = False

        # 从配置加载语言设置
        self.source_language = self.config.get('Language', 'source_language', fallback='zh-CN')
        self.target_language = self.config.get('Language', 'target_language', fallback='en-US')
        
        self.api_key = self.config.get('API', 'api_key', fallback='')
        self.api_region = self.config.get('API', 'region', fallback='eastasia')
        self.enable_tts = self.config.getboolean('Features', 'enable_tts', fallback=False)
        
        # 初始化队列
        self.wx_text_queue = queue.Queue()
        self.asr_fixed_words = queue.Queue()
        
        # 初始化音频相关变量
        self.mic = None
        self.audio_stream = None
        self.pyaudio_lock = threading.Lock()
        
        # 设置API密钥
        dashscope.api_key = self.api_key
    
    @property
    def is_running(self):
        with self._running_lock:
            return self._is_running
    
    @is_running.setter
    def is_running(self, value):
        with self._running_lock:
            self._is_running = value

    def set_languages(self, source_lang, target_lang):
        """设置源语言和目标语言"""
        if source_lang in self.SUPPORTED_LANGUAGES and target_lang in self.SUPPORTED_LANGUAGES:
            self.source_language = source_lang
            self.target_language = target_lang
            
            # 更新配置
            self.config.set('Language', 'source_language', source_lang)
            self.config.set('Language', 'target_language', target_lang)
            
            # 如果服务正在运行，需要重启服务
            was_running = self.is_running
            if was_running:
                self.stop()
                time.sleep(0.5)  # 等待服务停止
                self.start()
            
            return True
        return False

    def start(self):
        """启动翻译服务"""
        if self.is_running or not self.api_key:
            if not self.api_key:
                self.ui.post_status_update("错误：未设置API密钥")
            return
        
        try:
            self.is_running = True
            self.ui.post_status_update("翻译服务已启动")
            
            # 启动ASR线程
            self.asr_thread = threading.Thread(target=self.asr_task)
            self.asr_thread.daemon = True
            self.asr_thread.start()
            
            # 如果启用了TTS，启动TTS线程
            if self.enable_tts:
                tts_api_key = self.config.get('API', 'tts_api_key', fallback='')
                if not tts_api_key:
                    self.ui.post_status_update("警告：未设置TTS API密钥，TTS功能将不可用")
                    logger.warning("未设置TTS API密钥，TTS功能将不可用")
                else:
                    self.tts_thread = threading.Thread(target=self.tts_task)
                    self.tts_thread.daemon = True
                    self.tts_thread.start()
        except Exception as e:
            error_msg = f"启动翻译服务失败: {e}"
            logger.error(error_msg, exc_info=True)
            self.ui.post_status_update(error_msg)
            self.is_running = False
    
    def stop(self):
        """停止翻译服务"""
        self.is_running = False
        self.ui.post_status_update("翻译服务已停止")
        self.close_audio_resources()
        
        # 关闭音频流
        with self.pyaudio_lock:
            if self.audio_stream:
                self.audio_stream.stop_stream()
                self.audio_stream.close()
                self.audio_stream = None
            if self.mic:
                self.mic.terminate()
                self.mic = None
    
    def asr_task(self):
        """ASR任务，处理语音识别和翻译"""
        max_retries = 3
        retry_count = 0

        while self.is_running:
            try:
                class Callback(TranslationRecognizerCallback):
                    def __init__(self, service):
                        super().__init__()
                        self.service = service
                        # 初始化指针
                        self.sentence_ptr = 0
                        self.zh_word_ptr = 0
                        self.tg_word_ptr = 0
                    
                    def on_open(self) -> None:
                        with self.service.pyaudio_lock:
                            print('TranslationRecognizerCallback open.')
                            self.service.mic = pyaudio.PyAudio()
                            self.service.audio_stream = self.service.mic.open(
                                format=pyaudio.paInt16,
                                channels=1,
                                rate=16000,
                                input=True
                            )
                    
                    def on_close(self) -> None:
                        with self.service.pyaudio_lock:
                            print('TranslationRecognizerCallback close.')
                            if self.service.audio_stream:
                                self.service.audio_stream.stop_stream()
                                self.service.audio_stream.close()
                                self.service.audio_stream = None
                            if self.service.mic:
                                self.service.mic.terminate()
                                self.service.mic = None
                    
                    def on_event(
                        self,
                        request_id,
                        transcription_result: TranscriptionResult,
                        translation_result: TranslationResult,
                        usage,
                    ) -> None:
                        # 处理识别结果
                        self.service.wx_text_queue.put([transcription_result, translation_result])
                        
                        # 如果启用了TTS，处理固定的词语
                        if self.service.enable_tts and translation_result:
                            target_language_translation = translation_result.get_translation(
                                self.service.target_language.split('-')[0].lower()
                            )
                            if target_language_translation:
                                for i, word in enumerate(target_language_translation.words):
                                    if word.fixed and i >= self.tg_word_ptr:
                                        self.service.asr_fixed_words.put([word.text, False])
                                        self.tg_word_ptr += 1
                                
                                # 检查句子是否结束
                                if target_language_translation.is_sentence_end:
                                    self.sentence_ptr += 1
                                    self.tg_word_ptr = 0
                                    self.zh_word_ptr = 0
                                    self.service.asr_fixed_words.put(['', True])
                
                callback = Callback(self)
                
                # 设置ASR翻译器
                translator = TranslationRecognizerRealtime(
                    model='gummy-realtime-v1',
                    format='pcm',
                    sample_rate=16000,
                    transcription_enabled=True,
                    translation_enabled=True,
                    translation_target_languages=[self.target_language.split('-')[0].lower()],
                    semantic_punctuation_enabled=True,
                    callback=callback,
                )
                
                translator.start()
                logger.info(f'翻译器启动，请求ID: {translator.get_last_request_id()}')

                # 持续读取音频数据
                while self.is_running:
                    if self.audio_stream:
                        try:
                            data = self.audio_stream.read(3200, exception_on_overflow=False)
                            translator.send_audio_frame(data)
                        except Exception as e:
                            logger.error(f"音频读取错误: {e}", exc_info=True)
                            time.sleep(0.1)
                    else:
                        time.sleep(0.1)
                
                logger.info('翻译器停止')
                translator.stop()
                retry_count = 0  # 成功后重置计数
            except requests.RequestException as e:
                retry_count += 1
                logger.error(f"网络请求错误: {e}", exc_info=True)
                self.ui.post_status_update(f"网络连接问题: {e}")
                time.sleep(2)  # 网络错误等待时间略长
                
            except pyaudio.PyAudioError as e:
                retry_count += 1
                logger.error(f"音频设备错误: {e}", exc_info=True)
                self.ui.post_status_update(f"音频设备问题: {e}")
                time.sleep(1)
                
            except dashscope.common.error.AuthenticationError as e:
                logger.error(f"API认证错误: {e}", exc_info=True)
                self.ui.post_status_update("API密钥无效，请在设置中更新API密钥")
                time.sleep(5)  # 认证错误等待较长时间
                
            except Exception as e:
                retry_count += 1
                logger.error(f"ASR任务异常: {e}", exc_info=True)

                if retry_count >= max_retries:
                    self.ui.post_status_update(f"翻译服务异常: {e}")
                    time.sleep(5)  
                else:
                    time.sleep(1)
    
    def tts_task(self):
        """TTS任务，处理文本转语音"""
        # SiliconFlow CosyVoice API
        url = "https://api.siliconflow.cn/v1/audio/speech"
        headers = {
            "Authorization": f"Bearer {self.tts_api_key}",  # 使用类属性中已解密的API密钥
            "Content-Type": "application/json"
        }
        
        # 根据目标语言选择语音
        voice_mapping = {
            'en-US': "FunAudioLLM/CosyVoice2-0.5B:alex",  # 英语
            'zh-CN': "FunAudioLLM/CosyVoice2-0.5B:xiaoxiao",  # 中文
            'ja-JP': "FunAudioLLM/CosyVoice2-0.5B:takeshi",  # 日语
            # 可以根据需要添加更多语音
        }

        # 默认使用英语语音
        voice = voice_mapping.get(self.target_language, "FunAudioLLM/CosyVoice2-0.5B:alex")
        buffer = ''
        
        while self.is_running:
            if not self.enable_tts:
                time.sleep(0.1)
                continue
                
            if not self.asr_fixed_words.empty():
                word, is_sentence_end = self.asr_fixed_words.get()
                
                if is_sentence_end or ((word in ['、', '，', '。']) and len(buffer) > 15):
                    word += '[breath][breath][breath]'
                    buffer += word
                    print(f'send sentence: {buffer}')
                    
                    payload = {
                        "model": "FunAudioLLM/CosyVoice2-0.5B",
                        "input": buffer,
                        "voice": voice,
                        "response_format": "pcm",
                        "sample_rate": 24000,
                        "stream": True,
                        "speed": 1.4,
                        "gain": 0
                    }
                    
                    buffer_size = 4096
                    try:
                        with requests.request("POST", url, json=payload, headers=headers, stream=True) as response:
                            if response.status_code == 200:
                                # 使用上下文管理器处理音频资源
                                with AudioContextManager(format=8, channels=1, rate=24000, input=False) as stream:
                                    buffer2 = b""
                                    
                                    for chunk in response.iter_content(chunk_size=1024):
                                        if chunk:
                                            buffer2 += chunk
                                            while len(buffer2) >= buffer_size:
                                                data_to_play = buffer2[:buffer_size]
                                                stream.write(data_to_play)
                                                buffer2 = buffer2[buffer_size:]
                                    
                                    # 播放剩余的缓冲区数据
                                    if len(buffer2) > 0:
                                        stream.write(buffer2)
                            else:
                                logger.error(f"请求失败，状态码：{response.status_code}，响应：{response.text[:200]}")
                            
                            buffer = ''
                    except requests.RequestException as e:
                        logger.error(f"TTS HTTP请求异常: {e}", exc_info=True)
                    except Exception as e:
                        logger.error(f"TTS处理异常: {e}", exc_info=True)
                else:
                    buffer += word
            else:
                time.sleep(0.01)

    def release_resources(self):
        """释放所有资源"""
        # 停止运行
        self.is_running = False
        
        try:
            # 清空队列，避免阻塞
            while not self.wx_text_queue.empty():
                try:
                    self.wx_text_queue.get_nowait()
                except:
                    pass
            
            while not self.asr_fixed_words.empty():
                try:
                    self.asr_fixed_words.get_nowait()
                except:
                    pass

            # 等待线程结束
            if hasattr(self, 'asr_thread') and self.asr_thread.is_alive():
                self.asr_thread.join(timeout=2.0)
            
            if hasattr(self, 'tts_thread') and self.tts_thread.is_alive():
                self.tts_thread.join(timeout=2.0)
        except Exception as e:
            logger.error(f"线程终止错误: {e}", exc_info=True)
        self.close_audio_resources()

        # 关闭音频流
        with self.pyaudio_lock:
            if self.audio_stream:
                try:
                    self.audio_stream.stop_stream()
                    self.audio_stream.close()
                except Exception as e:
                    logger.error(f"关闭音频流错误: {e}", exc_info=True)
                finally:
                    self.audio_stream = None
            
            if self.mic:
                try:
                    self.mic.terminate()
                except Exception as e:
                    logger.error(f"终止PyAudio错误: {e}", exc_info=True)
                finally:
                    self.mic = None

    def update_api_settings(self):
        """更新API设置"""
        # 使用解密方法加载API密钥
        self.api_key = self.config.load_api_key('api_key')
        self.tts_api_key = self.config.load_api_key('tts_api_key')
        self.api_region = self.config.get('API', 'region', fallback='eastasia')
        self.enable_tts = self.config.getboolean('Features', 'enable_tts', fallback=False)
        
        # 更新DashScope API密钥
        dashscope.api_key = self.api_key

class SubtitleApp(wx.App):
    """字幕应用程序类"""
    
    def OnInit(self):
        """初始化应用程序"""
        # 创建主窗口
        self.frame = ModernFloatingSubtitleWindow()
        
        # 创建翻译服务
        self.translation_service = TranslationService(self.frame.config_manager, self.frame)
        self.frame.translation_service = self.translation_service  # 将服务实例传递给窗口
        
        # 启动翻译服务
        self.translation_service.start()
        
        return True

    
    def OnExit(self):
        """退出应用程序"""
        # 停止翻译服务
        if hasattr(self, 'translation_service'):
            self.translation_service.stop()
        
        return super().OnExit()

class AudioContextManager:
    """音频资源上下文管理器"""
    
    def __init__(self, format=pyaudio.paInt16, channels=1, rate=16000, input=True):
        self.format = format
        self.channels = channels
        self.rate = rate
        self.input = input
        self.p = None
        self.stream = None
    
    def __enter__(self):
        self.p = pyaudio.PyAudio()
        self.stream = self.p.open(
            format=self.format,
            channels=self.channels,
            rate=self.rate,
            input=self.input
        )
        return self.stream
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        if self.stream:
            self.stream.stop_stream()
            self.stream.close()
        if self.p:
            self.p.terminate()

def main():
    """主函数"""
    # 设置DPI感知
    PlatformHelper.setup_dpi_awareness()
    
    # 创建应用程序
    app = SubtitleApp(False)
    
    # 运行应用程序
    app.MainLoop()


if __name__ == "__main__":
    main()

        