"""
Interactive Economic Dashboard

Builds index.html: three linked views of eight economic indicators for 173 countries.
  1. Ranking      – which countries rank highest on one indicator (bar chart)
  2. Spread       – how countries are spread on the ranking's indicator (histogram)
  3. Relationship – how two indicators relate across countries (scatter plot)

Seven indicators come from the dataset; GDP per person is calculated here from GDP and
population. GDP growth is left out: most countries report growth over the previous quarter
while some (like the US) report an annualized rate, and 72 of 173 countries have no value,
so comparing countries on it would mislead.

The charts are written in Python with Altair (Vega-Lite). A small script in the page
connects the controls: region filters, the indicator pickers, and the country
highlighter (type names, or click a bar, a dot, or a histogram bar).

Run:  python dashboard.py      (needs pandas and altair; see requirements.txt)
"""

import json
import math
from pathlib import Path

import altair as alt
import pandas as pd

HERE = Path(__file__).resolve().parent
DATA_FILE = HERE / "country_economics_data.csv"
OUT_FILE = HERE / "index.html"

# ---------------------------------------------------------------------------
# Indicators: label, unit, number format, and the scale used for spread-out data.
#   "log"    for GDP and GDP per person (all positive; the largest are 100s of times the smallest)
#   "symlog" for inflation and interest rates (a few extreme values; inflation can be negative)
#   "linear" for everything else
# ---------------------------------------------------------------------------
INDICATORS = {
    "GDP":             {"label": "GDP", "unit": "US$ billions", "unit_long": "US$ billions",
                        "prefix": "$", "suffix": " bn", "fmt": ",.0f", "scale": "log"},
    "GDP per person":  {"label": "GDP per person", "unit": "US$", "unit_long": "US$ per person",
                        "prefix": "$", "suffix": "", "fmt": ",.0f", "scale": "log"},
    "Inflation Rate":  {"label": "Inflation rate", "unit": "%", "unit_long": "Percent",
                        "prefix": "", "suffix": "%", "fmt": ",.1f", "scale": "symlog"},
    "Interest Rate":   {"label": "Interest rate", "unit": "%", "unit_long": "Percent",
                        "prefix": "", "suffix": "%", "fmt": ",.2f", "scale": "symlog"},
    "Jobless Rate":    {"label": "Unemployment rate", "unit": "%", "unit_long": "Percent",
                        "prefix": "", "suffix": "%", "fmt": ",.1f", "scale": "linear"},
    "Debt/GDP":        {"label": "Government debt", "unit": "% of GDP", "unit_long": "Percent of GDP",
                        "prefix": "", "suffix": "% of GDP", "fmt": ",.1f", "scale": "linear"},
    "Gov. Budget":     {"label": "Government budget balance", "unit": "% of GDP", "unit_long": "Percent of GDP",
                        "prefix": "", "suffix": "% of GDP", "fmt": ",.1f", "scale": "linear"},
    "Current Account": {"label": "Current account balance", "unit": "% of GDP", "unit_long": "Percent of GDP",
                        "prefix": "", "suffix": "% of GDP", "fmt": ",.1f", "scale": "linear"},
}
DEFAULTS = {"metric": "GDP", "top": 25, "mx": "Inflation Rate", "my": "Interest Rate"}

# Colors and chart chrome (light and dark), from a palette checked for contrast.
# One color for every country; highlighted countries keep it and the rest turn gray.
FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif'
THEMES = {
    "light": {"main": "#2a78d6", "soft": "#3987e5", "dim": "#d6d5ce", "ink": "#0b0b0b", "ink2": "#52514e",
              "muted": "#898781", "surface": "#fcfcfb", "grid": "#e1e0d9", "axis": "#c3c2b7"},
    "dark":  {"main": "#3987e5", "soft": "#256abf", "dim": "#45453f", "ink": "#f0efec", "ink2": "#c3c2b7",
              "muted": "#898781", "surface": "#1a1a19", "grid": "#2c2c2a", "axis": "#383835"},
}

GEO_FILTER = "(region == 'All' || datum.Region == region) && (subregion == 'All' || datum.Subregion == subregion)"
IS_HL = "indexof(hl, datum.Name) >= 0"
SHOWN = f"length(hl) == 0 || {IS_HL}"     # true when a mark should keep its color
LABEL_LIMIT = 6    # name labels on the scatter plot and histogram only when this few are highlighted
EXTRA_LIMIT = 8    # highlighted countries outside the top N are added to the ranking up to this many

