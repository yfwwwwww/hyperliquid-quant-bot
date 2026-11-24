# 7×24 部署指南

> 这个机器人会在你选择的机器上持续运行并自动下单。**请确认你已理解风险**：策略无验证过的 edge，实盘可能亏损。先用 paper 跑通，再上测试网，最后才主网。

## 方案选择

| 环境 | 推荐方式 | 自动重启 | 开机自启 |
|---|---|---|---|
| Linux VPS（推荐） | systemd（`hyperbot.service`） | ✅ | ✅ |
| macOS 本机 | launchd（`com.hyperbot.bot.plist`） | ✅ | ✅ 登录后 |
| 任意（无 root） | `run_forever.sh` 守护循环 | ✅ | ❌ 需自己拉起 |

## 一、准备（所有方案通用）

```bash
# 1. 代码放到机器上，例如 /opt/hyperbot
cd /opt/hyperbot
python3.10 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2. 配置：先 paper，再改 testnet/mainnet
cp config.lowrisk.example.json config.json     # 按你的风险偏好选
# 或 cp config.paper.lowrisk.json config.json  # 先模拟

# 3. 密钥放 .env（chmod 600，绝不进 git）
cp deploy/env.example .env
chmod 600 .env
vim .env        # 填主钱包地址 + API 钱包私钥 + Telegram

# 4. 先手动验证能起来
.venv/bin/python -m hyperbot.run --config config.json --test-notify
.venv/bin/python -m hyperbot.run --config config.json   # 看到启动日志后 Ctrl+C
```

## 二、Linux VPS（systemd，推荐）

```bash
sudo useradd -r -s /usr/sbin/nologin hyperbot   # 可选：专用用户
sudo chown -R hyperbot:hyperbot /opt/hyperbot

sudo cp deploy/hyperbot.service /etc/systemd/system/hyperbot.service
# 按需修改 User/WorkingDirectory/路径
sudo systemctl daemon-reload
sudo systemctl enable --now hyperbot

# 看状态与日志
sudo systemctl status hyperbot
sudo journalctl -u hyperbot -f
tail -f /opt/hyperbot/logs/hyperbot.log
```

## 三、macOS 本机（launchd）

1. 把 `deploy/com.hyperbot.bot.plist` 里的 `/ABSOLUTE/PATH/TO/hyperbot`
   全部替换成你的真实绝对路径。
2. 建议用一个小 wrapper 加载 `.env`（launchd 不会自动读 `.env`）：
   ```bash
   cat > run_bot.sh <<'EOF'
   #!/bin/bash
   cd "$(dirname "$0")"
   set -a; source .env; set +a
   exec .venv/bin/python -m hyperbot.run --config config.json
   EOF
   chmod +x run_bot.sh
   ```
   然后把 plist 的 `ProgramArguments` 改成 `/ABSOLUTE/PATH/run_bot.sh`。
3. 加载：
   ```bash
   cp deploy/com.hyperbot.bot.plist ~/Library/LaunchAgents/
   launchctl load ~/Library/LaunchAgents/com.hyperbot.bot.plist
   launchctl list | grep hyperbot
   ```
4. 停止：`launchctl unload ~/Library/LaunchAgents/com.hyperbot.bot.plist`

## 四、无 root / 临时机器（守护脚本）

```bash
cp deploy/env.example .env && chmod 600 .env && vim .env
CONFIG=config.json VENV=.venv ./deploy/run_forever.sh &
# 或 nohup ... &
```

## 五、确认"活着"

```bash
# 进程在不在
pgrep -af "hyperbot.run" || ps aux | grep hyperbot
# 日志有没有在更新
tail -n 20 logs/hyperbot.log
# 账本有没有新增净值快照（--report 的数据源）
python -m hyperbot.run --config config.json --report
```

如果 `--report` 里 equity 快照时间停滞超过 1 小时，说明进程卡住了，看日志。

## 六、常见坑

- **Python 版本**：用 3.10。3.11+ 与 SDK 依赖有兼容问题。
- **API Wallet 权限**：只能用 API Wallet 私钥，主钱包地址填 `HYPERLIQUID_ACCOUNT_ADDRESS`。API Wallet 不能提币。
- **限流**：程序已内置 429 指数退避；但别同时开太多实例。
- **网络**：服务器要能访问 `api.hyperliquid.xyz`，且时间同步（NTP）要准，否则签名可能失败。
- **状态文件**：`state.json` / `ledger.jsonl` 必须可写；重启后靠它们恢复持仓元数据。
- **`use_native_tpsl`**：先在测试网确认触发单能挂上再用主网（见 README 顶部说明）。

## 七、安全清单

- [ ] `.env` 权限 600，且不在 git 里（`.gitignore` 已含）
- [ ] 用 API Wallet，不用主钱包私钥
- [ ] 先 paper → testnet → 小资金 mainnet
- [ ] 配好 Telegram 推送，别做"黑盒"运行
- [ ] 定期 `--report` 看回撤，到止损线就停
