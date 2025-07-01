import os
import re
import sys
import json
import time
import uuid
import shutil
import datetime
import subprocess
import select
from urllib.parse import urlparse, parse_qs
from threading import Timer

from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QFormLayout, QLabel, 
                            QLineEdit, QPushButton, QMessageBox, QProgressBar, 
                            QGroupBox, QDialog, QTabWidget, QCheckBox, QComboBox,
                            QTableWidget, QTableWidgetItem, QHeaderView, QFileDialog,
                            QSpinBox, QTextEdit, QAbstractItemView, QApplication,
                            QMainWindow, QToolBar)
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QSize, QTimer, QDateTime, QUrl
from PyQt5.QtGui import QIcon, QFont, QColor, QDesktopServices

# 导入插件基类
try:
    from youtube_downloader import PluginBase
except ImportError:
    # 为了开发时能够正确导入
    class PluginBase:
        def __init__(self, app_instance=None):
            self.app = app_instance


class LiveRecordingThread(QThread):
    """B站直播录制线程"""
    progress_updated = pyqtSignal(str, int, str)  # 房间ID, 进度, 状态消息
    record_complete = pyqtSignal(str, bool, str, str)  # 房间ID, 成功状态, 消息, 文件路径
    stream_info_updated = pyqtSignal(str, dict)  # 房间ID, 直播信息字典
    
    def __init__(self, room_id, output_dir, quality="best", format="flv", 
                danmaku=True, stream_url=None, cover_url=None, streamer_name=None):
        super().__init__()
        
        self.room_id = str(room_id)
        self.output_dir = output_dir
        self.quality = quality
        self.format = format
        self.danmaku = danmaku
        self.is_running = True
        self.process = None
        self.file_path = ""
        self.stream_url = stream_url
        self.cover_url = cover_url
        self.streamer_name = streamer_name
        self.check_interval = 5  # 检查直播状态的间隔（秒）
        self.heartbeat_timer = None
        self.current_file = None
        self.signal_sent = False
        
    def run(self):
        try:
            # 确保输出目录存在
            os.makedirs(self.output_dir, exist_ok=True)
            
            # 检查是否已提供流URL
            if not self.stream_url:
                # 获取流信息
                self.progress_updated.emit(self.room_id, 5, "获取直播流信息...")
                stream_info = self.get_stream_info()
                
                if not stream_info:
                    self.record_complete.emit(self.room_id, False, "获取直播流信息失败", "")
                    return
                
                # 更新流信息
                self.stream_info_updated.emit(self.room_id, stream_info)
                
                # 提取流URL
                self.stream_url = stream_info.get('stream_url')
                if not self.stream_url:
                    self.record_complete.emit(self.room_id, False, "无法获取直播流地址", "")
                    return
                    
                # 提取封面和主播名
                self.cover_url = stream_info.get('cover_url', '')
                self.streamer_name = stream_info.get('streamer_name', '')
            
            # 准备文件名
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            safe_streamer_name = re.sub(r'[\\/*?:"<>|]', "_", self.streamer_name) if self.streamer_name else ""
            
            if safe_streamer_name:
                filename = f"{safe_streamer_name}_{self.room_id}_{timestamp}.{self.format}"
            else:
                filename = f"B站直播_{self.room_id}_{timestamp}.{self.format}"
                
            self.file_path = os.path.join(self.output_dir, filename)
            self.current_file = self.file_path  # 初始设置current_file
            
            # 准备弹幕文件
            danmaku_path = None
            if self.danmaku:
                danmaku_path = os.path.splitext(self.file_path)[0] + ".xml"
            
            self.progress_updated.emit(self.room_id, 10, "开始录制直播...")
            
            # 下载封面
            if self.cover_url:
                try:
                    import requests
                    cover_path = os.path.splitext(self.file_path)[0] + ".jpg"
                    response = requests.get(self.cover_url, stream=True, timeout=10)
                    with open(cover_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=1024):
                            if chunk:
                                f.write(chunk)
                    print(f"已下载封面: {cover_path}")
                except Exception as e:
                    print(f"下载封面失败: {e}")
            
            # 开始录制
            start_time = time.time()
            self.start_time = start_time  # 保存开始时间
            self.start_recording(danmaku_path)
            
            # 录制结束
            duration = time.time() - start_time
            duration_str = str(datetime.timedelta(seconds=int(duration)))
            
            # 检查文件是否存在且大小大于0
            if os.path.exists(self.file_path) and os.path.getsize(self.file_path) > 0:
                if not self.signal_sent:  # 如果信号尚未发送
                    self.signal_sent = True
                    self.progress_updated.emit(self.room_id, 100, f"录制完成，时长: {duration_str}")
                    self.record_complete.emit(self.room_id, True, 
                                            f"录制完成，时长: {duration_str}", self.file_path)
            else:
                if not self.signal_sent:  # 如果信号尚未发送
                    self.signal_sent = True
                    self.record_complete.emit(self.room_id, False, "录制失败或文件为空", "")
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.progress_updated.emit(self.room_id, 0, f"录制出错")
            self.record_complete.emit(self.room_id, False, str(e), "")
        finally:
            # 确保停止心跳检测
            self.stop_heartbeat()
            # 确保进程被终止
            if hasattr(self, 'process') and self.process and self.process.poll() is None:
                try:
                    self.process.terminate()
                except:
                    pass
    
    def get_stream_info(self):
        """获取直播流信息"""
        try:
            import requests
            
            # 设置请求头，模拟浏览器行为
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36',
                'Referer': f'https://live.bilibili.com/{self.room_id}',
                'Accept': 'application/json, text/plain, */*',
                'Accept-Language': 'zh-CN,zh;q=0.9',
                'Origin': 'https://live.bilibili.com'
            }
            
            print(f"开始获取房间 {self.room_id} 的信息...")
            
            # 尝试获取真实房间号
            room_init_url = f"https://api.live.bilibili.com/room/v1/Room/room_init?id={self.room_id}"
            response = requests.get(room_init_url, headers=headers, timeout=10)
            
            # 检查响应状态码
            if response.status_code != 200:
                print(f"获取房间信息失败，状态码: {response.status_code}，响应内容: {response.text[:500]}")
                return None
                
            # 添加调试信息
            print(f"房间初始化API返回: {response.text[:200]}...")
            
            # 尝试解析JSON
            try:
                data = response.json()
            except Exception as e:
                print(f"解析JSON失败: {e}, 响应内容: {response.text[:100]}...")
                return None
            
            if data.get('code') == 0:
                real_room_id = str(data['data']['room_id'])
                print(f"真实房间号: {real_room_id}")
            else:
                real_room_id = self.room_id
                print(f"获取真实房间号失败，使用输入的房间号: {real_room_id}，返回码: {data.get('code')}，消息: {data.get('message')}")
            
            # 等待一小段时间，减轻API压力
            time.sleep(1)
            
            # 获取房间信息
            room_url = f"https://api.live.bilibili.com/xlive/web-room/v2/index/getRoomPlayInfo?room_id={real_room_id}&protocol=0,1&format=0,1,2&codec=0,1&qn=10000&platform=web&ptype=8"
            room_info_url = f"https://api.live.bilibili.com/room/v1/Room/get_info?room_id={real_room_id}"
            
            # 获取播放信息
            try:
                response = requests.get(room_url, headers=headers, timeout=10)
                if response.status_code != 200:
                    print(f"获取播放信息失败，状态码: {response.status_code}")
                    return None
                    
                print(f"播放信息API返回: {response.text[:200]}...")
                play_data = response.json()
            except Exception as e:
                print(f"获取或解析播放信息出错: {e}")
                return None
            
            # 等待一小段时间，减轻API压力
            time.sleep(1)
            
            # 获取房间基本信息
            try:
                response = requests.get(room_info_url, headers=headers, timeout=10)
                if response.status_code != 200:
                    print(f"获取房间基本信息失败，状态码: {response.status_code}")
                    return None
                    
                print(f"房间信息API返回: {response.text[:200]}...")
                info_data = response.json()
            except Exception as e:
                print(f"获取或解析房间基本信息出错: {e}")
                return None
            
            if play_data.get('code') != 0 or info_data.get('code') != 0:
                print(f"获取直播信息返回错误码: play_data.code={play_data.get('code')}, info_data.code={info_data.get('code')}")
                print(f"错误信息: play_data={play_data.get('message')}, info_data={info_data.get('message')}")
                return None
            
            # 提取主播名
            streamer_name = ""
            if info_data.get('data') and 'uname' in info_data['data']:
                streamer_name = info_data['data']['uname']
                print(f"成功获取主播名: {streamer_name}")
            else:
                # 尝试从另一个API获取主播信息
                try:
                    anchor_info_url = f"https://api.live.bilibili.com/live_user/v1/UserInfo/get_anchor_in_room?roomid={real_room_id}"
                    response = requests.get(anchor_info_url, headers=headers, timeout=10)
                    if response.status_code == 200:
                        anchor_data = response.json()
                        if anchor_data.get('code') == 0 and anchor_data.get('data') and 'info' in anchor_data['data']:
                            streamer_name = anchor_data['data']['info'].get('uname', '')
                            print(f"从anchor_info API获取到主播名: {streamer_name}")
                except Exception as e:
                    print(f"获取主播信息出错: {e}")
            
            if not streamer_name:
                print(f"警告：未能获取到主播名，详细信息: {json.dumps(info_data.get('data', {}), ensure_ascii=False)[:500]}...")
            
            # 检查是否在直播
            live_status = info_data['data'].get('live_status')
            if live_status != 1:
                print(f"房间未在直播，状态: {live_status}")
                return {
                    'live_status': live_status,
                    'title': info_data['data'].get('title', ''),
                    'streamer_name': streamer_name,
                    'cover_url': info_data['data'].get('user_cover', '') or info_data['data'].get('keyframe', '')
                }
            
            # 提取流URL
            stream_url = None
            stream_info = play_data['data'].get('playurl_info', {}).get('playurl', {}).get('stream', [])
            
            if stream_info:
                for stream in stream_info:
                    format_info = stream.get('format', [])
                    for format_item in format_info:
                        codec_info = format_item.get('codec', [])
                        if codec_info:
                            stream_url = codec_info[0].get('url_info', [])[0].get('host', '') + \
                                         codec_info[0].get('base_url', '') + \
                                         codec_info[0].get('url_info', [])[0].get('extra', '')
                            break
                    if stream_url:
                        break
            
            return {
                'live_status': live_status,
                'stream_url': stream_url,
                'title': info_data['data'].get('title', ''),
                'streamer_name': streamer_name,
                'cover_url': info_data['data'].get('user_cover', '') or info_data['data'].get('keyframe', '')
            }
            
        except Exception as e:
            print(f"获取直播流信息出错: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def start_recording(self, danmaku_path=None):
        """开始录制"""
        try:
            # 检查FFmpeg是否可用
            try:
                subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
            except:
                self.progress_updated.emit(self.room_id, 0, "FFmpeg未安装或不可用")
                raise Exception("FFmpeg未安装或不可用，请安装FFmpeg后重试")
            
            # 打印流URL用于调试
            print(f"准备录制直播流: {self.stream_url}")
            
            # 根据输出文件扩展名确定录制策略
            if self.file_path.endswith('.mp4'):
                # MP4录制方案：先录制为ts文件，再转换为mp4
                temp_ts_path = self.file_path.replace('.mp4', '.ts')
                
                # 录制TS格式的命令
                cmd = [
                    'ffmpeg', '-y',
                    '-reconnect', '1',
                    '-reconnect_streamed', '1',
                    '-reconnect_delay_max', '5',
                    '-timeout', '5000000',  # 增加超时时间
                    '-user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36',
                    '-headers', f'Referer: https://live.bilibili.com/{self.room_id}\r\n',
                    '-i', self.stream_url,
                    '-c', 'copy',
                    '-f', 'mpegts',
                    temp_ts_path
                ]
                
                # 记录临时文件路径，用于后续转换
                self.temp_ts_path = temp_ts_path
                self.is_mp4 = True
                # 设置当前文件为临时TS文件，用于实时获取文件大小
                self.current_file = temp_ts_path
            else:
                # 其他格式的命令
                cmd = [
                    'ffmpeg', '-y',
                    '-reconnect', '1',
                    '-reconnect_streamed', '1',
                    '-reconnect_delay_max', '5',
                    '-timeout', '5000000',  # 增加超时时间
                    '-user_agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36',
                    '-headers', f'Referer: https://live.bilibili.com/{self.room_id}\r\n',
                    '-i', self.stream_url,
                    '-c', 'copy'
                ]
                
                # 根据输出文件扩展名添加不同的参数
                if self.file_path.endswith('.flv'):
                    cmd.extend(['-f', 'flv'])
                elif self.file_path.endswith('.ts'):
                    cmd.extend(['-f', 'mpegts'])
                
                # 添加输出路径
                cmd.append(self.file_path)
                self.is_mp4 = False
                # 设置当前文件为输出文件，用于实时获取文件大小
                self.current_file = self.file_path
            
            # 打印完整命令用于调试(隐藏敏感信息)
            debug_cmd = cmd.copy()
            debug_cmd[debug_cmd.index(self.stream_url)] = "URL已隐藏"
            print(f"FFmpeg命令: {' '.join(debug_cmd)}")
            
            try:
                # 启动录制进程
                self.process = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding='utf-8',
                    errors='replace',
                    bufsize=1
                )
            except Exception as e:
                error_msg = f"启动FFmpeg进程失败: {str(e)}"
                print(error_msg)
                raise Exception(error_msg)
            
            # 启动弹幕录制
            if danmaku_path:
                self.start_danmaku_recording(danmaku_path)
            
            # 启动心跳检测
            self.start_heartbeat()
            
            # 监控进程输出
            while self.process.poll() is None:
                if not self.is_running:
                    self.process.terminate()
                    self.progress_updated.emit(self.room_id, 0, "录制已停止")
                    return
                
                # 读取一行输出，设置超时
                try:
                    line = self.process.stderr.readline().strip()
                    if line:
                        # 输出调试信息
                        if "error" in line.lower() or "fail" in line.lower():
                            print(f"FFmpeg警告/错误: {line}")
                        
                        # 提取时间信息
                        time_match = re.search(r'time=(\d+:\d+:\d+\.\d+)', line)
                        if time_match:
                            time_str = time_match.group(1)
                            # 更新进度
                            self.progress_updated.emit(self.room_id, 50, f"正在录制: {time_str}")
                    else:
                        # 睡眠一小段时间，避免CPU占用过高
                        time.sleep(0.1)
                except Exception as e:
                    # 读取过程中出错，记录错误
                    print(f"读取FFmpeg输出错误: {e}")
                    time.sleep(0.5)  # 出错后等待一段时间
            
            # 检查是否成功
            if self.process.returncode != 0 and self.is_running:
                error = self.process.stderr.read()
                self.progress_updated.emit(self.room_id, 0, f"录制意外停止")
                
                # 检查文件是否存在且有内容
                check_path = self.temp_ts_path if hasattr(self, 'temp_ts_path') else self.file_path
                if os.path.exists(check_path) and os.path.getsize(check_path) > 10240:  # >10KB
                    print(f"录制有错误但已保存部分内容: {check_path}")
                    # 如果是MP4格式，尝试将TS转换为MP4
                    if self.is_mp4 and hasattr(self, 'temp_ts_path'):
                        self.convert_ts_to_mp4()
                    return
                
                raise Exception(f"FFmpeg错误: {error[:500]}...")
            
            # 如果是MP4格式，将TS转换为MP4
            if self.is_mp4 and hasattr(self, 'temp_ts_path') and self.is_running:
                self.convert_ts_to_mp4()
            
        except Exception as e:
            if self.is_running:  # 如果不是人为停止
                import traceback
                traceback.print_exc()
                self.progress_updated.emit(self.room_id, 0, str(e))
                raise
    
    def convert_ts_to_mp4(self):
        """将TS文件转换为MP4"""
        try:
            self.progress_updated.emit(self.room_id, 75, "正在转换为MP4格式...")
            
            # MP4转换命令
            cmd = [
                'ffmpeg', '-y',
                '-i', self.temp_ts_path,
                '-c', 'copy',
                '-movflags', '+faststart',
                self.file_path
            ]
            
            print(f"开始将TS转换为MP4: {' '.join(cmd)}")
            
            # 执行转换
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            stdout, stderr = process.communicate()
            
            if process.returncode == 0 and os.path.exists(self.file_path) and os.path.getsize(self.file_path) > 0:
                print(f"成功将TS转换为MP4: {self.file_path}")
                self.progress_updated.emit(self.room_id, 90, "MP4转换完成")
                
                # 更新当前文件路径为MP4文件
                self.current_file = self.file_path
                
                # 删除临时TS文件
                try:
                    os.remove(self.temp_ts_path)
                except Exception as e:
                    print(f"删除临时TS文件失败: {e}")
            else:
                print(f"TS转MP4转换失败: {stderr}")
                # 保留TS文件作为备份
                self.file_path = self.temp_ts_path
                self.current_file = self.temp_ts_path
                
        except Exception as e:
            print(f"TS转MP4出错: {e}")
            import traceback
            traceback.print_exc()
            # 保留TS文件作为备份
            self.file_path = self.temp_ts_path
            self.current_file = self.temp_ts_path
    
    def start_danmaku_recording(self, danmaku_path):
        """开始录制弹幕"""
        try:
            # 检查是否已安装弹幕录制工具
            # 这里可以使用第三方库如bilibiliLiveRecorder或自己实现弹幕录制
            # 简化版，仅作为占位符
            print(f"开始录制弹幕: {danmaku_path}")
            
            # 实现弹幕录制逻辑
            # ...
            
        except Exception as e:
            print(f"弹幕录制出错: {e}")
    
    def start_heartbeat(self):
        """开始心跳检测"""
        self.check_stream_status()
        
    def check_stream_status(self):
        """检查直播流状态"""
        if not self.is_running:
            return
            
        try:
            stream_info = self.get_stream_info()
            
            if stream_info and stream_info.get('live_status') != 1:
                print("直播已结束")
                if self.process and self.process.poll() is None:
                    self.process.terminate()
                return
            
            # 更新流信息
            self.stream_info_updated.emit(self.room_id, stream_info or {})
            
            # 设置下一次检查
            self.heartbeat_timer = Timer(self.check_interval, self.check_stream_status)
            self.heartbeat_timer.daemon = True
            self.heartbeat_timer.start()
            
        except Exception as e:
            print(f"检查直播状态出错: {e}")
            # 设置下一次检查
            self.heartbeat_timer = Timer(self.check_interval, self.check_stream_status)
            self.heartbeat_timer.daemon = True
            self.heartbeat_timer.start()
    
    def stop_heartbeat(self):
        """停止心跳检测"""
        if self.heartbeat_timer:
            self.heartbeat_timer.cancel()
            self.heartbeat_timer = None
    
    def stop(self):
        """安全停止录制"""
        if not self.is_running:
            print(f"房间 {self.room_id} 的录制已经在停止中")
            return
            
        print(f"正在停止房间 {self.room_id} 的录制...")
        self.is_running = False
        self.stop_heartbeat()
        
        # 保存当前录制文件路径
        current_file = None
        if hasattr(self, 'file_path'):
            current_file = self.file_path
            if hasattr(self, 'temp_ts_path') and hasattr(self, 'is_mp4') and self.is_mp4:
                current_file = self.temp_ts_path
        
        # 优雅地停止FFmpeg进程
        if self.process and self.process.poll() is None:
            try:
                print(f"正在终止FFmpeg进程...")
                self.process.terminate()
                
                # 给FFmpeg更多时间来正确关闭文件
                for i in range(3):
                    if self.process.poll() is not None:
                        break
                    time.sleep(1)
                    
                if self.process.poll() is None:
                    self.process.kill()
                    self.process.wait(timeout=2)
            except Exception as e:
                print(f"停止FFmpeg进程时出错: {e}")
        
        # 等待一段时间确保文件系统更新
        time.sleep(1.5)
        
        # 再次检查文件状态
        file_exists = False
        file_size = 0
        
        if current_file and os.path.exists(current_file):
            file_exists = True
            file_size = os.path.getsize(current_file)
            print(f"找到录制文件: {current_file}, 大小: {file_size/1024:.2f} KB")
        
        # 重要: 不论文件大小如何，只要文件存在就认为成功
        if file_exists:
            # 如果是MP4格式，尝试将TS转换为MP4
            if hasattr(self, 'is_mp4') and self.is_mp4 and hasattr(self, 'temp_ts_path'):
                try:
                    self.convert_ts_to_mp4()
                except Exception as e:
                    print(f"转换MP4时出错: {e}")
            
            # 计算录制时长
            if hasattr(self, 'start_time'):
                duration = time.time() - self.start_time
                duration_str = str(datetime.timedelta(seconds=int(duration)))
                message = f"录制已保存，时长: {duration_str}"
            else:
                message = "录制已保存"
            
            # 确保file_path是最终的MP4文件路径（如果转换成功）
            if hasattr(self, 'is_mp4') and self.is_mp4 and os.path.exists(self.file_path):
                current_file = self.file_path
                
            # 只有在尚未发送信号的情况下才发送
            if not self.signal_sent:
                self.signal_sent = True
                # 发送成功信号时使用最新的文件路径    
                self.record_complete.emit(self.room_id, True, message, current_file)  
        else:
            if not self.signal_sent:
                self.signal_sent = True
                print(f"无法找到有效的录制文件")
                self.record_complete.emit(self.room_id, False, "录制失败或文件为空", "")
        
        # 等待线程结束
        self.wait(1000)  # 等待最多1秒
        # 如果线程仍在运行，使用更强制的方式
        if self.isRunning():
            print(f"警告：房间 {self.room_id} 的录制线程未能在1秒内停止，尝试强制停止")
            self.terminate()  # 强制终止线程
            self.wait(500)    # 再等待一小段时间
    def __del__(self):
        """在对象被销毁前确保线程安全停止"""
        try:
            self.is_running = False
            if self.isRunning():
                self.wait(3000)  # 等待最多3秒
        except:
            pass  # 忽略可能的异常，防止程序关闭时出错

