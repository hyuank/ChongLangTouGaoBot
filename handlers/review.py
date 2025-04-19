# handlers/review.py
"""
处理审核群的所有交互：
- 按钮回调 (handle_review_callback)
- 操作命令 (ok_command, no_command, re_command, echo_command, ban_command, unban_command, unre_command)
- /re 会话中的普通消息转发 (handle_review_group_message)
- 帮助命令 (pwshelp_command)
"""

import logging
import telegram
import html  # 导入 html 用于转义
import data_manager  # 导入 data_manager 以便访问 submission_list 的 id
from telegram import Update, User, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError

# 从其他模块导入
from config_loader import (
    get_group_id,
    get_blocked_users,
    add_blocked_user,
    remove_blocked_user,
    save_config_async,
    get_user_warning_count,
    add_warning_to_user,
    reset_user_warning,
)
import data_manager  # 导入 data_manager 以便访问 submission_list 的 id
from data_manager import get_submission, save_data_async

# 导入 posting 中的函数
from posting import post_submission, reject_submission, reply_to_submitter

logger = logging.getLogger(__name__)

# --- 帮助信息 ---
PWS_HELP_TEXT = """<blockquote expandable>📋 审核群指令帮助
▶️ 回复投稿消息时使用：
/ok [评论] - 采纳稿件，按投稿人选择的(匿名/实名)方式发布，评论将作为发布消息的附加文本
/no [理由] - 拒绝稿件，理由将附加到审核群消息和用户通知中
/re [内容] - 进入回复模式与投稿人对话，之后您发送的普通消息将自动转发给该用户，直到使用/unre
/echo [内容] - 直接发送单条消息给投稿人，不进入回复模式
/warn [理由] - 警告用户，三次警告后自动封禁
/ban - 将投稿人加入黑名单，阻止其投稿
/unban - 将投稿人从黑名单移除，恢复其投稿权限
/unre - 退出当前回复模式
▶️ 无需回复特定投稿消息：
/status - (权蛆) 显示机器人状态
/setgroup - (权蛆，群内) 设置当前群为审核群
/setchannel [ID或用户名] - (权蛆) 设置发布频道 (例如: /setchannel @mychannel 或 /setchannel -100123456)
/setchatlink [聊天群链接] - (权蛆) 设置小尾巴中"聊天"的超链接(例如: /setchatlink https://t.me/your_chat)
/setemoji [类型] [Emoji]- (权蛆) 设置小尾巴Emoji
可选类型: submission, channel, chat
例如: /setemoji submission 💬
</blockquote>"""


# --- 辅助函数：获取投稿详情 ---
async def get_submission_details(
    message: telegram.Message, context: ContextTypes.DEFAULT_TYPE
) -> tuple[str | None, dict | None, int | None, int | None]:
    """从回复消息中提取投稿信息"""
    group_id = get_group_id()
    bot_id = context.bot.id

    # 验证回复的消息是否是由 Bot 发送的
    if (
        not group_id
        or not message.reply_to_message
        or not message.reply_to_message.from_user
        or message.reply_to_message.from_user.id != bot_id
    ):
        # 如果不是回复机器人发的消息，则无效
        logger.debug(f"get_submission_details: 回复的消息无效或不是来自机器人。")
        return None, None, None, None

    reply_to_msg = message.reply_to_message  # 审稿人实际回复的消息
    reply_to_msg_id = reply_to_msg.message_id
    submission_key = f"{group_id}:{reply_to_msg_id}"  # 先尝试用回复的消息 ID 构建 Key
    submission_info = get_submission(submission_key)  # data_manager.get_submission

    # --- 新增：处理媒体组查找 ---
    if not submission_info and reply_to_msg.media_group_id:
        logger.debug(
            f"Key {submission_key} 未找到，且回复的是媒体组消息，尝试查找媒体组主记录..."
        )
        found_key = None
        # 遍历内存中的 submission_list 查找 (需要加锁以保证线程安全)
        with data_manager.DATA_LOCK:  # 访问全局变量需加锁
            # 为了效率，可以只查找最近的 N 条记录，或者只查找与当前群组相关的
            # 查找属于当前群组、是媒体组、且包含当前回复消息 ID 的记录
            for key, value in data_manager.submission_list.items():
                # 检查 key 是否属于当前群组
                if (
                    key.startswith(f"{group_id}:")
                    # 检查记录是否标记为媒体组
                    and value.get("is_media_group")
                    # 检查记录的媒体组转发 ID 列表是否包含当前回复的消息 ID
                    and reply_to_msg_id in value.get("media_group_fwd_ids", [])
                ):
                    found_key = key  # 找到了包含此消息的媒体组主记录
                    logger.debug(f"通过媒体组 ID 找到主记录 Key: {found_key}")
                    break  # 找到就跳出循环
        # 如果通过遍历找到了媒体组的主记录 Key
        if found_key:
            submission_key = found_key  # 更新 submission_key 为主记录的 key
            submission_info = get_submission(
                submission_key
            )  # 重新使用主 key 获取投稿信息
    # --- 媒体组查找结束 ---

    if not submission_info:
        logger.debug(f"get_submission_details: 最终未找到 key {submission_key} 的记录")
        return submission_key, None, None, None
    # --------------------------------------

    sender_id = submission_info.get("Sender_ID")
    original_msg_id = submission_info.get("Original_MsgID")

    try:
        sender_id_int = int(sender_id) if sender_id else None
    except (ValueError, TypeError):
        sender_id_int = None
    try:
        original_msg_id_int = int(original_msg_id) if original_msg_id else None
    except (ValueError, TypeError):
        original_msg_id_int = None

    logger.debug(f"get_submission_details: Found info for key {submission_key}")
    return submission_key, submission_info, sender_id_int, original_msg_id_int


