"""
Single source of truth for every threshold, weight and series id.

Design rule: NOTHING in the scoring path may hard-code a number. If you want to
tune the model, you tune it here, and the change is visible in one diff.
"""
from __future__ import annotations

# --------------------------------------------------------------------------
# 1. DATA SERIES
# --------------------------------------------------------------------------
# FRED series pulled via the keyless fredgraph CSV endpoint.
#   release_lag_days = how long AFTER the observation date the value is actually
#   published. Used by the backtest to avoid look-ahead bias. Daily market
#   series are published same/next day (lag 1). CPI for month M lands ~mid M+1.
FRED_SERIES = {
    # --- nominal curve ---
    "DGS30":       {"label": "30Y Treasury",          "freq": "d", "release_lag_days": 1},
    "DGS10":       {"label": "10Y Treasury",          "freq": "d", "release_lag_days": 1},
    "DGS5":        {"label": "5Y Treasury",           "freq": "d", "release_lag_days": 1},
    "DGS2":        {"label": "2Y Treasury",           "freq": "d", "release_lag_days": 1},
    "DGS3MO":      {"label": "3M Bill",               "freq": "d", "release_lag_days": 1},
    # --- real yields (TIPS) ---
    "DFII10":      {"label": "10Y Real Yield",        "freq": "d", "release_lag_days": 1},
    "DFII30":      {"label": "30Y Real Yield",        "freq": "d", "release_lag_days": 1},
    "DFII5":       {"label": "5Y Real Yield",         "freq": "d", "release_lag_days": 1},
    # --- inflation expectations ---
    "T10YIE":      {"label": "10Y Breakeven",         "freq": "d", "release_lag_days": 1},
    "T5YIE":       {"label": "5Y Breakeven",          "freq": "d", "release_lag_days": 1},
    "T5YIFR":      {"label": "5Y5Y Forward",          "freq": "d", "release_lag_days": 1},
    # --- term premium: THE variable that separates "real rate repricing"
    #     from "fiscal risk premium". ACM model, NY Fed. ---
    "THREEFYTP10": {"label": "10Y Term Premium (ACM)","freq": "d", "release_lag_days": 2},
    # --- dollar ---
    "DTWEXBGS":    {"label": "Broad Dollar Index",    "freq": "d", "release_lag_days": 4},
    # --- realised inflation (monthly, big lag) ---
    "CPIAUCSL":    {"label": "CPI",                   "freq": "m", "release_lag_days": 14},
    "CPILFESL":    {"label": "Core CPI",              "freq": "m", "release_lag_days": 14},
    "PCEPI":       {"label": "PCE",                   "freq": "m", "release_lag_days": 30},
    "PCEPILFE":    {"label": "Core PCE",              "freq": "m", "release_lag_days": 30},
    # --- policy rate & Fed balance sheet ---
    "FEDFUNDS":    {"label": "Fed Funds (eff, avg)",  "freq": "m", "release_lag_days": 2},
    "DFEDTARU":    {"label": "Fed Target Upper",      "freq": "d", "release_lag_days": 1},
    "WALCL":       {"label": "Fed Total Assets",      "freq": "w", "release_lag_days": 2},
    "WTREGEN":     {"label": "Treasury General Acct", "freq": "w", "release_lag_days": 2},
    "RRPONTSYD":   {"label": "Overnight Reverse Repo","freq": "d", "release_lag_days": 1},
    "WRESBAL":     {"label": "Reserve Balances",      "freq": "w", "release_lag_days": 2},
    # --- risk / credit (used as gates, not as thesis evidence) ---
    "BAMLH0A0HYM2":{"label": "HY OAS",                "freq": "d", "release_lag_days": 1},
    "VIXCLS":      {"label": "VIX",                   "freq": "d", "release_lag_days": 1},
    # --- fiscal fundamentals (quarterly, very slow) ---
    "GFDEGDQ188S": {"label": "Federal Debt / GDP",    "freq": "q", "release_lag_days": 90},
    "FYFSGDA188S": {"label": "Deficit / GDP (annual)","freq": "a", "release_lag_days": 270},
}

