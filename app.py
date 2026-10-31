"""Sri Lanka market price dashboard and data import controls."""

from datetime import date, timedelta
from contextlib import closing
import os

import pandas as pd
import plotly.express as px
import streamlit as st

# Community Cloud injects secrets separately from the checked-in source.
if not os.environ.get("DATABASE_URL"):
    try:
        os.environ["DATABASE_URL"] = st.secrets["database"]["url"]
    except Exception:
        pass

import database as db
from pdf_parser import report_url
from scraper import backfill, import_report

st.set_page_config(page_title="Sri Lanka Market Prices", page_icon="📈", layout="wide")
db.init_db()

st.title("Sri Lanka Market Price Intelligence")
st.caption("CBSL daily commodity prices to support export planning")


def read_prices() -> pd.DataFrame:
    with closing(db.get_connection()) as conn:
        rows = conn.execute(
            """SELECT p.*, pr.pdf_url FROM prices p
               JOIN price_reports pr ON p.report_date = pr.report_date
               ORDER BY p.report_date""",
        ).fetchall()
    frame = pd.DataFrame([dict(row) for row in rows])
    if not frame.empty:
        frame["report_date"] = pd.to_datetime(frame["report_date"])
    return frame


df = read_prices()
latest_date = df["report_date"].max().date() if not df.empty else None

with st.sidebar:
    st.header("Data")
    if db.DATABASE_URL:
        st.success("Persistent PostgreSQL storage")
    else:
        st.warning("SQLite storage is active. Use a PostgreSQL DATABASE_URL for persistent Community Cloud data.")
    st.write(f"Reports stored: **{db.get_report_count():,}**")
    if latest_date:
        st.write(f"Latest report: **{latest_date:%d %b %Y}**")
    st.caption("CBSL does not publish reports on every date. Missing dates are skipped.")
    with st.expander("Fetch a report"):
        fetch_date = st.date_input("Report date", value=date.today() - timedelta(days=1), max_value=date.today())
        if st.button("Fetch selected date", use_container_width=True):
            with st.spinner(f"Fetching {fetch_date:%d %b %Y}…"):
                try:
                    imported, rows, message = import_report(fetch_date, force=True)
                    st.success(f"{message}: {rows} price observations" if imported else message)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Could not import report: {exc}")
        st.link_button("Open CBSL PDF", report_url(fetch_date), use_container_width=True)

    with st.expander("Historical backfill"):
        st.warning("Backfill sends one request per calendar day. Use a date range you intend to import; missing weekend and holiday reports are skipped.")
        start = st.date_input("Start date", value=date(2016, 1, 1), key="backfill_start", max_value=date.today())
        end = st.date_input("End date", value=date.today() - timedelta(days=1), key="backfill_end", max_value=date.today())
        if st.button("Start / resume backfill", use_container_width=True, disabled=start > end):
            bar = st.progress(0)
            status = st.empty()
            total = (end - start).days + 1
            count = 0
            for index, (day, imported, rows, message) in enumerate(backfill(start, end), start=1):
                count += int(imported)
                status.write(f"{day:%d %b %Y}: {message} ({count} reports imported)")
                bar.progress(index / total)
            st.success(f"Backfill complete: imported {count} reports.")
            st.rerun()

tab_overview, tab_explore, tab_compare, tab_intelligence, tab_export = st.tabs(
    ["Overview", "Explore prices", "Compare", "Intelligence", "Export data"]
)

with tab_overview:
    if df.empty:
        st.info("No prices are stored yet. Fetch the latest CBSL report or choose a date range under Historical backfill.")
        st.markdown("**Data source:** Central Bank of Sri Lanka daily price reports.")
    else:
        st.subheader(f"Latest reported prices · {latest_date:%d %B %Y}")
        latest = df[df["report_date"].dt.date == latest_date].copy()
        key_items = latest["item_name"].drop_duplicates().head(6).tolist()
        featured = latest[latest["item_name"].isin(key_items)]
        for start_col in range(0, min(len(featured), 12), 4):
            cols = st.columns(4)
            for col, (_, row) in zip(cols, featured.iloc[start_col : start_col + 4].iterrows()):
                delta = None
                if pd.notna(row["yesterday_price"]) and pd.notna(row["today_price"]):
                    delta = row["today_price"] - row["yesterday_price"]
                col.metric(
                    f"{row['item_name']} · {row['market']} ({row['price_type']})",
                    f"Rs. {row['today_price']:,.2f} {row['unit']}" if pd.notna(row["today_price"]) else "Not reported",
                    f"{delta:+,.2f}" if delta is not None else None,
                )
        st.caption("Cards show selected rows from the latest report; use Explore prices to filter the full dataset.")

        st.subheader("Latest price changes")
        latest["change"] = latest["today_price"] - latest["yesterday_price"]
        changes = latest.dropna(subset=["change"]).sort_values("change", ascending=False)
        if not changes.empty:
            st.dataframe(
                changes[["category", "item_name", "market", "price_type", "yesterday_price", "today_price", "change", "unit"]],
                use_container_width=True,
                hide_index=True,
            )
        st.caption(f"Report source: [CBSL PDF]({latest.iloc[0]['pdf_url']})")

