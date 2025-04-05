# handlers/command.py

import logging
import telegram  # 需要导入 telegram 以获取 __version__
from telegram import Update
from telegram.ext import ContextTypes
from telegram.constants import ParseMode  # 确保导入
from telegram.error import TelegramError

from constants import Version_Code
from config_loader import (
    CONFIG,
    get_group_id,
    get_publish_channel_id,
    get_admin_id,
    update_config,
    save_config_async,
    get_blocked_users,
)
from data_manager import get_pending_submission_count

logger = logging.getLogger(__name__)

# --- 定义详细帮助文本 ---
HELP_TEXT = """
<b>欢迎使用投稿机器人！</b>

➡️ 请直接向我发送您想投稿的内容 (文字、图片、音频、视频、文件等)。
➡️ 您也可以转发消息给我来进行投稿。
➡️ 机器人会询问您希望保留来源（实名）还是匿名发送。

<b>可用命令:</b>
<code>/start</code> - 显示欢迎信息。
<code>/help</code> - 显示此详细帮助信息。
<code>/version</code> - 显示机器人版本。
<code>/about</code> - 显示机器人信息。
"""

ADMIN_HELP_TEXT = """
<b>权蛆命令:</b>
<code>/status</code> - 显示机器人当前配置状态。
<code>/setgroup</code> - (在目标群组内使用) 将当前群组设置为审稿群。
<code>/setchannel ID或用户名</code> - 设置发布频道 (例如: <code>@channel_name</code> 或 <code>-100123...</code>)。

<b>审核群指令:</b>
(请在审核群内使用 <code>/pwshelp</code> 获取详细指令)
"""
# -------------------------


async def handle_general_commands(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """处理通用命令、权蛆命令和帮助命令"""
    if not update.message or not update.message.text or not update.message.from_user:
        return

    message = update.message
    user = message.from_user
    command_text = message.text.lower()
    command_parts = command_text.split()
    command = command_parts[0].split("@")[0][1:]

    # --- 通用命令 ---
    if command == "start":
        await message.reply_text(
            "欢迎使用投稿机器人！\n"
            "请直接向我发送您想投稿的内容 (文字、图片、音频、视频、文件等)。\n"
            "命令帮助: /help"
        )
        return
    # -----------------------

    elif command == "help":
        base_help = HELP_TEXT
        admin_id_local = get_admin_id()
        # 如果是权蛆，追加权蛆帮助信息
        if user.id == admin_id_local:
            base_help += "\n" + ADMIN_HELP_TEXT
        await message.reply_text(
            base_help, parse_mode=ParseMode.HTML, disable_web_page_preview=True
        )
        return
    # ----------------------

    elif command == "version":
        await message.reply_text(
            f"Telegram Submission Bot\n"
            f"版本: {Version_Code}\n"
            f"基于 python-telegram-bot v{telegram.__version__}\n"
            f"源码: https://github.com/hyuank/ChongLangTouGaoBot"
        )
        return
    # ----------------------

    elif command == "about":
        about_text = "Powered by @mao_lain for @chonglangTV_rebuild"
        await message.reply_text(
            about_text, disable_web_page_preview=True
        )  # disable_web_page_preview 避免 @ 用户名被预览
        return
    # ---------------------------

    # --- 权蛆命令 ---
    admin_id_local = get_admin_id()
    if user.id == admin_id_local:
        current_group_id_local = get_group_id()

        if command == "setgroup":
            if message.chat.type in ["group", "supergroup"]:
                update_config("Group_ID", message.chat_id)
                await save_config_async()
                await message.reply_text(
                    f"✅ 已设置本群 ({message.chat.title}) 为审稿群。"
                )
                logger.info(
                    f"权蛆 {user.name} ({user.id}) 已设置审稿群为 {message.chat_id} ({message.chat.title})"
                )
            else:
                await message.reply_text("❌ 此命令只能在群组中使用。")
            return

        if command == "setchannel":
            if len(command_parts) > 1 and (
                command_parts[1].startswith("@")
                or command_parts[1].replace("-", "").isdigit()
            ):
                channel_id_str = command_parts[1]
                try:
                    try:
                        channel_id_to_check = int(channel_id_str)
                    except ValueError:
                        channel_id_to_check = channel_id_str

                    chat = await context.bot.get_chat(chat_id=channel_id_to_check)
                    if chat.type == "channel":
                        update_config("Publish_Channel_ID", channel_id_to_check)
                        await save_config_async()
                        await message.reply_text(
                            f"✅ 已设置发布频道为 {chat.title} ({channel_id_to_check})。请确保机器人是该频道的权蛆！"
                        )
                        logger.info(
                            f"权蛆 {user.name} ({user.id}) 已设置发布频道为 {channel_id_to_check} ({chat.title})"
                        )
                    else:
                        await message.reply_text(
                            f"❌ '{channel_id_str}' 不是一个有效的频道。"
                        )
                except TelegramError as e:
                    await message.reply_text(
                        f"❌ 无法验证频道 '{channel_id_str}'。错误: {e}."
                    )
                except Exception as e:
                    await message.reply_text(f"❌ 验证频道时发生未知错误: {e}")
            else:
                await message.reply_text(
                    "❌ 使用方法: /setchannel @频道用户名 或 /setchannel -100xxxxxxxxxx"
                )
            return

        if command == "status":
            group_info = "未设置"
            if current_group_id_local:
                try:
                    chat = await context.bot.get_chat(current_group_id_local)
                    group_info = f"{chat.title} ({current_group_id_local})"
                except Exception:
                    group_info = f"ID: {current_group_id_local} (无法获取名称)"

            channel_info = "未设置"
            channel_id_local = get_publish_channel_id()  # 使用局部变量
            if channel_id_local:
                try:
                    chat = await context.bot.get_chat(channel_id_local)
                    channel_info = f"{chat.title} ({channel_id_local})"
                except Exception:
                    channel_info = f"ID/Username: {channel_id_local} (无法获取名称)"

            bot_user = await context.bot.get_me()
            await message.reply_text(
                f"⚙️ 当前状态:\n"
                f"Bot ID: {bot_user.id}\n"
                f"Bot Username: @{bot_user.username}\n"
                f"权蛆 ID: {admin_id_local}\n"
                f"审稿群: {group_info}\n"
                f"发布频道: {channel_info}\n"
                f"待处理投稿数: {get_pending_submission_count()}\n"
                f"黑名单用户数: {len(get_blocked_users())}"  # 显示黑名单数量
            )
            return
    # 如果非权蛆尝试权蛆命令
    elif command in ["setgroup", "setchannel", "status"]:
        await message.reply_text("❌ 您无权使用此命令。")
        return
