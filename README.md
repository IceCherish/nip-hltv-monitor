# NIP 情报监控器（0 元方案）

这个项目每 6 分钟自动检查一次：

- HLTV 全站新闻（NIP 相关新闻会单独标记）
- Ninjas in Pyjamas 新赛程和改期
- NIP 阵容/转会动态
- NIP 比赛开始前约 30 分钟提醒

不需要购买服务器，电脑关机也能运行。代码使用 GitHub 公开仓库的免费 Actions，数据读取通过 HLTV 官方 RSS 和 Jina Reader 的免密钥免费基础额度完成。

> 暂时不包含 NIP 官推。GitHub 的定时任务不是实时系统，繁忙时可能晚几分钟执行。

## 最简单的免费通知：ntfy

1. 随机想一个别人猜不到的英文主题，例如 `nip-a8f3c2-yourname`。
2. 手机安装免费的 **ntfy** App，或者浏览器打开 `https://ntfy.sh/你的主题`，订阅这个主题。
3. 在 GitHub 仓库打开 **Settings → Secrets and variables → Actions → New repository secret**。
4. 名称填 `NTFY_TOPIC`，内容填刚才的主题。

主题相当于收件地址，不要写进公开代码，也不要使用简单的 `nip`。

## 上传到 GitHub 并启动

1. 在 GitHub 新建一个 **Public** 仓库，名字可用 `nip-hltv-monitor`，不要勾选自动创建 README。
2. 把本文件夹的内容上传到仓库。
3. 按上面的步骤添加 `NTFY_TOPIC`。
4. 打开仓库的 **Actions → NIP 情报监控 → Run workflow**，先手动运行一次。
5. 收到“监控已启动”后就完成了。以后 GitHub 会自动运行，你的电脑可以关机。

第一次正常运行只会记录当前已有内容并发送启动消息，不会把历史新闻全部轰炸一遍。

## 可选设置

在 **Settings → Secrets and variables → Actions → Variables** 可以添加：

| 名称 | 默认值 | 作用 |
| --- | --- | --- |
| `NEWS_MODE` | `all` | `all` 推送全部 HLTV 新闻；`nip` 只推 NIP；`off` 关闭新闻 |
| `REMINDER_MINUTES` | `30` | 提前多少分钟提醒比赛 |

如果不想使用 ntfy，也支持免费的 Telegram 或邮箱通知：

- Telegram Secrets：`TELEGRAM_BOT_TOKEN`、`TELEGRAM_CHAT_ID`
- 邮箱 Secrets：`SMTP_HOST`、`SMTP_PORT`、`SMTP_USERNAME`、`SMTP_PASSWORD`、`NOTIFY_EMAIL`

可以同时配置多个渠道，同一条提醒会一起发送。

## 本机测试

无需安装第三方 Python 包：

```powershell
python -m unittest discover -s tests -v
python monitor.py
```

配置通知环境变量后，可以只测试通知：

```powershell
python monitor.py --test-notification
```

## 需要知道的限制

- HLTV 会拦截普通程序访问，所以项目使用 Jina Reader 免费阅读层；第三方服务或网页结构变化时，任务可能暂时失败。
- 每次成功运行后，Actions 会把 `data/state.json` 的去重记录提交回仓库，避免重复通知。
- GitHub 可能在公开仓库连续 60 天没有任何活动时自动停用定时工作流；届时在 Actions 页面重新启用即可。
- 数据仅用于个人、非商业提醒，通知会附带 HLTV 原文链接。

