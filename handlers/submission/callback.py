# handlers/submission/callback.py

"""包含 handle_submission_callback 函数，只处理来自私聊按钮的回调。"""

import logging
from telegram import (  # <--- 修改导入
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    InputMediaVideo,
    MessageOriginUser,
    MessageOriginHiddenUser,
    MessageOriginChat,
    MessageOriginChannel,
)
from telegram.constants import ParseMode
from telegram.ext import ContextTypes
from telegram.error import TelegramError

from config_loader import get_group_id, get_blocked_users
import data_manager
from data_manager import (
    add_submission,
    save_data_async,
    get_submission,
)  # 导入 get_submission

logger = logging.getLogger(__name__)


async def handle_submission_callback(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """处理用户私聊中的按钮回调（包括单条消息和媒体组）"""
    query = update.callback_query
    if not query or not query.data or not query.message:
        return
    await query.answer()
    user = query.from_user
    message = query.message  # 带按钮的消息
    button_msg_id = message.message_id

    if user.id in get_blocked_users():
        try:
            await query.edit_message_text("❌ 您已被限制使用此机器人。")
        except TelegramError:
            pass
        return

    parts = query.data.split(":")
    if len(parts) < 3:
        logger.warning(f"收到格式错误的回调数据: {query.data}")
        return
    action_type = parts[0]
    prefix = parts[1]
    identifier = parts[2]
    # mg 格式: <type>:mg:<media_group_id>:<first_msg_id>
    first_msg_id_check = int(parts[3]) if prefix == "mg" and len(parts) > 3 else None

    if action_type == "cancel":
        original_id_info = identifier if prefix == "sm" else f"组 {identifier}"
        try:
            await query.edit_message_text(text="🗑️ 投稿已取消。")
            logger.info(
                f"用户 {user.name} ({user.id}) 取消了投稿 (标识: {original_id_info})"
            )
            if prefix == "mg":
                context.chat_data.pop(f"pending_group_{button_msg_id}", None)
        except TelegramError as e:
            logger.error(f"编辑取消消息失败: {e}")
        return

    current_group_id = get_group_id()
    if not current_group_id:
        try:
            await query.edit_message_text("❌ 抱歉，投稿功能暂时无法使用。")
        except TelegramError:
            pass
        return

    submission_type = None
    source_desc = ""
    if action_type == "real":
        submission_type, source_desc = "real", "保留来源"
    elif action_type == "anon":
        submission_type, source_desc = "anonymous", "匿名"
    else:
        logger.warning(f"未知的操作类型: {action_type}")
        return

    try:
        await query.edit_message_text(f"⏳ 正在处理您的 {submission_type} 投稿...")
    except TelegramError:
        pass

    forwarded_message_list = []
    first_original_msg_id = None
    submission_data_for_storage = None
    is_media_group = prefix == "mg"
    parsed_forward_origin = None
    media_list_info = None
    # ------------------------------------

    if prefix == "sm":
        # 处理单条消息的回调
        try:
            original_msg_id = int(identifier)
            first_original_msg_id = original_msg_id
            fwd_msg = await context.bot.forward_message(
                chat_id=current_group_id,
                from_chat_id=user.id,
                message_id=original_msg_id,
            )
            forwarded_message_list.append(fwd_msg)
            if fwd_msg.forward_origin:
                origin = fwd_msg.forward_origin
                origin_info = {"type": type(origin).__name__}
                if isinstance(origin, MessageOriginUser):
                    origin_info.update(
                        {
                            "sender_user_id": origin.sender_user.id,
                            "sender_user_name": origin.sender_user.full_name,
                        }
                    )
                elif isinstance(origin, MessageOriginHiddenUser):
                    origin_info["sender_user_name"] = origin.sender_user_name
                elif isinstance(origin, MessageOriginChat):
                    origin_info.update(
                        {
                            "sender_chat_id": origin.sender_chat.id,
                            "sender_chat_title": origin.sender_chat.title,
                            "sender_chat_username": origin.sender_chat.username,
                        }
                    )
                elif isinstance(origin, MessageOriginChannel):
                    origin_info.update(
                        {
                            "chat_id": origin.chat.id,
                            "chat_title": origin.chat.title,
                            "chat_username": origin.chat.username,
                            "message_id": origin.message_id,
                        }
                    )
                parsed_forward_origin = origin_info
            # ------------------------------------
        except (ValueError, TelegramError) as e:
            # 处理转发单条消息时发生的错误
            logger.error(f"单条消息回调转发 {identifier} 失败: {e}")
            try:
                await query.edit_message_text(
                    f"❌ 处理失败：无法转发原始消息 {identifier}。"
                )
            except TelegramError:
                pass
            return
    elif prefix == "mg":
        # 处理媒体组的回调
        media_group_id = identifier
        pending_group_key = f"pending_group_{button_msg_id}"
        # 确保 chat_data 存在
        if context.chat_data is None:
            logger.error(f"处理媒体组回调时 chat_data 为空 (用户ID: {user.id})")
            try:
                await query.edit_message_text("❌ 处理失败：内部数据错误。")
            except TelegramError:
                pass
            return
        group_info = context.chat_data.pop(pending_group_key, None)

        if group_info is None or group_info["media_group_id"] != media_group_id:
            logger.warning(
                f"找不到按钮 {button_msg_id} 对应的媒体组 {media_group_id} 信息。"
            )
            try:
                await query.edit_message_text("⏳ 此媒体组操作已过时或数据丢失。")
            except TelegramError:
                pass
            return

        # --- 获取媒体列表和来源信息 ---
        media_list_info = group_info.get("messages", [])  # <--- 获取 media_list_info
        parsed_forward_origin = group_info.get(
            "first_message_forward_origin"
        )  # <--- 获取来源
        # -----------------------------

        if not media_list_info:
            logger.error(f"媒体组 {media_group_id} 无有效媒体信息。")
            try:
                await query.edit_message_text("❌ 处理失败：媒体组数据错误。")
            except TelegramError:
                pass
            return

        first_original_msg_id = media_list_info[0]["message_id"]
        media_to_send = []
        caption_added = False
        for i, item in enumerate(media_list_info):
            # --- 跳过不支持的类型 ---
            media_type = item.get("type")
            if media_type == "unsupported":
                logger.debug(
                    f"跳过媒体组中不支持的条目: message_id={item.get('message_id')}"
                )
                continue
            caption = item.get("caption")
            caption_html_stored = item.get("caption_html")
            parse_mode = (
                ParseMode.HTML if caption_html_stored and i == 0 else None
            )  # 只有第一个需要解析模式
            current_caption_to_use = (
                caption_html_stored if i == 0 else None
            )  # 使用 HTML 版本
            has_spoiler = item.get("has_spoiler", False)
            file_id = item.get("file_id")
            if not file_id:
                continue

            if media_type == "photo":
                media_to_send.append(
                    InputMediaPhoto(
                        media=file_id,
                        caption=current_caption_to_use,
                        parse_mode=parse_mode,
                        has_spoiler=has_spoiler,
                    )
                )
                if current_caption_to_use:
                    caption_added = True  # 标记 caption 已用（虽然只用一次）
            elif media_type == "video":
                media_to_send.append(
                    InputMediaVideo(
                        media=file_id,
                        caption=current_caption_to_use,
                        parse_mode=parse_mode,
                        has_spoiler=has_spoiler,
                    )
                )
                if current_caption_to_use:
                    caption_added = True

        if not media_to_send:
            logger.error(f"媒体组 {media_group_id} 没有可以转发的有效媒体。")
            try:
                await query.edit_message_text("❌ 处理失败：媒体组内容无效。")
            except TelegramError:
                pass
            return

        try:
            # --- 将媒体组发送到审核群 ---
            sent_media_group_messages = await context.bot.send_media_group(
                chat_id=current_group_id, media=media_to_send
            )
            forwarded_message_list.extend(sent_media_group_messages)
            logger.info(
                f"已将媒体组 {media_group_id} ({len(sent_media_group_messages)}条) 发送到审核群 {current_group_id}"
            )
        except TelegramError as e:
            logger.error(f"send_media_group 转发媒体组 {media_group_id} 失败: {e}")
            try:
                await query.edit_message_text(f"❌ 转发媒体组失败: {e}")
            except TelegramError:
                pass
            return
    else:
        logger.error(f"未知的回调前缀: {prefix}")
        return

    if not forwarded_message_list:
        logger.error("处理回调后未能生成转发消息。")
        try:
            await query.edit_message_text("❌ 处理失败（转发错误）。")
        except TelegramError:
            pass
        return

    first_fwd_msg = forwarded_message_list[0]
    submission_key = f"{current_group_id}:{first_fwd_msg.message_id}"
    # --- 构建存储数据，包含解析后的来源和媒体列表 ---
    submission_data_for_storage = {
        "posted": False,
        "type": submission_type,
        "Sender_Name": user.full_name,
        "Sender_ID": user.id,
        "Original_MsgID": first_original_msg_id,
        "Markup_ID": None,
        "is_media_group": is_media_group,
        "media_group_fwd_ids": (
            [msg.message_id for msg in forwarded_message_list]
            if is_media_group
            else None
        ),
        "parsed_forward_origin": parsed_forward_origin,
        "messages": (
            media_list_info if is_media_group else None
        ),  # <-- 添加 messages 列表 (仅媒体组)
    }
    # ---------------------------------------------

    # ... (发送审核群状态消息) ...
    sender_link = f'<a href="tg://user?id={user.id}">{user.first_name}</a>'
    media_group_notice = " (媒体组)" if is_media_group else ""
    text = (
        f"📩 <b>新投稿</b>{media_group_notice}\n\n"
        + f"<b>投稿人:</b> {sender_link}\n"
        + f"<b>选择方式:</b> {source_desc}\n\n"
        + f"更多帮助: /pwshelp"
    )
    keyboard = []
    if submission_type == "real":
        keyboard.append(
            [InlineKeyboardButton("✅ 采用 (保留来源)", callback_data="receive:real")]
        )
    elif submission_type == "anonymous":
        keyboard.append(
            [InlineKeyboardButton("✅ 采用 (匿名)", callback_data="receive:anonymous")]
        )
    keyboard.append(
        [InlineKeyboardButton("❌ 拒绝", callback_data="reject:submission")]
    )
    markup = InlineKeyboardMarkup(keyboard)

    try:
        markup_msg = await context.bot.send_message(
            chat_id=current_group_id,
            text=text,
            reply_to_message_id=first_fwd_msg.message_id,
            reply_markup=markup,
            parse_mode=ParseMode.HTML,
            read_timeout=30,  # 增加读取超时时间
            write_timeout=30,  # 增加写入超时时间
            connect_timeout=30,  # 增加连接超时时间
            pool_timeout=30,  # 增加池超时时间
        )
        submission_data_for_storage["Markup_ID"] = markup_msg.message_id
        logger.info(
            f"已在审稿群 {current_group_id} 发送投稿 {submission_key} 的处理选项"
        )

        add_submission(submission_key, submission_data_for_storage)  # 保存完整数据
        # --- 添加日志：打印 ID ---
        logger.info(
            f"--- handle_submission_callback - AFTER add - submission_list ID: {id(data_manager.submission_list)} ---"
        )
        # ---------------------------
        # --- 添加日志：尝试立即读取 ---
        retrieved_after_add = get_submission(submission_key)
        logger.info(
            f"保存后立即读取 Key {submission_key}: {'找到了' if retrieved_after_add else '未找到'}"
        )
        # ---------------------------
        await save_data_async()

        try:
            await query.edit_message_text(text="✅ 感谢您的投稿！稿件已成功提交审核。")
        except TelegramError as e:
            logger.error(f"编辑用户确认消息失败: {e}")

    except TelegramError as e:
        logger.error(
            f"在审稿群 {current_group_id} 发送处理选项失败 for {submission_key}: {e}"
        )

        # 即使发送处理选项失败，也保存投稿信息
        submission_data_for_storage["Markup_ID"] = None  # 标记为没有处理按钮消息
        submission_data_for_storage["pending_markup"] = True  # 标记为待处理

        add_submission(submission_key, submission_data_for_storage)  # 保存投稿信息
        logger.info(f"尽管发送处理选项失败，仍然保存了投稿 {submission_key} 的信息")
        await save_data_async()

        try:
            await query.edit_message_text(
                f"⚠️ 发送稿件至审稿群时触发了杜蛆对机器人API的速率限制: {e}\n但投稿已成功转发到审核群，管理员稍后将处理您的投稿。"
            )
        except TelegramError:
            pass
