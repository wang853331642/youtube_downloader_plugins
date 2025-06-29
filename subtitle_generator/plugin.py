import os
import time
import math
import base64
import tempfile
import subprocess
import re
import json
from PyQt5.QtCore import QThread, pyqtSignal, Qt
from PyQt5.QtWidgets import (
    QPushButton, QDialog, QVBoxLayout, QHBoxLayout, QGroupBox, QLabel, 
    QLineEdit, QComboBox, QSpinBox, QCheckBox, QTextEdit, QProgressBar,
    QMessageBox, QFileDialog, QFormLayout, QProgressDialog, QSizePolicy,
    QWidget, QAction, QMenu, QMainWindow
)
from PyQt5.QtGui import QIcon

class PluginBase:
    """插件基类"""
    def __init__(self, app_instance=None):
        self.app = app_instance
        
    def get_hooks(self):
        """返回此插件提供的所有钩子"""
        return {}
        
    def initialize(self):
        """初始化插件"""
        return True

class SubtitleGeneratorPlugin(PluginBase):
    """字幕生成插件"""
    
    def __init__(self, app_instance=None):
        super().__init__(app_instance)
        self.name = "字幕生成器"
        self.version = "1.0.0"
        self.description = "从视频中自动生成字幕，支持多种语言和格式"
        self.author = "Claude"
        self.app = app_instance
        self.subtitle_button = None
        self.menu_action = None
        
    def initialize(self):
        """初始化插件"""
        print("字幕生成插件已初始化")
        self.add_subtitle_button()
        return True
    
    def get_hooks(self):
        """返回此插件提供的所有钩子"""
        return {
            "on_startup": self.on_startup,
            "on_shutdown": self.on_shutdown,
            "on_disable": self.on_disable
        }
    
    def on_startup(self):
        """应用启动时执行"""
        print("字幕生成插件已启动")
    
    def on_shutdown(self):
        """应用关闭时执行"""
        print("字幕生成插件即将关闭")
    
    def on_disable(self):
        """插件被禁用时执行"""
        print("字幕生成插件被禁用")
        self.cleanup_ui()
    
    def register_menu_actions(self, menu=None):
        """注册菜单动作"""
        try:
            if menu is None:
                print("未提供菜单，无法添加菜单项")
                return
            
            # 创建菜单项
            self.menu_action = QAction("字幕生成器", menu)
            self.menu_action.triggered.connect(self.show_subtitle_dialog)
            
            # 添加到菜单
            menu.addAction(self.menu_action)
            print("已添加字幕生成器菜单项")
        except Exception as e:
            print(f"添加字幕生成器菜单项失败: {e}")
    
    def cleanup_ui(self):
        """清理UI元素"""
        if hasattr(self, 'subtitle_button') and self.subtitle_button:
            try:
                # 从布局中移除按钮
                button = self.subtitle_button
                parent = button.parent()
                if parent:
                    layout = parent.layout()
                    if layout:
                        layout.removeWidget(button)
                button.setParent(None)  # 断开与父对象的连接
                print(f"已清理字幕生成按钮 (ID: {button.objectName()})")
            except Exception as e:
                print(f"清理字幕生成按钮失败: {e}")
        
        # 清理菜单项
        if hasattr(self, 'menu_action') and self.menu_action:
            try:
                menu = self.menu_action.parent()
                if menu:
                    menu.removeAction(self.menu_action)
                print("已清理字幕生成器菜单项")
            except Exception as e:
                print(f"清理字幕生成器菜单项失败: {e}")
    
    def add_subtitle_button(self):
        """添加字幕生成按钮到主界面"""
        if not self.app:
            print("未提供应用实例，无法添加字幕生成按钮")
            return
        
        from PyQt5.QtWidgets import QPushButton, QMainWindow
        from PyQt5.QtCore import QSize, Qt
        from PyQt5.QtGui import QIcon
        import os
        
        # 先清理可能存在的重复按钮
        if hasattr(self, 'subtitle_button') and self.subtitle_button:
            try:
                # 从布局中移除按钮
                button = self.subtitle_button
                parent = button.parent()
                if parent:
                    layout = parent.layout()
                    if layout:
                        layout.removeWidget(button)
                button.setParent(None)  # 断开与父对象的连接
            except Exception as e:
                print(f"清理字幕生成按钮失败: {e}")
        
        # 创建字幕生成按钮
        self.subtitle_button = QPushButton("字幕生成")
        
        # 设置唯一对象名
        button_id = f"subtitle_generator_button_{id(self)}"
        self.subtitle_button.setObjectName(button_id)
        
        # 设置图标
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "subtitle_icon.png")
        if os.path.exists(icon_path):
            self.subtitle_button.setIcon(QIcon(icon_path))
            self.subtitle_button.setIconSize(QSize(20, 20))
            print(f"已加载字幕图标: {icon_path}")
        
        # 设置样式 - 使用与字幕按钮相同的紫色
        self.subtitle_button.setStyleSheet("""
            QPushButton {
                background-color: #9C27B0;  /* 紫色，与字幕按钮保持一致 */
                color: white;
                border-radius: 5px;
                padding: 5px 10px;
                padding-left: 8px;
                font-weight: bold;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #7B1FA2;
            }
            QPushButton:pressed {
                background-color: #6A1B9A;
            }
        """)
        self.subtitle_button.setCursor(Qt.PointingHandCursor)
        
        # 根据截图调整宽度
        self.subtitle_button.setFixedWidth(120)  # 设置固定宽度，与字幕生成按钮匹配
        
        # 设置提示文本
        self.subtitle_button.setToolTip("从视频中自动生成字幕")
        
        # 连接点击事件
        self.subtitle_button.clicked.connect(self.show_subtitle_dialog)
        
        try:
            # 直接查找字幕按钮
            subtitle_btn = None
            for widget in self.app.findChildren(QPushButton):
                if widget.text() == "字幕":
                    subtitle_btn = widget
                    break
            
            if subtitle_btn:
                # 找到字幕按钮，获取其父布局
                parent = subtitle_btn.parent()
                if parent and parent.layout():
                    layout = parent.layout()
                    
                    # 找到字幕按钮在布局中的索引
                    for i in range(layout.count()):
                        item = layout.itemAt(i)
                        if item and item.widget() == subtitle_btn:
                            # 在字幕按钮右侧插入字幕生成按钮
                            layout.insertWidget(i + 1, self.subtitle_button)
                            print(f"已添加字幕生成按钮到字幕按钮右侧 (ID: {button_id})")
                            return
            
            # 如果找不到字幕按钮，尝试直接查找history_layout
            if hasattr(self.app, 'history_layout'):
                # 遍历布局中的所有项，查找字幕按钮
                for i in range(self.app.history_layout.count()):
                    item = self.app.history_layout.itemAt(i)
                    if item and item.widget() and isinstance(item.widget(), QPushButton) and item.widget().text() == "字幕":
                        # 在字幕按钮后插入
                        self.app.history_layout.insertWidget(i + 1, self.subtitle_button)
                        print(f"已添加字幕生成按钮到history_layout中字幕按钮右侧 (ID: {button_id})")
                        return
                
                # 如果没找到字幕按钮，直接添加到布局末尾
                self.app.history_layout.addWidget(self.subtitle_button)
                print(f"已添加字幕生成按钮到history_layout末尾 (ID: {button_id})")
                return
            
            # 最后的尝试，直接添加到应用实例
            self.subtitle_button.setParent(self.app)
            self.subtitle_button.move(380, 130)  # 根据截图，设置一个在字幕按钮右侧的位置
            self.subtitle_button.show()
            print(f"已将字幕生成按钮直接添加到应用界面 (ID: {button_id})")
        except Exception as e:
            print(f"添加字幕生成按钮失败: {e}")
            # 记录错误详情
            import traceback
            traceback.print_exc()
    
    def show_subtitle_dialog(self):
        """显示字幕生成对话框"""
        parent = self.app.main_window if hasattr(self.app, 'main_window') else None
        if not parent:
            # 尝试从应用实例中找到主窗口
            for widget in self.app.topLevelWidgets() if hasattr(self.app, 'topLevelWidgets') else []:
                if isinstance(widget, QMainWindow):
                    parent = widget
                    break
        
        dialog = SubtitleGeneratorDialog(parent)
        dialog.exec_()


