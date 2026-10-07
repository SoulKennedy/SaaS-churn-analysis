"""
Churn and funnel visuals (reads the staging tables built by churn_funnel_analysis.sql)

Setup: pip install pandas matplotlib sqlalchemy pymysql
Run:   python churn_visuals.py   (after running the SQL script)

Charts saved to ./charts:
  1_ltv_cac_by_channel.png   2_funnel_by_channel.png   3_churn_trend.png
  4_cohort_retention.png     5_churn_by_plan_billing.png
"""

import os
from getpass import getpass
from pathlib import Path
from urllib.parse import quote_plus

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HOST, PORT, USER, DATABASE = "localhost", 3306, "root", "churn_project"
CUTOFF = pd.Timestamp("2025-12-31")
GREY, BLUE, RED = "#B8BEC6", "#2F5D8C", "#D9534F"
OUT = Path(__file__).resolve().parent / "charts"


def style(ax):
    ax.spines[["top", "right"]].set_visible(False)


def headline(fig, ax, title, subtitle=None):
    fig.suptitle(title, fontsize=15, fontweight="bold", x=0.07, ha="left")
    if subtitle:
        ax.set_title(subtitle, loc="left", fontsize=11, color="#555555")


# ------------------------------------------------------------------ metrics
def channel_economics(customers, campaigns):
    g = customers.groupby("channel").agg(
        customers=("customer_id", "count"), churned=("is_churned", "sum"),
        months=("tenure_months", "sum"), arpa=("mrr", "mean"))
    g["spend"] = campaigns.groupby("channel")["spend"].sum()
    g["cac"] = g["spend"] / g["customers"]
    g["monthly_churn"] = g["churned"] / g["months"]
    g["ltv"] = g["arpa"] / g["monthly_churn"]
    g["ltv_cac"] = g["ltv"] / g["cac"]
    return g.sort_values("ltv_cac")


def monthly_churn(customers):
    rows = []
    for ms in pd.date_range("2024-01-01", CUTOFF, freq="MS"):
        me = ms + pd.DateOffset(months=1)
        base = (customers["signup_date"] < ms) & (customers["churn_date"].isna() | (customers["churn_date"] >= ms))
        lost = base & (customers["churn_date"] < me)
        rows.append({"month": ms, "rate": lost.sum() / base.sum() * 100 if base.sum() else np.nan})
    return pd.DataFrame(rows)


def cohort_retention(customers, ks=(1, 3, 6, 12)):
    c = customers.copy()
    c["cohort"] = c["signup_date"].dt.year.astype(str) + "-Q" + c["signup_date"].dt.quarter.astype(str)
    table = {}
    for k in ks:
        obs = c[c["observed_months"] >= k]
        table[f"M{k}"] = obs.groupby("cohort").apply(lambda d: (d["tenure_months"] >= k).mean() * 100,
                                                     include_groups=False)
    return pd.DataFrame(table).reindex(sorted(c["cohort"].unique()))


# ------------------------------------------------------------------ charts
def chart_ltv_cac(econ):
    fig, ax = plt.subplots(figsize=(10, 6))
    colors = [RED if v < 3 else BLUE for v in econ["ltv_cac"]]
    bars = ax.barh(econ.index, econ["ltv_cac"], color=colors)
    ax.bar_label(bars, labels=[f"{v:.1f}x" for v in econ["ltv_cac"]], padding=4, fontweight="bold")
    ax.axvline(3, color="#555555", linestyle="--", linewidth=1)
    ax.text(3.2, -0.45, "3x benchmark", color="#555555", fontsize=9)
    ax.set_xlabel("Customer lifetime value / acquisition cost")
    style(ax)
    best, worst = econ.iloc[-1], econ.iloc[0]
    below = (econ["ltv_cac"] < 3).sum()
    headline(fig, ax,
             f"{below} of {len(econ)} channels return less than 3x their acquisition cost",
             f"{econ.index[-1]} returns {best['ltv_cac']:.0f}x; {econ.index[0]} returns {worst['ltv_cac']:.1f}x")
    fig.tight_layout()
    fig.savefig(OUT / "1_ltv_cac_by_channel.png", dpi=200)


def chart_funnel(leads):
    g = leads.groupby("channel").agg(leads=("lead_id", "count"),
                                     trials=("trial_start_date", "count"),
                                     paid=("converted_date", "count"))
    g["to_trial"] = g["trials"] / g["leads"] * 100
    g["to_paid"] = g["paid"] / g["trials"] * 100
    g["overall"] = g["paid"] / g["leads"] * 100
    g = g.sort_values("overall", ascending=False)
    x, w = np.arange(len(g)), 0.38
    fig, ax = plt.subplots(figsize=(11, 6))
    b1 = ax.bar(x - w / 2, g["to_trial"], w, color=GREY, label="Lead to trial")
    b2 = ax.bar(x + w / 2, g["to_paid"], w, color=BLUE, label="Trial to paid")
    ax.bar_label(b1, fmt="%.0f%%", padding=3, fontsize=9)
    ax.bar_label(b2, fmt="%.0f%%", padding=3, fontsize=9, fontweight="bold")
    ax.set_xticks(x)
    ax.set_xticklabels(g.index)
    ax.set_ylabel("Conversion rate (%)")
    ax.legend(frameon=False)
    style(ax)
    headline(fig, ax,
             f"{g.index[0]} converts {g['overall'].iloc[0]:.0f}% of leads to paid; {g.index[-1]} only {g['overall'].iloc[-1]:.0f}%",
             f"{int(g['leads'].sum()):,} unique leads, {int(g['paid'].sum()):,} paying customers")
    fig.tight_layout()
    fig.savefig(OUT / "2_funnel_by_channel.png", dpi=200)


