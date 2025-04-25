# bot.py

"""Telegram Submission Bot - Main Entry Point"""

import logging
import sys
import warnings
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
    PicklePersistence,
)
from telegram.error import InvalidToken, TelegramError

# --- 统一导入自定义模块 ---
try:
    from constants import Version_Code
    import config_loader
    import data_manager
    import posting

    # --- 修改导入路径 ---
    from handlers.command import handle_general_commands
    from handlers.submission.message import handle_private_message
    from handlers.submission.callback import handle_submission_callback

    # jobs 模块不需要直接导入 handler，它被 message 模块内部使用
    # from handlers.submission.jobs import ...
    from handlers.review import (
        handle_review_group_message,
        handle_review_callback,
        pwshelp_command,
        ok_command,
        no_command,
        re_command,
        echo_command,
        ban_command,
        unban_command,
        unre_command,
        warn_command,
    )

    # --------------------

except ImportError as e:
    # 在记录日志前退出可能看不到日志，先打印错误
    print(f"FATAL: 启动失败 - 无法导入必要的模块或函数: {e}", file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"FATAL: 启动失败 - 初始化模块时发生错误: {e}", file=sys.stderr)
    sys.exit(1)

# --- 设置日志记录 (在所有导入之后) ---
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s"
)
logging.getLogger("httpx").setLevel(logging.WARNING)  # 减少 httpx 的日志量
logger = logging.getLogger(__name__)
# ---------------------

# --- 定义 ASCII Art ---
ASCII_ART = r"""
   ____ _                       _                     _____            ____                ____        _   
  / ___| |__   ___  _ __   __ _| |    __ _ _ __   __ |_   _|__  _   _ / ___| __ _  ___    | __ )  ___ | |_ 
 | |   | '_ \ / _ \| '_ \ / _` | |   / _` | '_ \ / _` || |/ _ \| | | | |  _ / _` |/ _ \   |  _ \ / _ \| __|
 | |___| | | | (_) | | | | (_| | |__| (_| | | | | (_| || | (_) | |_| | |_| | (_| | (_) |  | |_) | (_) | |_ 
  \____|_| |_|\___/|_| |_|\__, |_____\__,_|_| |_|\__, ||_|\___/ \__,_|\____|\__,_|\___/___|____/ \___/ \__|
                          |___/                  |___/                               |_____|               
"""
# 使用原始字符串 (r"...") 避免转义问题
# --------------------


