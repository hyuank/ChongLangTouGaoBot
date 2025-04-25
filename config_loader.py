# config_loader.py

import json
import os
import sys
import logging
import threading
from typing import List, Dict, Any, Optional, Union

logger = logging.getLogger(__name__)

# 获取当前脚本所在目录的绝对路径
PATH = os.path.dirname(os.path.realpath(__file__)) + os.sep

# 全局变量，用于存储从 config.json 加载的配置
CONFIG: Dict[str, Any] = {}  # 初始化为空字典，确保全局 CONFIG 变量存在

# =============================================
#  所有函数定义都放在这里 (在 try...except 之前)
# =============================================


# --- 配置保存函数 ---
def save_config_sync():
    """同步保存当前内存中的 CONFIG 到 config.json 文件"""
    config_path = PATH + "config.json"
    try:
        # 使用 CONFIG 的当前状态创建副本以进行保存，避免在写入时被修改
        config_to_save = CONFIG.copy()

        # --- 确保新的 Webhook 和测试模式字段有默认值 ---
        defaults = {
            "TestMode": True,
            "WebhookURL": "",
            "WebhookSecretToken": "",
            "ListenAddress": "0.0.0.0",
            "ListenPort": 8443,
            "BlockedUsers": [],
            "EnableFooter": False,
            "ChatLink": "",
            "FooterEmojis": {"submission": "👊", "channel": "🌊", "chat": "🔥"},
            "WarningUsers": {},
        }
        for key, default_value in defaults.items():
            if key not in config_to_save:
                # 特殊处理 BlockedUsers 和 WarningUsers 的类型
                if key == "BlockedUsers" and not isinstance(
                    config_to_save.get(key), list
                ):
                    config_to_save[key] = []
                elif key == "WarningUsers" and not isinstance(
                    config_to_save.get(key), dict
                ):
                    config_to_save[key] = {}
                # 对于其他键，如果不存在，则添加默认值
                elif key not in ["BlockedUsers", "WarningUsers"]:
                    config_to_save[key] = default_value
                    logger.info(
                        f"配置文件中未找到 '{key}'，将添加默认值: {default_value}。"
                    )

        # 确保 BlockedUsers 是列表 (以防万一在内存中被意外修改)
        if not isinstance(config_to_save.get("BlockedUsers"), list):
            config_to_save["BlockedUsers"] = []
        # 确保 WarningUsers 是字典
        if not isinstance(config_to_save.get("WarningUsers"), dict):
            config_to_save["WarningUsers"] = {}
        # 确保 FooterEmojis 及其默认键存在
        default_emojis = {"submission": "👊", "channel": "🌊", "chat": "🔥"}
        if "FooterEmojis" not in config_to_save or not isinstance(
            config_to_save.get("FooterEmojis"), dict
        ):
            config_to_save["FooterEmojis"] = default_emojis.copy()
        else:
            for emoji_key, emoji_value in default_emojis.items():
                if emoji_key not in config_to_save["FooterEmojis"]:
                    config_to_save["FooterEmojis"][emoji_key] = emoji_value
        # 确保 TestMode 是布尔值
        if not isinstance(config_to_save.get("TestMode"), bool):
            config_to_save["TestMode"] = True  # 默认为 True
            logger.warning("配置文件中的 'TestMode' 不是布尔值，已重置为 True。")
        # 确保 ListenPort 是整数
        try:
            if "ListenPort" in config_to_save:
                config_to_save["ListenPort"] = int(config_to_save["ListenPort"])
        except (ValueError, TypeError):
            config_to_save["ListenPort"] = 8443  # 默认端口
            logger.warning("配置文件中的 'ListenPort' 不是有效整数，已重置为 8443。")
        # ----------------------------------------------------------

        with open(config_path, "w", encoding="utf-8") as f:
            # 使用 json.dump 保存字典，ensure_ascii=False 保证中文正常显示，indent=4 美化格式
            json.dump(config_to_save, f, ensure_ascii=False, indent=4)
        # logger.info(f"配置已同步保存到 {config_path}") # 可以在调用处记录日志，避免日志过于频繁
    except Exception as e:
        logger.error(f"同步保存配置到 {config_path} 时出错: {e}", exc_info=True)