class ReplayDownloadThread(QThread):
    """B站直播回放下载线程"""
    progress_updated = pyqtSignal(int, str)  # 进度, 状态消息
    download_complete = pyqtSignal(bool, str, str)  # 成功状态, 消息, 文件路径
    
    def __init__(self, url, output_dir, quality="best"):
        super().__init__()
        
        self.url = url
        self.output_dir = output_dir
        self.quality = quality
        self.is_running = True
        self.file_path = ""
        
    def run(self):
        try:
            # 确保输出目录存在
            os.makedirs(self.output_dir, exist_ok=True)
            
            # 检查you-get是否可用
            try:
                subprocess.run(['you-get', '--version'], capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
            except:
                self.progress_updated.emit(0, "you-get未安装或不可用")
                self.download_complete.emit(False, "you-get未安装或不可用，请安装you-get后重试", "")
                return
            
            # 解析URL信息
            self.progress_updated.emit(5, "解析回放信息...")
            info = self.get_video_info()
            
            if not info:
                self.progress_updated.emit(0, "获取回放信息失败")
                self.download_complete.emit(False, "获取回放信息失败，请检查URL是否正确", "")
                return
            
            # 提取文件名
            title = info.get('title', 'B站直播回放')
            safe_title = re.sub(r'[\\/*?:"<>|]', "_", title)
            timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            filename = f"{safe_title}_{timestamp}.mp4"
            self.file_path = os.path.join(self.output_dir, filename)
            
            # 开始下载
            self.progress_updated.emit(10, "开始下载回放...")
            self.download_replay()
            
            # 检查文件是否存在且大小大于0
            if os.path.exists(self.file_path) and os.path.getsize(self.file_path) > 0:
                self.progress_updated.emit(100, "下载完成")
                self.download_complete.emit(True, "下载完成", self.file_path)
            else:
                self.progress_updated.emit(0, "下载失败或文件为空")
                self.download_complete.emit(False, "下载失败或文件为空", "")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            self.progress_updated.emit(0, f"下载出错")
            self.download_complete.emit(False, str(e), "")
    
    def get_video_info(self):
        """获取视频信息"""
        try:
            # 增加超时防止卡死
            cmd = ['you-get', '-i', '--timeout', '15', self.url]
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace'
            )
            
            stdout, stderr = process.communicate()
            
            if process.returncode != 0:
                print(f"获取视频信息失败: {stderr}")
                return None
            
            # 解析输出
            info = {}
            title_match = re.search(r'Title:\s+(.+)', stdout)
            if title_match:
                info['title'] = title_match.group(1).strip()
            
            # 提取可用格式
            formats = []
            format_section = False
            for line in stdout.split('\n'):
                if 'format:' in line.lower():
                    format_section = True
                    continue
                    
                if format_section and '-->' in line:
                    format_info = line.split('-->')
                    if len(format_info) >= 2:
                        format_id = format_info[0].strip()
                        format_desc = format_info[1].strip()
                        formats.append({'id': format_id, 'description': format_desc})
            
            info['formats'] = formats
            return info
            
        except Exception as e:
            print(f"获取视频信息出错: {e}")
            import traceback
            traceback.print_exc()
            return None
    
    def download_replay(self):
        """下载回放视频"""
        try:
            cmd = ['you-get', '-o', self.output_dir, '-O', os.path.splitext(os.path.basename(self.file_path))[0]]
            
            # 如果指定了质量
            if self.quality and self.quality != "best":
                cmd.extend(['--format', self.quality])
                
            cmd.append(self.url)
            
            process = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding='utf-8',
                errors='replace',
                bufsize=1,  # 行缓冲
                universal_newlines=True
            )
            
            # 监控下载进度
            for line in iter(process.stdout.readline, ''):
                if not self.is_running:
                    process.terminate()
                    return
                
                print(line.strip())
                
                # 尝试提取进度信息
                progress_match = re.search(r'(\d+)%', line)
                if progress_match:
                    progress = int(progress_match.group(1))
                    self.progress_updated.emit(progress, f"下载中... {progress}%")
                
                # 尝试提取文件信息
                file_match = re.search(r'Saving to: (.+)', line)
                if file_match:
                    path = file_match.group(1).strip('"\'')
                    self.file_path = os.path.abspath(path)
                    print(f"保存到: {self.file_path}")
            
            # 读取任何错误
            stderr = process.stderr.read()
            if stderr:
                print(f"下载过程中的错误: {stderr}")
            
            # 等待进程结束
            process.wait()
            
            if process.returncode != 0 and self.is_running:
                raise Exception(f"下载失败，返回码: {process.returncode}")
            
        except Exception as e:
            if self.is_running:  # 如果不是人为停止
                raise Exception(f"下载回放出错: {e}")
    
    def stop(self):
        """停止下载"""
        self.is_running = False
        
        # 等待线程结束
        self.wait(1000)  # 等待最多1秒