# Tick values for the compressed scales (values outside the data range are dropped by Vega).
SYMLOG_TICKS = [-50, -20, -10, -5, -2, 0, 2, 5, 10, 20, 50, 100, 200]
LOG_TICKS = [1, 10, 100, 1000, 10000, 100000, 1000000]


# ---------------------------------------------------------------------------
# Scale helpers: forward transform (used to fit trend lines and bin histograms in the
# same space the axis is drawn in) and its inverse.
# ---------------------------------------------------------------------------
def fwd(scale, v):
    if scale == "log":
        return f"log({v}) / LN10"
    if scale == "symlog":
        return f"({v} < 0 ? -1 : 1) * log(1 + abs({v})) / LN10"
    return v


def inv(scale, t):
    if scale == "log":
        return f"pow(10, {t})"
    if scale == "symlog":
        return f"({t} < 0 ? -1 : 1) * (pow(10, abs({t})) - 1)"
    return t


def fwd_num(scale, v):
    if scale == "log":
        return math.log10(v)
    if scale == "symlog":
        return math.copysign(math.log10(1 + abs(v)), v)
    return v


def value_scale(scale):
    if scale == "log":
        return alt.Scale(type="log", nice=False, padding=8)
    if scale == "symlog":
        return alt.Scale(type="symlog", constant=1, nice=False, padding=8)
    return alt.Scale(type="linear", nice=True, zero=False, padding=8)


def value_axis(scale, title):
    common = dict(title=title, labelFlush=False, labelOverlap="greedy", labelSeparation=6)
    if scale == "log":
        return alt.Axis(values=LOG_TICKS, labelExpr="format(datum.value, ',~f')", **common)
    if scale == "symlog":
        return alt.Axis(values=SYMLOG_TICKS, labelExpr="format(datum.value, ',~f')", **common)
    return alt.Axis(format=",~f", tickCount=6, **common)


def params(*names):
    """Unbound parameters; the page script sets their values."""
    values = {
        "metric": DEFAULTS["metric"], "mx": DEFAULTS["mx"], "my": DEFAULTS["my"],
        "region": "All", "subregion": "All", "hl": [], "ind": INDICATORS,
        "c_main": THEMES["light"]["main"], "c_dim": THEMES["light"]["dim"], "c_soft": THEMES["light"]["soft"],
        "c_ink": THEMES["light"]["ink"], "c_muted": THEMES["light"]["muted"],
        "c_surface": THEMES["light"]["surface"],
    }
    return [alt.param(name=n, value=values[n]) for n in names]


def color_if_shown():
    return alt.condition(SHOWN, alt.value(alt.ExprRef(expr="c_main")), alt.value(alt.ExprRef(expr="c_dim")))


def value_text(field, metric_param):
    """Formatted value with its unit, e.g. "$29,185 bn" or "3.4%"."""
    m = f"ind[{metric_param}]"
    return f"{m}.prefix + format({field}, {m}.fmt) + {m}.suffix"


def axis_title(metric_param):
    m = f"ind[{metric_param}]"
    return f"expr:{m}.label + ' (' + {m}.unit + ')'"


