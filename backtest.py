#!/usr/bin/env python3
"""
Historical replay of the rule engine, 2005 -> today.

This exists because a regime classifier that has never been run on history is
an untested hypothesis with a colour scheme. The questions it answers:

  1. What fraction of history does each stage occupy? A "Financial Repression"
     label that fires 40% of the time is not detecting a rare regime.
  2. What does it say about dates we already have opinions on (2008, 2011,
     2020, 2022)? Face validity.
  3. How often does the label flip? A classifier that changes its mind weekly
     is noise, whatever its average looks like.
  4. Does the stage label carry ANY forward information for gold/BTC? Reported
     with sample sizes, because the honest answer for Stage 3/4 is "N is far
     too small to conclude anything" and that has to be visible, not buried.

Run:  python3 backtest.py [--start 2005-01-01] [--out backtest_report.md]
"""
from __future__ import annotations

import argparse
import json
import statistics
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from engine import config, features, policy, scores, sources
from engine.features import Panel
from engine.stages import apply_hysteresis, raw_stage

ROOT = Path(__file__).resolve().parent


def load_cached_panel() -> Panel:
    raw = {}
    for sid, meta in config.FRED_SERIES.items():
        rows = sources._read_cache(f"fred_{sid}")
        if rows:
            raw[sid] = {"observations": rows, "label": meta["label"], "freq": meta["freq"],
                        "release_lag_days": meta["release_lag_days"],
                        "source": "FRED", "source_url": "", "from_cache": True}
    for _sym, meta in config.YAHOO_SERIES.items():
        rows = sources._read_cache(f"yahoo_{meta['key']}")
        if rows and meta["key"] not in raw:
            raw[meta["key"]] = {"observations": rows, "label": meta["label"], "freq": "d",
                                "release_lag_days": 0, "source": "Yahoo",
                                "source_url": "", "from_cache": True}
    return Panel(raw)


def replay(p: Panel, start: str) -> list[dict]:
    events = policy.load_events()
    d0 = datetime.strptime(start, "%Y-%m-%d").date()
    cal = [d for d in p.calendar if d >= d0]
    rows = []
    for i, d in enumerate(cal):
        f = features.compute_features(p, d)
        pol = policy.evaluate(d, events)
        sc = scores.compute_all(f, pol)
        rs, detail = raw_stage(f, sc, pol)
        rows.append({
            "date": d, "raw": rs,
            "fs": sc["fiscal_stress"]["score"],
            "fr": sc["financial_repression"]["score"],
            "db": sc["debasement"]["score"],
            "bl": sc["btc_liquidity"]["score"],
            "composite": sc["composite"],
            "driver": sc["driver_composite"],
            "confirm": sc["confirmation_composite"],
            "gold": f.get("GOLD_level"), "btc": f.get("BTC_level"),
            "dgs30": f.get("DGS30_level"), "dfii10": f.get("DFII10_level"),
            "coverage": sc["fiscal_stress"]["coverage_pct"],
        })
        if i % 1000 == 0 and i:
            print(f"  ... {i}/{len(cal)}  {d}")
    conf = apply_hysteresis([r["raw"] for r in rows],
                            [r["fs"] for r in rows], [r["fr"] for r in rows])
    for r, s in zip(rows, conf):
        r["stage"] = s
    return rows


def fwd_return(rows: list[dict], i: int, key: str, horizon: int) -> float | None:
    j = i + horizon
    if j >= len(rows):
        return None
    a, b = rows[i].get(key), rows[j].get(key)
    if not a or not b:
        return None
    return (b / a - 1.0) * 100.0


