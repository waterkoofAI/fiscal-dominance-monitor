# Fiscal Dominance Monitor

财政主导 / 金融压抑宏观状态机。每天自动抓官方数据 → 确定性规则引擎评分 →
判定所处阶段 → 输出规则化风险姿态 + 反向证伪信号。手机端 PWA。

**先读 [`ASSESSMENT.md`](ASSESSMENT.md)** —— 里面写清楚了这东西能做什么、
不能做什么，以及回测里它**没有**表现出前瞻预测力这件事。

---

## 它回答什么问题

> 我们现在到底是不是正在进入 **财政主导 → 金融压抑 → QE/YCC** 这条路径？
> 如果是，黄金和 BTC 哪个阶段更值得承担风险？

它**不**回答「BTC 明天涨不涨」。输出是 `Strong Bullish / Bullish / Neutral /
Caution / Bearish` 这种风险姿态标签，不是交易指令。整个代码库里没有任何
仓位、下单、金额的概念，这是刻意的。

---

## 快速开始

```bash
git clone <your-repo-url> && cd fiscal-dominance-monitor
python3 -m unittest discover -s tests    # 27 个测试，应全过
python3 -m engine.run --days 400         # 抓数据 + 跑引擎，约 2 分钟
cd docs && python3 -m http.server 8899   # 本地预览
```

打开 http://127.0.0.1:8899 —— 手机上是 Chrome「添加到主屏幕」。

**不需要任何 API key。** FRED 走无 key 的 `fredgraph.csv` 端点，
Yahoo 和 CoinGecko 都不需要认证。

---

## 部署到 GitHub Pages

1. 新建仓库，推上去
2. Settings → Pages → Source 选 **Deploy from a branch**，
   branch = `main`，folder = **`/docs`**
3. Settings → Actions → General → Workflow permissions
   选 **Read and write permissions**（工作流要把每日数据 commit 回来）
4. Actions 页签手动跑一次 `Daily macro update` 验证
5. 手机 Chrome 打开 `https://<user>.github.io/<repo>/` → 菜单 →「添加到主屏幕」

之后每天 **22:00 UTC（日本时间次日 07:00）** 自动更新。
`FRED_API_KEY` 是可选 secret，不设也能跑。

---

## 架构

```
FRED (无key CSV)  ┐
Yahoo Finance     ├─→ sources.py ──→ 磁盘缓存 (data/cache/*.csv, 进 git)
CoinGecko         ┘                        │
                                           ▼
                            features.py  派生指标 + 发布滞后对齐
                                           │
policy_events.json ──────────────────→ policy.py  人工台账（只有 fact 计分）
                                           │
                                           ▼
                              scores.py  四项评分 0–100
                                           │
                              ┌────────────┴────────────┐
                              ▼                         ▼
                        stages.py 阶段机          breakers.py 反向信号
                        （闸门 + 迟滞）            （每日主动证伪）
                              └────────────┬────────────┘
                                           ▼
                              signals.py + narrative.py
                                （风险姿态 + 中文日报，模板渲染）
                                           │
                                           ▼
                              docs/data/latest.json + history.json
                                           │
                                           ▼
                                  📱 docs/  静态 PWA
```

**没有 npm，没有构建步骤，没有数据库。** 仓库本身就是数据库——
每天的 JSON 提交进 git，半年后能精确复原任意一天的判断依据。

---

## 首页给你什么（以及不给什么）

| 卡片 | 内容 | 性质 |
|---|---|---|
| **策略 / 仓位姿态** | 黄金/BTC/30Y美债/美元 各自的信号 + 姿态（增持·维持·降低风险·减持）+ 5/20/60 日信号变化轨迹 | 规则化风险姿态 |
| **情景概率** | 正常化 / 财政主导 / 金融压抑 / QE-YCC / 债务危机 五条路径的权重，每条可展开看构成条件 | 规则映射后归一化 |
| **距离下一阶段** | 逐条闸门 ☑/☐ + **最近的触发点还差多少**（如「还差 13.4 分」「还差 31bp」） | 规则引擎内部状态 |
| **BTC 加仓确认清单** | 6 项条件，回答「你的宏观假设还差哪几条没被满足」 | 同上 |
| **反向信号** | 5 项预注册的证伪检验，与支持性证据同等显著 | 主动找自己错 |

