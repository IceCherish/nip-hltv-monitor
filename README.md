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
- NIP 赛前提醒和 HLTV Transfers 阵容动态；只推送今天或昨天的阵容动态，超过 1 天自动跳过
- 每次启动常驻程序时立即发送一条完整的 NIP 赛程消息（近期赛程预告 + 往期赛事回顾），并记为当天已发送；持续运行时每天北京时间 10 点检查当天是否发过，没发才补发
- 第一次正式运行最多发送 1 小时内的最新 2 条新闻；之后按 HLTV 新闻编号去重，只发送 1 小时内的新增新闻
- 每天北京时间 01:00（含）至 07:00（不含）为静默时段：不抓取、不发送新闻、赛程、阵容动态或赛前提醒，也不改动缓存
- 本机直连 NapCat 与 GitHub → 国内服务器中继两种模式
- 腾讯云机器翻译优先，额度用尽或接口失败时自动改用 Google 免费非官方翻译

第一次正式运行依次发送当前近期赛程与最近赛事回顾、1 小时内的最新 2 条新闻，不额外发送启动说明。程序会把已经见过的 HLTV 新闻编号保存到 `data/state.json`，以后重复检查时不会再次发送；如果一次检查发现多条新新闻，会按从旧到新的顺序发送。每篇新闻的标题、前 10 段正文和赛事名会合并批量翻译，显著减少请求次数；某篇翻译临时失败时不会影响赛程或赛前提醒，发布时间未超过 1 小时时会在下次检查重试，超过 1 小时后永久跳过并记入去重记录。

静默时段内的任务会正常结束但不访问数据源、不发送消息、不改缓存；07:00 恢复后仍按原有有效期判断，因此凌晨超过 1 小时的新闻不会补发，已经错过的赛前提醒也不会补发。手动触发正式的 `monitor` 同样遵守静默时段；明确使用的测试通知功能不受影响。

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
⏳ 距离 3 天 4 小时

⚔️ 对阵: NIP vs HEROIC
⏰ 时间: 7/2 15:00
⏳ 距离 4 天 4 小时

🎮 赛事: XPL
⚔️ 对阵: NIP vs FaZe
⏰ 时间: 7/3 15:00
⏳ 距离 5 天 4 小时

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

这不是假数据模拟：程序会立即抓取真实数据。只有存在 7 天内尚未开赛的 NIP 比赛时，才向测试群发送一条完整的 NIP 赛程消息（其中包含近期赛程预告和往期赛事回顾）；新闻逻辑独立，首次运行仍会发送最新 2 条 HLTV 新闻。之后每约 15 分钟检查一次；赛程或赛果变化本身不会触发这条消息，持续检查主要用于新新闻、转会动态和赛前提醒。保持程序不关闭时，每天北京时间 10 点之后会检查当天是否已经发过赛程；仅在存在 7 天内比赛且当天未发送时补发。按 `Ctrl+C` 停止。

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

GitHub Actions 中有两个独立工作流：“NIP 手动测试”用于你点击运行，“NIP 自动监控（外部定时）”由外部免费定时器通过 GitHub Workflow Dispatch API 每约 15 分钟触发。外部定时任务不会被当成一次“程序启动”，所以不会每 15 分钟重复发送赛程；它会检查新新闻、转会动态和赛前提醒，并在北京时间 10 点后检查当天是否已经发过赛程。只有存在 7 天内尚未开赛的比赛且当天未发送时，才会发送近期赛程和往期回顾；赛程或赛果变化本身不触发推送。你在“NIP 手动测试”中手动运行一次 `monitor`，同样只有存在 7 天内比赛时才发送赛程消息并记为当天已发。

cron-job.org 无需修改，继续保持每约 15 分钟触发即可。每天北京时间 01:00-07:00 的外部触发会成功运行，但监控程序会自行静默退出；07:00 起自动恢复。

## 翻译与安全

`TRANSLATE_PROVIDER=auto` 时，配置腾讯云密钥后会先用腾讯云机器翻译；腾讯额度用尽、鉴权失败或接口异常时自动切换到 Google 免费非官方端点。没有配置腾讯密钥时直接使用 Google。腾讯密钥只存 GitHub Actions Secrets。

可选的 `TENCENT_TERM_REPO_ID` 用于指定腾讯云英译中术语库。填写后，每次腾讯翻译请求都会明确按英文翻译为中文并携带该术语库 ID；Google 备用翻译不使用腾讯术语库。运行记录会显示本篇实际使用了腾讯云还是 Google。为兼容腾讯接口限制，启用腾讯云时每段最多 1800 字符。

中继请求带 Unix 时间戳、随机 nonce 和 HMAC-SHA256 签名；超过 5 分钟、重复 nonce、错误签名、总请求超过 6 MiB 或图片超过 4 MiB 会被拒绝。签名能验证来源并防篡改；使用 cpolar 时应优先选择 HTTPS 地址。

## 自动测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖新闻、赛程、赛果、转会、十段截断、正文图片筛选、图片失败降级、北京时间、同赛事分组、状态重置，以及签名文字/图片中继到 OneBot 的端到端流程。
