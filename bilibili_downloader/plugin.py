# 在 plugins/bilibili_downloader/plugin.py 中
import re
import json
import requests
from urllib.parse import urlparse, parse_qs
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QFormLayout, QLabel, 
                            QLineEdit, QPushButton, QComboBox, QCheckBox, 
                            QMessageBox, QProgressBar, QGroupBox, QDialog)
from PyQt5.QtCore import QThread, pyqtSignal, Qt

# 导入插件基类
try:
    from youtube_downloader import PluginBase
except ImportError:
    # 为了开发时能够正确导入
    class PluginBase:
        def __init__(self, app_instance=None):
            self.app = app_instance
            self.settings = {}
        
        def get_setting(self, key, default=None):
            return self.settings.get(key, default)
            
        def set_setting(self, key, value):
            self.settings[key] = value

class BilibiliDownloadThread(QThread):
    """B站视频下载线程"""
    progress_updated = pyqtSignal(int, str)
    download_complete = pyqtSignal(bool, str, str)
    
    def __init__(self, url, quality, output_dir, cookies=None):
        super().__init__()
        self.url = url
        self.quality = quality
        self.output_dir = output_dir
        self.cookies = cookies or {}

    def run(self):
        try:
            # 1. 解析视频ID
            video_id = self.extract_video_id(self.url)
            if not video_id:
                self.progress_updated.emit(0, "无效的B站视频链接")
                self.download_complete.emit(False, "", "无效的链接")
                return
                
            # 2. 获取视频信息
            self.progress_updated.emit(10, "正在获取视频信息...")
            video_info = self.get_video_info(video_id)
            if not video_info:
                self.progress_updated.emit(0, "获取视频信息失败")
                self.download_complete.emit(False, "", "获取信息失败")
                return
                
            # 3. 获取视频下载链接
            self.progress_updated.emit(30, "正在获取下载链接...")
            download_url = self.get_download_url(video_id, self.quality)
            if not download_url:
                self.progress_updated.emit(0, "获取下载链接失败")
                self.download_complete.emit(False, "", "获取下载链接失败")
                return
                
            # 4. 下载视频
            self.progress_updated.emit(50, "开始下载视频...")
            output_path = self.download_video(download_url, video_info['title'], self.output_dir)
            if not output_path:
                self.progress_updated.emit(0, "下载视频失败")
                self.download_complete.emit(False, "", "下载失败")
                return
                
            # 5. 完成下载
            self.progress_updated.emit(100, "下载完成")
            self.download_complete.emit(True, output_path, video_info['title'])
            
        except Exception as e:
            self.progress_updated.emit(0, f"下载出错: {str(e)}")
            self.download_complete.emit(False, "", str(e))
    
    def extract_video_id(self, url):
        """从URL中提取B站视频ID"""
        # 支持 https://www.bilibili.com/video/BV1xx411c7mD/ 格式
        bv_pattern = r'bilibili\.com/video/([BbAa][Vv][0-9a-zA-Z]+)'
        match = re.search(bv_pattern, url)
        if match:
            return match.group(1)
            
        # 支持 https://b23.tv/xxx 短链接
        if 'b23.tv' in url:
            try:
                response = requests.head(url, allow_redirects=True)
                return self.extract_video_id(response.url)
            except:
                pass
                
        return None
        
    def get_video_info(self, video_id):
        """获取B站视频信息"""
        try:
            api_url = f"https://api.bilibili.com/x/web-interface/view?bvid={video_id}"
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Referer': 'https://www.bilibili.com'
            }
            
            response = requests.get(api_url, headers=headers, cookies=self.cookies)
            data = response.json()
            
            if data['code'] == 0:
                return {
                    'title': data['data']['title'],
                    'cover': data['data']['pic'],
                    'author': data['data']['owner']['name'],
                    'aid': data['data']['aid'],
                    'cid': data['data']['cid']
                }
        except Exception as e:
            print(f"获取视频信息失败: {e}")
        
        return None
        
    def get_download_url(self, video_id, quality=0):
        """获取视频下载链接，直接返回B站视频URL即可，由yt-dlp实际处理"""
        # 使用通用的视频URL格式，yt-dlp会自动处理真实下载链接的获取
        try:
            if video_id.startswith(('BV', 'bv')):
                return f"https://www.bilibili.com/video/{video_id}"
            elif video_id.startswith(('AV', 'av')):
                return f"https://www.bilibili.com/video/{video_id}"
            return f"https://www.bilibili.com/video/{video_id}"
        except Exception as e:
            print(f"获取B站下载链接失败: {e}")
            return None
        
    def download_video(self, url, title, output_dir):
        """下载视频文件，使用yt-dlp实现实际下载"""
        try:
            import os
            import subprocess
            import sys
            
            # 创建输出目录
            os.makedirs(output_dir, exist_ok=True)
            
            # 生成安全的文件名
            safe_title = re.sub(r'[\\/:*?"<>|]', '_', title)
            output_path = os.path.join(output_dir, f"{safe_title}.mp4")
            
            # 构建yt-dlp命令
            cmd = ["yt-dlp"]
            
            # 临时cookie文件路径
            cookie_file_path = None
            
            # 添加cookie参数(如果有)
            if self.cookies:
                try:
                    import tempfile
                    cookie_file = tempfile.NamedTemporaryFile(delete=False, suffix='.txt', mode='w')
                    cookie_file_path = cookie_file.name
                    cookie_file.write(f"# Netscape HTTP Cookie File\n")
                    
                    for key, value in self.cookies.items():
                        if key and value:
                            cookie_file.write(f".bilibili.com\tTRUE\t/\tFALSE\t0\t{key}\t{value}\n")
                    
                    cookie_file.close()
                    cmd.extend(["--cookies", cookie_file_path])
                except Exception as e:
                    print(f"设置cookie失败: {e}")
                    
            try:
                # 添加其他参数
                cmd.extend([
                    url,                       # B站视频URL
                    "-o", output_path,         # 输出文件路径
                    "--no-playlist",           # 不作为播放列表下载
                    "--merge-output-format", "mp4", # 合并为mp4格式
                    "--no-check-certificate",  # 不检查SSL证书
                    "--user-agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "--referer", "https://www.bilibili.com/"
                ])
                
                self.progress_updated.emit(20, "开始下载...")
                
                # 在Windows下隐藏控制台窗口
                startupinfo = None
                if sys.platform == "win32":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                
                # 运行命令下载视频
                process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    startupinfo=startupinfo
                )
                
                # 监控进度
                while process.poll() is None:
                    line = process.stdout.readline().strip()
                    if line:
                        print(f"yt-dlp输出: {line}")
                        if '[download]' in line and '%' in line:
                            try:
                                percent = float(line.split('%')[0].split()[-1])
                                self.progress_updated.emit(int(percent), f"下载中... {percent:.1f}%")
                            except:
                                pass
                
                # 检查是否成功
                if process.returncode == 0:
                    self.progress_updated.emit(100, "下载完成!")
                    if os.path.exists(output_path):
                        return output_path
                    else:
                        # 尝试查找可能以不同扩展名下载的文件
                        base_dir = os.path.dirname(output_path)
                        base_name = os.path.splitext(os.path.basename(output_path))[0]
                        for ext in ['.mp4', '.mkv', '.flv', '.webm']:
                            alt_path = os.path.join(base_dir, f"{base_name}{ext}")
                            if os.path.exists(alt_path):
                                return alt_path
                else:
                    error = process.stderr.read()
                    print(f"下载失败，错误信息: {error}")
                    self.progress_updated.emit(0, "下载失败")
                    return None
                    
                return None
                
            finally:
                # 删除临时cookie文件
                if cookie_file_path and os.path.exists(cookie_file_path):
                    try:
                        os.unlink(cookie_file_path)
                        print(f"已删除临时cookie文件")
                    except Exception as e:
                        print(f"删除临时cookie文件失败: {e}")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            print(f"下载B站视频失败: {e}")
            self.progress_updated.emit(0, f"下载出错: {e}")
            return None