# Yahoo chart API symbols -> internal names
YAHOO_SERIES = {
    "GC=F":      {"key": "GOLD",   "label": "Gold (COMEX front)"},
    "DX-Y.NYB":  {"key": "DXY",    "label": "DXY"},
    "^NDX":      {"key": "NDX",    "label": "Nasdaq 100"},
    "^GSPC":     {"key": "SPX",    "label": "S&P 500"},
    "BTC-USD":   {"key": "BTC",    "label": "Bitcoin"},
    "^TNX":      {"key": "TNX",    "label": "10Y (Yahoo backup)"},
}

# --------------------------------------------------------------------------
# 2. COLLINEARITY GROUPS
# --------------------------------------------------------------------------
# DGS10 == DFII10 + T10YIE is an ARITHMETIC IDENTITY, not three independent
# observations. Scoring all three separately triple-counts one fact. Each group
# below has a cap on how much total score its members may contribute.
COLLINEAR_GROUPS = {
    "nominal10_decomp": {"members": ["DGS10", "DFII10", "T10YIE"], "cap": 18},
    "long_end":         {"members": ["DGS30", "DFII30"],           "cap": 22},
    "infl_exp":         {"members": ["T5YIE", "T10YIE", "T5YIFR"], "cap": 12},
}

# --------------------------------------------------------------------------
# 3. LOOKBACK WINDOWS (trading days)
# --------------------------------------------------------------------------
WINDOWS = {"w1": 5, "w4": 20, "w12": 60, "w52": 252}
PERCENTILE_LOOKBACK = 756          # 3 years of trading days
ZSCORE_LOOKBACK = 504              # 2 years

# --------------------------------------------------------------------------
# 4. LEVEL BANDS  (absolute anchors — the "human readable" half of the score)
# --------------------------------------------------------------------------
# Each band: (upper_bound, points). Last entry is the open-ended top band.
BANDS = {
    "DGS30": [(4.50, 0), (5.00, 4), (5.30, 8), (5.70, 12), (99.0, 15)],
    "THREEFYTP10": [(0.00, 0), (0.50, 3), (1.00, 6), (1.50, 9), (99.0, 12)],
    "DFII10_low": [(0.50, 12), (1.00, 9), (1.50, 6), (2.00, 3), (99.0, 0)],  # LOWER real yield = MORE repression
    "HY_OAS": [(3.00, 0), (4.50, 3), (6.00, 6), (99.0, 10)],
}

# --------------------------------------------------------------------------
# 5. SCORE WEIGHTS
# --------------------------------------------------------------------------
# Every score is built from components that each emit 0..max_points.
# Sum of max_points per score == 100 by construction (asserted in tests).

FISCAL_STRESS = {
    "level_30y":            18,   # absolute band
    "pctile_30y":           12,   # 3y percentile — catches regime relative to recent past
    "chg_30y_20d":          12,   # momentum
    "term_premium_level":   16,   # <- fiscal risk premium, the cleanest single read
    "term_premium_chg_60d": 12,
    "curve_stress":          8,   # 30Y-3M steepening while front-end pinned = fiscal, not growth
    "debt_gdp":              8,   # slow-moving fundamental
    "policy_intervention":  14,   # from the manual policy ledger (buybacks etc.)
}

FINANCIAL_REPRESSION = {
    "real_yield_level":         18,   # low/negative real yield = repression
    "real_yield_trend_60d":     18,   # FALLING real yield is the active signal
    "infl_realyield_divergence":22,   # inflation UP + real yield DOWN = the smoking gun
    "long_end_capped":          16,   # 30Y stops rising while deficits persist
    "policy_repression":        14,   # buyback/QE/YCC actions from ledger
    "dollar_debase":            12,   # broad dollar falling
}

DEBASEMENT = {
    "dxy_trend":            18,
    "gold_trend":           20,
    "gold_ath_proximity":   10,
    "btc_trend":            14,
    "infl_expectations":    16,   # breakevens / 5y5y rising
    "real_yield_support":   12,
    "gold_vs_dxy_coherence":10,   # they should move opposite; if not, signal is weak
}

