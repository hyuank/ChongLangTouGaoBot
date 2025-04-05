# data_manager.py

"""负责加载和保存 data.json (投稿数据)"""

import json
import os
import logging
import threading
import asyncio  # 引入 asyncio 以便 create_task
from config_loader import PATH
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

submission_list: Dict[str, Dict[str, Any]] = {}
DATA_FILE_PATH = PATH + "data.json"
DATA_LOCK = threading.Lock()

# --- 数据加载 ---
try:
    logger.info(f"正在从 {DATA_FILE_PATH} 加载投稿数据...")
    with open(DATA_FILE_PATH, "r", encoding="utf-8") as f:
        submission_list = json.load(f)
    logger.info(f"已加载 {len(submission_list)} 条投稿记录.")
except FileNotFoundError:
    logger.warning(f"未找到数据文件 {DATA_FILE_PATH}，将创建一个新的空数据文件。")
    submission_list = {}
except json.JSONDecodeError:
    logger.warning(f"数据文件 {DATA_FILE_PATH} 格式无效或为空，将使用空数据。")
    submission_list = {}
except Exception as e:
    logger.error(f"加载数据时发生未知错误: {e}")
    submission_list = {}  # 确保在出错时使用空字典

# 使用线程锁保护数据写入
DATA_LOCK = threading.Lock()


# --- 数据保存函数 ---
def save_data_sync():
    """同步保存数据到 data.json"""
    with DATA_LOCK:  # 获取锁以安全读取 submission_list
        try:
            data_copy = submission_list.copy()  # 在锁内复制
            logger.debug(f"准备同步保存 {len(data_copy)} 条记录...")
        except Exception as e:
            logger.error(f"复制 submission_list 时出错: {e}")
            return  # 复制失败则不继续

    # 文件写入操作可以在锁外进行，减少锁持有时间
    try:
        with open(DATA_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(data_copy, f, ensure_ascii=False, indent=4)
        logger.info(f"数据已同步保存到 {DATA_FILE_PATH}")
    except Exception as e:
        logger.error(f"同步保存数据到 {DATA_FILE_PATH} 时出错: {e}", exc_info=True)


async def save_data_async():
    """异步触发同步保存数据（在单独线程中执行）"""
    logger.debug("触发异步保存数据任务...")
    # 使用 threading.Thread 确保文件 I/O 不阻塞 asyncio 事件循环
    thread = threading.Thread(target=save_data_sync, daemon=True)
    thread.start()
    # 注意：如果需要在异步代码中等待保存完成（通常不需要），这会比较复杂。
    # asyncio.to_thread (Python 3.9+) 是更好的选择，但 threading 也能工作。


# --- 数据访问/修改函数 (修改：确保修改后触发保存) ---


def get_submission(key: str) -> dict | None:
    """获取指定 key 的投稿信息"""
    with DATA_LOCK:
        logger.debug(f"get_submission: 查询 Key: '{key}'")
        # --- 打印当前 keys ---
        current_keys = list(submission_list.keys())
        logger.debug(
            f"get_submission: 当前 keys ({len(current_keys)}个): {current_keys}"
        )
        # ---------------------
        result = submission_list.get(key)
        logger.debug(
            f"get_submission: 查询 Key '{key}' 结果: {'找到' if result else '未找到'}"
        )
        return result.copy() if result else None


def add_submission(key: str, data: dict):
    """添加或更新投稿信息，并触发异步保存"""
    should_save = False
    with DATA_LOCK:
        # 只有当数据实际发生变化时才标记需要保存（可选优化）
        if key not in submission_list or submission_list[key] != data:
            logger.info(f"add_submission: 添加/更新 Key: {key}")
            submission_list[key] = data.copy()  # 存储副本
            should_save = True
        else:
            logger.debug(f"add_submission: Key {key} 数据未变化，跳过更新。")

    # 在锁外触发保存（如果需要）
    if should_save:
        # 使用 asyncio.create_task 调度异步保存函数
        # 这需要在能够访问当前事件循环的地方调用（通常在异步函数内）
        # 如果这个函数可能在非异步上下文调用，则直接启动线程更安全
        # asyncio.create_task(save_data_async())
        threading.Thread(
            target=save_data_sync, daemon=True
        ).start()  # 直接启动线程更通用


def update_submission_status(key: str, posted: bool = True, status: str | None = None):
    """更新投稿状态，并触发异步保存"""
    should_save = False
    with DATA_LOCK:
        if key in submission_list:
            updated = False
            if submission_list[key].get("posted") != posted:
                submission_list[key]["posted"] = posted
                updated = True
            if status and submission_list[key].get("status") != status:
                submission_list[key]["status"] = status
                updated = True
            elif (
                status is None and "status" in submission_list[key] and posted
            ):  # 清理旧状态
                del submission_list[key]["status"]
                updated = True

            if updated:
                logger.info(
                    f"update_submission_status: 更新 Key: {key}, posted={posted}, status={status}"
                )
                should_save = True
            else:
                logger.debug(f"update_submission_status: Key {key} 状态未变化，跳过。")
        else:
            logger.warning(f"尝试更新不存在的投稿状态: {key}")

    if should_save:
        # asyncio.create_task(save_data_async())
        threading.Thread(target=save_data_sync, daemon=True).start()


def remove_submission(key: str) -> bool:
    """移除投稿信息，并触发异步保存"""
    removed = False
    with DATA_LOCK:
        if key in submission_list:
            logger.info(f"移除投稿记录: {key}")
            del submission_list[key]
            removed = True
        else:
            logger.warning(f"尝试移除不存在的投稿记录: {key}")

    if removed:
        # asyncio.create_task(save_data_async())
        threading.Thread(target=save_data_sync, daemon=True).start()
    return removed


def get_pending_submission_count() -> int:
    """获取待处理投稿数量"""
    with DATA_LOCK:
        count = sum(
            1 for sub in submission_list.values() if not sub.get("posted", False)
        )
        return count


logger.info(
    f"--- data_manager.py LOADED - submission_list ID: {id(submission_list)} ---"
)
