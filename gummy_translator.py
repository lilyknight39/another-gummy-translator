import os
import queue
import threading
import time

import dashscope
import pyaudio
import wx
import wx.richtext as rt
from dashscope.audio.asr import *
from dashscope.audio.tts_v2 import *

import requests
import ctypes  # 导入 ctypes 库

# Add a global variable to control TTS
enable_tts = False

# Set your Dashscope API key
def init_dashscope_api_key():
    """
        Set your DashScope API-key. More information:
        https://github.com/aliyun/alibabacloud-bailian-speech-demo/blob/master/PREREQUISITES.md
    """

    if 'DASHSCOPE_API_KEY' in os.environ:
        dashscope.api_key = os.environ[
            'DASHSCOPE_API_KEY']  # load API-key from environment variable DASHSCOPE_API_KEY
    else:
        dashscope.api_key = '<your-dashscope-api-key>'  # set API-key manually

# Set the target language for translation
target_language = 'zh'

# Lock for controlling access to the PyAudio stream
pyaudio_lock = threading.Lock()

# Initialize global variables for microphone and audio stream
mic = None
audio_stream = None
# Queue for text updates in wx
wx_text_queue = queue.Queue()
# Queue for fixed words from ASR
asr_fixed_words = queue.Queue()


# Handle the ASR task. This function will get audio from microphone in while loop and send it to ASR.
# The streaming output of ASR will be pushed back to the wx_text_queue and  asr_fixed_words
def gummyAsrTask():
    class Callback(TranslationRecognizerCallback):
        def __init__(self):
            super().__init__()
            # Initialize pointers for tracking words
            self.sentence_ptr = 0
            self.zh_word_ptr = 0
            self.tg_word_ptr = 0

        def on_open(self) -> None:
            # When the recognizer opens, set up the microphone stream
            global mic
            global audio_stream
            with pyaudio_lock:
                print('TranslationRecognizerCallback open.')
                mic = pyaudio.PyAudio()
                audio_stream = mic.open(format=pyaudio.paInt16,
                                        channels=1,
                                        rate=16000,
                                        input=True)

        def on_close(self) -> None:
            # Clean up the audio stream and microphone
            global mic
            global audio_stream
            print('TranslationRecognizerCallback close.')
            if audio_stream is None:
                print('audio_stream is None')
                return
            if audio_stream is not None:
                audio_stream.stop_stream()
                audio_stream.close()
                mic.terminate()
                audio_stream = None
                mic = None

        def on_event(
            self,
            request_id,
            transcription_result: TranscriptionResult,
            translation_result: TranslationResult,
            usage,
        ) -> None:
            new_chinese_words = ''
            new_target_language_words = ''
            is_sentence_end = False

            # Process transcription results. Only new fixed words will be pushed back.
            if transcription_result != None:
                for i, word in enumerate(transcription_result.words):
                    if word.fixed:
                        if i >= self.zh_word_ptr:
                            # print('new fixed ch word: ', word.text)
                            new_chinese_words += word.text
                            self.zh_word_ptr += 1

            # Process translation results. Only new fixed words will be pushed back.
            if translation_result != None:
                target_language_translation = translation_result.get_translation(
                    'zh')
                if target_language_translation != None:
                    for i, word in enumerate(
                            target_language_translation.words):
                        if word.fixed:
                            if i >= self.tg_word_ptr:
                                # print('new fixed {} word: '.format(
                                #     target_language, word.text))
                                asr_fixed_words.put([word.text, False])
                                new_target_language_words += word.text
                                self.tg_word_ptr += 1
                    # Check if the current sentence has ended
                    if target_language_translation.is_sentence_end:
                        print('target_language sentence end')
                        self.sentence_ptr += 1
                        self.tg_word_ptr = 0
                        self.zh_word_ptr = 0
                        asr_fixed_words.put(['', True])
                        is_sentence_end = True
            wx_text_queue.put([transcription_result, translation_result])

    callback = Callback()

    # Set up the ASR translator
    translator = TranslationRecognizerRealtime(
        model='gummy-realtime-v1',
        format='pcm',
        sample_rate=16000,
        transcription_enabled=True,
        translation_enabled=True,
        translation_target_languages=[target_language],
        semantic_punctuation_enabled=False,
        callback=callback,
    )

    print('translator start')
    translator.start()
    print('translator request_id: {}'.format(translator.get_last_request_id()))

    # Open a file to save microphone audio data
    saved_mic_audio_file = open('mic_audio.pcm', 'wb')

    # Continuously read audio data from the microphone
    while True:
        if audio_stream:
            try:
                data = audio_stream.read(3200, exception_on_overflow=False)
                translator.send_audio_frame(data)
                saved_mic_audio_file.write(data)
            except Exception as e:
                print(e)
        else:
            break

    print('translator stop')
    translator.stop()


