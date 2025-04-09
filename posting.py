# posting.py

"""包含实际执行发布操作和与投稿人交互的函数"""

import logging
import html  # 用于 HTML 转义
from typing import List, Optional, Dict, Any  # 引入所需类型
from urllib.parse import quote  # 未在此文件中直接使用，可能在其他地方用于 URL 编码
from telegram import Message, User, InputMediaPhoto, InputMediaVideo
from telegram.constants import ParseMode  # HTML, MARKDOWN
from telegram import (
    # 用于解析转发来源
    MessageOriginUser,
    MessageOriginHiddenUser,
    MessageOriginChat,
    MessageOriginChannel,
)
from telegram.ext import ContextTypes
from telegram.error import TelegramError, Forbidden, BadRequest  # TG API 错误类型

# 从其他模块导入
from config_loader import get_publish_channel_id, get_group_id
from data_manager import update_submission_status, save_data_async, add_submission

logger = logging.getLogger(__name__)


# --- 统一的发布函数 ---
async def post_submission(
    context: ContextTypes.DEFAULT_TYPE,
    msg_to_post: Message,  # 审稿群中被引用的原始投稿消息 (通常是第一条)
    editor: User,  # 执行操作的审稿人 (User 对象)
    submission_info: dict,  # 从 data_manager 获取的完整投稿信息
    comment: str | None = None,  # 审稿人添加的评论 (可选)
) -> Optional[List[Message] | Message]:  # 返回发布到频道的消息对象(列表)或 None
    """
    发布投稿到频道（通过复制内容方式），能正确处理单条或媒体组。
    自动附加审稿评论和来源信息（如果用户选择保留）。
    依赖传入的 submission_info 字典获取所有必要信息。

    Args:
        context: PTB 上下文对象。
        msg_to_post: 审稿群中对应的原始投稿消息 (用于获取 key 和一些元数据)。
        editor: 操作的审稿人。
        submission_info: 包含投稿所有信息的字典 (类型, 来源, ID 等)。
        comment: 审稿人添加的评论。

    Returns:
        成功发布时返回频道中的 Message 对象 (单条) 或 Message 列表 (媒体组)。
        失败时返回 None。
    """
    bot = context.bot
    channel_id_or_username = get_publish_channel_id()  # 获取目标频道 ID 或用户名
    group_id = get_group_id()  # 获取审稿群组 ID
    first_fwd_msg_id = msg_to_post.message_id  # 审稿群消息的 ID
    # 构建投稿的唯一 key (用于 data_manager)
    submission_key = f"{group_id}:{first_fwd_msg_id}" if group_id else None

    # --- 从 submission_info 解包关键信息 ---
    submission_type = submission_info.get("type", "anonymous")  # 投稿类型 (real/anon)
    is_real_name_request = submission_type == "real"
    is_media_group = submission_info.get("is_media_group", False)  # 是否为媒体组
    submitter_id_val = submission_info.get("Sender_ID")  # 投稿人 ID
    submitter_name = submission_info.get("Sender_Name", "匿名")  # 投稿人名称
    parsed_origin = submission_info.get("parsed_forward_origin")  # 解析后的原始转发来源
    media_list_info = submission_info.get("messages")  # 媒体组中各消息的信息列表
    # --------------------------------------

    # 检查必要的配置是否完整
    if not channel_id_or_username or not group_id or not submission_key:
        logger.error("发布失败：缺少频道/群组 ID 或 Key")
        if group_id:
            try:
                # 尝试在审稿群发送错误通知
                await bot.send_message(group_id, "❌ 发布失败！频道或群组配置不完整。")
            except TelegramError as e:
                logger.warning(f"发送配置错误通知失败: {e}")
        return None

    # 尝试将投稿人 ID 转为整数
    try:
        submitter_id_int = int(submitter_id_val) if submitter_id_val else None
    except (ValueError, TypeError):
        submitter_id_int = None

    posted_message_or_list = None  # 初始化返回值 (发布成功的消息对象)

    try:
        # --- 1. 构造附加信息：审稿评论 和 来源信息 --- #
        final_extra_content_parts = []  # 最终附加到消息末尾的内容列表
        editor_comment_text_for_review = ""  # 用于审稿群状态消息的评论部分

        # 处理审稿评论
        if comment:
            escaped_comment = html.escape(comment)  # 转义 HTML 特殊字符
            comment_part = f"\n\n<b>小编 ({html.escape(editor.first_name)}):</b>\n{escaped_comment}"
            final_extra_content_parts.append(comment_part)
            editor_comment_text_for_review = f"\n<b>评论:</b> {escaped_comment}"

        via_link_part = ""  # 最终发布消息的来源链接部分 (via ...)
        source_info_for_review = "\n<b>来源:</b> 匿名"  # 默认是匿名，用于审稿群状态消息
        via_prefix = "\n\nvia "

        # 如果用户请求保留来源 (实名投稿)
        if is_real_name_request:
            source_representation = None  # 用于审稿群状态消息中展示的来源表示
            logger.debug(
                f"处理实名请求: parsed_origin 类型 = {type(parsed_origin)}, 内容 = {parsed_origin}"
            )

            # 情况1：原始消息是转发来的 (parsed_origin 是一个字典)
            if parsed_origin and isinstance(parsed_origin, dict):
                origin_type = parsed_origin.get("type")  # 转发来源类型
                origin_display_name = "未知来源"
                origin_link = None  # 初始化为 None, 用于构建最终的 via 链接
                logger.debug(f"解析转发来源，类型: {origin_type}")

                # 根据不同的来源类型构建链接和显示名称
                if origin_type == "MessageOriginUser":
                    uid = parsed_origin.get("sender_user_id")
                    uname = parsed_origin.get("sender_user_name", "用户")
                    escaped_name = html.escape(uname)
                    if uid:
                        origin_display_name = escaped_name
                        origin_link = f'<a href="tg://user?id={uid}">{escaped_name}</a>'
                    else:
                        # 可能是隐藏用户或获取 ID 失败
                        origin_display_name = escaped_name
                        origin_link = escaped_name  # 无法生成链接，仅显示名称
                    source_representation = origin_link  # 用于审稿群
                elif origin_type == "MessageOriginHiddenUser":
                    # 隐藏来源用户，显示其名称，尝试链接到 *提交者* 的 ID
                    uname = parsed_origin.get("sender_user_name", "隐藏用户")
                    escaped_name = html.escape(uname)
                    uid = parsed_origin.get("sender_user_id")
                    origin_display_name = (
                        f'<a href="tg://user?id={submitter_id_int}">{escaped_name}</a>'
                    )
                    origin_link = origin_display_name  # 赋值非 None
                    source_representation = origin_link
                elif origin_type == "MessageOriginChat":
                    # 来源是群组
                    title = parsed_origin.get("sender_chat_title", "群组")
                    uname = parsed_origin.get("sender_chat_username")
                    escaped_title = html.escape(title)
                    origin_display_name = f"群组: {escaped_title}"
                    origin_link = origin_display_name  # 默认链接
                    if uname:
                        # 如果有用户名，生成公开群组链接
                        origin_link = (
                            f'群组: <a href="https://t.me/{uname}">{escaped_title}</a>'
                        )
                    source_representation = origin_link
                elif origin_type == "MessageOriginChannel":
                    # 来源是频道
                    title = parsed_origin.get("chat_title", "频道")
                    uname = parsed_origin.get("chat_username")
                    chat_id = parsed_origin.get("chat_id")
                    msg_id = parsed_origin.get("message_id")
                    escaped_title = html.escape(title)
                    link = None  # 频道消息链接
                    # 优先使用用户名构建公开链接
                    if uname and msg_id:
                        link = f"https://t.me/{uname}/{msg_id}"
                    # 否则尝试构建私密频道链接 (c/...)
                    elif chat_id and str(chat_id).startswith("-100") and msg_id:
                        numeric_id = str(chat_id)[4:]  # 去掉 -100 前缀
                        link = f"https://t.me/c/{numeric_id}/{msg_id}"

                    origin_display_name = f"频道: {escaped_title}"
                    if link:
                        origin_link = f'频道: <a href="{link}">{escaped_title}</a>'
                    else:
                        origin_link = origin_display_name  # 无法生成链接
                    source_representation = origin_link
                else:
                    # 未知的来源类型
                    logger.warning(f"未知的 parsed_origin type: '{origin_type}'")
                    origin_link = None  # 确保未知类型时 link 为 None

                # 如果成功生成了 origin_link，则构建 via 部分
                if origin_link:
                    via_link_part = via_prefix + origin_link
                    source_info_for_review = (
                        f"\n<b>来源:</b> 保留 (原始: {source_representation})"
                    )
                else:
                    # 未能解析出有效的来源链接
                    source_info_for_review = "\n<b>来源:</b> 保留 (无法解析原始来源)"
                    via_link_part = ""  # 保持为空
                logger.debug(
                    f"转发来源处理结果: source_info='{source_info_for_review}', via_part='{via_link_part}'"
                )

            # 情况2：原始消息不是转发来的，来源是提交者本人
            else:
                logger.debug(
                    f"处理提交者来源: submitter_id_int={submitter_id_int}, submitter_name='{submitter_name}'"
                )
                if submitter_id_int:
                    escaped_submitter_name = html.escape(submitter_name)
                    # 构建指向提交者的 tg://user 链接
                    source_representation = f'<a href="tg://user?id={submitter_id_int}">{escaped_submitter_name}</a>'
                    via_link_part = via_prefix + source_representation
                    source_info_for_review = (
                        f"\n<b>来源:</b> 保留 (提交者: {source_representation})"
                    )
                    logger.debug(
                        f"提交者来源处理结果: source_info='{source_info_for_review}', via_part='{via_link_part}'"
                    )
                else:
                    # 无法获取提交者 ID，按匿名处理
                    source_info_for_review = "\n<b>来源:</b> 匿名 (无法获取提交者ID)"
                    logger.debug("提交者来源处理结果: 无法获取提交者ID")
                    via_link_part = ""  # 确保 via_link_part 在此情况下为空

            # 将构建好的 via_link_part 添加到最终附加内容列表
            if via_link_part:
                final_extra_content_parts.append(via_link_part)
                logger.debug(
                    "已将非空的 via_link_part 添加到 final_extra_content_parts"
                )
            else:
                logger.debug("via_link_part 为空，未添加")
        # else: # 用户请求匿名，跳过来源处理 (默认已经是匿名)
        #     logger.debug("用户请求匿名，跳过来源处理")

        # 合并所有附加内容部分
        final_extra_content = "".join(final_extra_content_parts)
        logger.debug(f"最终附加内容 final_extra_content: '{final_extra_content}'")
        logger.debug(
            f"最终审稿群来源信息 source_info_for_review: '{source_info_for_review}'"
        )
        # ------------------------------------------------------ #

        # --- 2. 发送逻辑：区分单条消息和媒体组 --- #
        if is_media_group:
            # 确保媒体信息列表有效
            if not media_list_info or not isinstance(media_list_info, list):
                logger.error(
                    f"尝试发布媒体组 {submission_key} 但缺少有效的媒体信息列表 (submission_info['messages'])。"
                )
                return None

            media_to_send = []  # 用于 bot.send_media_group 的 InputMedia 列表

            # --- 获取第一个媒体的原始 caption，并将附加信息添加到其上 --- #
            first_item_info = media_list_info[0] if media_list_info else {}
            original_caption_media = first_item_info.get(
                "caption"
            )  # 第一个媒体的原 caption
            # 最终第一个媒体的 caption = 原 caption + 附加信息
            final_caption_for_first = (
                original_caption_media or ""
            ) + final_extra_content
            # 如果最终 caption 不为空，使用 HTML 解析模式
            final_parse_mode_for_first = (
                ParseMode.HTML if final_caption_for_first else None
            )
            # 清理空 caption (例如只有换行符)
            if final_caption_for_first and final_caption_for_first.strip() == "":
                final_caption_for_first, final_parse_mode_for_first = None, None
            # ------------------------------------------------------------ #

            # 遍历媒体组中的每个项目，构建 InputMedia 对象
            for i, item in enumerate(media_list_info):
                # 只有第一个媒体项带有 caption
                current_caption = final_caption_for_first if i == 0 else None
                current_parse_mode = final_parse_mode_for_first if i == 0 else None
                has_spoiler = item.get("has_spoiler", False)  # 是否有剧透遮罩
                file_id = item.get("file_id")  # 媒体文件的 file_id
                media_type = item.get("type")  # 媒体类型 (photo, video)

                if not file_id:
                    logger.warning(f"媒体组 {submission_key} 中的项目 {i} 缺少 file_id")
                    continue  # 跳过缺少 file_id 的项目

                # 根据媒体类型创建对应的 InputMedia 对象
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
                # 可以按需添加对其他类型 (Audio, Document) 的支持
                else:
                    logger.warning(
                        f"媒体组 {submission_key} 中包含暂不支持发布的类型: {media_type}"
                    )

            # 如果 media_to_send 为空 (例如所有项目都无效)
            if not media_to_send:
                logger.error(f"为媒体组 {submission_key} 构建的 InputMedia 列表为空。")
                return None

            # --- 使用 send_media_group 发送 --- #
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
                # 尝试通知审稿群
                if group_id:
                    try:
                        await bot.send_message(
                            group_id, f"❌ 发布媒体组 {first_fwd_msg_id} 失败: {e}"
                        )
                    except Exception:
                        pass  # 发送通知失败就算了
                return None  # 返回失败
            # --------------------------------- #

        else:  # 处理单条消息
            original_content = None  # 原始消息的文本或 caption (HTML 格式)
            is_text_message = False  # 标记是否为纯文本消息

            if msg_to_post.text:
                # 如果是文本消息
                original_content, is_text_message = msg_to_post.text_html, True
            elif msg_to_post.caption:
                # 如果是带标题的媒体消息
                original_content = msg_to_post.caption_html

            # 最终发送的内容 = 原始内容 + 附加信息
            final_content_single = (original_content or "") + final_extra_content
            # 如果最终内容不为空，使用 HTML 解析模式
            final_parse_mode_single = ParseMode.HTML if final_content_single else None
            # 清理空内容
            if final_content_single and final_content_single.strip() == "":
                final_content_single, final_parse_mode_single = None, None

            # 获取原始媒体是否有剧透遮罩
            original_has_spoiler = msg_to_post.has_media_spoiler

            # --- 根据消息类型选择合适的发送方法 --- #
            send_method_map = {
                "text": bot.send_message,
                "photo": bot.send_photo,
                "video": bot.send_video,
                "animation": bot.send_animation,  # GIF
                "audio": bot.send_audio,
                "document": bot.send_document,
                "voice": bot.send_voice,
                "sticker": bot.send_sticker,
                # ... 可以添加更多类型
            }
            send_method = None  # 最终使用的发送函数
            send_args = {"chat_id": channel_id_or_username}  # 发送函数的参数字典

            if is_text_message:
                send_method = send_method_map["text"]
                send_args.update(
                    {
                        "text": final_content_single or "[空文本]",  # 防止发送空消息
                        "parse_mode": final_parse_mode_single,
                        # 文本消息没有 disable_web_page_preview，需要单独处理链接预览
                    }
                )
            elif msg_to_post.photo:
                send_method = send_method_map["photo"]
                send_args.update(
                    {
                        "photo": msg_to_post.photo[-1].file_id,  # 发送最高分辨率图片
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
                        # 音频似乎不支持 has_spoiler
                    }
                )
            elif msg_to_post.document:
                send_method = send_method_map["document"]
                send_args.update(
                    {
                        "document": msg_to_post.document.file_id,
                        "caption": final_content_single,
                        "parse_mode": final_parse_mode_single,
                        # 文档似乎不支持 has_spoiler
                    }
                )
            elif msg_to_post.voice:
                send_method = send_method_map["voice"]
                send_args.update(
                    {
                        "voice": msg_to_post.voice.file_id,
                        "caption": final_content_single,
                        "parse_mode": final_parse_mode_single,
                        # 语音似乎不支持 has_spoiler
                    }
                )
            elif msg_to_post.sticker:
                send_method = send_method_map["sticker"]
                send_args.update({"sticker": msg_to_post.sticker.file_id})
                # 贴纸不支持 caption，附加信息需要单独发送
            else:
                # 遇到未知或不支持的消息类型
                logger.warning(
                    f"发布单条消息时遇到不支持的类型: {msg_to_post.effective_attachment}"
                )
                return None  # 返回失败
            # ------------------------------------------ #

            # --- 执行发送操作 --- #
            try:
                posted_message_or_list = await send_method(**send_args)
                logger.info(
                    f"单条稿件 {submission_key} 已作为 '{submission_type}' 类型发布到频道。"
                )

                # --- 特殊处理：为贴纸单独发送附加信息 --- #
                if (
                    msg_to_post.sticker
                    and final_extra_content
                    and final_extra_content.strip()
                ):
                    # 构建附加信息文本
                    sticker_extra_info = "【关于此贴纸】" + final_extra_content
                    # 作为对刚发送贴纸的回复发送
                    await bot.send_message(
                        chat_id=channel_id_or_username,
                        text=sticker_extra_info,
                        parse_mode=ParseMode.HTML,
                        reply_to_message_id=(
                            posted_message_or_list.message_id
                            if posted_message_or_list  # 确保贴纸消息发送成功
                            else None
                        ),
                    )
                # -------------------------------------- #

            except TelegramError as e:
                logger.error(f"发送单条消息 {submission_key} 失败: {e}")
                # 尝试通知审稿群
                if group_id:
                    try:
                        await bot.send_message(
                            group_id, f"❌ 发布稿件 {first_fwd_msg_id} 失败: {e}"
                        )
                    except Exception:
                        pass
                return None  # 返回失败
            # -------------------- #
        # ------------------------------------------ #

        # --- 3. 更新审稿群的状态消息 (编辑或发送新消息) --- #
        post_succeeded = bool(posted_message_or_list)  # 检查发布是否成功
        status_prefix = (
            "✅ <b>投稿已采用</b>\n\n"
            if post_succeeded
            else "⚠️ <b>投稿采用但发送失败</b>\n\n"  # 标记发送失败状态
        )
        # 构建审稿人链接
        editor_link = (
            f'<a href="tg://user?id={editor.id}">{html.escape(editor.first_name)}</a>'
        )
        # 构建审稿群状态文本
        text_for_review_group = (
            status_prefix
            + f"<b>投稿人:</b> <a href='tg://user?id={submitter_id_int}'>{html.escape(submitter_name)}</a> ({submitter_id_int or '未知ID'})\n"
            + f"{source_info_for_review}\n"  # 包含之前处理好的来源信息
            + f"<b>审稿人:</b> {editor_link}{editor_comment_text_for_review}\n"  # 包含审稿人和评论
            + f"<b>发布频道:</b> {channel_id_or_username}"  # 显示目标频道
        )
        # 获取之前发送的带按钮的消息 ID (如果有)
        markup_msg_id = submission_info.get("Markup_ID")
        sent_status_message = None  # 存储发送或编辑后的状态消息对象
        try:
            # 如果存在 Markup_ID，编辑该消息
            if markup_msg_id:
                sent_status_message = await bot.edit_message_text(
                    chat_id=group_id,
                    message_id=markup_msg_id,
                    text=text_for_review_group,
                    parse_mode=ParseMode.HTML,
                    reply_markup=None,  # 移除按钮
                )
            else:
                # 否则，作为对原始投稿消息的回复发送新状态消息
                sent_status_message = await bot.send_message(
                    chat_id=group_id,
                    text=text_for_review_group,
                    reply_to_message_id=first_fwd_msg_id,  # 回复原始投稿
                    parse_mode=ParseMode.HTML,
                )
            # 更新 Markup_ID 以便后续可能的编辑 (例如，如果采用后又想拒绝？虽然流程上少见)
            if sent_status_message:
                markup_msg_id = sent_status_message.message_id
        except TelegramError as e:
            logger.error(f"更新/发送审稿群状态消息 (采用) 失败: {e}")
        # ------------------------------------------------------ #

        # --- 4. 通知投稿人 --- #
        # 获取投稿人在私聊中的原始消息 ID
        original_msg_id_val = submission_info.get("Original_MsgID")
        original_msg_id = (
            int(original_msg_id_val)
            if original_msg_id_val and str(original_msg_id_val).isdigit()
            else None
        )

        # 仅在投稿人 ID 和原始消息 ID 都有效，且发布成功时通知
        if submitter_id_int and original_msg_id and post_succeeded:
            first_posted_msg = None  # 第一个发布到频道的消息对象
            # 处理媒体组和单条消息的不同返回类型
            if (
                isinstance(posted_message_or_list, (list, tuple))
                and len(posted_message_or_list) > 0
            ):
                first_posted_msg = posted_message_or_list[0]
            elif isinstance(posted_message_or_list, Message):
                first_posted_msg = posted_message_or_list

            post_link = None  # 初始化帖子链接
            # 如果成功获取到第一个发布的消息对象
            if first_posted_msg and isinstance(first_posted_msg, Message):
                try:
                    msg_id_to_link = first_posted_msg.message_id
                    # 根据频道是公开 (@username) 还是私有 (-100...) 构建链接
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
                        # 无法识别的频道 ID 格式
                        logger.warning(
                            f"无法为频道 {channel_id_or_username} 生成跳转链接。"
                        )
                except AttributeError:
                    # 防止 first_posted_msg 对象不完整或类型错误
                    logger.error(
                        f"获取 first_posted_msg.message_id 时出错", exc_info=True
                    )

            # --- 构建并发送通知消息 --- #
            notification_base_msg = "🎉 您的稿件已通过审核并发布！感谢您的支持！"
            final_notification_msg = notification_base_msg
            if post_link:
                # 如果成功生成链接，附加到通知消息
                final_notification_msg += f'\n<a href="{post_link}">点我跳转</a>'
            try:
                await bot.send_message(
                    chat_id=submitter_id_int,
                    text=final_notification_msg,
                    reply_to_message_id=original_msg_id,  # 回复投稿人原始消息
                    allow_sending_without_reply=True,  # 即使原始消息被删除也尝试发送
                    parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True,  # 禁用链接预览
                )
                logger.info(
                    f"已通知投稿人 {submitter_id_int} (采用){' 并附带跳转链接' if post_link else ''}。"
                )
            except Forbidden as e:
                # 用户阻止了机器人或注销了账号
                logger.warning(
                    f"通知投稿人 {submitter_id_int} (采用) 失败: 用户阻止或不存在 - {e}"
                )
            except BadRequest as e:
                # 通常是 reply_to_message_id 无效 (原始消息被删除且 allow_sending_without_reply=False)
                # 或其他 API 参数问题
                logger.warning(
                    f"通知投稿人 {submitter_id_int} (采用) 时发生 BadRequest: {e}"
                )
            except TelegramError as e:
                # 其他 Telegram API 错误
                logger.error(
                    f"通知投稿人 {submitter_id_int} (采用) 时发生 Telegram 错误: {e}"
                )
            except Exception as e:
                # 其他未知错误
                logger.error(
                    f"通知投稿人 {submitter_id_int} (采用) 时发生未知错误: {e}",
                    exc_info=True,
                )
            # -------------------------- #
        elif submitter_id_int and original_msg_id:  # 如果发布失败，也通知用户
            try:
                await bot.send_message(
                    chat_id=submitter_id_int,
                    text="ℹ️ 您的稿件已通过审核，但在发布时遇到技术问题，请稍后查看频道或联系权蛆。",
                    reply_to_message_id=original_msg_id,
                    allow_sending_without_reply=True,
                )
            except Exception as e:
                logger.warning(f"通知投稿人 {submitter_id_int} (发布失败) 时出错: {e}")
                pass  # 通知失败就算了
        # ----------------------- #

        # --- 5. 更新投稿状态并保存数据 --- #
        # 根据发布是否成功设置状态码
        status_code = (
            f"approved_{submission_type}" if post_succeeded else "failed_posting"
        )
        # 更新 submission_info 字典
        final_submission_data = {
            **submission_info,  # 展开原有信息
            "posted": True,  # 标记为已处理
            "status": status_code,  # 记录最终状态
            "Markup_ID": markup_msg_id,  # 保存最终的状态消息 ID
        }
        # 使用 add_submission 更新内存中的数据 (会覆盖旧数据)
        add_submission(submission_key, final_submission_data)
        # await save_data_async() # add_submission 内部已经触发异步保存
        return posted_message_or_list  # 返回发布成功的消息对象(列表)
        # --------------------------------- #

    except Exception as e:
        # --- 顶级异常处理 --- #
        logger.error(f"发布稿件 {submission_key} 时发生未知错误: {e}", exc_info=True)
        # 尝试在审稿群通知错误
        if group_id and submission_key:
            try:
                await bot.send_message(
                    group_id,
                    f"❌ 发布稿件 {submission_key.split(':')[-1]} 时发生内部错误，请检查日志。",
                )
            except Exception:
                pass
        # 即使发生错误，也尝试更新投稿状态为失败，避免重复处理
        if submission_key:
            # 标记为已处理，状态为失败
            update_submission_status(
                submission_key, posted=True, status="failed_posting"
            )  # 采用失败也标记为 posted=True, status=failed
            await save_data_async()
        return None