def chart_trend(trend):
    fig, ax = plt.subplots(figsize=(11, 5.5))
    ax.plot(trend["month"], trend["rate"], color=BLUE, marker="o", linewidth=2)
    ax.axhline(trend["rate"].mean(), color=GREY, linestyle="--")
    ax.text(trend["month"].iloc[0], trend["rate"].mean() + 0.15, f"average {trend['rate'].mean():.1f}%",
            color="#555555", fontsize=9)
    ax.set_ylabel("Monthly customer churn (%)")
    ax.set_ylim(0)
    style(ax)
    headline(fig, ax, f"Monthly churn averages {trend['rate'].mean():.1f}%",
             f"Peak {trend['rate'].max():.1f}% in {trend.loc[trend['rate'].idxmax(), 'month']:%b %Y}")
    fig.tight_layout()
    fig.savefig(OUT / "3_churn_trend.png", dpi=200)


def chart_cohorts(table):
    fig, ax = plt.subplots(figsize=(8, 6))
    data = np.ma.masked_invalid(table.to_numpy(dtype=float))
    im = ax.imshow(data, cmap="Blues", vmin=30, vmax=100, aspect="auto")
    for i in range(data.shape[0]):
        for j in range(data.shape[1]):
            if not data.mask[i, j]:
                ax.text(j, i, f"{data[i, j]:.0f}%", ha="center", va="center",
                        color="white" if data[i, j] > 70 else "black", fontweight="bold")
    ax.set_xticks(range(table.shape[1]))
    ax.set_xticklabels(table.columns)
    ax.set_yticks(range(table.shape[0]))
    ax.set_yticklabels(table.index)
    ax.set_xlabel("Months since signup")
    ax.set_ylabel("Signup cohort")
    fig.colorbar(im, ax=ax, label="% still subscribed")
    headline(fig, ax, "Retention by signup cohort",
             f"Typical customer retention at 12 months: {table['M12'].mean():.0f}%")
    fig.tight_layout()
    fig.savefig(OUT / "4_cohort_retention.png", dpi=200)


def chart_plan_billing(customers):
    g = (customers.groupby(["plan", "billing_cycle"])
         .apply(lambda d: d["is_churned"].sum() / d["tenure_months"].sum() * 100, include_groups=False)
         .unstack().reindex(["Basic", "Pro", "Business"]))
    x, w = np.arange(len(g)), 0.38
    fig, ax = plt.subplots(figsize=(9, 5.5))
    b1 = ax.bar(x - w / 2, g["monthly"], w, color=RED, label="Monthly billing")
    b2 = ax.bar(x + w / 2, g["annual"], w, color=BLUE, label="Annual billing")
    ax.bar_label(b1, fmt="%.1f%%", padding=3)
    ax.bar_label(b2, fmt="%.1f%%", padding=3)
    ax.set_xticks(x)
    ax.set_xticklabels(g.index)
    ax.set_ylabel("Monthly churn rate (%)")
    ax.legend(frameon=False)
    style(ax)
    headline(fig, ax, "Annual plans churn far less than monthly plans",
             "Monthly churn rate by plan; annual rates are understated (few renewals yet)")
    fig.tight_layout()
    fig.savefig(OUT / "5_churn_by_plan_billing.png", dpi=200)


def make_charts(leads, customers, campaigns):
    OUT.mkdir(exist_ok=True)
    chart_ltv_cac(channel_economics(customers, campaigns))
    chart_funnel(leads)
    chart_trend(monthly_churn(customers))
    chart_cohorts(cohort_retention(customers))
    chart_plan_billing(customers)
    print(f"Saved 5 charts to {OUT.resolve()}")


# ------------------------------------------------------------------ main
if __name__ == "__main__":
    from sqlalchemy import create_engine

    password = os.getenv("MYSQL_PASSWORD") or getpass("MySQL password: ")
    engine = create_engine(f"mysql+pymysql://{USER}:{quote_plus(password)}@{HOST}:{PORT}/{DATABASE}")

    leads = pd.read_sql("SELECT * FROM stg_leads", engine,
                        parse_dates=["lead_date", "trial_start_date", "converted_date"])
    customers = pd.read_sql("SELECT * FROM stg_customers", engine, parse_dates=["signup_date", "churn_date"])
    campaigns = pd.read_sql("SELECT * FROM campaigns", engine)
    customers["is_churned"] = customers["is_churned"].astype(int)
    campaigns["spend"] = campaigns["spend"].astype(float)
    customers["mrr"] = customers["mrr"].astype(float)

    make_charts(leads, customers, campaigns)
    plt.show()