BTC_LIQUIDITY = {
    "net_liquidity_trend":  22,   # WALCL - TGA - RRP
    "real_yield_direction": 18,
    "dxy_direction":        14,
    "btc_trend_20d":        14,
    "btc_vs_gold":          12,   # is BTC taking the baton from gold?
    "btc_vs_ndx":           10,   # decoupling from pure tech beta = macro-driven
    "credit_risk_gate":     10,   # HY spreads blowing out kills BTC regardless
}

# Composite: how the four roll up into one headline number.
COMPOSITE_WEIGHTS = {
    "fiscal_stress": 0.30,
    "financial_repression": 0.30,
    "debasement": 0.25,
    "btc_liquidity": 0.15,
}

# --------------------------------------------------------------------------
# 6. DRIVER vs CONFIRMATION SPLIT   (fixes the circularity problem)
# --------------------------------------------------------------------------
# The original spec makes Stage 3 require "Gold up + BTC up". That means the
# model can only tell you to buy gold AFTER gold has already gone up — it is a
# trend follower wearing a macro costume. We keep the confirmation logic (it is
# genuinely useful) but we SEPARATE it, so you can always see whether the
# CAUSAL claim (fiscal/policy/rates) and the MARKET VERDICT (gold/btc/usd)
# agree or disagree. The interesting states are the disagreements.
DRIVER_COMPONENTS = {
    "fiscal_stress": ["level_30y", "pctile_30y", "chg_30y_20d", "term_premium_level",
                      "term_premium_chg_60d", "curve_stress", "debt_gdp", "policy_intervention"],
    "financial_repression": ["real_yield_level", "real_yield_trend_60d",
                             "infl_realyield_divergence", "long_end_capped", "policy_repression"],
    "debasement": ["infl_expectations", "real_yield_support"],
    "btc_liquidity": ["net_liquidity_trend", "real_yield_direction"],
}
# Everything NOT listed above is treated as market confirmation.

# --------------------------------------------------------------------------
# 7. STAGE RULES
# --------------------------------------------------------------------------
# Hysteresis: a regime classifier that flips every three days is noise, not a
# regime. Entry needs ENTRY_PERSISTENCE consecutive qualifying days; exit needs
# the score to fall EXIT_BUFFER below the entry threshold.
ENTRY_PERSISTENCE = 3
EXIT_BUFFER = 6.0

# Asset signals need the same damping as the stage label, and for a sharper
# reason: the stage is context, but the SIGNAL is the line the user acts on.
# Measured failure — a debasement score of 39.9 vs 40.04 (0.15 points, both
# displaying as "40") flipped gold between 中性 and 偏多, because the score
# thresholds in signals.py are knife edges with no dead band. Requiring a new
# signal to hold for SIGNAL_PERSISTENCE consecutive days makes a one-day graze
# across a threshold a non-event.
SIGNAL_PERSISTENCE = 3

STAGE_DEFS = {
    0: {"name": "Normal Regime",            "name_cn": "常态",           "color": "#2ea043"},
    1: {"name": "Fiscal Stress",            "name_cn": "财政压力",       "color": "#d4a72c"},
    2: {"name": "Fiscal Dominance Watch",   "name_cn": "财政主导观察",   "color": "#e8873a"},
    3: {"name": "Financial Repression",     "name_cn": "金融压抑",       "color": "#e5484d"},
    4: {"name": "QE / YCC Regime Shift",    "name_cn": "货币体制转换",   "color": "#a457e8"},
}

STAGE_THRESHOLDS = {
    1: {"fiscal_stress_min": 35},
    2: {"fiscal_stress_min": 55, "extra_conditions_required": 2},
    3: {"financial_repression_min": 65, "hard_gates_required": "all"},
    4: {"requires_policy_fact": True},
}

# Stage 3 hard gates — ALL must hold. These are directional, not level-based.
STAGE3_GATES = {
    "inflation_not_falling":  "cpi_yoy_trend_3m >= -0.15",
    "real_yield_falling":     "dfii10_chg_60d < -0.10",
    "dollar_not_rising":      "dxy_chg_60d <= 1.0",
    "gold_rising":            "gold_chg_60d > 0",
    "long_end_not_spiraling": "dgs30_chg_60d < 0.60",
}