# --- 辅助函数：处理审核命令验证 ---
async def handle_review_command(
    update: Update, context: ContextTypes.DEFAULT_TYPE, command_name: str
):
    """处理审核群命令的通用入口和验证逻辑"""
    # 1. 检查是否回复了消息
    if not update.message or not update.message.reply_to_message:
        # 提醒用户需要回复投稿消息才能使用命令
        await update.message.reply_text(
            f"❌ 请回复一条投稿消息来使用 <code>/{command_name}</code> 命令。",
            parse_mode=ParseMode.HTML,
        )
        return None, None, None, None, None, None  # 返回空值表示验证失败

    # 2. 获取执行命令的审稿人
    editor = update.message.from_user
    if not editor:
        logger.warning(f"无法获取命令 {command_name} 的执行者信息。")
        return None, None, None, None, None, None  # 如果无法获取审稿人信息则失败

    # 3. 获取投稿详情
    # --- 修正调用点：传入 context ---
    # 调用 get_submission_details 获取投稿详情
    (
        submission_key,
        submission_info,
        sender_id,
        original_msg_id,
    ) = await get_submission_details(update.message, context)
    # ------------------------------

    # 4. 检查是否成功获取投稿信息
    if not submission_key or not submission_info:
        logger.warning(
            f"/{command_name} 命令无法找到有效的投稿记录 (key: {submission_key}) 或回复的消息无效。"
        )
        # get_submission_details 内部可能已回复，这里可以不再回复
        # (考虑: 是否需要告知用户找不到记录?)
        return None, None, None, None, None, None  # 验证失败

    # 5. 检查稿件是否已处理 (如果是以下命令，则允许对已处理稿件执行)
    allowed_for_posted = ["ban", "unban", "re", "echo", "warn"]
    if submission_info.get("posted", False) and command_name not in allowed_for_posted:
        status_text = submission_info.get("status", "已处理")
        await update.message.reply_text(f"ℹ️ 此稿件已被处理 (状态: {status_text})。")
        return None, None, None, None, None, None  # 验证失败 (稿件已处理)

    # 6. 检查是否存在投稿人 ID (所有命令都需要)
    if not sender_id:
        logger.error(
            f"命令 /{command_name} 无法获取稿件 {submission_key} 的投稿人 ID。"
        )
        await update.message.reply_text("❌ 无法获取投稿人 ID，无法执行此操作。")
        return None, None, None, None, None, None  # 验证失败 (缺少投稿人ID)

    # 7. 检查投稿人是否被阻止 (仅对需要交互的命令)
    if command_name in ["ok", "no", "re", "echo", "warn"]:
        if sender_id in get_blocked_users():
            # 如果投稿人已被阻止，则提示并阻止操作
            await update.message.reply_text(
                f"⚠️ 投稿人 {sender_id} 已被阻止，无法执行 <code>/{command_name}</code> 操作。请先 /unban。",
                parse_mode=ParseMode.HTML,
            )
            return None, None, None, None, None, None  # 验证失败 (用户被阻止)

    # 8. 获取命令参数
    args = context.args
    text_args = " ".join(args) if args else None

    # 所有检查通过
    logger.debug(f"/{command_name} 命令验证通过，参数: '{text_args}'")
    # 返回验证通过后的所有相关信息：审稿人, 投稿键, 投稿信息, 投稿人ID, 原始消息ID, 命令参数
    return (
        editor,
        submission_key,
        submission_info,
        sender_id,
        original_msg_id,
        text_args,
    )