class BilibiliDownloaderPlugin(PluginBase):
    """B站视频下载插件 - 支持从哔哩哔哩下载视频"""
    
    def __init__(self, app_instance=None):
        super().__init__(app_instance)
        self.name = "B站视频下载器"
        self.version = "1.0"
        self.description = "支持从哔哩哔哩网站下载视频的插件"
        self.author = "YT下载器团队"
        self.settings = {}  # 确保初始化settings属性
        self.app = app_instance  # 存储应用实例
        self.load_settings()
        
    # 添加 get_setting 和 set_setting 方法
    def get_setting(self, key, default=None):
        """获取插件设置"""
        return self.settings.get(key, default)
    def cleanup_ui(self):
        """清理插件添加的UI元素"""
        if hasattr(self, 'bilibili_button') and self.bilibili_button:
            try:
                # 从布局中移除按钮
                button = self.bilibili_button
                parent = button.parent()
                if parent:
                    layout = parent.layout()
                    if layout:
                        layout.removeWidget(button)
                button.setParent(None)  # 断开与父对象的连接，但不删除按钮对象
                print(f"已清理B站下载按钮 (ID: {button.objectName()})")
            except Exception as e:
                print(f"清理B站下载按钮失败: {e}")
    def set_setting(self, key, value):
        """设置插件设置"""
        self.settings[key] = value
        self.save_settings()  # 自动保存设置
        
    def initialize(self):
        """初始化插件"""
        print("B站下载插件已初始化")
        self.add_bilibili_action()
        return True
        
    def get_hooks(self):
        """返回此插件提供的所有钩子"""
        return {
            "on_startup": self.on_startup,
            "custom_action": self.add_bilibili_action
        }
        
    def on_startup(self):
        """应用启动时执行"""
        print("B站下载插件已启动")
        
    def add_bilibili_action(self):
        """添加B站下载按钮到主界面"""
        # 导入必要的模块
        import os
        from PyQt5.QtWidgets import QPushButton
        from PyQt5.QtCore import QSize, Qt
        from PyQt5.QtGui import QIcon
        
        if not self.app:
            print("无法添加B站下载按钮：应用实例不存在")
            return
        
        # 先清理可能存在的重复按钮
        self._remove_existing_buttons()
            
        # 检查自己的实例是否已添加按钮
        if hasattr(self, 'bilibili_button') and self.bilibili_button:
            # 如果按钮已存在但没有父对象（已被移除），则重新添加
            if self.bilibili_button.parent() is None:
                self._add_button_to_layout()
            return
        
        try:
            # 创建B站下载按钮
            self.bilibili_button = QPushButton("B站下载")
            
            # 设置唯一对象名
            button_id = f"bilibili_download_button_{id(self)}"
            self.bilibili_button.setObjectName(button_id)
            
            # 设置图标（可选，如果有图标的话）
            self.bilibili_button.setIcon(QIcon("path/to/icon.png"))
            self.bilibili_button.setIconSize(QSize(20, 20))
            
            # 使用与字幕按钮相同的样式
            self.bilibili_button.setStyleSheet("""
                QPushButton {
                    background-color: #FB7299;  /* B站粉色 */
                    color: white;
                    border-radius: 5px;
                    padding: 5px 10px;
                    padding-left: 8px;
                    font-weight: bold;
                    text-align: center;
                }
                QPushButton:hover {
                    background-color: #FC8BAB;
                }
                QPushButton:pressed {
                    background-color: #E45F86;
                }
            """)
            self.bilibili_button.setCursor(Qt.PointingHandCursor)
            
            # 设置与字幕按钮相同的宽度
            self.bilibili_button.setFixedWidth(100)
            
            # 连接点击事件
            self.bilibili_button.clicked.connect(self.show_bilibili_dialog)
            
            # 添加按钮到布局，优先放在字幕按钮旁边
            self._add_button_to_layout()
            
        except Exception as e:
            print(f"添加B站下载按钮失败: {e}")

    def _remove_existing_buttons(self):
        """查找并移除所有已存在的B站下载按钮"""
        try:
            # 检查历史布局中的按钮
            if hasattr(self.app, 'history_layout'):
                layout = self.app.history_layout
                buttons_to_remove = []
                
                # 首先找出所有需要移除的按钮
                for i in range(layout.count()):
                    widget = layout.itemAt(i).widget()
                    if widget and isinstance(widget, QPushButton) and "bilibili_download_button" in widget.objectName():
                        # 如果不是当前实例的按钮
                        if not hasattr(self, 'bilibili_button') or widget != self.bilibili_button:
                            buttons_to_remove.append(widget)
                
                # 然后移除这些按钮
                for widget in buttons_to_remove:
                    layout.removeWidget(widget)
                    widget.setParent(None)
                    widget.deleteLater()  # 确保widget被删除
                    print(f"移除已存在的B站下载按钮 (ID: {widget.objectName()})")
            
            # 检查工具栏布局中的按钮
            if hasattr(self.app, 'toolbar_layout'):
                layout = self.app.toolbar_layout
                buttons_to_remove = []
                
                for i in range(layout.count()):
                    widget = layout.itemAt(i).widget()
                    if widget and isinstance(widget, QPushButton) and "bilibili_download_button" in widget.objectName():
                        if not hasattr(self, 'bilibili_button') or widget != self.bilibili_button:
                            buttons_to_remove.append(widget)
                
                for widget in buttons_to_remove:
                    layout.removeWidget(widget)
                    widget.setParent(None)
                    widget.deleteLater()
                    print(f"移除已存在的B站下载按钮 (ID: {widget.objectName()})")
        
        except Exception as e:
            print(f"移除已存在的B站下载按钮时出错: {e}")

    def _add_button_to_layout(self):
        """将按钮添加到适当的布局中，优先放在字幕按钮旁边"""
        if not hasattr(self, 'bilibili_button') or not self.bilibili_button:
            return
        
        # 确保按钮已从其它布局中移除
        if self.bilibili_button.parent() is not None:
            parent = self.bilibili_button.parent()
            if parent and parent.layout():
                parent.layout().removeWidget(self.bilibili_button)
                self.bilibili_button.setParent(None)
        
        # 如果主界面有history_layout和subtitle_btn
        if hasattr(self.app, 'history_layout') and hasattr(self.app, 'subtitle_btn'):
            # 找到字幕按钮的索引
            for i in range(self.app.history_layout.count()):
                item = self.app.history_layout.itemAt(i)
                if item and item.widget() == self.app.subtitle_btn:
                    # 找到字幕按钮后，在其右侧插入B站下载按钮
                    self.app.history_layout.insertWidget(i + 1, self.bilibili_button)
                    print(f"已添加B站下载按钮到字幕按钮旁边 (ID: {self.bilibili_button.objectName()})")
                    return
            
            # 如果找不到字幕按钮但history_layout存在，直接添加到布局中
            self.app.history_layout.addWidget(self.bilibili_button)
            print(f"已添加B站下载按钮到history_layout (ID: {self.bilibili_button.objectName()})")
        elif hasattr(self.app, 'toolbar_layout'):
            # 如果找不到history_layout或字幕按钮，尝试添加到toolbar_layout
            self.app.toolbar_layout.addWidget(self.bilibili_button)
            print(f"已添加B站下载按钮到toolbar_layout (ID: {self.bilibili_button.objectName()})")
        else:
            print("无法找到适合的布局添加B站下载按钮")
        
    def show_bilibili_dialog(self):
        """显示B站下载对话框"""
        from PyQt5.QtWidgets import QDialog, QVBoxLayout, QLabel
        
        # 检查yt-dlp是否安装
        try:
            import subprocess
            subprocess.run(["yt-dlp", "--version"], capture_output=True, text=True)
        except:
            QMessageBox.critical(self.app, "缺少必要组件", 
                "无法找到yt-dlp，这是下载B站视频所必需的。\n\n"
                "请安装yt-dlp: pip install yt-dlp -U")
            return
        
        dialog = QDialog(self.app)
        dialog.setWindowTitle("B站视频下载")
        dialog.resize(500, 400)
        
        layout = QVBoxLayout(dialog)
        
        # 创建下载表单
        form_group = QGroupBox("视频信息")
        form_layout = QFormLayout(form_group)
        
        # URL输入框
        self.url_input = QLineEdit()
        self.url_input.setPlaceholderText("请输入B站视频链接...")
        form_layout.addRow("视频链接:", self.url_input)
        
        # 清晰度选择
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("高清 1080P", 80)
        self.quality_combo.addItem("高清 720P", 64)
        self.quality_combo.addItem("清晰 480P", 32)
        self.quality_combo.addItem("流畅 360P", 16)
        form_layout.addRow("清晰度:", self.quality_combo)
        
        layout.addWidget(form_group)
        
        # 进度显示
        self.progress_bar = QProgressBar()
        self.progress_bar.setValue(0)
        layout.addWidget(self.progress_bar)
        
        self.status_label = QLabel("准备下载...")
        layout.addWidget(self.status_label)
        
        # 下载按钮
        self.download_btn = QPushButton("开始下载")
        self.download_btn.setStyleSheet("""
            QPushButton {
                background-color: #FB7299;
                color: white;
                border-radius: 5px;
                padding: 8px 20px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #FC8BAB;
            }
            QPushButton:pressed {
                background-color: #E45F86;
            }
        """)
        self.download_btn.clicked.connect(self.start_download)
        layout.addWidget(self.download_btn)
        
        dialog.exec_()
        
    def start_download(self):
        """开始下载B站视频"""
        url = self.url_input.text().strip()
        if not url:
            QMessageBox.warning(None, "输入错误", "请输入有效的B站视频链接")
            return
            
        # 获取选择的清晰度
        quality = self.quality_combo.currentData()
        
        # 获取输出目录
        output_dir = self.get_setting("output_dir", "downloads")
        if hasattr(self.app, 'download_dir'):
            output_dir = self.app.download_dir
            
        # 获取cookie信息
        cookies = {}
        bilibili_cookie = self.get_setting("bilibili_cookie", "")
        if bilibili_cookie:
            # 尝试解析cookie字符串
            try:
                cookie_parts = bilibili_cookie.split(';')
                for part in cookie_parts:
                    if '=' in part:
                        key, value = part.split('=', 1)
                        cookies[key.strip()] = value.strip()
            except:
                print("Cookie解析失败，将使用默认方式下载")
                
        # 创建下载线程
        self.download_thread = BilibiliDownloadThread(url, quality, output_dir, cookies)
        self.download_thread.progress_updated.connect(self.update_progress)
        self.download_thread.download_complete.connect(self.on_download_complete)
        
        # 禁用下载按钮
        self.download_btn.setEnabled(False)
        self.download_btn.setText("下载中...")
        
        # 开始下载
        self.download_thread.start()
        
    def update_progress(self, value, message):
        """更新下载进度"""
        self.progress_bar.setValue(value)
        self.status_label.setText(message)
        
    def on_download_complete(self, success, file_path, title):
        """下载完成处理"""
        self.download_btn.setEnabled(True)
        self.download_btn.setText("开始下载")
        
        if success:
            QMessageBox.information(None, "下载完成", f"视频 '{title}' 已成功下载到:\n{file_path}")
        else:
            QMessageBox.warning(None, "下载失败", f"下载失败: {title}")
        
    def create_settings_widget(self):
        """创建设置界面"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        
        # 基本设置
        basic_group = QGroupBox("基本设置")
        basic_layout = QFormLayout(basic_group)
        
        # 默认下载目录
        self.output_dir_input = QLineEdit()
        self.output_dir_input.setText(self.get_setting("output_dir", "downloads"))
        self.output_dir_input.textChanged.connect(lambda text: self.set_setting("output_dir", text))
        
        browse_btn = QPushButton("浏览...")
        browse_btn.clicked.connect(self.browse_output_dir)
        
        dir_layout = QVBoxLayout()
        dir_layout.addWidget(self.output_dir_input)
        dir_layout.addWidget(browse_btn)
        
        basic_layout.addRow("默认下载目录:", dir_layout)
        
        # 账号设置
        account_group = QGroupBox("账号设置 (可选)")
        account_layout = QFormLayout(account_group)
        
        self.bilibili_cookie = QLineEdit()
        self.bilibili_cookie.setText(self.get_setting("bilibili_cookie", ""))
        self.bilibili_cookie.setEchoMode(QLineEdit.Password)
        self.bilibili_cookie.setPlaceholderText("输入B站Cookie以下载高清视频")
        self.bilibili_cookie.textChanged.connect(lambda text: self.set_setting("bilibili_cookie", text))
        account_layout.addRow("B站Cookie:", self.bilibili_cookie)
        
        layout.addWidget(basic_group)
        layout.addWidget(account_group)
        
        # 关于信息
        about_label = QLabel("B站视频下载插件 v1.0\n支持从哔哩哔哩网站下载视频")
        about_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(about_label)
        
        return widget
        
    def browse_output_dir(self):
        """浏览选择输出目录"""
        from PyQt5.QtWidgets import QFileDialog
        
        dir_path = QFileDialog.getExistingDirectory(
            None, 
            "选择下载目录", 
            self.output_dir_input.text()
        )
        
        if dir_path:
            self.output_dir_input.setText(dir_path)
            self.set_setting("output_dir", dir_path)
            
    def load_settings(self):
        """加载插件设置"""
        try:
            import os
            import json
            
            # 设置文件路径
            settings_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings")
            os.makedirs(settings_dir, exist_ok=True)
            settings_file = os.path.join(settings_dir, "settings.json")
            
            # 如果设置文件存在，则加载
            if os.path.exists(settings_file):
                with open(settings_file, "r", encoding="utf-8") as f:
                    self.settings = json.load(f)
        except Exception as e:
            print(f"加载B站下载器插件设置失败: {e}")
            self.settings = {}
    
    def save_settings(self):
        """保存插件设置"""
        try:
            import os
            import json
            
            # 设置文件路径
            settings_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "settings")
            os.makedirs(settings_dir, exist_ok=True)
            settings_file = os.path.join(settings_dir, "settings.json")
            
            # 保存设置到文件
            with open(settings_file, "w", encoding="utf-8") as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=4)
        except Exception as e:
            print(f"保存B站下载器插件设置失败: {e}")