# Handle the TTS task. This function will get text in asr_fixed_words in while loop and send it to TTS.
# The streaming output of TTS will be played back by the player.
def cosyvoiceTtsTask():
    #player = RealtimeMp3Player()
    #with pyaudio_lock:
    #    player.start()

    # Replace with SiliconFlow CosyVoice API
    url = "https://api.siliconflow.cn/v1/audio/speech"
    headers = {
        "Authorization": "Bearer <your-SiliconFlow-api-key>", # set your api-key
        "Content-Type": "application/json"
    }
    voice = "FunAudioLLM/CosyVoice2-0.5B:alex" # You can change the voice here
    buffer = ''

    # Continuously check for new words to synthesize
    while True:
        if not enable_tts:
            time.sleep(0.1)
            continue
        if not asr_fixed_words.empty():
            if not enable_tts:
                time.sleep(0.1)
                continue  # 如果 TTS 禁用，则跳过本次循环
            word, is_sentence_end = asr_fixed_words.get()
            if is_sentence_end  or ((word == '、' or word == '，' or word == '。' ) and len(buffer) > 15) :
            #if is_sentence_end  or (word == '、' or word == '，' or word == '。' ) :

                # when the sentence ends, wait for the previous sentence to finish synthesing and playing.
                #player.stop()
                #player.reset()
                #player.start()
                word += '[breath][breath][breath]'
                buffer += word
                # buffer += '[breath][breath][breath]'
                print('send sentence: ', buffer)
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

                
                buffer_size = 4096  # 缓冲区大小
                try:
                    response = requests.request("POST", url, json=payload, headers=headers, stream=True)
                    if response.status_code == 200:
                        p = pyaudio.PyAudio()
                        stream = p.open(format=8, channels=1, rate=24000, output=True) #修改format参数
                        buffer2 = b""  # 初始化缓冲区
                        for chunk in response.iter_content(chunk_size=1024):
                            if chunk:
                                #print("len_chunk:", len(chunk))
                                buffer2 += chunk  # 将数据块添加到缓冲区
                                #print("len_buffer:",len(buffer2))
                                while len(buffer2) >= buffer_size:  # 当缓冲区达到一定大小时
                                    data_to_play = buffer2[:buffer_size]  # 从缓冲区中取出数据
                                    stream.write(data_to_play)  # 播放数据
                                    buffer2 = buffer2[buffer_size:]  # 更新缓冲区
                        # 播放剩余的缓冲区数据
                        if len(buffer2) > 0 :
                            stream.write(buffer2)
                        stream.stop_stream()
                        stream.close()
                        p.terminate() 
                    else:
                        print(f"请求失败，状态码：{response.status_code}")
                    buffer = ''
                except requests.exceptions.RequestException as e:
                    print(f"请求异常: {e}")
                except Exception as e :
                    print(f"其他异常：{e}")
            else:
                buffer += word
                #print('buffer: ', buffer)
                    
        else:
            # Sleep briefly if no words are available
            time.sleep(0.01)

