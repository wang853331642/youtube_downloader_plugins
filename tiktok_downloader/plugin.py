import re
import os
import subprocess
import json
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel, 
                            QLineEdit, QPushButton, QMessageBox, QProgressBar, 
                            QGroupBox, QDialog, QHBoxLayout, QCheckBox)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QSize
from PyQt5.QtGui import QIcon

# 导入插件基类
try:
    from youtube_downloader import PluginBase
except ImportError:
    # 为了开发时能够正确导入
    class PluginBase:
        def __init__(self, app_instance=None):
            self.app = app_instance
            
class TiktokDownloadThread(QThread):
    """TikTok视频下载线程"""
    progress_updated = pyqtSignal(int, str)
    download_complete = pyqtSignal(bool, str, str)  # 成功状态, 消息, 文件路径
    
    def __init__(self, url, output_dir, no_watermark=True):
        super().__init__()
        
        self.url = url
        self.output_dir = output_dir
        self.no_watermark = no_watermark
        self.is_running = True
        self.file_path = ""
        
    def run(self):
        try:
            # 确保输出目录存在
            os.makedirs(self.output_dir, exist_ok=True)
            
            # 设置输出文件模板
            output_template = os.path.join(self.output_dir, "%(title)s.%(ext)s")
            
            # 设置yt-dlp命令
            cmd = [
                "yt-dlp", 
                self.url, 
                "-o", output_template,
                "--newline",  # 确保进度实时显示
                "--progress",  # 显示进度条
                "--no-colors", # 移除颜色代码，便于解析输出
                "--no-playlist", # 不下载播放列表
                "--no-check-certificate"  # 不检查证书
            ]
            
            # 添加无水印选项
            if self.no_watermark:
                cmd.append("--remux-video")
                cmd.append("mp4")
                cmd.append("--postprocessor-args")
                cmd.append("ffmpeg:-c:v libx264 -c:a aac -movflags +faststart")
            
            self.progress_updated.emit(5, "正在连接TikTok...")
            
            # 启动下载进程
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            # 捕获文件名
            downloaded_file = ""
            
            # 监控进度
            while process.poll() is None:
                if not self.is_running:
                    process.terminate()
                    self.progress_updated.emit(0, "下载已取消")
                    self.download_complete.emit(False, "取消下载", "")
                    return
                
                line = process.stdout.readline().strip()
                if not line:
                    continue
                    
                print(f"yt-dlp输出: {line}")
                
                # 检测下载进度
                if '[download]' in line and '%' in line:
                    try:
                        # 提取百分比
                        percent_str = line.split('%')[0].split()[-1]
                        percent = float(percent_str)
                        self.progress_updated.emit(int(percent), f"下载中... {percent:.1f}%")
                    except:
                        pass
                
                # 检测下载文件名
                elif 'Destination:' in line:
                    try:
                        downloaded_file = line.split('Destination:')[1].strip()
                        self.file_path = downloaded_file
                        print(f"下载文件: {downloaded_file}")
                        self.progress_updated.emit(2, f"准备下载: {os.path.basename(downloaded_file)}")
                    except:
                        pass
                
                # 检测下载速度等信息
                elif 'ETA' in line:
                    try:
                        eta_parts = line.split('ETA')[1].strip()
                        self.progress_updated.emit(50, f"下载中... ETA: {eta_parts}")
                    except:
                        pass
                        
                # 检测去水印处理
                elif '[ffmpeg]' in line:
                    self.progress_updated.emit(80, "正在去除水印...")
            
            # 检查是否成功
            if process.returncode == 0:
                self.progress_updated.emit(100, "下载完成!")
                self.download_complete.emit(True, "下载完成", self.file_path)
            else:
                error = process.stderr.read()
                self.progress_updated.emit(0, f"下载失败")
                self.download_complete.emit(False, f"下载失败: {error[:100]}...", "")
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.progress_updated.emit(0, f"下载出错")
            self.download_complete.emit(False, str(e), "")
    
    def stop(self):
        """安全停止下载过程"""
        self.is_running = False