# --- Bot 初始化回调 ---
async def post_init(application: Application):
    """在机器人启动后执行的初始化操作"""
    try:
        me = await application.bot.get_me()
        # 更新配置中的 Bot 信息 (如果需要保存到文件，config_loader 内部处理)
        config_loader.update_config("ID", me.id)
        username = f"@{me.username}" if me.username else f"Bot (ID: {me.id})"
        config_loader.update_config("Username", username)

        # --- 根据模式显示不同的启动信息 ---
        test_mode = config_loader.is_test_mode()
        mode_str = "测试模式 (Polling)" if test_mode else "生产模式 (Webhook)"
        logger.info(f"机器人启动成功！模式: {mode_str}")
        logger.info(f"ID: {me.id}, Username: {username}")
        # -----------------------------------

        logger.info(f"版本: {Version_Code}")
        logger.info(f"权蛆 ID: {config_loader.get_admin_id() or '未设置'}")
        logger.info(f"审稿群组 ID: {config_loader.get_group_id() or '未设置'}")
        logger.info(
            f"发布频道 ID: {config_loader.get_publish_channel_id() or '未设置'}"
        )

        # --- Webhook 设置 (仅在生产模式下) ---
        if not test_mode:
            webhook_url = config_loader.get_webhook_url()
            secret_token = config_loader.get_webhook_secret_token()
            if webhook_url:
                logger.info(f"Webhook URL: {webhook_url}")
                logger.info(
                    f"Webhook Secret Token: {'已设置' if secret_token else '未设置'}"
                )
                try:
                    await application.bot.set_webhook(
                        url=webhook_url,
                        allowed_updates=Update.ALL_TYPES,  # 接收所有更新类型
                        secret_token=secret_token or None,  # 如果为空字符串则不设置
                    )
                    webhook_info = await application.bot.get_webhook_info()
                    if webhook_info.url == webhook_url:
                        logger.info(f"Webhook 设置成功！当前指向: {webhook_info.url}")
                    else:
                        logger.error(
                            f"Webhook 设置失败！当前指向: {webhook_info.url} (预期: {webhook_url})"
                        )
                        # 可以在这里添加退出逻辑或进一步处理
                        # sys.exit(1)
                except TelegramError as e:
                    logger.error(f"设置 Webhook 时发生错误: {e}", exc_info=True)
                    # 也可以考虑退出
                    # sys.exit(1)
            else:
                # 理论上 config_loader 已经检查过，这里是双重保险
                logger.error("生产模式下 WebhookURL 未设置，无法启动 Webhook 服务！")
                # sys.exit(1)
        # ----------------------------------------

        # 启动时检查配置完整性
        admin_id = config_loader.get_admin_id()
        group_id = config_loader.get_group_id()
        channel_id = config_loader.get_publish_channel_id()

        if not admin_id or admin_id == 0:  # 检查是否为默认值 0
            logger.warning("配置文件中未设置有效的 'Admin' (权蛆用户 ID)")
        if not group_id or group_id == 0:  # 检查是否为默认值 0
            logger.warning(
                "配置文件中未设置 'Group_ID' (审稿群组 ID)，请权蛆在群组中使用 /setgroup 命令设置"
            )
        if not channel_id:
            logger.warning(
                "配置文件中未设置 'Publish_Channel_ID' (发布频道 ID)，请权蛆使用 /setchannel 命令设置"
            )

        # 尝试给权蛆发送启动成功的消息
        if admin_id and admin_id != 0:
            try:
                await application.bot.send_message(
                    chat_id=admin_id,
                    text=f"✅ 机器人已成功启动！\n模式: {mode_str}\n版本: {Version_Code}\nUsername: {username}",
                )
            except TelegramError as e:
                # 例如，如果用户阻止了机器人，会抛出 Forbidden 错误
                logger.warning(f"启动时向权蛆 {admin_id} 发送通知失败: {e}")
            except Exception as e:  # 其他可能的错误
                logger.warning(f"启动时向权蛆发送通知时发生未知错误: {e}")

    except Exception as e:
        # 使用 exc_info=True 记录详细错误堆栈
        logger.error(f"执行 post_init 时出错: {e}", exc_info=True)
        # raise e # 如果 post_init 失败则不应启动 Bot


# --- 错误处理器 ---
async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE):
    """记录和处理 PTB 内部错误，并尝试通知权蛆"""
    logger.error(f"处理更新时发生错误: {context.error}", exc_info=context.error)

    admin_id = config_loader.get_admin_id()
    if admin_id and admin_id != 0:
        # 仅尝试发送 Telegram API 错误通知，避免发送过多其他类型错误
        if isinstance(context.error, TelegramError):
            try:
                update_str = f"Update: {update}" if update else "N/A"
                # 限制消息长度，避免超过 Telegram 限制
                error_text = (
                    f"🆘 机器人发生 Telegram API 错误:\n"
                    f"Error: {context.error}\n"
                    f"{update_str[:1000]}\n"  # 截断 Update 信息
                    f"请检查日志获取详细信息。"
                )
                await context.bot.send_message(
                    chat_id=admin_id, text=error_text[:4000]
                )  # 再次截断总信息
            except Exception as e:
                logger.error(f"发送错误通知给权蛆失败: {e}")


