# NIP / HLTV 中文 QQ 情报机器人

最终推荐架构：

```text
GitHub Actions 抓取、翻译、下载正文图片、去重
        ↓ 带 HMAC-SHA256 签名的单条图文消息 HTTP POST
国内服务器 relay_server.py
        ↓ 只访问 127.0.0.1
NapCat / OneBot
        ↓
QQ群
```

国内服务器不需要访问 HLTV、GitHub、Jina Reader、图片 CDN 或翻译服务，只接收 GitHub 已经整理好的中文文字和图片文件并交给 NapCat。NapCat 的 3000 和 6099 端口不能开放到公网；公网只开放中继端口，默认 8787。

## 当前功能

- 自动读取所有 HLTV 新闻并翻译标题、正文
- 只发送前 10 个正文文字段落，文末始终附原文地址
- 正文相册图片与文字按原文顺序合并为一条 QQ 消息；排除评论区、队标、国旗等装饰图
- 单张图片失败会自动跳过，不影响整篇文字和原文链接；每篇最多 8 张、单张和累计图片均最多 4 MiB，整条图文失败时自动降级为一条纯文字消息
- 提取新闻正文赛程并转换为北京时间
- 展示全部 NIP 未开赛比赛，同一赛事名称只显示一次
- 展示 NIP 最近一个已结束赛事的全部赛果
- NIP 赛前提醒和 HLTV Transfers 阵容动态
- 每次启动常驻程序时立即发送一条完整的 NIP 赛程消息（近期赛程预告 + 往期赛事回顾），并记为当天已发送；持续运行时每天北京时间 10 点检查当天是否发过，没发才补发
- 第一次正式运行发送最新 2 条新闻；之后按 HLTV 新闻编号去重，只发送新增新闻
- 本机直连 NapCat 与 GitHub → 国内服务器中继两种模式
- Google 免费非官方翻译优先，可配置腾讯云故障备用

第一次正式运行依次发送当前近期赛程与最近赛事回顾、最新 2 条新闻，不额外发送启动说明。程序会把已经见过的 HLTV 新闻编号保存到 `data/state.json`，以后重复检查时不会再次发送；如果一次检查发现多条新新闻，会按从旧到新的顺序发送。每篇新闻的标题、前 10 段正文和赛事名会合并批量翻译，显著减少请求次数；某篇翻译临时失败时不会影响赛程或赛前提醒，该篇不会记为已发送，并在下次检查时重试。

## 最终消息样式

```text
📰【HLTV 新闻】

超级德古拉N半决赛设定
━━━━━━━━━━━━

（最多前 10 个中文正文段落）

🎮 赛事: 数字远征超级德古拉N 第 1 季
⚔️ 28/06/2026 21:30 Sharks vs Echo
⚔️ 29/06/2026 01:00 Inner Circle vs Acend

🔗点此阅读原文：https://www.hltv.org/news/45014/...
```

```text
🥷 【NIP 近期赛程预告】

🎮 赛事: XSE Pro League 2026
⚔️ 对阵: NIP vs 3DMAX
⏰ 时间: 7/1 15:00
⏳ 距离开赛还有 3 天 4 小时

⚔️ 对阵: NIP vs HEROIC
⏰ 时间: 7/2 15:00
⏳ 距离开赛还有 4 天 4 小时

🎮 赛事: XPL
⚔️ 对阵: NIP vs FaZe
⏰ 时间: 7/3 15:00
⏳ 距离开赛还有 5 天 4 小时

---

🏆 【往期赛事回顾】

🎮 赛事: Stake Ranked Episode 2
📊 赛果: NIP 2 : 0 HEROIC
📊 赛果: NIP 2 : 0 Sharks
```

## 模式一：当前电脑零成本运行

NapCat 和本项目在同一台 Windows 电脑运行：

```powershell
Copy-Item .env.example .env
notepad .env
```

至少填写：

```text
QQ_GROUP_ID=QQ群号
ONEBOT_HTTP_URL=http://127.0.0.1:3000
ONEBOT_ACCESS_TOKEN=NapCat HTTP 服务端的 Token
```

先做不会联网、不会发送 QQ 消息的内部检查：

```powershell
python -m unittest discover -s tests -v
```

然后把 QQ 小号和 NapCat 登录好，并先把 `QQ_GROUP_ID` 填成你的无人测试群。由你亲自执行下面这一条，开始真实运行：

