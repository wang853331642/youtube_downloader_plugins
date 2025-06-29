import os
import time
import datetime
import json
import threading
import shutil
import traceback
import sys
import re
import requests
from urllib.parse import urlparse, quote, unquote
from PyQt5.QtCore import QThread, pyqtSignal, Qt, QSize, QTimer
from PyQt5.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QLineEdit, QGroupBox, QRadioButton, QProgressBar, QMessageBox, 
    QFileDialog, QFormLayout, QTabWidget, QTextEdit, QCheckBox, 
    QSpinBox, QComboBox, QScrollArea, QWidget, QFrame
)
from PyQt5.QtGui import QIcon, QFont, QPixmap

# 导入必要的库
import paramiko  # 用于SFTP
import socket
import stat
import smb.SMBConnection  # 用于SMB
from smb.base import SharedFile
import msal  # 用于OneDrive
import pickle
import os.path
from googleapiclient.discovery import build
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
import io

# 添加父目录到路径
sys.path.append(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

# 导入插件基类
try:
    from youtube_downloader import PluginBase
except ImportError:
    try:
        import importlib
        PluginBase = importlib.import_module('youtube_downloader').PluginBase
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

# 同步线程类
class MediaSyncThread(QThread):
    """媒体库同步线程"""
    progress_updated = pyqtSignal(int, str)
    sync_complete = pyqtSignal(bool, str, int, int)  # 成功标志, 消息, 已同步文件数, 已同步总大小(字节)
    
    def __init__(self, source_dir, target_dir, sync_mode, exclude_exts=None):
        super().__init__()
        self.source_dir = source_dir
        self.target_dir = target_dir
        self.sync_mode = sync_mode  # 'upload', 'download', 'bidirectional'
        self.exclude_exts = exclude_exts or []
        self.running = True
        self.total_files = 0  # 总文件数
        self.processed_files = 0  # 已处理文件数
        self.synced_files = 0  # 实际同步的文件数
        self.synced_size = 0  # 实际同步的文件总大小(字节)
        self.synced_files_list = []  # 记录已同步的文件列表，用于计算总大小
        self.verified_total_size = 0  # 添加这个新属性
    def run(self):
        try:
            self.progress_updated.emit(0, "开始同步...")
            self.synced_files = 0
            self.synced_size = 0
            self.synced_files_list = []
            
            # 确保目标目录存在
            if not os.path.exists(self.target_dir):
                os.makedirs(self.target_dir, exist_ok=True)
            
            # 获取源目录和目标目录的文件列表
            source_files = self._get_files_info(self.source_dir)
            target_files = self._get_files_info(self.target_dir)
            
            # 根据同步模式确定需要处理的文件总数
            if self.sync_mode == 'upload':
                self.total_files = len(source_files)
            elif self.sync_mode == 'download':
                self.total_files = len(target_files)
            else:  # bidirectional
                self.total_files = len(set(list(source_files.keys()) + list(target_files.keys())))
            
            self.processed_files = 0
            
            # 根据同步模式执行不同的同步策略
            if self.sync_mode == 'upload':
                # 上传模式：将源目录文件复制到目标目录
                for rel_path, file_info in source_files.items():
                    if not self.running:
                        break
                    
                    # 检查是否需要排除该文件
                    if any(rel_path.lower().endswith(ext.lower()) for ext in self.exclude_exts):
                        self.processed_files += 1
                        continue
                    
                    source_path = os.path.join(self.source_dir, rel_path)
                    target_path = os.path.join(self.target_dir, rel_path)
                    
                    # 确保目标目录存在
                    os.makedirs(os.path.dirname(target_path), exist_ok=True)
                    
                    # 检查目标文件是否存在或需要更新
                    if rel_path not in target_files or target_files[rel_path]['mtime'] < file_info['mtime']:
                        self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在复制: {rel_path}")
                        try:
                            shutil.copy2(source_path, target_path)
                            self.synced_files += 1
                            self.synced_size += file_info['size']
                            self.synced_files_list.append(source_path)
                        except OSError as e:
                            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"复制失败: {rel_path} - {str(e)}")
                            print(f"复制文件失败: {source_path} -> {target_path}, 错误: {e}")
                    
                    self.processed_files += 1
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
            
            elif self.sync_mode == 'download':
                # 下载模式：将目标目录文件复制到源目录
                for rel_path, file_info in target_files.items():
                    if not self.running:
                        break
                    
                    # 检查是否需要排除该文件
                    if any(rel_path.lower().endswith(ext.lower()) for ext in self.exclude_exts):
                        self.processed_files += 1
                        continue
                    
                    source_path = os.path.join(self.source_dir, rel_path)
                    target_path = os.path.join(self.target_dir, rel_path)
                    
                    # 确保源目录存在
                    os.makedirs(os.path.dirname(source_path), exist_ok=True)
                    
                    # 检查源文件是否存在或需要更新
                    if rel_path not in source_files or source_files[rel_path]['mtime'] < file_info['mtime']:
                        self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在复制: {rel_path}")
                        try:
                            shutil.copy2(target_path, source_path)
                            self.synced_files += 1
                            self.synced_size += file_info['size']
                            self.synced_files_list.append(target_path)
                        except OSError as e:
                            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"复制失败: {rel_path} - {str(e)}")
                            print(f"复制文件失败: {target_path} -> {source_path}, 错误: {e}")
                    
                    self.processed_files += 1
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
            
            elif self.sync_mode == 'bidirectional':
                # 双向同步：保留最新版本的文件
                all_files = set(list(source_files.keys()) + list(target_files.keys()))
                
                for rel_path in all_files:
                    if not self.running:
                        break
                    
                    # 检查是否需要排除该文件
                    if any(rel_path.lower().endswith(ext.lower()) for ext in self.exclude_exts):
                        self.processed_files += 1
                        continue
                    
                    source_path = os.path.join(self.source_dir, rel_path)
                    target_path = os.path.join(self.target_dir, rel_path)
                    
                    # 如果文件只在源目录存在，复制到目标目录
                    if rel_path in source_files and rel_path not in target_files:
                        os.makedirs(os.path.dirname(target_path), exist_ok=True)
                        self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在复制到目标目录: {rel_path}")
                        try:
                            shutil.copy2(source_path, target_path)
                            self.synced_files += 1
                            self.synced_size += source_files[rel_path]['size']
                            self.synced_files_list.append(source_path)
                        except OSError as e:
                            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"复制失败: {rel_path} - {str(e)}")
                            print(f"复制文件失败: {source_path} -> {target_path}, 错误: {e}")
                    
                    # 如果文件只在目标目录存在，复制到源目录
                    elif rel_path in target_files and rel_path not in source_files:
                        os.makedirs(os.path.dirname(source_path), exist_ok=True)
                        self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在复制到源目录: {rel_path}")
                        try:
                            shutil.copy2(target_path, source_path)
                            self.synced_files += 1
                            self.synced_size += target_files[rel_path]['size']
                            self.synced_files_list.append(target_path)
                        except OSError as e:
                            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"复制失败: {rel_path} - {str(e)}")
                            print(f"复制文件失败: {target_path} -> {source_path}, 错误: {e}")
                    
                    # 如果文件在两个目录都存在，保留最新的版本
                    elif rel_path in source_files and rel_path in target_files:
                        if source_files[rel_path]['mtime'] > target_files[rel_path]['mtime']:
                            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在更新目标文件: {rel_path}")
                            try:
                                shutil.copy2(source_path, target_path)
                                self.synced_files += 1
                                self.synced_size += source_files[rel_path]['size']
                                self.synced_files_list.append(source_path)
                            except OSError as e:
                                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"更新失败: {rel_path} - {str(e)}")
                                print(f"更新文件失败: {source_path} -> {target_path}, 错误: {e}")
                        elif source_files[rel_path]['mtime'] < target_files[rel_path]['mtime']:
                            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在更新源文件: {rel_path}")
                            try:
                                shutil.copy2(target_path, source_path)
                                self.synced_files += 1
                                self.synced_size += target_files[rel_path]['size']
                                self.synced_files_list.append(target_path)
                            except OSError as e:
                                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"更新失败: {rel_path} - {str(e)}")
                                print(f"更新文件失败: {target_path} -> {source_path}, 错误: {e}")
                    
                    self.processed_files += 1
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
            
            # 同步完成后，验证总大小
            verified_size = self._verify_total_size()
            self.verified_total_size = verified_size 
            if self.running:
                # 使用与MediaSyncPlugin._format_size相同的格式化方式
                size_mb = verified_size / (1024 * 1024)
                if size_mb > 1000:
                    size_str = f"{size_mb/1024:.2f} GB"
                else:
                    size_str = f"{size_mb:.2f} MB"
                
                print(f"发送同步完成信号，文件数: {self.synced_files}, 大小: {verified_size} 字节")  # 添加这行日志
                self.progress_updated.emit(100, f"同步完成! 已同步 {self.synced_files} 个文件, 总大小 {size_str}")
                self.sync_complete.emit(True, "同步成功完成", self.synced_files, verified_size) 
            else:
                self.progress_updated.emit(0, "同步已取消")
                self.sync_complete.emit(False, "同步已取消", self.synced_files, verified_size)
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                # 尝试在异常情况下也验证总大小
                verified_size = self._verify_total_size()
                self.progress_updated.emit(0, f"同步出错: {str(e)}")
                self.sync_complete.emit(False, f"同步失败: {str(e)}", self.synced_files, verified_size)
            except:
                # 如果验证总大小也失败，则使用当前值
                self.progress_updated.emit(0, f"同步出错: {str(e)}")
                self.sync_complete.emit(False, f"同步失败: {str(e)}", self.synced_files, self.synced_size)
    
    def _verify_total_size(self):
        """验证总大小是否正确，如果不正确则重新计算"""
        # 总是重新计算总大小
        total_size = 0
        valid_files = []
        
        print(f"开始验证总大小，同步文件列表长度: {len(self.synced_files_list)}")
        
        for file_path in self.synced_files_list:
            if os.path.exists(file_path):
                try:
                    file_size = os.path.getsize(file_path)
                    total_size += file_size
                    valid_files.append(file_path)
                except (OSError, IOError) as e:
                    print(f"获取文件大小失败: {file_path}, 错误: {e}")
        
        # 更新同步文件列表，只保留有效文件
        self.synced_files_list = valid_files
        self.synced_size = total_size  # 使用验证后的总大小更新synced_size
        self.synced_files = len(valid_files)  # 使用验证后的文件数更新synced_files
        
        print(f"验证后的总大小: {total_size} 字节, 有效文件数: {len(valid_files)}")
        print(f"格式化后的大小: {total_size / (1024 * 1024):.2f} MB 或 {total_size / (1024 * 1024 * 1024):.2f} GB")
        return total_size  # 返回验证后的总大小
    
    def stop(self):
        """停止同步过程"""
        self.running = False
    
    def _get_files_info(self, directory):
        """获取目录中所有文件的相对路径和修改时间"""
        files_info = {}
        
        for root, _, files in os.walk(directory):
            for file in files:
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, directory)
                
                # 获取文件修改时间和大小
                stat = os.stat(file_path)
                files_info[rel_path] = {
                    'mtime': stat.st_mtime,
                    'size': stat.st_size
                }
        
        return files_info

# WebDAV同步线程类
class WebDAVSyncThread(QThread):
    """WebDAV同步线程"""
    progress_updated = pyqtSignal(int, str)
    sync_complete = pyqtSignal(bool, str, int, int)  # 成功标志, 消息, 已同步文件数, 已同步总大小(字节)
    
    def __init__(self, local_dir, webdav_url, username, password, sync_mode, exclude_exts=None):
        super().__init__()
        self.local_dir = local_dir
        self.webdav_url = webdav_url
        self.username = username
        self.password = password
        self.sync_mode = sync_mode  # 'upload', 'download', 'bidirectional'
        self.exclude_exts = exclude_exts or []
        self.running = True
        self.auth = (self.username, self.password)
        self.max_retries = 3  # 最大重试次数
        self.chunk_size = 8 * 1024 * 1024  # 8MB 分块上传大小
        self.total_files = 0  # 总文件数
        self.processed_files = 0  # 已处理文件数
        self.synced_files = 0  # 实际同步的文件数
        self.synced_size = 0  # 实际同步的文件总大小(字节)
        self.synced_files_list = []  # 记录已同步的文件列表，用于计算总大小
        self.verified_total_size = 0  # 添加这个新属性
    
    def run(self):
        try:
            self.progress_updated.emit(0, "开始WebDAV同步...")
            self.synced_files = 0
            self.synced_size = 0
            self.synced_files_list = []
            
            # 确保本地目录存在
            if not os.path.exists(self.local_dir):
                os.makedirs(self.local_dir, exist_ok=True)
            
            # 测试WebDAV连接
            self.progress_updated.emit(5, "测试WebDAV连接...")
            if not self._test_webdav_connection():
                self.progress_updated.emit(0, "WebDAV连接失败")
                self.sync_complete.emit(False, "WebDAV连接失败，请检查URL和认证信息", 0, 0)
                return
            
            # 获取本地文件列表
            self.progress_updated.emit(10, "获取本地文件列表...")
            local_files = self._get_local_files()
            
            # 获取远程文件列表
            self.progress_updated.emit(20, "获取远程文件列表...")
            remote_files = self._get_remote_files()
            
            if not self.running:
                self.progress_updated.emit(0, "同步已取消")
                self.sync_complete.emit(False, "同步已取消", self.synced_files, self.synced_size)
                return
            
            # 根据同步模式确定需要处理的文件总数
            if self.sync_mode == 'upload':
                self.total_files = len(local_files)
            elif self.sync_mode == 'download':
                self.total_files = len(remote_files)
            else:  # bidirectional
                self.total_files = len(set(list(local_files.keys()) + list(remote_files.keys())))
            
            self.processed_files = 0
            
            # 根据同步模式执行不同的同步策略
            if self.sync_mode == 'upload':
                self._upload_files(local_files, remote_files)
            elif self.sync_mode == 'download':
                self._download_files(local_files, remote_files)
            elif self.sync_mode == 'bidirectional':
                self._bidirectional_sync(local_files, remote_files)
            
            # 同步完成后，验证总大小
            verified_size = self._verify_total_size()
            self.verified_total_size = verified_size  # 确保保存验证后的大小
            self.synced_size = verified_size 
            if self.running:
                # 使用与MediaSyncPlugin._format_size相同的格式化方式
                size_mb = verified_size / (1024 * 1024)
                if size_mb > 1000:
                    size_str = f"{size_mb/1024:.2f} GB"
                else:
                    size_str = f"{size_mb:.2f} MB"
                
                self.progress_updated.emit(100, f"WebDAV同步完成! 已同步 {self.synced_files} 个文件, 总大小 {size_str}")
                self.sync_complete.emit(True, "WebDAV同步成功完成", self.synced_files, verified_size)
            else:
                self.progress_updated.emit(0, "WebDAV同步已取消")
                self.sync_complete.emit(False, "WebDAV同步已取消", self.synced_files, verified_size)
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                # 尝试在异常情况下也验证总大小
                verified_size = self._verify_total_size()
                self.progress_updated.emit(0, f"WebDAV同步出错: {str(e)}")
                self.sync_complete.emit(False, f"WebDAV同步失败: {str(e)}", self.synced_files, verified_size)
            except:
                # 如果验证总大小也失败，则使用当前值
                self.progress_updated.emit(0, f"WebDAV同步出错: {str(e)}")
                self.sync_complete.emit(False, f"WebDAV同步失败: {str(e)}", self.synced_files, self.synced_size)
    
    def _verify_total_size(self):
        """验证总大小是否正确，如果不正确则重新计算"""
        # 总是重新计算总大小
        total_size = 0
        valid_files = []
        
        print(f"开始验证总大小，同步文件列表长度: {len(self.synced_files_list)}")
        
        for file_path in self.synced_files_list:
            if os.path.exists(file_path):
                try:
                    file_size = os.path.getsize(file_path)
                    total_size += file_size
                    valid_files.append(file_path)
                except (OSError, IOError) as e:
                    print(f"获取文件大小失败: {file_path}, 错误: {e}")
        
        # 更新同步文件列表，只保留有效文件
        self.synced_files_list = valid_files
        self.synced_size = total_size  # 使用验证后的总大小更新synced_size
        self.synced_files = len(valid_files)  # 使用验证后的文件数更新synced_files
        self.verified_total_size = total_size  # 保存验证后的总大小
        
        print(f"验证后的总大小: {total_size} 字节, 有效文件数: {len(valid_files)}")
        print(f"格式化后的大小: {total_size / (1024 * 1024):.2f} MB 或 {total_size / (1024 * 1024 * 1024):.2f} GB")
        return total_size  # 返回验证后的总大小
    
    def stop(self):
        """停止同步过程"""
        self.running = False
    
    def _test_webdav_connection(self):
        """测试WebDAV连接"""
        try:
            for retry in range(self.max_retries):
                try:
                    response = requests.request("PROPFIND", self.webdav_url, auth=self.auth, headers={"Depth": "0"}, timeout=15)
                    return response.status_code in [200, 207]
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                    if retry < self.max_retries - 1:
                        self.progress_updated.emit(5, f"连接失败，正在重试 ({retry+1}/{self.max_retries})...")
                        time.sleep(2)  # 等待2秒后重试
                    else:
                        print(f"WebDAV连接测试失败: {e}")
                        return False
            return False
        except Exception as e:
            print(f"WebDAV连接测试失败: {e}")
            return False
    
    def _get_local_files(self):
        """获取本地文件列表"""
        files_info = {}
        
        for root, _, files in os.walk(self.local_dir):
            for file in files:
                # 检查是否需要排除该文件
                if any(file.lower().endswith(ext.lower()) for ext in self.exclude_exts):
                    continue
                
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, self.local_dir).replace('\\', '/')
                
                # 获取文件修改时间和大小
                stat = os.stat(file_path)
                files_info[rel_path] = {
                    'mtime': stat.st_mtime,
                    'size': stat.st_size,
                    'local_path': file_path
                }
        
        return files_info
    
    def _get_remote_files(self):
        """获取远程WebDAV文件列表"""
        files_info = {}
        
        try:
            # 递归获取所有文件
            self._list_webdav_files(self.webdav_url, "", files_info)
        except Exception as e:
            print(f"获取WebDAV文件列表失败: {e}")
        
        return files_info
    
    def _list_webdav_files(self, base_url, rel_path, files_info):
        """递归列出WebDAV目录中的所有文件"""
        if not self.running:
            return
        
        url = base_url
        if rel_path:
            # 确保rel_path已被正确编码
            url = f"{base_url.rstrip('/')}/{quote(rel_path)}"
        
        try:
            for retry in range(self.max_retries):
                try:
                    response = requests.request("PROPFIND", url, auth=self.auth, headers={"Depth": "1"}, timeout=15)
                    
                    if response.status_code not in [200, 207]:
                        if retry < self.max_retries - 1:
                            time.sleep(2)  # 等待2秒后重试
                            continue
                        return
                    
                    # 解析XML响应
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(response.content, 'xml')
                    
                    # 获取所有响应项
                    responses = soup.find_all('response')
                    
                    for resp in responses:
                        href = resp.find('href').text
                        parsed_url = urlparse(href)
                        path = parsed_url.path
                        
                        # 移除基本URL路径部分，获取相对路径
                        server_path = urlparse(base_url).path
                        if path.startswith(server_path):
                            item_rel_path = path[len(server_path):].lstrip('/')
                        else:
                            item_rel_path = path.lstrip('/')
                        
                        # 尝试对URL编码的路径进行解码
                        try:
                            item_rel_path = unquote(item_rel_path)
                        except:
                            pass
                        
                        # 跳过当前目录
                        if not item_rel_path or item_rel_path == rel_path:
                            continue
                        
                        # 检查是否是集合(目录)
                        is_collection = resp.find('resourcetype') and resp.find('resourcetype').find('collection')
                        
                        if is_collection:
                            # 递归处理子目录
                            self._list_webdav_files(base_url, item_rel_path, files_info)
                        else:
                            # 检查是否需要排除该文件
                            if any(item_rel_path.lower().endswith(ext.lower()) for ext in self.exclude_exts):
                                continue
                            
                            # 获取文件属性
                            last_modified = resp.find('getlastmodified')
                            content_length = resp.find('getcontentlength')
                            
                            mtime = 0
                            size = 0
                            
                            if last_modified:
                                try:
                                    # 解析HTTP日期格式
                                    time_str = last_modified.text
                                    time_struct = time.strptime(time_str, "%a, %d %b %Y %H:%M:%S GMT")
                                    mtime = time.mktime(time_struct)
                                except:
                                    pass
                            
                            if content_length:
                                try:
                                    size = int(content_length.text)
                                except:
                                    pass
                            
                            # 确保远程URL是完整的
                            remote_url = href
                            # 检查URL是否完整
                            if not remote_url.startswith(('http://', 'https://')):
                                # 构建完整URL
                                parsed_base = urlparse(base_url)
                                base_url_prefix = f"{parsed_base.scheme}://{parsed_base.netloc}"
                                if remote_url.startswith('/'):
                                    remote_url = f"{base_url_prefix}{remote_url}"
                                else:
                                    remote_url = f"{base_url_prefix}/{remote_url}"
                            
                            # 存储远程文件信息
                            files_info[item_rel_path] = {
                                'mtime': mtime,
                                'size': size,
                                'remote_url': remote_url
                            }
                    
                    # 成功获取数据，跳出重试循环
                    break
                
                except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                    if retry < self.max_retries - 1:
                        self.progress_updated.emit(20, f"获取文件列表失败，正在重试 ({retry+1}/{self.max_retries})...")
                        time.sleep(2)  # 等待2秒后重试
                    else:
                        print(f"列出WebDAV目录 {rel_path} 失败: {e}")
        
        except Exception as e:
            print(f"列出WebDAV目录 {rel_path} 失败: {e}")
    
    def _upload_files(self, local_files, remote_files):
        """上传本地文件到WebDAV服务器"""
        self.processed_files = 0
        
        for rel_path, file_info in local_files.items():
            if not self.running:
                break
            
            remote_url = f"{self.webdav_url.rstrip('/')}/{quote(rel_path)}"
            
            # 检查是否需要上传（文件不存在或较新）
            if rel_path not in remote_files or remote_files[rel_path]['mtime'] < file_info['mtime']:
                file_size = file_info['size']
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传: {rel_path}")
                
                # 确保远程目录存在
                remote_dir = os.path.dirname(rel_path)
                if remote_dir:
                    self._ensure_remote_dir(remote_dir)
                
                # 上传文件
                upload_success = False
                if file_size > 50 * 1024 * 1024:  # 大于50MB的文件使用分块上传
                    upload_success = self._upload_large_file(file_info['local_path'], remote_url, rel_path)
                else:
                    upload_success = self._upload_file_with_retry(file_info['local_path'], remote_url, rel_path)
                
                # 如果上传成功，更新统计信息
                if upload_success:
                    self.synced_files += 1
                    self.synced_size += file_size
                    self.synced_files_list.append(file_info['local_path'])
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _download_files(self, local_files, remote_files):
        """从WebDAV服务器下载文件"""
        self.processed_files = 0
        
        for rel_path, file_info in remote_files.items():
            if not self.running:
                break
            
            # 解码路径用于创建本地文件
            try:
                if '%' in rel_path:
                    decoded_rel_path = unquote(rel_path)
                    local_path = os.path.join(self.local_dir, decoded_rel_path)
                else:
                    local_path = os.path.join(self.local_dir, rel_path)
            except:
                local_path = os.path.join(self.local_dir, rel_path)
            
            remote_url = file_info['remote_url']
            
            # 检查是否需要下载（文件不存在或较新）
            if rel_path not in local_files or local_files[rel_path]['mtime'] < file_info['mtime']:
                # 发送进度更新时，尝试解码URL编码的路径
                try:
                    decoded_path = unquote(rel_path)
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载: {decoded_path}")
                except:
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载: {rel_path}")
                
                # 确保本地目录存在
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                
                # 下载文件
                file_size = file_info['size']
                download_success = False
                if file_size > 50 * 1024 * 1024:  # 大于50MB的文件使用分块下载
                    download_success = self._download_large_file(remote_url, local_path, rel_path, file_size)
                else:
                    download_success = self._download_file_with_retry(remote_url, local_path, rel_path)
                
                # 如果下载成功，更新统计信息
                if download_success:
                    self.synced_files += 1
                    self.synced_size += file_size
                    self.synced_files_list.append(local_path)
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _bidirectional_sync(self, local_files, remote_files):
        """双向同步本地和远程文件"""
        all_files = set(list(local_files.keys()) + list(remote_files.keys()))
        self.processed_files = 0
        
        for rel_path in all_files:
            if not self.running:
                break
            
            local_path = os.path.join(self.local_dir, rel_path)
            remote_url = f"{self.webdav_url.rstrip('/')}/{quote(rel_path)}"
            
            # 尝试解码路径用于显示
            try:
                decoded_path = unquote(rel_path)
            except:
                decoded_path = rel_path
            
            # 如果文件只在本地存在，上传到远程
            if rel_path in local_files and rel_path not in remote_files:
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传: {decoded_path}")
                
                # 确保远程目录存在
                remote_dir = os.path.dirname(rel_path)
                if remote_dir:
                    self._ensure_remote_dir(remote_dir)
                
                # 上传文件
                file_size = local_files[rel_path]['size']
                upload_success = False
                if file_size > 50 * 1024 * 1024:  # 大于50MB的文件使用分块上传
                    upload_success = self._upload_large_file(local_files[rel_path]['local_path'], remote_url, rel_path)
                else:
                    upload_success = self._upload_file_with_retry(local_files[rel_path]['local_path'], remote_url, rel_path)
                
                # 如果上传成功，更新统计信息
                if upload_success:
                    self.synced_files += 1
                    self.synced_size += file_size
                    self.synced_files_list.append(local_files[rel_path]['local_path'])
            
            # 如果文件只在远程存在，下载到本地
            elif rel_path in remote_files and rel_path not in local_files:
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载: {decoded_path}")
                
                # 确保本地目录存在
                os.makedirs(os.path.dirname(local_path), exist_ok=True)
                
                # 下载文件
                file_size = remote_files[rel_path]['size']
                download_success = False
                if file_size > 50 * 1024 * 1024:  # 大于50MB的文件使用分块下载
                    download_success = self._download_large_file(remote_files[rel_path]['remote_url'], local_path, rel_path, file_size)
                else:
                    download_success = self._download_file_with_retry(remote_files[rel_path]['remote_url'], local_path, rel_path)
                
                # 如果下载成功，更新统计信息
                if download_success:
                    self.synced_files += 1
                    self.synced_size += file_size
                    self.synced_files_list.append(local_path)
            
            # 如果文件在两边都存在，比较修改时间
            elif rel_path in local_files and rel_path in remote_files:
                if local_files[rel_path]['mtime'] > remote_files[rel_path]['mtime']:
                    # 本地文件较新，上传到远程
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传较新文件: {decoded_path}")
                    
                    file_size = local_files[rel_path]['size']
                    upload_success = False
                    if file_size > 50 * 1024 * 1024:  # 大于50MB的文件使用分块上传
                        upload_success = self._upload_large_file(local_files[rel_path]['local_path'], remote_url, rel_path)
                    else:
                        upload_success = self._upload_file_with_retry(local_files[rel_path]['local_path'], remote_url, rel_path)
                    
                    # 如果上传成功，更新统计信息
                    if upload_success:
                        self.synced_files += 1
                        self.synced_size += file_size
                        self.synced_files_list.append(local_files[rel_path]['local_path'])
                
                elif local_files[rel_path]['mtime'] < remote_files[rel_path]['mtime']:
                    # 远程文件较新，下载到本地
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载较新文件: {decoded_path}")
                    
                    file_size = remote_files[rel_path]['size']
                    download_success = False
                    if file_size > 50 * 1024 * 1024:  # 大于50MB的文件使用分块下载
                        download_success = self._download_large_file(remote_files[rel_path]['remote_url'], local_path, rel_path, file_size)
                    else:
                        download_success = self._download_file_with_retry(remote_files[rel_path]['remote_url'], local_path, rel_path)
                    
                    # 如果下载成功，更新统计信息
                    if download_success:
                        self.synced_files += 1
                        self.synced_size += file_size
                        self.synced_files_list.append(local_path)
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _upload_file_with_retry(self, local_path, remote_url, rel_path):
        """带重试机制的文件上传"""
        for retry in range(self.max_retries):
            try:
                with open(local_path, 'rb') as f:
                    response = requests.put(remote_url, data=f, auth=self.auth, timeout=60)
                    if response.status_code in [200, 201, 204]:
                        return True
                    else:
                        print(f"上传文件 {rel_path} 失败: {response.status_code}")
                        if retry < self.max_retries - 1:
                            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"上传失败，正在重试 ({retry+1}/{self.max_retries}): {rel_path}")
                            time.sleep(2)  # 等待2秒后重试
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                print(f"上传文件 {rel_path} 出错: {e}")
                if retry < self.max_retries - 1:
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"上传出错，正在重试 ({retry+1}/{self.max_retries}): {rel_path}")
                    time.sleep(2)  # 等待2秒后重试
        
        return False
    
    def _upload_large_file(self, local_path, remote_url, rel_path):
        """分块上传大文件"""
        file_size = os.path.getsize(local_path)
        
        try:
            with open(local_path, 'rb') as f:
                uploaded = 0
                while uploaded < file_size and self.running:
                    chunk = f.read(self.chunk_size)
                    if not chunk:
                        break
                    
                    # 计算当前块的范围
                    chunk_end = min(uploaded + len(chunk) - 1, file_size - 1)
                    content_range = f"bytes {uploaded}-{chunk_end}/{file_size}"
                    
                    # 上传当前块
                    success = False
                    for retry in range(self.max_retries):
                        try:
                            headers = {
                                "Content-Range": content_range,
                                "Content-Type": "application/octet-stream"
                            }
                            
                            response = requests.put(
                                remote_url, 
                                data=chunk, 
                                headers=headers,
                                auth=self.auth,
                                timeout=60
                            )
                            
                            if response.status_code in [200, 201, 204, 308]:
                                success = True
                                break
                            else:
                                print(f"上传文件块 {rel_path} ({content_range}) 失败: {response.status_code}")
                                if retry < self.max_retries - 1:
                                    self.progress_updated.emit(0, f"块上传失败，正在重试 ({retry+1}/{self.max_retries}): {rel_path}")
                                    time.sleep(2)
                        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                            print(f"上传文件块 {rel_path} ({content_range}) 出错: {e}")
                            if retry < self.max_retries - 1:
                                self.progress_updated.emit(0, f"块上传出错，正在重试 ({retry+1}/{self.max_retries}): {rel_path}")
                                time.sleep(2)
                    
                    if not success:
                        return False
                    
                    uploaded += len(chunk)
                    progress = int(uploaded * 100 / file_size)
                    self.progress_updated.emit(0, f"正在上传 ({progress}%): {rel_path}")
            
            return True
        except Exception as e:
            print(f"分块上传文件 {rel_path} 失败: {e}")
            return False
    
    def _download_file_with_retry(self, remote_url, local_path, rel_path):
        """带重试机制的文件下载"""
        for retry in range(self.max_retries):
            try:
                # 打印调试信息
                print(f"正在下载文件，URL: {remote_url}")
                
                # 检查URL是否完整
                if not remote_url.startswith(('http://', 'https://')):
                    # 如果URL不完整（例如只有路径部分），尝试使用webdav_url构建完整URL
                    if remote_url.startswith('/'):
                        parsed_base = urlparse(self.webdav_url)
                        base_url = f"{parsed_base.scheme}://{parsed_base.netloc}"
                        remote_url = f"{base_url}{remote_url}"
                    else:
                        remote_url = f"{self.webdav_url.rstrip('/')}/{remote_url}"
                    print(f"URL不完整，已修正为: {remote_url}")
                
                # 处理可能的URL编码问题 - 避免重复编码
                try:
                    parsed = urlparse(remote_url)
                    
                    # 确保协议和主机名存在
                    if not parsed.scheme or not parsed.netloc:
                        parsed_base = urlparse(self.webdav_url)
                        scheme = parsed.scheme or parsed_base.scheme
                        netloc = parsed.netloc or parsed_base.netloc
                        
                        # 使用原始路径，不进行重复编码
                        remote_url = f"{scheme}://{netloc}{parsed.path}"
                    
                except Exception as e:
                    print(f"URL修正失败: {e}")
                    # 尝试使用原始rel_path构建URL，但不进行额外编码
                    try:
                        parsed_base = urlparse(self.webdav_url)
                        base_url = f"{parsed_base.scheme}://{parsed_base.netloc}"
                        # 检查rel_path是否已经编码
                        if '%' in rel_path:
                            remote_url = f"{base_url}/{rel_path}"
                        else:
                            remote_url = f"{base_url}/{quote(rel_path)}"
                        print(f"使用备用方法构建URL: {remote_url}")
                    except Exception as e2:
                        print(f"备用URL构建失败: {e2}")
                
                # 下载文件
                response = requests.get(remote_url, auth=self.auth, stream=True, timeout=60)
                if response.status_code == 200:
                    with open(local_path, 'wb') as f:
                        for chunk in response.iter_content(chunk_size=8192):
                            if not self.running:
                                return False
                            f.write(chunk)
                    return True
                else:
                    print(f"下载文件 {rel_path} 失败: {response.status_code}")
                    
                    # 尝试替代方法 - 直接使用原始相对路径构建URL
                    if retry == 0:  # 只在第一次重试时尝试
                        try:
                            parsed_base = urlparse(self.webdav_url)
                            base_url = f"{parsed_base.scheme}://{parsed_base.netloc}"
                            # 检查rel_path是否已经编码
                            if '%' in rel_path:
                                alt_url = f"{base_url}/{rel_path}"
                            else:
                                alt_url = f"{base_url}/{quote(rel_path)}"
                            print(f"尝试替代URL: {alt_url}")
                            
                            alt_response = requests.get(alt_url, auth=self.auth, stream=True, timeout=60)
                            if alt_response.status_code == 200:
                                with open(local_path, 'wb') as f:
                                    for chunk in alt_response.iter_content(chunk_size=8192):
                                        if not self.running:
                                            return False
                                        f.write(chunk)
                                return True
                            else:
                                print(f"替代URL下载失败: {alt_response.status_code}")
                        except Exception as e:
                            print(f"替代URL下载出错: {e}")
                    
                    if retry < self.max_retries - 1:
                        # 尝试解码路径用于显示
                        try:
                            decoded_path = unquote(rel_path)
                            self.progress_updated.emit(0, f"下载失败，正在重试 ({retry+1}/{self.max_retries}): {decoded_path}")
                        except:
                            self.progress_updated.emit(0, f"下载失败，正在重试 ({retry+1}/{self.max_retries}): {rel_path}")
                        time.sleep(2)  # 等待2秒后重试
            except (requests.exceptions.ConnectionError, requests.exceptions.Timeout, requests.exceptions.InvalidSchema) as e:
                print(f"下载文件 {rel_path} 出错: {e}")
                
                # 如果是URL格式错误，尝试修复
                if isinstance(e, requests.exceptions.InvalidSchema):
                    try:
                        parsed_base = urlparse(self.webdav_url)
                        base_url = f"{parsed_base.scheme}://{parsed_base.netloc}"
                        # 检查rel_path是否已经编码
                        if '%' in rel_path:
                            remote_url = f"{base_url}/{rel_path}"
                        else:
                            remote_url = f"{base_url}/{quote(rel_path)}"
                        print(f"尝试修复无效URL格式: {remote_url}")
                    except Exception as fix_error:
                        print(f"修复URL格式失败: {fix_error}")
                
                if retry < self.max_retries - 1:
                    # 尝试解码路径用于显示
                    try:
                        decoded_path = unquote(rel_path)
                        self.progress_updated.emit(0, f"下载出错，正在重试 ({retry+1}/{self.max_retries}): {decoded_path}")
                    except:
                        self.progress_updated.emit(0, f"下载出错，正在重试 ({retry+1}/{self.max_retries}): {rel_path}")
                    time.sleep(2)  # 等待2秒后重试
        
        return False
    
    def _download_large_file(self, remote_url, local_path, rel_path, file_size):
        """分块下载大文件"""
        try:
            # 尝试解码路径用于显示
            try:
                decoded_path = unquote(rel_path)
            except:
                decoded_path = rel_path
            
            # 确保本地路径是有效的Windows路径
            try:
                # 对于Windows系统，将URL编码的路径解码为正常的文件名
                if os.name == 'nt':
                    dir_name = os.path.dirname(local_path)
                    file_name = os.path.basename(local_path)
                    if '%' in file_name:
                        decoded_file_name = unquote(file_name)
                        local_path = os.path.join(dir_name, decoded_file_name)
            except Exception as e:
                print(f"处理本地路径失败: {e}")
                
            with open(local_path, 'wb') as f:
                downloaded = 0
                chunk_size = self.chunk_size
                
                while downloaded < file_size and self.running:
                    # 计算当前块的范围
                    chunk_end = min(downloaded + chunk_size - 1, file_size - 1)
                    headers = {"Range": f"bytes={downloaded}-{chunk_end}"}
                    
                    # 下载当前块
                    success = False
                    for retry in range(self.max_retries):
                        try:
                            response = requests.get(
                                remote_url, 
                                headers=headers,
                                auth=self.auth,
                                stream=True,
                                timeout=60
                            )
                            
                            if response.status_code in [200, 206]:
                                for chunk in response.iter_content(chunk_size=8192):
                                    if not self.running:
                                        return False
                                    f.write(chunk)
                                success = True
                                break
                            else:
                                print(f"下载文件块 {rel_path} ({headers['Range']}) 失败: {response.status_code}")
                                if retry < self.max_retries - 1:
                                    self.progress_updated.emit(0, f"块下载失败，正在重试 ({retry+1}/{self.max_retries}): {decoded_path}")
                                    time.sleep(2)
                        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                            print(f"下载文件块 {rel_path} ({headers['Range']}) 出错: {e}")
                            if retry < self.max_retries - 1:
                                self.progress_updated.emit(0, f"块下载出错，正在重试 ({retry+1}/{self.max_retries}): {decoded_path}")
                                time.sleep(2)
                    
                    if not success:
                        return False
                    
                    downloaded = chunk_end + 1
                    progress = int(downloaded * 100 / file_size)
                    self.progress_updated.emit(0, f"正在下载 ({progress}%): {decoded_path}")
            
            return True
        except Exception as e:
            print(f"分块下载文件 {rel_path} 失败: {e}")
            return False
    
    def _ensure_remote_dir(self, remote_dir):
        """确保远程目录存在"""
        parts = remote_dir.split('/')
        current_path = ""
        
        for part in parts:
            if part:
                current_path = f"{current_path}/{part}" if current_path else part
                dir_url = f"{self.webdav_url.rstrip('/')}/{quote(current_path)}"
                
                # 检查目录是否存在
                for retry in range(self.max_retries):
                    try:
                        response = requests.request("PROPFIND", dir_url, auth=self.auth, headers={"Depth": "0"}, timeout=15)
                        
                        # 如果目录不存在，创建它
                        if response.status_code == 404:
                            mkdir_response = requests.request("MKCOL", dir_url, auth=self.auth, timeout=15)
                            if mkdir_response.status_code not in [201, 405]:  # 405表示目录可能已存在
                                print(f"创建远程目录 {current_path} 失败: {mkdir_response.status_code}")
                                if retry < self.max_retries - 1:
                                    time.sleep(2)  # 等待2秒后重试
                                    continue
                        
                        # 成功检查或创建目录，跳出重试循环
                        break
                    
                    except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
                        print(f"检查/创建远程目录 {current_path} 出错: {e}")
                        if retry < self.max_retries - 1:
                            time.sleep(2)  # 等待2秒后重试
