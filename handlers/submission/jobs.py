# handlers/submission/jobs.py

"""将媒体组处理的常量、辅助函数 cleanup_media_group_job 和 process_media_group 移动到这里。"""

import logging
from datetime import timedelta
from telegram import (  # <--- 修改导入
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    MessageOriginUser,
    MessageOriginHiddenUser,
    MessageOriginChat,
    MessageOriginChannel,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from telegram import (
    MessageOriginUser,
    MessageOriginChat,
    MessageOriginChannel,
)  # 导入 Origin 类型

# 导入必要的外部函数/变量
# (注意：jobs.py 现在可能需要导入 config_loader 和 data_manager 的东西，
#  或者将这些依赖通过 job.data 传递)
# from config_loader import get_blocked_users # 可能不需要直接访问
# from data_manager import ...

logger = logging.getLogger(__name__)

MEDIA_GROUP_CONTEXT_KEY = "pending_media_groups"
MEDIA_GROUP_JOB_PREFIX = "process_media_group_"
MEDIA_GROUP_DELAY = timedelta(seconds=1.5)


def cleanup_media_group_job(
    context: ContextTypes.DEFAULT_TYPE, chat_id: int, media_group_id: str
):
    """移除与特定媒体组关联的延迟任务"""
    job_name = f"{MEDIA_GROUP_JOB_PREFIX}{chat_id}_{media_group_id}"
    if not context.job_queue:
        logger.warning("cleanup_media_group_job: context.job_queue is None.")
        return
    current_jobs = context.job_queue.get_jobs_by_name(job_name)
    if current_jobs:
        logger.debug(f"移除旧的媒体组处理任务: {job_name} ({len(current_jobs)} 个)")
        for job in current_jobs:
            job.schedule_removal()


async def process_media_group(context: ContextTypes.DEFAULT_TYPE):
    """JobQueue 触发的任务，用于处理收集完成的媒体组"""
    job = context.job
    if (
        not job
        or not job.data
        or "chat_id" not in job.data
        or "media_group_id" not in job.data
    ):
        logger.error("process_media_group 任务缺少必要数据！")
        return

    chat_id = job.data["chat_id"]
    media_group_id = job.data["media_group_id"]
    user_id = job.data.get("user_id")
    user_name = job.data.get("user_name", "未知用户")

    logger.info(
        f"处理媒体组任务触发: chat_id={chat_id}, media_group_id={media_group_id}"
    )

    if not context.application:
        logger.error("Job context 缺少 application")
        return
    chat_data = context.application.chat_data.get(chat_id)
    if chat_data is None:
        chat_data = context.application.chat_data.setdefault(chat_id, {})

    media_group_data_key = f"group_{media_group_id}"
    pending_groups_in_chat = chat_data.get(MEDIA_GROUP_CONTEXT_KEY, {})
    collected_messages: list[Message] = pending_groups_in_chat.pop(
        media_group_data_key, []
    )  # 指定类型

    if not pending_groups_in_chat:
        chat_data.pop(MEDIA_GROUP_CONTEXT_KEY, None)
    if not collected_messages:
        logger.warning(f"处理媒体组 {media_group_id} 时消息列表为空。")
        return

    first_message = collected_messages[0]
    is_forward = first_message.forward_origin is not None
    must_be_real = False
    if is_forward and user_id:
        origin = first_message.forward_origin
        if isinstance(origin, (MessageOriginChat, MessageOriginChannel)) or (
            isinstance(origin, MessageOriginUser) and origin.sender_user.id != user_id
        ):
            must_be_real = True

    first_msg_id = first_message.message_id
    keyboard = []
    callback_prefix = f"mg:{media_group_id}:{first_msg_id}"
    if must_be_real:
        keyboard = [
            [
                InlineKeyboardButton(
                    "保留来源 (实名)", callback_data=f"real:{callback_prefix}"
                )
            ],
            [
                InlineKeyboardButton(
                    "取消投稿", callback_data=f"cancel:{callback_prefix}"
                )
            ],
        ]
    else:
        keyboard = [
            [
                InlineKeyboardButton(
                    "保留来源 (实名)", callback_data=f"real:{callback_prefix}"
                ),
                InlineKeyboardButton(
                    "匿名发送", callback_data=f"anon:{callback_prefix}"
                ),
            ],
            [
                InlineKeyboardButton(
                    "取消投稿", callback_data=f"cancel:{callback_prefix}"
                )
            ],
        ]

    markup = InlineKeyboardMarkup(keyboard)
    prompt_text = "检测到您发送了一组图片/视频，请选择投稿方式："
    if must_be_real:
        prompt_text += "\n(由于转发来源，只能选择保留来源)"

    try:
        sent_button_message = await first_message.reply_text(
            text=prompt_text, reply_markup=markup
        )
        logger.info(
            f"向用户 {user_name} ({user_id}) 发送媒体组 {media_group_id} 的投稿类型选择"
        )
        media_group_info_to_store = {
            "media_group_id": media_group_id,
            "messages": [],
            "is_forward": is_forward,
            # --- 新增：存储第一条消息的转发来源信息 ---
            "first_message_forward_origin": None,  # 默认为 None
        }
        if is_forward and first_message.forward_origin:
            # 存储解析后的信息，避免直接存复杂对象
            origin = first_message.forward_origin
            origin_info = {"type": type(origin).__name__}
            if isinstance(origin, MessageOriginUser):
                origin_info["sender_user_id"] = origin.sender_user.id
                origin_info["sender_user_name"] = origin.sender_user.full_name
            elif isinstance(origin, MessageOriginHiddenUser):
                origin_info["sender_user_name"] = origin.sender_user_name
            elif isinstance(origin, MessageOriginChat):
                origin_info["sender_chat_id"] = origin.sender_chat.id
                origin_info["sender_chat_title"] = origin.sender_chat.title
                origin_info["sender_chat_username"] = origin.sender_chat.username
            elif isinstance(origin, MessageOriginChannel):
                origin_info["chat_id"] = origin.chat.id
                origin_info["chat_title"] = origin.chat.title
                origin_info["chat_username"] = origin.chat.username
                origin_info["message_id"] = origin.message_id
            media_group_info_to_store["first_message_forward_origin"] = origin_info
        # ------------------------------------------

        for msg in collected_messages:
            media_info = {"message_id": msg.message_id}
            caption = msg.caption
            caption_entities = msg.caption_entities
            has_spoiler = msg.has_media_spoiler
            if msg.photo:
                media_info.update(
                    {
                        "type": "photo",
                        "file_id": msg.photo[-1].file_id,
                        "caption": caption,
                        "caption_entities": caption_entities,
                        "has_spoiler": has_spoiler,
                    }
                )
            elif msg.video:
                media_info.update(
                    {
                        "type": "video",
                        "file_id": msg.video.file_id,
                        "caption": caption,
                        "caption_entities": caption_entities,
                        "has_spoiler": has_spoiler,
                    }
                )
            else:
                logger.warning(f"媒体组 {media_group_id} 中包含不支持聚合的消息类型")
                continue
            media_group_info_to_store["messages"].append(media_info)

        if media_group_info_to_store["messages"]:
            chat_data[f"pending_group_{sent_button_message.message_id}"] = (
                media_group_info_to_store
            )
            logger.debug(
                f"Chat data for {user_id} updated with pending group info (button ID: {sent_button_message.message_id})"
            )
        else:
            logger.warning(f"媒体组 {media_group_id} 处理后没有有效的媒体信息可存储。")

    except TelegramError as e:
        logger.error(f"向用户 {user_id} 发送媒体组选项失败: {e}")
    except Exception as e:
        logger.error(f"处理媒体组 {media_group_id} 时发生未知错误: {e}", exc_info=True)