class FloatingSubtitleWindow(wx.Frame):
    def __init__(self):
        # 初始化背景相关属性
        self.is_dark_mode = False  # 初始为亮色模式
        self.bg_alpha = 0  # 初始背景透明度值(0-255)
        self.text_color = wx.Colour(0, 0, 0)  # 初始文字颜色
        # 根据初始模式设置背景颜色
        brightness = int((255 - self.bg_alpha) * 1)
        self.bg_color = wx.Colour(brightness, brightness, brightness) if not self.is_dark_mode else wx.Colour(0, 0, 0)
        
        # 设置背景样式为透明
        style = wx.STAY_ON_TOP | wx.RESIZE_BORDER | wx.DEFAULT_FRAME_STYLE
        
        super().__init__(
            parent=None,
            title='实时翻译字幕',
            style=style
        )
        
        # 属性初始化
        self.transparency = 255
        self.font_size = 14
        self.font_family = wx.FONTFAMILY_DEFAULT
        self.text_color = wx.Colour(0, 0, 0)
        self.MAX_CHARS = 1000

        self.SetSize((900,130))
    
        # 添加文本面板透明度属性
        self.text_alpha = 128  # 初始背景透明度值
        self.background_color = wx.Colour(0, 0, 0)  # 黑色背景
        
        # 初始化文本面板背景透明度
        self.panel_alpha = 200  # 初始透明度值，增大初始值使文本更容易看见
        
        if wx.Platform == "__WXMSW__":
            # 启用窗口透明
            hwnd = self.GetHandle()
            GWL_EXSTYLE = -20
            WS_EX_LAYERED = 0x80000
            style = ctypes.windll.user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            ctypes.windll.user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED)
            
            # 设置整个窗口的初始透明度
            ctypes.windll.user32.SetLayeredWindowAttributes(hwnd, 0, self.panel_alpha, 0x02)
        
        # 创建主面板
        self.panel = wx.Panel(self, style=wx.BORDER_NONE)
        self.panel.SetBackgroundColour(wx.Colour(255, 255, 255, 0))
        
        # 初始化布局
        self.main_sizer = wx.BoxSizer(wx.VERTICAL)
        
        # 创建文本面板
        self.chinese_panel = self.create_language_panel("源语言", "chinese_text_box")
        self.target_panel = self.create_language_panel("目标语言", "target_language_text_box")
        
        # 添加到布局
        self.main_sizer.Add(self.chinese_panel, 0, wx.EXPAND | wx.ALL, 2)
        self.main_sizer.AddSpacer(10)  # 添加一个高度为 10 的空白区域
        self.main_sizer.Add(self.target_panel, 1, wx.EXPAND | wx.ALL, 2)
        
        self.panel.SetSizer(self.main_sizer)
        
        # 初始化缓冲区
        self.chinese_buffer = ''
        self.chinese_text_buffer = [['', '']]  # 源语言文本缓冲区
        self.target_language_text_buffer = [['', '']]  # 目标语言文本缓冲区

        # 设置定时器用于更新文本
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.on_timer, self.timer)
        self.timer.Start(100)  # 每100毫秒更新一次

        # 绑定快捷键事件
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key_press)

        # 添加拖拽相关属性
        self.dragging = False
        self.drag_start_pos = None

        self.has_titlebar = True  # 初始状态为显示标题栏

        # 设置最小窗口大小
        self.SetMinSize((300, 100))

        # 创建定时器，每100毫秒检查一次鼠标位置
        self.mouse_check_timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, self.check_mouse_position, self.mouse_check_timer)
        self.mouse_check_timer.Start(100)  # 100毫秒间隔

        self.Center()
        self.Show()


    def check_mouse_position(self, event):
        """定时检查鼠标位置"""
        x, y = wx.GetMousePosition()  # 获取鼠标全局坐标
        rect = self.GetScreenRect()  # 获取窗口全局坐标的矩形区域
        if not rect.Contains(wx.Point(x, y)):
            if self.has_titlebar:
                #print("Mouse left the window (timer)")
                self.SetWindowStyleFlag(self.GetWindowStyleFlag() & ~wx.CAPTION)
                self.has_titlebar = False
                self.Refresh()
        else:
            if not self.has_titlebar:
                #print("Mouse in the window (timer)")
                self.SetWindowStyleFlag(self.GetWindowStyleFlag() | wx.CAPTION)
                self.has_titlebar = True
                self.Refresh()
    
    on_mouse_enter = None  # 移除鼠标进入事件处理
    show_titlebar = None  # 移除显示标题栏函数
    on_mouse_leave = None  # 移除鼠标离开事件处理
    hide_titlebar = None  # 移除隐藏标题栏函数

    def on_timer(self, event):
        """处理定时器事件，从队列中获取并更新文本"""
        try:
            while not wx_text_queue.empty():
                transcription_result, translation_result = wx_text_queue.get()
                self.update_text(transcription_result, translation_result)
        except Exception as e:
            print(f"定时器更新出错: {e}")
        event.Skip()

    def create_language_panel(self, title, text_box_name):
        panel = wx.Panel(self.panel)

        text_box = rt.RichTextCtrl(
            panel,
            style=wx.NO_BORDER | rt.RE_READONLY | rt.RE_MULTILINE
        )
        # text_box = stc.StyledTextCtrl(
        #     panel,
        #     style=wx.NO_BORDER
        # )
        text_box.SetMinSize((300, 30))

        font = wx.Font(
            wx.FontInfo(self.font_size)  # 字号
                .Family(wx.FONTFAMILY_DEFAULT)
                .Style(wx.FONTSTYLE_NORMAL)
                .Weight(wx.FONTWEIGHT_NORMAL)
                .AntiAliased(True)  # 关键：启用抗锯齿
                #.FaceName("微软雅黑")
        )
        text_box.SetFont(font)

        # 设置初始背景色
        text_box.SetBackgroundColour(self.bg_color)
        text_box.SetMargins(5, 2)

        # 设置文字颜色和样式
        #attr = wx.TextAttr()

        attr = rt.RichTextAttr()
        attr.SetAlignment(wx.TEXT_ALIGNMENT_LEFT)  #左对齐
        attr.SetLineSpacing(14)  # 设置行间距

        attr.SetTextColour(self.text_color)
        text_box.SetDefaultStyle(attr)

        sizer = wx.BoxSizer(wx.VERTICAL)
        #label = wx.StaticText(panel, label=title)
        #sizer.Add(label, 0, wx.EXPAND | wx.ALL, 1)
        sizer.Add(text_box, 1, wx.EXPAND | wx.ALL, 1)
        panel.SetSizer(sizer)

        setattr(self, text_box_name, text_box)

        return panel

    def set_panel_alpha(self, alpha):
        """设置文本面板背景透明度"""
        try:
            self.bg_alpha = alpha
            # 根据颜色模式计算背景亮度和颜色
            # 统一亮度计算逻辑，两种模式都基于alpha值
            brightness = int(1*(255 - alpha))  # 基础亮度值
            self.bg_color = wx.Colour(brightness, brightness, brightness)

            self.chinese_text_box.Freeze()
            self.target_language_text_box.Freeze()

            # 更新背景色
            self.chinese_text_box.SetBackgroundColour(self.bg_color)
            self.target_language_text_box.SetBackgroundColour(self.bg_color)
            self.panel.SetBackgroundColour(self.bg_color)
            self.SetBackgroundColour(self.bg_color)

            # 确保文字颜色不变
            # attr = wx.TextAttr()
            # attr.SetTextColour(self.text_color)
            # self.chinese_text_box.SetDefaultStyle(attr)
            # self.target_language_text_box.SetDefaultStyle(attr)

            # 刷新显示
            self.chinese_text_box.Refresh()
            self.target_language_text_box.Refresh()
            self.Refresh()

            self.chinese_text_box.Thaw()
            self.target_language_text_box.Thaw()

            print(f"背景透明度已更新: alpha={alpha}, 亮度值={brightness}")
        except Exception as e:
            print(f"设置背景透明度时出错: {e}")
            if self.chinese_text_box.IsFrozen():
                self.chinese_text_box.Thaw()
            if self.target_language_text_box.IsFrozen():
                self.target_language_text_box.Thaw()

    def on_key_press(self, event):
        key = event.GetKeyCode()
        if event.AltDown():
            if key == ord('T') or key == ord('t'):  # 检测Alt+T
                self.toggle_color_mode()
                return
            if key == wx.WXK_UP or key == wx.WXK_DOWN:
                new_alpha = self.bg_alpha
                if key == wx.WXK_UP:
                    new_alpha = min(255, self.bg_alpha + 20)
                else:
                    new_alpha = max(0, self.bg_alpha - 20)

                self.set_panel_alpha(new_alpha)
                return
        if event.AltDown():  # 检测 Alt 键
            if key == ord('S') or key == ord('s'):  # 检测 Alt+S
                global enable_tts
                enable_tts = not enable_tts  # 切换 TTS 状态
                print(f"TTS 已{'启用' if enable_tts else '禁用'}")
                if enable_tts:  # 如果 TTS 被启用
                    while not asr_fixed_words.empty():
                        asr_fixed_words.queue.clear()  # 清空队列
                return
        if event.ControlDown():
            if key == ord('H') or key == ord('h'):  # 检测Ctrl+H
                self.on_toggle_titlebar()
                return
        event.Skip()

    def on_toggle_titlebar(self):
        """切换标题栏的显示和隐藏"""
        if self.has_titlebar:
            self.SetWindowStyle(self.GetWindowStyle() & ~wx.CAPTION)
            self.has_titlebar = False
        else:
            self.SetWindowStyle(self.GetWindowStyle() | wx.CAPTION)
            self.has_titlebar = True
        self.Refresh()

    def toggle_color_mode(self):
        """切换黑白颜色模式"""
        self.is_dark_mode = not self.is_dark_mode
        # 设置文字颜色并应用
        self.text_color = wx.Colour(255, 255, 255) if self.is_dark_mode else wx.Colour(0, 0, 0)
        #attr = wx.TextAttr(self.text_color)
        # self.chinese_text_box.SetDefaultStyle(attr)
        # self.target_language_text_box.SetDefaultStyle(attr)
        # 应用新的背景设置
        #
        if self.is_dark_mode:
            self.set_panel_alpha(255)  # 重新应用当前透明度设置
            self.panel.SetBackgroundColour(wx.Colour(0, 0, 0, 0))
        else:
            self.set_panel_alpha(0)  # 重新应用当前透明度设置
            self.panel.SetBackgroundColour(wx.Colour(255, 255, 255, 0))

        # 立即刷新文本显示
        self.chinese_text_box.Refresh()
        self.target_language_text_box.Refresh()
        # 更新窗口透明度设置（仅Windows）
        if wx.Platform == "__WXMSW__":
            hwnd = self.GetHandle()
            ctypes.windll.user32.SetLayeredWindowAttributes(
                hwnd,
                0,
                self.bg_alpha,  # 使用实际的alpha值
                0x02  # LWA_ALPHA
            )

        # 更新UI组件
        self.chinese_text_box.Freeze()
        self.target_language_text_box.Freeze()

        try:
            # 更新背景色和文字颜色
            self.chinese_text_box.SetBackgroundColour(self.bg_color)
            self.target_language_text_box.SetBackgroundColour(self.bg_color)

            # 强制应用新的文字颜色
            attr = wx.TextAttr(self.text_color)
            attr.SetLineSpacing(14)  # 设置行间距
            self.chinese_text_box.SetDefaultStyle(attr)
            self.target_language_text_box.SetDefaultStyle(attr)
            # 重写当前文本以立即生效
            self.chinese_text_box.SetValue(self.chinese_text_box.GetValue())
            self.target_language_text_box.SetValue(self.target_language_text_box.GetValue())

            # 强制刷新显示
            self.chinese_text_box.Refresh()
            self.target_language_text_box.Refresh()
            self.panel.Layout()
            self.Refresh()

            # 更新窗口透明度设置（仅Windows）
            if wx.Platform == "__WXMSW__":
                hwnd = self.GetHandle()
                ctypes.windll.user32.SetLayeredWindowAttributes(
                    hwnd,
                    0,
                    self.panel_alpha,
                    0x02  # LWA_ALPHA
                )
        except Exception as e:
            print(f"切换颜色模式时出错: {e}")
        finally:
            self.chinese_text_box.Thaw()
            self.target_language_text_box.Thaw()

        # Try to set theme for RichTextCtrl (Windows specific) - Moved outside finally block
        if wx.Platform == "__WXMSW__":
            try:
                chinese_hwnd = self.chinese_text_box.GetHandle()
                target_hwnd = self.target_language_text_box.GetHandle()
                if self.is_dark_mode:
                    # Try applying explicit dark theme identifier
                    ctypes.windll.uxtheme.SetWindowTheme(chinese_hwnd, ctypes.c_wchar_p("DarkMode_Explorer"), None)
                    ctypes.windll.uxtheme.SetWindowTheme(target_hwnd, ctypes.c_wchar_p("DarkMode_Explorer"), None)
                else:
                    # Remove theme to revert to default
                    ctypes.windll.uxtheme.SetWindowTheme(chinese_hwnd, None, None)
                    ctypes.windll.uxtheme.SetWindowTheme(target_hwnd, None, None)
                # Refresh the controls after changing the theme
                self.chinese_text_box.Refresh()
                self.target_language_text_box.Refresh()

                # Attempt to set dark mode for the title bar (Windows 10 build 17763+ / Windows 11)
                try:
                    frame_hwnd = self.GetHandle()
                    # DWMWA_USE_IMMERSIVE_DARK_MODE = 20 (Win 11 22000+) or 19 (Older Win 10/11)
                    # We'll try 20 first, might need refinement based on OS version checks
                    attribute_value = 20 
                    value = ctypes.c_int(1) if self.is_dark_mode else ctypes.c_int(0)
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(frame_hwnd, attribute_value, ctypes.byref(value), ctypes.sizeof(value))
                except Exception as dwm_error:
                    # Fallback for older systems or if attribute 19 is needed
                    try:
                        attribute_value = 19
                        value = ctypes.c_int(1) if self.is_dark_mode else ctypes.c_int(0)
                        ctypes.windll.dwmapi.DwmSetWindowAttribute(frame_hwnd, attribute_value, ctypes.byref(value), ctypes.sizeof(value))
                    except Exception as dwm_error_fallback:
                         print(f"Error setting dark title bar (DWM): {dwm_error} / {dwm_error_fallback}")


            except Exception as theme_error:
                print(f"Error setting window theme: {theme_error}")

    def update_text(self, asr_result: TranscriptionResult, translation_result: TranslationResult):
        """更新文本框内容"""

        def process_result(result, text_buffer, text_box):
            is_new_sentence = False
            fixed_text = ''
            unfixed_text = ''

            if result is not None:  # 检查结果是否为空
                for word in result.words:
                    if word.fixed:
                        fixed_text += word.text
                    else:
                        unfixed_text += word.text

                # Update buffers with new text
                text_buffer[-1] = [fixed_text, unfixed_text]

                if result.is_sentence_end:
                    text_buffer.append(['', ''])

            fixed_text = ''
            unfixed_text = ''
            if result is not None and result.stash is not None:  # 检查结果和stash是否为空
                for word in result.stash.words:
                    if word['fixed']:
                        fixed_text += word.text
                    else:
                        unfixed_text += word.text
                text_buffer[-1] = [fixed_text, unfixed_text]

            # Clear and update text box
            text_box.Clear()

            attr = rt.RichTextAttr()
            attr.SetAlignment(wx.TEXT_ALIGNMENT_LEFT)  #左对齐
            attr.SetLineSpacing(14)  # 设置行间距
            #attr.SetTextColour(self.text_color)
            text_box.SetDefaultStyle(attr)

            # Write all lines except the last one in black
            normal_font = wx.Font(14, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                 wx.FONTWEIGHT_NORMAL)
            text_box.BeginFont(normal_font)
            text_box.BeginTextColour(wx.BLACK)
            if self.is_dark_mode:
                text_box.BeginTextColour(wx.WHITE)

            if len(text_buffer) > 1:
                text_box.WriteText(
                    ''.join([x[0] + x[1] for x in text_buffer[:-1]]))
                    #'\n'.join([x[0] + x[1] for x in text_buffer[:-1]]) + '\n')
                    #''.join([x[0] + x[1] for x in text_buffer[:-1]]) + '\n')

            # Write the last line in blue with larger font
            large_font = wx.Font(14, wx.FONTFAMILY_DEFAULT, wx.FONTSTYLE_NORMAL,
                                 wx.FONTWEIGHT_BOLD)
            text_box.BeginFont(large_font)
            text_box.BeginTextColour(wx.BLACK)
            if self.is_dark_mode:
                text_box.BeginTextColour(wx.WHITE)
            text_box.WriteText(text_buffer[-1][0] + text_buffer[-1][1])
            text_box.EndTextColour()
            text_box.EndFont()

            # Auto-scroll to the bottom of the text boxes
            text_box.ShowPosition(text_box.GetLastPosition() - 2)

        if asr_result:
            process_result(asr_result, self.chinese_text_buffer, self.chinese_text_box)

        if translation_result:
            translation = translation_result.get_translation('zh')
            if translation:
                process_result(translation, self.target_language_text_buffer, self.target_language_text_box)

if __name__ == '__main__':
    try:
        # 设置线程为守护线程
        ctypes.windll.shcore.SetProcessDpiAwareness(2) 
        asr_thread = threading.Thread(target=gummyAsrTask, daemon=True)
        asr_thread.start()
        tts_thread = threading.Thread(target=cosyvoiceTtsTask, daemon=True)
        tts_thread.start()
        
        app = wx.App(False)
        frame = FloatingSubtitleWindow()
        app.MainLoop()
    except KeyboardInterrupt:
        print("程序正在退出...")
    finally:
        # 清理资源
        if 'audio_stream' in globals() and audio_stream is not None:
            audio_stream.stop_stream()
            audio_stream.close()
        if 'mic' in globals() and mic is not None:
            mic.terminate()