async def save_config_async():
    """异步触发同步保存配置的操作"""
    logger.debug("触发异步保存配置...")
    # 在单独线程中运行 save_config_sync 以避免阻塞 asyncio 事件循环
    # daemon=True 使得主线程退出时该线程也会退出
    thread = threading.Thread(target=save_config_sync, daemon=True)
    thread.start()
    # 如果需要等待保存完成（通常不需要），可以 thread.join()，但这会阻塞异步代码


# --- 配置获取函数 ---
def get_token() -> str | None:
    """获取 Bot Token"""
    return CONFIG.get("Token")


def get_admin_id() -> int | None:
    """获取权蛆用户 ID (权蛆 ID)"""
    admin_id = CONFIG.get("Admin")
    try:
        # 尝试将获取到的值转为整数，如果失败或值为 None 则返回 None
        return int(admin_id) if admin_id else None
    except (ValueError, TypeError):
        # 处理 admin_id 不是有效数字字符串或 None 的情况
        return None


def get_group_id() -> int | None:
    """获取审稿群组 ID"""
    group_id = CONFIG.get("Group_ID")
    try:
        # Group ID 可以是负数，所以直接尝试转为整数
        return int(group_id) if group_id else None
    except (ValueError, TypeError):
        return None


def get_publish_channel_id() -> Union[str, int, None]:
    """获取发布频道的 ID 或用户名"""
    # 频道 ID 可能是 @username 或 -100xxxx (int)
    channel_id = CONFIG.get("Publish_Channel_ID")
    # 如果是数字字符串 (可能带负号)，尝试转为 int
    if isinstance(channel_id, str) and channel_id.replace("-", "").isdigit():
        try:
            return int(channel_id)
        except ValueError:
            # 转换失败（理论上不太可能发生），返回原字符串
            return channel_id
    # 返回原始值 (可能是 @username, None, 或已经是 int)
    return channel_id


# --- Webhook 和测试模式获取函数 ---
def is_test_mode() -> bool:
    """检查是否启用了测试模式 (轮询)"""
    # 默认为 True (测试模式)
    return bool(CONFIG.get("TestMode", True))


def get_webhook_url() -> Optional[str]:
    """获取 Webhook URL"""
    url = CONFIG.get("WebhookURL")
    if url and isinstance(url, str) and url.startswith("https://"):
        return url
    return None


def get_webhook_secret_token() -> Optional[str]:
    """获取 Webhook Secret Token"""
    token = CONFIG.get("WebhookSecretToken")
    # 返回字符串或 None，允许为空字符串
    return str(token) if token is not None else None


def get_listen_address() -> str:
    """获取 Webhook 监听地址"""
    # 默认为 '0.0.0.0'
    addr = CONFIG.get("ListenAddress")
    return str(addr) if addr else "0.0.0.0"


def get_listen_port() -> int:
    """获取 Webhook 监听端口"""
    port = CONFIG.get("ListenPort")
    try:
        # 确保端口是有效整数
        return int(port) if port else 8443
    except (ValueError, TypeError):
        # 默认端口 8443
        return 8443


def is_footer_enabled() -> bool:
    """检查是否启用小尾巴功能"""
    return bool(CONFIG.get("EnableFooter", False))


def get_chat_link() -> Optional[str]:
    """获取自定义聊天链接"""
    link = CONFIG.get("ChatLink")
    if link and isinstance(link, str) and link.startswith("https://"):
        return link
    return None


def update_config(key: str, value: Any):
    """安全地更新内存中的配置项 (CONFIG 字典)"""
    logger.debug(f"更新配置: {key} = {value}")
    CONFIG[key] = value
    # 注意：此函数只更新内存，需要显式调用 save_config_async() 或 save_config_sync() 来持久化