**不给的**：价格预测、点位目标、涨跌概率、买卖时点。
回测中该框架对金/BTC **未表现出前瞻预测力**（ASSESSMENT.md §3），
所以它给的是「宏观理由正在增强还是减弱」，不是「接下来会涨到多少」。

「情景概率」是**今天的数据有多符合各条路径的特征**，归一化到 100。
它不是校准过的预测概率——金融压抑几十年一遇，没有任何数据集能校准出它的真实发生率。

## 五个阶段

| Stage | 名称 | 触发条件 |
|---|---|---|
| 0 | 常态 | 财政压力 < 35 |
| 1 | 财政压力 | 财政压力 ≥ 35 |
| 2 | 财政主导观察 | 财政压力 ≥ 55 **且** 辅助条件满足 ≥2 项 |
| 3 | 金融压抑 | **6 个方向性硬闸门全部满足**（压抑分≥65、通胀未下行、实际利率在跌、美元未走强、黄金在涨、长端未失控） |
| 4 | 货币体制转换 | **政策事实**（台账 fact 类 QE/YCC）**且** 联储资产负债表 60 日扩张 ≥1% |

迟滞：进入需连续 3 天满足，退出需跌破阈值 6 分。

---

## 政策台账：为什么是人工维护的

`data/policy_events.json` 由人维护，**不从新闻自动抓取**。

Stage 4 是本系统后果最重的输出。让模型每天读标题去判断，等于给它一个把
「官员讨论资产负债表」读成「货币体制转换」的机会。所以：

- 每条事件带 `source_url` 和 `fact_or_inference`
- **只有 `fact` 计分**，`inference` 只显示不计分、永不触发阶段
- Stage 4 还必须同时有数据面佐证（WALCL 真的在扩）

新增一条：

```json
{
  "date": "2026-09-01",
  "institution": "U.S. Treasury",
  "event_type": "buyback_expanded",
  "title": "Treasury expands buyback size to $X bn per operation",
  "description": "",
  "source_url": "https://home.treasury.gov/...",
  "fact_or_inference": "fact",
  "verified": true
}
```

`event_type` 取值见 `engine/config.py::POLICY_EVENT_SCORES`。

> ⚠️ **仓库自带的 16 条种子事件全部标记 `verified: false`**，
> 是为了让回测跑起来预填的。核对来源后把 `verified` 改 true。
> 在那之前 Stage 4 不可信。

---

## 叙事跟踪：把别人的说法变成可监控的检验

有人发一套宏观论述，其中一部分可检验、一部分不可检验。常见的说服路径是先坐实
可检验的部分，然后让你的信心顺势外溢到不可证伪的部分。`data/narratives.json`
把这两半永久分开。

每条叙事存三块：

- `confirm` —— 成立则支持该说法的条件
- `refute` —— 成立则反驳该说法的条件
- `untestable` —— 明确无法检验的主张，**永远不参与打分**

```json
{
  "id": "some-thesis-2026-08",
  "title": "叙事标题",
  "source": "@who",
  "source_url": "https://...",
  "recorded_at": "2026-08-23",
  "summary_cn": "他的主张是……",
  "confirm": [
    {"id": "c1", "label_cn": "实际利率下行", "feature": "DFII10_chg_60d",
     "op": "<", "value": -0.10, "unit": "bp"}
  ],
  "refute": [
    {"id": "r1", "label_cn": "实际利率上行", "feature": "DFII10_chg_60d",
     "op": ">", "value": 0.10, "unit": "bp"}
  ],
  "untestable": ["政治时间表——任何结果都能被它吸收"],
  "scale_checks": [{"label_cn": "稳定币", "value": "$291.6B", "note": "量级核对"}]
}
```

