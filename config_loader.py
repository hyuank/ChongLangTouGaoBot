# config_loader.py

import json
import os
import sys
import logging
import threading
from typing import List, Dict, Any

logger = logging.getLogger(__name__)

PATH = os.path.dirname(os.path.realpath(__file__)) + os.sep

CONFIG: Dict[str, Any] = {}  # 初始化为空字典，确保全局 CONFIG 变量存在

# =============================================
#  所有函数定义都放在这里 (在 try...except 之前)
# =============================================


# --- 配置保存函数 ---
def save_config_sync():
    """同步保存配置到 config.json"""
    config_path = PATH + "config.json"
    try:
        # 使用 CONFIG 的当前状态创建副本以进行保存
        config_to_save = CONFIG.copy()
        # 确保 BlockedUsers 是列表（以防万一在内存中被意外修改）
        if "BlockedUsers" not in config_to_save or not isinstance(
            config_to_save.get("BlockedUsers"), list
        ):
            config_to_save["BlockedUsers"] = []

        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(config_to_save, f, ensure_ascii=False, indent=4)
        # logger.info(f"配置已同步保存到 {config_path}") # 可以在调用处记录日志
    except Exception as e:
        logger.error(f"同步保存配置到 {config_path} 时出错: {e}", exc_info=True)


async def save_config_async():
    """异步触发同步保存配置"""
    logger.debug("触发异步保存配置...")  # 添加调试日志
    # 在单独线程中运行以避免阻塞 asyncio 事件循环
    thread = threading.Thread(target=save_config_sync, daemon=True)
    thread.start()
    # 如果需要等待保存完成（通常不需要），可以 thread.join()，但这会阻塞


# --- 配置获取函数 ---
def get_token() -> str | None:
    return CONFIG.get("Token")


def get_admin_id() -> int | None:
    admin_id = CONFIG.get("Admin")
    try:
        return int(admin_id) if admin_id else None
    except (ValueError, TypeError):
        return None


def get_group_id() -> int | None:
    group_id = CONFIG.get("Group_ID")
    try:
        # Group ID 可以是负数
        return int(group_id) if group_id else None
    except (ValueError, TypeError):
        return None


def get_publish_channel_id() -> str | int | None:
    # 频道 ID 可能是 @username 或 -100xxxx
    channel_id = CONFIG.get("Publish_Channel_ID")
    # 如果是数字字符串，尝试转为 int
    if isinstance(channel_id, str) and channel_id.replace("-", "").isdigit():
        try:
            return int(channel_id)
        except ValueError:
            return channel_id  # 转换失败则返回原字符串
    return channel_id  # 返回原始值 (可能是 @username 或 None 或其他)


def update_config(key: str, value: Any):
    """安全地更新内存中的配置项"""
    logger.debug(f"更新配置: {key} = {value}")
    CONFIG[key] = value
    # 注意：此函数只更新内存，需要调用 save_config_async() 来持久化


# --- 黑名单管理函数 ---
def get_blocked_users() -> List[int]:
    """返回被阻止的用户 ID 列表 (确保是整数列表)"""
    blocked = CONFIG.get("BlockedUsers", [])
    if isinstance(blocked, list):
        valid_ids = []
        for item in blocked:
            try:
                valid_ids.append(int(item))
            except (ValueError, TypeError):
                logger.warning(f"配置文件中的 BlockedUsers 列表包含非整数项: {item}")
        return valid_ids
    logger.warning("配置文件中的 'BlockedUsers' 不是列表，返回空列表。")
    return []


def add_blocked_user(user_id: int | str):
    """将用户 ID 添加到阻止列表（内存中）"""
    try:
        uid_int = int(user_id)
    except (ValueError, TypeError):
        logger.warning(f"尝试添加无效的用户 ID 到黑名单: {user_id}")
        return False

    # 直接操作 CONFIG 中的列表 (如果不存在则创建)
    if "BlockedUsers" not in CONFIG or not isinstance(CONFIG.get("BlockedUsers"), list):
        CONFIG["BlockedUsers"] = []

    current_list = CONFIG["BlockedUsers"]
    if uid_int not in current_list:
        current_list.append(uid_int)
        logger.info(f"用户 {uid_int} 已添加到阻止列表 (内存中)。")
        # 注意: 需要调用 save_config_async() 来保存更改
        return True
    else:
        logger.info(f"用户 {uid_int} 已在阻止列表中。")
        return False


def remove_blocked_user(user_id: int | str):
    """从阻止列表中移除用户 ID（内存中）"""
    try:
        uid_int = int(user_id)
    except (ValueError, TypeError):
        logger.warning(f"尝试移除无效的用户 ID 从黑名单: {user_id}")
        return False

    if "BlockedUsers" in CONFIG and isinstance(CONFIG.get("BlockedUsers"), list):
        current_list = CONFIG["BlockedUsers"]
        if uid_int in current_list:
            try:
                current_list.remove(uid_int)
                logger.info(f"用户 {uid_int} 已从阻止列表移除 (内存中)。")
                # 注意: 需要调用 save_config_async() 来保存更改
                return True
            except ValueError:  # 以防万一
                logger.warning(f"尝试从黑名单列表移除 {uid_int} 时发生 ValueError。")
                return False
        else:
            logger.info(f"用户 {uid_int} 不在阻止列表中。")
            return False
    else:
        logger.info(f"用户 {uid_int} 不在阻止列表中 (或列表不存在)。")
        return False


# =============================================
#  配置文件加载逻辑 (在函数定义之后执行)
# =============================================
try:
    config_path = PATH + "config.json"
    logger.info(f"正在从 {config_path} 加载配置...")
    with open(config_path, "r", encoding="utf-8") as f:
        # 加载配置并更新到全局 CONFIG 字典
        loaded_config = json.load(f)
        CONFIG.update(loaded_config)
    logger.info("配置加载成功.")

    # --- 初始化/验证 BlockedUsers ---
    if "BlockedUsers" not in CONFIG or not isinstance(CONFIG.get("BlockedUsers"), list):
        logger.warning("配置文件中 'BlockedUsers' 丢失或格式不正确，将重置为空列表。")
        CONFIG["BlockedUsers"] = []
        # 触发一次保存以修复配置文件
        save_config_sync()

except FileNotFoundError:
    logger.error(f"错误：找不到配置文件 {config_path}。")
    default_config = {
        "Token": "YOUR_BOT_TOKEN",
        "Admin": 0,
        "Group_ID": 0,
        "Publish_Channel_ID": "",
        "BlockedUsers": [],
    }
    try:
        with open(config_path, "w", encoding="utf-8") as f:
            json.dump(default_config, f, ensure_ascii=False, indent=4)
        logger.info(
            f"已在 {config_path} 创建默认配置文件，请填写 Token 和 Admin ID 后重新运行。"
        )
    except Exception as e:
        logger.error(f"创建默认配置文件失败: {e}")
    sys.exit(1)

except json.JSONDecodeError as e:
    logger.error(f"错误：配置文件 {config_path} 格式无效: {e}")
    sys.exit(1)
except Exception as e:
    logger.error(f"加载配置时发生未知错误: {e}", exc_info=True)
    sys.exit(1)

# --- 加载完成后打印一些关键配置信息 ---
logger.debug(f"加载的权蛆 ID: {get_admin_id()}")
logger.debug(f"加载的群组 ID: {get_group_id()}")
logger.debug(f"加载的阻止用户: {get_blocked_users()}")
