# data_manager.py

"""负责加载和保存 data.json (存储投稿信息)"""

import json
import os
import logging
import threading
import asyncio  # 引入 asyncio 以便 create_task (虽然当前未使用)
from config_loader import PATH  # 从 config_loader 导入项目路径
from typing import Dict, Any, List

logger = logging.getLogger(__name__)

# 全局变量，用于在内存中存储投稿信息
# 字典结构: { "submission_key": { submission_data } }
# submission_key 通常是 "group_id:forwarded_message_id"
submission_list: Dict[str, Dict[str, Any]] = {}

# 数据文件路径
DATA_FILE_PATH = PATH + "data.json"
# 线程锁，用于保护对 submission_list 的并发访问
DATA_LOCK = threading.Lock()

# --- 数据加载 ---
try:
    logger.info(f"正在从 {DATA_FILE_PATH} 加载投稿数据...")
    with open(DATA_FILE_PATH, "r", encoding="utf-8") as f:
        submission_list = json.load(f)
    logger.info(f"已加载 {len(submission_list)} 条投稿记录.")
except FileNotFoundError:
    # 如果数据文件不存在，初始化为空字典，并在首次保存时创建文件
    logger.warning(f"未找到数据文件 {DATA_FILE_PATH}，将创建一个新的空数据文件。")
    submission_list = {}
except json.JSONDecodeError:
    # 如果文件内容无效，初始化为空字典，避免程序崩溃
    logger.warning(f"数据文件 {DATA_FILE_PATH} 格式无效或为空，将使用空数据。")
    submission_list = {}
except Exception as e:
    # 捕获其他可能的加载错误
    logger.error(f"加载数据时发生未知错误: {e}")
    submission_list = {}  # 确保在出错时使用空字典

# 使用线程锁保护数据写入
DATA_LOCK = threading.Lock()


# --- 数据保存函数 ---
def save_data_sync():
    """同步保存当前内存中的 submission_list 到 data.json 文件"""
    with DATA_LOCK:  # 获取锁以确保线程安全地读取 submission_list
        try:
            # 在持有锁的情况下复制字典，以减少锁的持有时间
            data_copy = submission_list.copy()
            logger.debug(f"准备同步保存 {len(data_copy)} 条记录...")
        except Exception as e:
            # 理论上复制内置字典不太可能出错，但以防万一
            logger.error(f"复制 submission_list 时出错: {e}")
            return  # 复制失败则不继续执行保存

    # 文件写入操作在锁之外进行，允许其他线程在写入时访问 submission_list
    try:
        with open(DATA_FILE_PATH, "w", encoding="utf-8") as f:
            json.dump(data_copy, f, ensure_ascii=False, indent=4)
        logger.info(f"数据已同步保存到 {DATA_FILE_PATH}")
    except Exception as e:
        logger.error(f"同步保存数据到 {DATA_FILE_PATH} 时出错: {e}", exc_info=True)


async def save_data_async():
    """异步触发同步保存数据的操作（在单独线程中执行）"""
    logger.debug("触发异步保存数据任务...")
    # 使用 threading.Thread 确保文件 I/O 不阻塞 asyncio 事件循环
    # daemon=True 使得主线程退出时该线程也会退出
    thread = threading.Thread(target=save_data_sync, daemon=True)
    thread.start()
    # 注意：如果需要在异步代码中等待保存完成（通常不需要），这会比较复杂。
    # asyncio.to_thread (Python 3.9+) 是更现代、推荐的方式，但 threading 也能工作。


# --- 数据访问/修改函数 (修改：确保修改后触发保存) ---


def get_submission(key: str) -> dict | None:
    """根据 key 获取指定的投稿信息字典"""
    with DATA_LOCK:
        logger.debug(f"get_submission: 查询 Key: '{key}'")
        # --- 调试日志：打印当前所有 keys --- #
        # current_keys = list(submission_list.keys())
        # logger.debug(
        #     f"get_submission: 当前 keys ({len(current_keys)}个): {current_keys}"
        # )
        # ------------------------------------ #
        result = submission_list.get(key)
        logger.debug(
            f"get_submission: 查询 Key '{key}' 结果: {'找到' if result else '未找到'}"
        )
        # 返回字典的副本，避免外部代码直接修改内存中的原始数据
        return result.copy() if result else None


def add_submission(key: str, data: dict):
    """添加或更新指定 key 的投稿信息，并触发异步保存"""
    should_save = False
    with DATA_LOCK:
        # 仅当数据实际发生变化时才标记需要保存（可选优化）
        if key not in submission_list or submission_list[key] != data:
            logger.info(f"add_submission: 添加/更新 Key: {key}")
            # 存储传入数据的副本
            submission_list[key] = data.copy()
            should_save = True
        else:
            # 如果 key 存在且数据完全相同，则不进行任何操作
            logger.debug(f"add_submission: Key {key} 数据未变化，跳过更新。")

    # 在锁外触发保存（如果 should_save 为 True）
    if should_save:
        # 使用 asyncio.create_task 调度异步保存函数 (如果确定在异步环境)
        # asyncio.create_task(save_data_async())

        # 使用 threading.Thread 直接启动线程，更通用，适用于任何上下文
        threading.Thread(target=save_data_sync, daemon=True).start()


def update_submission_status(key: str, posted: bool = True, status: str | None = None):
    """更新指定 key 投稿的发布状态 ('posted') 和可选的 'status' 字符串，并触发异步保存"""
    should_save = False
    with DATA_LOCK:
        if key in submission_list:
            updated = False  # 标记是否有实际更新
            # 更新 posted 状态
            if submission_list[key].get("posted") != posted:
                submission_list[key]["posted"] = posted
                updated = True
            # 更新 status 字符串
            if status and submission_list[key].get("status") != status:
                submission_list[key]["status"] = status
                updated = True
            # 如果 posted=True 且 status 为 None，则移除旧的 status 字段 (清理)
            elif status is None and "status" in submission_list[key] and posted:
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
            # 尝试更新一个不存在的 key
            logger.warning(f"尝试更新不存在的投稿状态: {key}")

    if should_save:
        # 触发异步保存
        # asyncio.create_task(save_data_async())
        threading.Thread(target=save_data_sync, daemon=True).start()


def remove_submission(key: str) -> bool:
    """根据 key 移除投稿信息，并触发异步保存"""
    removed = False
    with DATA_LOCK:
        if key in submission_list:
            logger.info(f"移除投稿记录: {key}")
            del submission_list[key]
            removed = True  # 标记成功移除
        else:
            logger.warning(f"尝试移除不存在的投稿记录: {key}")

    if removed:
        # 触发异步保存
        # asyncio.create_task(save_data_async())
        threading.Thread(target=save_data_sync, daemon=True).start()
    return removed  # 返回是否成功移除


def get_pending_submission_count() -> int:
    """获取当前内存中待处理 (posted=False) 的投稿数量"""
    with DATA_LOCK:
        count = sum(
            1 for sub in submission_list.values() if not sub.get("posted", False)
        )
        return count


# 模块加载完成时打印日志，显示 submission_list 对象的内存 ID (用于调试)
logger.info(
    f"--- data_manager.py LOADED - submission_list ID: {id(submission_list)} ---"
)
