# NIP / HLTV 中文 QQ 情报机器人

这是一个尽量零成本的个人监控器。它会定时检查：

- HLTV 全站新闻，抓取完整正文并普通翻译成中文
- 按正文顺序发送新闻配图
- 识别新闻正文里的比赛表，换算为北京时间后发送
- NIP 新赛程、改期和赛前提醒
- HLTV Transfers 页面里的 NIP 加入、离队、下放等阵容动态

新闻只读取 HLTV 的 `.newstext-con` 正文区域；评论区、评论头像和评论图片都不会进入解析结果。第一次运行只记录已有内容并发一条启动消息，不会补发全部历史新闻。

## QQ 群发送是怎么工作的

项目通过 NapCat 的 OneBot HTTP 接口，让登录着的 QQ 小号发送群消息。正文会被排成“中文标题 → 中文段落 → 正文图片 → 文中赛程 → 原文链接”，过长新闻会自动分段。

需要明确区分两件事：

- 抓新闻和翻译可以在免费 GitHub Actions 上跑。
- QQ 小号登录必须由 NapCat 常驻维持；GitHub 的临时机器不能替你长期登录 QQ。

所以，最终发 QQ 群有两种现实选择：

1. **真正 0 元**：在自己的 Windows 电脑运行 NapCat 和 `run_forever.py`。电脑开着就工作，关机就停止。
2. **电脑关机也工作**：在一台便宜的国内 Linux 服务器同时运行 NapCat 和本项目。

个人 QQ 自动化不是腾讯官方机器人接口，存在风控、掉线或版本更新后失效的可能。建议只用小号、控制频率，不做刷屏；不要把 OneBot 端口直接暴露到公网。

## 本机接入 NapCat

先在 NapCat WebUI 中登录 QQ 小号，并启用 OneBot 11 的 HTTP 服务。建议监听 `127.0.0.1:3000`，设置一个随机的 Access Token。

PowerShell 中临时配置并测试：

```powershell
$env:QQ_GROUP_ID="你的群号"
$env:ONEBOT_HTTP_URL="http://127.0.0.1:3000"
$env:ONEBOT_ACCESS_TOKEN="和 NapCat 一致的 Token"
python monitor.py --test-notification
python monitor.py --test-rich-article
```

测试成功后常驻运行：

```powershell
python run_forever.py
```

默认每 360 秒检查一次。可用 `CHECK_INTERVAL_SECONDS` 修改，但程序最低限制为 60 秒。

## 国内服务器能不能用

能，运行时并不要求服务器直接打开 HLTV 或 GitHub：

- HLTV 页面由免费的 Jina Reader 阅读层代取，程序访问的是 `r.jina.ai`。
- GitHub 只用于保存/更新源码；把文件上传到服务器后，日常运行不依赖 GitHub。
- 正文图片来自 `img-cdn.hltv.org`。
- 翻译默认使用免费的 Google 非官方端点；国内机器建议配置腾讯云机器翻译。

买服务器前最好先在目标地域测试下面三个地址是否可访问：`r.jina.ai`、`img-cdn.hltv.org`、`tmt.tencentcloudapi.com`。如果前两个也被目标网络阻断，单机方案就不能抓正文和图片，需要改成“GitHub 免费抓取 + 国内服务器只负责 QQ”的中继结构。

最低建议是 **2 核 2 GB、40 GB 盘、Ubuntu 22.04/24.04 x86_64**，并加 1–2 GB swap。Python 监控本身非常轻，内存主要被 Linux QQ/NapCat 占用。1 GB 机器不建议，容易在 QQ 更新或加载页面时被杀；预算允许时 2 核 4 GB 会稳得多。带宽和流量需求很小，普通轻量服务器套餐足够。

服务器环境变量样例在 `deploy/monitor.env.example`，systemd 服务样例在 `deploy/nip-monitor.service.example`。

## 翻译

默认 `TRANSLATE_PROVIDER=auto`：

- 优先使用 Google 的免费非官方接口，不需要密钥，但没有稳定性承诺。
- 同时配置了 `TENCENT_SECRET_ID` 和 `TENCENT_SECRET_KEY` 时，Google 重试失败后会自动切换到腾讯云机器翻译。

可显式设置 `TRANSLATE_PROVIDER=google` 或 `tencent`。腾讯云方案还可设置 `TENCENT_REGION`，默认 `ap-beijing`。密钥只能放环境变量或 GitHub Secrets，绝不能提交进公开仓库。

## NIP 转会动态怎么检测

程序每轮读取 HLTV 的 Transfers 页面，筛出包含 Ninjas in Pyjamas 且带有 transfers、joins、benched、parts ways、moved 等动作的记录，再用“动作文本 + 日期”的哈希与 `data/state.json` 对比。HLTV 新增或修改记录后，下一轮就会发现；它不是凭空预测，也不会比 HLTV 发布更早。

## 免费 GitHub Actions 模式

如果暂时不发 QQ，公开仓库可以每 6 分钟在 GitHub Actions 免费检查，并通过 ntfy、Telegram 或邮箱通知。工作流已经位于 `.github/workflows/monitor.yml`。

可配置的 Secrets：

- `NTFY_TOPIC`
- `TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`
- `SMTP_HOST`、`SMTP_PORT`、`SMTP_USERNAME`、`SMTP_PASSWORD`、`NOTIFY_EMAIL`
- 可选国内翻译：`TENCENT_SECRET_ID`、`TENCENT_SECRET_KEY`

可配置的 Variables：

| 名称 | 默认值 | 作用 |
| --- | --- | --- |
| `NEWS_MODE` | `all` | `all` 全部新闻；`nip` 只发 NIP；`off` 关闭新闻 |
| `REMINDER_MINUTES` | `30` | 赛前多少分钟提醒 |
| `TRANSLATE_PROVIDER` | `auto` | `auto`、`google` 或 `tencent` |
| `TENCENT_REGION` | `ap-beijing` | 腾讯翻译地域 |

公开仓库的标准 GitHub-hosted runner 免费。第一次手动运行后，工作流会把去重状态提交回仓库。

## 测试

项目只使用 Python 标准库，不需要安装第三方包：

```powershell
python -m unittest discover -s tests -v
python monitor.py
```

需要知道的限制：

- HLTV、Jina Reader、翻译服务或网页结构变化时，本轮可能失败，常驻模式会在下轮重试。
- Jina Reader 无密钥基础额度目前足够个人低频监控，但不是本项目控制的服务。
- GitHub 定时任务可能延后几分钟，不是严格实时系统。
- 数据仅用于个人、非商业提醒，消息保留 HLTV 原文链接。
