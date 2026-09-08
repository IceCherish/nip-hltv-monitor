# NIP / HLTV 中文 QQ 情报机器人

最终推荐架构：

```text
GitHub Actions 抓取、翻译、去重
        ↓ 带 HMAC-SHA256 签名的 HTTP POST
国内服务器 relay_server.py
        ↓ 只访问 127.0.0.1
NapCat / OneBot
        ↓
QQ群
```

国内服务器不需要访问 HLTV、GitHub、Jina Reader 或翻译服务，只接收已经整理好的中文文字并交给 NapCat。NapCat 的 3000 和 6099 端口不能开放到公网；公网只开放中继端口，默认 8787。

## 当前功能

- 自动读取所有 HLTV 新闻并翻译标题、正文
- 只发送前 10 个正文文字段落，文末始终附原文地址
- 不抓取、下载或发送图片；排除评论区
- 提取新闻正文赛程并转换为北京时间
- 展示全部 NIP 未开赛比赛，同一赛事名称只显示一次
- 展示 NIP 最近一个已结束赛事的全部赛果
- NIP 赛前提醒和 HLTV Transfers 阵容动态
- 本机直连 NapCat 与 GitHub → 国内服务器中继两种模式
- Google 免费非官方翻译优先，可配置腾讯云故障备用

第一次正式运行只发送启动消息、当前近期赛程和最近赛事回顾，不补发历史新闻。

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

测试：

```powershell
python -m unittest discover -s tests -v
python monitor.py --test-notification
python monitor.py --test-rich-article
python monitor.py
```

持续运行：

```powershell
python run_forever.py
```

电脑关机、休眠或关闭 NapCat 后就会停止。

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
- `monitor`：正式运行；第一次发送启动、近期赛程和往期回顾

正式成功后，工作流默认每 6 分钟执行一次。

## 翻译与安全

`TRANSLATE_PROVIDER=auto` 时先用 Google 免费非官方端点；配置腾讯云密钥后，Google 重试失败会自动切腾讯云。腾讯密钥只存 GitHub Actions Secrets。

中继请求带 Unix 时间戳、随机 nonce 和 HMAC-SHA256 签名；超过 5 分钟、重复 nonce、错误签名或超过 64 KiB 的请求会被拒绝。签名能验证来源并防篡改，但普通 HTTP 不加密新闻正文；以后有域名时建议加 HTTPS。

## 自动测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖新闻、赛程、赛果、转会、十段截断、图片排除、北京时间、同赛事分组，以及签名中继到 OneBot 的端到端流程。