class BilibiliLiveRecorderPlugin(PluginBase):
    """B站直播录制插件 - 录制直播和下载回放"""
    
    def __init__(self, app_instance=None):
        super().__init__(app_instance)
        # 添加插件元数据
        self.name = "B站直播录制"
        self.version = "1.0.0"
        self.description = "录制B站直播和下载回放视频，支持自定义设置，界面美观，使用简单"
        self.author = "YT下载器团队"
        self.config = self.load_config()
        self.recording_threads = {}  # 记录录制线程
        self.room_status = {}  # 直播间状态
        self.status_timer = None
        self._threads = {}  # 用于管理所有临时线程
        self.app = app_instance
        self._button_added = False  # 添加标记，防止重复添加按钮
        self._is_enabled = True  # 默认认为插件是启用的，具体检查在on_startup中进行
        
        print("B站直播录制插件实例已创建，将在应用启动时初始化")
        
        # 不在__init__中调用initialize，而是等待on_startup被调用
        # if app_instance:
        #     self.initialize()
    
    def initialize(self):
        """初始化插件"""
        print("B站直播录制插件开始初始化")
        
        # 检查插件是否被禁用
        if hasattr(self.app, 'plugin_manager'):
            try:
                plugin_id = None
                if hasattr(self.app.plugin_manager, 'get_plugin_id'):
                    plugin_id = self.app.plugin_manager.get_plugin_id(self)
                    print(f"初始化时获取到插件ID: {plugin_id}")
                
                if plugin_id and hasattr(self.app.plugin_manager, 'enabled_plugins'):
                    is_enabled = plugin_id in self.app.plugin_manager.enabled_plugins and self.app.plugin_manager.enabled_plugins[plugin_id]
                    print(f"初始化时插件启用状态: {is_enabled}")
                    
                    if not is_enabled:
                        print("初始化检查：B站直播录制插件已被禁用，不添加按钮")
                        self._is_enabled = False
                        return False
                    else:
                        # 明确设置为启用状态
                        self._is_enabled = True
                        print("初始化检查：B站直播录制插件已启用，将添加按钮")
            except Exception as e:
                print(f"初始化时检查插件状态出错: {e}")
                import traceback
                traceback.print_exc()
        
        # 使用定时器延迟添加按钮，以确保主窗口已完全加载
        QTimer.singleShot(1000, self.add_live_recorder_action)
        print("已设置延迟1秒后添加B站直播录制按钮")
        
        return True
    
    def load_config(self):
        """加载配置"""
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        default_config = {
            "output_dir": os.path.join(os.path.expanduser("~"), "Downloads", "BilibiliLive"),
            "quality": "best",
            "format": "flv",
            "record_danmaku": True,
            "auto_record_rooms": [],
            "check_interval": 60,  # 秒
            "auto_convert": False,
            "history": []  # 历史记录
        }
        
        print(f"尝试从 {config_path} 加载配置文件...")
        if os.path.exists(config_path):
            try:
                with open(config_path, 'r', encoding='utf-8') as f:
                    saved_config = json.load(f)
                    print(f"成功加载配置: {saved_config}")
                    # 更新默认配置
                    default_config.update(saved_config)
            except Exception as e:
                print(f"加载配置文件失败: {e}")
        else:
            print(f"配置文件不存在，使用默认配置")
        
        # 确保输出目录存在
        os.makedirs(default_config["output_dir"], exist_ok=True)
        
        return default_config
    
    def save_config(self):
        """保存配置"""
        config_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")
        try:
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            print("配置已保存")
        except Exception as e:
            print(f"保存配置失败: {e}")
    
    def add_live_recorder_action(self):
        """添加直播录制按钮到主界面"""
        try:
            print("开始执行add_live_recorder_action方法")
            
            # 再次检查插件是否被禁用
            if hasattr(self, '_is_enabled') and not self._is_enabled:
                print(f"检测到self._is_enabled={self._is_enabled}，不添加按钮")
                return
                
            if hasattr(self.app, 'plugin_manager'):
                try:
                    plugin_id = self.app.plugin_manager.get_plugin_id(self)
                    print(f"当前插件ID: {plugin_id}")
                    print(f"已启用的插件列表: {list(self.app.plugin_manager.enabled_plugins.keys())}")
                    
                    is_enabled = plugin_id in self.app.plugin_manager.enabled_plugins and self.app.plugin_manager.enabled_plugins[plugin_id]
                    print(f"插件启用状态检查结果: {is_enabled}")
                    
                    if not is_enabled:
                        print("插件管理器显示插件已被禁用，不添加按钮")
                        return
                except Exception as e:
                    print(f"检查插件状态时出错: {e}")
                    import traceback
                    traceback.print_exc()
            
            # 防止重复初始化按钮
            if self._button_added:
                print("B站直播录制按钮已经添加过，跳过")
                return
                
            # 先清理可能存在的重复按钮
            self._remove_existing_buttons()
            
            # 检查自己的实例是否已添加按钮
            if hasattr(self, 'live_recorder_button') and self.live_recorder_button:
                # 如果按钮已存在但没有父对象（已被移除），则重新添加
                if self.live_recorder_button.parent() is None:
                    self._add_button_to_layout()
                return
                
            # 创建直播录制按钮
            self.live_recorder_button = QPushButton("B站直播录制")
            
            # 设置唯一对象名
            button_id = f"bilibili_live_recorder_button_{id(self)}"
            self.live_recorder_button.setObjectName(button_id)
            
            # 添加图标
            icon_found = False
            
            # 1. 尝试在插件目录找图标
            icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bilibili_icon.png")
            if os.path.exists(icon_path):
                print(f"找到图标文件: {icon_path}")
                self.live_recorder_button.setIcon(QIcon(icon_path))
                self.live_recorder_button.setIconSize(QSize(20, 20))
                icon_found = True
            else:
                print(f"图标文件不存在: {icon_path}")
            
            # 2. 尝试在应用资源目录找图标
            if not icon_found and hasattr(self.app, "resource_dir"):
                app_icon_path = os.path.join(self.app.resource_dir, "icons", "bilibili.png")
                if os.path.exists(app_icon_path):
                    print(f"找到应用资源图标: {app_icon_path}")
                    self.live_recorder_button.setIcon(QIcon(app_icon_path))
                    self.live_recorder_button.setIconSize(QSize(20, 20))
                    icon_found = True
                else:
                    print(f"应用资源图标不存在: {app_icon_path}")
            
            # 修改按钮样式
            self.live_recorder_button.setStyleSheet("""
                QPushButton {
                    background-color: #FB7299;  /* B站粉色 */
                    color: white;
                    border: none;
                    border-radius: 5px;
                    padding: 5px 10px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #FC8BAA;
                    border: 1px solid #FB7299;
                }
                QPushButton:pressed {
                    background-color: #E45F86;
                }
            """)
            self.live_recorder_button.setCursor(Qt.PointingHandCursor)
            
            # 设置固定宽度
            self.live_recorder_button.setFixedWidth(120)
            
            # 连接点击事件
            self.live_recorder_button.clicked.connect(self.show_recorder_dialog)
            
            # 尝试添加到界面
            added = False
            
            # 检查app实例是否有效
            if not self.app:
                print("错误: app实例为空，无法添加按钮")
                return
                
            print(f"开始尝试添加按钮到界面，app类型: {type(self.app)}")
            
            # 1. 尝试找到其他平台按钮并在其旁边添加
            for btn_name in ["A站下载", "快手下载", "TikTok下载"]:
                if not added:
                    print(f"尝试查找按钮: {btn_name}")
                    found_buttons = []
                    for widget in self.app.findChildren(QPushButton):
                        if hasattr(widget, 'text') and widget.text() == btn_name:
                            found_buttons.append(widget)
                    
                    print(f"找到 {len(found_buttons)} 个 {btn_name} 按钮")
                    
                    for widget in found_buttons:
                        parent = widget.parent()
                        if parent and parent.layout():
                            layout = parent.layout()
                            print(f"找到按钮 {btn_name} 的父布局: {type(layout)}")
                            # 遍历布局查找按钮位置
                            for i in range(layout.count()):
                                item = layout.itemAt(i)
                                if item and item.widget() == widget:
                                    # 找到按钮后，在其后面插入B站直播录制按钮
                                    layout.insertWidget(i+1, self.live_recorder_button)
                                    print(f"已添加B站直播录制按钮到{btn_name}按钮旁边")
                                    added = True
                                    break
                        if added:
                            break
            
            # 2. 如果没有找到其他按钮，尝试找字幕按钮
            if not added:
                print("尝试查找字幕按钮")
                if hasattr(self.app, 'subtitle_btn'):
                    print("找到字幕按钮属性")
                    if hasattr(self.app, 'history_layout'):
                        print("找到history_layout属性")
                        # 找出字幕按钮在布局中的位置
                        for i in range(self.app.history_layout.count()):
                            item = self.app.history_layout.itemAt(i)
                            if item and item.widget() == self.app.subtitle_btn:
                                # 找到字幕按钮后，在其后面插入B站直播录制按钮
                                self.app.history_layout.insertWidget(i+1, self.live_recorder_button)
                                print("已添加B站直播录制按钮到字幕按钮旁边")
                                added = True
                                break
                    else:
                        print("未找到history_layout属性")
                else:
                    print("未找到字幕按钮属性")
            
            # 3. 如果没有找到合适的位置，则添加到默认位置
            if not added:
                print("尝试添加到默认布局")
                if hasattr(self.app, 'history_layout'):
                    print("找到history_layout，尝试添加按钮")
                    self.app.history_layout.addWidget(self.live_recorder_button)
                    print("已添加B站直播录制按钮到history_layout")
                    added = True
                elif hasattr(self.app, 'toolbar_layout'):
                    print("找到toolbar_layout，尝试添加按钮")
                    self.app.toolbar_layout.addWidget(self.live_recorder_button)
                    print("已添加B站直播录制按钮到toolbar_layout")
                    added = True
                else:
                    print("无法找到合适的布局添加B站直播录制按钮")
                    # 尝试直接查找主窗口中的布局
                    print("尝试查找主窗口中的布局")
                    layouts = []
                    for obj in self.app.children():
                        if isinstance(obj, QLayout):
                            layouts.append(obj)
                    print(f"找到 {len(layouts)} 个顶级布局")
                    
                    # 尝试查找所有QHBoxLayout
                    h_layouts = []
                    for widget in self.app.findChildren(QWidget):
                        if hasattr(widget, 'layout') and isinstance(widget.layout(), QHBoxLayout):
                            h_layouts.append(widget.layout())
                    print(f"找到 {len(h_layouts)} 个水平布局")
                    
                    if h_layouts:
                        # 尝试添加到第一个水平布局
                        h_layouts[0].addWidget(self.live_recorder_button)
                        print("已添加B站直播录制按钮到找到的第一个水平布局")
                        added = True
                    
                    # 如果还是没有添加成功，尝试直接添加到主窗口
                    if not added:
                        print("尝试直接添加按钮到主窗口")
                        # 查找主窗口
                        main_windows = [w for w in self.app.topLevelWidgets() if isinstance(w, QMainWindow)]
                        if main_windows:
                            main_window = main_windows[0]
                            print(f"找到主窗口: {main_window}")
                            
                            # 查找工具栏
                            toolbars = main_window.findChildren(QToolBar)
                            if toolbars:
                                print(f"找到工具栏，数量: {len(toolbars)}")
                                toolbars[0].addWidget(self.live_recorder_button)
                                print("已添加B站直播录制按钮到工具栏")
                                added = True
                            else:
                                # 尝试添加到中央部件
                                central_widget = main_window.centralWidget()
                                if central_widget and hasattr(central_widget, 'layout') and central_widget.layout():
                                    print("找到中央部件，尝试添加按钮")
                                    central_widget.layout().addWidget(self.live_recorder_button)
                                    print("已添加B站直播录制按钮到中央部件")
                                    added = True
                                else:
                                    print("无法找到中央部件或其布局")
                        else:
                            print("未找到主窗口")
            
            # 标记按钮已添加
            if added:
                self._button_added = True
                print("B站直播录制按钮添加成功，已设置_button_added=True")
                
                # 检查依赖
                self.check_dependencies()
            else:
                print("B站直播录制按钮添加失败，没有找到合适的布局")
                
        except Exception as e:
            print(f"添加B站直播录制按钮失败: {e}")
            import traceback
            traceback.print_exc()
    def _add_button_to_layout(self):
        """将按钮添加到适当的布局中，优先放在字幕按钮旁边"""
        if not hasattr(self, 'live_recorder_button') or not self.live_recorder_button:
            return
        
        # 确保按钮已从其它布局中移除
        if self.live_recorder_button.parent() is not None:
            parent = self.live_recorder_button.parent()
            if parent and parent.layout():
                parent.layout().removeWidget(self.live_recorder_button)
                self.live_recorder_button.setParent(None)
        
        # 以下代码类似您原有的代码，但更加健壮
        added = False
        
        # 1. 尝试找到字幕按钮
        if hasattr(self.app, 'history_layout') and hasattr(self.app, 'subtitle_btn'):
            # 找到字幕按钮的索引
            for i in range(self.app.history_layout.count()):
                item = self.app.history_layout.itemAt(i)
                if item and item.widget() == self.app.subtitle_btn:
                    # 找到字幕按钮后，在其右侧插入B站直播录制按钮
                    self.app.history_layout.insertWidget(i + 1, self.live_recorder_button)
                    print(f"已添加B站直播录制按钮到字幕按钮旁边")
                    added = True
                    break
        
        # 2. 如果没有找到合适的位置，则添加到默认位置
        if not added:
            if hasattr(self.app, 'history_layout'):
                self.app.history_layout.addWidget(self.live_recorder_button)
                print("已添加B站直播录制按钮到history_layout")
            elif hasattr(self.app, 'toolbar_layout'):
                self.app.toolbar_layout.addWidget(self.live_recorder_button)
                print("已添加B站直播录制按钮到toolbar_layout")
            else:
                print("无法找到合适的布局添加B站直播录制按钮")
                return
    def _remove_existing_buttons(self):
        """查找并移除所有已存在的B站直播录制按钮"""
        try:
            # 检查历史布局中的按钮
            if hasattr(self.app, 'history_layout'):
                layout = self.app.history_layout
                buttons_to_remove = []
                
                # 首先找出所有需要移除的按钮
                for i in range(layout.count()):
                    widget = layout.itemAt(i).widget()
                    if widget and isinstance(widget, QPushButton) and widget.text() == "B站直播录制":
                        # 如果不是当前实例的按钮
                        if not hasattr(self, 'live_recorder_button') or widget != self.live_recorder_button:
                            buttons_to_remove.append(widget)
                
                # 然后移除这些按钮
                for widget in buttons_to_remove:
                    layout.removeWidget(widget)
                    widget.setParent(None)
                    widget.deleteLater()  # 确保widget被删除
                    print(f"移除已存在的B站直播录制按钮")
            
            # 检查工具栏布局中的按钮
            if hasattr(self.app, 'toolbar_layout'):
                layout = self.app.toolbar_layout
                buttons_to_remove = []
                
                for i in range(layout.count()):
                    widget = layout.itemAt(i).widget()
                    if widget and isinstance(widget, QPushButton) and widget.text() == "B站直播录制":
                        if not hasattr(self, 'live_recorder_button') or widget != self.live_recorder_button:
                            buttons_to_remove.append(widget)
                
                for widget in buttons_to_remove:
                    layout.removeWidget(widget)
                    widget.setParent(None)
                    widget.deleteLater()
                    print(f"移除已存在的B站直播录制按钮")
        
        except Exception as e:
            print(f"移除已存在的B站直播录制按钮时出错: {e}")
    def check_dependencies(self):
        """检查必要依赖"""
        missing_deps = []
        
        # 检查FFmpeg
        try:
            subprocess.run(['ffmpeg', '-version'], capture_output=True, text=True, encoding='utf-8', errors='replace')
            ffmpeg_installed = True
        except:
            ffmpeg_installed = False
            missing_deps.append("FFmpeg")
        
        # 检查you-get (用于下载回放)
        try:
            subprocess.run(['you-get', '--version'], capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
            youget_installed = True
        except:
            youget_installed = False
            missing_deps.append("you-get")
        
        if missing_deps:
            print(f"警告: 缺少以下依赖: {', '.join(missing_deps)}")
            # 在实际使用时会提示用户安装
    
    def show_recorder_dialog(self):
        """显示直播录制对话框"""
        
        # 确保加载了最新的配置
        self.config = self.load_config()
        
        # 如果已经显示，则激活
        if hasattr(self, 'recorder_dialog') and self.recorder_dialog.isVisible():
            self.recorder_dialog.activateWindow()
            return
            
        # 创建对话框
        self.recorder_dialog = QDialog()
        self.recorder_dialog.setWindowTitle("B站直播录制")
        self.recorder_dialog.setMinimumWidth(800)
        self.recorder_dialog.setMinimumHeight(600)
        self.recorder_dialog.setWindowFlags(self.recorder_dialog.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        # 创建选项卡
        tab_widget = QTabWidget()
        
        # 创建直播选项卡
        self.live_tab = QWidget()
        self.create_live_tab()
        tab_widget.addTab(self.live_tab, "直播录制")
        
        # 创建回放选项卡
        self.replay_tab = QWidget()
        self.create_replay_tab()
        tab_widget.addTab(self.replay_tab, "回放下载")
        
        # 创建管理选项卡
        self.manage_tab = QWidget()
        self.create_manage_tab()
        tab_widget.addTab(self.manage_tab, "录制管理")
        
        # 创建设置选项卡
        self.settings_tab = QWidget()
        self.create_settings_tab()
        tab_widget.addTab(self.settings_tab, "设置")
        
        # 设置布局
        layout = QVBoxLayout()
        layout.addWidget(tab_widget)
        self.recorder_dialog.setLayout(layout)
        
        # 加载自动录制房间列表 - 确保在显示对话框前加载
        self.load_auto_rooms()
        
        # 启动状态更新定时器
        self.status_timer = QTimer()
        self.status_timer.timeout.connect(self.update_recording_status)
        self.status_timer.start(1000)  # 每秒更新一次
        
        def on_dialog_closed():
            # 停止定时器
            if self.status_timer:
                self.status_timer.stop()
                
            # 停止所有录制线程
            if hasattr(self, 'recording_threads'):
                room_ids = list(self.recording_threads.keys())
                for room_id in room_ids:
                    thread = self.recording_threads[room_id]
                    if thread.isRunning():
                        thread.stop()
                        # 给线程一些时间来停止
                        if not thread.wait(1000):  # 等待最多1秒
                            print(f"警告：房间 {room_id} 的录制线程未能在关闭对话框时正确停止")
            
            # 停止所有临时线程
            self.stop_all_temporary_threads()
        
        # 连接关闭信号
        self.recorder_dialog.finished.connect(on_dialog_closed)
        
        # 显示对话框
        self.recorder_dialog.exec_()
    
    def create_live_tab(self):
        """创建直播录制选项卡内容"""
        layout = QVBoxLayout(self.live_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # 直播信息输入区域
        form_group = QGroupBox("直播信息")
        form_layout = QFormLayout(form_group)
        form_layout.setContentsMargins(15, 20, 15, 15)
        form_layout.setSpacing(10)
        
        # 房间号输入框
        self.room_id_input = QLineEdit()
        self.room_id_input.setPlaceholderText("输入B站直播间号，如: 7734200")
        self.room_id_input.setMinimumHeight(28)
        form_layout.addRow("直播间号:", self.room_id_input)
        
        # 检查直播状态按钮
        self.check_live_btn = QPushButton("检查直播状态")
        self.check_live_btn.setStyleSheet("""
            QPushButton {
                background-color: #23ADE5;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                min-height: 20px;
                font-size: 13px;
                width: 100%;
            }
            QPushButton:hover {
                background-color: #4DBEF0;
            }
            QPushButton:pressed {
                background-color: #1E9CD0;
            }
        """)
        self.check_live_btn.setCursor(Qt.PointingHandCursor)
        self.check_live_btn.clicked.connect(self.check_live_status)
        form_layout.addRow("", self.check_live_btn)
        
        layout.addWidget(form_group)
        
        # 直播状态显示区域
        self.status_group = QGroupBox("直播状态")
        status_layout = QVBoxLayout(self.status_group)
        status_layout.setContentsMargins(15, 20, 15, 15)
        
        self.live_status_label = QLabel("请输入房间号并检查直播状态")
        self.live_status_label.setAlignment(Qt.AlignCenter)
        self.live_status_label.setWordWrap(True)
        status_layout.addWidget(self.live_status_label)
        
        self.streamer_name_label = QLabel("")
        self.streamer_name_label.setAlignment(Qt.AlignCenter)
        status_layout.addWidget(self.streamer_name_label)
        
        self.live_title_label = QLabel("")
        self.live_title_label.setAlignment(Qt.AlignCenter)
        self.live_title_label.setWordWrap(True)
        status_layout.addWidget(self.live_title_label)
        
        layout.addWidget(self.status_group)
        
        # 录制选项区域
        options_group = QGroupBox("录制选项")
        options_layout = QFormLayout(options_group)
        options_layout.setContentsMargins(15, 20, 15, 15)
        
        # 画质选择
        self.quality_combo = QComboBox()
        self.quality_combo.addItem("最高画质", "best")
        self.quality_combo.addItem("高清 (720P)", "720p")
        self.quality_combo.addItem("流畅 (480P)", "480p")
        self.quality_combo.addItem("普通 (360P)", "360p")
        
        # 从配置中加载默认画质
        current_quality = self.config.get("quality", "best")
        for i in range(self.quality_combo.count()):
            if self.quality_combo.itemData(i) == current_quality:
                self.quality_combo.setCurrentIndex(i)
                break
        
        options_layout.addRow("画质:", self.quality_combo)
        
        # 格式选择
        self.format_combo = QComboBox()
        self.format_combo.addItem("FLV格式", "flv")
        self.format_combo.addItem("MP4格式", "mp4")
        self.format_combo.addItem("TS格式", "ts")
        
        # 从配置中加载默认格式
        current_format = self.config.get("format", "flv")
        format_texts = {"flv": "FLV格式", "mp4": "MP4格式", "ts": "TS格式"}
        if current_format in format_texts:
            self.format_combo.setCurrentText(format_texts[current_format])
        
        options_layout.addRow("格式:", self.format_combo)
        
        # 弹幕录制
        self.danmaku_check = QCheckBox("录制弹幕")
        self.danmaku_check.setChecked(self.config.get("record_danmaku", True))
        options_layout.addRow("", self.danmaku_check)
        
        # 加入自动录制
        self.auto_record_check = QCheckBox("添加到自动录制列表")
        self.auto_record_check.setToolTip("启动时自动检测并录制该房间")
        options_layout.addRow("", self.auto_record_check)
        
        layout.addWidget(options_group)
        
        # 按钮区域
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)
        
        # 开始录制按钮
        self.start_record_btn = QPushButton("开始录制")
        self.start_record_btn.setStyleSheet("""
            QPushButton {
                background-color: #FB7299;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                min-height: 20px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #FC8BAA;
            }
            QPushButton:pressed {
                background-color: #E45F86;
            }
            QPushButton:disabled {
                background-color: #FFCCD5;
            }
        """)
        self.start_record_btn.setCursor(Qt.PointingHandCursor)
        self.start_record_btn.clicked.connect(self.start_recording)
        self.start_record_btn.setEnabled(False)
        buttons_layout.addWidget(self.start_record_btn)
        
        # 停止录制按钮
        self.stop_record_btn = QPushButton("停止录制")
        self.stop_record_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                min-height: 20px;
                font-size: 14px;
                font-weight: bold;
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
        self.stop_record_btn.setCursor(Qt.PointingHandCursor)
        self.stop_record_btn.clicked.connect(self.stop_recording)
        self.stop_record_btn.setEnabled(False)
        buttons_layout.addWidget(self.stop_record_btn)
        
        layout.addLayout(buttons_layout)
    
    def create_replay_tab(self):
        """创建回放下载选项卡内容"""
        layout = QVBoxLayout(self.replay_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # 检查you-get是否安装
        try:
            subprocess.run(['you-get', '--version'], capture_output=True, text=True, encoding='utf-8', errors='replace', check=True)
            self.youget_installed = True
        except:
            self.youget_installed = False
            
            # 显示警告
            warning_label = QLabel("未检测到you-get，回放下载功能不可用。请安装you-get后再使用。")
            warning_label.setStyleSheet("color: red; font-weight: bold; background-color: #FFEBEE; padding: 10px; border-radius: 5px;")
            warning_label.setWordWrap(True)
            layout.addWidget(warning_label)
            
            # 安装指导按钮
            install_btn = QPushButton("查看安装指导")
            install_btn.setStyleSheet("""
                QPushButton {
                    background-color: #2196F3;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 8px;
                    min-height: 20px;
                    font-size: 13px;
                }
                QPushButton:hover {
                    background-color: #42A5F5;
                }
                QPushButton:pressed {
                    background-color: #1976D2;
                }
            """)
            install_btn.clicked.connect(self.show_install_guide)
            layout.addWidget(install_btn)
            
            # 刷新按钮
            refresh_btn = QPushButton("刷新状态")
            refresh_btn.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    border: none;
                    border-radius: 4px;
                    padding: 8px;
                    min-height: 20px;
                    font-size: 13px;
                }
                QPushButton:hover {
                    background-color: #66BB6A;
                }
                QPushButton:pressed {
                    background-color: #388E3C;
                }
            """)
            refresh_btn.clicked.connect(self.refresh_youget_status)
            layout.addWidget(refresh_btn)
            
            # 提前返回，不创建其他内容
            return
        
        # 回放地址输入区域
        form_group = QGroupBox("回放信息")
        form_layout = QFormLayout(form_group)
        form_layout.setContentsMargins(15, 20, 15, 15)
        form_layout.setSpacing(10)
        
        # 回放URL输入框
        self.replay_url_input = QLineEdit()
        self.replay_url_input.setPlaceholderText("输入B站回放URL，例如: https://live.bilibili.com/record/R1xxx")
        self.replay_url_input.setMinimumHeight(28)
        form_layout.addRow("回放地址:", self.replay_url_input)
        
        # 检查回放按钮
        self.check_replay_btn = QPushButton("获取回放信息")
        self.check_replay_btn.setStyleSheet("""
            QPushButton {
                background-color: #23ADE5;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                min-height: 20px;
                font-size: 13px;
                width: 100%;
            }
            QPushButton:hover {
                background-color: #4DBEF0;
            }
            QPushButton:pressed {
                background-color: #1E9CD0;
            }
        """)
        self.check_replay_btn.setCursor(Qt.PointingHandCursor)
        self.check_replay_btn.clicked.connect(self.check_replay_info)
        form_layout.addRow("", self.check_replay_btn)
        
        layout.addWidget(form_group)
        
        # 回放信息显示区域
        self.replay_info_group = QGroupBox("回放信息")
        replay_info_layout = QVBoxLayout(self.replay_info_group)
        replay_info_layout.setContentsMargins(15, 20, 15, 15)
        
        self.replay_title_label = QLabel("请输入回放地址并获取信息")
        self.replay_title_label.setAlignment(Qt.AlignCenter)
        self.replay_title_label.setWordWrap(True)
        replay_info_layout.addWidget(self.replay_title_label)
        
        self.replay_format_label = QLabel("")
        self.replay_format_label.setAlignment(Qt.AlignCenter)
        replay_info_layout.addWidget(self.replay_format_label)
        
        layout.addWidget(self.replay_info_group)
        
        # 下载选项区域
        dl_options_group = QGroupBox("下载选项")
        dl_options_layout = QFormLayout(dl_options_group)
        dl_options_layout.setContentsMargins(15, 20, 15, 15)
        
        # 画质选择
        self.dl_quality_combo = QComboBox()
        self.dl_quality_combo.addItem("最高画质", "best")
        dl_options_layout.addRow("画质:", self.dl_quality_combo)
        
        layout.addWidget(dl_options_group)
        
        # 下载进度
        dl_progress_group = QGroupBox("下载进度")
        dl_progress_layout = QVBoxLayout(dl_progress_group)
        dl_progress_layout.setContentsMargins(15, 20, 15, 15)
        
        self.dl_progress_bar = QProgressBar()
        self.dl_progress_bar.setValue(0)
        self.dl_progress_bar.setMinimumHeight(20)
        dl_progress_layout.addWidget(self.dl_progress_bar)
        
        self.dl_status_label = QLabel("准备下载...")
        self.dl_status_label.setAlignment(Qt.AlignCenter)
        dl_progress_layout.addWidget(self.dl_status_label)
        
        layout.addWidget(dl_progress_group)
        
        # 按钮区域
        dl_buttons_layout = QHBoxLayout()
        dl_buttons_layout.setSpacing(10)
        
        # 开始下载按钮
        self.start_dl_btn = QPushButton("开始下载")
        self.start_dl_btn.setStyleSheet("""
            QPushButton {
                background-color: #FB7299;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                min-height: 20px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #FC8BAA;
            }
            QPushButton:pressed {
                background-color: #E45F86;
            }
            QPushButton:disabled {
                background-color: #FFCCD5;
            }
        """)
        self.start_dl_btn.setCursor(Qt.PointingHandCursor)
        self.start_dl_btn.clicked.connect(self.start_downloading)
        self.start_dl_btn.setEnabled(False)
        dl_buttons_layout.addWidget(self.start_dl_btn)
        
        # 取消下载按钮
        self.cancel_dl_btn = QPushButton("取消下载")
        self.cancel_dl_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                min-height: 20px;
                font-size: 14px;
                font-weight: bold;
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
        self.cancel_dl_btn.setCursor(Qt.PointingHandCursor)
        self.cancel_dl_btn.clicked.connect(self.cancel_downloading)
        self.cancel_dl_btn.setEnabled(False)
        dl_buttons_layout.addWidget(self.cancel_dl_btn)
        
        layout.addLayout(dl_buttons_layout)
    
    def create_manage_tab(self):
        """创建录制管理选项卡内容"""
        layout = QVBoxLayout(self.manage_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # 录制任务表格
        self.tasks_table = QTableWidget()
        self.tasks_table.setColumnCount(6)
        self.tasks_table.setHorizontalHeaderLabels(["房间号", "主播", "状态", "时长", "大小", "操作"])
        self.tasks_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.tasks_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tasks_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.tasks_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tasks_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.tasks_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.tasks_table.setAlternatingRowColors(True)
        self.tasks_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.tasks_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.tasks_table)
        
        # 按钮区域
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)
        
        # 刷新按钮
        refresh_btn = QPushButton("刷新状态")
        refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                min-height: 20px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #42A5F5;
            }
            QPushButton:pressed {
                background-color: #1976D2;
            }
        """)
        refresh_btn.clicked.connect(self.refresh_tasks)
        buttons_layout.addWidget(refresh_btn)
        
        # 停止所有按钮
        stop_all_btn = QPushButton("停止所有录制")
        stop_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                min-height: 20px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #FF5252;
            }
            QPushButton:pressed {
                background-color: #D32F2F;
            }
        """)
        stop_all_btn.clicked.connect(self.stop_all_recordings)
        buttons_layout.addWidget(stop_all_btn)
        
        # 打开保存目录
        open_dir_btn = QPushButton("打开保存目录")
        open_dir_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                min-height: 20px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #66BB6A;
            }
            QPushButton:pressed {
                background-color: #388E3C;
            }
        """)
        open_dir_btn.clicked.connect(self.open_output_dir)
        buttons_layout.addWidget(open_dir_btn)
        
        layout.addLayout(buttons_layout)
        
        # 录制历史区域
        history_group = QGroupBox("录制历史")
        history_layout = QVBoxLayout(history_group)
        history_layout.setContentsMargins(10, 20, 10, 10)
        
        # 历史记录表格
        self.history_table = QTableWidget()
        self.history_table.setColumnCount(8)  # 增加一列用于选择框
        self.history_table.setHorizontalHeaderLabels(["选择", "房间号", "主播", "标题", "时间", "时长", "大小", "操作"])
        self.history_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)  # 选择列
        self.history_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(3, QHeaderView.Stretch)
        self.history_table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(5, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(6, QHeaderView.ResizeToContents)
        self.history_table.horizontalHeader().setSectionResizeMode(7, QHeaderView.ResizeToContents)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.history_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        history_layout.addWidget(self.history_table)
        
        # 历史记录操作按钮区域
        history_buttons_layout = QHBoxLayout()
        
        # 全选按钮
        select_all_btn = QPushButton("全选")
        select_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                min-height: 20px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #42A5F5;
            }
            QPushButton:pressed {
                background-color: #1976D2;
            }
        """)
        select_all_btn.clicked.connect(self.select_all_history)
        history_buttons_layout.addWidget(select_all_btn)
        
        # 取消选择按钮
        deselect_all_btn = QPushButton("取消选择")
        deselect_all_btn.setStyleSheet("""
            QPushButton {
                background-color: #9E9E9E;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                min-height: 20px;
                font-size: 13px;
            }
            QPushButton:hover {
                background-color: #BDBDBD;
            }
            QPushButton:pressed {
                background-color: #757575;
            }
        """)
        deselect_all_btn.clicked.connect(self.deselect_all_history)
        history_buttons_layout.addWidget(deselect_all_btn)
        
        # 批量删除按钮
        delete_selected_btn = QPushButton("批量删除")
        delete_selected_btn.setStyleSheet("""
            QPushButton {
                background-color: #F44336;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px;
                min-height: 20px;
                font-size: 13px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #FF5252;
            }
            QPushButton:pressed {
                background-color: #D32F2F;
            }
        """)
        delete_selected_btn.clicked.connect(self.delete_selected_history)
        history_buttons_layout.addWidget(delete_selected_btn)
        
        history_layout.addLayout(history_buttons_layout)
        
        # 加载历史记录
        self.load_history()
        
        layout.addWidget(history_group)
    
    def create_settings_tab(self):
        """创建设置选项卡内容"""
        layout = QVBoxLayout(self.settings_tab)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(10)
        
        # 基本设置区域
        basic_group = QGroupBox("基本设置")
        basic_layout = QFormLayout(basic_group)
        basic_layout.setContentsMargins(15, 20, 15, 15)
        basic_layout.setSpacing(10)
        
        # 输出目录设置
        output_layout = QHBoxLayout()
        self.output_dir_input = QLineEdit()
        self.output_dir_input.setText(self.config.get("output_dir", ""))
        self.output_dir_input.setReadOnly(True)
        self.output_dir_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 5px;
                background-color: #F5F5F5;
            }
        """)
        output_layout.addWidget(self.output_dir_input)
        
        browse_btn = QPushButton("浏览...")
        browse_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 5px 10px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #42A5F5;
            }
            QPushButton:pressed {
                background-color: #1976D2;
            }
        """)
        browse_btn.clicked.connect(self.browse_output_dir)
        output_layout.addWidget(browse_btn)
        
        basic_layout.addRow("保存目录:", output_layout)
        
        # 默认画质设置
        self.default_quality_combo = QComboBox()
        self.default_quality_combo.addItem("最高画质", "best")
        self.default_quality_combo.addItem("高清 (720P)", "720p")
        self.default_quality_combo.addItem("流畅 (480P)", "480p")
        self.default_quality_combo.addItem("普通 (360P)", "360p")
        self.default_quality_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 5px;
                min-height: 25px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                border-left: 1px solid #CCCCCC;
            }
        """)
        
        # 从配置中加载默认画质
        current_quality = self.config.get("quality", "best")
        for i in range(self.default_quality_combo.count()):
            if self.default_quality_combo.itemData(i) == current_quality:
                self.default_quality_combo.setCurrentIndex(i)
                break
                
        basic_layout.addRow("默认画质:", self.default_quality_combo)
        
        # 默认格式设置
        self.default_format_combo = QComboBox()
        self.default_format_combo.addItem("FLV格式", "flv")
        self.default_format_combo.addItem("MP4格式", "mp4")
        self.default_format_combo.addItem("TS格式", "ts")
        self.default_format_combo.setStyleSheet("""
            QComboBox {
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 5px;
                min-height: 25px;
            }
            QComboBox::drop-down {
                subcontrol-origin: padding;
                subcontrol-position: top right;
                width: 20px;
                border-left: 1px solid #CCCCCC;
            }
        """)
        
        # 从配置中加载默认格式
        current_format = self.config.get("format", "flv")
        format_texts = {"flv": "FLV格式", "mp4": "MP4格式", "ts": "TS格式"}
        if current_format in format_texts:
            self.default_format_combo.setCurrentText(format_texts[current_format])
            
        basic_layout.addRow("默认格式:", self.default_format_combo)
        
        # 录制弹幕设置
        self.default_danmaku_check = QCheckBox()
        self.default_danmaku_check.setChecked(self.config.get("record_danmaku", True))
        self.default_danmaku_check.setStyleSheet("""
            QCheckBox {
                spacing: 5px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
        """)
        basic_layout.addRow("默认录制弹幕:", self.default_danmaku_check)
        
        # 自动转换视频
        self.auto_convert_check = QCheckBox()
        self.auto_convert_check.setChecked(self.config.get("auto_convert", False))
        self.auto_convert_check.setToolTip("录制结束后自动转换为MP4格式")
        self.auto_convert_check.setStyleSheet("""
            QCheckBox {
                spacing: 5px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
        """)
        basic_layout.addRow("自动转换MP4:", self.auto_convert_check)
        
        # 检查间隔
        self.check_interval_spin = QSpinBox()
        self.check_interval_spin.setRange(30, 600)
        self.check_interval_spin.setValue(self.config.get("check_interval", 60))
        self.check_interval_spin.setSuffix(" 秒")
        self.check_interval_spin.setStyleSheet("""
            QSpinBox {
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 5px;
                min-height: 25px;
            }
            QSpinBox::up-button, QSpinBox::down-button {
                width: 20px;
            }
        """)
        basic_layout.addRow("检查直播间隔:", self.check_interval_spin)
        
        layout.addWidget(basic_group)
        
        # 自动录制设置区域 - 美化部分
        auto_group = QGroupBox("自动录制设置")
        auto_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #CCCCCC;
                border-radius: 6px;
                margin-top: 12px;
                background-color: #FAFAFA;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 5px;
            }
        """)
        auto_layout = QVBoxLayout(auto_group)
        auto_layout.setContentsMargins(15, 20, 15, 15)
        auto_layout.setSpacing(10)
        
        # 自动录制房间列表
        self.auto_rooms_table = QTableWidget()
        self.auto_rooms_table.setColumnCount(3)
        self.auto_rooms_table.setHorizontalHeaderLabels(["房间号", "主播名", "操作"])
        # 修改列宽比例，让操作列更宽
        self.auto_rooms_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.auto_rooms_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        # 将操作列设置为固定宽度而不是自适应内容
        self.auto_rooms_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Fixed)
        self.auto_rooms_table.setColumnWidth(2, 150)  # 设置操作列宽度为120像素
        self.auto_rooms_table.setAlternatingRowColors(True)
        
        # 确保表格有足够高度和行数
        self.auto_rooms_table.setMinimumHeight(200)
        self.auto_rooms_table.setRowCount(1)  # 至少显示一行
        
        # 设置固定行高
        self.auto_rooms_table.verticalHeader().setDefaultSectionSize(40)
        self.auto_rooms_table.verticalHeader().setVisible(False)  # 隐藏行号
        
        # 设置选择行为和编辑触发
        self.auto_rooms_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.auto_rooms_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        
        # 设置表格样式
        self.auto_rooms_table.setStyleSheet("""
            QTableWidget {
                border: 1px solid #DDDDDD;
                border-radius: 4px;
                background-color: #FFFFFF;
                gridline-color: #EEEEEE;
                selection-background-color: #E3F2FD;
            }
            QTableWidget::item {
                padding: 6px;
                border-bottom: 1px solid #EEEEEE;
            }
            QTableWidget::item:selected {
                background-color: #E3F2FD;
                color: #000000;
            }
            QHeaderView::section {
                background-color: #F5F5F5;
                border: 0px;
                padding: 8px;
                font-weight: bold;
                border-bottom: 1px solid #DDDDDD;
            }
            QTableWidget::item:alternate {
                background-color: #F9F9F9;
            }
        """)
        
        auto_layout.addWidget(self.auto_rooms_table)
        
        # 添加自动录制房间 - 美化部分
        add_layout = QHBoxLayout()
        self.new_auto_room_input = QLineEdit()
        self.new_auto_room_input.setPlaceholderText("输入房间号")
        self.new_auto_room_input.setStyleSheet("""
            QLineEdit {
                border: 1px solid #CCCCCC;
                border-radius: 4px;
                padding: 8px;
                background-color: #FFFFFF;
                min-height: 20px;
            }
            QLineEdit:focus {
                border: 1px solid #2196F3;
            }
            QLineEdit::placeholder {
                color: #AAAAAA;
            }
        """)
        add_layout.addWidget(self.new_auto_room_input)
        
        add_btn = QPushButton("添加")
        add_btn.setStyleSheet("""
            QPushButton {
                background-color: #FB7299;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 15px;
                font-size: 13px;
                font-weight: bold;
                min-height: 20px;
            }
            QPushButton:hover {
                background-color: #FC8BAA;
            }
            QPushButton:pressed {
                background-color: #E45F86;
            }
        """)
        add_btn.setCursor(Qt.PointingHandCursor)
        add_btn.clicked.connect(self.add_auto_room)
        add_layout.addWidget(add_btn)
        
        auto_layout.addLayout(add_layout)
        
        # 添加说明文本
        help_text = QLabel("添加房间后点击'录制'按钮开始录制直播")
        help_text.setStyleSheet("""
            QLabel {
                color: #666666;
                font-style: italic;
                padding: 5px;
            }
        """)
        help_text.setAlignment(Qt.AlignCenter)
        auto_layout.addWidget(help_text)
        self.help_text = help_text 
        
        layout.addWidget(auto_group)
        
        # 按钮区域 - 美化部分
        settings_buttons_layout = QHBoxLayout()
        settings_buttons_layout.setSpacing(15)
        
        save_settings_btn = QPushButton("保存设置")
        save_settings_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 20px;
                min-height: 15px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #66BB6A;
            }
            QPushButton:pressed {
                background-color: #388E3C;
            }
        """)
        save_settings_btn.setCursor(Qt.PointingHandCursor)
        save_settings_btn.clicked.connect(self.save_settings)
        settings_buttons_layout.addWidget(save_settings_btn)
        
        # 重置按钮
        reset_settings_btn = QPushButton("重置设置")
        reset_settings_btn.setStyleSheet("""
            QPushButton {
                background-color: #9E9E9E;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 10px 15px;
                min-height: 20px;
                font-size: 14px;
            }
            QPushButton:hover {
                background-color: #BDBDBD;
            }
            QPushButton:pressed {
                background-color: #757575;
            }
        """)
        reset_settings_btn.setCursor(Qt.PointingHandCursor)
        reset_settings_btn.clicked.connect(self.reset_settings)
        settings_buttons_layout.addWidget(reset_settings_btn)
        
        layout.addLayout(settings_buttons_layout)
    
    def check_live_status(self):
        """检查直播状态"""
        room_id = self.room_id_input.text().strip()
        if not room_id:
            QMessageBox.warning(self.recorder_dialog, "输入错误", "请输入有效的B站直播间号")
            return
            
        # 验证输入是否为数字
        if not room_id.isdigit():
            QMessageBox.warning(self.recorder_dialog, "输入错误", "直播间号必须是数字")
            return
        
        # 显示加载状态
        self.live_status_label.setText("正在检查直播状态...")
        self.live_status_label.setStyleSheet("color: #2196F3;")
        self.streamer_name_label.setText("")
        self.live_title_label.setText("")
        
        # 创建线程进行API请求
        class CheckLiveThread(QThread):
            check_complete = pyqtSignal(dict)
            
            def __init__(self, room_id):
                super().__init__()
                self.room_id = room_id
                self._stop_flag = False
                self.max_retries = 2  # 最大重试次数
                
            def stop(self):
                self._stop_flag = True
                
            def should_stop(self):
                return self._stop_flag
            def __del__(self):
                """在对象被销毁前确保线程安全停止"""
                try:
                    self._stop_flag = True
                    if self.isRunning():
                        self.wait(3000)  # 等待最多3秒
                except:
                    pass  # 忽略可能的异常，防止程序关闭时出错    
            def run(self):
                if self.should_stop():
                    return
                    
                for retry in range(self.max_retries + 1):
                    try:
                        if self.should_stop():
                            return
                        
                        thread = LiveRecordingThread(self.room_id, "", "best")
                        info = thread.get_stream_info()
                        
                        if info is not None:
                            if not self.should_stop():
                                self.check_complete.emit(info)
                            return
                        elif retry < self.max_retries:
                            print(f"获取房间 {self.room_id} 信息失败，尝试重试 {retry + 1}/{self.max_retries}")
                            time.sleep(2)  # 重试前等待2秒
                        else:
                            print(f"获取房间 {self.room_id} 信息失败，已达最大重试次数")
                            if not self.should_stop():
                                self.check_complete.emit({})
                    except Exception as e:
                        print(f"检查直播状态出错: {e}")
                        if retry < self.max_retries:
                            print(f"尝试重试 {retry + 1}/{self.max_retries}")
                            time.sleep(2)  # 重试前等待2秒
                        else:
                            if not self.should_stop():
                                self.check_complete.emit({})
        
        # 回调函数
        def on_check_complete(info):
            if not info:
                self.live_status_label.setText("获取直播信息失败")
                self.live_status_label.setStyleSheet("color: red;")
                self.start_record_btn.setEnabled(False)
                # 显示详细错误信息
                QMessageBox.warning(self.recorder_dialog, "获取直播信息失败", 
                                   "无法获取房间信息，可能是B站API限制或房间不存在。\n\n请稍后再试或检查房间号是否正确。")
                return
                
            live_status = info.get('live_status', 0)
            if live_status == 1:
                self.live_status_label.setText("正在直播")
                self.live_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
                self.start_record_btn.setEnabled(True)
            else:
                self.live_status_label.setText("未开播")
                self.live_status_label.setStyleSheet("color: #9E9E9E;")
                self.start_record_btn.setEnabled(False)
                
            streamer_name = info.get('streamer_name', '')
            if streamer_name:
                self.streamer_name_label.setText(f"主播: {streamer_name}")
                
            title = info.get('title', '')
            if title:
                self.live_title_label.setText(f"标题: {title}")
                
            # 保存直播信息
            self.current_live_info = info
            
            # 更新房间状态
            self.room_status[room_id] = {
                'live_status': live_status,
                'streamer_name': streamer_name,
                'title': title,
                'cover_url': info.get('cover_url', ''),
                'last_check': time.time()
            }
        
        # 创建并安全启动线程
        check_thread = CheckLiveThread(room_id)
        check_thread.check_complete.connect(on_check_complete)
        self.start_thread(f"check_live_{room_id}", check_thread)
    
    def start_recording(self):
        """开始录制直播"""
        room_id = self.room_id_input.text().strip()
        if not room_id:
            QMessageBox.warning(self.recorder_dialog, "输入错误", "请输入有效的B站直播间号")
            return
            
        if room_id in self.recording_threads:
            QMessageBox.information(self.recorder_dialog, "录制中", f"房间 {room_id} 已经在录制中")
            return
            
        # 获取录制设置
        quality = self.quality_combo.currentData()
        format_type = self.format_combo.currentData()
        record_danmaku = self.danmaku_check.isChecked()
        add_to_auto = self.auto_record_check.isChecked()
        
        # 获取输出目录
        output_dir = self.config.get("output_dir", os.path.join(os.path.expanduser("~"), "Downloads", "BilibiliLive"))
        
        # 如果勾选了添加到自动录制
        if add_to_auto:
            auto_rooms = self.config.get("auto_record_rooms", [])
            
            # 检查是否已存在相同房间号
            room_exists = False
            for room in auto_rooms:
                if isinstance(room, str) and room == room_id:
                    room_exists = True
                    break
                elif isinstance(room, dict) and room.get('room_id', '') == room_id:
                    room_exists = True
                    break
            
            # 只有当房间不存在时才添加
            if not room_exists:
                info = getattr(self, 'current_live_info', {}) or {}
                auto_rooms.append({
                    'room_id': room_id,
                    'streamer_name': info.get('streamer_name', '')
                })
                self.config["auto_record_rooms"] = auto_rooms
                self.save_config()
                self.load_auto_rooms()  # 刷新自动录制表格
        
        # 创建录制线程
        stream_url = None
        cover_url = None
        streamer_name = None
        
        if hasattr(self, 'current_live_info'):
            stream_url = self.current_live_info.get('stream_url', None)
            cover_url = self.current_live_info.get('cover_url', None)
            streamer_name = self.current_live_info.get('streamer_name', None)
        
        thread = LiveRecordingThread(
            room_id, 
            output_dir, 
            quality, 
            format_type, 
            record_danmaku,
            stream_url,
            cover_url,
            streamer_name
        )
        
        # 连接信号
        thread.progress_updated.connect(self.on_record_progress_updated)
        thread.record_complete.connect(self.on_record_complete)
        thread.stream_info_updated.connect(self.on_stream_info_updated)
        
        # 保存线程并启动
        self.recording_threads[room_id] = thread
        thread.start()
        
        # 更新UI状态
        self.start_record_btn.setEnabled(False)
        self.stop_record_btn.setEnabled(True)
        
        # 更新房间状态
        if room_id not in self.room_status:
            self.room_status[room_id] = {}
        
        self.room_status[room_id]['recording'] = True
        self.room_status[room_id]['record_start_time'] = time.time()
        
        # 更新录制管理表格
        self.refresh_tasks()
        
        # 显示通知
        QMessageBox.information(self.recorder_dialog, "开始录制", f"已开始录制房间 {room_id}")
    
    def stop_recording(self):
        """停止录制直播"""
        room_id = self.room_id_input.text().strip()
        if not room_id or room_id not in self.recording_threads:
            QMessageBox.warning(self.recorder_dialog, "未录制", f"房间 {room_id} 未在录制中")
            return
            
        thread = self.recording_threads[room_id]
        thread.stop()  # 这会触发record_complete信号
        
        # 等待线程完全停止，最多等待3秒
        if not thread.wait(3000):
            print(f"警告：房间 {room_id} 的录制线程未能在3秒内停止")
        
        # 更新UI状态
        self.start_record_btn.setEnabled(True)
        self.stop_record_btn.setEnabled(False)
        
        # 显示通知
        QMessageBox.information(self.recorder_dialog, "停止录制", f"已停止录制房间 {room_id}")
    
    def on_record_progress_updated(self, room_id, progress, message):
        """录制进度更新"""
        # 如果当前正在显示的房间是正在更新的房间
        if self.room_id_input.text().strip() == room_id:
            self.live_status_label.setText(message)
            
        # 更新房间状态
        if room_id in self.room_status:
            self.room_status[room_id]['progress'] = progress
            self.room_status[room_id]['status_message'] = message
            
        # 刷新任务表格
        self.refresh_tasks()
    
    def on_record_complete(self, room_id, success, message, file_path):
        """录制完成"""
        print(f"录制完成信号: room_id={room_id}, success={success}, message={message}, file_path={file_path}")
        print(f"文件是否存在: {os.path.exists(file_path) if file_path else False}")
        if file_path and os.path.exists(file_path):
            print(f"文件大小: {os.path.getsize(file_path)/1024:.2f} KB")
        # 从记录线程中移除
        if room_id in self.recording_threads:
            thread = self.recording_threads[room_id]
            # 检查是否是录制MP4的情况，临时TS文件应该已转换为MP4
            if hasattr(thread, 'is_mp4') and thread.is_mp4:
                # 更新文件路径为最终的MP4文件
                if hasattr(thread, 'file_path'):
                    file_path = thread.file_path
                    # 重要：文件存在且已转换为MP4，应该标记为成功
                    if os.path.exists(file_path) and os.path.getsize(file_path) > 0:
                        success = True
                        if not message or "失败" in message:
                            message = f"录制已保存，文件: {os.path.basename(file_path)}"

            del self.recording_threads[room_id]
            
        # 再次确认文件是否存在，如果存在则标记为成功
        if file_path and os.path.exists(file_path):
            success = True
            if not message or "失败" in message:
                message = f"录制已保存，文件: {os.path.basename(file_path)}"
                
        # 更新房间状态
        if room_id in self.room_status:
            self.room_status[room_id]['recording'] = False
            self.room_status[room_id]['record_end_time'] = time.time()
            
        # 更新UI状态，如果当前房间就是完成的房间
        if self.room_id_input.text().strip() == room_id:
            if success:
                self.live_status_label.setText(f"录制完成: {message}")
                self.live_status_label.setStyleSheet("color: #4CAF50;")
            else:
                self.live_status_label.setText(f"录制失败: {message}")
                self.live_status_label.setStyleSheet("color: red;")
                
            self.start_record_btn.setEnabled(True)
            self.stop_record_btn.setEnabled(False)
        
        # 添加到历史记录
        if success and file_path:
            # 获取文件大小
            file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
            file_size_mb = file_size / (1024 * 1024)
            
            # 计算录制时长
            duration = 0
            start_time = self.room_status.get(room_id, {}).get('record_start_time', 0)
            end_time = self.room_status.get(room_id, {}).get('record_end_time', 0)
            if start_time and end_time:
                duration = end_time - start_time
                
            # 获取主播名和标题
            streamer_name = self.room_status.get(room_id, {}).get('streamer_name', '')
            title = self.room_status.get(room_id, {}).get('title', '')
            
            # 添加到历史记录
            history = self.config.get("history", [])
            history.append({
                'room_id': room_id,
                'streamer_name': streamer_name,
                'title': title,
                'file_path': file_path,
                'file_size': file_size,
                'file_size_mb': file_size_mb,
                'duration': duration,
                'time': time.time()
            })
            
            # 限制历史记录长度
            if len(history) > 100:
                history = history[-100:]
                
            self.config["history"] = history
            self.save_config()
            
            # 刷新历史表格
            self.load_history()
            
            # 自动转换为MP4
            if self.config.get("auto_convert", False) and file_path.endswith((".flv", ".ts")):
                self.convert_to_mp4(file_path)
        
        # 刷新任务表格
        self.refresh_tasks()
        
        # 刷新自动录制设置表格，更新录制按钮状态
        self.load_auto_rooms()
    
    def on_stream_info_updated(self, room_id, info):
        """直播流信息更新"""
        if room_id in self.room_status:
            if 'streamer_name' not in self.room_status[room_id] and 'streamer_name' in info:
                self.room_status[room_id]['streamer_name'] = info['streamer_name']
                
            if 'title' not in self.room_status[room_id] and 'title' in info:
                self.room_status[room_id]['title'] = info['title']
                
            if 'cover_url' not in self.room_status[room_id] and 'cover_url' in info:
                self.room_status[room_id]['cover_url'] = info['cover_url']
                
        # 刷新任务表格
        self.refresh_tasks()
    
    def refresh_tasks(self):
        """刷新录制任务表格"""
        self.tasks_table.setRowCount(0)
        
        row = 0
        for room_id, thread in self.recording_threads.items():
            self.tasks_table.insertRow(row)
            
            # 房间号
            room_item = QTableWidgetItem(room_id)
            self.tasks_table.setItem(row, 0, room_item)
            
            # 主播名
            streamer_name = self.room_status.get(room_id, {}).get('streamer_name', '')
            streamer_item = QTableWidgetItem(streamer_name)
            self.tasks_table.setItem(row, 1, streamer_item)
            
            # 状态
            status_message = self.room_status.get(room_id, {}).get('status_message', '录制中...')
            status_item = QTableWidgetItem(status_message)
            self.tasks_table.setItem(row, 2, status_item)
            
            # 录制时长
            duration = 0
            start_time = self.room_status.get(room_id, {}).get('record_start_time', 0)
            if start_time:
                duration = time.time() - start_time
            duration_str = str(datetime.timedelta(seconds=int(duration)))
            duration_item = QTableWidgetItem(duration_str)
            self.tasks_table.setItem(row, 3, duration_item)
            
            # 文件大小
            file_size_mb = self.room_status.get(room_id, {}).get('file_size_mb', 0)
            size_item = QTableWidgetItem(f"{file_size_mb:.2f} MB")
            self.tasks_table.setItem(row, 4, size_item)
            
            # 操作按钮
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(2, 2, 2, 2)
            actions_layout.setSpacing(5)
            
            stop_btn = QPushButton("停止")
            stop_btn.setStyleSheet("background-color: #F44336; color: white; border: none; padding: 3px; border-radius: 3px;")
            stop_btn.setCursor(Qt.PointingHandCursor)
            stop_btn.clicked.connect(lambda checked, rid=room_id: self.stop_room_recording(rid))
            actions_layout.addWidget(stop_btn)
            
            self.tasks_table.setCellWidget(row, 5, actions_widget)
            
            row += 1
    
    def stop_room_recording(self, room_id):
        """停止指定房间的录制"""
        if room_id in self.recording_threads:
            thread = self.recording_threads[room_id]
            thread.stop()  # 这会触发record_complete信号
    
    def stop_all_recordings(self):
        """停止所有录制"""
        if not self.recording_threads:
            QMessageBox.information(self.recorder_dialog, "无录制任务", "当前没有录制中的任务")
            return
            
        reply = QMessageBox.question(self.recorder_dialog, "确认停止", "确定要停止所有录制任务吗？",
                                  QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                                  
        if reply == QMessageBox.Yes:
            # 复制一份键列表，因为在迭代过程中会修改字典
            room_ids = list(self.recording_threads.keys())
            for room_id in room_ids:
                self.stop_room_recording(room_id)
    
    def update_recording_status(self):
        """定时更新录制状态"""
        # 更新录制时长和文件大小
        for room_id, thread in self.recording_threads.items():
            # 更新录制时长
            if room_id in self.room_status and 'record_start_time' in self.room_status[room_id]:
                duration = time.time() - self.room_status[room_id]['record_start_time']
                self.room_status[room_id]['duration'] = duration
                
            # 更新文件大小
            file_path = getattr(thread, 'current_file', None)
            if file_path and os.path.exists(file_path):
                try:
                    file_size = os.path.getsize(file_path)
                    size_mb = file_size / (1024 * 1024)
                    if room_id in self.room_status:
                        self.room_status[room_id]['file_size'] = file_size
                        self.room_status[room_id]['file_size_mb'] = size_mb
                except Exception as e:
                    print(f"获取文件大小出错: {e}")
        
        # 刷新任务表格
        self.refresh_tasks()
    
    def open_output_dir(self):
        """打开输出目录"""
        output_dir = self.config.get("output_dir", "")
        if output_dir and os.path.exists(output_dir):
            QDesktopServices.openUrl(QUrl.fromLocalFile(output_dir))
        else:
            QMessageBox.warning(self.recorder_dialog, "目录不存在", "输出目录不存在，请先在设置中配置正确的目录")
    
    def load_history(self):
        """加载历史记录"""
        history = self.config.get("history", [])
        self.history_table.setRowCount(0)
        
        row = 0
        for item in reversed(history):  # 倒序，最新的在前
            self.history_table.insertRow(row)
            
            # 添加选择框
            check_box = QCheckBox()
            check_box.setStyleSheet("QCheckBox { margin: 5px; }")
            check_cell = QWidget()
            layout = QHBoxLayout(check_cell)
            layout.addWidget(check_box)
            layout.setAlignment(Qt.AlignCenter)
            layout.setContentsMargins(0, 0, 0, 0)
            self.history_table.setCellWidget(row, 0, check_cell)
            
            # 房间号
            room_id = item.get('room_id', '')
            self.history_table.setItem(row, 1, QTableWidgetItem(room_id))
            
            # 主播名
            streamer_name = item.get('streamer_name', '')
            self.history_table.setItem(row, 2, QTableWidgetItem(streamer_name))
            
            # 标题
            title = item.get('title', '')
            self.history_table.setItem(row, 3, QTableWidgetItem(title))
            
            # 时间
            record_time = item.get('time', 0)
            time_str = datetime.datetime.fromtimestamp(record_time).strftime("%Y-%m-%d %H:%M")
            self.history_table.setItem(row, 4, QTableWidgetItem(time_str))
            
            # 时长
            duration = item.get('duration', 0)
            duration_str = str(datetime.timedelta(seconds=int(duration)))
            self.history_table.setItem(row, 5, QTableWidgetItem(duration_str))
            
            # 大小
            file_size_mb = item.get('file_size_mb', 0)
            self.history_table.setItem(row, 6, QTableWidgetItem(f"{file_size_mb:.2f} MB"))
            
            # 操作
            actions_widget = QWidget()
            actions_layout = QHBoxLayout(actions_widget)
            actions_layout.setContentsMargins(2, 2, 2, 2)
            actions_layout.setSpacing(5)
            
            file_path = item.get('file_path', '')
            
            open_btn = QPushButton("打开")
            open_btn.setStyleSheet("background-color: #2196F3; color: white; border: none; padding: 3px; border-radius: 3px; font-size: 11px;")
            open_btn.setCursor(Qt.PointingHandCursor)
            open_btn.clicked.connect(lambda checked, path=file_path: self.open_file(path))
            actions_layout.addWidget(open_btn)
            
            folder_btn = QPushButton("文件夹")
            folder_btn.setStyleSheet("background-color: #4CAF50; color: white; border: none; padding: 3px; border-radius: 3px; font-size: 11px;")
            folder_btn.setCursor(Qt.PointingHandCursor)
            folder_btn.clicked.connect(lambda checked, path=file_path: self.open_containing_folder(path))
            actions_layout.addWidget(folder_btn)
            
            self.history_table.setCellWidget(row, 7, actions_widget)
            
            row += 1
    
    def open_file(self, file_path):
        """打开文件"""
        if os.path.exists(file_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))
        else:
            QMessageBox.warning(self.recorder_dialog, "文件不存在", "录制文件已被移动或删除")
    
    def open_containing_folder(self, file_path):
        """打开包含文件的文件夹"""
        if os.path.exists(file_path):
            QDesktopServices.openUrl(QUrl.fromLocalFile(os.path.dirname(file_path)))
        else:
            QMessageBox.warning(self.recorder_dialog, "文件不存在", "录制文件已被移动或删除")
    
    def load_auto_rooms(self):
        """加载自动录制房间列表"""
        try:
            # 先清空表格
            self.auto_rooms_table.clearContents()
            self.auto_rooms_table.setRowCount(0)
            
            # 获取自动录制房间列表
            auto_rooms = self.config.get("auto_record_rooms", [])
            print(f"正在加载自动录制房间列表，共有 {len(auto_rooms)} 个房间")
            
            # 设置表头样式
            header = self.auto_rooms_table.horizontalHeader()
            header.setStyleSheet("QHeaderView::section { background-color: #f0f0f0; padding: 8px; }")
            
            # 设置行高
            self.auto_rooms_table.verticalHeader().setDefaultSectionSize(40)
            
            # 如果没有房间，添加一个提示行
            if not auto_rooms:
                self.auto_rooms_table.setRowCount(1)
                empty_item = QTableWidgetItem("暂无房间，请在下方输入房间号添加")
                empty_item.setTextAlignment(Qt.AlignCenter)
                empty_item.setForeground(QColor("#999999"))
                self.auto_rooms_table.setItem(0, 0, empty_item)
                self.auto_rooms_table.setSpan(0, 0, 1, 3)  # 合并单元格
                return
            
            # 添加房间数据
            rooms_to_update = []
            for i, room in enumerate(auto_rooms):
                # 解析房间信息
                if isinstance(room, str):  # 向后兼容旧格式
                    room_id = room
                    streamer_name = ""
                    rooms_to_update.append(room_id)
                else:
                    room_id = room.get('room_id', '')
                    streamer_name = room.get('streamer_name', '')
                    if not streamer_name:
                        rooms_to_update.append(room_id)
                
                # 插入新行
                row = self.auto_rooms_table.rowCount()
                self.auto_rooms_table.insertRow(row)
                
                # 添加房间号
                room_item = QTableWidgetItem(room_id)
                room_item.setTextAlignment(Qt.AlignCenter)
                self.auto_rooms_table.setItem(row, 0, room_item)
                
                # 添加主播名
                streamer_item = QTableWidgetItem(streamer_name if streamer_name else "加载中...")
                streamer_item.setTextAlignment(Qt.AlignCenter)
                if not streamer_name:
                    streamer_item.setForeground(QColor("#888888"))
                self.auto_rooms_table.setItem(row, 1, streamer_item)
                
                # 创建操作按钮
                actions_widget = QWidget()
                actions_layout = QHBoxLayout(actions_widget)
                actions_layout.setContentsMargins(2, 2, 2, 2)
                actions_layout.setSpacing(5)  # 减小按钮之间的间距
                actions_layout.setAlignment(Qt.AlignCenter)
                
                # 添加启动录制按钮
                start_btn = QPushButton("录制")
                start_btn.setFixedSize(40, 24)
                start_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #4CAF50;
                        color: white;
                        border: none;
                        border-radius: 3px;
                        font-size: 12px;
                        font-weight: bold;
                    }
                    QPushButton:hover {
                        background-color: #66BB6A;
                    }
                    QPushButton:pressed {
                        background-color: #388E3C;
                    }
                    QPushButton:disabled {
                        background-color: #A5D6A7;
                        color: #E8F5E9;
                    }
                """)
                start_btn.setCursor(Qt.PointingHandCursor)
                
                # 使用functools.partial来正确传递参数
                from functools import partial
                start_btn.clicked.connect(partial(self.start_room_recording, room_id))
                
                # 如果已经在录制，禁用按钮
                if hasattr(self, 'recording_threads') and room_id in self.recording_threads:
                    start_btn.setEnabled(False)
                    start_btn.setText("录制中")
                
                actions_layout.addWidget(start_btn)
                
                # 删除按钮
                remove_btn = QPushButton("删除")
                remove_btn.setFixedSize(40, 24)
                remove_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #F44336;
                        color: white;
                        border: none;
                        border-radius: 3px;
                        font-size: 12px;
                        font-weight: bold;
                    }
                    QPushButton:hover {
                        background-color: #FF5252;
                    }
                    QPushButton:pressed {
                        background-color: #D32F2F;
                    }
                """)
                remove_btn.setCursor(Qt.PointingHandCursor)
                
                # 使用functools.partial来正确传递参数
                remove_btn.clicked.connect(partial(self.remove_auto_room, row, room_id))
                
                actions_layout.addWidget(remove_btn)
                
                # 刷新按钮
                refresh_btn = QPushButton("刷新")
                refresh_btn.setFixedSize(40, 24)
                refresh_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #2196F3;
                        color: white;
                        border: none;
                        border-radius: 3px;
                        font-size: 12px;
                        font-weight: bold;
                    }
                    QPushButton:hover {
                        background-color: #42A5F5;
                    }
                    QPushButton:pressed {
                        background-color: #1976D2;
                    }
                """)
                refresh_btn.setCursor(Qt.PointingHandCursor)
                
                # 使用functools.partial来正确传递参数
                refresh_btn.clicked.connect(partial(self.refresh_room_info, room_id))
                
                actions_layout.addWidget(refresh_btn)
                
                # 设置操作列
                self.auto_rooms_table.setCellWidget(row, 2, actions_widget)
            
            # 确保表格可见
            self.auto_rooms_table.show()
            self.auto_rooms_table.update()
            
            # 如果有需要更新主播名的记录，启动后台线程进行更新
            if rooms_to_update:
                self.update_streamer_names(rooms_to_update)
                
        except Exception as e:
            import traceback
            print(f"加载自动录制房间列表出错: {e}")
            traceback.print_exc()
            # 显示错误信息
            self.auto_rooms_table.setRowCount(1)
            error_item = QTableWidgetItem(f"加载失败: {str(e)}")
            error_item.setTextAlignment(Qt.AlignCenter)
            error_item.setForeground(QColor("#FF0000"))
            self.auto_rooms_table.setItem(0, 0, error_item)
            self.auto_rooms_table.setSpan(0, 0, 1, 3)
    def start_room_recording(self, room_id):
        """开始录制指定房间"""
        # 如果已经在录制，跳过
        if hasattr(self, 'recording_threads') and room_id in self.recording_threads:
            QMessageBox.information(self.recorder_dialog, "已在录制", f"房间 {room_id} 已在录制中")
            return
            
        # 检查是否正在直播
        try:
            print(f"检查房间 {room_id} 是否在直播...")
            
            # 显示正在检查的提示
            cursor = self.recorder_dialog.cursor()
            self.recorder_dialog.setCursor(Qt.WaitCursor)  # 设置等待光标
            
            # 获取直播信息
            thread = LiveRecordingThread(room_id, "", "best")
            info = thread.get_stream_info()
            
            # 恢复光标
            self.recorder_dialog.setCursor(cursor)
            
            if not info:
                QMessageBox.warning(self.recorder_dialog, "获取直播信息失败", 
                                f"无法获取房间 {room_id} 的信息，请稍后再试")
                return
                
            if info.get('live_status') != 1:
                QMessageBox.information(self.recorder_dialog, "未开播", 
                                    f"房间 {room_id} 当前未在直播")
                return
                
            # 获取配置
            output_dir = self.config.get("output_dir", os.path.join(os.path.expanduser("~"), "Downloads", "BilibiliLive"))
            quality = self.config.get("quality", "best")
            format_type = self.config.get("format", "flv")
            record_danmaku = self.config.get("record_danmaku", True)
            
            # 确保输出目录存在
            os.makedirs(output_dir, exist_ok=True)
            
            # 启动录制线程
            if not hasattr(self, 'recording_threads'):
                self.recording_threads = {}
                
            record_thread = LiveRecordingThread(
                room_id, 
                output_dir, 
                quality, 
                format_type, 
                record_danmaku,
                info.get('stream_url'),
                info.get('cover_url'),
                info.get('streamer_name')
            )
            
            # 连接信号
            record_thread.progress_updated.connect(self.on_record_progress_updated)
            record_thread.record_complete.connect(self.on_record_complete)
            record_thread.stream_info_updated.connect(self.on_stream_info_updated)
            
            # 保存线程并启动
            self.recording_threads[room_id] = record_thread
            record_thread.start()
            
            # 更新房间状态
            if not hasattr(self, 'room_status'):
                self.room_status = {}
                
            self.room_status[room_id] = {
                'recording': True,
                'record_start_time': time.time(),
                'live_status': 1,
                'streamer_name': info.get('streamer_name', ''),
                'title': info.get('title', ''),
                'cover_url': info.get('cover_url', '')
            }
            
            # 刷新表格，更新按钮状态
            self.load_auto_rooms()
            
            # 刷新录制管理表格
            if hasattr(self, 'refresh_tasks'):
                self.refresh_tasks()
                
            QMessageBox.information(self.recorder_dialog, "开始录制", 
                                f"已开始录制房间 {room_id}" + 
                                (f" ({info.get('streamer_name', '')})" if info.get('streamer_name') else ""))
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self.recorder_dialog, "录制失败", f"开始录制房间 {room_id} 失败: {str(e)}")    
    def update_streamer_names(self, room_ids):
        """批量更新主播名信息"""
        if not room_ids:
            return
            
        class UpdateStreamerNamesThread(SafeThread):
            update_complete = pyqtSignal(dict)
            
            def __init__(self, room_ids):
                super().__init__()
                self.room_ids = room_ids
                
            def run(self):
                results = {}
                for room_id in self.room_ids:
                    if self.should_stop():
                        break
                        
                    try:
                        thread = LiveRecordingThread(room_id, "", "best")
                        info = thread.get_stream_info()
                        
                        if info and 'streamer_name' in info:
                            results[room_id] = info.get('streamer_name', '')
                            print(f"获取房间 {room_id} 主播名成功: {results[room_id]}")
                        else:
                            print(f"获取房间 {room_id} 信息失败或主播名为空")
                            
                    except Exception as e:
                        print(f"获取房间 {room_id} 主播名出错: {e}")
                        
                    # 避免API调用过于频繁
                    if not self.should_stop() and self.room_ids.index(room_id) < len(self.room_ids) - 1:
                        time.sleep(1)
                
                if not self.should_stop():
                    self.update_complete.emit(results)
        
        # 创建更新线程回调函数
        def on_update_complete(results):
            if not results:
                return
                
            # 更新配置信息中的主播名
            auto_rooms = self.config.get("auto_record_rooms", [])
            updated = False
            
            for i, room in enumerate(auto_rooms):
                room_id = room if isinstance(room, str) else room.get('room_id', '')
                if room_id in results and results[room_id]:
                    if isinstance(room, str):
                        # 旧格式转为新格式
                        auto_rooms[i] = {
                            'room_id': room,
                            'streamer_name': results[room_id]
                        }
                        updated = True
                    elif not room.get('streamer_name'):
                        # 更新空的主播名
                        auto_rooms[i]['streamer_name'] = results[room_id]
                        updated = True
            
            # 保存更新后的配置
            if updated:
                self.config["auto_record_rooms"] = auto_rooms
                self.save_config()
            
            # 刷新表格显示
            for row in range(self.auto_rooms_table.rowCount()):
                room_item = self.auto_rooms_table.item(row, 0)
                if room_item:
                    room_id = room_item.text()
                    if room_id in results and results[room_id]:
                        streamer_item = self.auto_rooms_table.item(row, 1)
                        if streamer_item:
                            streamer_item.setText(results[room_id])
                            streamer_item.setForeground(QColor("#000000"))
        
        # 创建并启动线程
        update_thread = UpdateStreamerNamesThread(room_ids)
        update_thread.update_complete.connect(on_update_complete)
        self.start_thread("update_streamer_names", update_thread)
        
    def refresh_room_info(self, room_id):
        """刷新指定房间的信息"""
        # 查找对应的行
        row = -1
        for r in range(self.auto_rooms_table.rowCount()):
            item = self.auto_rooms_table.item(r, 0)
            if item and item.text() == room_id:
                row = r
                break
                
        if row == -1:
            return
                
        # 更新显示状态
        streamer_item = self.auto_rooms_table.item(row, 1)
        if streamer_item:
            streamer_item.setText("刷新中...")
            streamer_item.setForeground(QColor("#888888"))
        
        # 启动线程进行刷新
        self.update_streamer_names([room_id])
    
    def add_auto_room(self):
        """添加自动录制房间"""
        room_id = self.new_auto_room_input.text().strip()
        if not room_id:
            QMessageBox.warning(self.recorder_dialog, "输入错误", "请输入有效的房间号")
            return
            
        if not room_id.isdigit():
            QMessageBox.warning(self.recorder_dialog, "输入错误", "房间号必须是数字")
            return
            
        # 检查是否已存在
        auto_rooms = self.config.get("auto_record_rooms", [])
        for room in auto_rooms:
            if isinstance(room, str) and room == room_id:
                QMessageBox.information(self.recorder_dialog, "已存在", f"房间 {room_id} 已在自动录制列表中")
                return
            elif isinstance(room, dict) and room.get('room_id', '') == room_id:
                QMessageBox.information(self.recorder_dialog, "已存在", f"房间 {room_id} 已在自动录制列表中")
                return
        
        # 显示正在加载状态
        self.new_auto_room_input.setEnabled(False)
        # 删除弹窗代码
        
        # 查询房间信息
        try:
            # 使用LiveRecordingThread进行查询
            thread = LiveRecordingThread(room_id, "", "best")
            
            # 尝试最多3次获取房间信息
            max_retries = 3
            info = None
            
            for retry in range(max_retries):
                try:
                    info = thread.get_stream_info()
                    if info and 'streamer_name' in info:
                        break
                    time.sleep(1)  # 重试间隔
                except Exception as e:
                    print(f"获取房间信息尝试 {retry+1}/{max_retries} 失败: {e}")
                    if retry == max_retries - 1:
                        raise
            
            if not info:
                # 删除弹窗代码，直接设置空的主播名
                streamer_name = ""
                print("无法获取房间信息，添加房间但不显示主播名")
            else:
                streamer_name = info.get('streamer_name', '')
                if not streamer_name:
                    print("警告：获取到的信息中没有主播名")
                else:
                    print(f"成功获取主播名: {streamer_name}")
                    
            # 添加到配置
            auto_rooms.append({
                'room_id': room_id,
                'streamer_name': streamer_name
            })
            
            self.config["auto_record_rooms"] = auto_rooms
            self.save_config()
            
            # 刷新表格
            self.load_auto_rooms()
            
            # 清空输入
            self.new_auto_room_input.clear()
            self.new_auto_room_input.setEnabled(True)
            
            # 显示成功信息
            if streamer_name:
                QMessageBox.information(self.recorder_dialog, "添加成功", f"已添加房间 {room_id}（{streamer_name}）到自动录制列表")
            else:
                QMessageBox.information(self.recorder_dialog, "添加成功", f"已添加房间 {room_id} 到自动录制列表")
            
        except Exception as e:
            import traceback
            traceback.print_exc()
            QMessageBox.warning(self.recorder_dialog, "添加失败", f"添加房间失败: {str(e)}")
            self.new_auto_room_input.setEnabled(True)
    
    def remove_auto_room(self, row, room_id):
        """移除自动录制房间"""
        reply = QMessageBox.question(self.recorder_dialog, "确认删除", 
                                f"确定要从自动录制列表中删除房间 {room_id} 吗？",
                                QMessageBox.Yes | QMessageBox.No)
                                
        if reply == QMessageBox.Yes:
            auto_rooms = self.config.get("auto_record_rooms", [])
            
            # 找到并删除
            for i, room in enumerate(auto_rooms):
                if (isinstance(room, str) and room == room_id) or \
                (isinstance(room, dict) and room.get('room_id', '') == room_id):
                    del auto_rooms[i]
                    break
                    
            self.config["auto_record_rooms"] = auto_rooms
            self.save_config()
            
            # 刷新表格
            self.load_auto_rooms()
    
    def browse_output_dir(self):
        """浏览输出目录"""
        current_dir = self.output_dir_input.text()
        directory = QFileDialog.getExistingDirectory(self.recorder_dialog, "选择保存目录", current_dir)
        if directory:
            self.output_dir_input.setText(directory)
    
    def save_settings(self):
        """保存设置"""
        self.config["output_dir"] = self.output_dir_input.text()
        self.config["quality"] = self.default_quality_combo.currentData()
        self.config["format"] = self.default_format_combo.currentData()
        self.config["record_danmaku"] = self.default_danmaku_check.isChecked()
        self.config["auto_convert"] = self.auto_convert_check.isChecked()
        self.config["check_interval"] = self.check_interval_spin.value()
        
        self.save_config()
        
        # 同步设置到直播录制选项卡
        if hasattr(self, 'quality_combo') and self.quality_combo:
            for i in range(self.quality_combo.count()):
                if self.quality_combo.itemData(i) == self.config["quality"]:
                    self.quality_combo.setCurrentIndex(i)
                    break
                    
        if hasattr(self, 'format_combo') and self.format_combo:
            format_texts = {"flv": "FLV格式", "mp4": "MP4格式", "ts": "TS格式"}
            if self.config["format"] in format_texts:
                self.format_combo.setCurrentText(format_texts[self.config["format"]])
                
        if hasattr(self, 'danmaku_check') and self.danmaku_check:
            self.danmaku_check.setChecked(self.config["record_danmaku"])
            
        QMessageBox.information(self.recorder_dialog, "设置已保存", "设置已保存并应用到当前界面")
    
    def reset_settings(self):
        """重置设置"""
        reply = QMessageBox.question(self.recorder_dialog, "确认重置", 
                                 "确定要重置所有设置吗？这将不会删除已录制的文件。",
                                 QMessageBox.Yes | QMessageBox.No)
                                 
        if reply == QMessageBox.Yes:
            # 重置配置
            self.config = {
                "output_dir": os.path.join(os.path.expanduser("~"), "Downloads", "BilibiliLive"),
                "quality": "best",
                "format": "flv",
                "record_danmaku": True,
                "auto_record_rooms": [],
                "check_interval": 60,
                "auto_convert": False,
                "history": self.config.get("history", [])  # 保留历史记录
            }
            
            self.save_config()
            
            # 重新加载UI
            self.output_dir_input.setText(self.config["output_dir"])
            self.default_quality_combo.setCurrentIndex(0)  # best
            self.default_format_combo.setCurrentIndex(0)  # flv
            self.default_danmaku_check.setChecked(True)
            self.auto_convert_check.setChecked(False)
            self.check_interval_spin.setValue(60)
            
            # 刷新自动录制表格
            self.load_auto_rooms()
            
            QMessageBox.information(self.recorder_dialog, "设置已重置", "所有设置已重置为默认值")
    
    def check_replay_info(self):
        """获取回放信息"""
        replay_url = self.replay_url_input.text().strip()
        if not replay_url:
            QMessageBox.warning(self.recorder_dialog, "输入错误", "请输入有效的B站回放地址")
            return
        
        # 验证URL格式
        if not replay_url.startswith("http"):
            QMessageBox.warning(self.recorder_dialog, "输入错误", "请输入完整的回放地址，以http开头")
            return
            
        # 显示加载状态
        self.replay_title_label.setText("正在获取回放信息...")
        self.replay_title_label.setStyleSheet("color: #2196F3;")
        self.replay_format_label.setText("")
        
        # 创建临时线程进行API请求
        class CheckReplayThread(QThread):
            check_complete = pyqtSignal(dict)
            
            def __init__(self, url):
                super().__init__()
                self.url = url
                self._stop_flag = False
                self.max_retries = 2
                
            def stop(self):
                self._stop_flag = True
                
            def should_stop(self):
                return self._stop_flag
                
            def run(self):
                if self.should_stop():
                    return
                
                for retry in range(self.max_retries + 1):
                    try:
                        if self.should_stop():
                            return
                            
                        thread = ReplayDownloadThread(self.url, "", "best")
                        info = thread.get_video_info()
                        
                        if info is not None:
                            if not self.should_stop():
                                self.check_complete.emit(info)
                            return
                        elif retry < self.max_retries:
                            print(f"获取回放信息失败，尝试重试 {retry + 1}/{self.max_retries}")
                            time.sleep(2)
                        else:
                            print(f"获取回放信息失败，已达最大重试次数")
                            if not self.should_stop():
                                self.check_complete.emit({})
                    except Exception as e:
                        print(f"检查回放信息出错: {e}")
                        import traceback
                        traceback.print_exc()
                        if retry < self.max_retries:
                            print(f"尝试重试 {retry + 1}/{self.max_retries}")
                            time.sleep(2)
                        else:
                            if not self.should_stop():
                                self.check_complete.emit({})
        
        def on_check_complete(info):
            if not info:
                self.replay_title_label.setText("获取回放信息失败")
                self.replay_title_label.setStyleSheet("color: red;")
                self.start_dl_btn.setEnabled(False)
                QMessageBox.warning(self.recorder_dialog, "获取回放信息失败", 
                                   "无法获取回放信息，可能是链接无效或B站API限制。\n\n请稍后再试或检查URL是否正确。")
                return
                
            title = info.get('title', '')
            if title:
                self.replay_title_label.setText(f"标题: {title}")
                self.replay_title_label.setStyleSheet("")
                
            # 显示可用格式
            formats = info.get('formats', [])
            if formats:
                formats_str = ", ".join([f"{f.get('id')} ({f.get('description')})" for f in formats])
                self.replay_format_label.setText(f"可用格式: {formats_str}")
                
                # 更新画质下拉框
                self.dl_quality_combo.clear()
                self.dl_quality_combo.addItem("最高画质", "best")
                for format_info in formats:
                    format_id = format_info.get('id', '')
                    format_desc = format_info.get('description', '')
                    self.dl_quality_combo.addItem(f"{format_desc}", format_id)
            
            # 保存回放信息
            self.current_replay_info = info
            
            # 启用下载按钮
            self.start_dl_btn.setEnabled(True)
        
        # 创建并安全启动线程
        check_thread = CheckReplayThread(replay_url)
        check_thread.check_complete.connect(on_check_complete)
        self.start_thread(f"check_replay_{replay_url}", check_thread)
    
    def start_downloading(self):
        """开始下载回放"""
        replay_url = self.replay_url_input.text().strip()
        if not replay_url:
            QMessageBox.warning(self.recorder_dialog, "输入错误", "请输入有效的B站回放地址")
            return
            
        # 获取下载设置
        quality = self.dl_quality_combo.currentData()
        
        # 获取输出目录
        output_dir = self.config.get("output_dir", os.path.join(os.path.expanduser("~"), "Downloads", "BilibiliLive"))
        
        # 创建下载线程
        download_thread = ReplayDownloadThread(replay_url, output_dir, quality)
        
        # 连接信号
        download_thread.progress_updated.connect(self.on_download_progress_updated)
        download_thread.download_complete.connect(self.on_download_complete)
        
        # 更新UI状态
        self.start_dl_btn.setEnabled(False)
        self.cancel_dl_btn.setEnabled(True)
        self.dl_progress_bar.setValue(0)
        self.dl_status_label.setText("准备下载...")
        
        # 安全启动线程
        self.start_thread("download_replay", download_thread)
        self.download_thread = download_thread  # 保持兼容性
    
    def cancel_downloading(self):
        """取消下载回放"""
        if hasattr(self, 'download_thread') and self.download_thread.isRunning():
            self.download_thread.stop()
            
            # 更新UI状态
            self.start_dl_btn.setEnabled(True)
            self.cancel_dl_btn.setEnabled(False)
            self.dl_status_label.setText("下载已取消")
    
    def on_download_progress_updated(self, progress, message):
        """下载进度更新"""
        self.dl_progress_bar.setValue(progress)
        self.dl_status_label.setText(message)
    
    def on_download_complete(self, success, message, file_path):
        """下载完成"""
        # 更新UI状态
        self.start_dl_btn.setEnabled(True)
        self.cancel_dl_btn.setEnabled(False)
        
        if success:
            self.dl_status_label.setText("下载完成")
            
            # 添加到历史记录
            if file_path:
                # 获取文件大小
                file_size = os.path.getsize(file_path) if os.path.exists(file_path) else 0
                file_size_mb = file_size / (1024 * 1024)
                
                title = getattr(self, 'current_replay_info', {}).get('title', os.path.basename(file_path))
                
                history = self.config.get("history", [])
                history.append({
                    'room_id': "回放",
                    'streamer_name': "",
                    'title': title,
                    'file_path': file_path,
                    'file_size': file_size,
                    'file_size_mb': file_size_mb,
                    'duration': 0,
                    'time': time.time()
                })
                
                # 限制历史记录长度
                if len(history) > 100:
                    history = history[-100:]
                    
                self.config["history"] = history
                self.save_config()
                
                # 刷新历史表格
                self.load_history()
                
                # 询问是否打开文件
                reply = QMessageBox.question(self.recorder_dialog, "下载完成", 
                                         f"回放已下载完成!\n\n文件: {os.path.basename(file_path)}\n大小: {file_size_mb:.2f} MB\n\n是否打开文件?",
                                         QMessageBox.Yes | QMessageBox.No)
                if reply == QMessageBox.Yes:
                    QDesktopServices.openUrl(QUrl.fromLocalFile(file_path))
        else:
            self.dl_status_label.setText(f"下载失败: {message}")
            QMessageBox.warning(self.recorder_dialog, "下载失败", f"下载失败: {message}")
    
    def convert_to_mp4(self, file_path):
        """将文件转换为MP4格式"""
        if not os.path.exists(file_path):
            return
            
        # 检查是否已是MP4
        if file_path.lower().endswith(".mp4"):
            return
            
        # 构建输出路径
        output_path = os.path.splitext(file_path)[0] + ".mp4"
        
        # 创建转换线程
        class ConvertThread(QThread):
            convert_complete = pyqtSignal(bool, str)
            
            def __init__(self, input_file, output_file):
                super().__init__()
                self.input_file = input_file
                self.output_file = output_file
                
            def run(self):
                try:
                    cmd = ["ffmpeg", "-i", self.input_file, "-c:v", "copy", "-c:a", "copy", self.output_file]
                    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='replace')
                    stdout, stderr = process.communicate()
                    
                    if process.returncode == 0 and os.path.exists(self.output_file):
                        self.convert_complete.emit(True, self.output_file)
                    else:
                        print(f"转换失败: {stderr}")
                        self.convert_complete.emit(False, "")
                except Exception as e:
                    print(f"转换出错: {e}")
                    self.convert_complete.emit(False, "")
            def __del__(self):
                """在对象被销毁前确保线程安全停止"""
                try:
                    if self.isRunning():
                        self.wait(3000)  # 等待最多3秒
                except:
                    pass  # 忽略可能的异常，防止程序关闭时出错
        convert_thread = ConvertThread(file_path, output_path)
        convert_thread.convert_complete.connect(lambda success, path: 
            print(f"转换{'成功' if success else '失败'}: {path}"))
        convert_thread.start()
    
    def check_auto_record_rooms(self):
        """检查自动录制房间列表"""
        auto_rooms = self.config.get("auto_record_rooms", [])
        if not auto_rooms:
            return
            
        print(f"正在检查自动录制房间列表，共有 {len(auto_rooms)} 个房间")
        
        # 记录需要录制的房间
        rooms_to_record = []
        
        for room_info in auto_rooms:
            if isinstance(room_info, str):
                room_id = room_info
            else:
                room_id = room_info.get('room_id', '')
                
            if not room_id:
                continue
                
            # 如果已经在录制中，跳过
            if hasattr(self, 'recording_threads') and room_id in self.recording_threads:
                print(f"房间 {room_id} 已在录制中，跳过检查")
                continue
                
            # 检查是否正在直播
            try:
                print(f"检查房间 {room_id} 是否在直播...")
                thread = LiveRecordingThread(room_id, "", "best")
                info = thread.get_stream_info()
                
                if info and info.get('live_status') == 1:
                    print(f"发现房间 {room_id} 正在直播，准备自动录制")
                    
                    # 获取配置
                    output_dir = self.config.get("output_dir", os.path.join(os.path.expanduser("~"), "Downloads", "BilibiliLive"))
                    quality = self.config.get("quality", "best")
                    format_type = self.config.get("format", "flv")
                    record_danmaku = self.config.get("record_danmaku", True)
                    
                    # 确保输出目录存在
                    os.makedirs(output_dir, exist_ok=True)
                    
                    # 启动录制线程
                    if not hasattr(self, 'recording_threads'):
                        self.recording_threads = {}
                        
                    record_thread = LiveRecordingThread(
                        room_id, 
                        output_dir, 
                        quality, 
                        format_type, 
                        record_danmaku,
                        info.get('stream_url'),
                        info.get('cover_url'),
                        info.get('streamer_name')
                    )
                    
                    # 连接信号
                    record_thread.progress_updated.connect(self.on_record_progress_updated)
                    record_thread.record_complete.connect(self.on_record_complete)
                    record_thread.stream_info_updated.connect(self.on_stream_info_updated)
                    
                    # 保存线程并启动
                    self.recording_threads[room_id] = record_thread
                    record_thread.start()
                    
                    # 更新房间状态
                    if not hasattr(self, 'room_status'):
                        self.room_status = {}
                        
                    self.room_status[room_id] = {
                        'recording': True,
                        'record_start_time': time.time(),
                        'live_status': 1,
                        'streamer_name': info.get('streamer_name', ''),
                        'title': info.get('title', ''),
                        'cover_url': info.get('cover_url', '')
                    }
                    
                    print(f"已开始自动录制房间 {room_id}")
                    
            except Exception as e:
                print(f"检查房间 {room_id} 出错: {e}")
    
    def show_install_guide(self):
        """显示安装指导"""
        guide_text = """
        <h3>安装 you-get</h3>
        <p>you-get 是一个命令行程序，用于从各种视频网站下载视频，包括B站回放。</p>
        
        <h4>安装方法：</h4>
        <ol>
            <li>确保已安装 Python 3.6+</li>
            <li>打开命令行（终端/CMD）</li>
            <li>输入命令：<pre>pip install you-get</pre></li>
            <li>等待安装完成</li>
            <li>重启本插件</li>
        </ol>
        
        <h4>详细文档：</h4>
        <p><a href="https://github.com/soimort/you-get">https://github.com/soimort/you-get</a></p>
        """
        
        msg_box = QMessageBox(self.recorder_dialog)
        msg_box.setWindowTitle("安装指导")
        msg_box.setTextFormat(Qt.RichText)
        msg_box.setText(guide_text)
        msg_box.setStandardButtons(QMessageBox.Ok)
        msg_box.exec_()
    
    def refresh_youget_status(self):
        """刷新you-get状态"""
        try:
            subprocess.run(['you-get', '--version'], capture_output=True, text=True, encoding='utf-8', errors='replace')
            QMessageBox.information(self.recorder_dialog, "检测成功", "you-get 已成功安装！请重启插件以启用回放下载功能。")
        except:
            QMessageBox.warning(self.recorder_dialog, "未安装", "未检测到 you-get，请先安装后再使用回放下载功能。")
    
    def cleanup_ui(self):
        """清理UI资源"""
        print("开始清理B站直播录制插件UI资源...")
        
        # 停止录制状态更新定时器
        if hasattr(self, 'status_timer') and self.status_timer:
            self.status_timer.stop()
        
        # 清理所有B站直播录制按钮
        if hasattr(self, 'app') and self.app:
            try:
                found_buttons = []
                for widget in self.app.findChildren(QPushButton):
                    if widget and hasattr(widget, 'text') and widget.text() == "B站直播录制":
                        found_buttons.append(widget)
                
                for button in found_buttons:
                    print(f"找到按钮: {button.objectName()}, 正在移除...")
                    parent = button.parent()
                    if parent and parent.layout():
                        parent.layout().removeWidget(button)
                    button.hide()
                    button.setVisible(False)
                    button.setParent(None)
                    button.deleteLater()
            except Exception as e:
                print(f"移除按钮时出错: {e}")
        
        # 停止所有临时线程
        if hasattr(self, '_threads'):
            self.stop_all_temporary_threads()
        
        # 隐藏对话框
        if hasattr(self, 'recorder_dialog') and self.recorder_dialog:
            self.recorder_dialog.hide()
        
        # 标记按钮已移除
        self._button_added = False
        if hasattr(self, 'live_recorder_button'):
            self.live_recorder_button = None
        
        # 刷新UI
        QApplication.processEvents()
        print("B站直播录制插件UI资源清理完成")
        
    def __del__(self):
        """在对象被销毁前停止所有线程"""
        self.stop_all_threads()
        if hasattr(self, '_threads'):
            thread_names = list(self._threads.keys())
            for name in thread_names:
                self.stop_thread(name)

    def stop_all_threads(self):
        """停止所有运行中的线程"""
        # 停止录制线程
        if hasattr(self, 'recording_threads'):
            threads = list(self.recording_threads.values())
            for thread in threads:
                if thread.isRunning():
                    try:
                        thread.stop()  # 发送停止信号
                        thread.wait(3000)  # 等待最多3秒
                        if thread.isRunning():
                            print(f"警告：线程未能正常停止")
                    except Exception as e:
                        print(f"停止线程时出错: {e}")
        
        # 停止所有临时线程
        if hasattr(self, '_threads'):
            self.stop_all_temporary_threads()
    
    def start_thread(self, thread_name, thread):
        """启动并跟踪线程"""
        self.stop_thread(thread_name)  # 先停止同名线程（如果存在）
        
        thread.finished.connect(lambda: self.on_thread_finished(thread_name))
        thread.finished.connect(thread.deleteLater)  # 自动清理
        thread.start()
        self._threads[thread_name] = thread
        
    def stop_thread(self, thread_name):
        """停止指定线程"""
        if thread_name in self._threads:
            thread = self._threads[thread_name]
            if thread and thread.isRunning():
                if isinstance(thread, SafeThread):
                    thread.stop()  # 使用安全停止机制
                    if not thread.wait(3000):  # 等待最多3秒
                        print(f"警告: 线程 {thread_name} 未能正常停止")
                else:
                    try:
                        thread.disconnect()  # 断开所有信号连接
                        thread.terminate()   # 强制终止线程
                        thread.wait(1000)    # 等待线程结束，最多1秒
                    except:
                        pass
            self._threads.pop(thread_name, None)
            
    def on_thread_finished(self, thread_name):
        """线程完成后的回调"""
        self._threads.pop(thread_name, None)
        print(f"线程 {thread_name} 已完成并清理")
    
    def stop_all_temporary_threads(self):
        """停止所有临时线程但保留录制线程"""
        thread_names = list(self._threads.keys())
        for name in thread_names:
            if not name.startswith("recording_"):  # 不停止录制线程
                self.stop_thread(name)
    
    def get_hooks(self):
        """返回此插件提供的所有钩子"""
        return {
            "on_startup": self.on_startup,
            "on_disable": self.on_disable,
            "on_enable": self.on_enable
        }
        
    def on_startup(self):
        """应用启动时执行"""
        print("B站直播录制插件已启动，开始初始化")
        
        # 调用initialize方法进行初始化
        self.initialize()
        
        # 额外检查插件是否启用（以防initialize中的检查有问题）
        is_enabled = self.check_enabled_status()
        
        if not is_enabled:
            print("B站直播录制插件已禁用，不添加按钮")
            self._is_enabled = False
            
        return True
    
    def check_enabled_status(self):
        """检查插件当前的启用状态"""
        if not hasattr(self.app, 'plugin_manager'):
            print("无法检查插件状态：app实例没有plugin_manager属性")
            return True  # 默认启用
            
        try:
            plugin_id = self.app.plugin_manager.get_plugin_id(self)
            is_enabled = plugin_id in self.app.plugin_manager.enabled_plugins and self.app.plugin_manager.enabled_plugins[plugin_id]
            print(f"插件 {plugin_id} 当前启用状态: {is_enabled}")
            return is_enabled
        except Exception as e:
            print(f"检查插件启用状态时出错: {e}")
            import traceback
            traceback.print_exc()
            return True  # 出错时默认启用
            
    def on_enable(self):
        """插件被启用时执行"""
        print("B站直播录制插件被启用")
        self._is_enabled = True
        
        # 延迟添加按钮
        QTimer.singleShot(1000, self.add_live_recorder_action)
        print("已设置延迟1秒后添加B站直播录制按钮")
        
        return True
    
    def on_disable(self):
        """插件被禁用时执行"""
        print("B站直播录制插件被禁用")
        
        # 设置禁用状态
        self._is_enabled = False
        
        # 停止自动检查定时器
        if hasattr(self, 'auto_check_timer') and self.auto_check_timer:
            self.auto_check_timer.stop()
        
        # 清理UI元素
        try:
            # 1. 先尝试移除当前实例的按钮
            if hasattr(self, 'live_recorder_button') and self.live_recorder_button:
                button = self.live_recorder_button
                parent = button.parent()
                if parent:
                    layout = parent.layout()
                    if layout:
                        layout.removeWidget(button)
                
                # 断开与父对象的连接并设置为不可见
                button.hide()
                button.setVisible(False)
                button.setParent(None)
                button.deleteLater()  # 确保彻底删除按钮
                print("已移除B站直播录制按钮")
                
                # 删除引用和标记
                self.live_recorder_button = None
                self._button_added = False
            
            # 2. 查找并移除所有遗留的同名按钮
            if hasattr(self, 'app') and self.app:
                for widget in self.app.findChildren(QPushButton):
                    if widget and hasattr(widget, 'text') and widget.text() == "B站直播录制":
                        print(f"找到遗留按钮，正在移除...")
                        
                        # 从父布局中移除
                        parent = widget.parent()
                        if parent and parent.layout():
                            parent.layout().removeWidget(widget)
                        
                        # 彻底删除按钮
                        widget.hide()
                        widget.setVisible(False)
                        widget.setParent(None)
                        widget.deleteLater()
                        
            # 3. 如果有对话框正在显示，关闭它
            if hasattr(self, 'recorder_dialog') and self.recorder_dialog:
                try:
                    self.recorder_dialog.close()
                    self.recorder_dialog.deleteLater()
                    self.recorder_dialog = None
                except Exception as e:
                    print(f"关闭对话框时出错: {e}")
                    
            # 4. 停止所有录制线程
            if hasattr(self, 'recording_threads'):
                room_ids = list(self.recording_threads.keys())
                for room_id in room_ids:
                    thread = self.recording_threads[room_id]
                    if thread.isRunning():
                        thread.stop()
                        thread.wait(1000)  # 等待最多1秒
                self.recording_threads = {}
                
        except Exception as e:
            print(f"清理UI元素时出错: {e}")
            import traceback
            traceback.print_exc()
        
        # 清理其他UI资源
        self.cleanup_ui()
        
        # 停止所有线程
        self.stop_all_threads()
        
        # 刷新界面
        try:
            from PyQt5.QtWidgets import QApplication
            QApplication.processEvents()
        except:
            pass
            
        # 保存禁用状态到配置文件
        try:
            if hasattr(self.app, 'plugin_manager') and hasattr(self.app.plugin_manager, 'save_settings'):
                print("尝试保存插件禁用状态")
                # 确保插件ID在启用列表中，但值为False
                plugin_id = self.app.plugin_manager.get_plugin_id(self)
                self.app.plugin_manager.enabled_plugins[plugin_id] = False
                self.app.plugin_manager.save_settings()
                print(f"已保存插件禁用状态，插件ID: {plugin_id}")
        except Exception as e:
            print(f"保存插件禁用状态时出错: {e}")
            import traceback
            traceback.print_exc()
            
        return True
    def select_all_history(self):
        """全选历史记录"""
        for row in range(self.history_table.rowCount()):
            cell_widget = self.history_table.cellWidget(row, 0)
            if cell_widget:
                check_box = cell_widget.findChild(QCheckBox)
                if check_box:
                    check_box.setChecked(True)
    
    def deselect_all_history(self):
        """取消选择所有历史记录"""
        for row in range(self.history_table.rowCount()):
            cell_widget = self.history_table.cellWidget(row, 0)
            if cell_widget:
                check_box = cell_widget.findChild(QCheckBox)
                if check_box:
                    check_box.setChecked(False)
    
    def delete_selected_history(self):
        """删除选中的历史记录"""
        # 收集选中的行
        selected_rows = []
        for row in range(self.history_table.rowCount()):
            cell_widget = self.history_table.cellWidget(row, 0)
            if cell_widget:
                check_box = cell_widget.findChild(QCheckBox)
                if check_box and check_box.isChecked():
                    selected_rows.append(row)
        
        if not selected_rows:
            QMessageBox.information(self.recorder_dialog, "未选择", "请先选择要删除的记录")
            return
        
        # 确认删除
        reply = QMessageBox.question(
            self.recorder_dialog, 
            "确认删除", 
            f"确定要删除选中的 {len(selected_rows)} 条记录吗？\n注意：这只会删除历史记录，不会删除实际文件。",
            QMessageBox.Yes | QMessageBox.No, 
            QMessageBox.No
        )
        
        if reply != QMessageBox.Yes:
            return
        
        # 获取历史记录
        history = self.config.get("history", [])
        history_reversed = list(reversed(history))  # 倒序，与表格显示一致
        
        # 从大到小排序行号，以便从后向前删除
        selected_rows.sort(reverse=True)
        
        # 删除选中的记录
        for row in selected_rows:
            if row < len(history_reversed):
                del history_reversed[row]
        
        # 更新配置
        self.config["history"] = list(reversed(history_reversed))  # 恢复原来的顺序
        self.save_config()
        
        # 重新加载历史记录
        self.load_history()
        
        QMessageBox.information(self.recorder_dialog, "删除成功", f"已删除 {len(selected_rows)} 条历史记录")
class SafeThread(QThread):
    def __init__(self):
        super().__init__()
        self._stop_flag = False
        
    def stop(self):
        self._stop_flag = True
        
    def should_stop(self):
        return self._stop_flag
    def __del__(self):
        """在对象被销毁前确保线程安全停止"""
        try:
            self._stop_flag = True
            if self.isRunning():
                self.wait(3000)  # 等待最多3秒
        except:
            pass  # 忽略可能的异常，防止程序关闭时出错
    
    