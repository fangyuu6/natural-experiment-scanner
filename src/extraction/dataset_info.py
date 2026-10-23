"""
数据源信息库
提供常用数据库的详细信息、变量列表、访问方式
"""
from typing import Dict, List, Optional


# 数据源详细信息
DATASET_INFO = {
    "CPS": {
        "name": "Current Population Survey",
        "provider": "U.S. Census Bureau & Bureau of Labor Statistics",
        "url": "https://www.census.gov/programs-surveys/cps.html",
        "access": "Public, IPUMS-CPS recommended",
        "frequency": "Monthly",
        "sample_size": "~60,000 households/month",
        "key_variables": [
            "employment status",
            "hours worked",
            "earnings/wages",
            "occupation",
            "industry",
            "demographics (age, education, race)",
            "union membership",
            "health insurance coverage"
        ],
        "time_coverage": "1962-present",
        "geography": "State, metro area (limited)",
        "best_for": "Labor market outcomes, employment, wages, hours worked",
        "limitations": "Cross-sectional (not panel), no firm identifiers",
        "cost": "Free"
    },

    "QCEW": {
        "name": "Quarterly Census of Employment and Wages",
        "provider": "Bureau of Labor Statistics",
        "url": "https://www.bls.gov/cew/",
        "access": "Public",
        "frequency": "Quarterly",
        "sample_size": "Universe (all covered employers)",
        "key_variables": [
            "employment levels",
            "total wages",
            "average weekly wage",
            "establishments count",
            "industry (NAICS)"
        ],
        "time_coverage": "1990-present",
        "geography": "County, MSA, state",
        "best_for": "Firm-level employment, wages by industry/geography",
        "limitations": "No worker demographics, self-employed not covered",
        "cost": "Free"
    },

    "ACS": {
        "name": "American Community Survey",
        "provider": "U.S. Census Bureau",
        "url": "https://www.census.gov/programs-surveys/acs",
        "access": "Public, IPUMS-USA recommended",
        "frequency": "Annual",
        "sample_size": "~3.5 million addresses/year",
        "key_variables": [
            "income",
            "employment status",
            "occupation/industry",
            "education",
            "housing",
            "demographics",
            "commuting",
            "health insurance"
        ],
        "time_coverage": "2000-present (1-year), 2005-present (5-year)",
        "geography": "Block group level (5-year), state/county (1-year)",
        "best_for": "Detailed demographics, geographic variation, housing",
        "limitations": "Annual only (no monthly/quarterly), smaller sample than decennial census",
        "cost": "Free"
    },

    "MSHA": {
        "name": "Mine Safety and Health Administration Data",
        "provider": "U.S. Department of Labor",
        "url": "https://www.msha.gov/mine-data-retrieval-system",
        "access": "Public",
        "frequency": "Continuous",
        "sample_size": "All U.S. mines",
        "key_variables": [
            "mine employment",
            "hours worked",
            "production (coal/mineral output)",
            "accidents/violations",
            "mine characteristics (size, type)",
            "inspector visits"
        ],
        "time_coverage": "1970s-present (varies by dataset)",
        "geography": "Mine-level (with coordinates)",
        "best_for": "Mining industry employment, safety, productivity",
        "limitations": "Industry-specific, no worker demographics",
        "cost": "Free"
    },

    "IRS SOI": {
        "name": "IRS Statistics of Income",
        "provider": "Internal Revenue Service",
        "url": "https://www.irs.gov/statistics",
        "access": "Public aggregates; microdata restricted",
        "frequency": "Annual",
        "sample_size": "Varies (stratified samples)",
        "key_variables": [
            "adjusted gross income",
            "wages/salaries",
            "self-employment income",
            "capital gains",
            "deductions",
            "tax liability",
            "business income (1099-K, Schedule C)"
        ],
        "time_coverage": "1916-present (varies)",
        "geography": "State, ZIP code (aggregated)",
        "best_for": "Income distribution, tax compliance, gig economy (1099-K)",
        "limitations": "Aggregated public data; microdata requires approval",
        "cost": "Free (aggregates), restricted access (microdata)"
    },

    "MEPS": {
        "name": "Medical Expenditure Panel Survey",
        "provider": "Agency for Healthcare Research and Quality",
        "url": "https://www.meps.ahrq.gov/",
        "access": "Public",
        "frequency": "Annual",
        "sample_size": "~30,000 individuals",
        "key_variables": [
            "health insurance coverage",
            "medical expenditures",
            "health status",
            "employment",
            "income",
            "access to care",
            "prescription drugs"
        ],
        "time_coverage": "1996-present",
        "geography": "Regional (4 Census regions)",
        "best_for": "Health insurance, healthcare costs, labor-health interactions",
        "limitations": "Small sample, limited geography",
        "cost": "Free"
    },

    "IPEDS": {
        "name": "Integrated Postsecondary Education Data System",
        "provider": "National Center for Education Statistics",
        "url": "https://nces.ed.gov/ipeds/",
        "access": "Public",
        "frequency": "Annual",
        "sample_size": "All Title IV institutions (~6,500)",
        "key_variables": [
            "enrollment",
            "completions (degrees)",
            "faculty/staff",
            "finances/revenue",
            "tuition/fees",
            "student aid",
            "graduation rates"
        ],
        "time_coverage": "1980-present",
        "geography": "Institution-level",
        "best_for": "Higher education employment, enrollment, finances",
        "limitations": "Institution-level only (no student microdata)",
        "cost": "Free"
    },

    "BLS": {
        "name": "Bureau of Labor Statistics (various surveys)",
        "provider": "U.S. Department of Labor",
        "url": "https://www.bls.gov/data/",
        "access": "Public",
        "frequency": "Varies (monthly, quarterly, annual)",
        "sample_size": "Varies by survey",
        "key_variables": [
            "employment (CES)",
            "unemployment rate",
            "wages (OES)",
            "productivity",
            "labor force participation",
            "consumer price index",
            "job openings (JOLTS)"
        ],
        "time_coverage": "Varies (some back to 1940s)",
        "geography": "National, state, MSA",
        "best_for": "Aggregate labor market indicators, industry wages",
        "limitations": "Mostly aggregated, limited microdata",
        "cost": "Free"
    }
}


