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
)
import data_manager  # 导入 data_manager 以便访问 submission_list 的 id
from data_manager import get_submission, save_data_async

# 导入 posting 中的函数
from posting import post_submission, reject_submission, reply_to_submitter

logger = logging.getLogger(__name__)

# --- 帮助信息 ---
PWS_HELP_TEXT = """
<b>审核群指令帮助</b> (<code>/pwshelp</code>):
(请在回复投稿消息时使用以下指令)

<code>/ok (可选评论)</code> - 采纳稿件。将按投稿人原选方式（实名/匿名）发布。评论将附加到频道消息下。
<code>/no (可选理由)</code> - 拒绝稿件。理由将附加到审核群消息和用户通知中。
<code>/re (回复内容)</code> - 进入回复模式，直接与投稿人对话。之后您发送的普通消息将自动转发给该用户，直到使用 <code>/unre</code>。
<code>/echo (回复内容)</code> - 直接向投稿人发送单条消息，不进入回复模式。
<code>/ban</code> - 将该投稿人加入黑名单，阻止其再次投稿。
<code>/unban</code> - 将该投稿人从黑名单移除。
<code>/unre</code> - 退出当前的回复模式 (<code>/re</code> 状态)。

<b>(以下指令无需回复投稿消息)</b>
<code>/status</code> - (管理员) 显示机器人状态。
<code>/setgroup</code> - (管理员，群内) 设置当前群为审核群。
<code>/setchannel ID或用户名</code> - (管理员) 设置发布频道。(例如: <code>/setchannel @mychannel</code> 或 <code>/setchannel -100123...</code>)
"""


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

    reply_to_msg = (
        message.reply_to_message
    )  # 这是 Bot 发送到群里的消息 (可能是转发的单条，也可能是媒体组的第一条)
    submission_key = f"{group_id}:{reply_to_msg.message_id}"
    submission_info = get_submission(submission_key)

    if not submission_info:
        logger.debug(
            f"get_submission_details: 在 data_manager 中未找到 key {submission_key}"
        )
        # --- 针对媒体组的兼容处理 ---
        # 检查这条消息是否是已知媒体组的一部分 (需要访问消息对象检查 media_group_id)
        # 并且检查 data_manager 中是否有以这个 media_group_id 关联的记录
        # 这个逻辑比较复杂，而且容易出错，暂时不实现。
        # 更好的方法是在存储 submission_info 时，如果 is_media_group=True，
        # 不仅用第一条消息 ID 做 key，还可以在一个地方额外记录 media_group_id -> first_message_key 的映射。
        # 或者，在 handle_submission_callback 中，为媒体组的 *每一条* 消息都创建一条记录？（这会导致记录冗余）

        # --- 简化处理：假设 key 找不到就是真的找不到了 ---
        return submission_key, None, None, None
        # -----------------------------------------

    # --- 验证被回复的消息是否符合预期 ---
    # is_media_group_in_record = submission_info.get('is_media_group', False)
    # has_forward_origin = bool(reply_to_msg.forward_origin)
    #
    # if is_media_group_in_record and has_forward_origin:
    #     logger.warning(f"记录显示是媒体组，但回复的消息有 forward_origin (不匹配): key={submission_key}")
    #     # 可能数据不一致，可以选择返回错误
    #     # return submission_key, None, None, None
    # elif not is_media_group_in_record and not has_forward_origin:
    #     logger.warning(f"记录显示是单条消息，但回复的消息没有 forward_origin (不匹配): key={submission_key}")
    #     # 可能数据不一致
    #     # return submission_key, None, None, None
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
    if not update.message or not update.message.reply_to_message:
        # 使用 code 格式化命令名
        await update.message.reply_text(
            f"❌ 请回复一条投稿消息来使用 <code>/{command_name}</code> 命令。",
            parse_mode=ParseMode.HTML,
        )
        return None, None, None, None, None, None

    editor = update.message.from_user
    if not editor:
        return None, None, None, None, None, None

    # --- 修正调用点：传入 context ---
    (
        submission_key,
        submission_info,
        sender_id,
        original_msg_id,
    ) = await get_submission_details(update.message, context)
    # ------------------------------

    if not submission_key or not submission_info:
        logger.warning(f"/{command_name} 命令无法找到有效的投稿记录或回复无效。")
        # get_submission_details 内部可能已回复，这里可以不再回复
        # await update.message.reply_text("❌ 无法找到对应的投稿记录，或回复的消息无效。")
        return None, None, None, None, None, None

    # 允许 ban/unban 对已处理稿件操作
    if submission_info.get("posted", False) and command_name not in ["ban", "unban"]:
        status_text = submission_info.get("status", "已处理")
        await update.message.reply_text(f"ℹ️ 此稿件已被处理 (状态: {status_text})。")
        return None, None, None, None, None, None

    if not sender_id:  # ban/unban 也需要 sender_id
        await update.message.reply_text("❌ 无法获取投稿人 ID，无法执行此操作。")
        return None, None, None, None, None, None

    # 检查投稿人是否被阻止
    if command_name in ["ok", "no", "re", "echo"]:
        if sender_id in get_blocked_users():
            # 使用 code 格式化命令名
            await update.message.reply_text(
                f"⚠️ 投稿人 {sender_id} 已被阻止，无法执行 <code>/{command_name}</code> 操作。请先 /unban。",
                parse_mode=ParseMode.HTML,
            )
            return None, None, None, None, None, None

    args = context.args
    text_args = " ".join(args) if args else None

    logger.debug(f"/{command_name} 命令验证通过，参数: '{text_args}'")
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
    if update.message and update.message.chat.type in ["group", "supergroup"]:
        try:
            await update.message.reply_text(
                PWS_HELP_TEXT, parse_mode=ParseMode.HTML, disable_web_page_preview=True
            )
        except TelegramError as e:
            logger.error(f"发送 HTML 帮助信息失败: {e}")
            plain_text_help = (
                PWS_HELP_TEXT.replace("<code>", "`")
                .replace("</code>", "`")
                .replace("<b>", "")
                .replace("</b>", "")
                .replace("<", "<")
                .replace(">", ">")
            )
            try:
                await update.message.reply_text(
                    "发送格式化帮助失败...\n" + plain_text_help
                )
            except Exception as fallback_e:
                logger.error(f"发送纯文本帮助也失败: {fallback_e}")


