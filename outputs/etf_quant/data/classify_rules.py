"""宽基 ETF 自动分类规则（可解释、可复现）。

分类不依赖基金名称单一字段，而是结合“跟踪标的 + 业绩比较基准 + 基金名称”综合判断。
优先级（高 -> 低）：
  杠杆/反向 > 增强 > 债券/货币 > 商品 > 跨境 > 因子/策略 > 行业/主题 > 宽基。

对容易混淆的境内/跨境指数（如 MSCI 中国A股 与 MSCI中国）做显式区分，
避免把境内宽基误判为跨境。
"""

from __future__ import annotations

import re


LEVERAGE = ["杠杆", "反向", "两倍", "三倍", "1.5倍", "分级", "看跌", "看涨期权"]
ENHANCED = ["增强", "指数增强", "指增"]

BOND_MONEY = ["债", "国债", "国开债", "城投", "信用债", "转债", "可转债", "货币", "现金",
              "短融", "政金债", "利率债", "金融债", "同业存单", "债券", "添益", "理财金",
              "保证金", "交易货币"]

COMMODITY = ["黄金", "白银", "原油", "石油", "豆粕", "能源", "有色", "煤炭", "钢铁",
             "稀土", "铜", "锂", "镍", "锌", "商品", "期货", "油气", "矿业", "金属"]

# 跨境/境外（注意：MSCI中国A股、MSCI中国A50、富时中国A50、标普中国A股为境内宽基，需排除）
CROSS_BORDER = [
    "恒生", "港股", "香港", "H股", "国企指数", "纳斯达克", "标普500", "道琼斯",
    "日经", "德国", "法国", "英国", "富时100", "富时全球", "越南", "印度", "沙特",
    "亚太", "亚洲", "新兴市场", "美国", "美股", "跨境", "海外", "全球", "中概",
    "互联网", "港股通", "沪港深", "港股科技", "恒生科技", "恒生医疗",
    "MSCI新兴", "MSCI全球", "标普港股通", "标普全球", "MSCI港股",
]
# MSCI中国 与 标普 需要负向断言，避免误伤境内 A 股宽基
CROSS_BORDER_RE = [r"MSCI中国(?!A)", r"标普(?!中国A)"]

FACTOR = ["红利", "价值", "成长", "低波", "低波动", "质量", "动量", "等权", "基本面",
          "ESG", "央企", "国企", "股东回报", "高股息", "自由现金流", "现金牛",
          "红利低波", "红利质量", "质量成长", "治理", "责任", "共同富裕", "国企改革",
          "央企创新", "央企科技", "国企一带一路", "央企股东回报"]

INDUSTRY = [
    "证券", "银行", "保险", "非银", "金融", "医药", "医疗", "生物", "创新药",
    "白酒", "食品饮料", "食品", "消费", "芯片", "半导体", "集成电路", "电子",
    "新能源", "光伏", "电池", "储能", "军工", "国防", "地产", "房地产", "有色金属",
    "煤炭", "钢铁", "计算机", "软件", "传媒", "游戏", "通信", "5G", "汽车",
    "智能车", "家电", "农业", "养殖", "化工", "建材", "机械", "电力", "公用事业",
    "环保", "物流", "航运", "旅游", "酒店", "航空", "纺织", "轻工", "高端制造",
    "机器人", "人工智能", "AI", "云计算", "大数据", "数字经济", "科技", "信创",
    "酒", "基建", "中药", "石化", "卫星", "新材料", "养老", "影视", "智能制造",
    "物联网", "高端装备", "低碳", "碳中和", "电信", "船舶", "稀有金属", "稀有",
    "交运", "农牧渔", "养殖业", "农业主题", "国防军工", "证券保险", "生物医药",
    "医疗保健", "科创芯片", "半导体材料", "集成电路产业",
]

# 宽基指数（跟踪标的字段中的规范写法，按“精确子串”匹配，避免前缀泄漏）
# 注：中证全指 只有“中证全指/中证全指指数”本身才是宽基；“中证全指食品/医药/…”是细分行业。
BROAD_EXACT = [
    "中证全指", "中证A股全指", "中证A股", "国证A50",
]
BROAD_PATTERNS = [
    r"沪深300", r"上证50", r"上证180", r"上证指数", r"上证综合", r"上证综指",
    r"上证超大盘", r"上证中盘", r"上证380", r"深证成指", r"深证成份", r"深证100",
    r"中证100(?!0)", r"中证1000", r"中证2000", r"中证500", r"中证800",
    r"中证A500", r"中证A50(?!0)", r"中证A100", r"国证2000", r"创业板指", r"创业板50",
    r"科创50", r"科创板50", r"科创100", r"科创综指", r"双创50", r"MSCI中国A股",
    r"MSCI中国A50", r"富时中国A50", r"标普中国A股",
]