# --- 拒绝投稿函数 --- #
async def reject_submission(
    context: ContextTypes.DEFAULT_TYPE,
    submission_key: str,  # 投稿的唯一 key
    submission_info: dict,  # 投稿信息字典
    editor: User,  # 操作的审稿人
    reason: str | None = None,  # 拒绝理由 (可选)
):
    """处理拒绝投稿的逻辑，包括更新审稿群状态和通知投稿人。"""
    bot = context.bot
    group_id = get_group_id()
    sender_id_val = submission_info.get("Sender_ID")
    sender_name = submission_info.get("Sender_Name", "匿名")

    # 尝试获取整数形式的投稿人 ID
    try:
        sender_id_int = int(sender_id_val) if sender_id_val else "未知ID"
    except (ValueError, TypeError):
        sender_id_int = "未知ID"

    # 获取投稿人在私聊中的原始消息 ID
    original_msg_id_val = submission_info.get("Original_MsgID")
    original_msg_id = (
        int(original_msg_id_val)
        if original_msg_id_val and str(original_msg_id_val).isdigit()
        else None
    )

    logger.info(
        f"审稿人 {editor.name} ({editor.id}) 拒绝稿件 {submission_key} (理由: {reason or '无'})"
    )

    # 转义拒绝理由中的 HTML 特殊字符
    escaped_reason = html.escape(reason) if reason else None
    # 获取之前带按钮的消息 ID
    markup_msg_id = submission_info.get("Markup_ID")
    # 构建审稿人链接
    editor_link = (
        f'<a href="tg://user?id={editor.id}">{html.escape(editor.first_name)}</a>'
    )
    # 从 key 中提取审稿群原始消息 ID (用于回复)
    original_submission_msg_id_str = submission_key.split(":")[-1]
    # 构建拒绝理由文本 (如果存在)
    reason_text = f"\n<b>拒绝理由:</b> {escaped_reason}" if escaped_reason else ""
    # 构建审稿群状态消息文本
    text = (
        f"🚫 <b>投稿已拒绝</b>\n\n"
        f"<b>投稿人:</b> <a href='tg://user?id={sender_id_int}'>{html.escape(sender_name)}</a> ({sender_id_int})\n"
        f"<b>原选择方式:</b> {submission_info.get('type', '未知')}\n"
        f"<b>审稿人:</b> {editor_link}{reason_text}"
    )

    sent_status_message = None  # 存储发送/编辑后的状态消息对象
    # --- 更新审稿群状态消息 --- #
    try:
        # 如果有 Markup_ID，编辑原消息
        if markup_msg_id:
            sent_status_message = await bot.edit_message_text(
                chat_id=group_id,
                message_id=markup_msg_id,
                text=text,
                parse_mode=ParseMode.HTML,
                reply_markup=None,  # 移除按钮
            )
        else:
            # 否则，尝试回复审稿群中的原始投稿消息
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
                # 如果无法获取原始消息 ID，直接发送新消息
                sent_status_message = await bot.send_message(
                    chat_id=group_id, text=text, parse_mode=ParseMode.HTML
                )
        # 更新 Markup_ID
        if sent_status_message:
            markup_msg_id = sent_status_message.message_id
    except TelegramError as e:
        logger.error(
            f"更新/发送审稿群状态消息 {markup_msg_id or 'N/A'} (拒绝) 失败: {e}"
        )
    # -------------------------- #

    # --- 通知投稿人 --- #
    notification_text = "抱歉，您之前的投稿未能通过审核。"
    if escaped_reason:
        notification_text += f"\n理由: {escaped_reason}"

    # 仅当投稿人 ID 和原始消息 ID 有效时尝试通知
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
            # 处理用户阻止机器人或对话不存在等情况
            logger.warning(f"通知投稿人 {sender_id_int} (拒绝) 失败: {e}")
        except TelegramError as e:
            logger.error(f"通知投稿人 {sender_id_int} (拒绝) 时发生 Telegram 错误: {e}")
        except Exception as e:
            logger.error(
                f"通知投稿人 {sender_id_int} (拒绝) 时发生未知错误: {e}", exc_info=True
            )
    # ----------------- #

    # --- 更新投稿状态并保存数据 --- #
    final_submission_data = {
        **submission_info,
        "posted": True,  # 标记为已处理
        "status": "rejected",  # 状态为拒绝
        "Markup_ID": markup_msg_id,  # 保存状态消息 ID
    }
    add_submission(submission_key, final_submission_data)
    await save_data_async()