class SFTPSyncThread(QThread):
    """SFTP同步线程"""
    progress_updated = pyqtSignal(int, str)
    sync_complete = pyqtSignal(bool, str, int, int)  # 成功标志, 消息, 已同步文件数, 已同步总大小(字节)
    
    def __init__(self, local_dir, host, port, username, password, remote_dir, sync_mode, private_key_path=None, exclude_exts=None):
        super().__init__()
        self.local_dir = local_dir
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.private_key_path = private_key_path
        self.remote_dir = remote_dir
        self.sync_mode = sync_mode  # 'upload', 'download', 'bidirectional'
        self.exclude_exts = exclude_exts or []
        self.running = True
        self.max_retries = 3  # 最大重试次数
        self.chunk_size = 8 * 1024 * 1024  # 8MB 分块上传大小
        self.total_files = 0  # 总文件数
        self.processed_files = 0  # 已处理文件数
        self.synced_files = 0  # 实际同步的文件数
        self.synced_size = 0  # 实际同步的文件总大小(字节)
        self.synced_files_list = []  # 记录已同步的文件列表，用于计算总大小
        self.verified_total_size = 0  # 添加这个新属性
        self.sftp_client = None
        self.ssh_client = None
    
    def run(self):
        try:
            self.progress_updated.emit(0, "开始SFTP同步...")
            self.synced_files = 0
            self.synced_size = 0
            self.synced_files_list = []
            
            # 确保本地目录存在
            if not os.path.exists(self.local_dir):
                os.makedirs(self.local_dir, exist_ok=True)
            
            # 测试SFTP连接
            self.progress_updated.emit(5, "测试SFTP连接...")
            if not self._connect_sftp():
                self.progress_updated.emit(0, "SFTP连接失败")
                self.sync_complete.emit(False, "SFTP连接失败，请检查连接信息", 0, 0)
                return
            
            # 获取本地文件列表
            self.progress_updated.emit(10, "获取本地文件列表...")
            local_files = self._get_local_files()
            
            # 获取远程文件列表
            self.progress_updated.emit(20, "获取远程文件列表...")
            remote_files = self._get_remote_files()
            
            if not self.running:
                self.progress_updated.emit(0, "同步已取消")
                self.sync_complete.emit(False, "同步已取消", self.synced_files, self.synced_size)
                self._disconnect_sftp()
                return
            
            # 根据同步模式确定需要处理的文件总数
            if self.sync_mode == 'upload':
                self.total_files = len(local_files)
            elif self.sync_mode == 'download':
                self.total_files = len(remote_files)
            else:  # bidirectional
                self.total_files = len(set(list(local_files.keys()) + list(remote_files.keys())))
            
            self.processed_files = 0
            
            # 根据同步模式执行不同的同步策略
            if self.sync_mode == 'upload':
                self._upload_files(local_files, remote_files)
            elif self.sync_mode == 'download':
                self._download_files(local_files, remote_files)
            elif self.sync_mode == 'bidirectional':
                self._bidirectional_sync(local_files, remote_files)
            
            # 同步完成后，验证总大小
            verified_size = self._verify_total_size()
            self.verified_total_size = verified_size
            self.synced_size = verified_size
            
            # 断开SFTP连接
            self._disconnect_sftp()
            
            if self.running:
                # 格式化大小显示
                size_mb = verified_size / (1024 * 1024)
                if size_mb > 1000:
                    size_str = f"{size_mb/1024:.2f} GB"
                else:
                    size_str = f"{size_mb:.2f} MB"
                
                self.progress_updated.emit(100, f"SFTP同步完成! 已同步 {self.synced_files} 个文件, 总大小 {size_str}")
                self.sync_complete.emit(True, "SFTP同步成功完成", self.synced_files, verified_size)
            else:
                self.progress_updated.emit(0, "SFTP同步已取消")
                self.sync_complete.emit(False, "SFTP同步已取消", self.synced_files, verified_size)
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                # 尝试断开连接
                self._disconnect_sftp()
                # 尝试在异常情况下也验证总大小
                verified_size = self._verify_total_size()
                self.progress_updated.emit(0, f"同步出错: {str(e)}")
                self.sync_complete.emit(False, f"SFTP同步失败: {str(e)}", self.synced_files, verified_size)
            except:
                # 如果验证总大小也失败，则使用当前值
                self.progress_updated.emit(0, f"同步出错: {str(e)}")
                self.sync_complete.emit(False, f"SFTP同步失败: {str(e)}", self.synced_files, self.synced_size)
    
    def _connect_sftp(self):
        """连接到SFTP服务器"""
        try:
            self.ssh_client = paramiko.SSHClient()
            self.ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            
            # 使用私钥或密码进行认证
            if self.private_key_path and os.path.exists(self.private_key_path):
                try:
                    private_key = paramiko.RSAKey.from_private_key_file(self.private_key_path, password=self.password if self.password else None)
                    self.ssh_client.connect(
                        hostname=self.host,
                        port=self.port,
                        username=self.username,
                        pkey=private_key,
                        timeout=10
                    )
                except Exception as e:
                    print(f"私钥认证失败，尝试密码认证: {e}")
                    # 如果私钥认证失败，尝试密码认证
                    if self.password:
                        self.ssh_client.connect(
                            hostname=self.host,
                            port=self.port,
                            username=self.username,
                            password=self.password,
                            timeout=10
                        )
                    else:
                        return False
            else:
                # 使用密码认证
                if self.password:
                    self.ssh_client.connect(
                        hostname=self.host,
                        port=self.port,
                        username=self.username,
                        password=self.password,
                        timeout=10
                    )
                else:
                    # 尝试使用SSH代理或默认密钥
                    self.ssh_client.connect(
                        hostname=self.host,
                        port=self.port,
                        username=self.username,
                        timeout=10
                    )
            
            # 创建SFTP客户端
            self.sftp_client = self.ssh_client.open_sftp()
            
            # 测试远程目录是否存在
            try:
                self.sftp_client.stat(self.remote_dir)
            except FileNotFoundError:
                # 远程目录不存在，尝试创建
                try:
                    self._mkdir_p(self.remote_dir)
                except Exception as e:
                    print(f"创建远程目录失败: {e}")
                    return False
            
            return True
        except Exception as e:
            print(f"SFTP连接失败: {e}")
            if self.ssh_client:
                self.ssh_client.close()
            return False
    
    def _disconnect_sftp(self):
        """断开SFTP连接"""
        try:
            if self.sftp_client:
                self.sftp_client.close()
            if self.ssh_client:
                self.ssh_client.close()
        except:
            pass
    
    def _mkdir_p(self, remote_directory):
        """递归创建远程目录"""
        if remote_directory == '/':
            return
        
        if remote_directory == '':
            return
        
        try:
            self.sftp_client.stat(remote_directory)
        except IOError:
            dirname, basename = os.path.split(remote_directory.rstrip('/'))
            self._mkdir_p(dirname)
            if basename:
                try:
                    self.sftp_client.mkdir(remote_directory)
                except IOError as e:
                    if 'File exists' not in str(e):
                        raise
    
    def _get_local_files(self):
        """获取本地文件列表"""
        files_info = {}
        
        for root, _, files in os.walk(self.local_dir):
            for file in files:
                # 检查是否需要排除该文件
                if any(file.lower().endswith(ext.lower()) for ext in self.exclude_exts):
                    continue
                
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, self.local_dir).replace('\\', '/')
                
                # 获取文件修改时间和大小
                stat = os.stat(file_path)
                files_info[rel_path] = {
                    'mtime': stat.st_mtime,
                    'size': stat.st_size,
                    'local_path': file_path
                }
        
        return files_info
    
    def _get_remote_files(self):
        """获取远程SFTP文件列表"""
        files_info = {}
        
        try:
            self._list_sftp_files(self.remote_dir, "", files_info)
        except Exception as e:
            print(f"获取SFTP文件列表失败: {e}")
        
        return files_info
    
    def _list_sftp_files(self, base_dir, rel_path, files_info):
        """递归列出SFTP目录中的所有文件"""
        if not self.running:
            return
        
        full_path = os.path.join(base_dir, rel_path).replace('\\', '/')
        
        try:
            for entry in self.sftp_client.listdir_attr(full_path):
                if not self.running:
                    return
                
                # 跳过隐藏文件
                if entry.filename.startswith('.'):
                    continue
                
                entry_rel_path = os.path.join(rel_path, entry.filename).replace('\\', '/')
                entry_full_path = os.path.join(base_dir, entry_rel_path).replace('\\', '/')
                
                # 检查是否是目录
                if stat.S_ISDIR(entry.st_mode):
                    # 递归处理子目录
                    self._list_sftp_files(base_dir, entry_rel_path, files_info)
                else:
                    # 检查是否需要排除该文件
                    if any(entry.filename.lower().endswith(ext.lower()) for ext in self.exclude_exts):
                        continue
                    
                    # 存储文件信息
                    files_info[entry_rel_path] = {
                        'mtime': entry.st_mtime,
                        'size': entry.st_size,
                        'remote_path': entry_full_path
                    }
        except Exception as e:
            print(f"列出SFTP目录 {full_path} 失败: {e}")
    
    def _ensure_remote_dir(self, remote_dir):
        """确保远程目录存在"""
        if not remote_dir:
            return
        
        # 构建完整路径
        full_path = os.path.join(self.remote_dir, remote_dir).replace('\\', '/')
        
        try:
            self._mkdir_p(full_path)
            return True
        except Exception as e:
            print(f"创建远程目录失败: {full_path}, 错误: {e}")
            return False
    
    def _upload_files(self, local_files, remote_files):
        """上传本地文件到SFTP服务器"""
        self.processed_files = 0
        
        for rel_path, file_info in local_files.items():
            if not self.running:
                break
            
            remote_path = os.path.join(self.remote_dir, rel_path).replace('\\', '/')
            
            # 检查是否需要上传（文件不存在或较新）
            if rel_path not in remote_files or remote_files[rel_path]['mtime'] < file_info['mtime']:
                file_size = file_info['size']
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传: {rel_path}")
                
                # 确保远程目录存在
                remote_dir = os.path.dirname(rel_path)
                if remote_dir:
                    self._ensure_remote_dir(remote_dir)
                
                # 上传文件
                upload_success = self._upload_file_with_retry(file_info['local_path'], remote_path)
                
                # 如果上传成功，更新统计信息
                if upload_success:
                    self.synced_files += 1
                    self.synced_size += file_size
                    self.synced_files_list.append(file_info['local_path'])
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _upload_file_with_retry(self, local_path, remote_path):
        """带重试的文件上传"""
        for retry in range(self.max_retries):
            try:
                self.sftp_client.put(local_path, remote_path)
                return True
            except Exception as e:
                print(f"上传文件失败 (尝试 {retry+1}/{self.max_retries}): {local_path} -> {remote_path}, 错误: {e}")
                if retry < self.max_retries - 1:
                    time.sleep(2)  # 等待2秒后重试
                else:
                    return False
    
    def _download_files(self, local_files, remote_files):
        """从SFTP服务器下载文件"""
        self.processed_files = 0
        
        for rel_path, file_info in remote_files.items():
            if not self.running:
                break
            
            local_path = os.path.join(self.local_dir, rel_path)
            remote_path = file_info['remote_path']
            
            # 检查是否需要下载（文件不存在或较新）
            if rel_path not in local_files or local_files[rel_path]['mtime'] < file_info['mtime']:
                file_size = file_info['size']
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载: {rel_path}")
                
                # 确保本地目录存在
                local_dir = os.path.dirname(local_path)
                if not os.path.exists(local_dir):
                    os.makedirs(local_dir, exist_ok=True)
                
                # 下载文件
                download_success = self._download_file_with_retry(remote_path, local_path)
                
                # 如果下载成功，更新统计信息
                if download_success:
                    self.synced_files += 1
                    self.synced_size += file_size
                    self.synced_files_list.append(local_path)
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _download_file_with_retry(self, remote_path, local_path):
        """带重试的文件下载"""
        for retry in range(self.max_retries):
            try:
                self.sftp_client.get(remote_path, local_path)
                return True
            except Exception as e:
                print(f"下载文件失败 (尝试 {retry+1}/{self.max_retries}): {remote_path} -> {local_path}, 错误: {e}")
                if retry < self.max_retries - 1:
                    time.sleep(2)  # 等待2秒后重试
                else:
                    return False
    
    def _bidirectional_sync(self, local_files, remote_files):
        """双向同步文件"""
        self.processed_files = 0
        
        # 合并所有文件路径
        all_paths = set(list(local_files.keys()) + list(remote_files.keys()))
        
        for rel_path in all_paths:
            if not self.running:
                break
            
            # 检查文件是否存在于本地和远程
            local_exists = rel_path in local_files
            remote_exists = rel_path in remote_files
            
            # 如果文件只存在于本地，则上传
            if local_exists and not remote_exists:
                file_info = local_files[rel_path]
                remote_path = os.path.join(self.remote_dir, rel_path).replace('\\', '/')
                
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传: {rel_path}")
                
                # 确保远程目录存在
                remote_dir = os.path.dirname(rel_path)
                if remote_dir:
                    self._ensure_remote_dir(remote_dir)
                
                # 上传文件
                if self._upload_file_with_retry(file_info['local_path'], remote_path):
                    self.synced_files += 1
                    self.synced_size += file_info['size']
                    self.synced_files_list.append(file_info['local_path'])
            
            # 如果文件只存在于远程，则下载
            elif not local_exists and remote_exists:
                file_info = remote_files[rel_path]
                local_path = os.path.join(self.local_dir, rel_path)
                
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载: {rel_path}")
                
                # 确保本地目录存在
                local_dir = os.path.dirname(local_path)
                if not os.path.exists(local_dir):
                    os.makedirs(local_dir, exist_ok=True)
                
                # 下载文件
                if self._download_file_with_retry(file_info['remote_path'], local_path):
                    self.synced_files += 1
                    self.synced_size += file_info['size']
                    self.synced_files_list.append(local_path)
            
            # 如果文件同时存在于本地和远程，则比较修改时间
            elif local_exists and remote_exists:
                local_info = local_files[rel_path]
                remote_info = remote_files[rel_path]
                
                # 如果本地文件较新，则上传
                if local_info['mtime'] > remote_info['mtime']:
                    remote_path = os.path.join(self.remote_dir, rel_path).replace('\\', '/')
                    
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传较新文件: {rel_path}")
                    
                    # 上传文件
                    if self._upload_file_with_retry(local_info['local_path'], remote_path):
                        self.synced_files += 1
                        self.synced_size += local_info['size']
                        self.synced_files_list.append(local_info['local_path'])
                
                # 如果远程文件较新，则下载
                elif local_info['mtime'] < remote_info['mtime']:
                    local_path = os.path.join(self.local_dir, rel_path)
                    
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载较新文件: {rel_path}")
                    
                    # 下载文件
                    if self._download_file_with_retry(remote_info['remote_path'], local_path):
                        self.synced_files += 1
                        self.synced_size += remote_info['size']
                        self.synced_files_list.append(local_path)
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _verify_total_size(self):
        """验证总大小是否正确，如果不正确则重新计算"""
        # 总是重新计算总大小
        total_size = 0
        valid_files = []
        
        print(f"开始验证总大小，同步文件列表长度: {len(self.synced_files_list)}")
        
        for file_path in self.synced_files_list:
            if os.path.exists(file_path):
                try:
                    file_size = os.path.getsize(file_path)
                    total_size += file_size
                    valid_files.append(file_path)
                except (OSError, IOError) as e:
                    print(f"获取文件大小失败: {file_path}, 错误: {e}")
        
        # 更新同步文件列表，只保留有效文件
        self.synced_files_list = valid_files
        self.synced_size = total_size  # 使用验证后的总大小更新synced_size
        self.synced_files = len(valid_files)  # 使用验证后的文件数更新synced_files
        
        print(f"验证后的总大小: {total_size} 字节, 有效文件数: {len(valid_files)}")
        print(f"格式化后的大小: {total_size / (1024 * 1024):.2f} MB 或 {total_size / (1024 * 1024 * 1024):.2f} GB")
        return total_size  # 返回验证后的总大小
    
    def stop(self):
        """停止同步过程"""
        self.running = False