with tab_explore:
    if df.empty:
        st.info("Import reports to explore historical prices.")
    else:
        c1, c2, c3, c4 = st.columns(4)
        categories = sorted(df["category"].dropna().unique())
        selected_category = c1.selectbox("Category", ["All"] + categories)
        scope = df if selected_category == "All" else df[df["category"] == selected_category]
        item = c2.selectbox("Item", sorted(scope["item_name"].dropna().unique()))
        item_scope = scope[scope["item_name"] == item]
        market = c3.selectbox("Market", sorted(item_scope["market"].dropna().unique()))
        price_type = c4.selectbox("Price type", sorted(item_scope["price_type"].dropna().unique()))
        min_date = df["report_date"].min().date()
        max_date = latest_date
        days = st.select_slider("History window", options=[30, 60, 90, 180, 365, 9999], value=90, format_func=lambda n: "All" if n == 9999 else f"{n} days")
        date_start = min_date if days == 9999 else max(min_date, max_date - timedelta(days=days))
        hist = item_scope[(item_scope["market"] == market) & (item_scope["price_type"] == price_type) & (item_scope["report_date"].dt.date >= date_start)].sort_values("report_date")
        if hist.empty:
            st.info("No prices match the current filters.")
        else:
            line = px.line(hist, x="report_date", y="today_price", markers=True, title=f"{item} · {market} · {price_type}", labels={"report_date": "Report date", "today_price": f"Price ({hist.iloc[0]['unit']})"})
            st.plotly_chart(line, use_container_width=True)
            st.dataframe(hist[["report_date", "category", "item_name", "market", "price_type", "yesterday_price", "today_price", "unit"]], use_container_width=True, hide_index=True)

with tab_compare:
    if df.empty:
        st.info("Import reports before comparing prices.")
    else:
        items = sorted(df["item_name"].dropna().unique())
        selected_items = st.multiselect("Choose 2–5 items", items, default=items[: min(3, len(items))])
        markets = sorted(df["market"].dropna().unique())
        market = st.selectbox("Market", markets, key="compare_market")
        price_types = sorted(df["price_type"].dropna().unique())
        price_type = st.selectbox("Price type", price_types, key="compare_type")
        if selected_items:
            compare = df[df["item_name"].isin(selected_items) & (df["market"] == market) & (df["price_type"] == price_type)]
            if not compare.empty:
                st.plotly_chart(px.line(compare, x="report_date", y="today_price", color="item_name", markers=True, title=f"Price comparison · {market} · {price_type}"), use_container_width=True)
                st.caption("Items may use different units; compare each series with its unit shown in the table.")
                st.dataframe(compare[["report_date", "item_name", "market", "price_type", "today_price", "unit"]], use_container_width=True, hide_index=True)
            else:
                st.info("No matching observations for these items and filters.")
        if len(selected_items) > 5:
            st.warning("Choose at most five items for a readable comparison.")