# --- 回复投稿人函数 --- #
async def reply_to_submitter(
    context: ContextTypes.DEFAULT_TYPE,
    sender_id: int,  # 投稿人 User ID
    original_msg_id: int | None,  # 投稿人在私聊中的原始消息 ID (用于回复)
    reply_text: str,  # 审稿人的回复内容
    editor: User,  # 执行回复的审稿人
):
    """通过 Bot 向投稿人发送回复消息。"""
    bot = context.bot
    logger.info(
        f"审稿人 {editor.name} ({editor.id}) 正在回复用户 {sender_id}：{reply_text[:50]}..."
    )
    try:
        reply_param = {}  # 回复参数
        if original_msg_id:
            # 如果有原始消息 ID，设置回复参数
            reply_param = {
                "reply_to_message_id": original_msg_id,
                "allow_sending_without_reply": True,
            }
        # 转义回复内容中的 HTML 特殊字符
        escaped_reply_text = html.escape(reply_text)
        # 添加回复标识前缀
        text_to_send = f"【审稿回复】\n{escaped_reply_text}"
        # 发送消息
        await bot.send_message(chat_id=sender_id, text=text_to_send, **reply_param)
        logger.info(f"成功向用户 {sender_id} 发送回复。")
        return True  # 返回成功
    except (Forbidden, BadRequest) as e:
        # 处理用户阻止机器人或对话不存在等情况
        logger.warning(f"回复投稿人 {sender_id} 失败: 用户阻止或对话不存在 - {e}")
        # 尝试在审稿群发送失败通知
        group_id = get_group_id()
        if group_id:
            try:
                await bot.send_message(
                    group_id,
                    f"❌ 回复用户 {sender_id} 失败：用户可能已阻止机器人或对话不存在。",
                )
            except Exception:
                pass  # 通知失败就算了
        return False  # 返回失败
    except TelegramError as e:
        logger.error(f"回复投稿人 {sender_id} 时发生 Telegram 错误: {e}")
        return False  # 返回失败
    except Exception as e:
        logger.error(f"回复投稿人 {sender_id} 时发生未知错误: {e}", exc_info=True)
        return False  # 返回失败