```powershell
python run_forever.py
```

这不是假数据模拟：程序会立即抓取真实数据，并向测试群发送一条完整的 NIP 赛程消息（其中包含近期赛程预告和往期赛事回顾）以及最新 2 条 HLTV 新闻。之后每约 6 分钟检查一次；赛程或赛果变化本身不会触发这条消息，持续检查主要用于新新闻、转会动态和赛前提醒。保持程序不关闭时，每天北京时间 10 点之后会检查当天是否已经发过赛程，没发才补发。按 `Ctrl+C` 停止。

电脑关机、休眠、关闭这个窗口或退出 NapCat 后都会停止。我们不会替你执行 `python run_forever.py`，因为这条命令会真实向你配置的 QQ 群发送消息。

需要让本机下次重新发送最新 2 条新闻时，先停止常驻程序，再执行：

```powershell
python monitor.py --reset-news-state
python run_forever.py
```

第一条命令只清除首次运行和新闻编号记录，不发送消息，也不会清除已经发送过的赛前提醒。

## 模式二：GitHub 抓取，国内服务器发 QQ

### 国内服务器

把项目文件手动上传到 `/opt/nip-hltv-monitor`。服务器只需要 Python 3 和 NapCat，不需要第三方 Python 包。

```bash
cd /opt/nip-hltv-monitor
cp deploy/relay.env.example .env
nano .env
```

填写群号、OneBot Token 和一段至少 32 位的随机 `RELAY_SECRET`。测试启动：

```bash
python3 relay_server.py
```

服务器本机检查：

```bash
curl http://127.0.0.1:8787/health
```

应返回 `{"ok": true, "service": "nip-hltv-relay"}`。

云服务器安全组只开放 TCP 8787。不要开放 NapCat 的 3000 和 WebUI 的 6099。

设置常驻：

```bash
cp deploy/nip-relay.service.example /etc/systemd/system/nip-relay.service
systemctl daemon-reload
systemctl enable --now nip-relay
systemctl status nip-relay
```

### GitHub 仓库 Secrets

进入 `Settings → Secrets and variables → Actions → Secrets → New repository secret`，添加：

- `RELAY_URL`：`http://服务器公网IP:8787/notify`
- `RELAY_SECRET`：与服务器 `.env` 完全相同

进入 `Settings → Actions → General → Workflow permissions`，选择 `Read and write permissions` 并保存，以便工作流更新 `data/state.json`。

进入 `Actions → NIP 情报监控 → Run workflow`：

- `test_notification`：只测试 GitHub → 国内服务器 → QQ
- `test_article`：发送一篇真实的中文 HLTV 测试新闻
- `reset_state`：只重置首次运行和新闻编号，不发送消息；下一次 `monitor` 会重新发送赛程和最新 2 条新闻
- `monitor`：正式运行；第一次发送近期赛程/往期回顾和最新 2 条新闻

正式成功后，GitHub Actions 默认在每小时第 3、9、15、21、27、33、39、45、51、57 分钟启动临时检查任务，也就是每约 6 分钟一次并避开整点拥堵。自动任务在 Actions 列表中显示为“自动检查（每6分钟）”。定时任务不会被当成一次“程序启动”，所以不会每 6 分钟重复发送赛程；它会检查新新闻、转会动态和赛前提醒，并在北京时间 10 点后检查当天是否已经发过赛程，没发才补发。赛程或赛果变化本身不触发推送。你在 Actions 页面手动运行一次 `monitor`，则视为你主动启动一次，会发送一条完整的 NIP 赛程消息并记为当天已发。GitHub 的定时任务可能排队，因此不保证精确到秒。

## 翻译与安全

`TRANSLATE_PROVIDER=auto` 时先用 Google 免费非官方端点；配置腾讯云密钥后，Google 重试失败会自动切腾讯云。腾讯密钥只存 GitHub Actions Secrets。

中继请求带 Unix 时间戳、随机 nonce 和 HMAC-SHA256 签名；超过 5 分钟、重复 nonce、错误签名、总请求超过 6 MiB 或图片超过 4 MiB 会被拒绝。签名能验证来源并防篡改；使用 cpolar 时应优先选择 HTTPS 地址。

## 自动测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖新闻、赛程、赛果、转会、十段截断、正文图片筛选、图片失败降级、北京时间、同赛事分组、状态重置，以及签名文字/图片中继到 OneBot 的端到端流程。
