from abc import ABCMeta, abstractmethod
from pathlib import Path
from typing import Any, List, Dict, Tuple, Optional

from apscheduler.triggers.cron import CronTrigger
from transmission_rpc import Client
from urllib.parse import urlparse
from datetime import datetime, timedelta

from app.chain import ChainBase
from app.log import logger
from app.core.config import settings
from app.core.event import EventManager
from app.db.plugindata_oper import PluginDataOper
from app.db.systemconfig_oper import SystemConfigOper
from app.helper.downloader import DownloaderHelper
from app.helper.message import MessageHelper
from app.schemas import Notification, NotificationType, MessageChannel


class PluginChian(ChainBase):
    """
    插件处理链
    """
    pass


class TrackerLimit(metaclass=ABCMeta):
    """
    插件模块基类，通过继续该类实现插件功能
    除内置属性外，还有以下方法可以扩展或调用：
    - stop_service() 停止插件服务
    - get_config() 获取配置信息
    - update_config() 更新配置信息
    - init_plugin() 生效配置信息
    - get_data_path() 获取插件数据保存目录
    """
    # 插件名称
    plugin_name = "批量tracker限速"
    # 插件描述
    plugin_desc = "对同一个tracker地址的种子进行限速"
    # 插件图标
    plugin_icon = "brush.jpg"
    # 插件版本
    plugin_version = "0.0.1"
    # 插件作者
    plugin_author = "jxxghp,Fone-1"
    # 作者主页
    author_url = "https://github.com/Fone-1"
    # 插件配置项ID前缀
    plugin_config_prefix = "trackerLimit_"
    # 加载顺序
    plugin_order = 21
    # 可使用的用户级别
    auth_level = 2

    # 存储下载器配置
    downloader_client = {}
    #
    downloader_helper = None
    # 是否启用
    enabled = False
    # 存储tracker限速设置
    trackers_limits = ""
    # 是否通知
    notify = False
    # 是否立即运行一次
    onlyonce = False
    # 是否重新获取所有下载器的tracker
    getTrackers = False
    # 需要限速的下载器
    limitdownloader = None
    # 定时
    cron = None
    # 是否启用定时任务
    task_enabled = False
    # 默认12小时限速一次
    _limit_interval = 720

    def __init__(self):
        # 插件数据
        self.plugindata = PluginDataOper()
        # 处理链
        self.chain = PluginChian()
        # 系统配置
        self.systemconfig = SystemConfigOper()
        # 系统消息
        self.systemmessage = MessageHelper()
        # 事件管理器
        self.eventmanager = EventManager()

    def init_plugin(self, config: dict = None):
        """
        生效配置信息
        :param config: 配置信息字典
        """
        self.enabled = config.get("enabled", False)
        self.notify = config.get("notify", True)
        self.onlyonce = config.get("onlyonce", False)
        self.downloader_helper = DownloaderHelper()
        self.trackers_limits = config.get("trackers_limits", "")
        self.getTrackers = config.get("getTrackers", False)
        self.cron = config.get("cron")
        self.limitdownloader = config.get("limitdownloader")
        self.task_enabled = self.enabled and self.limitdownloader
        # self.downloader_client = config.get("downloader_client", {})

        if not self.downloader_client:
            logger.info("未读取到下载器配置，开始连接下载器")
            for config in self.downloader_helper.get_configs().values():
                if config.type == 'transmission':
                    host = urlparse(config.config['host'])
                    TRANSMISSION_HOST = host.hostname
                    TRANSMISSION_PORT = host.port
                    USERNAME = config.config['username']
                    PASSWORD = config.config['password']
                    isHttps = host.scheme == 'https'
                    try:
                        if isHttps:
                            client = Client(
                                host=TRANSMISSION_HOST,
                                port=TRANSMISSION_PORT,
                                username=USERNAME,
                                password=PASSWORD,
                                protocol='https'
                            )
                        else:
                            client = Client(
                                host=TRANSMISSION_HOST,
                                port=TRANSMISSION_PORT,
                                username=USERNAME,
                                password=PASSWORD,
                            )
                    except:
                        logger.error(f"连接下载器{config.config['host']}失败！")
                    self.downloader_client[config.name] = client
                    if self.getTrackers:
                        self.getTrackers = False
                        self.update_config({"getTrackers": False, "trackers_limits": ""})
                        # 获取所有种子
                        torrents = client.get_torrents()
                        for torrent in torrents:
                            # 获取当前种子的Tracker列表
                            tracker = torrent.trackers[0]
                            # 解析Tracker的域名
                            url = tracker.announce
                            hostname = urlparse(url).hostname
                            if self.trackers_limits.find(hostname) == -1:
                                self.trackers_limits += hostname
                                self.trackers_limits += ' -1'
                                self.trackers_limits += '\n'
                        print(self.trackers_limits)
                        self.update_config({"trackers_limits": self.trackers_limits})
                        pass
                    else:
                        pass
        if self.onlyonce:
            self.apply_limits()
            self.onlyonce = False
            self.update_config({"onlyonce": False, "trackers_limits": self.trackers_limits})
        pass

    def apply_limits(self):
        """
        应用限速设置
        """
        limits = self.trackers_limits.split('\n')
        speed = {}
        cancelNum = 0
        applyNum = 0
        for limit in limits:
            if limit is None or limit == '' or limit.find(' ') == -1:
                pass
            temp = limit.split(' ')
            if len(temp) == 2:
                speed[temp[0]] = temp[1]
        for key in self.downloader_client:
            if key not in self.limitdownloader:
                logger.info("下载器"+key+"不在限速下载器列表内，取消操作")
                pass
            downloader = self.downloader_client[key]
            torrents = downloader.get_torrents()
            for torrent in torrents:
                # 获取当前种子的Tracker列表
                hosts = []
                for tracker in torrent.trackers:
                    url = tracker.announce
                    hostname = urlparse(url).hostname
                    hosts.append(hostname)
                limit_host = [item for item in hosts if item in speed.keys()]
                if limit_host:
                    current_limit = torrent.upload_limit
                    new_limit = int(speed[str(limit_host[0])])
                    if new_limit == -1:
                        logger.info(f'取消种子 [{torrent.name}] 限速，改用全局限速')
                        downloader.change_torrent(
                            torrent.id,
                            honors_session_limits=True,
                            upload_limited=False
                        )
                        cancelNum += 1
                        pass
                    # 仅当限速值变化时更新
                    elif current_limit != new_limit:
                        logger.info(f'更新种子 [{torrent.name}] 限速: {new_limit}KB/s')
                        downloader.change_torrent(
                            torrent.id,
                            upload_limit=new_limit,
                            honors_session_limits=False,
                            upload_limited=True
                        )
                        applyNum += 1
        msg = f"{cancelNum}个种子取消限速，{applyNum}个种子限速成功"
        if self.notify:
            self.post_message(mtype=NotificationType.Plugin, title="批量Tracker限速", text=msg)
        pass

    def get_state(self) -> bool:
        """
        获取插件运行状态
        """
        return self.enabled

    @staticmethod
    def get_command() -> List[Dict[str, Any]]:
        """
        注册插件远程命令
        [{
            "cmd": "/xx",
            "event": EventType.xx,
            "desc": "名称",
            "category": "分类，需要注册到Wechat时必须有分类",
            "data": {}
        }]
        """
        pass

    def get_api(self) -> List[Dict[str, Any]]:
        """
        注册插件API
        [{
            "path": "/xx",
            "endpoint": self.xxx,
            "methods": ["GET", "POST"],
            "summary": "API名称",
            "description": "API说明"
        }]
        """
        pass

    def get_form(self) -> Tuple[List[dict], Dict[str, Any]]:
        """
        拼装插件配置页面，需要返回两块数据：1、页面配置；2、数据结构
        插件配置页面使用Vuetify组件拼装，参考：https://vuetifyjs.com/
        """
        downloader_options = [{"title": config.name, "value": config.name}
                              for config in self.downloader_helper.get_configs().values()]

        return [
                   {
                       'component': 'VForm',
                       'content': [
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'enabled',
                                                   'label': '启用插件',
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'notify',
                                                   'label': '发送通知',
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                           'md': 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'onlyonce',
                                                   'label': '立即运行一次',
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           "cols": 12,
                                           "md": 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'getTrackers',
                                                   'label': '重新获取Tracker',
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           "cols": 12,
                                           "md": 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VSwitch',
                                               'props': {
                                                   'model': 'cancelLimit',
                                                   'label': '取消限速',
                                               }
                                           }
                                       ]
                                   },
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           "cols": 12,
                                           "md": 4
                                       },
                                       'content': [
                                           {
                                               'component': 'VCronField',
                                               'props': {
                                                   'model': 'cron',
                                                   'label': '执行周期',
                                                   'placeholder': '如：0 0-1 * * FRI,SUN'
                                               }
                                           }
                                       ]
                                   },
                               ]
                           },
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12
                                       },
                                       'content': [
                                           {
                                               'component': 'VSelect',
                                               'props': {
                                                   'multiple': True,
                                                   'chips': True,
                                                   'clearable': True,
                                                   'model': 'limitdownloader',
                                                   'label': '限速下载器',
                                                   'items': downloader_options
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12
                                       },
                                       'content': [
                                           {
                                               'component': 'VTextarea',
                                               'props': {
                                                   'model': 'trackers_limits',
                                                   'label': 'tracker限速',
                                                   'rows': 10,
                                                   'placeholder': '每行一个配置，格式为：tracker limits'
                                               }
                                           }
                                       ]
                                   }
                               ]
                           },
                           {
                               'component': 'VRow',
                               'content': [
                                   {
                                       'component': 'VCol',
                                       'props': {
                                           'cols': 12,
                                       },
                                       'content': [
                                           {
                                               'component': 'VAlert',
                                               'props': {
                                                   'type': 'info',
                                                   'variant': 'tonal',
                                                   'text': '限速格式为host limits，中间有空格！！！单位为kb！！(-1为不限速)'
                                               }
                                           }
                                       ]
                                   }
                               ]
                           }
                       ]
                   }
               ], {
                   "enabled": False,
                   "trackers_limits": self.trackers_limits,
                   "onlyonce": False,
                   "getTrackers": False,
                   "corn":self.cron
               }

    def get_page(self) -> List[dict]:
        """
        拼装插件详情页面，需要返回页面配置，同时附带数据
        插件详情页面使用Vuetify组件拼装，参考：https://vuetifyjs.com/
        """

        pass

    def get_service(self) -> List[Dict[str, Any]]:
        """
        注册插件公共服务
        [{
            "id": "服务ID",
            "name": "服务名称",
            "trigger": "触发器：cron/interval/date/CronTrigger.from_crontab()",
            "func": self.xxx,
            "kwargs": {} # 定时器参数
        }]
        """
        if self.task_enabled:
            if self.cron:
                values = self.cron.split()
                values[0] = f"{datetime.now().minute % 10}/10"
                cron = " ".join(values)
                logger.info(f"下载器限速定时服务启动，执行周期 {cron}")
                cron_trigger = CronTrigger.from_crontab(cron)
                return [{
                    "id": "BrushFlow",
                    "name": "下载器限速服务",
                    "trigger": cron_trigger,
                    "func": self.apply_limits
                }]
            else:
                logger.info(f"下载器限速定时服务启动，时间间隔 {self._limit_interval} 分钟")
                return [{
                    "id": "BrushFlow",
                    "name": "下载器限速服务",
                    "trigger": "interval",
                    "func": self.apply_limits,
                    "kwargs": {"minutes": self._limit_interval}
                }]
        pass

    def get_dashboard(self, key: str, **kwargs) -> Optional[Tuple[Dict[str, Any], Dict[str, Any], List[dict]]]:
        """
        获取插件仪表盘页面，需要返回：1、仪表板col配置字典；2、全局配置（自动刷新等）；3、仪表板页面元素配置json（含数据）
        1、col配置参考：
        {
            "cols": 12, "md": 6
        }
        2、全局配置参考：
        {
            "refresh": 10, // 自动刷新时间，单位秒
            "border": True, // 是否显示边框，默认True，为False时取消组件边框和边距，由插件自行控制
            "title": "组件标题", // 组件标题，如有将显示该标题，否则显示插件名称
            "subtitle": "组件子标题", // 组件子标题，缺省时不展示子标题
        }
        3、页面配置使用Vuetify组件拼装，参考：https://vuetifyjs.com/

        kwargs参数可获取的值：1、user_agent：浏览器UA

        :param key: 仪表盘key，根据指定的key返回相应的仪表盘数据，缺省时返回一个固定的仪表盘数据（兼容旧版）
        """
        pass

    def get_dashboard_meta(self) -> Optional[List[Dict[str, str]]]:
        """
        获取插件仪表盘元信息
        返回示例：
            [{
                "key": "dashboard1", // 仪表盘的key，在当前插件范围唯一
                "name": "仪表盘1" // 仪表盘的名称
            }, {
                "key": "dashboard2",
                "name": "仪表盘2"
            }]
        """
        pass

    def stop_service(self):
        """
        停止插件
        """
        pass

    def update_config(self, config: dict, plugin_id: Optional[str] = None) -> bool:
        """
        更新配置信息
        :param config: 配置信息字典
        :param plugin_id: 插件ID
        """
        if not plugin_id:
            plugin_id = self.__class__.__name__
        return self.systemconfig.set(f"plugin.{plugin_id}", config)

    def get_config(self, plugin_id: Optional[str] = None) -> Any:
        """
        获取配置信息
        :param plugin_id: 插件ID
        """
        if not plugin_id:
            plugin_id = self.__class__.__name__
        return self.systemconfig.get(f"plugin.{plugin_id}")

    def get_data_path(self, plugin_id: Optional[str] = None) -> Path:
        """
        获取插件数据保存目录
        """
        if not plugin_id:
            plugin_id = self.__class__.__name__
        data_path = settings.PLUGIN_DATA_PATH / f"{plugin_id}"
        if not data_path.exists():
            data_path.mkdir(parents=True)
        return data_path

    def save_data(self, key: str, value: Any, plugin_id: Optional[str] = None):
        """
        保存插件数据
        :param key: 数据key
        :param value: 数据值
        :param plugin_id: 插件ID
        """
        if not plugin_id:
            plugin_id = self.__class__.__name__
        self.plugindata.save(plugin_id, key, value)

    def get_data(self, key: Optional[str] = None, plugin_id: Optional[str] = None) -> Any:
        """
        获取插件数据
        :param key: 数据key
        :param plugin_id: plugin_id
        """
        if not plugin_id:
            plugin_id = self.__class__.__name__
        return self.plugindata.get_data(plugin_id, key)

    def del_data(self, key: str, plugin_id: Optional[str] = None) -> Any:
        """
        删除插件数据
        :param key: 数据key
        :param plugin_id: plugin_id
        """
        if not plugin_id:
            plugin_id = self.__class__.__name__
        return self.plugindata.del_data(plugin_id, key)

    def post_message(self, channel: MessageChannel = None, mtype: NotificationType = None, title: Optional[str] = None,
                     text: Optional[str] = None, image: Optional[str] = None, link: Optional[str] = None,
                     userid: Optional[str] = None, username: Optional[str] = None,
                     **kwargs):
        """
        发送消息
        """
        if not link:
            link = settings.MP_DOMAIN(f"#/plugins?tab=installed&id={self.__class__.__name__}")
        self.chain.post_message(Notification(
            channel=channel, mtype=mtype, title=title, text=text,
            image=image, link=link, userid=userid, username=username, **kwargs
        ))

    def close(self):
        pass