# --- /pwshelp 命令处理器 ---
async def pwshelp_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """显示审核群帮助指令"""
    # 确保消息来自群组或超级群组
    if update.message and update.message.chat.type in ["group", "supergroup"]:
        try:
            # 使用折叠引用格式发送帮助信息，以减少屏幕占用
            await update.message.reply_text(
                PWS_HELP_TEXT, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
        except TelegramError as e:
            # 如果发送 HTML 格式失败 (例如格式错误或权限问题)
            logger.error(f"发送 HTML 帮助信息失败: {e}")
            # 尝试将 HTML 格式的帮助文本转换为纯文本
            plain_text_help = (
                PWS_HELP_TEXT.replace("<blockquote expandable>", "")
                .replace("</blockquote>", "")
                .replace("<", "\\<")
                .replace(">", "\\>")
            )
            try:
                # 发送纯文本版本的帮助信息
                await update.message.reply_text(
                    "发送格式化帮助失败...\n" + plain_text_help
                )
            except Exception as fallback_e:
                # 如果连纯文本都发送失败，记录严重错误
                logger.error(f"发送纯文本帮助也失败: {fallback_e}")


# --- 审核命令处理器 ---
async def ok_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /ok 命令 (采纳稿件)"""
    # 调用通用命令验证和信息提取函数
    (
        editor,
        submission_key,
        submission_info,
        sender_id,
        original_msg_id,
        comment,  # /ok 命令的参数作为评论文本
    ) = await handle_review_command(update, context, "ok")
    # 如果验证失败或信息不完整，则直接返回
    if not editor or not submission_info:
        return

    # 获取被审稿人回复的投稿消息对象 (用于转发或获取内容)
    reply_to_msg = update.message.reply_to_message
    logger.info(f"审稿人 {editor.name} 准备使用 /ok 处理稿件 {submission_key}")
    # 调用 posting 模块的函数来处理稿件发布逻辑
    post_result = await post_submission(
        context, reply_to_msg, editor, submission_info, comment
    )

    # 根据发布结果向审稿人发送确认消息
    if post_result:
        submission_type = submission_info.get(
            "type", "未知"
        )  # 获取投稿类型（实名/匿名）
        confirmation_text = f"✅ 稿件已作为 '{submission_type}' 类型发布。"
        # 判断原始投稿是否为纯文本或贴纸
        is_text_or_sticker = reply_to_msg.text or reply_to_msg.sticker
        # 如果审稿人提供了评论，并且原稿是媒体（非文本/贴纸）
        if comment and not is_text_or_sticker:
            # 评论将作为附加文本添加到媒体消息下方
            confirmation_text += " 评论已附加。"
        # 如果审稿人提供了评论，并且原稿是文本或贴纸
        elif comment:
            # 评论将作为对发布后消息的回复发送
            confirmation_text += " 评论已作为回复发送。"
        await update.message.reply_text(confirmation_text)
    else:
        # 如果 post_submission 返回 False 或抛出异常 (内部已处理)
        await update.message.reply_text("❌ 采纳并发布稿件时出错，请检查日志。")


async def no_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /no 命令"""
    (
        editor,
        submission_key,
        submission_info,
        sender_id,
        original_msg_id,
        reason,
    ) = await handle_review_command(update, context, "no")
    if not editor or not submission_info:
        return
    await reject_submission(context, submission_key, submission_info, editor, reason)
    await update.message.reply_text(
        f"🚫 稿件已拒绝。{'已附加理由。' if reason else ''}"
    )


async def re_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /re 命令 (进入与投稿人回复模式)"""
    # 调用通用命令验证和信息提取函数
    (
        editor,
        submission_key,
        submission_info,
        sender_id,
        original_msg_id,
        reply_text,  # /re 命令的参数作为首次回复内容
    ) = await handle_review_command(update, context, "re")
    # 如果验证失败或信息不完整，则直接返回
    if not editor or not submission_info:
        return
    # 检查审稿人是否提供了首次回复内容
    if not reply_text:
        await update.message.reply_text(
            "❌ 请输入要回复的内容：<code>/re <回复内容></code>",
            parse_mode=ParseMode.HTML,
        )
        return

    # 在 user_data 中存储当前回复会话的目标用户 ID 和原始投稿消息 ID
    # 这将用于 handle_review_group_message 转发后续消息
    context.user_data["reply_session_target_id"] = sender_id
    context.user_data["reply_session_original_msg_id"] = original_msg_id
    # 调用 posting 模块的函数向投稿人发送首次回复
    success = await reply_to_submitter(
        context, sender_id, original_msg_id, reply_text, editor
    )
    # 根据首次回复的发送结果进行反馈
    if success:
        # 发送成功，提示审稿人已进入回复模式
        await update.message.reply_text(
            f"✉️ 已向用户 {sender_id} 发送回复，并进入回复模式...\n使用 /unre 结束。"
        )
    else:
        # 发送失败 (可能用户已拉黑机器人)，提示审稿人并清除会话状态
        await update.message.reply_text(
            f"❌ 回复用户 {sender_id} 失败，未进入回复模式。"
        )
        # 清除 user_data 中的会话标记，避免后续消息被错误转发
        context.user_data.pop("reply_session_target_id", None)
        context.user_data.pop("reply_session_original_msg_id", None)


async def echo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /echo 命令"""
    (
        editor,
        submission_key,
        submission_info,
        sender_id,
        original_msg_id,
        reply_text,
    ) = await handle_review_command(update, context, "echo")
    if not editor or not submission_info:
        return
    if not reply_text:
        await update.message.reply_text(
            "❌ 请输入要发送的内容：<code>/echo <回复内容></code>",
            parse_mode=ParseMode.HTML,
        )
        return

    success = await reply_to_submitter(
        context, sender_id, original_msg_id, reply_text, editor
    )
    if success:
        await update.message.reply_text(f"📢 已向用户 {sender_id} 发送单次消息。")
    else:
        await update.message.reply_text(f"❌ 发送单次消息给用户 {sender_id} 失败。")


async def ban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /ban 命令"""
    (
        editor,
        submission_key,
        submission_info,
        sender_id,
        original_msg_id,
        _,
    ) = await handle_review_command(update, context, "ban")
    if not editor or not submission_info:
        return

    if add_blocked_user(sender_id):
        await save_config_async()
        await update.message.reply_text(f"🚫 用户 {sender_id} 已被添加到黑名单。")
    else:
        await update.message.reply_text(f"ℹ️ 用户 {sender_id} 已在黑名单中或添加失败。")


async def unban_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /unban 命令"""
    (
        editor,
        submission_key,
        submission_info,
        sender_id,
        original_msg_id,
        _,
    ) = await handle_review_command(update, context, "unban")
    if not editor or not submission_info:
        return

    if remove_blocked_user(sender_id):
        await save_config_async()
        await update.message.reply_text(f"✅ 用户 {sender_id} 已从黑名单移除。")
    else:
        await update.message.reply_text(f"ℹ️ 用户 {sender_id} 不在黑名单中或移除失败。")


async def unre_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /unre 命令"""
    # 这个命令不需要回复投稿消息，直接操作 user_data
    if "reply_session_target_id" in context.user_data:
        target = context.user_data.pop("reply_session_target_id", None)
        context.user_data.pop("reply_session_original_msg_id", None)
        await update.message.reply_text(f"✅ 已退出对用户 {target} 的回复模式。")
    else:
        await update.message.reply_text("ℹ️ 您当前未处于任何回复模式。")


# --- 审核群消息处理器 (仅处理 /re 会话) ---
async def handle_review_group_message(
    update: Update, context: ContextTypes.DEFAULT_TYPE
):
    """处理审核群中的非命令消息，主要用于转发 /re 会话"""
    # 忽略没有消息体或发送者的更新 (例如机器人自身的消息或服务消息)
    if not update.message or not update.message.from_user:
        return

    message = update.message
    editor = message.from_user  # 获取发送消息的审稿人

    # 从当前审稿人的 user_data 中获取回复会话的目标用户 ID 和原始消息 ID
    reply_target_id = context.user_data.get("reply_session_target_id")
    reply_original_msg_id = context.user_data.get("reply_session_original_msg_id")

    # 只有当该审稿人正处于回复会话中时才处理
    if reply_target_id:
        # 再次检查回复目标是否已被加入黑名单 (可能在会话期间被 ban)
        if reply_target_id in get_blocked_users():
            await message.reply_text(
                f"⚠️ 无法继续回复，用户 {reply_target_id} 已被阻止。请使用 /unre。"
            )
            return  # 阻止继续发送

        # 获取消息的文本内容
        text_content = message.text
        # 如果消息包含附件但没有文本 (例如直接发送图片/文件)
        if not text_content and message.effective_attachment:
            # 提示审稿人回复模式下不支持直接转发媒体
            await message.reply_text(
                "ℹ️ 回复模式下暂不支持直接发送媒体文件，请使用文字回复。"
            )
            return  # 忽略此消息
        # 如果消息没有文本内容也没有附件 (例如空消息或仅含格式的消息)
        elif not text_content:
            logger.debug("忽略空的 /re 会话消息")
            return  # 忽略此消息

        # 将审稿人的文本消息通过 posting 模块转发给投稿人
        success = await reply_to_submitter(
            context, reply_target_id, reply_original_msg_id, text_content, editor
        )
        # 如果转发失败
        if not success:
            # 在审核群给审稿人发送一个低调的失败提示 (不引用原消息，尝试静默)
            # 提示发送失败，可能是因为用户已阻止机器人
            await message.reply_text(
                "⚠️ (消息发送给用户失败，可能已被对方阻止)",
                quote=False,  # 不引用审稿人的原消息
                disable_notification=True,  # 尝试不发出通知音
            )
        # else: # 成功时可以不提示，避免刷屏
        #     await message.reply_text("✅ (已发送)", quote=False, disable_notification=True)
        return  # 处理完毕，这是 /re 会话消息

    # 如果当前审稿人没有处于 /re 会话中，则忽略这条普通消息
    logger.debug(
        f"忽略审核群中来自 {editor.name} 的普通消息 (非 /re 会话): {message.text[:50] if message.text else '<非文本>'}"
    )


# --- 审核群按钮回调处理器 ---
async def handle_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理审核群中内联按钮的回调"""
    query = update.callback_query
    # 1. 验证回调查询、数据和消息
    if not query or not query.data or not query.message:
        logger.warning("收到无效的回调查询。")
        return
    # 尽快应答回调，避免按钮一直转圈
    await query.answer()

    user = query.from_user  # 获取点击按钮的用户 (审稿人)
    message = query.message  # 获取包含按钮的消息

    # 2. 验证按钮消息是否是回复了某条消息 (预期是回复原始投稿)
    if not message.reply_to_message:
        logger.warning(
            f"审稿群按钮回调，但按钮消息 ({message.message_id}) 未回复任何消息。Callback data: {query.data}"
        )
        try:
            # 尝试编辑按钮消息，告知错误
            await query.edit_message_text("❌ 操作失败：按钮状态错误 (未回复消息)。")
        except TelegramError as e:
            logger.error(f"编辑按钮消息以提示错误失败: {e}")
        return

    original_submission_msg = message.reply_to_message
    logger.info(
        f"处理审核群回调，按钮消息 ID: {message.message_id}, 回复的消息 ID: {original_submission_msg.message_id}, 回调数据: {query.data}"
    )
    # --- 添加日志：打印 ID (用于调试 data_manager 问题) ---
    logger.info(
        f"--- handle_review_callback - BEFORE query - submission_list ID: {id(data_manager.submission_list)} ---"
    )
    # 3. 获取投稿详情
    # --- 修正调用点：传入 context ---
    # 注意：传入的是按钮消息 `message`, 它回复了原始投稿消息
    (
        submission_key,
        submission_info,
        sender_id,
        original_msg_id,
    ) = await get_submission_details(message, context)
    # ------------------------------

    # 4. 检查是否成功获取投稿信息
    if not submission_info:
        group_id = get_group_id()
        first_msg_id = original_submission_msg.message_id
        # 尝试直接使用被回复消息的ID构建key
        direct_key = f"{group_id}:{first_msg_id}"
        logger.warning(
            f"审稿群按钮回调，但投稿信息 {submission_key} 不存在。尝试直接查找 {direct_key}。Callback data: {query.data}"
        )

        # 直接尝试查找被回复消息的记录
        direct_submission_info = get_submission(direct_key)

        if direct_submission_info:
            # 找到了直接记录
            logger.info(f"找到了直接记录 {direct_key}，继续处理")
            submission_key = direct_key
            submission_info = direct_submission_info
            sender_id = direct_submission_info.get("Sender_ID")
            original_msg_id = direct_submission_info.get("Original_MsgID")

            # 更新Markup_ID，因为现在我们知道了正确的按钮消息ID
            if direct_submission_info.get(
                "pending_markup"
            ) and not direct_submission_info.get("Markup_ID"):
                direct_submission_info["Markup_ID"] = message.message_id
                direct_submission_info["pending_markup"] = False
                logger.info(
                    f"更新了投稿 {direct_key} 的Markup_ID为 {message.message_id}"
                )
                await save_data_async()
        else:
            # 遍历查找同一群组中的所有投稿记录，检查是否有媒体组包含当前消息ID
            found_key = None
            found_info = None

            with data_manager.DATA_LOCK:
                for key, info in data_manager.submission_list.items():
                    if key.startswith(f"{group_id}:") and info.get("is_media_group"):
                        # 检查媒体组转发ID列表是否包含当前回复的消息ID
                        if first_msg_id in info.get("media_group_fwd_ids", []):
                            found_key = key
                            found_info = info
                            logger.info(f"通过媒体组列表找到了投稿记录: {found_key}")
                            break

            if found_key and found_info:
                submission_key = found_key
                submission_info = found_info
                sender_id = found_info.get("Sender_ID")
                original_msg_id = found_info.get("Original_MsgID")

                # 更新Markup_ID（如果需要）
                if found_info.get("pending_markup") and not found_info.get("Markup_ID"):
                    found_info["Markup_ID"] = message.message_id
                    found_info["pending_markup"] = False
                    logger.info(
                        f"更新了投稿 {found_key} 的Markup_ID为 {message.message_id}"
                    )
                    await save_data_async()
            else:
                # 仍然找不到，返回错误
                try:
                    # 尝试编辑按钮消息告知错误
                    await query.edit_message_text(
                        f"❌ 操作失败：找不到该投稿记录 ({submission_key})。"
                    )
                except TelegramError as e:
                    logger.error(f"编辑按钮消息以提示找不到记录失败: {e}")
                return

    # 5. 检查稿件是否已被处理
    if submission_info.get("posted", False):
        status_text = submission_info.get("status", "已处理")
        # 通过 answer() 发送短暂提示
        await query.answer(f"该投稿已被处理 (状态: {status_text})。", show_alert=False)
        return

    # 6. 检查是否存在投稿人 ID
    if not sender_id:
        logger.error(
            f"无法处理审稿群按钮回调 {query.data} (稿件 {submission_key})：缺少有效的投稿人 ID。"
        )
        try:
            await query.edit_message_text("❌ 操作失败：缺少投稿人信息。")
        except TelegramError as e:
            logger.error(f"编辑按钮消息以提示缺少投稿人信息失败: {e}")
        return

    # 7. 检查投稿人是否在黑名单中
    if sender_id in get_blocked_users():
        # 通过 answer() 发送弹窗提示
        await query.answer(
            f"⚠️ 操作失败：投稿人 {sender_id} 已被阻止。", show_alert=True
        )
        return

    editor = user  # 确认操作者

    # --- 根据回调数据 (query.data) 执行不同的操作 ---

    # 如果点击的是"实名接收"按钮
    if query.data == "receive:real":
        logger.info(f"审稿人 {editor.name} 点击按钮采用稿件 {submission_key} (实名)")
        # 双重检查：确认记录中的类型是否匹配
        if submission_info.get("type") == "real":
            # 调用 post_submission 发布 (按钮不带评论)
            await post_submission(
                context, original_submission_msg, editor, submission_info, comment=None
            )
            # post_submission 内部会修改按钮状态
        else:
            # 类型不符，提示审稿人状态可能已变，建议用命令
            await query.answer(
                "⚠️ 按钮类型 ('real') 与记录 ('{}') 不符，建议使用 /ok 命令。".format(
                    submission_info.get("type")
                ),
                show_alert=True,
            )

    # 如果点击的是"匿名接收"按钮
    elif query.data == "receive:anonymous":
        logger.info(f"审稿人 {editor.name} 点击按钮采用稿件 {submission_key} (匿名)")
        # 双重检查：确认记录中的类型是否匹配
        if submission_info.get("type") == "anonymous":
            # 调用 post_submission 发布
            await post_submission(
                context, original_submission_msg, editor, submission_info, comment=None
            )
        else:
            # 类型不符提示
            await query.answer(
                "⚠️ 按钮类型 ('anonymous') 与记录 ('{}') 不符，建议使用 /ok 命令。".format(
                    submission_info.get("type")
                ),
                show_alert=True,
            )

    # 如果点击的是"拒绝"按钮
    elif query.data == "reject:submission":
        logger.info(f"审稿人 {editor.name} 点击按钮拒绝稿件 {submission_key}")
        # 调用 reject_submission 拒绝 (按钮不带理由)
        await reject_submission(
            context, submission_key, submission_info, editor, reason=None
        )
        # reject_submission 内部会修改按钮状态

    # 处理未知的回调数据
    else:
        logger.warning(f"收到未知的审稿群回调数据: {query.data} 来自用户 {editor.name}")
        try:
            # 尝试编辑按钮，告知未知操作
            await query.edit_message_text("❌ 操作失败：未知按钮。")
        except TelegramError as e:
            logger.error(f"编辑按钮消息以提示未知按钮失败: {e}")


# --- 警告命令处理器 ---
async def warn_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /warn 命令 (警告投稿人)"""
    # 调用通用命令验证和信息提取函数
    (
        editor,
        submission_key,
        submission_info,
        sender_id,
        original_msg_id,
        reason,  # /warn 命令的参数作为警告理由
    ) = await handle_review_command(update, context, "warn")
    # 如果验证失败或信息不完整，则直接返回
    if not editor or not submission_info:
        return

    # 添加警告并获取当前警告次数
    warning_count = add_warning_to_user(sender_id)
    
    # 保存配置变更
    await save_config_async()
    
    # 自动封禁逻辑：当警告次数达到3次时
    if warning_count >= 3:
        # 添加到黑名单
        if add_blocked_user(sender_id):
            await save_config_async()
            
            # 给投稿人发送被封禁的通知
            try:
                ban_text = "⚠️ 由于您已累计收到3次警告，您已被禁止使用投稿功能。"
                await context.bot.send_message(
                    chat_id=sender_id,
                    text=ban_text,
                    reply_to_message_id=original_msg_id,
                    allow_sending_without_reply=True,
                )
                logger.info(f"用户 {sender_id} 因累计3次警告已被自动封禁并通知。")
            except Exception as e:
                logger.error(f"通知被封禁用户 {sender_id} 失败: {e}")
            
            # 通知审稿群
            await update.message.reply_text(
                f"🚫 用户 {sender_id} 已累计收到3次警告，系统已自动将其加入黑名单。"
            )
            return
    
    # 构造警告消息文本
    warning_text = f"⚠️ 警告：您收到了管理员的警告 ({warning_count}/3)"
    if reason:
        warning_text += f"\n警告原因: {reason}"
    warning_text += f"\n注意：累计3次警告将被自动禁止使用投稿功能。"
    
    # 发送警告给投稿人
    try:
        await context.bot.send_message(
            chat_id=sender_id,
            text=warning_text,
            reply_to_message_id=original_msg_id,
            allow_sending_without_reply=True,
        )
        logger.info(f"已向用户 {sender_id} 发送警告 (当前警告次数: {warning_count}/3)")
        
        # 向审稿群确认
        await update.message.reply_text(
            f"✅ 已向用户 {sender_id} 发送警告 (当前警告次数: {warning_count}/3)。"
            + (f"\n警告原因: {reason}" if reason else "")
        )
    except Exception as e:
        logger.error(f"向用户 {sender_id} 发送警告失败: {e}")
        await update.message.reply_text(
            f"❌ 向用户 {sender_id} 发送警告失败: {e}"
        )