class TiktokDownloaderPlugin(PluginBase):
    """TikTok短视频下载插件 - 使用yt-dlp下载TikTok视频"""
    
    def __init__(self, app_instance=None):
        super().__init__(app_instance)
        self.name = "TikTok短视频下载器"
        self.version = "1.0.0"
        self.description = "使用yt-dlp命令下载TikTok视频，支持去除水印，界面美观，使用简单"
        self.author = "YT下载器团队"
        self.app = app_instance
        
    def initialize(self):
        """初始化插件"""
        print("TikTok下载插件已初始化")
        self.add_tiktok_action()
        return True
    
    def add_tiktok_action(self):
        """添加TikTok下载按钮到主界面"""
        try:
            # 创建TikTok下载按钮
            self.tiktok_button = QPushButton("TikTok下载")
            
            # 添加图标
            icon_found = False
            
            # 1. 尝试在插件目录找图标
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tiktok_icon.png")
            if os.path.exists(icon_path):
                self.tiktok_button.setIcon(QIcon(icon_path))
                self.tiktok_button.setIconSize(QSize(20, 20))
                icon_found = True
            
            # 2. 尝试在应用资源目录找图标
            if not icon_found and hasattr(self.app, "resource_dir"):
                app_icon_path = os.path.join(self.app.resource_dir, "icons", "tiktok.png")
                if os.path.exists(app_icon_path):
                    self.tiktok_button.setIcon(QIcon(app_icon_path))
                    self.tiktok_button.setIconSize(QSize(20, 20))
                    icon_found = True
            
            # 修改按钮样式
            self.tiktok_button.setStyleSheet("""
                QPushButton {
                    background-color: #000000;  /* TikTok黑色 */
                    color: white;
                    border: none;
                    border-radius: 5px;
                    padding: 5px 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #25F4EE;  /* TikTok青色 */
                    color: black;
                    border: 1px solid #FE2C55;  /* TikTok红色 */
                }
                QPushButton:pressed {
                    background-color: #FE2C55;  /* TikTok红色 */
                    color: white;
                }
            """)
            self.tiktok_button.setCursor(Qt.PointingHandCursor)
            
            # 设置固定宽度
            self.tiktok_button.setFixedWidth(120)
            
            # 连接点击事件
            self.tiktok_button.clicked.connect(self.show_tiktok_dialog)
            
            # 尝试添加到界面，优先放在A站按钮旁边或字幕按钮旁边
            added = False
            
            # 1. 尝试找到A站按钮并在其旁边添加
            if not added:
                for widget in self.app.findChildren(QPushButton):
                    if widget.text() == "A站下载":
                        parent = widget.parent()
                        if parent and parent.layout():
                            layout = parent.layout()
                            # 遍历布局查找按钮位置
                            for i in range(layout.count()):
                                item = layout.itemAt(i)
                                if item and item.widget() == widget:
                                    # 找到A站按钮后，在其后面插入TikTok按钮
                                    layout.insertWidget(i+1, self.tiktok_button)
                                    print("已添加TikTok下载按钮到A站按钮旁边")
                                    added = True
                                    break
                        break
            
            # 2. 如果没有找到A站按钮，尝试找字幕按钮
            if not added and hasattr(self.app, 'subtitle_btn') and hasattr(self.app, 'history_layout'):
                # 找出字幕按钮在布局中的位置
                for i in range(self.app.history_layout.count()):
                    item = self.app.history_layout.itemAt(i)
                    if item and item.widget() == self.app.subtitle_btn:
                        # 找到字幕按钮后，在其后面插入TikTok按钮
                        self.app.history_layout.insertWidget(i+1, self.tiktok_button)
                        print("已添加TikTok下载按钮到字幕按钮旁边")
                        added = True
                        break
            
            # 3. 如果没有找到合适的位置，则添加到默认位置
            if not added:
                if hasattr(self.app, 'history_layout'):
                    self.app.history_layout.addWidget(self.tiktok_button)
                    print("已添加TikTok下载按钮到history_layout")
                elif hasattr(self.app, 'toolbar_layout'):
                    self.app.toolbar_layout.addWidget(self.tiktok_button)
                    print("已添加TikTok下载按钮到toolbar_layout")
                else:
                    print("无法找到合适的布局添加TikTok下载按钮")
                
        except Exception as e:
            print(f"添加TikTok下载按钮失败: {e}")
            
    def show_tiktok_dialog(self):
        """显示TikTok下载对话框"""
        # 检查yt-dlp是否安装
        try:
            subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True)
        except:
            QMessageBox.critical(self.app, "缺少必要组件", 
                "无法找到yt-dlp，这是下载TikTok视频所必需的。\n\n"
                "请安装yt-dlp: pip install yt-dlp -U")
            return
            
        dialog = QDialog(self.app)
        dialog.setWindowTitle("TikTok视频下载")
        dialog.resize(520, 350)
        # 去除右上角的问号按钮
        dialog.setWindowFlags(dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        # 设置窗口样式
        dialog.setStyleSheet("""
            QDialog {
                background-color: #F8F8F8;
            }
            QGroupBox {
                font-weight: bold;
                border: 1px solid #CCCCCC;
                border-radius: 5px;
                margin-top: 10px;
                background-color: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
                color: #FE2C55;  /* TikTok红色 */
            }
            QLabel {
                color: #333333;
            }
            QProgressBar {
                border: 1px solid #CCCCCC;
                border-radius: 3px;
                text-align: center;
                background-color: white;
                height: 20px;
            }
            QProgressBar::chunk {
                background-color: #FE2C55;  /* TikTok红色 */
                width: 5px;
                margin: 0px;
            }
            QPushButton {
                border-radius: 5px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QCheckBox {
                color: #333333;
            }
            QCheckBox::indicator {
                width: 16px;
                height: 16px;
            }
            QCheckBox::indicator:checked {
                image: url(icons/checkbox_checked.png);
                background-color: #25F4EE;  /* TikTok青色 */
                border: 1px solid #FE2C55;  /* TikTok红色 */
            }
        """)
        
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(15, 15, 15, 15)
        layout.setSpacing(10)
        
        # TikTok标题显示
        title_layout = QHBoxLayout()
        title_icon = QLabel("📱")
        title_icon.setStyleSheet("font-size: 24px;")
        title_text = QLabel("TikTok视频下载")
        title_text.setStyleSheet("font-size: 18px; font-weight: bold; color: #FE2C55;")
        title_layout.addWidget(title_icon)
        title_layout.addWidget(title_text)
        title_layout.addStretch()
        layout.addLayout(title_layout)
        
        # 创建下载表单
        form_group = QGroupBox("视频信息")
        form_layout = QFormLayout(form_group)
        form_layout.setContentsMargins(15, 20, 15, 15)
        form_layout.setSpacing(10)
        
        # URL输入框
        self.url_input = QLineEdit()
        self.url_input.setText("")
        self.url_input.setPlaceholderText("请输入TikTok视频链接")
        self.url_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 5px;
                background-color: white;
                selection-background-color: #FE2C55;  /* TikTok红色 */
            }
            QLineEdit:focus {
                border: 1px solid #FE2C55;  /* TikTok红色 */
            }
        """)
        self.url_input.setMinimumHeight(28)
        form_layout.addRow("视频链接:", self.url_input)
        
        # 去水印选项
        self.no_watermark_check = QCheckBox("去除视频水印（可能需要额外处理时间）")
        self.no_watermark_check.setChecked(True)
        form_layout.addRow("", self.no_watermark_check)
        
        layout.addWidget(form_group)
        
        # 进度显示
        progress_group = QGroupBox("下载进度")
        progress_layout = QVBoxLayout(progress_group)
        progress_layout.setContentsMargins(15, 20, 15, 15)
        
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        self.progress_bar.setMinimumHeight(20)
        progress_layout.addWidget(self.progress_bar)
        
        self.status_label = QLabel("准备下载...")
        self.status_label.setAlignment(Qt.AlignCenter)
        progress_layout.addWidget(self.status_label)
        
        layout.addWidget(progress_group)
        
        # 提示说明
        tips_label = QLabel("提示: TikTok视频链接可从应用内分享按钮获取，或从浏览器地址栏复制")
        tips_label.setStyleSheet("color: #757575; font-size: 11px;")
        layout.addWidget(tips_label)
        
        # 按钮布局
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)
        
        # 下载按钮
        self.download_btn = QPushButton("开始下载")
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #FE2C55;  /* TikTok红色 */
                color: white;
                border: none;
            }
            QPushButton:hover {
                background-color: #FF4D70;
            }
            QPushButton:pressed {
                background-color: #E6254C;
            }
            QPushButton:disabled {
                background-color: #FFCCD5;
            }
        """)
        self.download_btn.setCursor(Qt.PointingHandCursor)
        self.download_btn.setMinimumHeight(36)
        self.download_btn.clicked.connect(self.start_download)
        buttons_layout.addWidget(self.download_btn)
        
        # 取消按钮
        self.cancel_btn = QPushButton("取消下载")
        self.cancel_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border: none;
            }
            QPushButton:hover {
                background-color: #FF5252;
            }
            QPushButton:pressed {
                background-color: #D32F2F;
            }
            QPushButton:disabled {
                background-color: #FFCDD2;
            }
        """)
        self.cancel_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_btn.setMinimumHeight(36)
        self.cancel_btn.clicked.connect(self.cancel_download)
        self.cancel_btn.setEnabled(False)
        buttons_layout.addWidget(self.cancel_btn)
        
        # 关闭按钮
        self.close_btn = QPushButton("关闭")
        self.close_btn.setStyleSheet("""
            QPushButton {
                background-color: #9E9E9E;
                color: white;
                border: none;
            }
            QPushButton:hover {
                background-color: #BDBDBD;
            }
            QPushButton:pressed {
                background-color: #757575;
            }
        """)
        self.close_btn.setCursor(Qt.PointingHandCursor)
        self.close_btn.setMinimumHeight(36)
        self.close_btn.clicked.connect(dialog.close)
        buttons_layout.addWidget(self.close_btn)
        
        layout.addLayout(buttons_layout)
        
        # 版权信息
        version_label = QLabel(f"TikTok下载器 v{self.version} | {self.author}")
        version_label.setStyleSheet("color: #BDBDBD; font-size: 10px;")
        version_label.setAlignment(Qt.AlignRight)
        layout.addWidget(version_label)
        
        self.download_dialog = dialog
        dialog.exec_()
        
    def start_download(self):
        """开始下载TikTok视频"""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(None, "输入错误", "请输入有效的TikTok视频链接")
            return
            
        # 获取输出目录
        output_dir = "downloads"
        if hasattr(self.app, 'download_dir'):
            output_dir = self.app.download_dir
            
        # 获取去水印选项
        no_watermark = self.no_watermark_check.isChecked()
            
        # 创建并启动下载线程
        self.download_thread = TiktokDownloadThread(url, output_dir, no_watermark)
        self.download_thread.progress_updated.connect(self.update_progress)
        self.download_thread.download_complete.connect(self.on_download_complete)
        
        # 更新界面状态
        self.download_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)
        self.progress_bar.setValue(0)
        self.status_label.setText("准备下载...")
        
        self.download_thread.start()
        
    def update_progress(self, value, message):
        """更新下载进度"""
        self.progress_bar.setValue(value)
        self.status_label.setText(message)
        
    def on_download_complete(self, success, message, file_path):
        """下载完成处理"""
        self.download_btn.setEnabled(True)
        self.cancel_btn.setEnabled(False)
        
        if success:
            # 如果下载成功且有文件路径
            if file_path and os.path.exists(file_path):
                # 获取文件大小
                file_size = os.path.getsize(file_path) / (1024 * 1024)  # MB
                file_name = os.path.basename(file_path)
                
                # 显示下载完成的详细信息
                QMessageBox.information(
                    None, 
                    "下载完成", 
                    f"视频已成功下载!\n\n"
                    f"文件名: {file_name}\n"
                    f"大小: {file_size:.2f} MB\n"
                    f"保存位置: {file_path}"
                )
                
                # 更新状态文本
                self.status_label.setText(f"下载完成: {file_name}")
                
                # 尝试打开文件所在的文件夹
                try:
                    if hasattr(self.app, 'open_folder'):
                        # 如果应用有打开文件夹的方法
                        self.app.open_folder(os.path.dirname(file_path))
                except:
                    pass
            else:
                # 文件不存在但下载成功，可能是路径未正确获取
                QMessageBox.information(None, "下载完成", "视频已成功下载!")
                self.status_label.setText("下载完成!")
        else:
            # 下载失败
            QMessageBox.warning(None, "下载失败", f"{message}")
            self.status_label.setText("下载失败，请重试")
            # 重置进度条
            self.progress_bar.setValue(0)
            
    def cancel_download(self):
        """取消正在进行的下载"""
        if hasattr(self, 'download_thread') and self.download_thread:
            self.download_thread.stop()
            self.download_btn.setEnabled(True)
            self.cancel_btn.setEnabled(False)
            self.status_label.setText("下载已取消")

    def cleanup_ui(self):
        """清理插件添加的UI元素"""
        if hasattr(self, 'tiktok_button') and self.tiktok_button:
            try:
                # 从布局中移除按钮
                button = self.tiktok_button
                parent = button.parent()
                if parent:
                    layout = parent.layout()
                    if layout:
                        layout.removeWidget(button)
                button.setParent(None)  # 断开与父对象的连接
                print(f"已清理TikTok下载按钮")
            except Exception as e:
                print(f"清理TikTok下载按钮失败: {e}")

    def get_hooks(self):
        """返回此插件提供的所有钩子"""
        return {
            "on_startup": self.on_startup,
            "on_disable": self.on_disable,
            "custom_action": self.add_tiktok_action
        }
        
    def on_startup(self):
        """应用启动时执行"""
        print("TikTok下载插件已启动")
        self.add_tiktok_action()
        
    def on_disable(self):
        """插件被禁用时执行"""
        print("TikTok下载插件被禁用")
        self.cleanup_ui()