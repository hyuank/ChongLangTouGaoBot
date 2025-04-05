# posting.py

"""包含实际执行发布操作和与投稿人交互的函数"""

import logging
import html
from typing import List, Optional, Dict, Any  # 引入所需类型
from urllib.parse import quote
from telegram import Message, User, InputMediaPhoto, InputMediaVideo
from telegram.constants import ParseMode
from telegram import (
    MessageOriginUser,
    MessageOriginHiddenUser,
    MessageOriginChat,
    MessageOriginChannel,
)
from telegram.ext import ContextTypes
from telegram.error import TelegramError, Forbidden, BadRequest

# 从其他模块导入
from config_loader import get_publish_channel_id, get_group_id
from data_manager import update_submission_status, save_data_async, add_submission

logger = logging.getLogger(__name__)


# --- 统一的发布函数 ---
async def post_submission(
    context: ContextTypes.DEFAULT_TYPE,
    msg_to_post: Message,  # 审稿群消息 (第一条，用于 key 和 editor)
    editor: User,
    submission_info: dict,  # 包含所有需要的信息!
    comment: str | None = None,
) -> Optional[List[Message] | Message]:
    """
    发布投稿到频道（复制内容方式），能正确处理单条或媒体组，并附加评论和来源。
    依赖传入的 submission_info。
    """
    bot = context.bot
    channel_id_or_username = get_publish_channel_id()
    group_id = get_group_id()
    first_fwd_msg_id = msg_to_post.message_id
    submission_key = f"{group_id}:{first_fwd_msg_id}" if group_id else None

    # 从 submission_info 获取关键信息
    submission_type = submission_info.get("type", "anonymous")
    is_real_name_request = submission_type == "real"
    is_media_group = submission_info.get("is_media_group", False)
    submitter_id_val = submission_info.get("Sender_ID")
    submitter_name = submission_info.get("Sender_Name", "匿名")
    parsed_origin = submission_info.get("parsed_forward_origin")
    media_list_info = submission_info.get("messages")  # 媒体组信息

    if not channel_id_or_username or not group_id or not submission_key:
        logger.error("发布失败：缺少频道/群组 ID 或 Key")
        if group_id:
            try:
                await bot.send_message(group_id, "❌ 发布失败！频道或群组配置不完整。")
            except TelegramError as e:
                logger.warning(f"发送配置错误通知失败: {e}")
        return None
    try:
        submitter_id_int = int(submitter_id_val) if submitter_id_val else None
    except (ValueError, TypeError):
        submitter_id_int = None

    posted_message_or_list = None  # 初始化返回值

    try:
        # --- 1. 构造附加信息：评论和来源 ---
        final_extra_content_parts = []
        editor_comment_text_for_review = ""
        if comment:
            escaped_comment = html.escape(comment)
            comment_part = f"\n\n<b>小编 ({html.escape(editor.first_name)}):</b>\n{escaped_comment}"
            final_extra_content_parts.append(comment_part)
            editor_comment_text_for_review = f"\n<b>评论:</b> {escaped_comment}"

        via_link_part = ""
        source_info_for_review = "\n<b>来源:</b> 匿名"  # 默认是匿名
        via_prefix = "\n\nvia "

        if is_real_name_request:  # 用户请求了保留来源
            source_representation = None  # 用于显示在审稿群
            logger.debug(
                f"处理实名请求: parsed_origin 类型 = {type(parsed_origin)}, 内容 = {parsed_origin}"
            )

            if parsed_origin and isinstance(
                parsed_origin, dict
            ):  # 情况1：原始消息是转发
                origin_type = parsed_origin.get("type")
                origin_display_name = "未知来源"
                origin_link = None  # 初始化为 None
                logger.debug(f"解析转发来源，类型: {origin_type}")

                if origin_type == "MessageOriginUser":
                    uid = parsed_origin.get("sender_user_id")
                    uname = parsed_origin.get("sender_user_name", "用户")
                    escaped_name = html.escape(uname)
                    if uid:
                        origin_display_name = escaped_name
                        origin_link = f'<a href="tg://user?id={uid}">{escaped_name}</a>'
                    else:  # 可能是隐藏用户或获取 ID 失败
                        origin_display_name = escaped_name
                        origin_link = escaped_name  # 无法链接
                    source_representation = origin_link
                elif origin_type == "MessageOriginHiddenUser":
                    uname = parsed_origin.get("sender_user_name", "隐藏用户")
                    escaped_name = html.escape(uname)
                    uid = parsed_origin.get("sender_user_id")
                    origin_display_name = (
                        f'<a href="tg://user?id={submitter_id_int}">{escaped_name}</a>'
                    )
                    origin_link = origin_display_name  # 赋值非 None
                    source_representation = origin_link
                elif origin_type == "MessageOriginChat":
                    title = parsed_origin.get("sender_chat_title", "群组")
                    uname = parsed_origin.get("sender_chat_username")
                    escaped_title = html.escape(title)
                    origin_display_name = f"群组: {escaped_title}"
                    origin_link = origin_display_name
                    if uname:
                        origin_link = (
                            f'群组: <a href="https://t.me/{uname}">{escaped_title}</a>'
                        )
                    source_representation = origin_link
                elif origin_type == "MessageOriginChannel":
                    title = parsed_origin.get("chat_title", "频道")
                    uname = parsed_origin.get("chat_username")
                    chat_id = parsed_origin.get("chat_id")
                    msg_id = parsed_origin.get("message_id")
                    escaped_title = html.escape(title)
                    link = None
                    if uname and msg_id:
                        link = f"https://t.me/{uname}/{msg_id}"
                    elif chat_id and str(chat_id).startswith("-100") and msg_id:
                        numeric_id = str(chat_id)[4:]
                        link = f"https://t.me/c/{numeric_id}/{msg_id}"
                    origin_display_name = f"频道: {escaped_title}"
                    if link:
                        origin_link = f'频道: <a href="{link}">{escaped_title}</a>'
                    else:
                        origin_link = origin_display_name
                    source_representation = origin_link
                else:
                    logger.warning(f"未知的 parsed_origin type: '{origin_type}'")
                    origin_link = None  # 确保未知类型时 link 为 None

                if origin_link:
                    via_link_part = via_prefix + origin_link
                    source_info_for_review = (
                        f"\n<b>来源:</b> 保留 (原始: {source_representation})"
                    )
                else:
                    source_info_for_review = "\n<b>来源:</b> 保留 (无法解析原始来源)"
                    via_link_part = ""
                logger.debug(
                    f"转发来源处理结果: source_info='{source_info_for_review}', via_part='{via_link_part}'"
                )

            else:  # 情况2：原始消息不是转发，来源是提交者本人
                logger.debug(
                    f"处理提交者来源: submitter_id_int={submitter_id_int}, submitter_name='{submitter_name}'"
                )
                if submitter_id_int:
                    escaped_submitter_name = html.escape(submitter_name)
                    source_representation = f'<a href="tg://user?id={submitter_id_int}">{escaped_submitter_name}</a>'
                    via_link_part = via_prefix + source_representation  # 构建 via link
                    source_info_for_review = (
                        f"\n<b>来源:</b> 保留 (提交者: {source_representation})"
                    )
                    logger.debug(
                        f"提交者来源处理结果: source_info='{source_info_for_review}', via_part='{via_link_part}'"
                    )
                else:  # 无法获取提交者 ID
                    source_info_for_review = "\n<b>来源:</b> 匿名 (无法获取提交者ID)"
                    logger.debug("提交者来源处理结果: 无法获取提交者ID")
                    via_link_part = ""  # 确保 via_link_part 在此情况下为空

            if via_link_part:
                final_extra_content_parts.append(via_link_part)
                logger.debug(
                    "已将非空的 via_link_part 添加到 final_extra_content_parts"
                )
            else:
                logger.debug("via_link_part 为空，未添加")

        # else: # 用户请求匿名
        #     logger.debug("用户请求匿名，跳过来源处理")

        final_extra_content = "".join(final_extra_content_parts)
        logger.debug(f"最终附加内容 final_extra_content: '{final_extra_content}'")
        logger.debug(
            f"最终审稿群来源信息 source_info_for_review: '{source_info_for_review}'"
        )

        # --- 2. 发送逻辑：区分单条和媒体组 ---
        if is_media_group:
            if not media_list_info or not isinstance(
                media_list_info, list
            ):  # 增加类型检查
                logger.error(
                    f"尝试发布媒体组 {submission_key} 但缺少有效的媒体信息列表 (submission_info['messages'])。"
                )
                return None

            media_to_send = []
            # --- 获取第一个媒体的原始 caption ---
            first_item_info = media_list_info[0] if media_list_info else {}
            original_caption_media = first_item_info.get("caption")
            final_caption_for_first = (
                original_caption_media or ""
            ) + final_extra_content
            final_parse_mode_for_first = (
                ParseMode.HTML if final_caption_for_first else None
            )
            if final_caption_for_first and final_caption_for_first.strip() == "":
                final_caption_for_first, final_parse_mode_for_first = None, None
            # ------------------------------------

            for i, item in enumerate(media_list_info):
                current_caption = final_caption_for_first if i == 0 else None
                current_parse_mode = final_parse_mode_for_first if i == 0 else None
                has_spoiler = item.get("has_spoiler", False)
                file_id = item.get("file_id")
                media_type = item.get("type")

                if not file_id:
                    logger.warning(f"媒体组 {submission_key} 中的项目 {i} 缺少 file_id")
                    continue

                if media_type == "photo":
                    media_to_send.append(
                        InputMediaPhoto(
                            media=file_id,
                            caption=current_caption,
                            parse_mode=current_parse_mode,
                            has_spoiler=has_spoiler,
                        )
                    )
                elif media_type == "video":
                    media_to_send.append(
                        InputMediaVideo(
                            media=file_id,
                            caption=current_caption,
                            parse_mode=current_parse_mode,
                            has_spoiler=has_spoiler,
                        )
                    )
                # Add other types if needed (Audio, Document) - check if they support has_spoiler
                else:
                    logger.warning(
                        f"媒体组 {submission_key} 中包含暂不支持发布的类型: {media_type}"
                    )

            if not media_to_send:
                logger.error(f"为媒体组 {submission_key} 构建的 InputMedia 列表为空。")
                return None

            try:
                posted_message_or_list = await bot.send_media_group(
                    chat_id=channel_id_or_username, media=media_to_send
                )
                logger.info(
                    f"媒体组 {submission_key} 已通过 send_media_group 发布到频道。"
                )
            except TelegramError as e:
                logger.error(
                    f"使用 send_media_group 发布媒体组 {submission_key} 失败: {e}"
                )
                # 可以考虑返回错误信息给审稿群
                if group_id:
                    try:
                        await bot.send_message(
                            group_id, f"❌ 发布媒体组 {first_fwd_msg_id} 失败: {e}"
                        )
                    except Exception:
                        pass
                return None

        else:  # 处理单条消息
            original_content = None
            is_text_message = False
            if msg_to_post.text:
                original_content, is_text_message = msg_to_post.text_html, True
            elif msg_to_post.caption:
                original_content = msg_to_post.caption_html
            final_content_single = (original_content or "") + final_extra_content
            final_parse_mode_single = ParseMode.HTML if final_content_single else None
            if final_content_single and final_content_single.strip() == "":
                final_content_single, final_parse_mode_single = None, None
            original_has_spoiler = msg_to_post.has_media_spoiler

            send_method_map = {
                "text": bot.send_message,
                "photo": bot.send_photo,
                "video": bot.send_video,
                "animation": bot.send_animation,
                "audio": bot.send_audio,
                "document": bot.send_document,
                "voice": bot.send_voice,
                "sticker": bot.send_sticker,
            }
            send_method = None
            send_args = {"chat_id": channel_id_or_username}

            if is_text_message:
                send_method = send_method_map["text"]
                send_args.update(
                    {
                        "text": final_content_single or "[空文本]",
                        "parse_mode": final_parse_mode_single,
                    }
                )
            elif msg_to_post.photo:
                send_method = send_method_map["photo"]
                send_args.update(
                    {
                        "photo": msg_to_post.photo[-1].file_id,
                        "caption": final_content_single,
                        "parse_mode": final_parse_mode_single,
                        "has_spoiler": original_has_spoiler,
                    }
                )
            elif msg_to_post.video:
                send_method = send_method_map["video"]
                send_args.update(
                    {
                        "video": msg_to_post.video.file_id,
                        "caption": final_content_single,
                        "parse_mode": final_parse_mode_single,
                        "has_spoiler": original_has_spoiler,
                    }
                )
            elif msg_to_post.animation:
                send_method = send_method_map["animation"]
                send_args.update(
                    {
                        "animation": msg_to_post.animation.file_id,
                        "caption": final_content_single,
                        "parse_mode": final_parse_mode_single,
                        "has_spoiler": original_has_spoiler,
                    }
                )
            elif msg_to_post.audio:
                send_method = send_method_map["audio"]
                send_args.update(
                    {
                        "audio": msg_to_post.audio.file_id,
                        "caption": final_content_single,
                        "parse_mode": final_parse_mode_single,
                    }
                )
            elif msg_to_post.document:
                send_method = send_method_map["document"]
                send_args.update(
                    {
                        "document": msg_to_post.document.file_id,
                        "caption": final_content_single,
                        "parse_mode": final_parse_mode_single,
                    }
                )
            elif msg_to_post.voice:
                send_method = send_method_map["voice"]
                send_args.update(
                    {
                        "voice": msg_to_post.voice.file_id,
                        "caption": final_content_single,
                        "parse_mode": final_parse_mode_single,
                    }
                )
            elif msg_to_post.sticker:
                send_method = send_method_map["sticker"]
                send_args.update({"sticker": msg_to_post.sticker.file_id})
            else:
                logger.warning(
                    f"发布单条消息时遇到不支持的类型: {msg_to_post.effective_attachment}"
                )
                return None

            try:
                posted_message_or_list = await send_method(**send_args)
                logger.info(
                    f"单条稿件 {submission_key} 已作为 '{submission_type}' 类型发布到频道。"
                )

                # 贴纸附加信息处理
                if (
                    msg_to_post.sticker
                    and final_extra_content
                    and final_extra_content.strip()
                ):
                    sticker_extra_info = "【关于此贴纸】" + final_extra_content
                    await bot.send_message(
                        chat_id=channel_id_or_username,
                        text=sticker_extra_info,
                        parse_mode=ParseMode.HTML,
                        reply_to_message_id=(
                            posted_message_or_list.message_id
                            if posted_message_or_list
                            else None
                        ),
                    )

            except TelegramError as e:
                logger.error(f"发送单条消息 {submission_key} 失败: {e}")
                # 可以尝试通知审稿群
                if group_id:
                    try:
                        await bot.send_message(
                            group_id, f"❌ 发布稿件 {first_fwd_msg_id} 失败: {e}"
                        )
                    except Exception:
                        pass
                return None

        # --- 3. 更新审稿群状态消息 ---
        post_succeeded = bool(posted_message_or_list)
        status_prefix = (
            "✅ <b>投稿已采用</b>\n\n"
            if post_succeeded
            else "⚠️ <b>投稿采用但发送失败</b>\n\n"
        )
        editor_link = (
            f'<a href="tg://user?id={editor.id}">{html.escape(editor.first_name)}</a>'
        )
        text_for_review_group = (
            status_prefix
            + f"<b>投稿人:</b> <a href='tg://user?id={submitter_id_int}'>{html.escape(submitter_name)}</a> ({submitter_id_int or '未知ID'})\n"
            + f"{source_info_for_review}\n"
            + f"<b>审稿人:</b> {editor_link}{editor_comment_text_for_review}\n"
            + f"<b>发布频道:</b> {channel_id_or_username}"
        )
        markup_msg_id = submission_info.get("Markup_ID")
        sent_status_message = None
        try:
            if markup_msg_id:
                sent_status_message = await bot.edit_message_text(
                    chat_id=group_id,
                    message_id=markup_msg_id,
                    text=text_for_review_group,
                    parse_mode=ParseMode.HTML,
                    reply_markup=None,
                )
            else:
                sent_status_message = await bot.send_message(
                    chat_id=group_id,
                    text=text_for_review_group,
                    reply_to_message_id=first_fwd_msg_id,
                    parse_mode=ParseMode.HTML,
                )
            if sent_status_message:
                markup_msg_id = sent_status_message.message_id
        except TelegramError as e:
            logger.error(f"更新/发送审稿群状态消息 (采用) 失败: {e}")

        # --- 4. 通知投稿人 ---
        original_msg_id_val = submission_info.get("Original_MsgID")
        original_msg_id = (
            int(original_msg_id_val)
            if original_msg_id_val and str(original_msg_id_val).isdigit()
            else None
        )

        if submitter_id_int and original_msg_id and post_succeeded:
            first_posted_msg = None
            if (
                isinstance(posted_message_or_list, (list, tuple))
                and len(posted_message_or_list) > 0
            ):
                first_posted_msg = posted_message_or_list[0]
            elif isinstance(posted_message_or_list, Message):
                first_posted_msg = posted_message_or_list

            post_link = None
            if first_posted_msg and isinstance(first_posted_msg, Message):
                try:
                    msg_id_to_link = first_posted_msg.message_id
                    if isinstance(
                        channel_id_or_username, str
                    ) and channel_id_or_username.startswith("@"):
                        post_link = f"https://t.me/{channel_id_or_username[1:]}/{msg_id_to_link}"
                    elif isinstance(channel_id_or_username, int) and str(
                        channel_id_or_username
                    ).startswith("-100"):
                        numeric_id = str(channel_id_or_username)[4:]
                        post_link = f"https://t.me/c/{numeric_id}/{msg_id_to_link}"
                    else:
                        logger.warning(
                            f"无法为频道 {channel_id_or_username} 生成跳转链接。"
                        )
                except AttributeError:
                    logger.error(
                        f"获取 first_posted_msg.message_id 时出错", exc_info=True
                    )

            notification_base_msg = "🎉 您的稿件已通过审核并发布！感谢您的支持！"
            final_notification_msg = notification_base_msg
            if post_link:
                final_notification_msg += f'\n<a href="{post_link}">点我跳转</a>'
            try:
                await bot.send_message(
                    chat_id=submitter_id_int,
                    text=final_notification_msg,
                    reply_to_message_id=original_msg_id,
                    allow_sending_without_reply=True,
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,
                )
                logger.info(
                    f"已通知投稿人 {submitter_id_int} (采用){' 并附带跳转链接' if post_link else ''}。"
                )
            except Exception as e:
                logger.error(
                    f"通知投稿人 {submitter_id_int} (采用) 时发生错误: {e}",
                    exc_info=True,
                )
        elif submitter_id_int and original_msg_id:  # 发布失败
            try:
                await bot.send_message(
                    chat_id=submitter_id_int,
                    text="ℹ️ 您的稿件已通过审核，但在发布时遇到问题。",
                    reply_to_message_id=original_msg_id,
                    allow_sending_without_reply=True,
                )
            except Exception:
                pass

        # --- 5. 更新状态和保存数据 ---
        status_code = (
            f"approved_{submission_type}" if post_succeeded else "failed_posting"
        )
        final_submission_data = {
            **submission_info,
            "posted": True,
            "status": status_code,
            "Markup_ID": markup_msg_id,
        }
        add_submission(submission_key, final_submission_data)
        await save_data_async()
        return posted_message_or_list

    except Exception as e:
        logger.error(f"发布稿件 {submission_key} 时发生未知错误: {e}", exc_info=True)
        if group_id and submission_key:
            try:
                await bot.send_message(
                    group_id,
                    f"❌ 发布稿件 {submission_key} 时发生内部错误，请检查日志。",
                )
            except Exception:
                pass
        if submission_key:
            update_submission_status(
                submission_key, posted=True, status="failed_posting"
            )  # 采用失败也标记为 posted=True, status=failed
            await save_data_async()
        return None


