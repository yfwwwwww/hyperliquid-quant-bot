# 量化交易系统 · Hyperliquid 永续合约

一个从 0 到 1 独立完成的**量化策略研究 + 自动化执行框架**：行情接入 → 信号计算 → 风控 → 模拟盘/实盘执行 → 账本报表 → 部署运维，全链路自己拆、自己写、自己验证。

> **这个项目最有价值的地方不是收益率，而是一份诚实的结论**：我用样本外回测把 5 类"看起来很强"的过滤器全部证伪，最后只留下一个可复现的朴素基线。详见 [研究结论](#研究结论用样本外回测证伪自己)。

`Python 3.10` · `hyperliquid-python-sdk` · `WebSocket` · `JSONL 账本` · `systemd`

---

## 项目规模

| 项 | 数据 |
| --- | --- |
| 代码规模 | 36 个 Python 模块，约 5,900 行 |
| 覆盖品种 | BTC / ETH / SOL，可扩展到 147 个股票、商品永续（`xyz:` 市场） |
| 回测方法 | 单品种 70% 训练 / 30% 样本外，多品种交叉验证 |
| 运行模式 | `paper` 模拟盘 / `testnet` 测试网 / `mainnet` 实盘，配置切换 |
| 风控 | 单笔风险 1%、名义敞口上限、单日亏损 5% 熔断、冷却期、净值下限停机 |

## 系统结构

四层解耦，每层可以单独替换：

```
策略研究   回测 / 参数寻优 / 样本外验证 / 资金费率研究
    ↓
执行层     行情接入（REST + WS）· 信号计算 · 下单 · 止损 · 移动止盈
    ↓
风控层     仓位换算 · 敞口上限 · 日亏熔断 · 冷却 · 净值下限
    ↓
观测层     append-only 账本 · 业绩报表 · 10 秒哨兵 · Telegram/webhook 推送
```

目录：

```
run.py            入口与命令行（--backtest / --optimize / --scout / --report …）
config.py         配置加载与校验（20+ 可调参数）
indicators.py     EMA / ATR / ADX / ROC / MACD（纯 Python 实现）
strategy.py       信号 + 过滤器 + 止损逻辑
risk.py           仓位换算与取整
client.py         交易所 SDK 封装（行情、下单、资金费率、触发单、重试）
feed.py           WebSocket 行情（bbo 价格 + taker 成交）
mtf.py            多周期共振（实时与回测共用同一套对齐逻辑）
orderflow.py      L2 盘口失衡 + 主动买卖比
account.py        模拟盘 / 实盘账户（多品种）
bot.py            主循环
backtest.py       多品种回测 + train/test 切分 + 基线对比
optimize.py       样本外参数寻优
funding.py        资金费率 carry / 反向研究
ledger.py         append-only 成交与净值账本（JSONL）
report.py         业绩汇总与每日摘要
scout.py          10 秒哨兵扫描 + 推送
dashboard.py      网页看板（持仓、净值、扫描结果）
deploy/           systemd / launchd 部署脚本
```

## 研究结论：用样本外回测证伪自己

**基线**：4h 周期，EMA20 上穿 EMA100 做多、下穿做空，3×ATR 止损。

| 品种 | 全样本 | 样本内 (train 70%) | 样本外 (test 30%) |
| --- | --- | --- | --- |
| BTC | +31.8% | +19.3% | +11.8% |
| ETH | +47.8% | +18.4% | +28.0% |
| SOL | +21.1% | +13.4% | +10.5% |
| **合计** | **+33.6%** | — | — |

然后我逐个测试了"更聪明"的过滤器，结论很克制：

1. **多周期共振当硬门槛，几乎把交易全滤掉，且跑输基线**：要求 3 个周期全同向时，BTC 只剩 3 笔 / −3.2%，ETH 0 笔，SOL 1 笔 / −0.9%，合计 **−1.35%**；放宽到多数派也只有 +19.97%，仍低于基线 +33.6%。
2. **ADX、波动率上限、动量过滤器**：参数寻优后最优解基本回到朴素双均线，样本外没有带来提升。
3. **资金费率（carry / 反向）**：跨币方向不一致、阈值敏感，不构成稳定收益来源。
4. **订单流**：没有历史 L2 / taker 数据，无法回测，只能实盘观察，价值未经验证——所以我没有把它写进结论。

**最终判断：我没有找到稳定盈利的模型。** 这个仓库的定位是**可插拔的策略研究工具 + 严格的风控框架**，不是印钞机。默认参数只是"相对稳健"的保守起点，不代表能赚钱。

做出这个结论的过程，比任何一条曲线都更值钱：**先定义假设，再看样本外，不通过就砍掉**。

## 快速开始

代码是标准的 Python 包（相对导入），所以**目录名要叫 `hyperbot`**，从它的上一级目录用 `-m` 运行：

```bash
git clone https://github.com/<your-name>/hyperliquid-quant-bot.git hyperbot
cd hyperbot
python3.10 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json

cd ..   # 回到 hyperbot 的上一级目录

# 回测（含样本外切分）
python -m hyperbot.run --config hyperbot/config.json --backtest

# 参数寻优 / 资金费率研究 / 哨兵扫描
python -m hyperbot.run --config hyperbot/config.json --optimize
python -m hyperbot.run --config hyperbot/config.json --funding
python -m hyperbot.run --config hyperbot/config.json --scout

# 模拟盘主循环
python -m hyperbot.run --config hyperbot/config.json
```

运行后每笔成交与净值快照会追加写入 `ledger.jsonl`，跑 2–4 周后：

```bash
python -m hyperbot.run --config config.json --report
```

输出净值曲线起止、最大回撤、已实现盈亏、回合数、胜率、盈亏比，以及按币种、按日的拆解。**看最大回撤和连续亏损，而不是某一天的高光。**

## 安全边界

- 只用 **API Wallet（代理钱包）** 私钥，不用主钱包私钥；API Wallet 无法提币。
- 私钥与推送 token 只走环境变量，`config.json` / `state.json` / 账本文件全部在 `.gitignore` 里。
- 上线顺序固定：`paper` → `testnet` → `mainnet`，先小资金、低杠杆、单品种。
- 机器人必须持续运行，止损依赖轮询；开启 `use_native_tpsl` 后交易所端还会挂触发单兜底。
- 部署脚本通过环境变量 `DEPLOY_SRV` 传入服务器地址，仓库里不出现任何主机信息。

## 免责声明

本项目仅供学习与研究，不构成任何投资建议。合约交易带杠杆，风险极高，盈亏自负。