with tab_intelligence:
    if df.empty:
        st.info("Import reports to unlock alerts and market intelligence.")
    else:
        st.subheader("Price threshold alerts")
        alert_item = st.selectbox("Alert item", sorted(df["item_name"].unique()), key="alert_item")
        alert_rows = df[df["item_name"] == alert_item]
        a1, a2 = st.columns(2)
        alert_market = a1.selectbox("Market", sorted(alert_rows["market"].unique()), key="alert_market")
        alert_type = a2.selectbox("Price type", sorted(alert_rows["price_type"].unique()), key="alert_type")
        threshold = st.number_input("Alert when today's price is at or above (Rs.)", min_value=0.0, value=1000.0, step=50.0)
        latest_alert = alert_rows[(alert_rows["market"] == alert_market) & (alert_rows["price_type"] == alert_type)].sort_values("report_date").tail(1)
        if not latest_alert.empty:
            current = latest_alert.iloc[0]
            if pd.notna(current["today_price"]) and current["today_price"] >= threshold:
                st.warning(f"{alert_item} reached Rs. {current['today_price']:,.2f} on {current['report_date']:%d %b %Y}.")
            else:
                st.success("The latest reported price is below this threshold.")
        st.caption("Thresholds are evaluated when this dashboard is open; persistent alerts and messaging can be added in a later release.")

        st.subheader("Lowest recent market price")
        best_item = st.selectbox("Commodity", sorted(df["item_name"].unique()), key="best_item")
        best_rows = df[(df["item_name"] == best_item) & (df["price_type"] == "retail")].copy()
        recent_start = df["report_date"].max() - pd.Timedelta(days=7)
        best_rows = best_rows[best_rows["report_date"] >= recent_start]
        if not best_rows.empty:
            best_prices = best_rows.groupby(["market", "unit"], as_index=False).agg(
                average_7d=("today_price", "mean"),
                latest_price=("today_price", "last"),
                reports=("today_price", "count"),
            ).sort_values("average_7d")
            st.dataframe(best_prices, use_container_width=True, hide_index=True)
            st.caption("Ranks markets by their reported retail price average over the last seven calendar days. Units remain as published by CBSL.")
        else:
            st.info("No retail prices for this item in the last seven days.")

        st.subheader("Wholesale and retail spread")
        spread_item = st.selectbox("Spread item", sorted(df["item_name"].unique()), key="spread_item")
        spread_rows = df[df["item_name"] == spread_item]
        wholesale_markets = set(spread_rows.loc[spread_rows["price_type"] == "wholesale", "market"])
        retail_markets = set(spread_rows.loc[spread_rows["price_type"] == "retail", "market"])
        spread_markets = sorted(wholesale_markets & retail_markets)
        spread_market = st.selectbox("Market with both price types", spread_markets or sorted(spread_rows["market"].unique()), key="spread_market")
        spread_rows = spread_rows[spread_rows["market"] == spread_market]
        spread_pivot = spread_rows.pivot_table(index="report_date", columns="price_type", values="today_price", aggfunc="first").reset_index()
        if {"wholesale", "retail"}.issubset(spread_pivot.columns):
            spread_pivot["spread"] = spread_pivot["retail"] - spread_pivot["wholesale"]
            spread_pivot["spread_percent"] = spread_pivot["spread"] / spread_pivot["wholesale"].replace(0, float("nan")) * 100
            st.plotly_chart(px.line(spread_pivot, x="report_date", y="spread", markers=True, title=f"Retail minus wholesale · {spread_item} · {spread_market}"), use_container_width=True)
            st.dataframe(spread_pivot[["report_date", "wholesale", "retail", "spread", "spread_percent"]].tail(30), use_container_width=True, hide_index=True)
        else:
            st.info("No matching wholesale and retail series are available for this item and market.")

        st.subheader("Seasonal price pattern")
        seasonal_item = st.selectbox("Seasonal item", sorted(df["item_name"].unique()), key="seasonal_item")
        seasonal_rows = df[df["item_name"] == seasonal_item].copy()
        seasonal_rows["month"] = seasonal_rows["report_date"].dt.month
        seasonal = seasonal_rows.groupby(["month", "market", "price_type"], as_index=False)["today_price"].mean()
        seasonal["month_name"] = pd.to_datetime(seasonal["month"], format="%m").dt.strftime("%b")
        st.plotly_chart(px.line(seasonal, x="month_name", y="today_price", color="market", line_dash="price_type", markers=True, category_orders={"month_name": ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]}, title=f"Average monthly prices by market · {seasonal_item}"), use_container_width=True)
        if seasonal_rows["report_date"].dt.year.nunique() < 2:
            st.caption("Seasonal patterns become more useful after at least two years of reports have been imported.")

with tab_export:
    if df.empty:
        st.info("Import reports to enable CSV export.")
    else:
        st.write(f"Download the full dataset ({len(df):,} market price observations) as CSV.")
        st.download_button("Download all prices as CSV", df.drop(columns=["pdf_url"], errors="ignore").to_csv(index=False).encode("utf-8-sig"), "sri_lanka_prices.csv", "text/csv")
        with closing(db.get_connection()) as conn:
            rows = conn.execute("SELECT report_date, pdf_url, scraped_at FROM price_reports ORDER BY report_date").fetchall()
        report_rows = pd.DataFrame([dict(row) for row in rows])
        st.download_button("Download report index as CSV", report_rows.to_csv(index=False).encode("utf-8-sig"), "sri_lanka_reports.csv", "text/csv")