def _norm(s: str | None) -> str:
    if s is None or (isinstance(s, float) and s != s):  # 处理 NaN
        return ""
    return str(s).strip().replace(" ", "")


def _any_in(text: str, kws: list[str]) -> bool:
    return any(k in text for k in kws)


def _any_re(text: str, pats: list[str]) -> bool:
    return any(re.search(p, text) for p in pats)


def broad_subtype(track_index: str, name: str) -> str | None:
    """宽基子类别，用于同风格重复暴露控制。"""
    from ..config import BROAD_SUBTYPES
    text = _norm(track_index) + _norm(name)
    for sub, patterns in BROAD_SUBTYPES:
        for pat in patterns:
            if re.search(pat, text):
                return sub
    return None


def _is_broad_match(ti: str, bm: str) -> str | None:
    """在跟踪标的/基准中查找宽基匹配，返回命中的模式描述。"""
    for pat in BROAD_PATTERNS:
        if re.search(pat, ti) or re.search(pat, bm):
            return pat
    # 中证全指 只做精确匹配
    for name in BROAD_EXACT:
        if ti == name or ti == name + "指数" or bm == name or bm == name + "指数":
            return name
    return None


# 规范指数名（用于把跟踪同一指数的多只 ETF 去重成一个“独立投资机会”）
CANONICAL_INDEX = [
    ("沪深300", r"沪深300"),
    ("上证50", r"上证50"),
    ("上证180", r"上证180"),
    ("上证综指", r"上证(综合|综指|指数)"),
    ("深证成指", r"深证成(指|份)"),
    ("深证100", r"深证100|深100"),
    ("中证100", r"中证100(?!0)"),
    ("中证A50", r"中证A50(?!0)"),
    ("中证A100", r"中证A100"),
    ("富时中国A50", r"富时中国A50"),
    ("MSCI中国A50", r"MSCI中国A50"),
    ("中证500", r"中证500"),
    ("上证中盘", r"上证中盘"),
    ("上证380", r"上证380"),
    ("中证1000", r"中证1000"),
    ("中证2000", r"中证2000"),
    ("国证2000", r"国证2000"),
    ("创业板指", r"创业板指"),
    ("创业板50", r"创业板50"),
    ("科创50", r"科创50|科创板50"),
    ("中证A500", r"中证A500"),
    ("中证800", r"中证800"),
    ("中证全指", r"中证全指"),
    ("中证A股", r"中证A股"),
    ("MSCI中国A股", r"MSCI中国A股"),
]


def canonical_index(track_index: str | None, name: str) -> str:
    """返回去重用的规范指数名；无法识别时退回原始跟踪标的或基金名。"""
    ti = _norm(track_index)
    nm = _norm(name)
    text = ti + nm
    for label, pat in CANONICAL_INDEX:
        if re.search(pat, text):
            return label
    return ti or nm or "未知"


def classify(track_index: str | None, benchmark: str | None, name: str) -> dict:
    """返回 {category, basis, subtype}。"""
    ti = _norm(track_index)
    bm = _norm(benchmark)
    nm = _norm(name)
    joined = ti + bm + nm

    if _any_in(joined, LEVERAGE):
        return {"category": "leveraged", "basis": "名称/标的含杠杆或反向", "subtype": None}
    if _any_in(nm, ENHANCED):
        return {"category": "enhanced", "basis": "指数增强型（主动叠加）", "subtype": None}
    if _any_in(joined, BOND_MONEY):
        return {"category": "bond_or_money", "basis": "债券/货币类", "subtype": None}
    if _any_in(joined, COMMODITY):
        return {"category": "commodity", "basis": "商品/期货类", "subtype": None}
    if _any_in(joined, CROSS_BORDER) or _any_re(joined, CROSS_BORDER_RE):
        return {"category": "cross_border", "basis": "跨境/境外指数", "subtype": None}
    if _any_in(joined, FACTOR):
        return {"category": "factor_or_theme", "basis": "因子/策略类（红利/价值/低波/治理等）", "subtype": None}
    if _any_in(joined, INDUSTRY):
        return {"category": "industry_or_theme", "basis": "行业/主题类", "subtype": None}

    hit = _is_broad_match(ti, bm)
    if hit:
        subtype = broad_subtype(ti, nm)
        return {"category": "broad", "basis": f"跟踪/基准为宽基指数（匹配 {hit}）", "subtype": subtype}

    # 宽基兜底：跟踪标的字段缺失但名称明确为宽基
    if not ti:
        for pat in BROAD_PATTERNS:
            if re.search(pat, nm):
                subtype = broad_subtype("", nm)
                return {"category": "broad", "basis": f"跟踪标的缺失，名称匹配 {pat}", "subtype": subtype}

    return {"category": "other", "basis": "未匹配，需人工复核", "subtype": None}