# OneDrive同步线程类
class OneDriveSyncThread(QThread):
    """OneDrive同步线程"""
    progress_updated = pyqtSignal(int, str)
    sync_complete = pyqtSignal(bool, str, int, int)  # 成功标志, 消息, 已同步文件数, 已同步总大小(字节)
    
    def __init__(self, local_dir, remote_folder, client_id, client_secret, sync_mode, exclude_exts=None):
        super().__init__()
        self.local_dir = local_dir
        self.remote_folder = remote_folder
        self.client_id = client_id
        self.client_secret = client_secret
        self.sync_mode = sync_mode  # 'upload', 'download', 'bidirectional'
        self.exclude_exts = exclude_exts or []
        self.running = True
        self.max_retries = 3  # 最大重试次数
        self.chunk_size = 10 * 1024 * 1024  # 10MB 分块上传大小
        self.total_files = 0  # 总文件数
        self.processed_files = 0  # 已处理文件数
        self.synced_files = 0  # 实际同步的文件数
        self.synced_size = 0  # 实际同步的文件总大小(字节)
        self.synced_files_list = []  # 记录已同步的文件列表，用于计算总大小
        self.verified_total_size = 0  # 添加这个新属性
        self.access_token = None
        self.token_path = os.path.join(os.path.expanduser("~"), ".onedrive_token.json")
    
    def run(self):
        try:
            self.progress_updated.emit(0, "开始OneDrive同步...")
            self.synced_files = 0
            self.synced_size = 0
            self.synced_files_list = []
            
            # 确保本地目录存在
            if not os.path.exists(self.local_dir):
                os.makedirs(self.local_dir, exist_ok=True)
            
            # 获取访问令牌
            self.progress_updated.emit(5, "获取OneDrive访问令牌...")
            if not self._get_access_token():
                self.progress_updated.emit(0, "OneDrive认证失败")
                self.sync_complete.emit(False, "OneDrive认证失败，请检查客户端ID和密钥", 0, 0)
                return
            
            # 获取本地文件列表
            self.progress_updated.emit(10, "获取本地文件列表...")
            local_files = self._get_local_files()
            
            # 获取远程文件列表
            self.progress_updated.emit(20, "获取OneDrive文件列表...")
            remote_files = self._get_remote_files()
            
            if not self.running:
                self.progress_updated.emit(0, "同步已取消")
                self.sync_complete.emit(False, "同步已取消", self.synced_files, self.synced_size)
                return
            
            # 根据同步模式确定需要处理的文件总数
            if self.sync_mode == 'upload':
                self.total_files = len(local_files)
            elif self.sync_mode == 'download':
                self.total_files = len(remote_files)
            else:  # bidirectional
                self.total_files = len(set(list(local_files.keys()) + list(remote_files.keys())))
            
            self.processed_files = 0
            
            # 根据同步模式执行不同的同步策略
            if self.sync_mode == 'upload':
                self._upload_files(local_files, remote_files)
            elif self.sync_mode == 'download':
                self._download_files(local_files, remote_files)
            elif self.sync_mode == 'bidirectional':
                self._bidirectional_sync(local_files, remote_files)
            
            # 同步完成后，验证总大小
            verified_size = self._verify_total_size()
            self.verified_total_size = verified_size
            self.synced_size = verified_size
            
            if self.running:
                # 格式化大小显示
                size_mb = verified_size / (1024 * 1024)
                if size_mb > 1000:
                    size_str = f"{size_mb/1024:.2f} GB"
                else:
                    size_str = f"{size_mb:.2f} MB"
                
                self.progress_updated.emit(100, f"OneDrive同步完成! 已同步 {self.synced_files} 个文件, 总大小 {size_str}")
                self.sync_complete.emit(True, "OneDrive同步成功完成", self.synced_files, verified_size)
            else:
                self.progress_updated.emit(0, "OneDrive同步已取消")
                self.sync_complete.emit(False, "OneDrive同步已取消", self.synced_files, verified_size)
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                # 尝试在异常情况下也验证总大小
                verified_size = self._verify_total_size()
                self.progress_updated.emit(0, f"同步出错: {str(e)}")
                self.sync_complete.emit(False, f"OneDrive同步失败: {str(e)}", self.synced_files, verified_size)
            except:
                # 如果验证总大小也失败，则使用当前值
                self.progress_updated.emit(0, f"同步出错: {str(e)}")
                self.sync_complete.emit(False, f"OneDrive同步失败: {str(e)}", self.synced_files, self.synced_size)
    
    def _get_access_token(self):
        """获取OneDrive访问令牌"""
        try:
            # 检查是否有保存的令牌
            if os.path.exists(self.token_path):
                with open(self.token_path, 'r') as f:
                    token_data = json.load(f)
                
                # 检查令牌是否过期
                if token_data.get('expires_at', 0) > time.time():
                    self.access_token = token_data.get('access_token')
                    return True
            
            # 创建MSAL应用
            app = msal.ConfidentialClientApplication(
                self.client_id,
                authority="https://login.microsoftonline.com/common",
                client_credential=self.client_secret
            )
            
            # 获取授权URL
            auth_url = app.get_authorization_request_url(
                ["Files.ReadWrite"],
                redirect_uri="http://localhost:8000",
                state="12345"
            )
            
            # 提示用户访问授权URL
            self.progress_updated.emit(5, "请在浏览器中完成OneDrive授权...")
            QMessageBox.information(None, "OneDrive授权", 
                                   f"请在浏览器中打开以下URL并授权应用访问您的OneDrive:\n\n{auth_url}\n\n授权完成后，请复制回调URL中的代码。")
            
            # 获取用户输入的授权代码
            from PyQt5.QtWidgets import QInputDialog
            code, ok = QInputDialog.getText(None, "输入授权代码", "请输入OneDrive授权后获得的代码:")
            
            if not ok or not code:
                return False
            
            # 使用授权代码获取访问令牌
            result = app.acquire_token_by_authorization_code(
                code,
                scopes=["Files.ReadWrite"],
                redirect_uri="http://localhost:8000"
            )
            
            if "access_token" not in result:
                print(f"获取访问令牌失败: {result.get('error_description', '')}")
                return False
            
            # 保存令牌
            self.access_token = result["access_token"]
            token_data = {
                "access_token": result["access_token"],
                "refresh_token": result.get("refresh_token", ""),
                "expires_at": time.time() + result.get("expires_in", 3600)
            }
            
            with open(self.token_path, 'w') as f:
                json.dump(token_data, f)
            
            return True
        except Exception as e:
            print(f"获取OneDrive访问令牌失败: {e}")
            return False
    
    def _get_local_files(self):
        """获取本地文件列表"""
        files_info = {}
        
        for root, _, files in os.walk(self.local_dir):
            for file in files:
                # 检查是否需要排除该文件
                if any(file.lower().endswith(ext.lower()) for ext in self.exclude_exts):
                    continue
                
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, self.local_dir).replace('\\', '/')
                
                # 获取文件修改时间和大小
                stat = os.stat(file_path)
                files_info[rel_path] = {
                    'mtime': stat.st_mtime,
                    'size': stat.st_size,
                    'local_path': file_path
                }
        
        return files_info
    
    def _get_remote_files(self):
        """获取OneDrive远程文件列表"""
        files_info = {}
        
        try:
            # 构建API请求头
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
            
            # 获取根目录或指定目录的内容
            base_url = "https://graph.microsoft.com/v1.0/me/drive"
            if self.remote_folder and self.remote_folder != "/":
                # 先获取指定文件夹的ID
                folder_path = self.remote_folder.strip("/")
                folder_url = f"{base_url}/root:/{folder_path}"
                response = requests.get(folder_url, headers=headers)
                if response.status_code != 200:
                    print(f"获取远程文件夹信息失败: {response.text}")
                    return files_info
                
                folder_id = response.json().get('id')
                items_url = f"{base_url}/items/{folder_id}/children"
            else:
                items_url = f"{base_url}/root/children"
            
            # 递归获取所有文件
            self._list_onedrive_files(items_url, "", files_info, headers)
        except Exception as e:
            print(f"获取OneDrive文件列表失败: {e}")
        
        return files_info
    
    def _list_onedrive_files(self, url, rel_path, files_info, headers):
        """递归列出OneDrive目录中的所有文件"""
        if not self.running:
            return
        
        try:
            response = requests.get(url, headers=headers)
            if response.status_code != 200:
                print(f"获取OneDrive文件列表失败: {response.text}")
                return
            
            data = response.json()
            items = data.get('value', [])
            
            for item in items:
                if not self.running:
                    return
                
                name = item.get('name', '')
                item_type = item.get('folder') and 'folder' or 'file'
                
                if item_type == 'folder':
                    # 构建子文件夹的相对路径
                    folder_rel_path = os.path.join(rel_path, name).replace('\\', '/')
                    # 获取子文件夹的内容
                    children_url = item.get('@microsoft.graph.downloadUrl') or item.get('id')
                    children_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{item.get('id')}/children"
                    self._list_onedrive_files(children_url, folder_rel_path, files_info, headers)
                else:
                    # 检查是否需要排除该文件
                    if any(name.lower().endswith(ext.lower()) for ext in self.exclude_exts):
                        continue
                    
                    # 构建文件的相对路径
                    file_rel_path = os.path.join(rel_path, name).replace('\\', '/')
                    
                    # 获取文件属性
                    file_size = item.get('size', 0)
                    modified_time = item.get('lastModifiedDateTime', '')
                    
                    # 转换修改时间为时间戳
                    try:
                        mtime = time.mktime(time.strptime(modified_time, "%Y-%m-%dT%H:%M:%S.%fZ"))
                    except:
                        mtime = 0
                    
                    # 存储文件信息
                    files_info[file_rel_path] = {
                        'mtime': mtime,
                        'size': file_size,
                        'remote_id': item.get('id', ''),
                        'download_url': item.get('@microsoft.graph.downloadUrl', '')
                    }
            
            # 检查是否有更多页
            next_link = data.get('@odata.nextLink')
            if next_link:
                self._list_onedrive_files(next_link, rel_path, files_info, headers)
        
        except Exception as e:
            print(f"列出OneDrive目录 {rel_path} 失败: {e}")
    
    def _ensure_remote_dir(self, remote_dir):
        """确保远程目录存在"""
        if not remote_dir:
            return True
        
        try:
            # 构建API请求头
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
            
            # 分割路径
            parts = remote_dir.split('/')
            current_path = ""
            parent_id = None
            
            # 如果有指定的远程文件夹，先获取其ID
            if self.remote_folder and self.remote_folder != "/":
                base_folder = self.remote_folder.strip("/")
                folder_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{base_folder}"
                response = requests.get(folder_url, headers=headers)
                if response.status_code != 200:
                    print(f"获取远程基础文件夹信息失败: {response.text}")
                    return False
                
                parent_id = response.json().get('id')
            
            # 逐级创建目录
            for part in parts:
                if not part:
                    continue
                
                current_path = f"{current_path}/{part}" if current_path else part
                
                # 检查目录是否存在
                if parent_id:
                    check_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{parent_id}/children?$filter=name eq '{part}' and folder ne null"
                else:
                    check_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{current_path}"
                
                response = requests.get(check_url, headers=headers)
                
                if response.status_code == 200:
                    # 目录存在，获取其ID
                    if parent_id:
                        items = response.json().get('value', [])
                        if items:
                            parent_id = items[0].get('id')
                        else:
                            # 目录不存在，创建它
                            create_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{parent_id}/children"
                            folder_data = {
                                "name": part,
                                "folder": {},
                                "@microsoft.graph.conflictBehavior": "replace"
                            }
                            create_response = requests.post(create_url, headers=headers, json=folder_data)
                            if create_response.status_code in [200, 201]:
                                parent_id = create_response.json().get('id')
                            else:
                                print(f"创建OneDrive目录失败: {create_response.text}")
                                return False
                    else:
                        parent_id = response.json().get('id')
                else:
                    # 目录不存在，创建它
                    if parent_id:
                        create_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{parent_id}/children"
                    else:
                        create_url = "https://graph.microsoft.com/v1.0/me/drive/root/children"
                    
                    folder_data = {
                        "name": part,
                        "folder": {},
                        "@microsoft.graph.conflictBehavior": "replace"
                    }
                    create_response = requests.post(create_url, headers=headers, json=folder_data)
                    if create_response.status_code in [200, 201]:
                        parent_id = create_response.json().get('id')
                    else:
                        print(f"创建OneDrive目录失败: {create_response.text}")
                        return False
            
            return True
        except Exception as e:
            print(f"确保OneDrive目录存在失败: {e}")
            return False
    
    def _upload_files(self, local_files, remote_files):
        """上传本地文件到OneDrive"""
        self.processed_files = 0
        
        for rel_path, file_info in local_files.items():
            if not self.running:
                break
            
            # 检查是否需要上传（文件不存在或较新）
            if rel_path not in remote_files or remote_files[rel_path]['mtime'] < file_info['mtime']:
                file_size = file_info['size']
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传: {rel_path}")
                
                # 确保远程目录存在
                remote_dir = os.path.dirname(rel_path)
                if remote_dir:
                    self._ensure_remote_dir(remote_dir)
                
                # 上传文件
                upload_success = False
                if file_size > 4 * 1024 * 1024:  # 大于4MB的文件使用分块上传
                    upload_success = self._upload_large_file(file_info['local_path'], rel_path)
                else:
                    upload_success = self._upload_small_file(file_info['local_path'], rel_path)
                
                # 如果上传成功，更新统计信息
                if upload_success:
                    self.synced_files += 1
                    self.synced_size += file_size
                    self.synced_files_list.append(file_info['local_path'])
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _upload_small_file(self, local_path, rel_path):
        """上传小文件到OneDrive"""
        try:
            # 构建API请求头
            headers = {
                'Authorization': f'Bearer {self.access_token}'
            }
            
            # 构建上传URL
            upload_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{self.remote_folder.strip('/')}/{rel_path}:/content"
            
            # 读取文件内容
            with open(local_path, 'rb') as f:
                file_content = f.read()
            
            # 上传文件
            response = requests.put(upload_url, headers=headers, data=file_content)
            
            if response.status_code in [200, 201]:
                return True
            else:
                print(f"上传文件失败: {response.text}")
                return False
        except Exception as e:
            print(f"上传文件失败: {e}")
            return False
    
    def _upload_large_file(self, local_path, rel_path):
        """分块上传大文件到OneDrive"""
        try:
            # 构建API请求头
            headers = {
                'Authorization': f'Bearer {self.access_token}',
                'Content-Type': 'application/json'
            }
            
            # 获取文件大小
            file_size = os.path.getsize(local_path)
            
            # 创建上传会话
            session_url = f"https://graph.microsoft.com/v1.0/me/drive/root:/{self.remote_folder.strip('/')}/{rel_path}:/createUploadSession"
            session_data = {
                "item": {
                    "@microsoft.graph.conflictBehavior": "replace"
                }
            }
            
            session_response = requests.post(session_url, headers=headers, json=session_data)
            
            if session_response.status_code != 200:
                print(f"创建上传会话失败: {session_response.text}")
                return False
            
            upload_url = session_response.json().get('uploadUrl')
            
            # 分块上传
            with open(local_path, 'rb') as f:
                offset = 0
                while offset < file_size:
                    chunk_size = min(self.chunk_size, file_size - offset)
                    chunk_data = f.read(chunk_size)
                    
                    # 构建范围头
                    range_header = f"bytes {offset}-{offset + chunk_size - 1}/{file_size}"
                    chunk_headers = {
                        'Content-Length': str(chunk_size),
                        'Content-Range': range_header
                    }
                    
                    # 上传分块
                    chunk_response = requests.put(upload_url, headers=chunk_headers, data=chunk_data)
                    
                    if chunk_response.status_code not in [200, 201, 202]:
                        print(f"上传分块失败: {chunk_response.text}")
                        return False
                    
                    offset += chunk_size
                    
                    # 更新进度
                    progress = min(99, int(offset * 100 / file_size))
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传: {rel_path} ({progress}%)")
            
            return True
        except Exception as e:
            print(f"分块上传文件失败: {e}")
            return False
    
    def _download_files(self, local_files, remote_files):
        """从OneDrive下载文件"""
        self.processed_files = 0
        
        for rel_path, file_info in remote_files.items():
            if not self.running:
                break
            
            local_path = os.path.join(self.local_dir, rel_path)
            
            # 检查是否需要下载（文件不存在或较新）
            if rel_path not in local_files or local_files[rel_path]['mtime'] < file_info['mtime']:
                file_size = file_info['size']
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载: {rel_path}")
                
                # 确保本地目录存在
                local_dir = os.path.dirname(local_path)
                if not os.path.exists(local_dir):
                    os.makedirs(local_dir, exist_ok=True)
                
                # 下载文件
                download_success = self._download_file(file_info['download_url'] or file_info['remote_id'], local_path)
                
                # 如果下载成功，更新统计信息
                if download_success:
                    self.synced_files += 1
                    self.synced_size += file_size
                    self.synced_files_list.append(local_path)
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _download_file(self, download_url_or_id, local_path):
        """从OneDrive下载文件"""
        try:
            # 构建API请求头
            headers = {
                'Authorization': f'Bearer {self.access_token}'
            }
            
            # 如果是文件ID而不是下载URL，则获取下载URL
            if not download_url_or_id.startswith('http'):
                file_id = download_url_or_id
                info_url = f"https://graph.microsoft.com/v1.0/me/drive/items/{file_id}"
                
                response = requests.get(info_url, headers=headers)
                if response.status_code != 200:
                    print(f"获取文件信息失败: {response.text}")
                    return False
                
                download_url = response.json().get('@microsoft.graph.downloadUrl')
                if not download_url:
                    print("无法获取下载URL")
                    return False
            else:
                download_url = download_url_or_id
            
            # 下载文件
            response = requests.get(download_url, stream=True)
            if response.status_code != 200:
                print(f"下载文件失败: {response.text}")
                return False
            
            # 保存文件
            with open(local_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    if chunk:
                        f.write(chunk)
            
            return True
        except Exception as e:
            print(f"下载文件失败: {e}")
            return False
    
    def _bidirectional_sync(self, local_files, remote_files):
        """双向同步文件"""
        self.processed_files = 0
        
        # 合并所有文件路径
        all_paths = set(list(local_files.keys()) + list(remote_files.keys()))
        
        for rel_path in all_paths:
            if not self.running:
                break
            
            # 检查文件是否存在于本地和远程
            local_exists = rel_path in local_files
            remote_exists = rel_path in remote_files
            
            # 如果文件只存在于本地，则上传
            if local_exists and not remote_exists:
                file_info = local_files[rel_path]
                
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传: {rel_path}")
                
                # 确保远程目录存在
                remote_dir = os.path.dirname(rel_path)
                if remote_dir:
                    self._ensure_remote_dir(remote_dir)
                
                # 上传文件
                upload_success = False
                if file_info['size'] > 4 * 1024 * 1024:  # 大于4MB的文件使用分块上传
                    upload_success = self._upload_large_file(file_info['local_path'], rel_path)
                else:
                    upload_success = self._upload_small_file(file_info['local_path'], rel_path)
                
                # 如果上传成功，更新统计信息
                if upload_success:
                    self.synced_files += 1
                    self.synced_size += file_info['size']
                    self.synced_files_list.append(file_info['local_path'])
            
            # 如果文件只存在于远程，则下载
            elif not local_exists and remote_exists:
                file_info = remote_files[rel_path]
                local_path = os.path.join(self.local_dir, rel_path)
                
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载: {rel_path}")
                
                # 确保本地目录存在
                local_dir = os.path.dirname(local_path)
                if not os.path.exists(local_dir):
                    os.makedirs(local_dir, exist_ok=True)
                
                # 下载文件
                download_success = self._download_file(file_info['download_url'] or file_info['remote_id'], local_path)
                
                # 如果下载成功，更新统计信息
                if download_success:
                    self.synced_files += 1
                    self.synced_size += file_info['size']
                    self.synced_files_list.append(local_path)
            
            # 如果文件同时存在于本地和远程，则比较修改时间
            elif local_exists and remote_exists:
                local_info = local_files[rel_path]
                remote_info = remote_files[rel_path]
                
                # 如果本地文件较新，则上传
                if local_info['mtime'] > remote_info['mtime']:
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传较新文件: {rel_path}")
                    
                    # 上传文件
                    upload_success = False
                    if local_info['size'] > 4 * 1024 * 1024:  # 大于4MB的文件使用分块上传
                        upload_success = self._upload_large_file(local_info['local_path'], rel_path)
                    else:
                        upload_success = self._upload_small_file(local_info['local_path'], rel_path)
                    
                    # 如果上传成功，更新统计信息
                    if upload_success:
                        self.synced_files += 1
                        self.synced_size += local_info['size']
                        self.synced_files_list.append(local_info['local_path'])
                
                # 如果远程文件较新，则下载
                elif local_info['mtime'] < remote_info['mtime']:
                    local_path = os.path.join(self.local_dir, rel_path)
                    
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载较新文件: {rel_path}")
                    
                    # 下载文件
                    download_success = self._download_file(remote_info['download_url'] or remote_info['remote_id'], local_path)
                    
                    # 如果下载成功，更新统计信息
                    if download_success:
                        self.synced_files += 1
                        self.synced_size += remote_info['size']
                        self.synced_files_list.append(local_path)
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _verify_total_size(self):
        """验证总大小是否正确，如果不正确则重新计算"""
        # 总是重新计算总大小
        total_size = 0
        valid_files = []
        
        print(f"开始验证总大小，同步文件列表长度: {len(self.synced_files_list)}")
        
        for file_path in self.synced_files_list:
            if os.path.exists(file_path):
                try:
                    file_size = os.path.getsize(file_path)
                    total_size += file_size
                    valid_files.append(file_path)
                except (OSError, IOError) as e:
                    print(f"获取文件大小失败: {file_path}, 错误: {e}")
        
        # 更新同步文件列表，只保留有效文件
        self.synced_files_list = valid_files
        self.synced_size = total_size  # 使用验证后的总大小更新synced_size
        self.synced_files = len(valid_files)  # 使用验证后的文件数更新synced_files
        
        print(f"验证后的总大小: {total_size} 字节, 有效文件数: {len(valid_files)}")
        print(f"格式化后的大小: {total_size / (1024 * 1024):.2f} MB 或 {total_size / (1024 * 1024 * 1024):.2f} GB")
        return total_size  # 返回验证后的总大小
    
    def stop(self):
        """停止同步过程"""
        self.running = False
class GoogleDriveSyncThread(QThread):
    """Google Drive同步线程"""
    progress_updated = pyqtSignal(int, str)
    sync_complete = pyqtSignal(bool, str, int, int)  # 成功标志, 消息, 已同步文件数, 已同步总大小(字节)

    def __init__(self, local_dir, remote_folder, credentials_file, sync_mode, exclude_exts=None):
        super().__init__()
        self.local_dir = local_dir
        self.remote_folder = remote_folder
        self.credentials_file = credentials_file
        self.sync_mode = sync_mode  # 'upload', 'download', 'bidirectional'
        self.exclude_exts = exclude_exts or []
        self.running = True
        self.max_retries = 3  # 最大重试次数
        self.chunk_size = 10 * 1024 * 1024  # 10MB 分块上传大小
        self.total_files = 0  # 总文件数
        self.processed_files = 0  # 已处理文件数
        self.synced_files = 0  # 实际同步的文件数
        self.synced_size = 0  # 实际同步的文件总大小(字节)
        self.synced_files_list = []  # 记录已同步的文件列表，用于计算总大小
        self.verified_total_size = 0  # 添加这个新属性
        self.drive_service = None
        self.token_path = os.path.join(os.path.expanduser("~"), ".gdrive_token.pickle")
        self.scopes = ['https://www.googleapis.com/auth/drive']
    
    def run(self):
        try:
            self.progress_updated.emit(0, "开始Google Drive同步...")
            self.synced_files = 0
            self.synced_size = 0
            self.synced_files_list = []
            
            # 确保本地目录存在
            if not os.path.exists(self.local_dir):
                os.makedirs(self.local_dir, exist_ok=True)
            
            # 获取Google Drive服务
            self.progress_updated.emit(5, "连接Google Drive...")
            if not self._get_drive_service():
                self.progress_updated.emit(0, "Google Drive认证失败")
                self.sync_complete.emit(False, "Google Drive认证失败，请检查凭据文件", 0, 0)
                return
            
            # 获取本地文件列表
            self.progress_updated.emit(10, "获取本地文件列表...")
            local_files = self._get_local_files()
            
            # 获取远程文件列表
            self.progress_updated.emit(20, "获取Google Drive文件列表...")
            remote_files, remote_folder_id = self._get_remote_files()
            
            if not self.running:
                self.progress_updated.emit(0, "同步已取消")
                self.sync_complete.emit(False, "同步已取消", self.synced_files, self.synced_size)
                return
            
            # 根据同步模式确定需要处理的文件总数
            if self.sync_mode == 'upload':
                self.total_files = len(local_files)
            elif self.sync_mode == 'download':
                self.total_files = len(remote_files)
            else:  # bidirectional
                self.total_files = len(set(list(local_files.keys()) + list(remote_files.keys())))
            
            self.processed_files = 0
            
            # 根据同步模式执行不同的同步策略
            if self.sync_mode == 'upload':
                self._upload_files(local_files, remote_files, remote_folder_id)
            elif self.sync_mode == 'download':
                self._download_files(local_files, remote_files)
            elif self.sync_mode == 'bidirectional':
                self._bidirectional_sync(local_files, remote_files, remote_folder_id)
            
            # 同步完成后，验证总大小
            verified_size = self._verify_total_size()
            self.verified_total_size = verified_size
            self.synced_size = verified_size
            
            if self.running:
                # 格式化大小显示
                size_mb = verified_size / (1024 * 1024)
                if size_mb > 1000:
                    size_str = f"{size_mb/1024:.2f} GB"
                else:
                    size_str = f"{size_mb:.2f} MB"
                
                self.progress_updated.emit(100, f"Google Drive同步完成! 已同步 {self.synced_files} 个文件, 总大小 {size_str}")
                self.sync_complete.emit(True, "Google Drive同步成功完成", self.synced_files, verified_size)
            else:
                self.progress_updated.emit(0, "Google Drive同步已取消")
                self.sync_complete.emit(False, "Google Drive同步已取消", self.synced_files, verified_size)
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                # 尝试在异常情况下也验证总大小
                verified_size = self._verify_total_size()
                self.progress_updated.emit(0, f"同步出错: {str(e)}")
                self.sync_complete.emit(False, f"Google Drive同步失败: {str(e)}", self.synced_files, verified_size)
            except:
                # 如果验证总大小也失败，则使用当前值
                self.progress_updated.emit(0, f"同步出错: {str(e)}")
                self.sync_complete.emit(False, f"Google Drive同步失败: {str(e)}", self.synced_files, self.synced_size)
    
    def _get_drive_service(self):
        """获取Google Drive服务"""
        try:
            creds = None
            
            # 检查是否有保存的令牌
            if os.path.exists(self.token_path):
                with open(self.token_path, 'rb') as token:
                    creds = pickle.load(token)
            
            # 如果没有有效的凭据，则让用户登录
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    if not os.path.exists(self.credentials_file):
                        print(f"凭据文件不存在: {self.credentials_file}")
                        return False
                    
                    flow = InstalledAppFlow.from_client_secrets_file(self.credentials_file, self.scopes)
                    creds = flow.run_local_server(port=0)
                
                # 保存凭据以供下次使用
                with open(self.token_path, 'wb') as token:
                    pickle.dump(creds, token)
            
            # 创建Drive服务
            self.drive_service = build('drive', 'v3', credentials=creds)
            return True
        except Exception as e:
            print(f"获取Google Drive服务失败: {e}")
            return False
    
    def _get_local_files(self):
        """获取本地文件列表"""
        files_info = {}
        
        for root, _, files in os.walk(self.local_dir):
            for file in files:
                # 检查是否需要排除该文件
                if any(file.lower().endswith(ext.lower()) for ext in self.exclude_exts):
                    continue
                
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, self.local_dir).replace('\\', '/')
                
                # 获取文件修改时间和大小
                stat = os.stat(file_path)
                files_info[rel_path] = {
                    'mtime': stat.st_mtime,
                    'size': stat.st_size,
                    'local_path': file_path
                }
        
        return files_info
    
    def _get_remote_files(self):
        """获取Google Drive远程文件列表"""
        files_info = {}
        remote_folder_id = 'root'  # 默认使用根目录
        
        try:
            # 如果指定了远程文件夹，则获取其ID
            if self.remote_folder and self.remote_folder != "/":
                folder_name = os.path.basename(self.remote_folder.rstrip('/'))
                parent_id = 'root'
                
                # 查询文件夹
                query = f"name = '{folder_name}' and mimeType = 'application/vnd.google-apps.folder' and '{parent_id}' in parents and trashed = false"
                results = self.drive_service.files().list(
                    q=query,
                    spaces='drive',
                    fields='files(id, name)'
                ).execute()
                
                items = results.get('files', [])
                
                if not items:
                    # 文件夹不存在，创建它
                    folder_metadata = {
                        'name': folder_name,
                        'mimeType': 'application/vnd.google-apps.folder',
                        'parents': [parent_id]
                    }
                    folder = self.drive_service.files().create(body=folder_metadata, fields='id').execute()
                    remote_folder_id = folder.get('id')
                else:
                    remote_folder_id = items[0]['id']
            
            # 递归获取所有文件
            self._list_drive_files(remote_folder_id, "", files_info)
            
            return files_info, remote_folder_id
        except Exception as e:
            print(f"获取Google Drive文件列表失败: {e}")
            return files_info, remote_folder_id
    
    def _list_drive_files(self, folder_id, rel_path, files_info):
        """递归列出Google Drive目录中的所有文件"""
        if not self.running:
            return
        
        try:
            # 列出文件夹中的所有文件和子文件夹
            query = f"'{folder_id}' in parents and trashed = false"
            page_token = None
            
            while True:
                results = self.drive_service.files().list(
                    q=query,
                    spaces='drive',
                    fields='nextPageToken, files(id, name, mimeType, modifiedTime, size)',
                    pageToken=page_token
                ).execute()
                
                items = results.get('files', [])
                
                for item in items:
                    if not self.running:
                        return
                    
                    name = item.get('name', '')
                    mime_type = item.get('mimeType', '')
                    
                    # 处理文件夹
                    if mime_type == 'application/vnd.google-apps.folder':
                        # 构建子文件夹的相对路径
                        folder_rel_path = os.path.join(rel_path, name).replace('\\', '/')
                        # 递归处理子文件夹
                        self._list_drive_files(item['id'], folder_rel_path, files_info)
                    else:
                        # 检查是否需要排除该文件
                        if any(name.lower().endswith(ext.lower()) for ext in self.exclude_exts):
                            continue
                        
                        # 构建文件的相对路径
                        file_rel_path = os.path.join(rel_path, name).replace('\\', '/')
                        
                        # 获取文件属性
                        modified_time = item.get('modifiedTime', '')
                        file_size = item.get('size', '0')
                        
                        # 转换修改时间为时间戳
                        try:
                            mtime = time.mktime(time.strptime(modified_time, "%Y-%m-%dT%H:%M:%S.%fZ"))
                        except:
                            mtime = 0
                        
                        # 转换文件大小为整数
                        try:
                            size = int(file_size)
                        except:
                            size = 0
                        
                        # 存储文件信息
                        files_info[file_rel_path] = {
                            'mtime': mtime,
                            'size': size,
                            'file_id': item['id']
                        }
                
                page_token = results.get('nextPageToken')
                if not page_token:
                    break
        except Exception as e:
            print(f"列出Google Drive目录 {rel_path} 失败: {e}")
    
    def _ensure_remote_dir(self, remote_dir, parent_id):
        """确保远程目录存在，返回目录ID"""
        if not remote_dir:
            return parent_id
        
        try:
            # 分割路径
            parts = remote_dir.split('/')
            current_id = parent_id
            
            # 逐级创建目录
            for part in parts:
                if not part:
                    continue
                
                # 查询文件夹是否存在
                query = f"name = '{part}' and mimeType = 'application/vnd.google-apps.folder' and '{current_id}' in parents and trashed = false"
                results = self.drive_service.files().list(
                    q=query,
                    spaces='drive',
                    fields='files(id, name)'
                ).execute()
                
                items = results.get('files', [])
                
                if not items:
                    # 文件夹不存在，创建它
                    folder_metadata = {
                        'name': part,
                        'mimeType': 'application/vnd.google-apps.folder',
                        'parents': [current_id]
                    }
                    folder = self.drive_service.files().create(body=folder_metadata, fields='id').execute()
                    current_id = folder.get('id')
                else:
                    current_id = items[0]['id']
            
            return current_id
        except Exception as e:
            print(f"确保Google Drive目录存在失败: {e}")
            return parent_id
    
    def _upload_files(self, local_files, remote_files, parent_folder_id):
        """上传本地文件到Google Drive"""
        self.processed_files = 0
        
        for rel_path, file_info in local_files.items():
            if not self.running:
                break
            
            # 检查是否需要上传（文件不存在或较新）
            if rel_path not in remote_files or remote_files[rel_path]['mtime'] < file_info['mtime']:
                file_size = file_info['size']
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传: {rel_path}")
                
                # 确保远程目录存在
                remote_dir = os.path.dirname(rel_path)
                folder_id = self._ensure_remote_dir(remote_dir, parent_folder_id)
                
                # 上传文件
                upload_success = self._upload_file(file_info['local_path'], os.path.basename(rel_path), folder_id)
                
                # 如果上传成功，更新统计信息
                if upload_success:
                    self.synced_files += 1
                    self.synced_size += file_size
                    self.synced_files_list.append(file_info['local_path'])
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _upload_file(self, local_path, file_name, parent_id):
        """上传文件到Google Drive"""
        try:
            # 文件元数据
            file_metadata = {
                'name': file_name,
                'parents': [parent_id]
            }
            
            # 获取MIME类型
            mime_type = 'application/octet-stream'
            
            # 创建媒体对象
            media = MediaFileUpload(
                local_path,
                mimetype=mime_type,
                resumable=True,
                chunksize=self.chunk_size
            )
            
            # 创建文件上传请求
            request = self.drive_service.files().create(
                body=file_metadata,
                media_body=media,
                fields='id'
            )
            
            # 执行上传
            response = None
            while response is None and self.running:
                status, response = request.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传: {file_name} ({progress}%)")
            
            if not self.running:
                return False
            
            return True
        except Exception as e:
            print(f"上传文件失败: {e}")
            return False
    
    def _download_files(self, local_files, remote_files):
        """从Google Drive下载文件"""
        self.processed_files = 0
        
        for rel_path, file_info in remote_files.items():
            if not self.running:
                break
            
            local_path = os.path.join(self.local_dir, rel_path)
            
            # 检查是否需要下载（文件不存在或较新）
            if rel_path not in local_files or local_files[rel_path]['mtime'] < file_info['mtime']:
                file_size = file_info['size']
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载: {rel_path}")
                
                # 确保本地目录存在
                local_dir = os.path.dirname(local_path)
                if not os.path.exists(local_dir):
                    os.makedirs(local_dir, exist_ok=True)
                
                # 下载文件
                download_success = self._download_file(file_info['file_id'], local_path)
                
                # 如果下载成功，更新统计信息
                if download_success:
                    self.synced_files += 1
                    self.synced_size += file_size
                    self.synced_files_list.append(local_path)
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _download_file(self, file_id, local_path):
        """从Google Drive下载文件"""
        try:
            request = self.drive_service.files().get_media(fileId=file_id)
            
            # 创建一个文件对象用于保存下载的内容
            fh = io.FileIO(local_path, 'wb')
            downloader = MediaIoBaseDownload(fh, request, chunksize=self.chunk_size)
            
            # 执行下载
            done = False
            while not done and self.running:
                status, done = downloader.next_chunk()
                if status:
                    progress = int(status.progress() * 100)
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载: {os.path.basename(local_path)} ({progress}%)")
            
            fh.close()
            
            if not self.running:
                return False
            
            return True
        except Exception as e:
            print(f"下载文件失败: {e}")
            return False
    
    def _bidirectional_sync(self, local_files, remote_files, parent_folder_id):
        """双向同步文件"""
        self.processed_files = 0
        
        # 合并所有文件路径
        all_paths = set(list(local_files.keys()) + list(remote_files.keys()))
        
        for rel_path in all_paths:
            if not self.running:
                break
            
            # 检查文件是否存在于本地和远程
            local_exists = rel_path in local_files
            remote_exists = rel_path in remote_files
            
            # 如果文件只存在于本地，则上传
            if local_exists and not remote_exists:
                file_info = local_files[rel_path]
                
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传: {rel_path}")
                
                # 确保远程目录存在
                remote_dir = os.path.dirname(rel_path)
                folder_id = self._ensure_remote_dir(remote_dir, parent_folder_id)
                
                # 上传文件
                if self._upload_file(file_info['local_path'], os.path.basename(rel_path), folder_id):
                    self.synced_files += 1
                    self.synced_size += file_info['size']
                    self.synced_files_list.append(file_info['local_path'])
            
            # 如果文件只存在于远程，则下载
            elif not local_exists and remote_exists:
                file_info = remote_files[rel_path]
                local_path = os.path.join(self.local_dir, rel_path)
                
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载: {rel_path}")
                
                # 确保本地目录存在
                local_dir = os.path.dirname(local_path)
                if not os.path.exists(local_dir):
                    os.makedirs(local_dir, exist_ok=True)
                
                # 下载文件
                if self._download_file(file_info['file_id'], local_path):
                    self.synced_files += 1
                    self.synced_size += file_info['size']
                    self.synced_files_list.append(local_path)
            
            # 如果文件同时存在于本地和远程，则比较修改时间
            elif local_exists and remote_exists:
                local_info = local_files[rel_path]
                remote_info = remote_files[rel_path]
                
                # 如果本地文件较新，则上传
                if local_info['mtime'] > remote_info['mtime']:
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传较新文件: {rel_path}")
                    
                    # 上传文件
                    if self._upload_file(local_info['local_path'], os.path.basename(rel_path), parent_folder_id):
                        self.synced_files += 1
                        self.synced_size += local_info['size']
                        self.synced_files_list.append(local_info['local_path'])
                
                # 如果远程文件较新，则下载
                elif local_info['mtime'] < remote_info['mtime']:
                    local_path = os.path.join(self.local_dir, rel_path)
                    
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载较新文件: {rel_path}")
                    
                    # 下载文件
                    if self._download_file(remote_info['file_id'], local_path):
                        self.synced_files += 1
                        self.synced_size += remote_info['size']
                        self.synced_files_list.append(local_path)
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _verify_total_size(self):
        """验证总大小是否正确，如果不正确则重新计算"""
        # 总是重新计算总大小
        total_size = 0
        valid_files = []
        
        print(f"开始验证总大小，同步文件列表长度: {len(self.synced_files_list)}")
        
        for file_path in self.synced_files_list:
            if os.path.exists(file_path):
                try:
                    file_size = os.path.getsize(file_path)
                    total_size += file_size
                    valid_files.append(file_path)
                except (OSError, IOError) as e:
                    print(f"获取文件大小失败: {file_path}, 错误: {e}")
        
        # 更新同步文件列表，只保留有效文件
        self.synced_files_list = valid_files
        self.synced_size = total_size  # 使用验证后的总大小更新synced_size
        self.synced_files = len(valid_files)  # 使用验证后的文件数更新synced_files
        
        print(f"验证后的总大小: {total_size} 字节, 有效文件数: {len(valid_files)}")
        print(f"格式化后的大小: {total_size / (1024 * 1024):.2f} MB 或 {total_size / (1024 * 1024 * 1024):.2f} GB")
        return total_size  # 返回验证后的总大小
    
    def stop(self):
        """停止同步过程"""
        self.running = False
    