# --- 主函数 ---
def main():
    """主函数，设置并运行机器人"""

    # --- 在日志配置后，主逻辑开始前打印 ASCII Art ---
    logger.info(ASCII_ART)
    # ---------------------------------------------

    logger.info(f"===== 正在启动 Submission Bot {Version_Code} =====")

    # --- 配置和 Token/Admin ID 检查 (保持不变) ---
    token = config_loader.get_token()
    if not token or token == "YOUR_BOT_TOKEN":
        logger.error(
            "错误：配置文件 config.json 中未设置有效的 'Token'。请填写后重新运行。"
        )
        sys.exit(1)
    admin_id = config_loader.get_admin_id()
    if not admin_id or admin_id == 0:
        logger.error(
            "错误：配置文件 config.json 中未设置有效的 'Admin' 用户 ID。请填写后重新运行。"
        )
        sys.exit(1)

    # --- 配置持久化 ---
    # 使用 PicklePersistence 保存 bot_data, chat_data, user_data
    persistence_file = config_loader.PATH + "bot_persistence.pkl"
    persistence = PicklePersistence(filepath=persistence_file)
    logger.info(f"将使用持久化文件: {persistence_file}")

    application = None
    try:
        application = (
            ApplicationBuilder()
            .token(token)
            .persistence(persistence)  # 添加持久化
            .post_init(post_init)  # 注册启动后回调
            .build()
        )

        # --- 注册处理器 ---
        # 优先级组 (group 越小优先级越高)
        # GROUP_CALLBACK: 最高优先级，处理按钮回调
        # GROUP_COMMANDS: 处理命令
        # GROUP_MESSAGES: 最低优先级，处理普通消息
        GROUP_CALLBACK = 1
        GROUP_COMMANDS = 2
        GROUP_MESSAGES = 3

        # 1. 回调查询处理器 (高优先级)
        #    处理来自用户私聊的按钮回调 (单条或媒体组)
        #    匹配格式: <type>:<prefix>:<identifier>(:<check_id>)
        #    type: real (实名), anon (匿名), cancel (取消)
        #    prefix: sm (单消息), mg (媒体组)
        submission_callback_pattern = r"^(real|anon|cancel):(sm|mg):"
        application.add_handler(
            CallbackQueryHandler(
                handle_submission_callback, pattern=submission_callback_pattern
            ),
            group=GROUP_CALLBACK,
        )
        #    处理来自审核群的按钮回调
        #    匹配格式: receive:real, receive:anonymous, reject:submission
        review_callback_pattern = (
            r"^(receive:real|receive:anonymous|reject:submission)$"
        )
        application.add_handler(
            CallbackQueryHandler(
                handle_review_callback, pattern=review_callback_pattern
            ),
            group=GROUP_CALLBACK,
        )

        # 2. 定义并注册命令处理器
        #    通用命令和权蛆命令 (可在私聊或群组使用)
        application.add_handler(
            CommandHandler(
                [
                    "start",
                    "version",
                    "status",
                    "setgroup",  # 权蛆设置审核群
                    "setchannel",  # 权蛆设置发布频道
                    "setchatlink",  # 权蛆设置小尾巴中的"聊天"链接
                    "setemoji",  # 权蛆设置小尾巴中的 Emoji
                    "about",
                    "help",
                ],
                handle_general_commands,
            ),
            group=GROUP_COMMANDS,
        )
        #    审核群帮助命令 (仅在群组生效)
        application.add_handler(
            CommandHandler("pwshelp", pwshelp_command, filters=filters.ChatType.GROUPS),
            group=GROUP_COMMANDS,
        )
        #    审核群操作命令 (仅在群组生效)
        review_cmd_filters = filters.ChatType.GROUPS
        application.add_handler(
            CommandHandler("ok", ok_command, filters=review_cmd_filters),  # 通过投稿
            group=GROUP_COMMANDS,
        )
        application.add_handler(
            CommandHandler("no", no_command, filters=review_cmd_filters),  # 拒绝投稿
            group=GROUP_COMMANDS,
        )
        application.add_handler(
            CommandHandler("re", re_command, filters=review_cmd_filters),  # 回复投稿人
            group=GROUP_COMMANDS,
        )
        application.add_handler(
            CommandHandler(
                "echo", echo_command, filters=review_cmd_filters
            ),  # 在群内回显消息（调试用）
            group=GROUP_COMMANDS,
        )
        application.add_handler(
            CommandHandler("ban", ban_command, filters=review_cmd_filters),  # 封禁用户
            group=GROUP_COMMANDS,
        )
        application.add_handler(
            CommandHandler(
                "unban", unban_command, filters=review_cmd_filters
            ),  # 解封用户
            group=GROUP_COMMANDS,
        )
        application.add_handler(
            CommandHandler(
                "unre", unre_command, filters=review_cmd_filters
            ),  # 结束回复会话
            group=GROUP_COMMANDS,
        )
        application.add_handler(
            CommandHandler(
                "warn", warn_command, filters=review_cmd_filters
            ),  # 警告命令
            group=GROUP_COMMANDS,
        )

        # 3. 定义并注册消息处理器 (优先级较低)
        #    私聊消息处理器 (处理用户发起的投稿)
        #    过滤掉命令消息，只处理私聊中的普通消息
        private_msg_filters = (
            filters.ChatType.PRIVATE & ~filters.COMMAND & filters.UpdateType.MESSAGE
        )
        application.add_handler(
            MessageHandler(private_msg_filters, handle_private_message),
            group=GROUP_MESSAGES,
        )

        #    群聊非命令消息处理器 (主要用于处理 /re 回复会话)
        #    过滤掉命令消息，只处理群组中的普通消息
        group_msg_filters = (
            filters.ChatType.GROUPS & ~filters.COMMAND & filters.UpdateType.MESSAGE
        )
        application.add_handler(
            MessageHandler(group_msg_filters, handle_review_group_message),
            group=GROUP_MESSAGES,
        )

        # 注册错误处理器
        application.add_error_handler(error_handler)

        # --- 根据 TestMode 选择启动方式 ---
        if config_loader.is_test_mode():
            # 测试模式：启动轮询
            logger.info("检测到测试模式，将使用 Polling 启动...")
            application.run_polling(allowed_updates=Update.ALL_TYPES)
        else:
            # 生产模式：启动 Webhook
            webhook_url = config_loader.get_webhook_url()
            listen_address = config_loader.get_listen_address()
            port = config_loader.get_listen_port()
            secret_token = config_loader.get_webhook_secret_token()

            logger.info("检测到生产模式，将使用 Webhook 启动...")
            logger.info(f" - Webhook URL: {webhook_url}")
            logger.info(f" - 监听地址: {listen_address}")
            logger.info(f" - 监听端口: {port}")
            logger.info(f" - Secret Token: {'已设置' if secret_token else '未设置'}")

            # 启动内建的 Webhook 服务器
            # 注意：这需要你的环境安装了 uvicorn 和 httpx[http2] (或者相应的依赖)
            # PTB 会自动处理这些依赖
            application.run_webhook(
                listen=listen_address,
                port=port,
                secret_token=secret_token or None,  # 如果为空字符串则不传
                webhook_url=webhook_url,  # 可选，但建议提供以便 PTB 验证
                allowed_updates=Update.ALL_TYPES,
            )

        logger.info("机器人主循环已停止。")
        # -----------------------------------

    # --- 异常处理和 finally 块 ---
    except InvalidToken:
        logger.error("错误：无效的 Bot Token。请检查 config.json 中的 'Token'。")
        sys.exit(1)
    except TelegramError as e:
        # 例如网络问题或 Telegram 服务器问题
        logger.error(f"连接 Telegram 时发生错误: {e}", exc_info=True)
        sys.exit(1)
    except Exception as e:
        # 捕获其他所有未预料到的异常
        logger.error(f"启动或运行机器人时发生未处理的异常: {e}", exc_info=True)
        sys.exit(1)
    finally:
        # 确保在程序退出前（无论正常或异常）尝试保存数据
        logger.info("尝试在退出前最后一次保存数据...")
        try:
            # 保存投稿数据 (data.json)
            data_manager.save_data_sync()
            # 保存配置数据 (config.json, 主要是黑名单和警告用户)
            config_loader.save_config_sync()
            # --- Webhook 清理 (仅生产模式) ---
            if application and not config_loader.is_test_mode():
                logger.info("尝试删除 Webhook 设置...")
                # 使用 run_until_complete 确保异步操作在退出前完成
                import asyncio

                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        # 如果事件循环仍在运行 (例如正常关闭)
                        loop.create_task(application.bot.delete_webhook())
                    else:
                        # 如果事件循环已停止 (例如异常退出)
                        asyncio.run(application.bot.delete_webhook())
                    logger.info("Webhook 已成功删除。")
                except TelegramError as e:
                    logger.warning(f"删除 Webhook 时出错: {e}")
                except Exception as e:
                    logger.error(f"删除 Webhook 时发生未知错误: {e}", exc_info=True)
            # -----------------------------------
        except Exception as e:
            logger.error(f"退出时保存数据、配置或清理 Webhook 失败: {e}", exc_info=True)
        logger.info("===== Submission Bot 已停止 =====")


if __name__ == "__main__":
    main()