class SubtitleGeneratorDialog(QDialog):
    """字幕生成对话框"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.parent = parent
        # 去除右上角的问号按钮
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        self.initUI()
    
    def initUI(self):
        """初始化界面"""
        self.setWindowTitle("字幕生成器")
        self.setMinimumWidth(650)
        self.setMinimumHeight(550)
        
        # 设置样式表
        self.setStyleSheet("""
        QDialog {
            background-color: #f5f5f5;
        }
        QGroupBox {
            border: 1px solid #cccccc;
            border-radius: 5px;
            margin-top: 10px;
            font-weight: bold;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px 0 5px;
        }
        QPushButton {
            background-color: #4CAF50;
            color: white;
            border: none;
            border-radius: 3px;
            padding: 5px 10px;
            min-height: 25px;
        }
        QPushButton:hover {
            background-color: #45a049;
        }
        QPushButton:pressed {
            background-color: #3d8b40;
        }
        QLineEdit, QTextEdit {
            border: 1px solid #cccccc;
            border-radius: 3px;
            padding: 3px;
            background-color: white;
        }
        QProgressBar {
            border: 1px solid #cccccc;
            border-radius: 3px;
            text-align: center;
            background-color: white;
        }
        QProgressBar::chunk {
            background-color: #4CAF50;
        }
        """)
        
        # 主布局
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # 视频文件选择
        file_group = QGroupBox("视频文件")
        file_layout = QHBoxLayout(file_group)
        self.video_path_input = QLineEdit()
        browse_btn = QPushButton("浏览")
        browse_btn.clicked.connect(self.browse_video_file)
        file_layout.addWidget(self.video_path_input)
        file_layout.addWidget(browse_btn)
        
        # 输出设置
        output_group = QGroupBox("输出设置")
        output_layout = QFormLayout(output_group)
        self.output_path_input = QLineEdit()
        browse_output_btn = QPushButton("浏览")
        browse_output_btn.clicked.connect(self.browse_output_file)
        
        output_path_layout = QHBoxLayout()
        output_path_layout.addWidget(self.output_path_input)
        output_path_layout.addWidget(browse_output_btn)
        
        self.output_format_combo = QComboBox()
        self.output_format_combo.addItems(["SRT", "VTT", "ASS"])
        self.output_format_combo.currentIndexChanged.connect(self.update_output_extension)
        
        output_layout.addRow("输出路径:", output_path_layout)
        output_layout.addRow("字幕格式:", self.output_format_combo)
        
        # 语音识别设置
        asr_group = QGroupBox("语音识别设置")
        asr_layout = QFormLayout(asr_group)
        
        self.source_lang_combo = QComboBox()
        self.source_lang_combo.addItems(["自动检测", "中文", "英文", "日语", "韩语", "法语", "德语", "俄语", "西班牙语"])
        
        self.engine_combo = QComboBox()
        self.engine_combo.addItems(["Whisper API", "本地Whisper"])  # 移除Ollama选项
        self.engine_combo.currentIndexChanged.connect(self.update_model_options)
        
        self.model_combo = QComboBox()
        
        # 添加API Key输入框（初始隐藏）
        self.api_key_layout = QHBoxLayout()
        self.api_key_input = QLineEdit()
        self.api_key_input.setEchoMode(QLineEdit.Password)  # 密码模式显示
        self.api_key_input.setPlaceholderText("输入OpenAI API Key")
        self.api_key_layout.addWidget(self.api_key_input)
        
        asr_layout.addRow("语音语言:", self.source_lang_combo)
        asr_layout.addRow("识别引擎:", self.engine_combo)
        asr_layout.addRow("模型选择:", self.model_combo)
        asr_layout.addRow("API Key:", self.api_key_layout)
        
        # 初始化模型选项
        self.update_model_options()
        
        # 高级选项
        advanced_group = QGroupBox("高级选项")
        advanced_layout = QFormLayout(advanced_group)
        
        self.timestamp_precision = QSpinBox()
        self.timestamp_precision.setRange(1, 10)
        self.timestamp_precision.setValue(3)
        self.timestamp_precision.setSuffix(" 秒")
        
        self.noise_reduction = QCheckBox("启用噪音抑制")
        self.noise_reduction.setChecked(True)
        
        self.speaker_diarization = QCheckBox("启用说话人分离")
        
        advanced_layout.addRow("时间轴精度:", self.timestamp_precision)
        advanced_layout.addRow("", self.noise_reduction)
        advanced_layout.addRow("", self.speaker_diarization)
        
        # 预览区域
        preview_group = QGroupBox("生成预览")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_text = QTextEdit()
        self.preview_text.setReadOnly(True)
        self.preview_text.setPlaceholderText("生成的字幕将在此处预览...")
        self.preview_text.setMinimumHeight(200)  # 增加最小高度
        self.preview_text.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOn)  # 始终显示垂直滚动条
        preview_layout.addWidget(self.preview_text)
        
        # 按钮区域
        button_layout = QHBoxLayout()
        self.generate_btn = QPushButton("开始生成")
        self.generate_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border-radius: 5px;
                padding: 8px 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #1976D2;
            }
            QPushButton:pressed {
                background-color: #0D47A1;
            }
        """)
        self.generate_btn.clicked.connect(self.start_generation)
        
        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border-radius: 5px;
                padding: 8px 15px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #D32F2F;
            }
            QPushButton:pressed {
                background-color: #B71C1C;
            }
        """)
        # 将取消按钮连接到 reject 方法，这会关闭对话框
        self.cancel_btn.clicked.connect(self.reject)
        
        button_layout.addStretch()
        button_layout.addWidget(self.generate_btn)
        button_layout.addWidget(self.cancel_btn)
        
        # 底部状态栏
        status_layout = QHBoxLayout()
        self.status_label = QLabel("准备就绪")
        self.status_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        status_layout.addWidget(self.status_label)
        
        # 添加所有组件到布局
        main_layout.addWidget(file_group)
        main_layout.addWidget(output_group)
        main_layout.addWidget(asr_group)
        main_layout.addWidget(advanced_group)
        main_layout.addWidget(preview_group)
        main_layout.addLayout(button_layout)
        main_layout.addLayout(status_layout)
        
        self.setLayout(main_layout)
    def reject(self):
        """重写 reject 方法，在关闭对话框前执行清理操作"""
        # 如果有正在运行的字幕生成线程，先停止它
        if hasattr(self, 'generation_thread') and self.generation_thread.isRunning():
            self.generation_thread.stop()
            
            # 给线程一点时间来响应停止信号
            import time
            time.sleep(0.1)
            
            # 强制终止线程（如果可能）
            if self.generation_thread.isRunning():
                self.generation_thread.terminate()
        
        # 调用父类的 reject 方法关闭对话框
        super().reject()
    def update_model_options(self):
        """根据选择的引擎更新可用模型"""
        engine = self.engine_combo.currentText()
        self.model_combo.clear()
        
        # 显示或隐藏API Key输入框
        api_key_visible = engine == "Whisper API"
        self.api_key_input.setVisible(api_key_visible)
        self.api_key_layout.parentWidget().findChild(QLabel, "", Qt.FindDirectChildrenOnly).setVisible(api_key_visible)
        
        if engine == "Whisper API":
            self.model_combo.addItems(["whisper-1", "whisper-large-v3"])
            
            # 尝试从环境变量获取API Key
            api_key = os.environ.get("OPENAI_API_KEY", "")
            if api_key:
                self.api_key_input.setText(api_key)
            
            # 检查OpenAI库是否可用
            try:
                import openai
            except ImportError as e:
                QMessageBox.warning(
                    self, 
                    "依赖缺失", 
                    "检测到OpenAI库可能未安装或存在版本冲突。\n"
                    "如果继续使用Whisper API，可能会遇到错误。\n\n"
                    f"错误详情: {str(e)}\n\n"
                    "建议解决方案:\n"
                    "1. 安装OpenAI库: pip install openai\n"
                    "2. 如果有版本冲突，请尝试: pip install httpx==0.24.1\n"
                    "3. 或者使用本地Whisper模型"
                )
        else:  # 本地Whisper
            self.model_combo.addItems([
                "tiny", 
                "base", 
                "small", 
                "medium", 
                "large-v3",
                "large-v3-turbo"
            ])
            
            # 检查whisper库是否可用
            try:
                import whisper
            except ImportError:
                QMessageBox.warning(
                    self, 
                    "依赖缺失", 
                    "检测到Whisper库可能未安装。\n"
                    "如果继续使用本地Whisper，可能会遇到错误。\n\n"
                    "建议解决方案:\n"
                    "1. 安装Whisper库: pip install openai-whisper\n"
                    "2. 或者使用Whisper API"
                )
    def browse_video_file(self):
        """浏览选择视频文件"""
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择视频文件", "", 
            "视频文件 (*.mp4 *.mkv *.avi *.mov *.webm *.flv);;所有文件 (*.*)"
        )
        
        if file_path:
            self.video_path_input.setText(file_path)
            # 自动设置输出路径，替换扩展名为选定的字幕格式
            output_format = self.output_format_combo.currentText().lower()
            base_path = os.path.splitext(file_path)[0]
            self.output_path_input.setText(f"{base_path}.{output_format}")
    
    def browse_output_file(self):
        """浏览选择输出文件路径"""
        current_path = self.output_path_input.text()
        initial_dir = os.path.dirname(current_path) if current_path else ""
        
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存字幕文件", initial_dir,
            "SRT字幕文件 (*.srt);;WebVTT字幕文件 (*.vtt);;ASS字幕文件 (*.ass);;所有文件 (*.*)"
        )
        
        if file_path:
            self.output_path_input.setText(file_path)
            # 根据选择的文件类型更新下拉框
            ext = os.path.splitext(file_path)[1].lower()
            if ext == ".srt":
                self.output_format_combo.setCurrentText("SRT")
            elif ext == ".vtt":
                self.output_format_combo.setCurrentText("VTT")
            elif ext == ".ass":
                self.output_format_combo.setCurrentText("ASS")
    
    def update_output_extension(self):
        """根据选择的格式更新输出文件扩展名"""
        current_path = self.output_path_input.text()
        if not current_path:
            return
            
        # 获取当前选择的格式
        output_format = self.output_format_combo.currentText().lower()
        
        # 更新文件扩展名
        base_path = os.path.splitext(current_path)[0]
        self.output_path_input.setText(f"{base_path}.{output_format}")
    
    def start_generation(self):
        """开始字幕生成过程"""
        video_path = self.video_path_input.text()
        if not video_path or not os.path.exists(video_path):
            QMessageBox.warning(self, "错误", "请选择有效的视频文件")
            return
            
        # 获取设置
        output_path = self.output_path_input.text()
        if not output_path:
            QMessageBox.warning(self, "错误", "请指定输出文件路径")
            return
            
        # 确保输出目录存在
        output_dir = os.path.dirname(output_path)
        if output_dir and not os.path.exists(output_dir):
            try:
                os.makedirs(output_dir)
            except Exception as e:
                QMessageBox.warning(self, "错误", f"无法创建输出目录: {str(e)}")
                return
        
        source_lang = self.source_lang_combo.currentText()
        engine = self.engine_combo.currentText()
        model = self.model_combo.currentText()
        
        # 检查API Key并设置环境变量
        if engine == "Whisper API":
            api_key = self.api_key_input.text().strip()
            if not api_key:
                QMessageBox.warning(self, "错误", "使用Whisper API需要提供API Key")
                return
            # 使用特殊的环境变量名，避免与系统环境变量冲突
            os.environ["OPENAI_API_KEY_USER_INPUT"] = api_key
        
        # 显示进度对话框
        self.progress_dialog = QProgressDialog("正在生成字幕...", "取消", 0, 100, self)
        self.progress_dialog.setWindowTitle("字幕生成")
        self.progress_dialog.setWindowModality(Qt.WindowModal)
        self.progress_dialog.setValue(0)
        self.progress_dialog.canceled.connect(self.cancel_generation)
        self.progress_dialog.setWindowFlags(self.progress_dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        self.progress_dialog.show()
        
        # 创建并启动生成线程
        self.generation_thread = SubtitleGenerationThread(
            video_path, 
            output_path, 
            source_lang, 
            engine, 
            model,
            self.timestamp_precision.value(),
            self.noise_reduction.isChecked(),
            self.speaker_diarization.isChecked()
        )
        self.generation_thread.progress_updated.connect(self.update_generation_progress)
        self.generation_thread.preview_updated.connect(self.update_preview)
        self.generation_thread.generation_completed.connect(self.generation_completed)
        self.generation_thread.start()
    
    def update_generation_progress(self, value, message):
        """更新生成进度"""
        if hasattr(self, 'progress_dialog') and self.progress_dialog:
            self.progress_dialog.setValue(value)
            self.progress_dialog.setLabelText(message)
            self.status_label.setText(message)
    
    def update_preview(self, preview_text):
        """更新预览区域"""
        self.preview_text.setText(preview_text)
    
    def generation_completed(self, success, message, file_path):
        """处理生成完成事件"""
        if hasattr(self, 'progress_dialog') and self.progress_dialog:
            self.progress_dialog.close()
        
        if success:
            self.status_label.setText("字幕生成成功")
            QMessageBox.information(self, "完成", f"字幕生成成功！\n保存路径: {file_path}")
        else:
            self.status_label.setText(f"错误: {message}")
            QMessageBox.critical(self, "错误", f"字幕生成失败: {message}")
    
    def cancel_generation(self):
        """取消字幕生成过程"""
        if hasattr(self, 'generation_thread') and self.generation_thread.isRunning():
            print("发送停止信号到字幕生成线程")
            self.generation_thread.stop()
            self.status_label.setText("正在取消字幕生成...")
            
            # 给线程一点时间来响应停止信号
            import time
            time.sleep(0.5)
            
            # 强制终止线程（如果可能）
            if self.generation_thread.isRunning():
                self.generation_thread.terminate()  # 注意：这是一个强制操作，可能会导致资源泄漏
            
            # 确保关闭进度对话框
            if hasattr(self, 'progress_dialog') and self.progress_dialog:
                self.progress_dialog.close()
            
            QMessageBox.information(self, "已取消", "字幕生成已取消")
        else:
            # 如果线程未运行，只需关闭进度对话框
            if hasattr(self, 'progress_dialog') and self.progress_dialog:
                self.progress_dialog.close()


class SubtitleGenerationThread(QThread):
    """字幕生成线程"""
    progress_updated = pyqtSignal(int, str)
    preview_updated = pyqtSignal(str)
    generation_completed = pyqtSignal(bool, str, str)
    
    def __init__(self, video_path, output_path, source_lang, engine, model, 
                timestamp_precision, noise_reduction, speaker_diarization, api_key=None):
        super().__init__()
        self.video_path = video_path
        self.output_path = output_path
        self.source_lang = source_lang
        self.engine = engine
        self.model = model
        self.timestamp_precision = timestamp_precision
        self.noise_reduction = noise_reduction
        self.speaker_diarization = speaker_diarization
        self.api_key = api_key
        self.is_running = True
    
    def run(self):
        try:
            # 1. 提取音频
            self.progress_updated.emit(10, "正在从视频中提取音频...")
            audio_path = self._extract_audio()
            
            # 检查是否已请求停止
            if not self.is_running:
                raise Exception("操作已取消")
            
            # 2. 准备语音识别
            self.progress_updated.emit(20, "正在准备语音识别模型...")
            
            # 检查是否已请求停止
            if not self.is_running:
                raise Exception("操作已取消")
            
            # 3. 根据选择的引擎执行语音识别
            self.progress_updated.emit(30, "正在执行语音识别...")
            if self.engine == "Whisper API":
                subtitle_data = self._recognize_with_whisper_api(audio_path)
            else:  # 本地Whisper
                subtitle_data = self._recognize_with_local_whisper(audio_path)
            
            # 检查是否已请求停止
            if not self.is_running:
                raise Exception("操作已取消")
            
            # 4. 生成字幕文件
            self.progress_updated.emit(80, "正在生成字幕文件...")
            self._generate_subtitle_file(subtitle_data)
            
            # 检查是否已请求停止
            if not self.is_running:
                raise Exception("操作已取消")
            
            # 5. 清理临时文件
            self.progress_updated.emit(90, "正在清理临时文件...")
            if os.path.exists(audio_path):
                os.remove(audio_path)
            
            # 6. 完成
            self.progress_updated.emit(100, "字幕生成完成！")
            self.generation_completed.emit(True, "字幕生成成功", self.output_path)
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.generation_completed.emit(False, str(e), "")
    
    def _extract_audio(self):
        """从视频中提取音频"""
        temp_dir = tempfile.gettempdir()
        audio_path = os.path.join(temp_dir, f"temp_audio_{int(time.time())}.wav")
        
        # 使用FFmpeg提取音频
        cmd = [
            "ffmpeg", "-i", self.video_path, 
            "-q:a", "0", "-map", "a", "-ac", "1", "-ar", "16000",
            audio_path
        ]
        
        process = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding='utf-8', errors='replace'
        )
        
        while process.poll() is None and self.is_running:
            time.sleep(0.1)
        
        if not self.is_running:
            process.terminate()
            raise Exception("操作已取消")
        
        if process.returncode != 0:
            error = process.stderr.read()
            raise Exception(f"音频提取失败: {error}")
        
        return audio_path
    
    
    def _recognize_with_whisper_api(self, audio_path):
        """使用OpenAI Whisper API进行语音识别"""
        try:
            import openai
        except ImportError as e:
            # 捕获导入错误
            error_msg = str(e)
            if "BaseTransport" in error_msg:
                raise Exception(
                    "OpenAI库与httpx版本不兼容。请尝试以下解决方案：\n"
                    "1. 升级或降级httpx: pip install httpx==0.24.1\n"
                    "2. 重新安装OpenAI: pip install --upgrade openai\n"
                    "3. 或者使用本地Whisper模型进行识别"
                )
            else:
                raise Exception(f"无法导入OpenAI库: {error_msg}\n请安装: pip install openai")
        
        # 获取API密钥
        api_key = os.environ.get("OPENAI_API_KEY_USER_INPUT")  # 使用特殊的环境变量名
        if not api_key:
            raise Exception("未设置OpenAI API密钥，请在界面中输入API Key")
        
        # 创建一个新的OpenAI客户端实例，避免使用全局配置
        client = openai.OpenAI(api_key=api_key)
        
        try:
            # 分段处理音频文件（API有文件大小限制）
            segment_duration = 240  # 4分钟一段（API限制25MB）
            audio_segments = self._split_audio(audio_path, segment_duration)
            
            all_segments = []
            total_segments = len(audio_segments)
            
            for i, segment_path in enumerate(audio_segments):
                self.progress_updated.emit(
                    30 + int(50 * i / total_segments), 
                    f"正在识别音频片段 {i+1}/{total_segments}..."
                )
                
                # 调用Whisper API
                try:
                    with open(segment_path, "rb") as audio_file:
                        response = client.audio.transcriptions.create(
                            model=self.model,
                            file=audio_file,
                            language=self._map_language_code(self.source_lang) if self.source_lang != "自动检测" else None,
                            response_format="srt" if self.output_path.endswith(".srt") else "vtt"
                        )
                except Exception as api_error:
                    raise Exception(f"Whisper API调用失败: {str(api_error)}")
                
                # 解析结果
                segments = self._parse_whisper_api_response(response, i * segment_duration)
                all_segments.extend(segments)
                
                # 更新预览
                preview_text = self._format_segments_preview(all_segments)
                self.preview_updated.emit(preview_text)
                
                # 清理临时文件
                os.remove(segment_path)
            
            return all_segments
        except Exception as e:
            # 捕获所有其他异常
            if "无法导入" not in str(e) and "不兼容" not in str(e):
                raise Exception(f"Whisper API处理失败: {str(e)}")
            else:
                raise e
    
    def _parse_whisper_api_response(self, response, time_offset=0):
        """解析Whisper API返回的结果"""
        segments = []
        
        # 解析SRT或VTT格式
        content = response.text if hasattr(response, 'text') else str(response)
        
        if self.output_path.endswith(".srt"):
            # 解析SRT格式
            pattern = r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n([\s\S]*?)(?=\n\d+\n|$)'
            matches = re.findall(pattern, content)
            
            for _, start_str, end_str, text in matches:
                start_time = self._time_str_to_seconds(start_str) + time_offset
                end_time = self._time_str_to_seconds(end_str) + time_offset
                text = text.strip()
                
                segments.append({
                    "start": start_time,
                    "end": end_time,
                    "text": text
                })
        else:
            # 解析VTT格式
            pattern = r'(\d{2}:\d{2}:\d{2}\.\d{3}) --> (\d{2}:\d{2}:\d{2}\.\d{3})\n([\s\S]*?)(?=\n\d{2}:\d{2}:\d{2}\.\d{3}|$)'
            matches = re.findall(pattern, content)
            
            for start_str, end_str, text in matches:
                start_time = self._time_str_to_seconds(start_str) + time_offset
                end_time = self._time_str_to_seconds(end_str) + time_offset
                text = text.strip()
                
                segments.append({
                    "start": start_time,
                    "end": end_time,
                    "text": text
                })
        
        return segments
    
    def _recognize_with_local_whisper(self, audio_path):
        """使用本地Whisper模型进行语音识别"""
        try:
            import whisper
            import torch
        except ImportError:
            raise Exception("未安装whisper库，请运行: pip install openai-whisper torch")
        
        # 加载模型
        self.progress_updated.emit(25, f"正在加载Whisper {self.model}模型...")
        
        # 处理large-v3-turbo模型
        model_name = self.model
        if model_name == "large-v3-turbo":
            model = whisper.load_model("large-v3", device="cuda" if torch.cuda.is_available() else "cpu")
        else:
            model = whisper.load_model(model_name, device="cuda" if torch.cuda.is_available() else "cpu")
        
        # 检查是否已请求停止
        if not self.is_running:
            raise Exception("操作已取消")
        
        # 执行转录
        self.progress_updated.emit(35, "正在执行语音识别...")
        
        options = {
            "language": self._map_language_code(self.source_lang) if self.source_lang != "自动检测" else None,
            "task": "transcribe",
            "verbose": True  # 启用详细输出，可以看到进度
        }
        
        # 直接处理整个音频文件
        result = model.transcribe(audio_path, **options)
        
        # 检查是否已请求停止
        if not self.is_running:
            raise Exception("操作已取消")
        
        # 解析结果
        segments = []
        for segment in result["segments"]:
            start_time = segment["start"]
            end_time = segment["end"]
            text = segment["text"].strip()
            
            segments.append({
                "start": start_time,
                "end": end_time,
                "text": text
            })
        
        # 更新预览
        preview_text = self._format_segments_preview(segments)
        self.preview_updated.emit(preview_text)
        
        return segments
    
    def _split_audio(self, audio_path, segment_duration):
        """将音频分割成多个小片段"""
        temp_dir = tempfile.gettempdir()
        
        # 获取音频总时长
        cmd = [
            "ffprobe", "-v", "error", "-show_entries", "format=duration", 
            "-of", "default=noprint_wrappers=1:nokey=1", audio_path
        ]
        
        process = subprocess.run(cmd, capture_output=True, text=True)
        duration = float(process.stdout.strip())
        
        # 计算分段数
        num_segments = math.ceil(duration / segment_duration)
        segment_paths = []
        
        for i in range(num_segments):
            start_time = i * segment_duration
            output_path = os.path.join(temp_dir, f"segment_{i}_{int(time.time())}.wav")
            
            cmd = [
                "ffmpeg", "-i", audio_path, "-ss", str(start_time),
                "-t", str(segment_duration), "-c", "copy", output_path
            ]
            
            subprocess.run(cmd, capture_output=True)
            segment_paths.append(output_path)
        
        return segment_paths
    
    def _time_str_to_seconds(self, time_str):
        """将时间字符串转换为秒数"""
        if ',' in time_str:
            # SRT格式: 00:00:00,000
            h, m, rest = time_str.split(':')
            s, ms = rest.split(',')
            return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
        else:
            # VTT格式: 00:00:00.000
            h, m, rest = time_str.split(':')
            s, ms = rest.split('.')
            return int(h) * 3600 + int(m) * 60 + int(s) + int(ms) / 1000
    
    def _format_segments_preview(self, segments):
        """格式化字幕段落为预览文本"""
        preview = ""
        
        # 不再限制显示的字幕数量，显示所有字幕
        for segment in segments:
            start_time = self._format_time(segment['start'])
            end_time = self._format_time(segment['end'])
            text = segment['text'].strip()
            
            preview += f"{start_time} --> {end_time}\n{text}\n\n"
        
        return preview
    
    def _format_time(self, seconds):
        """将秒数格式化为时间字符串"""
        h = int(seconds / 3600)
        m = int((seconds % 3600) / 60)
        s = int(seconds % 60)
        ms = int((seconds - int(seconds)) * 1000)
        
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    
    def _generate_subtitle_file(self, subtitle_data):
        """生成字幕文件"""
        # 确保输出目录存在
        os.makedirs(os.path.dirname(os.path.abspath(self.output_path)), exist_ok=True)
        
        # 根据文件扩展名选择格式
        if self.output_path.endswith(".srt"):
            self._write_srt_file(subtitle_data)
        elif self.output_path.endswith(".vtt"):
            self._write_vtt_file(subtitle_data)
        elif self.output_path.endswith(".ass"):
            self._write_ass_file(subtitle_data)
        else:
            # 默认使用SRT格式
            self._write_srt_file(subtitle_data)
    
    def _write_srt_file(self, subtitle_data):
        """写入SRT格式字幕文件"""
        with open(self.output_path, "w", encoding="utf-8") as f:
            for i, segment in enumerate(subtitle_data):
                start_time = self._format_time(segment["start"])
                end_time = self._format_time(segment["end"])
                text = segment["text"]
                
                f.write(f"{i+1}\n")
                f.write(f"{start_time} --> {end_time}\n")
                f.write(f"{text}\n\n")
    
    def _write_vtt_file(self, subtitle_data):
        """写入VTT格式字幕文件"""
        with open(self.output_path, "w", encoding="utf-8") as f:
            f.write("WEBVTT\n\n")
            
            for segment in subtitle_data:
                start_time = self._format_time(segment["start"]).replace(",", ".")
                end_time = self._format_time(segment["end"]).replace(",", ".")
                text = segment["text"]
                
                f.write(f"{start_time} --> {end_time}\n")
                f.write(f"{text}\n\n")
    
    def _write_ass_file(self, subtitle_data):
        """写入ASS格式字幕文件"""
        with open(self.output_path, "w", encoding="utf-8") as f:
            # 写入ASS头部
            f.write("[Script Info]\n")
            f.write("Title: Auto-generated subtitle\n")
            f.write("ScriptType: v4.00+\n")
            f.write("WrapStyle: 0\n")
            f.write("ScaledBorderAndShadow: yes\n")
            f.write("PlayResX: 1920\n")
            f.write("PlayResY: 1080\n\n")
            
            # 写入样式
            f.write("[V4+ Styles]\n")
            f.write("Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n")
            f.write("Style: Default,Arial,48,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,2,2,2,10,10,10,1\n\n")
            
            # 写入事件
            f.write("[Events]\n")
            f.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
            
            for segment in subtitle_data:
                start_time = self._format_ass_time(segment["start"])
                end_time = self._format_ass_time(segment["end"])
                text = segment["text"].replace("\n", "\\N")
                
                f.write(f"Dialogue: 0,{start_time},{end_time},Default,,0,0,0,,{text}\n")
    
    def _format_ass_time(self, seconds):
        """将秒数格式化为ASS时间字符串 (h:mm:ss.cc)"""
        h = int(seconds / 3600)
        m = int((seconds % 3600) / 60)
        s = int(seconds % 60)
        cs = int((seconds - int(seconds)) * 100)  # 百分之一秒
        
        return f"{h}:{m:02d}:{s:02d}.{cs:02d}"
    
    def _map_language_code(self, language):
        """将语言名称映射到语言代码"""
        language_map = {
            "自动检测": None,
            "中文": "zh",
            "英文": "en",
            "日语": "ja",
            "韩语": "ko",
            "法语": "fr",
            "德语": "de",
            "俄语": "ru",
            "西班牙语": "es"
        }
        return language_map.get(language, None)
    
    def stop(self):
        """停止处理"""
        self.is_running = False
        print("字幕生成线程收到停止信号")