# ---------------------------------------------------------------------------
# 1. Ranking: top N countries on one indicator (a few highlighted countries are always shown)
# ---------------------------------------------------------------------------
def ranking_chart(top_n):
    step = {10: 26, 25: 20, 50: 16, 0: 13}[top_n]   # the page may tighten 10 and 25 to fit the screen
    keep = "true" if top_n == 0 else f"datum.rank <= {top_n} || ({IS_HL} && length(hl) <= {EXTRA_LIMIT})"
    base = (
        alt.Chart(alt.NamedData(name="countries"))
        .transform_filter(GEO_FILTER)
        .transform_calculate(v="toNumber(datum[metric])")
        .transform_filter("isValid(datum.v) && isFinite(datum.v)")
        .transform_window(rank="rank()", sort=[alt.SortField("v", order="descending")])
        .transform_joinaggregate(n_ranked="count()")
        .transform_filter(keep)
        .transform_calculate(
            label="datum.rank + '. ' + datum.Name",
            value_text=value_text("datum.v", "metric"),
            rank_text="datum.rank + ' of ' + datum.n_ranked",
        )
        .transform_calculate(
            desc="datum.Name + ', ' + datum.Region + ': ' + ind[metric].label + ' ' + datum.value_text"
                 " + ', rank ' + datum.rank_text"
        )
    )
    y = alt.Y("label:N", sort=alt.EncodingSortField("rank", op="min", order="ascending"),
              scale=alt.Scale(paddingInner=0.28, paddingOuter=0.1),
              axis=alt.Axis(title=None, ticks=False, domain=False, labelLimit=170, labelPadding=8,
                            labelFontSize=11 if top_n in (10, 25) else 10))
    x = alt.X("v:Q", stack=None, scale=alt.Scale(nice=True),
              axis=alt.Axis(title=None, format=",~f", grid=True, tickCount=4, labelFlush=False))
    tooltip = [alt.Tooltip("Name:N", title="Country"), alt.Tooltip("value_text:N", title="__V__"),
               alt.Tooltip("rank_text:N", title="Rank"), alt.Tooltip("Region:N")]
    common = dict(y=y, x=x, color=color_if_shown(), tooltip=tooltip, description="desc:N")
    bars_pos = base.transform_filter("datum.v >= 0").mark_bar(cornerRadiusEnd=4, cursor="pointer").encode(**common)
    bars_neg = base.transform_filter("datum.v < 0").mark_bar(
        cornerRadiusTopLeft=4, cornerRadiusBottomLeft=4, cursor="pointer").encode(**common)
    layers = [bars_pos, bars_neg]
    if top_n in (10, 25):  # value at the tip of each bar when there are few bars
        text = dict(y=y, x=x, text=alt.Text("value_text:N"))
        layers += [
            base.transform_filter("datum.v >= 0").mark_text(align="left", dx=5, fontSize=10.5, aria=False,
                                                             color=alt.ExprRef(expr="c_ink")).encode(**text),
            base.transform_filter("datum.v < 0").mark_text(align="right", dx=-5, fontSize=10.5, aria=False,
                                                            color=alt.ExprRef(expr="c_ink")).encode(**text),
        ]
    return (
        alt.layer(*layers)
        .add_params(*params("metric", "region", "subregion", "hl", "ind", "c_main", "c_dim", "c_ink"))
        .properties(width="container", height=alt.Step(step), autosize={"type": "fit-x", "contains": "padding"})
    )


# ---------------------------------------------------------------------------
# 2. Spread: histogram of the ranking's indicator. Bins holding highlighted countries keep
#    their color; clicking a bin highlights its countries (handled by the page script).
# ---------------------------------------------------------------------------
def spread_chart(scale):
    valid = "isValid(datum.v) && isFinite(datum.v)" + (" && datum.v > 0" if scale == "log" else "")
    base = (
        alt.Chart(alt.NamedData(name="countries"))
        .transform_filter(GEO_FILTER)
        .transform_calculate(v="toNumber(datum[metric])")
        .transform_filter(valid)
        .transform_calculate(t=fwd(scale, "datum.v"), hl_num=f"{IS_HL} ? 1 : 0")
    )
    if scale == "log":
        axis = alt.Axis(title=axis_title("metric"), values=[fwd_num("log", v) for v in LOG_TICKS],
                        labelExpr=f"format({inv('log', 'datum.value')}, ',~f')", labelFlush=False)
    elif scale == "symlog":
        axis = alt.Axis(title=axis_title("metric"), values=[fwd_num("symlog", v) for v in SYMLOG_TICKS],
                        labelExpr=f"format({inv('symlog', 'datum.value')}, ',.2~r')", labelFlush=False)
    else:
        axis = alt.Axis(title=axis_title("metric"), format=",~f", labelFlush=False)
    bins = (
        base.transform_bin(["b0", "b1"], "t", bin=alt.Bin(maxbins=24))
        .transform_aggregate(n="count()", n_hl="sum(hl_num)", groupby=["b0", "b1"])
        .transform_calculate(
            lo=inv(scale, "datum.b0"), hi=inv(scale, "datum.b1"),
            range_text=(f"ind[metric].prefix + format(datum.lo, ',.3~r') + ' to ' + "
                        f"format(datum.hi, ',.3~r') + ind[metric].suffix"),
        )
        .transform_calculate(desc="datum.range_text + ': ' + datum.n + (datum.n == 1 ? ' country' : ' countries')")
        .mark_bar(binSpacing=2, cornerRadiusEnd=4, cursor="pointer")
        .encode(
            x=alt.X("b0:Q", bin="binned", axis=axis, scale=alt.Scale(zero=False)),
            x2="b1:Q",
            y=alt.Y("n:Q", stack=None, axis=alt.Axis(title="Countries", format="d", tickMinStep=1, grid=True)),
            color=alt.condition("length(hl) == 0 || datum.n_hl > 0",
                                alt.value(alt.ExprRef(expr="c_soft")), alt.value(alt.ExprRef(expr="c_dim"))),
            tooltip=[alt.Tooltip("range_text:N", title="Range"), alt.Tooltip("n:Q", title="Countries")],
            description="desc:N",
        )
    )
    # Labels for highlighted countries alternate between two rows so close neighbors don't overlap.
    marked = (
        base.transform_filter(f"{IS_HL} && length(hl) <= {LABEL_LIMIT}")
        .transform_window(lane="row_number()", sort=[alt.SortField("t", order="ascending")])
        .transform_calculate(desc=f"datum.Name + ': ' + {value_text('datum.v', 'metric')}",
                             label_y="-6 - ((datum.lane - 1) % 2) * 14")
    )
    rules = marked.mark_rule(strokeWidth=2, color=alt.ExprRef(expr="c_ink")).encode(x="t:Q", description="desc:N")
    names = marked.mark_text(align="left", baseline="bottom", dx=-4, fontSize=11, fontWeight=600, aria=False,
                             color=alt.ExprRef(expr="c_ink")).encode(x="t:Q", y=alt.Y("label_y:Q", scale=None),
                                                                     text="Name:N")
    return (
        alt.layer(bins, rules, names)
        .add_params(*params("metric", "region", "subregion", "hl", "ind", "c_soft", "c_dim", "c_ink"))
        .properties(width="container", height=230, padding={"left": 4, "right": 12, "top": 38, "bottom": 4},
                    autosize={"type": "fit-x", "contains": "padding"})
    )


