"""
Synthetic dataset: SaaS marketing funnel + subscription churn
Output (./data): campaigns.csv, leads.csv, customers.csv, monthly_activity.csv

Setup: pip install numpy pandas
Run:   python generate_data.py

Seeded, so everyone who runs it gets the same data.
Deliberate data problems to clean in SQL:
  - duplicate leads (same email, later lead_id)
  - inconsistent channel spelling / stray spaces
  - missing country values
"""

from pathlib import Path

import numpy as np
import pandas as pd

SEED = 42
START, CUTOFF = pd.Timestamp("2024-01-01"), pd.Timestamp("2025-12-31")
N_LEADS = 14_000
DUP_RATE, MESSY_RATE, NULL_COUNTRY_RATE = 0.015, 0.03, 0.04

rng = np.random.default_rng(SEED)
out = Path(__file__).resolve().parent / "data"
out.mkdir(exist_ok=True)

# share of leads, lead->trial rate, trial->paid rate, cost per lead, churn multiplier
CHANNELS = {
    "Organic Search": (0.24, 0.30, 0.30, 8, 1.0),
    "Paid Search":    (0.20, 0.26, 0.28, 60, 1.1),
    "Social Ads":     (0.16, 0.18, 0.20, 40, 1.5),
    "Email":          (0.10, 0.34, 0.33, 5, 0.9),
    "Referral":       (0.08, 0.45, 0.42, 22, 0.6),
    "Webinar":        (0.08, 0.38, 0.36, 70, 0.8),
    "Affiliate":      (0.14, 0.15, 0.17, 32, 1.6),
}
PLANS = ["Basic", "Pro", "Business"]
PRICE = {"Basic": 39, "Pro": 99, "Business": 249}
BASE_CHURN = {"Basic": 0.060, "Pro": 0.035, "Business": 0.020}  # monthly hazard
ANNUAL_P = {"Basic": 0.15, "Pro": 0.30, "Business": 0.45}
BASE_LOGINS = {"Basic": 14, "Pro": 22, "Business": 38}
PLAN_MIX = {
    "default":   [0.50, 0.35, 0.15],
    "Referral":  [0.30, 0.45, 0.25],
    "Webinar":   [0.30, 0.45, 0.25],
    "Social Ads": [0.65, 0.28, 0.07],
    "Affiliate": [0.65, 0.28, 0.07],
}
SIZES = {
    "Basic":    (["Solo", "2-10", "11-50"], [0.50, 0.40, 0.10]),
    "Pro":      (["2-10", "11-50", "51-200"], [0.40, 0.40, 0.20]),
    "Business": (["11-50", "51-200", "200+"], [0.30, 0.50, 0.20]),
}
REASONS = ["Price", "Missing features", "Switched to competitor",
           "Poor support", "Business closed", "No longer needed"]
REASON_P = {
    "Basic":    [0.35, 0.15, 0.15, 0.08, 0.07, 0.20],
    "Pro":      [0.22, 0.25, 0.25, 0.10, 0.08, 0.10],
    "Business": [0.10, 0.25, 0.30, 0.15, 0.12, 0.08],
}
COUNTRIES = ["United States", "United Kingdom", "Germany", "Canada", "India",
             "Australia", "France", "Brazil", "Spain"]
COUNTRY_P = [0.45, 0.12, 0.08, 0.08, 0.08, 0.06, 0.05, 0.04, 0.04]

# ---------------------------------------------------------------- leads
names = list(CHANNELS)
months = pd.date_range(START, CUTOFF, freq="MS")
w = np.linspace(1.0, 1.6, len(months))  # lead volume grows over time
w /= w.sum()
month_start = months[rng.choice(len(months), N_LEADS, p=w)]
offset = (rng.random(N_LEADS) * month_start.days_in_month.to_numpy()).astype(int)
lead_date = month_start + pd.to_timedelta(offset, unit="D")

channel = rng.choice(names, N_LEADS, p=[CHANNELS[c][0] for c in names])
to_trial = np.array([CHANNELS[c][1] for c in channel])
to_paid = np.array([CHANNELS[c][2] for c in channel])

trial_start = lead_date + pd.to_timedelta(rng.integers(0, 15, N_LEADS), unit="D")
has_trial = (rng.random(N_LEADS) < to_trial) & (trial_start <= CUTOFF)
converted = trial_start + pd.to_timedelta(rng.integers(3, 15, N_LEADS), unit="D")
has_paid = has_trial & (rng.random(N_LEADS) < to_paid) & (converted <= CUTOFF)

country = rng.choice(COUNTRIES, N_LEADS, p=COUNTRY_P).astype(object)
country[rng.random(N_LEADS) < NULL_COUNTRY_RATE] = None

leads = pd.DataFrame({
    "email": [f"user{i:05d}@example.com" for i in range(N_LEADS)],
    "lead_date": lead_date,
    "channel_clean": channel,
    "country": country,
    "trial_start_date": pd.Series(trial_start).where(has_trial).to_numpy(),
    "converted_date": pd.Series(converted).where(has_paid).to_numpy(),
})
leads["trial_start_date"] = pd.to_datetime(leads["trial_start_date"])
leads["converted_date"] = pd.to_datetime(leads["converted_date"])

# duplicates: same person re-enters the CRM a few days later, never converts
dup = leads.sample(int(N_LEADS * DUP_RATE), random_state=SEED).copy()
dup["lead_date"] += pd.to_timedelta(rng.integers(1, 6, len(dup)), unit="D")
dup = dup[dup["lead_date"] <= CUTOFF]
dup["trial_start_date"] = pd.NaT
dup["converted_date"] = pd.NaT
leads = pd.concat([leads, dup]).sort_values("lead_date", kind="stable").reset_index(drop=True)
leads.insert(0, "lead_id", np.arange(1, len(leads) + 1))