def get_dataset_details(dataset_abbr: str) -> Optional[Dict]:
    """获取数据源详细信息"""
    return DATASET_INFO.get(dataset_abbr.upper())


def get_all_datasets() -> List[str]:
    """获取所有可用数据源列表"""
    return list(DATASET_INFO.keys())


def search_datasets_by_variable(variable_keyword: str) -> List[str]:
    """根据变量关键词搜索数据源"""
    matches = []
    keyword_lower = variable_keyword.lower()

    for abbr, info in DATASET_INFO.items():
        # 搜索key_variables和best_for字段
        if any(keyword_lower in var.lower() for var in info["key_variables"]):
            matches.append(abbr)
        elif keyword_lower in info["best_for"].lower():
            matches.append(abbr)

    return matches


def format_dataset_card(dataset_abbr: str) -> str:
    """格式化数据源信息卡片（用于HTML展示）"""
    info = get_dataset_details(dataset_abbr)
    if not info:
        return f"未找到 {dataset_abbr} 的详细信息"

    card = f"""
### {info['name']} ({dataset_abbr})

**提供方**: {info['provider']}
**访问方式**: {info['access']}
**数据频率**: {info['frequency']}
**样本规模**: {info['sample_size']}
**时间跨度**: {info['time_coverage']}
**地理粒度**: {info['geography']}
**费用**: {info['cost']}

**核心变量**:
{chr(10).join(f"- {var}" for var in info['key_variables'][:6])}

**最适合研究**: {info['best_for']}

**局限性**: {info['limitations']}

📊 [访问数据库]({info['url']})
"""
    return card