class SMBSyncThread(QThread):
    """SMB/CIFS网络共享同步线程"""
    progress_updated = pyqtSignal(int, str)
    sync_complete = pyqtSignal(bool, str, int, int)  # 成功标志, 消息, 已同步文件数, 已同步总大小(字节)
    
    def __init__(self, local_dir, server, share, username, password, remote_dir, sync_mode, exclude_exts=None):
        super().__init__()
        self.local_dir = local_dir
        self.server = server
        self.share = share
        self.username = username
        self.password = password
        self.remote_dir = remote_dir
        self.sync_mode = sync_mode  # 'upload', 'download', 'bidirectional'
        self.exclude_exts = exclude_exts or []
        self.running = True
        self.max_retries = 3  # 最大重试次数
        self.chunk_size = 8 * 1024 * 1024  # 8MB 分块上传大小
        self.total_files = 0  # 总文件数
        self.processed_files = 0  # 已处理文件数
        self.synced_files = 0  # 实际同步的文件数
        self.synced_size = 0  # 实际同步的文件总大小(字节)
        self.synced_files_list = []  # 记录已同步的文件列表，用于计算总大小
        self.verified_total_size = 0  # 添加这个新属性
        self.conn = None
    
    def run(self):
        try:
            self.progress_updated.emit(0, "开始SMB网络共享同步...")
            self.synced_files = 0
            self.synced_size = 0
            self.synced_files_list = []
            
            # 确保本地目录存在
            if not os.path.exists(self.local_dir):
                os.makedirs(self.local_dir, exist_ok=True)
            
            # 连接SMB服务器
            self.progress_updated.emit(5, "连接SMB服务器...")
            if not self._connect_smb():
                self.progress_updated.emit(0, "SMB连接失败")
                self.sync_complete.emit(False, "SMB连接失败，请检查连接信息", 0, 0)
                return
            
            # 获取本地文件列表
            self.progress_updated.emit(10, "获取本地文件列表...")
            local_files = self._get_local_files()
            
            # 获取远程文件列表
            self.progress_updated.emit(20, "获取SMB网络共享文件列表...")
            remote_files = self._get_remote_files()
            
            if not self.running:
                self.progress_updated.emit(0, "同步已取消")
                self.sync_complete.emit(False, "同步已取消", self.synced_files, self.synced_size)
                self._disconnect_smb()
                return
            
            # 根据同步模式确定需要处理的文件总数
            if self.sync_mode == 'upload':
                self.total_files = len(local_files)
            elif self.sync_mode == 'download':
                self.total_files = len(remote_files)
            else:  # bidirectional
                self.total_files = len(set(list(local_files.keys()) + list(remote_files.keys())))
            
            self.processed_files = 0
            
            # 根据同步模式执行不同的同步策略
            if self.sync_mode == 'upload':
                self._upload_files(local_files, remote_files)
            elif self.sync_mode == 'download':
                self._download_files(local_files, remote_files)
            elif self.sync_mode == 'bidirectional':
                self._bidirectional_sync(local_files, remote_files)
            
            # 同步完成后，验证总大小
            verified_size = self._verify_total_size()
            self.verified_total_size = verified_size
            self.synced_size = verified_size
            
            # 断开SMB连接
            self._disconnect_smb()
            
            if self.running:
                # 格式化大小显示
                size_mb = verified_size / (1024 * 1024)
                if size_mb > 1000:
                    size_str = f"{size_mb/1024:.2f} GB"
                else:
                    size_str = f"{size_mb:.2f} MB"
                
                self.progress_updated.emit(100, f"SMB网络共享同步完成! 已同步 {self.synced_files} 个文件, 总大小 {size_str}")
                self.sync_complete.emit(True, "SMB网络共享同步成功完成", self.synced_files, verified_size)
            else:
                self.progress_updated.emit(0, "SMB网络共享同步已取消")
                self.sync_complete.emit(False, "SMB网络共享同步已取消", self.synced_files, verified_size)
                
        except Exception as e:
            import traceback
            traceback.print_exc()
            try:
                # 尝试断开连接
                self._disconnect_smb()
                # 尝试在异常情况下也验证总大小
                verified_size = self._verify_total_size()
                self.progress_updated.emit(0, f"同步出错: {str(e)}")
                self.sync_complete.emit(False, f"SMB网络共享同步失败: {str(e)}", self.synced_files, verified_size)
            except:
                # 如果验证总大小也失败，则使用当前值
                self.progress_updated.emit(0, f"同步出错: {str(e)}")
                self.sync_complete.emit(False, f"SMB网络共享同步失败: {str(e)}", self.synced_files, self.synced_size)
    
    def _connect_smb(self):
        """连接到SMB服务器"""
        try:
            # 获取本机计算机名
            client_name = socket.gethostname()
            
            # 创建SMB连接
            self.conn = smb.SMBConnection.SMBConnection(
                self.username,
                self.password,
                client_name,
                self.server,
                use_ntlm_v2=True,
                is_direct_tcp=True
            )
            
            # 连接到服务器
            server_ip = socket.gethostbyname(self.server)
            connected = self.conn.connect(server_ip, 445)  # 445是SMB直接TCP端口
            
            if not connected:
                # 尝试使用NetBIOS端口
                connected = self.conn.connect(server_ip, 139)
            
            if not connected:
                print("SMB连接失败")
                return False
            
            # 检查共享是否存在
            shares = self.conn.listShares()
            share_exists = False
            for share_info in shares:
                if share_info.name == self.share:
                    share_exists = True
                    break
            
            if not share_exists:
                print(f"共享 '{self.share}' 不存在")
                return False
            
            # 检查远程目录是否存在
            if self.remote_dir and self.remote_dir != "/":
                try:
                    self.conn.listPath(self.share, self.remote_dir)
                except:
                    # 尝试创建远程目录
                    self._mkdir_p(self.remote_dir)
            
            return True
        except Exception as e:
            print(f"SMB连接失败: {e}")
            return False
    
    def _disconnect_smb(self):
        """断开SMB连接"""
        try:
            if self.conn:
                self.conn.close()
        except:
            pass
    
    def _mkdir_p(self, remote_directory):
        """递归创建远程目录"""
        if remote_directory == '/' or remote_directory == '':
            return
        
        # 规范化路径
        remote_directory = remote_directory.replace('\\', '/')
        if remote_directory.startswith('/'):
            remote_directory = remote_directory[1:]
        if remote_directory.endswith('/'):
            remote_directory = remote_directory[:-1]
        
        # 分割路径
        parts = remote_directory.split('/')
        current_path = ""
        
        # 逐级创建目录
        for part in parts:
            if not part:
                continue
            
            current_path = f"{current_path}/{part}" if current_path else part
            
            try:
                self.conn.listPath(self.share, current_path)
            except:
                try:
                    parent_path = "/".join(current_path.split('/')[:-1])
                    self.conn.createDirectory(self.share, current_path)
                except Exception as e:
                    print(f"创建目录失败 {current_path}: {e}")
    
    def _get_local_files(self):
        """获取本地文件列表"""
        files_info = {}
        
        for root, _, files in os.walk(self.local_dir):
            for file in files:
                # 检查是否需要排除该文件
                if any(file.lower().endswith(ext.lower()) for ext in self.exclude_exts):
                    continue
                
                file_path = os.path.join(root, file)
                rel_path = os.path.relpath(file_path, self.local_dir).replace('\\', '/')
                
                # 获取文件修改时间和大小
                stat = os.stat(file_path)
                files_info[rel_path] = {
                    'mtime': stat.st_mtime,
                    'size': stat.st_size,
                    'local_path': file_path
                }
        
        return files_info
    
    def _get_remote_files(self):
        """获取远程SMB文件列表"""
        files_info = {}
        
        try:
            base_dir = self.remote_dir.replace('\\', '/')
            if base_dir.startswith('/'):
                base_dir = base_dir[1:]
            
            self._list_smb_files(base_dir, "", files_info)
        except Exception as e:
            print(f"获取SMB文件列表失败: {e}")
        
        return files_info
    
    def _list_smb_files(self, base_dir, rel_path, files_info):
        """递归列出SMB目录中的所有文件"""
        if not self.running:
            return
        
        try:
            # 构建完整路径
            full_path = os.path.join(base_dir, rel_path).replace('\\', '/')
            if full_path.startswith('/'):
                full_path = full_path[1:]
            if not full_path:
                full_path = '/'
            
            # 列出目录内容
            for entry in self.conn.listPath(self.share, full_path):
                if not self.running:
                    return
                
                # 跳过当前目录和上级目录
                if entry.filename in ['.', '..']:
                    continue
                
                # 构建相对路径
                entry_rel_path = os.path.join(rel_path, entry.filename).replace('\\', '/')
                
                # 检查是否是目录
                if entry.isDirectory:
                    # 递归处理子目录
                    self._list_smb_files(base_dir, entry_rel_path, files_info)
                else:
                    # 检查是否需要排除该文件
                    if any(entry.filename.lower().endswith(ext.lower()) for ext in self.exclude_exts):
                        continue
                    
                    # 存储文件信息
                    files_info[entry_rel_path] = {
                        'mtime': entry.last_write_time,
                        'size': entry.file_size,
                        'remote_path': os.path.join(full_path, entry.filename).replace('\\', '/')
                    }
        except Exception as e:
            print(f"列出SMB目录 {full_path} 失败: {e}")
    
    def _ensure_remote_dir(self, remote_dir):
        """确保远程目录存在"""
        if not remote_dir:
            return True
        
        try:
            # 构建完整路径
            full_path = os.path.join(self.remote_dir, remote_dir).replace('\\', '/')
            if full_path.startswith('/'):
                full_path = full_path[1:]
            
            self._mkdir_p(full_path)
            return True
        except Exception as e:
            print(f"创建远程目录失败: {full_path}, 错误: {e}")
            return False
    
    def _upload_files(self, local_files, remote_files):
        """上传本地文件到SMB服务器"""
        self.processed_files = 0
        
        for rel_path, file_info in local_files.items():
            if not self.running:
                break
            
            # 规范化SMB路径
            remote_path = os.path.join(self.remote_dir, rel_path).replace('\\', '/')
            if remote_path.startswith('/'):
                remote_path = remote_path[1:]
            
            # 检查是否需要上传（文件不存在或较新）
            if rel_path not in remote_files or remote_files[rel_path]['mtime'] < file_info['mtime']:
                file_size = file_info['size']
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传: {rel_path}")
                
                # 确保远程目录存在
                remote_dir = os.path.dirname(rel_path)
                if remote_dir:
                    self._ensure_remote_dir(remote_dir)
                
                # 上传文件
                upload_success = self._upload_file(file_info['local_path'], remote_path)
                
                # 如果上传成功，更新统计信息
                if upload_success:
                    self.synced_files += 1
                    self.synced_size += file_size
                    self.synced_files_list.append(file_info['local_path'])
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _upload_file(self, local_path, remote_path):
        """上传文件到SMB服务器"""
        try:
            with open(local_path, 'rb') as file:
                self.conn.storeFile(self.share, remote_path, file)
            return True
        except Exception as e:
            print(f"上传文件失败: {local_path} -> {remote_path}, 错误: {e}")
            return False
    
    def _download_files(self, local_files, remote_files):
        """从SMB服务器下载文件"""
        self.processed_files = 0
        
        for rel_path, file_info in remote_files.items():
            if not self.running:
                break
            
            local_path = os.path.join(self.local_dir, rel_path)
            remote_path = file_info['remote_path']
            
            # 检查是否需要下载（文件不存在或较新）
            if rel_path not in local_files or local_files[rel_path]['mtime'] < file_info['mtime']:
                file_size = file_info['size']
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载: {rel_path}")
                
                # 确保本地目录存在
                local_dir = os.path.dirname(local_path)
                if not os.path.exists(local_dir):
                    os.makedirs(local_dir, exist_ok=True)
                
                # 下载文件
                download_success = self._download_file(remote_path, local_path)
                
                # 如果下载成功，更新统计信息
                if download_success:
                    self.synced_files += 1
                    self.synced_size += file_size
                    self.synced_files_list.append(local_path)
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _download_file(self, remote_path, local_path):
        """从SMB服务器下载文件"""
        try:
            with open(local_path, 'wb') as file:
                self.conn.retrieveFile(self.share, remote_path, file)
            return True
        except Exception as e:
            print(f"下载文件失败: {remote_path} -> {local_path}, 错误: {e}")
            return False
    
    def _bidirectional_sync(self, local_files, remote_files):
        """双向同步文件"""
        self.processed_files = 0
        
        # 合并所有文件路径
        all_paths = set(list(local_files.keys()) + list(remote_files.keys()))
        
        for rel_path in all_paths:
            if not self.running:
                break
            
            # 检查文件是否存在于本地和远程
            local_exists = rel_path in local_files
            remote_exists = rel_path in remote_files
            
            # 如果文件只存在于本地，则上传
            if local_exists and not remote_exists:
                file_info = local_files[rel_path]
                remote_path = os.path.join(self.remote_dir, rel_path).replace('\\', '/')
                if remote_path.startswith('/'):
                    remote_path = remote_path[1:]
                
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传: {rel_path}")
                
                # 确保远程目录存在
                remote_dir = os.path.dirname(rel_path)
                if remote_dir:
                    self._ensure_remote_dir(remote_dir)
                
                # 上传文件
                if self._upload_file(file_info['local_path'], remote_path):
                    self.synced_files += 1
                    self.synced_size += file_info['size']
                    self.synced_files_list.append(file_info['local_path'])
            
            # 如果文件只存在于远程，则下载
            elif not local_exists and remote_exists:
                file_info = remote_files[rel_path]
                local_path = os.path.join(self.local_dir, rel_path)
                
                self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载: {rel_path}")
                
                # 确保本地目录存在
                local_dir = os.path.dirname(local_path)
                if not os.path.exists(local_dir):
                    os.makedirs(local_dir, exist_ok=True)
                
                # 下载文件
                if self._download_file(file_info['remote_path'], local_path):
                    self.synced_files += 1
                    self.synced_size += file_info['size']
                    self.synced_files_list.append(local_path)
            
            # 如果文件同时存在于本地和远程，则比较修改时间
            elif local_exists and remote_exists:
                local_info = local_files[rel_path]
                remote_info = remote_files[rel_path]
                
                # 如果本地文件较新，则上传
                if local_info['mtime'] > remote_info['mtime']:
                    remote_path = os.path.join(self.remote_dir, rel_path).replace('\\', '/')
                    if remote_path.startswith('/'):
                        remote_path = remote_path[1:]
                    
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在上传较新文件: {rel_path}")
                    
                    # 上传文件
                    if self._upload_file(local_info['local_path'], remote_path):
                        self.synced_files += 1
                        self.synced_size += local_info['size']
                        self.synced_files_list.append(local_info['local_path'])
                
                # 如果远程文件较新，则下载
                elif local_info['mtime'] < remote_info['mtime']:
                    local_path = os.path.join(self.local_dir, rel_path)
                    
                    self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"正在下载较新文件: {rel_path}")
                    
                    # 下载文件
                    if self._download_file(remote_info['remote_path'], local_path):
                        self.synced_files += 1
                        self.synced_size += remote_info['size']
                        self.synced_files_list.append(local_path)
            
            self.processed_files += 1
            self.progress_updated.emit(int(self.processed_files * 100 / self.total_files), f"已处理: {self.processed_files}/{self.total_files}")
    
    def _verify_total_size(self):
        """验证总大小是否正确，如果不正确则重新计算"""
        # 总是重新计算总大小
        total_size = 0
        valid_files = []
        
        print(f"开始验证总大小，同步文件列表长度: {len(self.synced_files_list)}")
        
        for file_path in self.synced_files_list:
            if os.path.exists(file_path):
                try:
                    file_size = os.path.getsize(file_path)
                    total_size += file_size
                    valid_files.append(file_path)
                except (OSError, IOError) as e:
                    print(f"获取文件大小失败: {file_path}, 错误: {e}")
        
        # 更新同步文件列表，只保留有效文件
        self.synced_files_list = valid_files
        self.synced_size = total_size  # 使用验证后的总大小更新synced_size
        self.synced_files = len(valid_files)  # 使用验证后的文件数更新synced_files
        
        print(f"验证后的总大小: {total_size} 字节, 有效文件数: {len(valid_files)}")
        print(f"格式化后的大小: {total_size / (1024 * 1024):.2f} MB 或 {total_size / (1024 * 1024 * 1024):.2f} GB")
        return total_size  # 返回验证后的总大小
    
    def stop(self):
        """停止同步过程"""
        self.running = False