def get_footer_emojis() -> Dict[str, str]:
    """获取小尾巴 Emoji 配置字典"""
    # 返回副本以防外部修改
    emojis = CONFIG.get(
        "FooterEmojis", {"submission": "👊", "channel": "🌊", "chat": "🔥"}
    )
    # 确保返回的是字典
    return (
        emojis.copy()
        if isinstance(emojis, dict)
        else {"submission": "👊", "channel": "🌊", "chat": "🔥"}
    )


# --- 黑名单管理函数 ---
def get_blocked_users() -> List[int]:
    """返回被阻止的用户 ID 列表 (确保是整数列表)"""
    blocked = CONFIG.get("BlockedUsers", [])  # 获取列表，如果不存在则返回空列表
    if isinstance(blocked, list):
        valid_ids = []
        for item in blocked:
            try:
                # 确保列表中的每个 ID 都是整数
                valid_ids.append(int(item))
            except (ValueError, TypeError):
                logger.warning(f"配置文件中的 BlockedUsers 列表包含非整数项: {item}")
        return valid_ids
    # 如果 BlockedUsers 不是列表 (配置错误)，则返回空列表并警告
    logger.warning("配置文件中的 'BlockedUsers' 不是列表，返回空列表。")
    CONFIG["BlockedUsers"] = []  # 重置为列表
    return []


def add_blocked_user(user_id: Union[int, str]):
    """将用户 ID 添加到阻止列表（仅内存中）"""
    try:
        uid_int = int(user_id)  # 确保传入的是有效数字 ID
    except (ValueError, TypeError):
        logger.warning(f"尝试添加无效的用户 ID 到黑名单: {user_id}")
        return False  # 添加失败

    # 直接操作 CONFIG 中的列表 (如果不存在或类型错误则创建/重置)
    if "BlockedUsers" not in CONFIG or not isinstance(CONFIG.get("BlockedUsers"), list):
        CONFIG["BlockedUsers"] = []

    current_list = CONFIG["BlockedUsers"]
    if uid_int not in current_list:
        current_list.append(uid_int)
        logger.info(f"用户 {uid_int} 已添加到阻止列表 (内存中)。")
        # 注意: 需要调用 save_config_async() 来保存更改
        return True  # 添加成功
    else:
        logger.info(f"用户 {uid_int} 已在阻止列表中。")
        return False  # 已存在，未添加


def remove_blocked_user(user_id: Union[int, str]):
    """从阻止列表中移除用户 ID（仅内存中）"""
    try:
        uid_int = int(user_id)  # 确保传入的是有效数字 ID
    except (ValueError, TypeError):
        logger.warning(f"尝试移除无效的用户 ID 从黑名单: {user_id}")
        return False  # 移除失败

    # 检查 BlockedUsers 是否存在且是列表
    if "BlockedUsers" in CONFIG and isinstance(CONFIG.get("BlockedUsers"), list):
        current_list = CONFIG["BlockedUsers"]
        if uid_int in current_list:
            try:
                current_list.remove(uid_int)
                logger.info(f"用户 {uid_int} 已从阻止列表移除 (内存中)。")
                # 注意: 需要调用 save_config_async() 来保存更改
                return True  # 移除成功
            except ValueError:  # 理论上不会发生，因为上面检查了 in current_list
                logger.warning(f"尝试从黑名单列表移除 {uid_int} 时发生 ValueError。")
                return False
        else:
            logger.info(f"用户 {uid_int} 不在阻止列表中。")
            return False  # 用户不在列表中
    else:
        logger.info(f"用户 {uid_int} 不在阻止列表中 (或列表不存在)。")
        # 如果列表不存在，也视为不在列表中
        if "BlockedUsers" not in CONFIG or not isinstance(
            CONFIG.get("BlockedUsers"), list
        ):
            CONFIG["BlockedUsers"] = []
        return False