# --- 审核命令处理器 ---
async def ok_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理 /ok 命令"""
    (
        editor,
        submission_key,
        submission_info,
        sender_id,
        original_msg_id,
        comment,
    ) = await handle_review_command(update, context, "ok")
    if not editor or not submission_info:
        return

    reply_to_msg = update.message.reply_to_message  # 获取被回复的投稿消息
    logger.info(f"审稿人 {editor.name} 准备使用 /ok 处理稿件 {submission_key}")
    post_result = await post_submission(
        context, reply_to_msg, editor, submission_info, comment
    )

    if post_result:
        submission_type = submission_info.get("type", "未知")
        confirmation_text = f"✅ 稿件已作为 '{submission_type}' 类型发布。"
        is_text_or_sticker = reply_to_msg.text or reply_to_msg.sticker
        if comment and not is_text_or_sticker:
            confirmation_text += " 评论已附加。"
        elif comment:
            confirmation_text += " 评论已作为回复发送。"
        await update.message.reply_text(confirmation_text)
    else:
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
    """处理 /re 命令"""
    (
        editor,
        submission_key,
        submission_info,
        sender_id,
        original_msg_id,
        reply_text,
    ) = await handle_review_command(update, context, "re")
    if not editor or not submission_info:
        return
    if not reply_text:
        await update.message.reply_text(
            "❌ 请输入要回复的内容：<code>/re <回复内容></code>",
            parse_mode=ParseMode.HTML,
        )
        return

    context.user_data["reply_session_target_id"] = sender_id
    context.user_data["reply_session_original_msg_id"] = original_msg_id
    success = await reply_to_submitter(
        context, sender_id, original_msg_id, reply_text, editor
    )
    if success:
        await update.message.reply_text(
            f"✉️ 已向用户 {sender_id} 发送回复，并进入回复模式...\n使用 /unre 结束。"
        )
    else:
        await update.message.reply_text(
            f"❌ 回复用户 {sender_id} 失败，未进入回复模式。"
        )
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
    if not update.message or not update.message.from_user:
        return

    message = update.message
    editor = message.from_user

    reply_target_id = context.user_data.get("reply_session_target_id")
    reply_original_msg_id = context.user_data.get("reply_session_original_msg_id")

    if reply_target_id:
        if reply_target_id in get_blocked_users():
            await message.reply_text(
                f"⚠️ 无法继续回复，用户 {reply_target_id} 已被阻止。请使用 /unre。"
            )
            return

        text_content = message.text
        if not text_content and message.effective_attachment:
            await message.reply_text(
                "ℹ️ 回复模式下暂不支持直接发送媒体文件，请使用文字回复。"
            )
            return
        elif not text_content:
            logger.debug("忽略空的 /re 会话消息")
            return

        success = await reply_to_submitter(
            context, reply_target_id, reply_original_msg_id, text_content, editor
        )
        if not success:
            # 发送失败时给审稿人提示
            await message.reply_text(
                "⚠️ (消息发送给用户失败，可能已被对方阻止)",
                quote=False,
                disable_notification=True,
            )
        # else: # 成功时可以不提示，避免刷屏
        #     await message.reply_text("✅ (已发送)", quote=False, disable_notification=True)
        return

    # 忽略非 /re 会话中的普通消息
    logger.debug(
        f"忽略审核群中的普通消息: {message.text[:50] if message.text else '<非文本>'}"
    )


# --- 审核群按钮回调处理器 ---
async def handle_review_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理审核群中按钮的回调"""
    query = update.callback_query
    if not query or not query.data or not query.message:
        return
    await query.answer()

    user = query.from_user
    message = query.message

    if not message.reply_to_message:
        logger.warning(
            f"审稿群按钮回调，但按钮消息未回复任何消息。Callback data: {query.data}"
        )
        try:
            await query.edit_message_text("❌ 操作失败：按钮状态错误。")
        except TelegramError:
            pass
        return

    original_submission_msg = message.reply_to_message
    logger.info(
        f"处理审核群回调，按钮消息 ID: {message.message_id}, 回复的消息 ID: {original_submission_msg.message_id}"
    )
    # --- 添加日志：打印 ID ---
    logger.info(
        f"--- handle_review_callback - BEFORE query - submission_list ID: {id(data_manager.submission_list)} ---"
    )
    # --- 修正调用点：传入 context ---
    (
        submission_key,
        submission_info,
        sender_id,
        original_msg_id,
    ) = await get_submission_details(message, context)
    # ------------------------------

    if not submission_info:
        logger.warning(
            f"审稿群按钮回调，但投稿信息 {submission_key} 不存在。Callback data: {query.data}"
        )
        try:
            await query.edit_message_text(f"❌ 操作失败：找不到该投稿记录。")
        except TelegramError:
            pass
        return

    if submission_info.get("posted", False):
        status_text = submission_info.get("status", "已处理")
        await query.answer(f"该投稿已被处理 (状态: {status_text})。")
        return

    if not sender_id:
        logger.error(f"无法处理审稿群按钮回调 {query.data}：缺少有效的投稿人 ID。")
        try:
            await query.edit_message_text("❌ 操作失败：缺少投稿人信息。")
        except TelegramError:
            pass
        return

    if sender_id in get_blocked_users():
        await query.answer(
            f"⚠️ 操作失败：投稿人 {sender_id} 已被阻止。", show_alert=True
        )
        return

    editor = user

    if query.data == "receive:real":
        logger.info(f"审稿人 {editor.name} 点击按钮采用稿件 {submission_key} (实名)")
        if submission_info.get("type") == "real":
            await post_submission(
                context, original_submission_msg, editor, submission_info, comment=None
            )
        else:
            await query.answer("⚠️ 按钮类型与记录不符，建议用命令。", show_alert=True)

    elif query.data == "receive:anonymous":
        logger.info(f"审稿人 {editor.name} 点击按钮采用稿件 {submission_key} (匿名)")
        if submission_info.get("type") == "anonymous":
            await post_submission(
                context, original_submission_msg, editor, submission_info, comment=None
            )
        else:
            await query.answer("⚠️ 按钮类型与记录不符，建议用命令。", show_alert=True)

    elif query.data == "reject:submission":
        logger.info(f"审稿人 {editor.name} 点击按钮拒绝稿件 {submission_key}")
        await reject_submission(
            context, submission_key, submission_info, editor, reason=None
        )

    else:
        logger.warning(f"收到未知的审稿群回调数据: {query.data}")