# 媒体库同步插件类
class MediaSyncPlugin(PluginBase):
    """跨设备媒体库同步插件"""
    
    def __init__(self, app_instance=None):
        super().__init__(app_instance)
        self.name = "跨设备媒体库同步"
        self.version = "1.0.0"
        self.description = "实现PC、移动设备和智能电视之间的视频库同步，随时随地访问您的媒体收藏"
        self.author = "Claude"
        self.app = app_instance
        self.sync_button = None
        self.sync_thread = None
        self.webdav_thread = None
        self.sftp_thread = None
        self.onedrive_thread = None
        self.gdrive_thread = None
        self.smb_thread = None
        self.settings = {
            "local_media_dir": "",
            "external_media_dir": "",
            "webdav_url": "",
            "webdav_username": "",
            "webdav_password": "",
            "sftp_host": "",
            "sftp_port": 22,
            "sftp_username": "",
            "sftp_password": "",
            "sftp_private_key": "",
            "sftp_remote_dir": "",
            "onedrive_client_id": "",
            "onedrive_client_secret": "",
            "onedrive_remote_folder": "",
            "gdrive_credentials_file": "",
            "gdrive_remote_folder": "",
            "smb_server": "",
            "smb_share": "",
            "smb_username": "",
            "smb_password": "",
            "smb_remote_dir": "",
            "sync_mode": "bidirectional",
            "auto_sync": False,
            "auto_sync_interval": 60,  # 分钟
            "exclude_extensions": [".tmp", ".part", ".downloading"],
            "last_sync_time": "",
            "synced_files_count": 0,  # 已同步文件数
            "synced_total_size": 0    # 已同步总大小(字节)
        }
        self.load_settings()
        self.auto_sync_timer = None
    
    def get_setting(self, key, default=None):
        """获取插件设置"""
        return self.settings.get(key, default)
    
    def set_setting(self, key, value):
        """设置插件设置"""
        self.settings[key] = value
        self.save_settings()
    
    def cleanup_ui(self):
        """清理UI元素"""
        if hasattr(self, 'sync_button') and self.sync_button:
            try:
                # 从布局中移除按钮
                button = self.sync_button
                parent = button.parent()
                if parent:
                    layout = parent.layout()
                    if layout:
                        layout.removeWidget(button)
                button.setParent(None)  # 断开与父对象的连接
                print(f"已清理媒体库同步按钮 (ID: {button.objectName()})")
            except Exception as e:
                print(f"清理媒体库同步按钮失败: {e}")
        
        if self.auto_sync_timer:
            self.auto_sync_timer.stop()
            self.auto_sync_timer = None
    
    def initialize(self):
        """初始化插件"""
        print("媒体库同步插件已初始化")
        self.add_sync_button()
        
        # 设置自动同步定时器
        if self.get_setting("auto_sync", False):
            self.start_auto_sync_timer()
        
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
        print("媒体库同步插件已启动")
    
    def on_shutdown(self):
        """应用关闭时执行"""
        print("媒体库同步插件即将关闭")
        # 停止所有正在运行的同步线程
        if self.sync_thread and self.sync_thread.isRunning():
            self.sync_thread.stop()
            self.sync_thread.wait()
        
        if self.webdav_thread and self.webdav_thread.isRunning():
            self.webdav_thread.stop()
            self.webdav_thread.wait()
        
        if self.sftp_thread and self.sftp_thread.isRunning():
            self.sftp_thread.stop()
            self.sftp_thread.wait()
        
        if self.onedrive_thread and self.onedrive_thread.isRunning():
            self.onedrive_thread.stop()
            self.onedrive_thread.wait()
        
        if self.gdrive_thread and self.gdrive_thread.isRunning():
            self.gdrive_thread.stop()
            self.gdrive_thread.wait()
        
        if self.smb_thread and self.smb_thread.isRunning():
            self.smb_thread.stop()
            self.smb_thread.wait()
    
    def on_disable(self):
        """插件被禁用时执行"""
        print("媒体库同步插件被禁用")
        self.cleanup_ui()
    
    def add_sync_button(self):
        """添加同步按钮到主界面"""
        if not self.app:
            return
        
        from PyQt5.QtWidgets import QPushButton
        from PyQt5.QtCore import QSize, Qt
        from PyQt5.QtGui import QIcon
        import os
        
        # 先清理可能存在的重复按钮
        if hasattr(self, 'sync_button') and self.sync_button:
            try:
                # 从布局中移除按钮
                button = self.sync_button
                parent = button.parent()
                if parent:
                    layout = parent.layout()
                    if layout:
                        layout.removeWidget(button)
                button.setParent(None)  # 断开与父对象的连接
            except Exception as e:
                print(f"清理媒体库同步按钮失败: {e}")
        
        # 创建同步按钮
        self.sync_button = QPushButton("媒体库同步")
        
        # 设置唯一对象名
        button_id = f"media_sync_button_{id(self)}"
        self.sync_button.setObjectName(button_id)
        
        # 设置图标
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_icon.png")
        if os.path.exists(icon_path):
            self.sync_button.setIcon(QIcon(icon_path))
            self.sync_button.setIconSize(QSize(20, 20))
            print(f"已加载同步图标: {icon_path}")
        else:
            print(f"同步图标不存在: {icon_path}")
        
        # 设置样式
        self.sync_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                border-radius: 5px;
                padding: 5px 10px;
                padding-left: 8px;  /* 为图标留出空间 */
                font-weight: bold;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
        """)
        self.sync_button.setCursor(Qt.PointingHandCursor)
        
        # 设置固定宽度，与其他按钮保持一致
        self.sync_button.setFixedWidth(100)
        
        # 连接点击事件
        self.sync_button.clicked.connect(self.show_sync_dialog)
        
        # 添加按钮到布局，优先放在字幕按钮旁边
        try:
            # 如果主界面有history_layout和subtitle_btn
            if hasattr(self.app, 'history_layout') and hasattr(self.app, 'subtitle_btn'):
                # 找到字幕按钮的索引
                for i in range(self.app.history_layout.count()):
                    item = self.app.history_layout.itemAt(i)
                    if item and item.widget() == self.app.subtitle_btn:
                        # 找到字幕按钮后，在其右侧插入媒体库同步按钮
                        self.app.history_layout.insertWidget(i + 1, self.sync_button)
                        print(f"已添加媒体库同步按钮到字幕按钮旁边 (ID: {button_id})")
                        return
                
                # 如果找不到字幕按钮但history_layout存在，直接添加到布局中
                self.app.history_layout.addWidget(self.sync_button)
                print(f"已添加媒体库同步按钮到history_layout (ID: {button_id})")
            elif hasattr(self.app, 'toolbar_layout'):
                # 如果找不到history_layout或字幕按钮，尝试添加到toolbar_layout
                self.app.toolbar_layout.addWidget(self.sync_button)
                print(f"已添加媒体库同步按钮到toolbar_layout (ID: {button_id})")
            else:
                # 尝试添加到主窗口
                self.sync_button.setParent(self.app)
                self.sync_button.show()
                print(f"已添加媒体库同步按钮到主窗口 (ID: {button_id})")
        except Exception as e:
            print(f"添加媒体库同步按钮失败: {e}")
    
    def show_sync_dialog(self):
        """显示媒体库同步对话框"""
        dialog = MediaSyncDialog(self.app, self)
        dialog.exec_()
    
    def start_auto_sync_timer(self):
        """启动自动同步定时器"""
        if self.auto_sync_timer:
            self.auto_sync_timer.stop()
        
        interval_minutes = self.get_setting("auto_sync_interval", 60)
        interval_ms = interval_minutes * 60 * 1000
        
        self.auto_sync_timer = QTimer()
        self.auto_sync_timer.setInterval(interval_ms)
        self.auto_sync_timer.timeout.connect(self.auto_sync)
        self.auto_sync_timer.start()
        
        print(f"已启动自动同步定时器，间隔: {interval_minutes} 分钟")
    
    def auto_sync(self):
        """执行自动同步"""
        print("开始执行自动同步...")
        
        # 检查是否配置了同步目录
        local_dir = self.get_setting("local_media_dir", "")
        external_dir = self.get_setting("external_media_dir", "")
        webdav_url = self.get_setting("webdav_url", "")
        
        # 优先使用WebDAV同步
        if webdav_url and local_dir:
            self.start_webdav_sync(
                local_dir, 
                webdav_url, 
                self.get_setting("webdav_username", ""), 
                self.get_setting("webdav_password", ""), 
                self.get_setting("sync_mode", "bidirectional"),
                auto_mode=True
            )
        # 否则使用本地目录同步
        elif local_dir and external_dir:
            self.start_local_sync(
                local_dir, 
                external_dir, 
                self.get_setting("sync_mode", "bidirectional"),
                auto_mode=True
            )
        else:
            print("自动同步失败：未配置同步目录")
    
    def start_local_sync(self, source_dir, target_dir, sync_mode, auto_mode=False):
        """开始本地同步"""
        if not source_dir or not target_dir:
            if not auto_mode:
                QMessageBox.warning(self.app, "同步错误", "请先设置源目录和目标目录")
            return
        
        # 检查目录是否存在
        if not os.path.exists(source_dir):
            if not auto_mode:
                QMessageBox.warning(self.app, "同步错误", f"源目录不存在: {source_dir}")
            return
        
        # 停止正在运行的同步线程
        if self.sync_thread and self.sync_thread.isRunning():
            self.sync_thread.stop()
            self.sync_thread.wait()
        
        # 创建并启动同步线程
        self.plugin.sync_thread = MediaSyncThread(local_dir, external_dir, sync_mode, exclude_exts)
        self.plugin.sync_thread.progress_updated.connect(self.update_local_progress)

        # 修改这一行，使用包装方法
        self.plugin.sync_thread.sync_complete.connect(self.on_local_sync_complete_wrapper)

        self.plugin.sync_thread.start()
        
        # 记录同步开始时间
        self.set_setting("last_sync_time", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    def on_sync_complete_wrapper(self, success, message, synced_files, synced_size):
        """同步完成信号的包装处理函数"""
        print(f"接收到同步完成信号，文件数: {synced_files}, 大小: {synced_size} 字节")  # 添加这行日志
        self.on_sync_complete(success, message, synced_files, synced_size, self.auto_mode)
    def on_local_sync_complete_wrapper(self, success, message, synced_files, synced_size):
        """本地同步完成信号的包装处理函数"""
        print(f"接收到本地同步完成信号，文件数: {synced_files}, 大小: {synced_size} 字节")
        # 直接使用接收到的参数，不要从其他地方获取
        self.on_local_sync_complete(success, message, synced_files, synced_size)
    def start_webdav_sync(self, local_dir, webdav_url, username, password, sync_mode, auto_mode=False):
        """开始WebDAV同步"""
        if not local_dir or not webdav_url:
            if not auto_mode:
                QMessageBox.warning(self, "同步错误", "请先设置本地目录和WebDAV URL")
            return
        
        # 检查本地目录是否存在
        if not os.path.exists(local_dir):
            if not auto_mode:
                QMessageBox.warning(self, "同步错误", f"本地目录不存在: {local_dir}")
            return
        
        # 停止正在运行的同步线程
        if self.webdav_thread and self.webdav_thread.isRunning():
            self.webdav_thread.stop()
            self.webdav_thread.wait()
        
        # 创建并启动新的WebDAV同步线程
        exclude_exts = self.get_setting("exclude_extensions", [])
        self.webdav_thread = WebDAVSyncThread(local_dir, webdav_url, username, password, sync_mode, exclude_exts)
        self.webdav_thread.progress_updated.connect(self.update_sync_progress)
        self.webdav_thread.sync_complete.connect(lambda success, msg, files, size: self.on_sync_complete(success, msg, files, size, auto_mode))
        self.webdav_thread.start()
        
        # 记录同步开始时间
        self.set_setting("last_sync_time", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    def update_sync_progress(self, value, message):
        """更新同步进度"""
        # 在自动模式下，只打印日志
        print(f"同步进度: {value}% - {message}")
    
    def on_sync_complete(self, success, message, synced_files, synced_size, auto_mode=False):
        """同步完成处理"""
        if success:
            # 计算总大小的字符串表示（与对话框中一致）
            size_str = self._format_size(synced_size)
            print(f"同步成功: {message}, 已同步 {synced_files} 个文件, 总大小 {size_str}")
            
            # 更新同步统计信息
            self.settings["synced_files_count"] = synced_files
            self.settings["synced_total_size"] = synced_size
            # 立即保存设置到文件
            self.save_settings()
            
            # 如果对话框存在，更新其UI
            for widget in QApplication.topLevelWidgets():
                if isinstance(widget, MediaSyncDialog):
                    widget.update_sync_status()
                    break
            
            if not auto_mode:
                QMessageBox.information(self.app, "同步完成", f"同步成功完成\n已同步 {synced_files} 个文件, 总大小 {size_str}")
        else:
            print(f"同步失败: {message}")
            if not auto_mode:
                QMessageBox.warning(self.app, "同步失败", message)
    
    def _format_size(self, size_bytes):
        """将字节数格式化为可读的大小字符串"""
        # 转换为MB
        size_mb = size_bytes / (1024 * 1024)
        # 如果大于1000MB，则转换为GB显示
        if size_mb > 1000:
            return f"{size_mb/1024:.2f} GB"
        else:
            return f"{size_mb:.2f} MB"
    
    def load_settings(self):
        """加载插件设置"""
        try:
            settings_file = os.path.join(os.path.expanduser('~'), '.media_sync_plugin_settings.json')
            if os.path.exists(settings_file):
                with open(settings_file, 'r', encoding='utf-8') as f:
                    saved_settings = json.load(f)
                    # 更新设置，保留默认值
                    for key, value in saved_settings.items():
                        self.settings[key] = value
                print("媒体库同步插件设置已加载")
        except Exception as e:
            print(f"加载媒体库同步插件设置失败: {e}")
    
    def save_settings(self):
        """保存插件设置"""
        try:
            settings_file = os.path.join(os.path.expanduser('~'), '.media_sync_plugin_settings.json')
            with open(settings_file, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, ensure_ascii=False, indent=2)
            print("媒体库同步插件设置已保存")
        except Exception as e:
            print(f"保存媒体库同步插件设置失败: {e}")

# 媒体库同步对话框
class MediaSyncDialog(QDialog):
    """媒体库同步设置和控制对话框"""
    
    def __init__(self, parent, plugin):
        super().__init__(parent)
        self.plugin = plugin
        # 去除右上角的问号按钮
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowContextHelpButtonHint)
        
        # 确保插件设置中有正确的统计信息
        if "synced_files_count" not in self.plugin.settings:
            self.plugin.settings["synced_files_count"] = 0
        if "synced_total_size" not in self.plugin.settings:
            self.plugin.settings["synced_total_size"] = 0
            
        self.init_ui()
        
        # 初始化后立即更新同步状态
        self.update_sync_status()
    
    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle("跨设备媒体库同步")
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
        QTabWidget::pane {
            border: 1px solid #cccccc;
            border-radius: 3px;
            background-color: white;
        }
        QTabBar::tab {
            background-color: #e0e0e0;
            border: 1px solid #cccccc;
            border-bottom: none;
            border-top-left-radius: 3px;
            border-top-right-radius: 3px;
            padding: 5px 10px;
            margin-right: 2px;
        }
        QTabBar::tab:selected {
            background-color: white;
            border-bottom: 1px solid white;
        }
        QTabBar::tab:!selected {
            margin-top: 2px;
        }
        """)
        
        # 主布局
        main_layout = QVBoxLayout()
        main_layout.setSpacing(10)
        main_layout.setContentsMargins(15, 15, 15, 15)
        
        # 创建标签页
        tab_widget = QTabWidget()
        
        # 本地同步标签页
        local_sync_tab = QWidget()
        tab_widget.addTab(local_sync_tab, "本地同步")
        
        # WebDAV同步标签页
        webdav_sync_tab = QWidget()
        tab_widget.addTab(webdav_sync_tab, "WebDAV同步")
        
        # SFTP同步标签页
        sftp_sync_tab = QWidget()
        tab_widget.addTab(sftp_sync_tab, "SFTP同步")
        
        # OneDrive同步标签页
        onedrive_sync_tab = QWidget()
        tab_widget.addTab(onedrive_sync_tab, "OneDrive同步")
        
        # Google Drive同步标签页
        gdrive_sync_tab = QWidget()
        tab_widget.addTab(gdrive_sync_tab, "Google Drive同步")
        
        # SMB网络共享同步标签页
        smb_sync_tab = QWidget()
        tab_widget.addTab(smb_sync_tab, "SMB网络共享同步")
        
        # 同步设置标签页
        settings_tab = QWidget()
        tab_widget.addTab(settings_tab, "同步设置")
        
        # 同步状态标签页
        status_tab = QWidget()
        tab_widget.addTab(status_tab, "同步状态")
        
        # 设置各标签页内容
        self.setup_local_sync_tab(local_sync_tab)
        self.setup_webdav_sync_tab(webdav_sync_tab)
        self.setup_sftp_sync_tab(sftp_sync_tab)
        self.setup_onedrive_sync_tab(onedrive_sync_tab)
        self.setup_gdrive_sync_tab(gdrive_sync_tab)
        self.setup_smb_sync_tab(smb_sync_tab)
        self.setup_settings_tab(settings_tab)
        self.setup_status_tab(status_tab)
        
        # 添加标签页到主布局
        main_layout.addWidget(tab_widget)
        
        # 底部按钮
        button_layout = QHBoxLayout()
        
        close_btn = QPushButton("关闭")
        close_btn.setStyleSheet("""
            QPushButton {
                background-color: #757575;
                min-width: 80px;
            }
            QPushButton:hover {
                background-color: #616161;
            }
            QPushButton:pressed {
                background-color: #424242;
            }
        """)
        close_btn.clicked.connect(self.accept)
        
        button_layout.addStretch()
        button_layout.addWidget(close_btn)
        
        main_layout.addLayout(button_layout)
        
        self.setLayout(main_layout)
        
        # 加载设置
        self.load_settings()
    
    def setup_local_sync_tab(self, tab):
        """设置本地同步标签页"""
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 本地媒体库目录
        local_dir_group = QGroupBox("本地媒体库目录")
        local_dir_layout = QHBoxLayout()
        local_dir_layout.setContentsMargins(15, 15, 15, 15)
        
        self.local_dir_edit = QLineEdit()
        self.local_dir_edit.setPlaceholderText("选择本地媒体库目录")
        
        browse_local_btn = QPushButton("浏览...")
        browse_local_btn.setFixedWidth(80)
        browse_local_btn.clicked.connect(self.browse_local_dir)
        
        local_dir_layout.addWidget(self.local_dir_edit)
        local_dir_layout.addWidget(browse_local_btn)
        
        local_dir_group.setLayout(local_dir_layout)
        
        # 外部媒体库目录
        external_dir_group = QGroupBox("外部媒体库目录")
        external_dir_layout = QHBoxLayout()
        external_dir_layout.setContentsMargins(15, 15, 15, 15)
        
        self.external_dir_edit = QLineEdit()
        self.external_dir_edit.setPlaceholderText("选择外部媒体库目录（移动硬盘、网络共享等）")
        
        browse_external_btn = QPushButton("浏览...")
        browse_external_btn.setFixedWidth(80)
        browse_external_btn.clicked.connect(self.browse_external_dir)
        
        external_dir_layout.addWidget(self.external_dir_edit)
        external_dir_layout.addWidget(browse_external_btn)
        
        external_dir_group.setLayout(external_dir_layout)
        
        # 同步模式
        sync_mode_group = QGroupBox("同步模式")
        sync_mode_layout = QVBoxLayout()
        sync_mode_layout.setContentsMargins(15, 15, 15, 15)
        sync_mode_layout.setSpacing(10)
        
        self.upload_radio = QRadioButton("上传（本地→外部）")
        self.download_radio = QRadioButton("下载（外部→本地）")
        self.bidirectional_radio = QRadioButton("双向同步（保留最新版本）")
        
        self.bidirectional_radio.setChecked(True)
        
        sync_mode_layout.addWidget(self.upload_radio)
        sync_mode_layout.addWidget(self.download_radio)
        sync_mode_layout.addWidget(self.bidirectional_radio)
        
        sync_mode_group.setLayout(sync_mode_layout)
        
        # 同步进度
        progress_group = QGroupBox("同步进度")
        progress_layout = QVBoxLayout()
        progress_layout.setContentsMargins(15, 15, 15, 15)
        progress_layout.setSpacing(10)
        
        self.local_progress_bar = QProgressBar()
        self.local_progress_bar.setValue(0)
        
        self.local_status_label = QLabel("准备就绪")
        self.local_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        
        progress_layout.addWidget(self.local_progress_bar)
        progress_layout.addWidget(self.local_status_label)
        
        progress_group.setLayout(progress_layout)
        
        # 同步按钮
        sync_btn_layout = QHBoxLayout()
        sync_btn_layout.setSpacing(10)
        
        self.start_local_sync_btn = QPushButton("开始同步")
        self.start_local_sync_btn.setIcon(QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_icon.png")))
        self.start_local_sync_btn.clicked.connect(self.start_local_sync)
        
        self.stop_local_sync_btn = QPushButton("停止同步")
        self.stop_local_sync_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
            }
            QPushButton:hover {
                background-color: #e53935;
            }
            QPushButton:pressed {
                background-color: #d32f2f;
            }
        """)
        self.stop_local_sync_btn.clicked.connect(self.stop_local_sync)
        self.stop_local_sync_btn.setEnabled(False)
        
        sync_btn_layout.addWidget(self.start_local_sync_btn)
        sync_btn_layout.addWidget(self.stop_local_sync_btn)
        
        # 添加到布局
        layout.addWidget(local_dir_group)
        layout.addWidget(external_dir_group)
        layout.addWidget(sync_mode_group)
        layout.addWidget(progress_group)
        layout.addLayout(sync_btn_layout)
        layout.addStretch()
        
        tab.setLayout(layout)
    
    def setup_webdav_sync_tab(self, tab):
        """设置WebDAV同步标签页"""
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 本地媒体库目录
        local_dir_group = QGroupBox("本地媒体库目录")
        local_dir_layout = QHBoxLayout()
        local_dir_layout.setContentsMargins(15, 15, 15, 15)
        
        self.webdav_local_dir_edit = QLineEdit()
        self.webdav_local_dir_edit.setPlaceholderText("选择本地媒体库目录")
        
        browse_local_btn = QPushButton("浏览...")
        browse_local_btn.setFixedWidth(80)
        browse_local_btn.clicked.connect(self.browse_webdav_local_dir)
        
        local_dir_layout.addWidget(self.webdav_local_dir_edit)
        local_dir_layout.addWidget(browse_local_btn)
        
        local_dir_group.setLayout(local_dir_layout)
        
        # WebDAV服务器设置
        webdav_group = QGroupBox("WebDAV服务器设置")
        webdav_layout = QFormLayout()
        webdav_layout.setContentsMargins(15, 15, 15, 15)
        webdav_layout.setSpacing(15)
        webdav_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        
        # 添加HTTP/HTTPS切换按钮
        protocol_layout = QHBoxLayout()
        
        self.http_radio = QRadioButton("HTTP")
        self.https_radio = QRadioButton("HTTPS")
        self.https_radio.setChecked(True)  # 默认选择HTTPS
        
        # 设置按钮样式
        protocol_style = """
            QRadioButton {
                background-color: #f0f0f0;
                border: 1px solid #cccccc;
                border-radius: 3px;
                padding: 5px 10px;
                margin-right: 10px;
            }
            QRadioButton:checked {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
            }
            QRadioButton:hover {
                background-color: #e0e0e0;
            }
            QRadioButton:checked:hover {
                background-color: #45a049;
            }
        """
        self.http_radio.setStyleSheet(protocol_style)
        self.https_radio.setStyleSheet(protocol_style)
        
        # 连接切换事件
        self.http_radio.toggled.connect(self.update_webdav_protocol)
        self.https_radio.toggled.connect(self.update_webdav_protocol)
        
        protocol_layout.addWidget(self.http_radio)
        protocol_layout.addWidget(self.https_radio)
        protocol_layout.addStretch()
        
        webdav_layout.addRow("协议:", protocol_layout)
        
        # URL输入框
        url_layout = QHBoxLayout()
        self.webdav_protocol_label = QLabel("https://")
        self.webdav_protocol_label.setStyleSheet("font-weight: bold; color: #4CAF50;")
        
        self.webdav_url_edit = QLineEdit()
        self.webdav_url_edit.setPlaceholderText("example.com/webdav/")
        
        # 添加端口号输入框
        self.port_label = QLabel("端口:")
        self.port_label.setStyleSheet("margin-left: 10px;")
        self.port_edit = QSpinBox()
        self.port_edit.setRange(1, 65535)
        self.port_edit.setValue(80)  # HTTP默认端口
        self.port_edit.setFixedWidth(70)
        self.port_edit.setStyleSheet("""
            QSpinBox {
                border: 1px solid #cccccc;
                border-radius: 3px;
                padding: 4px;
            }
        """)
        
        # 默认隐藏端口输入框，只在HTTP模式下显示
        self.port_label.setVisible(False)
        self.port_edit.setVisible(False)
        
        url_layout.addWidget(self.webdav_protocol_label)
        url_layout.addWidget(self.webdav_url_edit)
        url_layout.addWidget(self.port_label)
        url_layout.addWidget(self.port_edit)
        url_layout.addStretch()
        
        webdav_layout.addRow("服务器地址:", url_layout)
        
        # 用户名和密码
        self.webdav_username_edit = QLineEdit()
        self.webdav_username_edit.setPlaceholderText("输入WebDAV用户名")
        
        self.webdav_password_edit = QLineEdit()
        self.webdav_password_edit.setEchoMode(QLineEdit.Password)
        self.webdav_password_edit.setPlaceholderText("输入WebDAV密码")
        
        self.test_webdav_btn = QPushButton("测试连接")
        self.test_webdav_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                max-width: 120px;
            }
            QPushButton:hover {
                background-color: #1E88E5;
            }
            QPushButton:pressed {
                background-color: #1976D2;
            }
        """)
        self.test_webdav_btn.clicked.connect(self.test_webdav_connection)
        
        webdav_layout.addRow("用户名:", self.webdav_username_edit)
        webdav_layout.addRow("密码:", self.webdav_password_edit)
        webdav_layout.addRow("", self.test_webdav_btn)
        
        webdav_group.setLayout(webdav_layout)
        
        # 同步模式
        webdav_sync_mode_group = QGroupBox("同步模式")
        webdav_sync_mode_layout = QVBoxLayout()
        webdav_sync_mode_layout.setContentsMargins(15, 15, 15, 15)
        webdav_sync_mode_layout.setSpacing(10)
        
        self.webdav_upload_radio = QRadioButton("上传（本地→WebDAV）")
        self.webdav_download_radio = QRadioButton("下载（WebDAV→本地）")
        self.webdav_bidirectional_radio = QRadioButton("双向同步（保留最新版本）")
        
        self.webdav_bidirectional_radio.setChecked(True)
        
        webdav_sync_mode_layout.addWidget(self.webdav_upload_radio)
        webdav_sync_mode_layout.addWidget(self.webdav_download_radio)
        webdav_sync_mode_layout.addWidget(self.webdav_bidirectional_radio)
        
        webdav_sync_mode_group.setLayout(webdav_sync_mode_layout)
        
        # 同步进度
        webdav_progress_group = QGroupBox("同步进度")
        webdav_progress_layout = QVBoxLayout()
        webdav_progress_layout.setContentsMargins(15, 15, 15, 15)
        webdav_progress_layout.setSpacing(10)
        
        self.webdav_progress_bar = QProgressBar()
        self.webdav_progress_bar.setValue(0)
        
        self.webdav_status_label = QLabel("准备就绪")
        self.webdav_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        
        webdav_progress_layout.addWidget(self.webdav_progress_bar)
        webdav_progress_layout.addWidget(self.webdav_status_label)
        
        webdav_progress_group.setLayout(webdav_progress_layout)
        
        # 同步按钮
        webdav_sync_btn_layout = QHBoxLayout()
        webdav_sync_btn_layout.setSpacing(10)
        
        self.start_webdav_sync_btn = QPushButton("开始同步")
        self.start_webdav_sync_btn.setIcon(QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_icon.png")))
        self.start_webdav_sync_btn.clicked.connect(self.start_webdav_sync)
        
        self.stop_webdav_sync_btn = QPushButton("停止同步")
        self.stop_webdav_sync_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
            }
            QPushButton:hover {
                background-color: #e53935;
            }
            QPushButton:pressed {
                background-color: #d32f2f;
            }
        """)
        self.stop_webdav_sync_btn.clicked.connect(self.stop_webdav_sync)
        self.stop_webdav_sync_btn.setEnabled(False)
        
        webdav_sync_btn_layout.addWidget(self.start_webdav_sync_btn)
        webdav_sync_btn_layout.addWidget(self.stop_webdav_sync_btn)
        
        # 添加到布局
        layout.addWidget(local_dir_group)
        layout.addWidget(webdav_group)
        layout.addWidget(webdav_sync_mode_group)
        layout.addWidget(webdav_progress_group)
        layout.addLayout(webdav_sync_btn_layout)
        layout.addStretch()
        
        tab.setLayout(layout)
    def setup_sftp_sync_tab(self, tab):
        """设置SFTP同步标签页"""
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 本地媒体库目录
        local_dir_group = QGroupBox("本地媒体库目录")
        local_dir_layout = QHBoxLayout()
        local_dir_layout.setContentsMargins(15, 15, 15, 15)
        
        self.sftp_local_dir_edit = QLineEdit()
        self.sftp_local_dir_edit.setPlaceholderText("选择本地媒体库目录")
        
        browse_local_btn = QPushButton("浏览...")
        browse_local_btn.setFixedWidth(80)
        browse_local_btn.clicked.connect(self.browse_sftp_local_dir)
        
        local_dir_layout.addWidget(self.sftp_local_dir_edit)
        local_dir_layout.addWidget(browse_local_btn)
        
        local_dir_group.setLayout(local_dir_layout)
        
        # SFTP服务器设置
        sftp_group = QGroupBox("SFTP服务器设置")
        sftp_layout = QFormLayout()
        sftp_layout.setContentsMargins(15, 15, 15, 15)
        sftp_layout.setSpacing(15)
        sftp_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        
        self.sftp_host_edit = QLineEdit()
        self.sftp_host_edit.setPlaceholderText("例如: sftp.example.com 或 192.168.1.100")
        
        self.sftp_port_edit = QSpinBox()
        self.sftp_port_edit.setRange(1, 65535)
        self.sftp_port_edit.setValue(22)
        
        self.sftp_username_edit = QLineEdit()
        self.sftp_username_edit.setPlaceholderText("SFTP用户名")
        
        self.sftp_password_edit = QLineEdit()
        self.sftp_password_edit.setPlaceholderText("SFTP密码")
        self.sftp_password_edit.setEchoMode(QLineEdit.Password)
        
        self.sftp_key_edit = QLineEdit()
        self.sftp_key_edit.setPlaceholderText("私钥文件路径（可选）")
        
        browse_key_btn = QPushButton("浏览...")
        browse_key_btn.setFixedWidth(80)
        browse_key_btn.clicked.connect(self.browse_sftp_key)
        
        key_layout = QHBoxLayout()
        key_layout.addWidget(self.sftp_key_edit)
        key_layout.addWidget(browse_key_btn)
        
        self.sftp_remote_dir_edit = QLineEdit()
        self.sftp_remote_dir_edit.setPlaceholderText("远程目录路径，例如: /home/user/media")
        
        sftp_layout.addRow("主机:", self.sftp_host_edit)
        sftp_layout.addRow("端口:", self.sftp_port_edit)
        sftp_layout.addRow("用户名:", self.sftp_username_edit)
        sftp_layout.addRow("密码:", self.sftp_password_edit)
        sftp_layout.addRow("私钥文件:", key_layout)
        sftp_layout.addRow("远程目录:", self.sftp_remote_dir_edit)
        
        sftp_group.setLayout(sftp_layout)
        
        # 同步模式
        sftp_sync_mode_group = QGroupBox("同步模式")
        sftp_sync_mode_layout = QVBoxLayout()
        sftp_sync_mode_layout.setContentsMargins(15, 15, 15, 15)
        sftp_sync_mode_layout.setSpacing(10)
        
        self.sftp_upload_radio = QRadioButton("上传（本地→SFTP）")
        self.sftp_download_radio = QRadioButton("下载（SFTP→本地）")
        self.sftp_bidirectional_radio = QRadioButton("双向同步（保留最新版本）")
        
        self.sftp_bidirectional_radio.setChecked(True)
        
        sftp_sync_mode_layout.addWidget(self.sftp_upload_radio)
        sftp_sync_mode_layout.addWidget(self.sftp_download_radio)
        sftp_sync_mode_layout.addWidget(self.sftp_bidirectional_radio)
        
        sftp_sync_mode_group.setLayout(sftp_sync_mode_layout)
        
        # 同步进度
        sftp_progress_group = QGroupBox("同步进度")
        sftp_progress_layout = QVBoxLayout()
        sftp_progress_layout.setContentsMargins(15, 15, 15, 15)
        sftp_progress_layout.setSpacing(10)
        
        self.sftp_progress_bar = QProgressBar()
        self.sftp_progress_bar.setValue(0)
        
        self.sftp_status_label = QLabel("准备就绪")
        self.sftp_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        
        sftp_progress_layout.addWidget(self.sftp_progress_bar)
        sftp_progress_layout.addWidget(self.sftp_status_label)
        
        sftp_progress_group.setLayout(sftp_progress_layout)
        
        # 同步按钮
        sftp_sync_btn_layout = QHBoxLayout()
        sftp_sync_btn_layout.setSpacing(10)
        
        self.test_sftp_btn = QPushButton("测试连接")
        self.test_sftp_btn.clicked.connect(self.test_sftp_connection)
        
        self.start_sftp_sync_btn = QPushButton("开始同步")
        self.start_sftp_sync_btn.setIcon(QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_icon.png")))
        self.start_sftp_sync_btn.clicked.connect(self.start_sftp_sync)
        
        self.stop_sftp_sync_btn = QPushButton("停止同步")
        self.stop_sftp_sync_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
            }
            QPushButton:hover {
                background-color: #e53935;
            }
            QPushButton:pressed {
                background-color: #d32f2f;
            }
        """)
        self.stop_sftp_sync_btn.clicked.connect(self.stop_sftp_sync)
        self.stop_sftp_sync_btn.setEnabled(False)
        
        sftp_sync_btn_layout.addWidget(self.test_sftp_btn)
        sftp_sync_btn_layout.addWidget(self.start_sftp_sync_btn)
        sftp_sync_btn_layout.addWidget(self.stop_sftp_sync_btn)
        
        # 添加到布局
        layout.addWidget(local_dir_group)
        layout.addWidget(sftp_group)
        layout.addWidget(sftp_sync_mode_group)
        layout.addWidget(sftp_progress_group)
        layout.addLayout(sftp_sync_btn_layout)
        layout.addStretch()
        
        tab.setLayout(layout)

    def setup_onedrive_sync_tab(self, tab):
        """设置OneDrive同步标签页"""
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 本地媒体库目录
        local_dir_group = QGroupBox("本地媒体库目录")
        local_dir_layout = QHBoxLayout()
        local_dir_layout.setContentsMargins(15, 15, 15, 15)
        
        self.onedrive_local_dir_edit = QLineEdit()
        self.onedrive_local_dir_edit.setPlaceholderText("选择本地媒体库目录")
        
        browse_local_btn = QPushButton("浏览...")
        browse_local_btn.setFixedWidth(80)
        browse_local_btn.clicked.connect(self.browse_onedrive_local_dir)
        
        local_dir_layout.addWidget(self.onedrive_local_dir_edit)
        local_dir_layout.addWidget(browse_local_btn)
        
        local_dir_group.setLayout(local_dir_layout)
        
        # OneDrive设置
        onedrive_group = QGroupBox("OneDrive API设置")
        onedrive_layout = QFormLayout()
        onedrive_layout.setContentsMargins(15, 15, 15, 15)
        onedrive_layout.setSpacing(15)
        onedrive_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        
        self.onedrive_client_id_edit = QLineEdit()
        self.onedrive_client_id_edit.setPlaceholderText("Microsoft Azure应用程序客户端ID")
        
        self.onedrive_client_secret_edit = QLineEdit()
        self.onedrive_client_secret_edit.setPlaceholderText("Microsoft Azure应用程序客户端密钥")
        self.onedrive_client_secret_edit.setEchoMode(QLineEdit.Password)
        
        self.onedrive_folder_edit = QLineEdit()
        self.onedrive_folder_edit.setPlaceholderText("OneDrive中的文件夹路径，例如: /Media 或留空使用根目录")
        
        onedrive_layout.addRow("客户端ID:", self.onedrive_client_id_edit)
        onedrive_layout.addRow("客户端密钥:", self.onedrive_client_secret_edit)
        onedrive_layout.addRow("远程文件夹:", self.onedrive_folder_edit)
        
        onedrive_group.setLayout(onedrive_layout)
        
        # 同步模式
        onedrive_sync_mode_group = QGroupBox("同步模式")
        onedrive_sync_mode_layout = QVBoxLayout()
        onedrive_sync_mode_layout.setContentsMargins(15, 15, 15, 15)
        onedrive_sync_mode_layout.setSpacing(10)
        
        self.onedrive_upload_radio = QRadioButton("上传（本地→OneDrive）")
        self.onedrive_download_radio = QRadioButton("下载（OneDrive→本地）")
        self.onedrive_bidirectional_radio = QRadioButton("双向同步（保留最新版本）")
        
        self.onedrive_bidirectional_radio.setChecked(True)
        
        onedrive_sync_mode_layout.addWidget(self.onedrive_upload_radio)
        onedrive_sync_mode_layout.addWidget(self.onedrive_download_radio)
        onedrive_sync_mode_layout.addWidget(self.onedrive_bidirectional_radio)
        
        onedrive_sync_mode_group.setLayout(onedrive_sync_mode_layout)
        
        # 同步进度
        onedrive_progress_group = QGroupBox("同步进度")
        onedrive_progress_layout = QVBoxLayout()
        onedrive_progress_layout.setContentsMargins(15, 15, 15, 15)
        onedrive_progress_layout.setSpacing(10)
        
        self.onedrive_progress_bar = QProgressBar()
        self.onedrive_progress_bar.setValue(0)
        
        self.onedrive_status_label = QLabel("准备就绪")
        self.onedrive_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        
        onedrive_progress_layout.addWidget(self.onedrive_progress_bar)
        onedrive_progress_layout.addWidget(self.onedrive_status_label)
        
        onedrive_progress_group.setLayout(onedrive_progress_layout)
        
        # 同步按钮
        onedrive_sync_btn_layout = QHBoxLayout()
        onedrive_sync_btn_layout.setSpacing(10)
        
        self.start_onedrive_sync_btn = QPushButton("开始同步")
        self.start_onedrive_sync_btn.setIcon(QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_icon.png")))
        self.start_onedrive_sync_btn.clicked.connect(self.start_onedrive_sync)
        
        self.stop_onedrive_sync_btn = QPushButton("停止同步")
        self.stop_onedrive_sync_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
            }
            QPushButton:hover {
                background-color: #e53935;
            }
            QPushButton:pressed {
                background-color: #d32f2f;
            }
        """)
        self.stop_onedrive_sync_btn.clicked.connect(self.stop_onedrive_sync)
        self.stop_onedrive_sync_btn.setEnabled(False)
        
        onedrive_sync_btn_layout.addWidget(self.start_onedrive_sync_btn)
        onedrive_sync_btn_layout.addWidget(self.stop_onedrive_sync_btn)
        
        # 添加到布局
        layout.addWidget(local_dir_group)
        layout.addWidget(onedrive_group)
        layout.addWidget(onedrive_sync_mode_group)
        layout.addWidget(onedrive_progress_group)
        layout.addLayout(onedrive_sync_btn_layout)
        layout.addStretch()
        
        tab.setLayout(layout)

    def setup_gdrive_sync_tab(self, tab):
        """设置Google Drive同步标签页"""
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 本地媒体库目录
        local_dir_group = QGroupBox("本地媒体库目录")
        local_dir_layout = QHBoxLayout()
        local_dir_layout.setContentsMargins(15, 15, 15, 15)
        
        self.gdrive_local_dir_edit = QLineEdit()
        self.gdrive_local_dir_edit.setPlaceholderText("选择本地媒体库目录")
        
        browse_local_btn = QPushButton("浏览...")
        browse_local_btn.setFixedWidth(80)
        browse_local_btn.clicked.connect(self.browse_gdrive_local_dir)
        
        local_dir_layout.addWidget(self.gdrive_local_dir_edit)
        local_dir_layout.addWidget(browse_local_btn)
        
        local_dir_group.setLayout(local_dir_layout)
        
        # Google Drive设置
        gdrive_group = QGroupBox("Google Drive API设置")
        gdrive_layout = QFormLayout()
        gdrive_layout.setContentsMargins(15, 15, 15, 15)
        gdrive_layout.setSpacing(15)
        gdrive_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        
        self.gdrive_credentials_edit = QLineEdit()
        self.gdrive_credentials_edit.setPlaceholderText("Google API凭据文件路径 (credentials.json)")
        
        browse_creds_btn = QPushButton("浏览...")
        browse_creds_btn.setFixedWidth(80)
        browse_creds_btn.clicked.connect(self.browse_gdrive_credentials)
        
        creds_layout = QHBoxLayout()
        creds_layout.addWidget(self.gdrive_credentials_edit)
        creds_layout.addWidget(browse_creds_btn)
        
        self.gdrive_folder_edit = QLineEdit()
        self.gdrive_folder_edit.setPlaceholderText("Google Drive中的文件夹名称，例如: Media 或留空使用根目录")
        
        gdrive_layout.addRow("凭据文件:", creds_layout)
        gdrive_layout.addRow("远程文件夹:", self.gdrive_folder_edit)
        
        gdrive_group.setLayout(gdrive_layout)
        
        # 同步模式
        gdrive_sync_mode_group = QGroupBox("同步模式")
        gdrive_sync_mode_layout = QVBoxLayout()
        gdrive_sync_mode_layout.setContentsMargins(15, 15, 15, 15)
        gdrive_sync_mode_layout.setSpacing(10)
        
        self.gdrive_upload_radio = QRadioButton("上传（本地→Google Drive）")
        self.gdrive_download_radio = QRadioButton("下载（Google Drive→本地）")
        self.gdrive_bidirectional_radio = QRadioButton("双向同步（保留最新版本）")
        
        self.gdrive_bidirectional_radio.setChecked(True)
        
        gdrive_sync_mode_layout.addWidget(self.gdrive_upload_radio)
        gdrive_sync_mode_layout.addWidget(self.gdrive_download_radio)
        gdrive_sync_mode_layout.addWidget(self.gdrive_bidirectional_radio)
        
        gdrive_sync_mode_group.setLayout(gdrive_sync_mode_layout)
        
        # 同步进度
        gdrive_progress_group = QGroupBox("同步进度")
        gdrive_progress_layout = QVBoxLayout()
        gdrive_progress_layout.setContentsMargins(15, 15, 15, 15)
        gdrive_progress_layout.setSpacing(10)
        
        self.gdrive_progress_bar = QProgressBar()
        self.gdrive_progress_bar.setValue(0)
        
        self.gdrive_status_label = QLabel("准备就绪")
        self.gdrive_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        
        gdrive_progress_layout.addWidget(self.gdrive_progress_bar)
        gdrive_progress_layout.addWidget(self.gdrive_status_label)
        
        gdrive_progress_group.setLayout(gdrive_progress_layout)
        
        # 同步按钮
        gdrive_sync_btn_layout = QHBoxLayout()
        gdrive_sync_btn_layout.setSpacing(10)
        
        self.start_gdrive_sync_btn = QPushButton("开始同步")
        self.start_gdrive_sync_btn.setIcon(QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_icon.png")))
        self.start_gdrive_sync_btn.clicked.connect(self.start_gdrive_sync)
        
        self.stop_gdrive_sync_btn = QPushButton("停止同步")
        self.stop_gdrive_sync_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
            }
            QPushButton:hover {
                background-color: #e53935;
            }
            QPushButton:pressed {
                background-color: #d32f2f;
            }
        """)
        self.stop_gdrive_sync_btn.clicked.connect(self.stop_gdrive_sync)
        self.stop_gdrive_sync_btn.setEnabled(False)
        
        gdrive_sync_btn_layout.addWidget(self.start_gdrive_sync_btn)
        gdrive_sync_btn_layout.addWidget(self.stop_gdrive_sync_btn)
        
        # 添加到布局
        layout.addWidget(local_dir_group)
        layout.addWidget(gdrive_group)
        layout.addWidget(gdrive_sync_mode_group)
        layout.addWidget(gdrive_progress_group)
        layout.addLayout(gdrive_sync_btn_layout)
        layout.addStretch()
        
        tab.setLayout(layout)

    def setup_smb_sync_tab(self, tab):
        """设置SMB网络共享同步标签页"""
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 本地媒体库目录
        local_dir_group = QGroupBox("本地媒体库目录")
        local_dir_layout = QHBoxLayout()
        local_dir_layout.setContentsMargins(15, 15, 15, 15)
        
        self.smb_local_dir_edit = QLineEdit()
        self.smb_local_dir_edit.setPlaceholderText("选择本地媒体库目录")
        
        browse_local_btn = QPushButton("浏览...")
        browse_local_btn.setFixedWidth(80)
        browse_local_btn.clicked.connect(self.browse_smb_local_dir)
        
        local_dir_layout.addWidget(self.smb_local_dir_edit)
        local_dir_layout.addWidget(browse_local_btn)
        
        local_dir_group.setLayout(local_dir_layout)
        
        # SMB网络共享设置
        smb_group = QGroupBox("SMB网络共享设置")
        smb_layout = QFormLayout()
        smb_layout.setContentsMargins(15, 15, 15, 15)
        smb_layout.setSpacing(15)
        smb_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        
        self.smb_server_edit = QLineEdit()
        self.smb_server_edit.setPlaceholderText("服务器名称或IP地址，例如: MYNAS 或 192.168.1.100")
        
        self.smb_share_edit = QLineEdit()
        self.smb_share_edit.setPlaceholderText("共享名称，例如: Media")
        
        self.smb_username_edit = QLineEdit()
        self.smb_username_edit.setPlaceholderText("用户名（可选）")
        
        self.smb_password_edit = QLineEdit()
        self.smb_password_edit.setPlaceholderText("密码（可选）")
        self.smb_password_edit.setEchoMode(QLineEdit.Password)
        
        self.smb_remote_dir_edit = QLineEdit()
        self.smb_remote_dir_edit.setPlaceholderText("共享中的目录路径，例如: /Media 或留空使用根目录")
        
        smb_layout.addRow("服务器:", self.smb_server_edit)
        smb_layout.addRow("共享名称:", self.smb_share_edit)
        smb_layout.addRow("用户名:", self.smb_username_edit)
        smb_layout.addRow("密码:", self.smb_password_edit)
        smb_layout.addRow("远程目录:", self.smb_remote_dir_edit)
        
        smb_group.setLayout(smb_layout)
        
        # 同步模式
        smb_sync_mode_group = QGroupBox("同步模式")
        smb_sync_mode_layout = QVBoxLayout()
        smb_sync_mode_layout.setContentsMargins(15, 15, 15, 15)
        smb_sync_mode_layout.setSpacing(10)
        
        self.smb_upload_radio = QRadioButton("上传（本地→SMB共享）")
        self.smb_download_radio = QRadioButton("下载（SMB共享→本地）")
        self.smb_bidirectional_radio = QRadioButton("双向同步（保留最新版本）")
        
        self.smb_bidirectional_radio.setChecked(True)
        
        smb_sync_mode_layout.addWidget(self.smb_upload_radio)
        smb_sync_mode_layout.addWidget(self.smb_download_radio)
        smb_sync_mode_layout.addWidget(self.smb_bidirectional_radio)
        
        smb_sync_mode_group.setLayout(smb_sync_mode_layout)
        
        # 同步进度
        smb_progress_group = QGroupBox("同步进度")
        smb_progress_layout = QVBoxLayout()
        smb_progress_layout.setContentsMargins(15, 15, 15, 15)
        smb_progress_layout.setSpacing(10)
        
        self.smb_progress_bar = QProgressBar()
        self.smb_progress_bar.setValue(0)
        
        self.smb_status_label = QLabel("准备就绪")
        self.smb_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        
        smb_progress_layout.addWidget(self.smb_progress_bar)
        smb_progress_layout.addWidget(self.smb_status_label)
        
        smb_progress_group.setLayout(smb_progress_layout)
        
        # 同步按钮
        smb_sync_btn_layout = QHBoxLayout()
        smb_sync_btn_layout.setSpacing(10)
        
        self.test_smb_btn = QPushButton("测试连接")
        self.test_smb_btn.clicked.connect(self.test_smb_connection)
        
        self.start_smb_sync_btn = QPushButton("开始同步")
        self.start_smb_sync_btn.setIcon(QIcon(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_icon.png")))
        self.start_smb_sync_btn.clicked.connect(self.start_smb_sync)
        
        self.stop_smb_sync_btn = QPushButton("停止同步")
        self.stop_smb_sync_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
            }
            QPushButton:hover {
                background-color: #e53935;
            }
            QPushButton:pressed {
                background-color: #d32f2f;
            }
        """)
        self.stop_smb_sync_btn.clicked.connect(self.stop_smb_sync)
        self.stop_smb_sync_btn.setEnabled(False)
        
        smb_sync_btn_layout.addWidget(self.test_smb_btn)
        smb_sync_btn_layout.addWidget(self.start_smb_sync_btn)
        smb_sync_btn_layout.addWidget(self.stop_smb_sync_btn)
        
        # 添加到布局
        layout.addWidget(local_dir_group)
        layout.addWidget(smb_group)
        layout.addWidget(smb_sync_mode_group)
        layout.addWidget(smb_progress_group)
        layout.addLayout(smb_sync_btn_layout)
        layout.addStretch()
        
        tab.setLayout(layout)
    def browse_sftp_local_dir(self):
        """浏览SFTP本地目录"""
        dir_path = QFileDialog.getExistingDirectory(self, "选择本地媒体库目录", self.sftp_local_dir_edit.text())
        if dir_path:
            self.sftp_local_dir_edit.setText(dir_path)
            self.plugin.set_setting("sftp_local_dir", dir_path)

    def browse_sftp_key(self):
        """浏览SFTP私钥文件"""
        file_path, _ = QFileDialog.getOpenFileName(self, "选择私钥文件", "", "所有文件 (*)")
        if file_path:
            self.sftp_key_edit.setText(file_path)
            self.plugin.set_setting("sftp_private_key", file_path)

    def browse_onedrive_local_dir(self):
        """浏览OneDrive本地目录"""
        dir_path = QFileDialog.getExistingDirectory(self, "选择本地媒体库目录", self.onedrive_local_dir_edit.text())
        if dir_path:
            self.onedrive_local_dir_edit.setText(dir_path)
            self.plugin.set_setting("onedrive_local_dir", dir_path)

    def browse_gdrive_local_dir(self):
        """浏览Google Drive本地目录"""
        dir_path = QFileDialog.getExistingDirectory(self, "选择本地媒体库目录", self.gdrive_local_dir_edit.text())
        if dir_path:
            self.gdrive_local_dir_edit.setText(dir_path)
            self.plugin.set_setting("gdrive_local_dir", dir_path)

    def browse_gdrive_credentials(self):
        """浏览Google Drive凭据文件"""
        file_path, _ = QFileDialog.getOpenFileName(self, "选择Google API凭据文件", "", "JSON文件 (*.json)")
        if file_path:
            self.gdrive_credentials_edit.setText(file_path)
            self.plugin.set_setting("gdrive_credentials_file", file_path)

    def browse_smb_local_dir(self):
        """浏览SMB本地目录"""
        dir_path = QFileDialog.getExistingDirectory(self, "选择本地媒体库目录", self.smb_local_dir_edit.text())
        if dir_path:
            self.smb_local_dir_edit.setText(dir_path)
            self.plugin.set_setting("smb_local_dir", dir_path)

    # 测试连接函数
    def test_sftp_connection(self):
        """测试SFTP连接"""
        self.test_sftp_btn.setText("正在测试...")
        self.test_sftp_btn.setEnabled(False)
        
        host = self.sftp_host_edit.text().strip()
        port = self.sftp_port_edit.value()
        username = self.sftp_username_edit.text().strip()
        password = self.sftp_password_edit.text()
        private_key = self.sftp_key_edit.text().strip()
        remote_dir = self.sftp_remote_dir_edit.text().strip()
        
        if not host or not username:
            QMessageBox.warning(self, "连接错误", "请输入主机和用户名")
            self.test_sftp_btn.setText("测试连接")
            self.test_sftp_btn.setEnabled(True)
            return
        
        # 创建一个临时线程进行连接测试
        class TestSFTPThread(QThread):
            test_complete = pyqtSignal(bool, str)
            
            def __init__(self, host, port, username, password, private_key, remote_dir):
                super().__init__()
                self.host = host
                self.port = port
                self.username = username
                self.password = password
                self.private_key = private_key
                self.remote_dir = remote_dir

            def run(self):
                try:
                    # 创建SSH客户端
                    ssh_client = paramiko.SSHClient()
                    ssh_client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
                    
                    # 使用私钥或密码进行认证
                    if self.private_key and os.path.exists(self.private_key):
                        try:
                            private_key = paramiko.RSAKey.from_private_key_file(self.private_key, password=self.password if self.password else None)
                            ssh_client.connect(
                                hostname=self.host,
                                port=self.port,
                                username=self.username,
                                pkey=private_key,
                                timeout=10
                            )
                        except Exception as e:
                            # 如果私钥认证失败，尝试密码认证
                            if self.password:
                                ssh_client.connect(
                                    hostname=self.host,
                                    port=self.port,
                                    username=self.username,
                                    password=self.password,
                                    timeout=10
                                )
                            else:
                                self.test_complete.emit(False, f"连接失败: {str(e)}")
                                return
                    else:
                        # 使用密码认证
                        if self.password:
                            ssh_client.connect(
                                hostname=self.host,
                                port=self.port,
                                username=self.username,
                                password=self.password,
                                timeout=10
                            )
                        else:
                            # 尝试使用SSH代理或默认密钥
                            ssh_client.connect(
                                hostname=self.host,
                                port=self.port,
                                username=self.username,
                                timeout=10
                            )
                    
                    # 创建SFTP客户端
                    sftp_client = ssh_client.open_sftp()
                    
                    # 测试远程目录是否存在
                    try:
                        if self.remote_dir:
                            sftp_client.stat(self.remote_dir)
                    except FileNotFoundError:
                        # 远程目录不存在
                        self.test_complete.emit(False, f"远程目录不存在: {self.remote_dir}")
                        return
                    
                    # 关闭连接
                    sftp_client.close()
                    ssh_client.close()
                    
                    self.test_complete.emit(True, "SFTP连接成功")
                except Exception as e:
                    self.test_complete.emit(False, f"连接失败: {str(e)}")
        
        # 创建并启动测试线程
        self.sftp_test_thread = TestSFTPThread(host, port, username, password, private_key, remote_dir)
        self.sftp_test_thread.test_complete.connect(self.on_sftp_test_result)
        self.sftp_test_thread.start()

    def on_sftp_test_result(self, success, message):
        """SFTP测试结果处理"""
        self.test_sftp_btn.setText("测试连接")
        self.test_sftp_btn.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "连接测试", message)
            self.add_log("SFTP连接测试成功")
        else:
            QMessageBox.warning(self, "连接测试", message)
            self.add_log(f"SFTP连接测试失败: {message}")

    def test_smb_connection(self):
        """测试SMB连接"""
        self.test_smb_btn.setText("正在测试...")
        self.test_smb_btn.setEnabled(False)
        
        server = self.smb_server_edit.text().strip()
        share = self.smb_share_edit.text().strip()
        username = self.smb_username_edit.text().strip()
        password = self.smb_password_edit.text()
        remote_dir = self.smb_remote_dir_edit.text().strip()
        
        if not server or not share:
            QMessageBox.warning(self, "连接错误", "请输入服务器名称和共享名称")
            self.test_smb_btn.setText("测试连接")
            self.test_smb_btn.setEnabled(True)
            return
        
        # 创建一个临时线程进行连接测试
        class TestSMBThread(QThread):
            test_complete = pyqtSignal(bool, str)
            
            def __init__(self, server, share, username, password, remote_dir):
                super().__init__()
                self.server = server
                self.share = share
                self.username = username
                self.password = password
                self.remote_dir = remote_dir
            
            def run(self):
                try:
                    # 获取本机计算机名
                    client_name = socket.gethostname()
                    
                    # 创建SMB连接
                    conn = smb.SMBConnection.SMBConnection(
                        self.username,
                        self.password,
                        client_name,
                        self.server,
                        use_ntlm_v2=True,
                        is_direct_tcp=True
                    )
                    
                    # 连接到服务器
                    try:
                        server_ip = socket.gethostbyname(self.server)
                        connected = conn.connect(server_ip, 445)  # 445是SMB直接TCP端口
                        
                        if not connected:
                            # 尝试使用NetBIOS端口
                            connected = conn.connect(server_ip, 139)
                    except:
                        # 如果通过主机名解析失败，尝试直接使用IP地址
                        connected = conn.connect(self.server, 445)
                        
                        if not connected:
                            # 尝试使用NetBIOS端口
                            connected = conn.connect(self.server, 139)
                    
                    if not connected:
                        self.test_complete.emit(False, "SMB连接失败")
                        return
                    
                    # 检查共享是否存在
                    shares = conn.listShares()
                    share_exists = False
                    for share_info in shares:
                        if share_info.name == self.share:
                            share_exists = True
                            break
                    
                    if not share_exists:
                        self.test_complete.emit(False, f"共享 '{self.share}' 不存在")
                        return
                    
                    # 检查远程目录是否存在
                    if self.remote_dir and self.remote_dir != "/":
                        try:
                            remote_dir = self.remote_dir
                            if remote_dir.startswith('/'):
                                remote_dir = remote_dir[1:]
                            conn.listPath(self.share, remote_dir)
                        except:
                            self.test_complete.emit(False, f"远程目录 '{self.remote_dir}' 不存在")
                            return
                    
                    # 关闭连接
                    conn.close()
                    
                    self.test_complete.emit(True, "SMB连接成功")
                except Exception as e:
                    self.test_complete.emit(False, f"连接失败: {str(e)}")
        
        # 创建并启动测试线程
        self.smb_test_thread = TestSMBThread(server, share, username, password, remote_dir)
        self.smb_test_thread.test_complete.connect(self.on_smb_test_result)
        self.smb_test_thread.start()

    def on_smb_test_result(self, success, message):
        """SMB测试结果处理"""
        self.test_smb_btn.setText("测试连接")
        self.test_smb_btn.setEnabled(True)
        
        if success:
            QMessageBox.information(self, "连接测试", message)
            self.add_log("SMB连接测试成功")
        else:
            QMessageBox.warning(self, "连接测试", message)
            self.add_log(f"SMB连接测试失败: {message}")

    # 开始同步函数
    def start_sftp_sync(self):
        """开始SFTP同步"""
        local_dir = self.sftp_local_dir_edit.text().strip()
        host = self.sftp_host_edit.text().strip()
        port = self.sftp_port_edit.value()
        username = self.sftp_username_edit.text().strip()
        password = self.sftp_password_edit.text()
        private_key = self.sftp_key_edit.text().strip()
        remote_dir = self.sftp_remote_dir_edit.text().strip()
        
        if not local_dir or not host or not username or not remote_dir:
            QMessageBox.warning(self, "同步错误", "请先设置本地目录、主机、用户名和远程目录")
            return
        
        # 检查本地目录是否存在
        if not os.path.exists(local_dir):
            QMessageBox.warning(self, "同步错误", f"本地目录不存在: {local_dir}")
            return
        
        # 确定同步模式
        sync_mode = "bidirectional"
        if self.sftp_upload_radio.isChecked():
            sync_mode = "upload"
        elif self.sftp_download_radio.isChecked():
            sync_mode = "download"
        
        # 更新UI状态
        self.start_sftp_sync_btn.setEnabled(False)
        self.stop_sftp_sync_btn.setEnabled(True)
        
        # 添加日志
        self.add_log(f"开始SFTP同步: {sync_mode} 模式")
        self.add_log(f"本地目录: {local_dir}")
        self.add_log(f"SFTP服务器: {host}:{port}")
        self.add_log(f"远程目录: {remote_dir}")
        
        # 获取排除的文件扩展名
        exclude_exts = [ext.strip() for ext in self.exclude_exts_edit.text().split(",") if ext.strip()]
        
        # 创建并启动同步线程
        self.plugin.sftp_thread = SFTPSyncThread(
            local_dir, host, port, username, password, remote_dir, sync_mode, private_key, exclude_exts
        )
        self.plugin.sftp_thread.progress_updated.connect(self.update_sftp_progress)
        self.plugin.sftp_thread.sync_complete.connect(self.on_sftp_sync_complete)
        self.plugin.sftp_thread.start()
        
        # 保存设置
        self.plugin.set_setting("sftp_local_dir", local_dir)
        self.plugin.set_setting("sftp_host", host)
        self.plugin.set_setting("sftp_port", port)
        self.plugin.set_setting("sftp_username", username)
        self.plugin.set_setting("sftp_password", password)
        self.plugin.set_setting("sftp_private_key", private_key)
        self.plugin.set_setting("sftp_remote_dir", remote_dir)
        self.plugin.set_setting("sync_mode", sync_mode)
        self.plugin.set_setting("last_sync_time", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def start_onedrive_sync(self):
        """开始OneDrive同步"""
        local_dir = self.onedrive_local_dir_edit.text().strip()
        client_id = self.onedrive_client_id_edit.text().strip()
        client_secret = self.onedrive_client_secret_edit.text()
        remote_folder = self.onedrive_folder_edit.text().strip()
        
        if not local_dir or not client_id or not client_secret:
            QMessageBox.warning(self, "同步错误", "请先设置本地目录、客户端ID和客户端密钥")
            return
        
        # 检查本地目录是否存在
        if not os.path.exists(local_dir):
            QMessageBox.warning(self, "同步错误", f"本地目录不存在: {local_dir}")
            return
        
        # 确定同步模式
        sync_mode = "bidirectional"
        if self.onedrive_upload_radio.isChecked():
            sync_mode = "upload"
        elif self.onedrive_download_radio.isChecked():
            sync_mode = "download"
        
        # 更新UI状态
        self.start_onedrive_sync_btn.setEnabled(False)
        self.stop_onedrive_sync_btn.setEnabled(True)
        
        # 添加日志
        self.add_log(f"开始OneDrive同步: {sync_mode} 模式")
        self.add_log(f"本地目录: {local_dir}")
        self.add_log(f"远程文件夹: {remote_folder or '根目录'}")
        
        # 获取排除的文件扩展名
        exclude_exts = [ext.strip() for ext in self.exclude_exts_edit.text().split(",") if ext.strip()]
        
        # 创建并启动同步线程
        self.plugin.onedrive_thread = OneDriveSyncThread(
            local_dir, remote_folder, client_id, client_secret, sync_mode, exclude_exts
        )
        self.plugin.onedrive_thread.progress_updated.connect(self.update_onedrive_progress)
        self.plugin.onedrive_thread.sync_complete.connect(self.on_onedrive_sync_complete)
        self.plugin.onedrive_thread.start()
        
        # 保存设置
        self.plugin.set_setting("onedrive_local_dir", local_dir)
        self.plugin.set_setting("onedrive_client_id", client_id)
        self.plugin.set_setting("onedrive_client_secret", client_secret)
        self.plugin.set_setting("onedrive_remote_folder", remote_folder)
        self.plugin.set_setting("sync_mode", sync_mode)
        self.plugin.set_setting("last_sync_time", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def start_gdrive_sync(self):
        """开始Google Drive同步"""
        local_dir = self.gdrive_local_dir_edit.text().strip()
        credentials_file = self.gdrive_credentials_edit.text().strip()
        remote_folder = self.gdrive_folder_edit.text().strip()
        
        if not local_dir or not credentials_file:
            QMessageBox.warning(self, "同步错误", "请先设置本地目录和凭据文件")
            return
        
        # 检查本地目录是否存在
        if not os.path.exists(local_dir):
            QMessageBox.warning(self, "同步错误", f"本地目录不存在: {local_dir}")
            return
        
        # 检查凭据文件是否存在
        if not os.path.exists(credentials_file):
            QMessageBox.warning(self, "同步错误", f"凭据文件不存在: {credentials_file}")
            return
        
        # 确定同步模式
        sync_mode = "bidirectional"
        if self.gdrive_upload_radio.isChecked():
            sync_mode = "upload"
        elif self.gdrive_download_radio.isChecked():
            sync_mode = "download"
        
        # 更新UI状态
        self.start_gdrive_sync_btn.setEnabled(False)
        self.stop_gdrive_sync_btn.setEnabled(True)
        
        # 添加日志
        self.add_log(f"开始Google Drive同步: {sync_mode} 模式")
        self.add_log(f"本地目录: {local_dir}")
        self.add_log(f"远程文件夹: {remote_folder or '根目录'}")
        
        # 获取排除的文件扩展名
        exclude_exts = [ext.strip() for ext in self.exclude_exts_edit.text().split(",") if ext.strip()]
        
        # 创建并启动同步线程
        self.plugin.gdrive_thread = GoogleDriveSyncThread(
            local_dir, remote_folder, credentials_file, sync_mode, exclude_exts
        )
        self.plugin.gdrive_thread.progress_updated.connect(self.update_gdrive_progress)
        self.plugin.gdrive_thread.sync_complete.connect(self.on_gdrive_sync_complete)
        self.plugin.gdrive_thread.start()
        
        # 保存设置
        self.plugin.set_setting("gdrive_local_dir", local_dir)
        self.plugin.set_setting("gdrive_credentials_file", credentials_file)
        self.plugin.set_setting("gdrive_remote_folder", remote_folder)
        self.plugin.set_setting("sync_mode", sync_mode)
        self.plugin.set_setting("last_sync_time", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    def start_smb_sync(self):
        """开始SMB网络共享同步"""
        local_dir = self.smb_local_dir_edit.text().strip()
        server = self.smb_server_edit.text().strip()
        share = self.smb_share_edit.text().strip()
        username = self.smb_username_edit.text().strip()
        password = self.smb_password_edit.text()
        remote_dir = self.smb_remote_dir_edit.text().strip()
        
        if not local_dir or not server or not share:
            QMessageBox.warning(self, "同步错误", "请先设置本地目录、服务器名称和共享名称")
            return
        
        # 检查本地目录是否存在
        if not os.path.exists(local_dir):
            QMessageBox.warning(self, "同步错误", f"本地目录不存在: {local_dir}")
            return
        
        # 确定同步模式
        sync_mode = "bidirectional"
        if self.smb_upload_radio.isChecked():
            sync_mode = "upload"
        elif self.smb_download_radio.isChecked():
            sync_mode = "download"
        
        # 更新UI状态
        self.start_smb_sync_btn.setEnabled(False)
        self.stop_smb_sync_btn.setEnabled(True)
        
        # 添加日志
        self.add_log(f"开始SMB网络共享同步: {sync_mode} 模式")
        self.add_log(f"本地目录: {local_dir}")
        self.add_log(f"SMB服务器: {server}")
        self.add_log(f"共享名称: {share}")
        self.add_log(f"远程目录: {remote_dir or '根目录'}")
        
        # 获取排除的文件扩展名
        exclude_exts = [ext.strip() for ext in self.exclude_exts_edit.text().split(",") if ext.strip()]
        
        # 创建并启动同步线程
        self.plugin.smb_thread = SMBSyncThread(
            local_dir, server, share, username, password, remote_dir, sync_mode, exclude_exts
        )
        self.plugin.smb_thread.progress_updated.connect(self.update_smb_progress)
        self.plugin.smb_thread.sync_complete.connect(self.on_smb_sync_complete)
        self.plugin.smb_thread.start()
        
        # 保存设置
        self.plugin.set_setting("smb_local_dir", local_dir)
        self.plugin.set_setting("smb_server", server)
        self.plugin.set_setting("smb_share", share)
        self.plugin.set_setting("smb_username", username)
        self.plugin.set_setting("smb_password", password)
        self.plugin.set_setting("smb_remote_dir", remote_dir)
        self.plugin.set_setting("sync_mode", sync_mode)
        self.plugin.set_setting("last_sync_time", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

    # 停止同步函数
    def stop_sftp_sync(self):
        """停止SFTP同步"""
        if self.plugin.sftp_thread and self.plugin.sftp_thread.isRunning():
            self.plugin.sftp_thread.stop()
            self.add_log("正在停止SFTP同步...")
            self.sftp_status_label.setText("正在停止...")
            self.sftp_status_label.setStyleSheet("color: #FFA000; font-weight: bold;")

    def stop_onedrive_sync(self):
        """停止OneDrive同步"""
        if self.plugin.onedrive_thread and self.plugin.onedrive_thread.isRunning():
            self.plugin.onedrive_thread.stop()
            self.add_log("正在停止OneDrive同步...")
            self.onedrive_status_label.setText("正在停止...")
            self.onedrive_status_label.setStyleSheet("color: #FFA000; font-weight: bold;")

    def stop_gdrive_sync(self):
        """停止Google Drive同步"""
        if self.plugin.gdrive_thread and self.plugin.gdrive_thread.isRunning():
            self.plugin.gdrive_thread.stop()
            self.add_log("正在停止Google Drive同步...")
            self.gdrive_status_label.setText("正在停止...")
            self.gdrive_status_label.setStyleSheet("color: #FFA000; font-weight: bold;")

    def stop_smb_sync(self):
        """停止SMB网络共享同步"""
        if self.plugin.smb_thread and self.plugin.smb_thread.isRunning():
            self.plugin.smb_thread.stop()
            self.add_log("正在停止SMB网络共享同步...")
            self.smb_status_label.setText("正在停止...")
            self.smb_status_label.setStyleSheet("color: #FFA000; font-weight: bold;")

    # 更新进度函数
    def update_sftp_progress(self, value, message):
        """更新SFTP同步进度"""
        self.sftp_progress_bar.setValue(value)
        self.sftp_status_label.setText(message)
        if value == 100:
            self.sftp_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        elif value == 0 and "错误" in message:
            self.sftp_status_label.setStyleSheet("color: #F44336; font-weight: bold;")
        else:
            self.sftp_status_label.setStyleSheet("color: #2196F3; font-weight: bold;")
        
        # 添加日志
        if value == 0 or value == 100 or "错误" in message:
            self.add_log(f"SFTP同步: {message}")

    def update_onedrive_progress(self, value, message):
        """更新OneDrive同步进度"""
        self.onedrive_progress_bar.setValue(value)
        self.onedrive_status_label.setText(message)
        if value == 100:
            self.onedrive_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        elif value == 0 and "错误" in message:
            self.onedrive_status_label.setStyleSheet("color: #F44336; font-weight: bold;")
        else:
            self.onedrive_status_label.setStyleSheet("color: #2196F3; font-weight: bold;")
        
        # 添加日志
        if value == 0 or value == 100 or "错误" in message:
            self.add_log(f"OneDrive同步: {message}")

    def update_gdrive_progress(self, value, message):
        """更新Google Drive同步进度"""
        self.gdrive_progress_bar.setValue(value)
        self.gdrive_status_label.setText(message)
        if value == 100:
            self.gdrive_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        elif value == 0 and "错误" in message:
            self.gdrive_status_label.setStyleSheet("color: #F44336; font-weight: bold;")
        else:
            self.gdrive_status_label.setStyleSheet("color: #2196F3; font-weight: bold;")
        
        # 添加日志
        if value == 0 or value == 100 or "错误" in message:
            self.add_log(f"Google Drive同步: {message}")

    def update_smb_progress(self, value, message):
        """更新SMB网络共享同步进度"""
        self.smb_progress_bar.setValue(value)
        self.smb_status_label.setText(message)
        if value == 100:
            self.smb_status_label.setStyleSheet("color: #4CAF50; font-weight: bold;")
        elif value == 0 and "错误" in message:
            self.smb_status_label.setStyleSheet("color: #F44336; font-weight: bold;")
        else:
            self.smb_status_label.setStyleSheet("color: #2196F3; font-weight: bold;")
        
        # 添加日志
        if value == 0 or value == 100 or "错误" in message:
            self.add_log(f"SMB网络共享同步: {message}")

    # 同步完成处理函数
    def on_sftp_sync_complete(self, success, message, synced_files, synced_size):
        """SFTP同步完成处理"""
        # 更新UI状态
        self.start_sftp_sync_btn.setEnabled(True)
        self.stop_sftp_sync_btn.setEnabled(False)
        
        # 从线程对象获取验证后的总大小
        verified_size = self.plugin.sftp_thread.verified_total_size
        
        print(f"处理SFTP同步完成，成功: {success}, 同步文件数: {synced_files}")
        print(f"信号传递的大小: {synced_size} 字节")
        print(f"线程对象中的验证大小: {verified_size} 字节")
        
        if success:
            # 更新同步统计信息到插件设置，使用verified_size而不是synced_size
            self.plugin.settings["synced_files_count"] = synced_files
            self.plugin.settings["synced_total_size"] = verified_size
            
            print(f"更新插件设置，同步文件数: {synced_files}, 同步大小: {verified_size} 字节")
            
            # 立即保存设置到文件
            self.plugin.save_settings()
            
            # 使用verified_size参数
            size_str = self._format_size(verified_size)
            
            # 更新UI标签
            self.total_files_label.setText(str(synced_files))
            self.total_size_label.setText(size_str)
            
            print(f"格式化后的大小: {size_str}")
            
            self.add_log(f"SFTP同步成功: {message}, 已同步 {synced_files} 个文件, 总大小 {size_str}")
            QMessageBox.information(self, "同步完成", f"{message}\n已同步 {synced_files} 个文件, 总大小 {size_str}")
        else:
            self.add_log(f"SFTP同步失败: {message}")
            QMessageBox.warning(self, "同步失败", message)

    def on_onedrive_sync_complete(self, success, message, synced_files, synced_size):
        """OneDrive同步完成处理"""
        # 更新UI状态
        self.start_onedrive_sync_btn.setEnabled(True)
        self.stop_onedrive_sync_btn.setEnabled(False)
        
        # 从线程对象获取验证后的总大小
        verified_size = self.plugin.onedrive_thread.verified_total_size
        
        if success:
            # 更新同步统计信息到插件设置
            self.plugin.settings["synced_files_count"] = synced_files
            self.plugin.settings["synced_total_size"] = verified_size
            
            # 立即保存设置到文件
            self.plugin.save_settings()
            
            # 使用verified_size参数
            size_str = self._format_size(verified_size)
            
            # 更新UI标签
            self.total_files_label.setText(str(synced_files))
            self.total_size_label.setText(size_str)
            
            self.add_log(f"OneDrive同步成功: {message}, 已同步 {synced_files} 个文件, 总大小 {size_str}")
            QMessageBox.information(self, "同步完成", f"{message}\n已同步 {synced_files} 个文件, 总大小 {size_str}")
        else:
            self.add_log(f"OneDrive同步失败: {message}")
            QMessageBox.warning(self, "同步失败", message)

    def on_gdrive_sync_complete(self, success, message, synced_files, synced_size):
        """Google Drive同步完成处理"""
        # 更新UI状态
        self.start_gdrive_sync_btn.setEnabled(True)
        self.stop_gdrive_sync_btn.setEnabled(False)
        
        # 从线程对象获取验证后的总大小
        verified_size = self.plugin.gdrive_thread.verified_total_size
        
        if success:
            # 更新同步统计信息到插件设置
            self.plugin.settings["synced_files_count"] = synced_files
            self.plugin.settings["synced_total_size"] = verified_size
            
            # 立即保存设置到文件
            self.plugin.save_settings()
            
            # 使用verified_size参数
            size_str = self._format_size(verified_size)
            
            # 更新UI标签
            self.total_files_label.setText(str(synced_files))
            self.total_size_label.setText(size_str)
            
            self.add_log(f"Google Drive同步成功: {message}, 已同步 {synced_files} 个文件, 总大小 {size_str}")
            QMessageBox.information(self, "同步完成", f"{message}\n已同步 {synced_files} 个文件, 总大小 {size_str}")
        else:
            self.add_log(f"Google Drive同步失败: {message}")
            QMessageBox.warning(self, "同步失败", message)

    def on_smb_sync_complete(self, success, message, synced_files, synced_size):
        """SMB网络共享同步完成处理"""
        # 更新UI状态
        self.start_smb_sync_btn.setEnabled(True)
        self.stop_smb_sync_btn.setEnabled(False)
        
        # 从线程对象获取验证后的总大小
        verified_size = self.plugin.smb_thread.verified_total_size
        
        if success:
            # 更新同步统计信息到插件设置
            self.plugin.settings["synced_files_count"] = synced_files
            self.plugin.settings["synced_total_size"] = verified_size
            
            # 立即保存设置到文件
            self.plugin.save_settings()
            
            # 使用verified_size参数
            size_str = self._format_size(verified_size)
            
            # 更新UI标签
            self.total_files_label.setText(str(synced_files))
            self.total_size_label.setText(size_str)
            
            self.add_log(f"SMB网络共享同步成功: {message}, 已同步 {synced_files} 个文件, 总大小 {size_str}")
            QMessageBox.information(self, "同步完成", f"{message}\n已同步 {synced_files} 个文件, 总大小 {size_str}")
        else:
            self.add_log(f"SMB网络共享同步失败: {message}")
            QMessageBox.warning(self, "同步失败", message)
        # 
    def setup_settings_tab(self, tab):
        """设置同步设置标签页"""
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 自动同步设置
        auto_sync_group = QGroupBox("自动同步设置")
        auto_sync_layout = QVBoxLayout()
        auto_sync_layout.setContentsMargins(15, 15, 15, 15)
        auto_sync_layout.setSpacing(15)
        
        self.auto_sync_check = QCheckBox("启用自动同步")
        self.auto_sync_check.setStyleSheet("font-weight: bold; color: #333333;")
        
        interval_layout = QHBoxLayout()
        interval_layout.addWidget(QLabel("同步间隔:"))
        
        self.sync_interval_spin = QSpinBox()
        self.sync_interval_spin.setMinimum(5)
        self.sync_interval_spin.setMaximum(1440)
        self.sync_interval_spin.setValue(60)
        self.sync_interval_spin.setSuffix(" 分钟")
        self.sync_interval_spin.setFixedWidth(100)
        
        interval_layout.addWidget(self.sync_interval_spin)
        interval_layout.addStretch()
        
        auto_sync_layout.addWidget(self.auto_sync_check)
        auto_sync_layout.addLayout(interval_layout)
        
        auto_sync_group.setLayout(auto_sync_layout)
        
        # 排除文件设置
        exclude_group = QGroupBox("排除文件设置")
        exclude_layout = QVBoxLayout()
        exclude_layout.setContentsMargins(15, 15, 15, 15)
        exclude_layout.setSpacing(10)
        
        exclude_label = QLabel("排除的文件扩展名:")
        
        self.exclude_exts_edit = QLineEdit()
        self.exclude_exts_edit.setPlaceholderText("输入要排除的文件扩展名，用逗号分隔（例如: .tmp,.part,.downloading）")
        
        exclude_layout.addWidget(exclude_label)
        exclude_layout.addWidget(self.exclude_exts_edit)
        
        exclude_group.setLayout(exclude_layout)
        
        # 保存设置按钮
        save_btn_layout = QHBoxLayout()
        
        save_settings_btn = QPushButton("保存设置")
        save_settings_btn.setStyleSheet("""
            QPushButton {
                background-color: #2196F3;
                min-width: 120px;
            }
            QPushButton:hover {
                background-color: #1E88E5;
            }
            QPushButton:pressed {
                background-color: #1976D2;
            }
        """)
        save_settings_btn.setIcon(QIcon.fromTheme("document-save"))
        save_settings_btn.clicked.connect(self.save_settings)
        
        save_btn_layout.addStretch()
        save_btn_layout.addWidget(save_settings_btn)
        
        # 添加到布局
        layout.addWidget(auto_sync_group)
        layout.addWidget(exclude_group)
        layout.addLayout(save_btn_layout)
        layout.addStretch()
        
        tab.setLayout(layout)
    
    def setup_status_tab(self, tab):
        """设置同步状态标签页"""
        layout = QVBoxLayout()
        layout.setSpacing(10)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # 同步状态信息
        status_group = QGroupBox("同步状态信息")
        status_layout = QFormLayout()
        status_layout.setContentsMargins(15, 15, 15, 15)
        status_layout.setSpacing(15)
        status_layout.setFieldGrowthPolicy(QFormLayout.ExpandingFieldsGrow)
        
        self.last_sync_time_label = QLabel("从未同步")
        self.last_sync_time_label.setStyleSheet("font-weight: bold;")
        
        self.sync_mode_label = QLabel("未设置")
        self.sync_mode_label.setStyleSheet("font-weight: bold;")
        
        self.total_files_label = QLabel("0")
        self.total_files_label.setStyleSheet("font-weight: bold;")
        
        self.total_size_label = QLabel("0 MB")
        self.total_size_label.setStyleSheet("font-weight: bold;")
        
        status_layout.addRow(QLabel("上次同步时间:"), self.last_sync_time_label)
        status_layout.addRow(QLabel("当前同步模式:"), self.sync_mode_label)
        status_layout.addRow(QLabel("已同步文件数:"), self.total_files_label)
        status_layout.addRow(QLabel("已同步总大小:"), self.total_size_label)
        
        status_group.setLayout(status_layout)
        
        # 同步日志
        log_group = QGroupBox("同步日志")
        log_layout = QVBoxLayout()
        log_layout.setContentsMargins(15, 15, 15, 15)
        log_layout.setSpacing(10)
        
        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setStyleSheet("""
            QTextEdit {
                background-color: #f9f9f9;
                font-family: Consolas, "Courier New", monospace;
                font-size: 12px;
            }
        """)
        
        clear_log_btn = QPushButton("清空日志")
        clear_log_btn.setStyleSheet("""
            QPushButton {
                background-color: #607d8b;
                max-width: 120px;
            }
            QPushButton:hover {
                background-color: #455a64;
            }
            QPushButton:pressed {
                background-color: #37474f;
            }
        """)
        clear_log_btn.clicked.connect(self.clear_log)
        
        log_layout.addWidget(self.log_text)
        log_layout.addWidget(clear_log_btn, 0, Qt.AlignRight)
        
        log_group.setLayout(log_layout)
        
        # 添加到布局
        layout.addWidget(status_group)
        layout.addWidget(log_group)
        
        tab.setLayout(layout)
        
        # 加载同步状态
        self.update_sync_status()
    
    def add_log(self, message):
        """添加日志消息"""
        # 尝试解码URL编码的文件名
        try:
            # 检查消息中是否包含编码的URL
            if "%e" in message or "%E" in message:
                # 分割消息以找到文件路径部分
                parts = message.split(": ", 1)
                if len(parts) == 2:
                    prefix = parts[0] + ": "
                    encoded_path = parts[1]
                    
                    # 解码URL编码的路径
                    try:
                        decoded_path = unquote(encoded_path)
                        # 更新消息为解码后的内容
                        message = prefix + decoded_path
                    except:
                        # 如果解码失败，保持原样
                        pass
        except:
            # 如果处理过程中出现任何错误，保持原始消息
            pass
        
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        log_message = f"[{timestamp}] {message}"
        
        # 根据消息类型设置不同颜色
        if "成功" in message or "完成" in message:
            log_message = f'<span style="color: #4CAF50;">{log_message}</span>'
        elif "失败" in message or "错误" in message:
            log_message = f'<span style="color: #f44336;">{log_message}</span>'
        elif "警告" in message:
            log_message = f'<span style="color: #FF9800;">{log_message}</span>'
        elif "开始" in message:
            log_message = f'<span style="color: #2196F3;">{log_message}</span>'
            
        self.log_text.append(log_message)
        
        # 滚动到底部
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())
    
    def browse_local_dir(self):
        """浏览本地媒体库目录"""
        directory = QFileDialog.getExistingDirectory(self, "选择本地媒体库目录")
        if directory:
            self.local_dir_edit.setText(directory)
    
    def browse_external_dir(self):
        """浏览外部媒体库目录"""
        directory = QFileDialog.getExistingDirectory(self, "选择外部媒体库目录")
        if directory:
            self.external_dir_edit.setText(directory)
    
    def browse_webdav_local_dir(self):
        """浏览WebDAV本地媒体库目录"""
        directory = QFileDialog.getExistingDirectory(self, "选择本地媒体库目录")
        if directory:
            self.webdav_local_dir_edit.setText(directory)
    
    def update_webdav_protocol(self):
        """更新WebDAV协议标签"""
        if self.http_radio.isChecked():
            self.webdav_protocol_label.setText("http://")
            self.webdav_protocol_label.setStyleSheet("font-weight: bold; color: #FF9800;")
            # 在HTTP模式下显示端口输入框
            self.port_label.setVisible(True)
            self.port_edit.setVisible(True)
        else:
            self.webdav_protocol_label.setText("https://")
            self.webdav_protocol_label.setStyleSheet("font-weight: bold; color: #4CAF50;")
            # 在HTTPS模式下隐藏端口输入框
            self.port_label.setVisible(False)
            self.port_edit.setVisible(False)
    
    def test_webdav_connection(self):
        """测试WebDAV连接"""
        # 获取完整URL（包括协议和端口）
        protocol = "http://" if self.http_radio.isChecked() else "https://"
        server_url = self.webdav_url_edit.text().strip()
        
        # 在HTTP模式下添加端口号
        if self.http_radio.isChecked() and self.port_edit.value() != 80:
            # 检查URL中是否已包含端口号
            if ":" not in server_url.split("/")[0]:
                server_parts = server_url.split("/", 1)
                if len(server_parts) > 1:
                    server_url = f"{server_parts[0]}:{self.port_edit.value()}/{server_parts[1]}"
                else:
                    server_url = f"{server_parts[0]}:{self.port_edit.value()}"
        
        url = protocol + server_url
        
        username = self.webdav_username_edit.text().strip()
        password = self.webdav_password_edit.text()
        
        if not server_url:
            QMessageBox.warning(self, "连接错误", "请输入WebDAV服务器地址")
            return
        
        self.test_webdav_btn.setEnabled(False)
        self.test_webdav_btn.setText("正在测试...")
        
        # 创建测试线程
        class TestWebDAVThread(QThread):
            test_result = pyqtSignal(bool, str)
            
            def __init__(self, url, username, password):
                super().__init__()
                self.url = url
                self.username = username
                self.password = password
            
            def run(self):
                try:
                    auth = None
                    if self.username and self.password:
                        auth = (self.username, self.password)
                    
                    response = requests.request("PROPFIND", self.url, auth=auth, headers={"Depth": "0"}, timeout=10)
                    
                    if response.status_code in [200, 207]:
                        self.test_result.emit(True, f"连接成功（状态码: {response.status_code}）")
                    else:
                        self.test_result.emit(False, f"连接失败（状态码: {response.status_code}）")
                except Exception as e:
                    self.test_result.emit(False, f"连接错误: {str(e)}")
        
        # 创建并启动测试线程
        self.test_thread = TestWebDAVThread(url, username, password)
        self.test_thread.test_result.connect(self.on_webdav_test_result)
        self.test_thread.finished.connect(lambda: self.test_webdav_btn.setEnabled(True))
        self.test_thread.start()
    
    def start_webdav_sync(self):
        """开始WebDAV同步"""
        local_dir = self.webdav_local_dir_edit.text().strip()
        
        # 获取完整URL（包括协议和端口）
        protocol = "http://" if self.http_radio.isChecked() else "https://"
        server_url = self.webdav_url_edit.text().strip()
        
        # 在HTTP模式下添加端口号
        if self.http_radio.isChecked() and self.port_edit.value() != 80:
            # 检查URL中是否已包含端口号
            if ":" not in server_url.split("/")[0]:
                server_parts = server_url.split("/", 1)
                if len(server_parts) > 1:
                    server_url = f"{server_parts[0]}:{self.port_edit.value()}/{server_parts[1]}"
                else:
                    server_url = f"{server_parts[0]}:{self.port_edit.value()}"
        
        webdav_url = protocol + server_url
        
        username = self.webdav_username_edit.text().strip()
        password = self.webdav_password_edit.text()
        
        if not local_dir or not server_url:
            QMessageBox.warning(self, "同步错误", "请先设置本地目录和WebDAV服务器地址")
            return
        
        # 检查本地目录是否存在
        if not os.path.exists(local_dir):
            QMessageBox.warning(self, "同步错误", f"本地目录不存在: {local_dir}")
            return
        
        # 确定同步模式
        sync_mode = "bidirectional"
        if self.webdav_upload_radio.isChecked():
            sync_mode = "upload"
        elif self.webdav_download_radio.isChecked():
            sync_mode = "download"
        
        # 更新UI状态
        self.start_webdav_sync_btn.setEnabled(False)
        self.stop_webdav_sync_btn.setEnabled(True)
        
        # 添加日志
        self.add_log(f"开始WebDAV同步: {sync_mode} 模式")
        self.add_log(f"本地目录: {local_dir}")
        self.add_log(f"WebDAV URL: {webdav_url}")
        
        # 获取排除的文件扩展名
        exclude_exts = [ext.strip() for ext in self.exclude_exts_edit.text().split(",") if ext.strip()]
        
        # 创建并启动WebDAV同步线程
        self.plugin.webdav_thread = WebDAVSyncThread(local_dir, webdav_url, username, password, sync_mode, exclude_exts)
        self.plugin.webdav_thread.progress_updated.connect(self.update_webdav_progress)
        self.plugin.webdav_thread.sync_complete.connect(self.on_webdav_sync_complete)
        self.plugin.webdav_thread.start()
        
        # 保存设置
        self.plugin.set_setting("local_media_dir", local_dir)
        self.plugin.set_setting("webdav_url", webdav_url)
        self.plugin.set_setting("webdav_username", username)
        self.plugin.set_setting("webdav_password", password)
        self.plugin.set_setting("sync_mode", sync_mode)
        self.plugin.set_setting("last_sync_time", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        
        # 保存协议选择和端口设置
        self.plugin.set_setting("webdav_use_https", self.https_radio.isChecked())
        self.plugin.set_setting("webdav_http_port", self.port_edit.value())
    
    def stop_local_sync(self):
        """停止本地同步"""
        if self.plugin.sync_thread and self.plugin.sync_thread.isRunning():
            self.plugin.sync_thread.stop()
            self.add_log("正在停止本地同步...")
            self.local_status_label.setText("正在停止...")
    
    def stop_webdav_sync(self):
        """停止WebDAV同步"""
        if self.plugin.webdav_thread and self.plugin.webdav_thread.isRunning():
            self.plugin.webdav_thread.stop()
            self.add_log("正在停止WebDAV同步...")
            self.webdav_status_label.setText("正在停止...")
    
    def update_local_progress(self, value, message):
        """更新本地同步进度"""
        self.local_progress_bar.setValue(value)
        self.local_status_label.setText(message)
        self.add_log(message)
    
    def update_webdav_progress(self, value, message):
        """更新WebDAV同步进度"""
        self.webdav_progress_bar.setValue(value)
        
        # 尝试解码URL编码的文件名
        try:
            # 检查消息中是否包含编码的URL
            if "%e" in message or "%E" in message:
                # 分割消息以找到文件路径部分
                parts = message.split(": ", 1)
                if len(parts) == 2:
                    prefix = parts[0] + ": "
                    encoded_path = parts[1]
                    
                    # 解码URL编码的路径
                    try:
                        decoded_path = unquote(encoded_path)
                        # 更新消息为解码后的内容
                        message = prefix + decoded_path
                    except:
                        # 如果解码失败，保持原样
                        pass
        except:
            # 如果处理过程中出现任何错误，保持原始消息
            pass
        
        self.webdav_status_label.setText(message)
        self.add_log(message)
    
    def on_local_sync_complete(self, success, message, synced_files, synced_size):
        """本地同步完成处理"""
        # 更新UI状态
        self.start_local_sync_btn.setEnabled(True)
        self.stop_local_sync_btn.setEnabled(False)
        
        # 从线程对象获取验证后的总大小
        verified_size = self.plugin.sync_thread.verified_total_size
        
        print(f"处理本地同步完成，成功: {success}, 同步文件数: {synced_files}")
        print(f"信号传递的大小: {synced_size} 字节")
        print(f"线程对象中的验证大小: {verified_size} 字节")
        
        if success:
            # 更新同步统计信息到插件设置，使用verified_size而不是synced_size
            self.plugin.settings["synced_files_count"] = synced_files
            self.plugin.settings["synced_total_size"] = verified_size
            
            print(f"更新插件设置，同步文件数: {synced_files}, 同步大小: {verified_size} 字节")
            
            # 立即保存设置到文件
            self.plugin.save_settings()
            
            # 使用verified_size参数
            size_str = self._format_size(verified_size)
            
            # 更新UI标签
            self.total_files_label.setText(str(synced_files))
            self.total_size_label.setText(size_str)
            
            print(f"格式化后的大小: {size_str}")
            
            self.add_log(f"本地同步成功: {message}, 已同步 {synced_files} 个文件, 总大小 {size_str}")
            QMessageBox.information(self, "同步完成", f"{message}\n已同步 {synced_files} 个文件, 总大小 {size_str}")
        else:
            self.add_log(f"本地同步失败: {message}")
            QMessageBox.warning(self, "同步失败", message)
    
    def on_webdav_sync_complete(self, success, message, synced_files, synced_size):
        """WebDAV同步完成处理"""
        # 更新UI状态
        self.start_webdav_sync_btn.setEnabled(True)
        self.stop_webdav_sync_btn.setEnabled(False)
        
        # 从线程对象获取验证后的总大小
        verified_size = self.plugin.webdav_thread.verified_total_size
        
        print(f"处理WebDAV同步完成，成功: {success}, 同步文件数: {synced_files}")
        print(f"信号传递的大小: {synced_size} 字节")
        print(f"线程对象中的验证大小: {verified_size} 字节")
        
        if success:
            # 更新同步统计信息到插件设置，使用verified_size而不是synced_size
            self.plugin.settings["synced_files_count"] = synced_files
            self.plugin.settings["synced_total_size"] = verified_size
            
            print(f"更新插件设置，同步文件数: {synced_files}, 同步大小: {verified_size} 字节")
            
            # 立即保存设置到文件
            self.plugin.save_settings()
            
            # 使用verified_size参数
            size_str = self._format_size(verified_size)
            
            # 更新UI标签
            self.total_files_label.setText(str(synced_files))
            self.total_size_label.setText(size_str)
            
            print(f"格式化后的大小: {size_str}")
            
            self.add_log(f"WebDAV同步成功: {message}, 已同步 {synced_files} 个文件, 总大小 {size_str}")
            QMessageBox.information(self, "同步完成", f"{message}\n已同步 {synced_files} 个文件, 总大小 {size_str}")
        else:
            self.add_log(f"WebDAV同步失败: {message}")
            QMessageBox.warning(self, "同步失败", message)
    
    def save_settings(self):
        """保存UI设置"""
        # 本地同步设置
        self.plugin.set_setting("local_media_dir", self.local_dir_edit.text().strip())
        self.plugin.set_setting("external_media_dir", self.external_dir_edit.text().strip())
        
        # WebDAV同步设置
        self.plugin.set_setting("webdav_local_dir", self.webdav_local_dir_edit.text().strip())
        
        # 构建完整的WebDAV URL
        webdav_url = self.webdav_url_edit.text().strip()  # 修复这一行，使用正确的变量名
        if webdav_url:
            protocol = "https://" if self.https_radio.isChecked() else "http://"
            webdav_url = f"{protocol}{webdav_url}"
            self.plugin.set_setting("webdav_url", webdav_url)
        
        self.plugin.set_setting("webdav_username", self.webdav_username_edit.text().strip())
        self.plugin.set_setting("webdav_password", self.webdav_password_edit.text())
        
        
        # SFTP同步设置
        self.plugin.set_setting("sftp_local_dir", self.sftp_local_dir_edit.text().strip())
        self.plugin.set_setting("sftp_host", self.sftp_host_edit.text().strip())
        self.plugin.set_setting("sftp_port", self.sftp_port_edit.value())
        self.plugin.set_setting("sftp_username", self.sftp_username_edit.text().strip())
        self.plugin.set_setting("sftp_password", self.sftp_password_edit.text())
        self.plugin.set_setting("sftp_private_key", self.sftp_key_edit.text().strip())
        self.plugin.set_setting("sftp_remote_dir", self.sftp_remote_dir_edit.text().strip())
        
        # OneDrive同步设置
        self.plugin.set_setting("onedrive_local_dir", self.onedrive_local_dir_edit.text().strip())
        self.plugin.set_setting("onedrive_client_id", self.onedrive_client_id_edit.text().strip())
        self.plugin.set_setting("onedrive_client_secret", self.onedrive_client_secret_edit.text())
        self.plugin.set_setting("onedrive_remote_folder", self.onedrive_folder_edit.text().strip())
        
        # Google Drive同步设置
        self.plugin.set_setting("gdrive_local_dir", self.gdrive_local_dir_edit.text().strip())
        self.plugin.set_setting("gdrive_credentials_file", self.gdrive_credentials_edit.text().strip())
        self.plugin.set_setting("gdrive_remote_folder", self.gdrive_folder_edit.text().strip())
        
        # SMB网络共享同步设置
        self.plugin.set_setting("smb_local_dir", self.smb_local_dir_edit.text().strip())
        self.plugin.set_setting("smb_server", self.smb_server_edit.text().strip())
        self.plugin.set_setting("smb_share", self.smb_share_edit.text().strip())
        self.plugin.set_setting("smb_username", self.smb_username_edit.text().strip())
        self.plugin.set_setting("smb_password", self.smb_password_edit.text())
        self.plugin.set_setting("smb_remote_dir", self.smb_remote_dir_edit.text().strip())
        
        # 同步模式设置
        if self.upload_radio.isChecked():
            self.plugin.set_setting("sync_mode", "upload")
        elif self.download_radio.isChecked():
            self.plugin.set_setting("sync_mode", "download")
        else:
            self.plugin.set_setting("sync_mode", "bidirectional")
        
        # 自动同步设置
        self.plugin.set_setting("auto_sync", self.auto_sync_check.isChecked())
        self.plugin.set_setting("auto_sync_interval", self.sync_interval_spin.value())
        
        # 排除的文件扩展名
        exclude_exts = [ext.strip() for ext in self.exclude_exts_edit.text().split(",") if ext.strip()]
        self.plugin.set_setting("exclude_extensions", exclude_exts)
        
        # 保存设置到文件
        self.plugin.save_settings()
    
    def load_settings(self):
        """加载设置到UI"""
        # 本地同步设置
        self.local_dir_edit.setText(self.plugin.get_setting("local_media_dir", ""))
        self.external_dir_edit.setText(self.plugin.get_setting("external_media_dir", ""))
        
        # WebDAV同步设置
        self.webdav_local_dir_edit.setText(self.plugin.get_setting("webdav_local_dir", ""))
        
        webdav_url = self.plugin.get_setting("webdav_url", "")
        if webdav_url:
            if webdav_url.startswith("https://"):
                self.https_radio.setChecked(True)
                webdav_url = webdav_url[8:]
            elif webdav_url.startswith("http://"):
                self.http_radio.setChecked(True)
                webdav_url = webdav_url[7:]
            # 修复这一行，使用正确的变量名
            self.webdav_url_edit.setText(webdav_url)
        
        self.webdav_username_edit.setText(self.plugin.get_setting("webdav_username", ""))
        self.webdav_password_edit.setText(self.plugin.get_setting("webdav_password", ""))
        
        # SFTP同步设置
        self.sftp_local_dir_edit.setText(self.plugin.get_setting("sftp_local_dir", ""))
        self.sftp_host_edit.setText(self.plugin.get_setting("sftp_host", ""))
        self.sftp_port_edit.setValue(self.plugin.get_setting("sftp_port", 22))
        self.sftp_username_edit.setText(self.plugin.get_setting("sftp_username", ""))
        self.sftp_password_edit.setText(self.plugin.get_setting("sftp_password", ""))
        self.sftp_key_edit.setText(self.plugin.get_setting("sftp_private_key", ""))
        self.sftp_remote_dir_edit.setText(self.plugin.get_setting("sftp_remote_dir", ""))
        
        # OneDrive同步设置
        self.onedrive_local_dir_edit.setText(self.plugin.get_setting("onedrive_local_dir", ""))
        self.onedrive_client_id_edit.setText(self.plugin.get_setting("onedrive_client_id", ""))
        self.onedrive_client_secret_edit.setText(self.plugin.get_setting("onedrive_client_secret", ""))
        self.onedrive_folder_edit.setText(self.plugin.get_setting("onedrive_remote_folder", ""))
        
        # Google Drive同步设置
        self.gdrive_local_dir_edit.setText(self.plugin.get_setting("gdrive_local_dir", ""))
        self.gdrive_credentials_edit.setText(self.plugin.get_setting("gdrive_credentials_file", ""))
        self.gdrive_folder_edit.setText(self.plugin.get_setting("gdrive_remote_folder", ""))
        
        # SMB网络共享同步设置
        self.smb_local_dir_edit.setText(self.plugin.get_setting("smb_local_dir", ""))
        self.smb_server_edit.setText(self.plugin.get_setting("smb_server", ""))
        self.smb_share_edit.setText(self.plugin.get_setting("smb_share", ""))
        self.smb_username_edit.setText(self.plugin.get_setting("smb_username", ""))
        self.smb_password_edit.setText(self.plugin.get_setting("smb_password", ""))
        self.smb_remote_dir_edit.setText(self.plugin.get_setting("smb_remote_dir", ""))
        
        # 同步模式设置
        sync_mode = self.plugin.get_setting("sync_mode", "bidirectional")
        if sync_mode == "upload":
            self.upload_radio.setChecked(True)
            self.webdav_upload_radio.setChecked(True)
            self.sftp_upload_radio.setChecked(True)
            self.onedrive_upload_radio.setChecked(True)
            self.gdrive_upload_radio.setChecked(True)
            self.smb_upload_radio.setChecked(True)
        elif sync_mode == "download":
            self.download_radio.setChecked(True)
            self.webdav_download_radio.setChecked(True)
            self.sftp_download_radio.setChecked(True)
            self.onedrive_download_radio.setChecked(True)
            self.gdrive_download_radio.setChecked(True)
            self.smb_download_radio.setChecked(True)
        else:  # bidirectional
            self.bidirectional_radio.setChecked(True)
            self.webdav_bidirectional_radio.setChecked(True)
            self.sftp_bidirectional_radio.setChecked(True)
            self.onedrive_bidirectional_radio.setChecked(True)
            self.gdrive_bidirectional_radio.setChecked(True)
            self.smb_bidirectional_radio.setChecked(True)
        
        # 自动同步设置
        self.auto_sync_check.setChecked(self.plugin.get_setting("auto_sync", False))
        self.sync_interval_spin.setValue(self.plugin.get_setting("auto_sync_interval", 60))
        
        # 排除的文件扩展名
        exclude_exts = self.plugin.get_setting("exclude_extensions", [".tmp", ".part", ".downloading"])
        if isinstance(exclude_exts, list):
            self.exclude_exts_edit.setText(", ".join(exclude_exts))
        else:
            self.exclude_exts_edit.setText(".tmp, .part, .downloading")
    
    def update_sync_status(self):
        """更新同步状态信息"""
        # 确保从文件重新加载设置，获取最新数据
        self.plugin.load_settings()
        
        # 更新上次同步时间
        last_sync_time = self.plugin.get_setting("last_sync_time", "")
        if last_sync_time:
            self.last_sync_time_label.setText(last_sync_time)
        else:
            self.last_sync_time_label.setText("从未同步")
        
        # 更新同步模式
        sync_mode = self.plugin.get_setting("sync_mode", "bidirectional")
        if sync_mode == "upload":
            self.sync_mode_label.setText("上传（本地→外部/WebDAV）")
        elif sync_mode == "download":
            self.sync_mode_label.setText("下载（外部/WebDAV→本地）")
        else:
            self.sync_mode_label.setText("双向同步（保留最新版本）")
        
        # 更新文件统计信息 - 直接从插件设置中读取
        synced_files = self.plugin.settings.get("synced_files_count", 0)
        synced_size = self.plugin.settings.get("synced_total_size", 0)
        
        print(f"更新同步状态UI，文件数: {synced_files}, 大小: {synced_size} 字节")
        
        self.total_files_label.setText(str(synced_files))
        size_str = self._format_size(synced_size)
        self.total_size_label.setText(size_str)
        
        print(f"设置UI标签，文件数: {synced_files}, 格式化大小: {size_str}")
    
    def _format_size(self, size_bytes):
        """将字节数格式化为可读的大小字符串"""
        # 转换为MB
        size_mb = size_bytes / (1024 * 1024)
        # 如果大于1000MB，则转换为GB显示
        if size_mb > 1000:
            return f"{size_mb/1024:.2f} GB"
        else:
            return f"{size_mb:.2f} MB"
    
    def clear_log(self):
        """清空日志"""
        self.log_text.clear()
        self.add_log("日志已清空")
    
    def on_webdav_test_result(self, success, message):
        """WebDAV测试结果处理"""
        self.test_webdav_btn.setText("测试连接")
        
        if success:
            QMessageBox.information(self, "连接测试", message)
            self.add_log("WebDAV连接测试成功")
        else:
            QMessageBox.warning(self, "连接测试", message)
            self.add_log(f"WebDAV连接测试失败: {message}")
    
    def start_local_sync(self):
        """开始本地同步"""
        local_dir = self.local_dir_edit.text().strip()
        external_dir = self.external_dir_edit.text().strip()
        
        if not local_dir or not external_dir:
            QMessageBox.warning(self, "同步错误", "请先设置本地和外部媒体库目录")
            return
        
        # 检查目录是否存在
        if not os.path.exists(local_dir):
            QMessageBox.warning(self, "同步错误", f"本地目录不存在: {local_dir}")
            return
        
        # 确定同步模式
        sync_mode = "bidirectional"
        if self.upload_radio.isChecked():
            sync_mode = "upload"
        elif self.download_radio.isChecked():
            sync_mode = "download"
        
        # 更新UI状态
        self.start_local_sync_btn.setEnabled(False)
        self.stop_local_sync_btn.setEnabled(True)
        
        # 添加日志
        self.add_log(f"开始本地同步: {sync_mode} 模式")
        self.add_log(f"本地目录: {local_dir}")
        self.add_log(f"外部目录: {external_dir}")
        
        # 获取排除的文件扩展名
        exclude_exts = [ext.strip() for ext in self.exclude_exts_edit.text().split(",") if ext.strip()]
        
        # 创建并启动同步线程
        self.plugin.sync_thread = MediaSyncThread(local_dir, external_dir, sync_mode, exclude_exts)
        self.plugin.sync_thread.progress_updated.connect(self.update_local_progress)
        
        # 直接连接到on_local_sync_complete方法，不使用lambda
        self.plugin.sync_thread.sync_complete.connect(self.on_local_sync_complete)
        
        self.plugin.sync_thread.start()
        
        # 保存设置
        self.plugin.set_setting("local_media_dir", local_dir)
        self.plugin.set_setting("external_media_dir", external_dir)
        self.plugin.set_setting("sync_mode", sync_mode)
        self.plugin.set_setting("last_sync_time", datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    
    def accept(self):
        """对话框关闭时保存设置"""
        self.plugin.save_settings()
        super().accept()
    
    def reject(self):
        """对话框取消时保存设置"""
        self.plugin.save_settings()
        super().reject()