# --- 警告用户管理函数 ---
def get_warning_users() -> Dict[str, int]:
    """返回被警告的用户 ID 及其警告次数的字典"""
    warnings = CONFIG.get("WarningUsers", {})
    if not isinstance(warnings, dict):
        logger.warning("配置文件中的 'WarningUsers' 不是字典，返回空字典。")
        CONFIG["WarningUsers"] = {}
        return {}

    # 确保键是字符串，值是整数
    valid_warnings = {}
    for user_id, count in warnings.items():
        try:
            valid_warnings[str(user_id)] = int(count)
        except (ValueError, TypeError):
            logger.warning(
                f"配置文件中的 WarningUsers 字典包含无效项: {user_id}:{count}"
            )

    return valid_warnings


def get_user_warning_count(user_id: Union[int, str]) -> int:
    """获取指定用户的警告次数"""
    try:
        uid_str = str(user_id)  # 确保用户ID是字符串格式
    except (ValueError, TypeError):
        logger.warning(f"尝试获取无效用户 ID 的警告次数: {user_id}")
        return 0  # 返回0表示无警告

    warnings = get_warning_users()
    return warnings.get(uid_str, 0)


def add_warning_to_user(user_id: Union[int, str]) -> int:
    """给用户添加一次警告，返回警告后的总次数"""
    try:
        uid_str = str(user_id)  # 确保用户ID是字符串格式
    except (ValueError, TypeError):
        logger.warning(f"尝试给无效用户 ID 添加警告: {user_id}")
        return 0  # 返回0表示操作失败

    # 确保 WarningUsers 是字典
    if "WarningUsers" not in CONFIG or not isinstance(CONFIG.get("WarningUsers"), dict):
        CONFIG["WarningUsers"] = {}

    # 获取当前警告次数并加1
    current_count = CONFIG["WarningUsers"].get(uid_str, 0)
    new_count = current_count + 1
    CONFIG["WarningUsers"][uid_str] = new_count

    logger.info(f"用户 {user_id} 的警告次数已增加到 {new_count} (内存中)。")
    # 注意: 需要调用 save_config_async() 来保存更改
    return new_count


def reset_user_warning(user_id: Union[int, str]) -> bool:
    """重置用户的警告次数为0，如果用户有警告记录则返回True"""
    try:
        uid_str = str(user_id)  # 确保用户ID是字符串格式
    except (ValueError, TypeError):
        logger.warning(f"尝试重置无效用户 ID 的警告: {user_id}")
        return False  # 操作失败

    # 确保 WarningUsers 是字典
    if "WarningUsers" not in CONFIG or not isinstance(CONFIG.get("WarningUsers"), dict):
        CONFIG["WarningUsers"] = {}
        return False  # 没有警告记录

    # 检查用户是否有警告记录
    if uid_str in CONFIG["WarningUsers"]:
        # 只有在当前次数不为0时才重置并记录
        if CONFIG["WarningUsers"][uid_str] != 0:
            CONFIG["WarningUsers"][uid_str] = 0
            logger.info(f"用户 {user_id} 的警告次数已重置为0 (内存中)。")
            # 注意: 需要调用 save_config_async() 来保存更改
            return True  # 成功重置
        else:
            return False  # 本身就是0，不算重置成功
    else:
        return False  # 用户没有警告记录


