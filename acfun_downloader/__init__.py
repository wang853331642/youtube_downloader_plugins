# 在 plugins/bilibili_downloader/__init__.py 中
from .plugin import AcfunDownloaderPlugin

# 导出插件类，使插件管理器能够找到它
__all__ = ['AcfunDownloaderPlugin']