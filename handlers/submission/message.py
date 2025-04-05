# handlers/submission/message.py

"""包含 handle_private_message 函数，负责接收私聊消息，区分单条和媒体组，并调度 Job。"""

import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TelegramError
from telegram import (
    MessageOriginUser,
    MessageOriginChat,
    MessageOriginChannel,
)  # 导入 Origin 类型

from config_loader import get_blocked_users

# 导入 jobs 中的常量和函数
from .jobs import (
    MEDIA_GROUP_CONTEXT_KEY,
    MEDIA_GROUP_JOB_PREFIX,
    MEDIA_GROUP_DELAY,
    cleanup_media_group_job,
    process_media_group,
)

logger = logging.getLogger(__name__)


async def handle_private_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理用户私聊发来的消息（处理单条消息或收集媒体组）"""
    if not update.message or not update.message.from_user:
        return

    message = update.message
    user = update.message.from_user
    chat_id = message.chat_id

    if user.id in get_blocked_users():
        logger.info(f"用户 {user.id} 在黑名单中，已阻止其交互。")
        return

    # --- 处理媒体组 ---
    if message.media_group_id:
        media_group_id = message.media_group_id
        job_name = f"{MEDIA_GROUP_JOB_PREFIX}{chat_id}_{media_group_id}"
        media_group_data_key = f"group_{media_group_id}"

        if MEDIA_GROUP_CONTEXT_KEY not in context.chat_data:
            context.chat_data[MEDIA_GROUP_CONTEXT_KEY] = {}
        if media_group_data_key not in context.chat_data[MEDIA_GROUP_CONTEXT_KEY]:
            context.chat_data[MEDIA_GROUP_CONTEXT_KEY][media_group_data_key] = []

        context.chat_data[MEDIA_GROUP_CONTEXT_KEY][media_group_data_key].append(message)
        logger.debug(
            f"收到媒体组 {media_group_id} 的消息 {message.message_id}，已收集 {len(context.chat_data[MEDIA_GROUP_CONTEXT_KEY][media_group_data_key])} 条。"
        )

        cleanup_media_group_job(context, chat_id, media_group_id)
        if not context.job_queue:
            logger.error("无法安排媒体组处理任务：context.job_queue is None.")
            return
        context.job_queue.run_once(
            process_media_group,  # 使用导入的函数
            when=MEDIA_GROUP_DELAY,
            data={
                "chat_id": chat_id,
                "media_group_id": media_group_id,
                "user_id": user.id,
                "user_name": user.full_name,
            },
            name=job_name,
        )
        logger.debug(
            f"已安排/重置媒体组处理任务: {job_name} 在 {MEDIA_GROUP_DELAY} 后执行。"
        )
        return

    # --- 处理单条消息 (非媒体组) ---
    else:
        original_msg_id = message.message_id
        is_forward = message.forward_origin is not None
        must_be_real = False
        if is_forward:
            origin = message.forward_origin
            if isinstance(origin, (MessageOriginChat, MessageOriginChannel)) or (
                isinstance(origin, MessageOriginUser)
                and origin.sender_user.id != user.id
            ):
                must_be_real = True

        keyboard = []
        callback_prefix = f"sm:{original_msg_id}"
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
        prompt_text = "请选择投稿方式："
        if must_be_real:
            prompt_text += "\n(由于转发来源，只能选择保留来源)"

        try:
            await message.reply_text(text=prompt_text, reply_markup=markup)
            logger.info(
                f"向用户 {user.name} ({user.id}) 发送单条消息 {original_msg_id} 的投稿类型选择"
            )
        except TelegramError as e:
            logger.error(f"向用户 {user.id} 发送单条消息选项失败: {e}")