# =============================================
#  配置文件加载逻辑 (在函数定义之后执行)
# =============================================
try:
    config_path = PATH + "config.json"
    logger.info(f"正在从 {config_path} 加载配置...")
    with open(config_path, "r", encoding="utf-8") as f:
        # 加载 JSON 文件内容并更新到全局 CONFIG 字典
        loaded_config = json.load(f)
        CONFIG.update(loaded_config)
    logger.info("配置加载成功.")

    # --- 验证和初始化新配置项 ---
    needs_save = False  # 标记是否需要保存修复后的配置
    defaults = {
        "TestMode": True,
        "WebhookURL": "",
        "WebhookSecretToken": "",
        "ListenAddress": "0.0.0.0",
        "ListenPort": 8443,
        "BlockedUsers": [],
        "EnableFooter": False,
        "ChatLink": "",
        "FooterEmojis": {"submission": "👊", "channel": "🌊", "chat": "🔥"},
        "WarningUsers": {},
    }
    for key, default_value in defaults.items():
        if key not in CONFIG:
            CONFIG[key] = default_value
            logger.warning(f"配置文件中未找到 '{key}'，已添加默认值: {default_value}。")
            needs_save = True

    # 强制类型检查和修正
    if not isinstance(CONFIG.get("BlockedUsers"), list):
        logger.warning("配置文件中的 'BlockedUsers' 不是列表，已重置为空列表。")
        CONFIG["BlockedUsers"] = []
        needs_save = True
    if not isinstance(CONFIG.get("WarningUsers"), dict):
        logger.warning("配置文件中的 'WarningUsers' 不是字典，已重置为空字典。")
        CONFIG["WarningUsers"] = {}
        needs_save = True
    if not isinstance(CONFIG.get("TestMode"), bool):
        logger.warning("配置文件中的 'TestMode' 不是布尔值，已重置为 True。")
        CONFIG["TestMode"] = True
        needs_save = True
    try:
        if "ListenPort" in CONFIG:
            CONFIG["ListenPort"] = int(CONFIG["ListenPort"])
    except (ValueError, TypeError):
        logger.warning("配置文件中的 'ListenPort' 不是有效整数，已重置为 8443。")
        CONFIG["ListenPort"] = 8443
        needs_save = True

    if needs_save:
        logger.info("配置文件已更新，正在保存更改...")
        save_config_sync()  # 保存一次修正后的配置

except FileNotFoundError:
    # 如果配置文件不存在，则初始化默认值
    logger.warning(f"未找到配置文件 {config_path}，将使用默认配置创建新文件。")
    CONFIG = {
        "Token": "",
        "Admin": "",
        "Group_ID": None,
        "Publish_Channel_ID": None,
        "TestMode": True,
        "WebhookURL": "",
        "WebhookSecretToken": "",
        "ListenAddress": "0.0.0.0",
        "ListenPort": 8443,
        "BlockedUsers": [],
        "WarningUsers": {},
        "EnableFooter": False,
        "ChatLink": "",
        "FooterEmojis": {"submission": "👊", "channel": "🌊", "chat": "🔥"},
    }
    # 创建一个包含默认值的新配置文件
    try:
        save_config_sync()
        logger.info(
            f"已创建默认配置文件 {config_path}，请填写 Token 和 Admin ID 后重新运行。"
            f"如果需要在生产环境使用 Webhook，请修改 TestMode 为 false 并填写 Webhook 相关配置。"
        )
    except Exception as e:
        logger.error(f"创建默认配置文件失败: {e}", exc_info=True)
    sys.exit(1)  # 缺少关键信息，必须退出

except json.JSONDecodeError:
    # 如果配置文件格式无效
    logger.error(f"配置文件 {config_path} 不是有效的 JSON 格式。请检查配置。")
    sys.exit(1)  # 无法加载配置，退出程序
except Exception as e:
    # 捕获其他可能的错误
    logger.error(f"加载配置时发生未知错误: {e}", exc_info=True)
    sys.exit(1)  # 无法加载配置，退出程序

# --- 加载完成后打印一些关键配置信息 (Debug 级别) ---
logger.debug(f"加载的权蛆 ID: {get_admin_id()}")
logger.debug(f"加载的群组 ID: {get_group_id()}")
logger.debug(f"加载的阻止用户: {get_blocked_users()}")
logger.debug(f"测试模式: {is_test_mode()}")
if not is_test_mode():
    logger.debug(f"Webhook URL: {get_webhook_url()}")
    logger.debug(f"Webhook 监听地址: {get_listen_address()}")
    logger.debug(f"Webhook 监听端口: {get_listen_port()}")
    logger.debug(
        f"Webhook Secret Token: {'已设置' if get_webhook_secret_token() else '未设置'}"
    )

# --- 检查生产模式下的 Webhook URL ---
if not is_test_mode() and not get_webhook_url():
    logger.error(
        "错误：当前为生产模式 (TestMode=false)，但未配置有效的 WebhookURL。请设置后重试。"
    )
    sys.exit(1)