# --- reject_submission ---
async def reject_submission(
    context: ContextTypes.DEFAULT_TYPE,
    submission_key: str,
    submission_info: dict,
    editor: User,
    reason: str | None = None,
):
    """处理拒绝投稿的逻辑"""
    bot = context.bot
    group_id = get_group_id()
    sender_id_val = submission_info.get("Sender_ID")
    sender_name = submission_info.get("Sender_Name", "匿名")
    try:
        sender_id_int = int(sender_id_val) if sender_id_val else "未知ID"
    except (ValueError, TypeError):
        sender_id_int = "未知ID"
    original_msg_id_val = submission_info.get("Original_MsgID")
    original_msg_id = (
        int(original_msg_id_val)
        if original_msg_id_val and str(original_msg_id_val).isdigit()
        else None
    )

    logger.info(
        f"审稿人 {editor.name} ({editor.id}) 拒绝稿件 {submission_key} (理由: {reason or '无'})"
    )

    escaped_reason = html.escape(reason) if reason else None
    markup_msg_id = submission_info.get("Markup_ID")
    editor_link = (
        f'<a href="tg://user?id={editor.id}">{html.escape(editor.first_name)}</a>'
    )
    original_submission_msg_id_str = submission_key.split(":")[-1]
    reason_text = f"\n<b>拒绝理由:</b> {escaped_reason}" if escaped_reason else ""
    text = (
        f"🚫 <b>投稿已拒绝</b>\n\n"
        f"<b>投稿人:</b> <a href='tg://user?id={sender_id_int}'>{html.escape(sender_name)}</a> ({sender_id_int})\n"
        f"<b>原选择方式:</b> {submission_info.get('type', '未知')}\n"
        f"<b>审稿人:</b> {editor_link}{reason_text}"
    )

    sent_status_message = None
    try:
        if markup_msg_id:
            sent_status_message = await bot.edit_message_text(
                chat_id=group_id,
                message_id=markup_msg_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=None,
            )
        else:
            original_submission_msg_id_for_reply = (
                int(original_submission_msg_id_str)
                if original_submission_msg_id_str.isdigit()
                else None
            )
            if original_submission_msg_id_for_reply:
                sent_status_message = await bot.send_message(
                    chat_id=group_id,
                    text=text,
                    reply_to_message_id=original_submission_msg_id_for_reply,
                    parse_mode=ParseMode.HTML,
                )
            else:
                sent_status_message = await bot.send_message(
                    chat_id=group_id, text=text, parse_mode=ParseMode.HTML
                )
        if sent_status_message:
            markup_msg_id = sent_status_message.message_id
    except TelegramError as e:
        logger.error(
            f"更新/发送审稿群状态消息 {markup_msg_id or 'N/A'} (拒绝) 失败: {e}"
        )

    notification_text = "抱歉，您之前的投稿未能通过审核。"
    if escaped_reason:
        notification_text += f"\n理由: {escaped_reason}"
    if sender_id_int != "未知ID" and original_msg_id:
        try:
            await context.bot.send_message(
                chat_id=sender_id_int,
                text=notification_text,
                reply_to_message_id=original_msg_id,
                allow_sending_without_reply=True,
            )
            logger.info(f"已通知用户 {sender_id_int} 稿件被拒绝")
        except (Forbidden, BadRequest) as e:
            logger.warning(f"通知投稿人 {sender_id_int} (拒绝) 失败: {e}")
        except TelegramError as e:
            logger.error(f"通知投稿人 {sender_id_int} (拒绝) 时发生 Telegram 错误: {e}")
        except Exception as e:
            logger.error(
                f"通知投稿人 {sender_id_int} (拒绝) 时发生未知错误: {e}", exc_info=True
            )

    final_submission_data = {
        **submission_info,
        "posted": True,
        "status": "rejected",
        "Markup_ID": markup_msg_id,
    }
    add_submission(submission_key, final_submission_data)
    await save_data_async()