# ---------------------------------------------------------------- campaigns
leads["quarter"] = leads["lead_date"].dt.to_period("Q")
quarters = sorted(leads["quarter"].unique())
camp_rows, camp_lookup = [], {}
for q in quarters:
    for ch in names:
        cid = f"C{len(camp_rows) + 1:03d}"
        camp_lookup[(ch, q)] = cid
        camp_rows.append({
            "campaign_id": cid,
            "campaign_name": f"{ch} Q{q.quarter} {q.year}",
            "channel": ch,
            "start_date": q.start_time.normalize(),
            "end_date": min(q.end_time.normalize(), CUTOFF),
        })
campaigns = pd.DataFrame(camp_rows)
leads["campaign_id"] = [camp_lookup[(c, q)] for c, q in zip(leads["channel_clean"], leads["quarter"])]
n_per = leads.groupby("campaign_id").size()
cpl = campaigns["channel"].map({c: CHANNELS[c][3] for c in names})
campaigns["spend"] = (campaigns["campaign_id"].map(n_per).fillna(0) * cpl
                      * rng.lognormal(0, 0.12, len(campaigns))).round(2)

# messy channel spelling on ~3% of rows
styles = [str.lower, str.upper, lambda s: " " + s, lambda s: s + " "]
leads["channel"] = leads["channel_clean"]
for i in leads.index[rng.random(len(leads)) < MESSY_RATE]:
    leads.at[i, "channel"] = styles[rng.integers(0, len(styles))](leads.at[i, "channel_clean"])

# ---------------------------------------------------------------- customers + activity
paid = leads[leads["converted_date"].notna()].sort_values("converted_date", kind="stable")
cust_rows, act_rows = [], []
for cid, r in enumerate(paid.itertuples(), 1):
    cm = CHANNELS[r.channel_clean][4]
    plan = rng.choice(PLANS, p=PLAN_MIX.get(r.channel_clean, PLAN_MIX["default"]))
    annual = rng.random() < ANNUAL_P[plan]
    mrr = round(PRICE[plan] * (0.85 if annual else 1.0), 2)
    size = rng.choice(SIZES[plan][0], p=SIZES[plan][1])
    eng = rng.lognormal(0, 0.35)                       # latent engagement
    eng_f = 1.6 if eng < 0.8 else 0.6 if eng > 1.25 else 1.0
    signup = r.converted_date
    avail = (CUTOFF - signup).days // 30

    churn_m = None
    for m in range(1, avail + 1):
        if annual:
            if m % 12:
                continue
            p = 0.16 * cm * eng_f                       # decide only at renewal
        else:
            tf = 1.8 if m <= 3 else 1.2 if m <= 6 else 1.0
            p = BASE_CHURN[plan] * cm * tf * eng_f
        if rng.random() < min(p, 0.9):
            churn_m = m
            break

    churn_date = reason = None
    if churn_m:
        churn_date = signup + pd.Timedelta(days=30 * churn_m - int(rng.integers(0, 10)))
        reason = rng.choice(REASONS, p=REASON_P[plan])

    cust_rows.append({
        "customer_id": cid, "lead_id": r.lead_id, "signup_date": signup, "plan": plan,
        "billing_cycle": "annual" if annual else "monthly", "mrr": mrr,
        "country": r.country, "company_size": size,
        "churn_date": churn_date, "churn_reason": reason,
    })

    end = churn_date if churn_date is not None else CUTOFF
    n = (end.to_period("M") - signup.to_period("M")).n + 1
    mths = pd.period_range(signup.to_period("M"), periods=n, freq="M").to_timestamp()
    decay = rng.random() < (0.70 if churn_m else 0.08)  # engagement fades before churn
    lam_l, lam_t = np.full(n, BASE_LOGINS[plan] * eng), np.full(n, 0.25)
    if decay:
        k = min(3, n)
        lam_l[-k:] *= np.array([0.75, 0.5, 0.3])[-k:]
        lam_t[-k:] += np.array([0.2, 0.5, 0.9])[-k:]
    for mth, lg, tk in zip(mths, rng.poisson(lam_l), rng.poisson(lam_t)):
        act_rows.append((cid, mth, int(lg), int(tk)))

customers = pd.DataFrame(cust_rows)
activity = pd.DataFrame(act_rows, columns=["customer_id", "activity_month", "logins", "support_tickets"])

# ---------------------------------------------------------------- save
leads_out = leads[["lead_id", "email", "lead_date", "channel", "campaign_id",
                   "country", "trial_start_date", "converted_date"]]
fmt = "%Y-%m-%d"
campaigns.to_csv(out / "campaigns.csv", index=False, date_format=fmt)
leads_out.to_csv(out / "leads.csv", index=False, date_format=fmt)
customers.to_csv(out / "customers.csv", index=False, date_format=fmt)
activity.to_csv(out / "monthly_activity.csv", index=False, date_format=fmt)

print(f"campaigns        {len(campaigns):>7,}")
print(f"leads            {len(leads_out):>7,}  (incl. {len(dup)} duplicates)")
print(f"customers        {len(customers):>7,}  ({customers['churn_date'].notna().mean():.1%} churned)")
print(f"monthly_activity {len(activity):>7,}")
print(f"total spend      {campaigns['spend'].sum():>10,.0f}")