条件支持 `all` / `any` 嵌套。`feature` 是 `engine/features.py` 输出的任一字段
（跑 `python3 -c "…compute_features…"` 可列出全部）。

**两条铁律：**

1. **`refute` 必须和 `confirm` 同时写。** 事后再决定「什么算错」不是检验。
   `tests/test_engine.py` 会拒绝任何没有 `refute` 的叙事条目。
2. **条件是结构化数据，不是表达式。** 这里没有 `eval()`，也不会有——
   叙事文件是别人贴的内容，属于不可信输入，绝不能让它执行任何东西。
   有一个 AST 测试守着这条。

## 常用命令

```bash
python3 -m engine.run --days 400          # 每日更新（抓网络）
python3 -m engine.run --no-refresh        # 只用缓存，秒级，改规则时用
python3 backtest.py --start 2005-01-01    # 历史回放 → backtest_report.md
python3 sensitivity.py                    # 阈值敏感性检验
python3 -m unittest discover -s tests -v  # 测试
python3 tools_make_icons.py               # 重新生成图标
```

---

## 调参

**所有阈值和权重集中在 `engine/config.py`**，代码里不允许出现硬编码数字。
改完先跑 `sensitivity.py` 看结论稳不稳，再跑 `backtest.py` 看历史行为变了多少。

四项评分的权重各自加总必须为 100，`tests/test_engine.py` 会守这条。

---

## 数据源

| 类别 | 序列 | 来源 |
|---|---|---|
| 名义利率 | DGS30 / DGS10 / DGS5 / DGS2 / DGS3MO | FRED |
| 实际利率 | DFII10 / DFII30 / DFII5 | FRED |
| 通胀预期 | T10YIE / T5YIE / T5YIFR | FRED |
| **期限溢价** | **THREEFYTP10**（ACM 模型，NY Fed） | FRED |
| 通胀 | CPIAUCSL / CPILFESL / PCEPI / PCEPILFE | FRED |
| 美元 | DTWEXBGS（广义） / DX-Y.NYB（DXY） | FRED / Yahoo |
| 流动性 | WALCL / WTREGEN / RRPONTSYD / WRESBAL | FRED |
| 信用/风险 | BAMLH0A0HYM2 / VIXCLS | FRED |
| 财政 | GFDEGDQ188S / FYFSGDA188S | FRED |
| 资产 | GC=F / ^NDX / ^GSPC | Yahoo |
| BTC | bitcoin | CoinGecko（回退 Yahoo） |

净流动性 = `WALCL − WTREGEN − RRPONTSYD×1000`（前两者单位为百万美元，
第三个为十亿美元，单位不对齐会差 1000 倍——`TestNetLiquidity` 守着这条）。

---

## 抓取层的三个坑（实测，别"优化"回去）

1. **FRED 不能带浏览器 User-Agent** —— 带了就 20 秒超时返回 0 字节。用 curl 默认 UA。
2. **Yahoo 必须带浏览器 User-Agent** —— 不带直接 `Edge: Too Many Requests`。
   两者要求正好相反，所以 `_http_get` 有 `send_ua` 参数。
3. **全程 `--http1.1`，不要 `--compressed`** —— FRED 会间歇性掐断 HTTP/2 流
   （curl rc=92 INTERNAL_ERROR）。
4. Yahoo 长历史要用 `period1`/`period2` 时间戳，**不能用 `range=max`** ——
   后者会静默降频到月线（26 年只回 267 个点）。

---

## 免责声明

本工具输出规则化风险姿态标签，**不是交易指令，不构成投资建议**。
所有分数与阶段由确定性规则计算，日报文字由模板渲染，不经过语言模型。
回测中该框架**未表现出对黄金/BTC 的前瞻预测力**，详见 `ASSESSMENT.md` 第 3 节。