# ---------------------------------------------------------------------------
# 3. Relationship: two indicators, one dot per country, with a trend line and medians
# ---------------------------------------------------------------------------
def relationship_chart(xs, ys):
    valid = "isValid(datum.x) && isFinite(datum.x) && isValid(datum.y) && isFinite(datum.y)"
    if xs == "log":
        valid += " && datum.x > 0"
    if ys == "log":
        valid += " && datum.y > 0"
    base = (
        alt.Chart(alt.NamedData(name="countries"))
        .transform_filter(GEO_FILTER)
        .transform_calculate(x="toNumber(datum[mx])", y="toNumber(datum[my])")
        .transform_filter(valid)
    )
    X = alt.X("x:Q", scale=value_scale(xs), axis=value_axis(xs, axis_title("mx")))
    Y = alt.Y("y:Q", scale=value_scale(ys), axis=value_axis(ys, axis_title("my")))
    marks = base.transform_calculate(
        is_hl=IS_HL, hl_order=f"{IS_HL} ? 1 : 0",
        x_text=value_text("datum.x", "mx"), y_text=value_text("datum.y", "my"),
    ).transform_calculate(
        desc="datum.Name + ': ' + ind[mx].label + ' ' + datum.x_text + ', ' + ind[my].label + ' ' + datum.y_text"
    )
    tooltip = [alt.Tooltip("Name:N", title="Country"), alt.Tooltip("x_text:N", title="__X__"),
               alt.Tooltip("y_text:N", title="__Y__"), alt.Tooltip("Region:N")]
    medians = base.transform_aggregate(x_med="median(x)", y_med="median(y)")
    trend = (
        base.transform_calculate(tx=fwd(xs, "datum.x"), ty=fwd(ys, "datum.y"))
        .transform_regression("tx", "ty")
        .transform_calculate(x=inv(xs, "datum.tx"), y=inv(ys, "datum.ty"))
        .mark_line(strokeDash=[6, 4], strokeWidth=2, color=alt.ExprRef(expr="c_muted"), aria=False)
        .encode(x="x:Q", y="y:Q")
    )
    x_med = medians.mark_rule(strokeWidth=1, color=alt.ExprRef(expr="c_muted"), aria=False).encode(x="x_med:Q")
    y_med = medians.mark_rule(strokeWidth=1, color=alt.ExprRef(expr="c_muted"), aria=False).encode(y="y_med:Q")
    dots = marks.mark_circle(opacity=1, stroke=alt.ExprRef(expr="c_surface"), strokeWidth=1.5, cursor="pointer").encode(
        x=X, y=Y, color=color_if_shown(),
        size=alt.condition(IS_HL, alt.value(150), alt.value(64)),
        order=alt.Order("hl_order:Q"), tooltip=tooltip, description="desc:N",
    )
    # A larger invisible target around each dot, so the pointer doesn't have to land dead-center.
    hit = marks.mark_circle(opacity=0, size=500, aria=False, cursor="pointer").encode(x=X, y=Y, tooltip=tooltip)
    names = marks.transform_filter(f"{IS_HL} && length(hl) <= {LABEL_LIMIT}").mark_text(
        align="left", dx=9, dy=-7, fontSize=11, fontWeight=600, color=alt.ExprRef(expr="c_ink"), aria=False
    ).encode(x=X, y=Y, text="Name:N")
    return (
        alt.layer(x_med, y_med, trend, hit, dots, names)
        .add_params(*params("mx", "my", "region", "subregion", "hl", "ind",
                            "c_main", "c_dim", "c_ink", "c_muted", "c_surface"))
        .properties(width="container", height=430, autosize={"type": "fit-x", "contains": "padding"})
    )


