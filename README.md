# ChongLangTouGaoBot - Telegram 投稿机器人

![ChongLangTouGaoBot](https://socialify.git.ci/hyuank/ChongLangTouGaoBot/image?custom_description=&description=1&font=Source+Code+Pro&forks=1&issues=1&language=1&name=1&owner=1&pattern=Brick+Wall&pulls=1&stargazers=1&theme=Auto)

[![License](https://img.shields.io/github/license/hyuank/ChongLangTouGaoBot?logo=apache)](https://github.com/hyuank/ChongLangTouGaoBot/blob/main/LICENSE)
![GitHub Repo stars](https://img.shields.io/github/stars/hyuank/ChongLangTouGaoBot?style=flat&logo=github)
![GitHub forks](https://img.shields.io/github/forks/hyuank/ChongLangTouGaoBot?style=flat&logo=github)
[![GitHub Download](https://img.shields.io/github/downloads/hyuank/ChongLangTouGaoBot/total?logo=github)](https://img.shields.io/github/v/release/hyuank/ChongLangTouGaoBot)
[![GitHub Release](https://img.shields.io/github/v/release/hyuank/ChongLangTouGaoBot?logo=github)](https://github.com/hyuank/ChongLangTouGaoBot/releases)
[![GitHub Release](https://img.shields.io/github/v/release/hyuank/ChongLangTouGaoBot?include_prereleases&label=pre-release&logo=github)](https://github.com/hyuank/ChongLangTouGaoBot/releases)
![GitHub last commit](https://img.shields.io/github/last-commit/hyuank/ChongLangTouGaoBot?logo=github)

## 一个为浪人新闻 [@chonglangtv_rebuild](https://t.me/chonglangtv_rebuild) 开发的 Telegram 投稿机器人

冲浪投稿机器人: [@chonglangtougao_bot](https://t.me/chonglangtougao_bot)

基于 `python-telegram-bot` v22 开发的异步投稿机器人，允许用户向机器人私聊投稿，由指定审核群的权蛆进行审核，并通过机器人发布到指定频道。

## 环境需求

- Python 3.8+ (推荐 3.10+)
- 依赖见 `requirements.txt` (`python-telegram-bot[job-queue, webhooks]` `uvicorn`)

## 部署与配置

1.  **克隆仓库**:

    ```bash
    git clone https://github.com/hyuank/ChongLangTouGaoBot.git
    cd ChongLangTouGaoBot
    ```

2.  **创建并激活虚拟环境**:

    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    ```

3.  **安装依赖**:

    ```bash
    pip install -r requirements.txt
    ```

4.  **配置 `config.json`**:

    - 需要填写必填信息：
      ```json
      {
        "Token": "YOUR_BOT_TOKEN", // 从 BotFather 获取 (必填)
        "Admin": 123456789, // 你的 Telegram User ID (机器人权蛆) (必填)
        "Group_ID": 0, // 审稿群组 ID (初始为 0，启动后由权蛆在群内使用 /setgroup 设置)
        "Publish_Channel_ID": "", // 发布频道 ID 或用户名 (初始为空，启动后由权蛆使用 /setchannel 设置)
        "ID": 0, // Bot ID (无须填写，机器人自动记录)
        "Username": "", // Bot 用户名 (无需填写，机器人自动记录)
        "TestMode": true, // 是否启用测试模式 (true: 使用轮询, false: 使用Webhook, 默认 true)
        "WebhookURL": "https://example.com", // Webhook URL (TestMode 为 false 时必填, 必须是 HTTPS 地址)
        "WebhookSecretToken": "", // Webhook密钥 (可选，但强烈建议在生产环境设置，可以通过运行 token_urlsafe.py 进行生成)
        "ListenAddress": "0.0.0.0", // Webhook监听地址 (默认 0.0.0.0)
        "ListenPort": 8443, // Webhook监听端口 (默认 8443)
        "EnableFooter": true, // 是否启用小尾巴 (默认 true)
        "ChatLink": "", // 小尾巴中的"聊天"链接 (可选)
        "BlockedUsers": [], // 黑名单用户 ID 列表 (默认为空)
        "WarningUsers": {}, // 被警告用户及其次数 (默认为空)
        "FooterEmojis": {
          "submission": "👊", // 投稿小尾巴链接前的Emoji
          "channel": "🌊", // 频道小尾巴链接前的Emoji
          "chat": "🔥" // 聊天小尾巴链接前的Emoji
        }
      }
      ```
    - **重要**: 如果使用 Webhook 模式，请注意：
      - `WebhookURL` 是一个有效的、可以通过公网访问并绑定好服务器的 **HTTPS** 域名。（我个人是使用 1panel 自带的 OpenResty 一键绑定在 Python 运行环境中运行的机器人）
      - 部分云服务器的防火墙策略需要用户手动放行 `监听端口(ListenPort)` 。

5.  **配置 Supervisor 守护进程 (可选，仅供参考，配置针对轮询模式，如果选择 Webhook 模式可能需要进行修改)**:

    - 创建 Supervisor 配置文件：

    ```ini
    [program:ChongLangTouGaoBot] ; 自定义程序名称
    command                 = /root/ChongLangTouGaoBot/.venv/bin/python /root/ChongLangTouGaoBot/bot.py ; 根据个人需要修改
    directory               = /root/ChongLangTouGaoBot ; 根据个人需要修改
    autostart               = true ; 开机自启
    startsecs               = 3
    stdout_logfile          = /opt/log/ChongLangTouGaoBot.out.log ; 根据个人需要修改
    stderr_logfile          = /opt/log/ChongLangTouGaoBot.err.log ; 根据个人需要修改
    stdout_logfile_maxbytes = 2MB ; 日志大小上限，根据个人需要修改
    stderr_logfile_maxbytes = 2MB ; 日志大小上限，根据个人需要修改
    user                    = root ; 根据个人需要修改，确保有运行目录的读写权限
    priority                = 999
    numprocs                = 1 ; 建议设置为1，否则有玄学问题
    process_name            = %(program_name)s_%(process_num)02d
    ```

    - 加载配置并启动:

    ```bash
    sudo supervisorctl reread
    sudo supervisorctl update
    sudo supervisorctl start ChongLangTouGaoBot
    sudo supervisorctl status ChongLangTouGaoBot
    ```

6.  **初始化设置**:
    - 启动机器人后，权蛆需要将机器人添加到**稿件审核群**后发送 `/setgroup` 命令。
    - 权蛆通过私聊或在任意群组发送 `/setchannel @频道用户名` 或 `/setchannel 频道数字ID（通常以-100开头）` 命令设置发布频道。
    - 确保机器人已被添加到稿件审核群和稿件发布频道，并在稿件发布频道拥有权蛆权限。

## 功能特性

- [x] 支持轮询和 Webhook 两种运行模式
- [x] 支持文字、图片、视频、动画 (GIF)、音频、语音、文件、贴纸投稿
- [x] 支持媒体组（多图/视频）投稿处理
- [x] 支持剧透遮罩 (Spoiler) 媒体保留遮罩效果
- [x] 支持用户选择匿名或保留来源（实名）投稿
- [x] 支持在频道稿件末尾添加小尾巴，格式：(自定义 emoji)投稿 (自定义 emoji)频道 (自定义 emoji)聊天，可自定义聊天群链接（频道链接不支持私密频道）和 emoji
- [x] 审稿黑奴可以通过**命令**或**按钮**审核稿件
  - [x] `/ok (评论)`: 采纳并通过机器人发布（可选评价）
  - [x] `/no (理由)`: 拒绝稿件（可选理由）
  - [x] `/re (内容)`: 进入回复模式，直接与投稿人对话。之后您发送的普通消息将自动转发给该用户，直到使用 /unre
  - [x] `/echo (内容)`: 直接向投稿人发送单条消息，不进入回复模式
  - [x] `/unre`: 退出当前的回复模式
  - [x] `/warn (理由)`: 警告用户 (3 次警告自动封禁)
  - [x] `/ban`: 回复稿件将投稿人加入黑名单
  - [x] `/unban`: 回复稿件将投稿人移出黑名单
- [x] 权蛆功能
  - [x] `/setgroup`: 在群内设置审核群组
  - [x] `/setchannel`: 设置发布频道
  - [x] `/setchatlink`: 设置小尾巴中的“聊天”链接
  - [x] `/setemoji`: 设置小尾巴链接前的 Emoji
  - [x] `/status`: 查看机器人配置和状态
- [x] 向投稿人发送审核结果通知
  - [x] 若通过则通知会包含跳转到对应频道消息的链接
  - [x] 若拒绝则可包含理由（如果有的话）
- [x] 低性能要求，无数据库依赖
- [x] 用户投稿采用异步处理，实现同一用户同时投稿多个内容

## TODO / 未来计划

- [x] 已知 bug
  - [x] 暂无
- [ ] 对/ban 封禁用户进行管理，使用 ID 进行解封
- [ ] 自定义拒绝理由模板

## 许可证

本项目基于 [Apache-2.0](https://github.com/hyuank/ChongLangTouGaoBot/blob/main/LICENSE) 许可证开源。

## 鸣谢

- [python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)