# --- reply_to_submitter ---
async def reply_to_submitter(
    context: ContextTypes.DEFAULT_TYPE,
    sender_id: int,
    original_msg_id: int | None,
    reply_text: str,
    editor: User,
):
    """向投稿人发送回复消息"""
    bot = context.bot
    logger.info(
        f"审稿人 {editor.name} ({editor.id}) 正在回复用户 {sender_id}：{reply_text[:50]}..."
    )
    try:
        reply_param = {}
        if original_msg_id:
            reply_param = {
                "reply_to_message_id": original_msg_id,
                "allow_sending_without_reply": True,
            }
        escaped_reply_text = html.escape(reply_text)
        text_to_send = f"【审稿回复】\n{escaped_reply_text}"
        await bot.send_message(chat_id=sender_id, text=text_to_send, **reply_param)
        logger.info(f"成功向用户 {sender_id} 发送回复。")
        return True
    except (Forbidden, BadRequest) as e:
        logger.warning(f"回复投稿人 {sender_id} 失败: 用户阻止或对话不存在 - {e}")
        group_id = get_group_id()
        if group_id:
            try:
                await bot.send_message(
                    group_id,
                    f"❌ 回复用户 {sender_id} 失败：用户可能已阻止机器人或对话不存在。",
                )
            except Exception:
                pass
        return False
    except TelegramError as e:
        logger.error(f"回复投稿人 {sender_id} 时发生 Telegram 错误: {e}")
        return False
    except Exception as e:
        logger.error(f"回复投稿人 {sender_id} 时发生未知错误: {e}", exc_info=True)
        return False