# ---------------------------------------------------------------------------
# Spec export: Altair builds every variant; titles that depend on the chosen indicator
# are turned into Vega-Lite expressions (Altair's schema only accepts plain strings there).
# ---------------------------------------------------------------------------
def to_spec(chart):
    spec = chart.to_dict()
    spec.pop("$schema", None)
    spec.pop("config", None)

    def walk(node):
        if isinstance(node, dict):
            for k, val in list(node.items()):
                if k == "title" and isinstance(val, str) and val.startswith("expr:"):
                    node[k] = {"expr": val[5:]}
                else:
                    walk(val)
        elif isinstance(node, list):
            for item in node:
                walk(item)
    walk(spec)
    return spec


def vega_config(t):
    return {
        "background": t["surface"], "font": FONT, "padding": {"left": 4, "right": 12, "top": 6, "bottom": 4},
        "view": {"stroke": None},
        "axis": {"gridColor": t["grid"], "gridWidth": 1, "domainColor": t["axis"], "tickColor": t["axis"],
                 "labelColor": t["ink2"], "labelFontSize": 11, "titleColor": t["ink2"], "titleFontSize": 12,
                 "titleFontWeight": 600, "titlePadding": 10},
        "text": {"font": FONT},
    }


def load_data():
    df = pd.read_csv(DATA_FILE)
    source_cols = [c for c in INDICATORS if c != "GDP per person"]
    df = df[["Name", "Region", "Subregion", "Population"] + source_cols].copy()
    for col in source_cols + ["Population"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ["Name", "Region", "Subregion"]:
        df[col] = df[col].astype("string").str.strip()
    # GDP per person in US$: GDP is in US$ billions and population in millions.
    per_person = df["GDP"] * 1000 / df["Population"]
    df["GDP per person"] = per_person.where(df["Population"] > 0).round(0)
    df = df[["Name", "Region", "Subregion"] + list(INDICATORS)]
    df = df.sort_values("Name").reset_index(drop=True)
    records = json.loads(df.to_json(orient="records"))  # NaN becomes null
    return df, records


def main():
    df, records = load_data()
    subregions = {r: sorted(df.loc[df["Region"] == r, "Subregion"].dropna().unique().tolist())
                  for r in sorted(df["Region"].dropna().unique())}
    specs = {
        "rank": {f"n{n}": to_spec(ranking_chart(n)) for n in (10, 25, 50, 0)},
        "rel": {f"{xs}|{ys}": to_spec(relationship_chart(xs, ys))
                for xs in ("linear", "log", "symlog") for ys in ("linear", "log", "symlog")},
        "spread": {s: to_spec(spread_chart(s)) for s in ("linear", "log", "symlog")},
    }
    themes = {name: {**t, "vega": vega_config(t)} for name, t in THEMES.items()}
    payload = {
        "data": records, "indicators": INDICATORS, "defaults": DEFAULTS, "subregions": subregions,
        "specs": specs, "themes": themes, "limits": {"label": LABEL_LIMIT, "extra": EXTRA_LIMIT},
    }
    template = (HERE / "page_template.html").read_text(encoding="utf-8")
    page = (template
            .replace("/*__PAYLOAD__*/", json.dumps(payload, separators=(",", ":")).replace("</", "<\\/"))
            .replace("__N_COUNTRIES__", str(len(df)))
            .replace("__N_INDICATORS__", str(len(INDICATORS))))
    OUT_FILE.write_text(page, encoding="utf-8")
    print(f"Saved {OUT_FILE.name}: {len(df)} countries, {len(INDICATORS)} indicators, "
          f"{sum(len(v) for v in specs.values())} chart variants, {len(page) / 1024:.0f} KB")


if __name__ == "__main__":
    main()