def analyse(rows: list[dict]) -> str:
    out: list[str] = []
    W = out.append
    n = len(rows)
    W(f"# Fiscal Dominance Monitor — 历史回放报告\n")
    W(f"样本：{rows[0]['date']} → {rows[-1]['date']}，共 {n} 个交易日\n")
    W("> 生成方式：用**当前**的规则与阈值重放历史。这不是样本外检验，"
      "阈值本身是人定的先验，不是拟合出来的——但也没有经过历史校准。"
      "下面的数字只回答「这套规则在历史上会怎么叫」，不回答「叫得对不对」。\n")

    # ---- 1. stage occupancy
    W("\n## 1. 各阶段占比\n")
    W("| Stage | 名称 | 天数 | 占比 |")
    W("|---|---|---:|---:|")
    cnt = Counter(r["stage"] for r in rows)
    for s in sorted(config.STAGE_DEFS):
        c = cnt.get(s, 0)
        W(f"| {s} | {config.STAGE_DEFS[s]['name_cn']} | {c} | {c/n*100:.1f}% |")

    # ---- 2. episodes
    W("\n## 2. 阶段区间（连续 ≥ 20 交易日）\n")
    W("| 阶段 | 起 | 止 | 交易日 |")
    W("|---|---|---|---:|")
    cur, st = rows[0]["stage"], 0
    eps = []
    for i in range(1, n + 1):
        if i == n or rows[i]["stage"] != cur:
            if i - st >= 20:
                eps.append((cur, rows[st]["date"], rows[i - 1]["date"], i - st))
            if i < n:
                cur, st = rows[i]["stage"], i
    for s, a, b, ln in eps:
        W(f"| {s} {config.STAGE_DEFS[s]['name_cn']} | {a} | {b} | {ln} |")
    W(f"\n共 {len(eps)} 段。段数太多 = 分类器不稳定；太少 = 阈值可能过钝。\n")

    # ---- 3. flip frequency
    flips = sum(1 for i in range(1, n) if rows[i]["stage"] != rows[i - 1]["stage"])
    raw_flips = sum(1 for i in range(1, n) if rows[i]["raw"] != rows[i - 1]["raw"])
    W("\n## 3. 稳定性\n")
    W(f"- 迟滞后阶段变更：**{flips}** 次（平均每 {n/max(1,flips):.0f} 个交易日一次）")
    W(f"- 未加迟滞的原始标签变更：{raw_flips} 次")
    W(f"- 迟滞消除了 {(1-flips/max(1,raw_flips))*100:.0f}% 的抖动")

    # ---- 4. face validity on known dates
    W("\n## 4. 已知节点的面效度\n")
    W("| 日期 | 事件 | Stage | 财政压力 | 金融压抑 | 贬值 | BTC流动性 |")
    W("|---|---|---:|---:|---:|---:|---:|")
    marks = [("2007-08-15", "次贷初期"), ("2008-10-15", "雷曼后恐慌"),
             ("2009-03-16", "QE1 全面展开"), ("2011-08-08", "美债降级"),
             ("2013-06-20", "缩减恐慌"), ("2015-12-16", "首次加息"),
             ("2020-03-23", "疫情崩盘底"), ("2020-08-06", "QE 无限 + 金价新高"),
             ("2021-11-10", "CPI 6.2% 通胀失控"), ("2022-06-15", "加息 75bp"),
             ("2022-10-21", "英国养老金/长端危机"), ("2023-10-19", "10Y 破 5%"),
             ("2024-09-18", "开始降息"), ("2025-04-08", "关税冲击")]
    by_date = {r["date"].isoformat(): r for r in rows}
    for ds, label in marks:
        r = by_date.get(ds)
        if not r:
            near = [x for x in rows if abs((x["date"] - datetime.strptime(ds, "%Y-%m-%d").date()).days) <= 5]
            r = near[0] if near else None
        if r:
            W(f"| {r['date']} | {label} | **{r['stage']}** | {r['fs']:.0f} | "
              f"{r['fr']:.0f} | {r['db']:.0f} | {r['bl']:.0f} |")
        else:
            W(f"| {ds} | {label} | 无数据 | | | | |")

    # ---- 5. forward returns conditioned on stage
    W("\n## 5. 阶段 → 前瞻收益（60 交易日）\n")
    W("**看 N。** Stage 3/4 的样本量注定极小，这是这类框架的根本限制，"
      "不是可以靠更多数据修好的问题。\n")
    for asset, key in (("黄金", "gold"), ("BTC", "btc")):
        buckets: dict[int, list[float]] = defaultdict(list)
        for i, r in enumerate(rows):
            fr_ = fwd_return(rows, i, key, 60)
            if fr_ is not None:
                buckets[r["stage"]].append(fr_)
        W(f"\n### {asset}（60日后收益 %）\n")
        W("| Stage | N(重叠) | 有效独立样本≈ | 中位数 | 均值 | 标准差 | 可否下结论 |")
        W("|---|---:|---:|---:|---:|---:|---|")
        for s in sorted(buckets):
            v = buckets[s]
            eff = max(1, len(v) // 60)     # overlapping 60d windows
            med = statistics.median(v)
            mean = statistics.mean(v)
            sd = statistics.pstdev(v) if len(v) > 1 else 0.0
            verdict = "否，N 太小" if eff < 8 else ("勉强，仅供参考" if eff < 20 else "有一定意义")
            W(f"| {s} | {len(v)} | {eff} | {med:+.1f} | {mean:+.1f} | {sd:.1f} | {verdict} |")

    # ---- 6. driver vs confirmation
    W("\n## 6. 驱动分 vs 市场确认分（诊断循环论证）\n")
    both = [(r["driver"], r["confirm"]) for r in rows
            if r["driver"] is not None and r["confirm"] is not None]
    if both:
        dv = [x for x, _ in both]
        cv = [y for _, y in both]
        mdv, mcv = statistics.mean(dv), statistics.mean(cv)
        cov = sum((a - mdv) * (b - mcv) for a, b in both) / len(both)
        sd1, sd2 = statistics.pstdev(dv), statistics.pstdev(cv)
        corr = cov / (sd1 * sd2) if sd1 and sd2 else 0.0
        W(f"- 相关系数 = **{corr:.2f}**（N={len(both)}）")
        W(f"- 驱动分均值 {mdv:.1f}（σ={sd1:.1f}）；市场确认分均值 {mcv:.1f}（σ={sd2:.1f}）")
        W("- 相关系数若接近 1，说明「宏观驱动」和「价格已经涨了」讲的是同一件事，"
          "整套框架就退化成趋势跟踪；接近 0 才说明两者是独立信息。")

    # ---- 7. discrimination check
    W("\n## 7. 分值区分度（对照 PM H1 的失败模式）\n")
    for name, k in (("综合", "composite"), ("财政压力", "fs"), ("金融压抑", "fr"),
                    ("贬值", "db"), ("BTC流动性", "bl")):
        v = [r[k] for r in rows if r[k] is not None]
        if not v:
            continue
        v_sorted = sorted(v)
        p10 = v_sorted[int(len(v) * 0.10)]
        p50 = v_sorted[int(len(v) * 0.50)]
        p90 = v_sorted[int(len(v) * 0.90)]
        W(f"- **{name}**：min {min(v):.0f} / p10 {p10:.0f} / 中位 {p50:.0f} / "
          f"p90 {p90:.0f} / max {max(v):.0f}（跨度 {max(v)-min(v):.0f}）")
    W("\n跨度过窄（例如全部挤在 40-60）就是 PM H1 那种「评分器无区分度」的病，"
      "必须在信任任何输出之前先修。\n")

    return "\n".join(out)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2005-01-01")
    ap.add_argument("--out", default="backtest_report.md")
    ap.add_argument("--json", default="docs/data/backtest.json")
    args = ap.parse_args()

    print("[backtest] loading cached panel ...")
    p = load_cached_panel()
    print(f"[backtest] calendar {p.calendar[0]} -> {p.calendar[-1]}")
    rows = replay(p, args.start)
    print(f"[backtest] replayed {len(rows)} days")

    report = analyse(rows)
    (ROOT / args.out).write_text(report)
    print(f"[backtest] report -> {args.out}")

    jp = ROOT / args.json
    jp.parent.mkdir(parents=True, exist_ok=True)
    step = max(1, len(rows) // 3000)
    thin = rows[::step]
    jp.write_text(json.dumps({
        "dates": [r["date"].isoformat() for r in thin],
        "stage": [r["stage"] for r in thin],
        "composite": [r["composite"] for r in thin],
        "fs": [r["fs"] for r in thin], "fr": [r["fr"] for r in thin],
        "db": [r["db"] for r in thin], "bl": [r["bl"] for r in thin],
    }, separators=(",", ":")))
    print(f"[backtest] json -> {args.json}")


if __name__ == "__main__":
    main()