# Stage 4 may ONLY be entered on a recorded policy FACT (never on inference)
# AND only while the balance sheet is observably expanding.
#
# Learned the hard way: with a fact-only, 365-day rule the backtest put 28% of
# 2005-2026 in Stage 4, because "QE was announced eleven months ago" kept the
# label pinned long after the programme had been tapered away. A regime label
# has to describe the regime you are IN, not an anniversary. So Stage 4 now
# needs a recent trigger fact *and* corroboration from WALCL. Emergency
# facilities (BTFP etc.) were also dropped from the trigger set — they are
# liquidity backstops, not monetary regime changes; they still score under
# financial repression.
STAGE4_TRIGGER_EVENTS = {
    "qe_announced", "ycc_announced", "yield_target_announced",
    "balance_sheet_expansion_confirmed",
}
STAGE4_FACT_WINDOW_DAYS = 120
STAGE4_BALANCE_SHEET_MIN_60D_PCT = 1.0

# --------------------------------------------------------------------------
# 8. POLICY EVENT SCORING (ledger-driven, human-entered facts)
# --------------------------------------------------------------------------
POLICY_EVENT_SCORES = {
    "qt_normal":                      -10,
    "qt_taper":                        10,
    "qt_end":                          15,
    "rate_cut":                        10,
    "rate_hike":                      -12,
    "buyback_routine":                  5,
    "buyback_expanded":                15,
    "issuance_shift_to_bills":         12,
    "long_yield_discussion":           25,
    "emergency_facility_small":        15,
    "emergency_facility_large":        30,
    "balance_sheet_expansion_confirmed":35,
    "qe_announced":                    40,
    "ycc_announced":                   50,
    "yield_target_announced":          50,
}
POLICY_EVENT_HALFLIFE_DAYS = 45   # events decay; a buyback from 2024 is not news

# --------------------------------------------------------------------------
# 9. ASSET SIGNAL MAPPING
# --------------------------------------------------------------------------
SIGNAL_LEVELS = ["Strong Bearish", "Bearish", "Caution", "Neutral",
                 "Bullish", "Strong Bullish"]

# --------------------------------------------------------------------------
# 10. DATA FRESHNESS / CONFIDENCE
# --------------------------------------------------------------------------
# Confidence is knocked down when inputs are stale. A "Stage 3" call built on
# genuinely old CPI deserves a visible asterisk.
#
# The measure has to be staleness RELATIVE TO THE SERIES' OWN CADENCE, not
# absolute age. A monthly PCE observation is ~80 days old by observation date
# on any ordinary day of the year — that is the series working normally, not a
# data outage. Grading everything on absolute age had the monitor reporting
# 40/100 confidence on a completely healthy day.
#
#   grace = natural period + publication lag + buffer
FREQ_PERIOD_DAYS = {"d": 1, "w": 7, "m": 31, "q": 92, "a": 366}
STALENESS_PENALTY = {
    "d": {"buffer": 5,  "penalty_per_day": 2.0,  "max_penalty": 20},
    "w": {"buffer": 8,  "penalty_per_day": 1.0,  "max_penalty": 12},
    "m": {"buffer": 15, "penalty_per_day": 0.40, "max_penalty": 12},
    "q": {"buffer": 35, "penalty_per_day": 0.15, "max_penalty": 8},
    "a": {"buffer": 95, "penalty_per_day": 0.03, "max_penalty": 6},
}
MAX_TOTAL_STALENESS_PENALTY = 40.0

# Series whose staleness actually threatens the verdict. A stale annual
# deficit/GDP figure should not dent confidence the way a stale 30Y would.
CORE_SERIES = {
    "DGS30", "DGS10", "DFII10", "T10YIE", "T5YIFR", "THREEFYTP10",
    "DTWEXBGS", "CPIAUCSL", "CPILFESL", "WALCL", "GOLD", "BTC", "DXY",
}
NONCORE_PENALTY_SCALE = 0.35

HISTORY_START = "2003-01-01"   # DFII10 starts 2003; earlier = no real yield
