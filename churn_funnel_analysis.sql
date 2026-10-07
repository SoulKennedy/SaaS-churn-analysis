-- Setup
USE churn_project;
SET @cutoff = '2025-12-31';

DROP TABLE IF EXISTS stg_engagement;
DROP TABLE IF EXISTS stg_customers;
DROP TABLE IF EXISTS stg_leads;


-- Data quality checks
SELECT 'leads rows' AS check_name, COUNT(*) AS result FROM leads
UNION ALL
SELECT 'duplicate emails', COUNT(*) - COUNT(DISTINCT email) FROM leads
UNION ALL
SELECT 'channel spelling variants', COUNT(DISTINCT BINARY channel) - COUNT(DISTINCT LOWER(TRIM(channel))) FROM leads
UNION ALL
SELECT 'leads with missing country', SUM(country IS NULL) FROM leads
UNION ALL
SELECT 'customers without a lead', COUNT(*) FROM customers c LEFT JOIN leads l ON l.lead_id = c.lead_id WHERE l.lead_id IS NULL
UNION ALL
SELECT 'trial before lead date', COALESCE(SUM(trial_start_date < lead_date), 0) FROM leads;


-- Staging: clean and deduplicate leads
CREATE TABLE stg_leads AS
SELECT
    lead_id,
    email,
    lead_date,
    channel,
    campaign_id,
    COALESCE(country, 'Unknown') AS country,
    trial_start_date,
    converted_date
FROM (
    SELECT
        l.lead_id,
        l.email,
        l.lead_date,
        l.campaign_id,
        l.country,
        l.trial_start_date,
        l.converted_date,
        CASE LOWER(TRIM(l.channel))
            WHEN 'organic search' THEN 'Organic Search'
            WHEN 'paid search'    THEN 'Paid Search'
            WHEN 'social ads'     THEN 'Social Ads'
            WHEN 'email'          THEN 'Email'
            WHEN 'referral'       THEN 'Referral'
            WHEN 'webinar'        THEN 'Webinar'
            WHEN 'affiliate'      THEN 'Affiliate'
            ELSE 'Unknown'
        END AS channel,
        ROW_NUMBER() OVER (
            PARTITION BY l.email
            ORDER BY (l.converted_date IS NULL), l.lead_date, l.lead_id
        ) AS rn
    FROM leads l
) cleaned
WHERE rn = 1;

ALTER TABLE stg_leads ADD INDEX idx_lead (lead_id), ADD INDEX idx_campaign (campaign_id);


-- Staging: customers with channel, tenure and cohort
CREATE TABLE stg_customers AS
SELECT
    c.customer_id,
    c.lead_id,
    COALESCE(l.channel, 'Unknown')                              AS channel,
    c.signup_date,
    c.plan,
    c.billing_cycle,
    c.mrr,
    COALESCE(c.country, 'Unknown')                              AS country,
    c.company_size,
    c.churn_date,
    c.churn_reason,
    (c.churn_date IS NOT NULL)                                  AS is_churned,
    TIMESTAMPDIFF(MONTH, c.signup_date, COALESCE(c.churn_date, @cutoff)) AS tenure_months,
    TIMESTAMPDIFF(MONTH, c.signup_date, @cutoff)                AS observed_months
FROM customers c
LEFT JOIN stg_leads l ON l.lead_id = c.lead_id;

ALTER TABLE stg_customers ADD PRIMARY KEY (customer_id), ADD INDEX idx_channel (channel);


-- Staging: engagement (first 3 vs last 3 months per customer)
CREATE TABLE stg_engagement AS
SELECT
    customer_id,
    COUNT(*)                                          AS months_observed,
    AVG(CASE WHEN rn_first <= 3 THEN logins END)      AS logins_first3,
    AVG(CASE WHEN rn_last  <= 3 THEN logins END)      AS logins_last3,
    AVG(CASE WHEN rn_last  <= 3 THEN support_tickets END) AS tickets_last3
FROM (
    SELECT
        customer_id,
        logins,
        support_tickets,
        ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY activity_month)      AS rn_first,
        ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY activity_month DESC) AS rn_last
    FROM monthly_activity
) a
GROUP BY customer_id
HAVING COUNT(*) >= 6;

ALTER TABLE stg_engagement ADD PRIMARY KEY (customer_id);


-- Q1: Funnel by channel
SELECT
    CASE WHEN GROUPING(channel) = 1 THEN 'ALL CHANNELS' ELSE channel END AS channel,
    COUNT(*)                                                             AS leads,
    SUM(trial_start_date IS NOT NULL)                                    AS trials,
    SUM(converted_date IS NOT NULL)                                      AS paid_customers,
    ROUND(SUM(trial_start_date IS NOT NULL) / COUNT(*) * 100, 1)         AS lead_to_trial_pct,
    ROUND(SUM(converted_date IS NOT NULL)
          / NULLIF(SUM(trial_start_date IS NOT NULL), 0) * 100, 1)       AS trial_to_paid_pct,
    ROUND(SUM(converted_date IS NOT NULL) / COUNT(*) * 100, 2)           AS lead_to_paid_pct
FROM stg_leads
GROUP BY channel WITH ROLLUP
ORDER BY GROUPING(channel), lead_to_paid_pct DESC;


-- Q2: Time to convert by channel (days)
SELECT
    channel,
    COUNT(*)                                          AS converted_leads,
    ROUND(AVG(DATEDIFF(trial_start_date, lead_date)), 1)      AS days_lead_to_trial,
    ROUND(AVG(DATEDIFF(converted_date, trial_start_date)), 1) AS days_trial_to_paid,
    ROUND(AVG(DATEDIFF(converted_date, lead_date)), 1)        AS days_lead_to_paid
FROM stg_leads
WHERE converted_date IS NOT NULL
GROUP BY channel
ORDER BY days_lead_to_paid;


-- Q3: Campaign efficiency (cost per acquired customer)
SELECT
    x.*,
    RANK() OVER (ORDER BY x.cac) AS cac_rank
FROM (
    SELECT
        c.campaign_id,
        c.campaign_name,
        c.channel,
        c.spend,
        COUNT(l.lead_id)                                              AS leads,
        SUM(l.converted_date IS NOT NULL)                             AS customers,
        ROUND(c.spend / NULLIF(SUM(l.converted_date IS NOT NULL), 0), 2) AS cac
    FROM campaigns c
    LEFT JOIN stg_leads l ON l.campaign_id = c.campaign_id
    GROUP BY c.campaign_id, c.campaign_name, c.channel, c.spend
) x
WHERE x.cac IS NOT NULL
ORDER BY cac_rank;


-- Q4: Channel economics (CAC, churn, LTV, LTV:CAC, payback)
SELECT
    s.channel,
    ROUND(s.spend, 0)                                             AS spend,
    c.customers,
    ROUND(s.spend / c.customers, 2)                               AS cac,
    ROUND(c.arpa, 2)                                              AS arpa,
    ROUND(c.churned / c.customer_months * 100, 2)                 AS monthly_churn_pct,
    ROUND(c.arpa / (c.churned / c.customer_months), 2)            AS ltv,
    ROUND((c.arpa / (c.churned / c.customer_months)) / (s.spend / c.customers), 2) AS ltv_cac_ratio,
    ROUND((s.spend / c.customers) / c.arpa, 1)                    AS payback_months
FROM (
    SELECT channel, SUM(spend) AS spend
    FROM campaigns
    GROUP BY channel
) s
JOIN (
    SELECT
        channel,
        COUNT(*)           AS customers,
        SUM(is_churned)    AS churned,
        SUM(tenure_months) AS customer_months,
        AVG(mrr)           AS arpa
    FROM stg_customers
    GROUP BY channel
) c ON c.channel = s.channel COLLATE utf8mb4_0900_ai_ci
ORDER BY ltv_cac_ratio DESC;


-- Q5: Monthly churn trend (customers and MRR)
WITH RECURSIVE months AS (
    SELECT CAST('2024-01-01' AS DATE) AS month_start
    UNION ALL
    SELECT DATE_ADD(month_start, INTERVAL 1 MONTH)
    FROM months
    WHERE month_start < DATE_SUB(@cutoff, INTERVAL DAYOFMONTH(@cutoff) - 1 DAY)
)
SELECT
    t.month_start,
    t.customers_start,
    t.new_customers,
    t.churned,
    ROUND(t.churned / NULLIF(t.customers_start, 0) * 100, 2)     AS churn_rate_pct,
    ROUND(t.mrr_start, 2)                                        AS mrr_start,
    ROUND(t.mrr_churned, 2)                                      AS mrr_churned,
    ROUND(t.mrr_churned / NULLIF(t.mrr_start, 0) * 100, 2)       AS mrr_churn_pct
FROM (
    SELECT
        m.month_start,
        SUM(c.signup_date < m.month_start
            AND (c.churn_date IS NULL OR c.churn_date >= m.month_start))          AS customers_start,
        SUM(c.signup_date >= m.month_start
            AND c.signup_date < DATE_ADD(m.month_start, INTERVAL 1 MONTH))       AS new_customers,
        SUM(c.signup_date < m.month_start
            AND c.churn_date >= m.month_start
            AND c.churn_date < DATE_ADD(m.month_start, INTERVAL 1 MONTH))        AS churned,
        SUM(CASE WHEN c.signup_date < m.month_start
                  AND (c.churn_date IS NULL OR c.churn_date >= m.month_start)
                 THEN c.mrr ELSE 0 END)                                           AS mrr_start,
        SUM(CASE WHEN c.signup_date < m.month_start
                  AND c.churn_date >= m.month_start
                  AND c.churn_date < DATE_ADD(m.month_start, INTERVAL 1 MONTH)
                 THEN c.mrr ELSE 0 END)                                           AS mrr_churned
    FROM months m
    CROSS JOIN stg_customers c
    GROUP BY m.month_start
) t
ORDER BY t.month_start;


-- Q6: Churn by plan and billing cycle
SELECT
    plan,
    billing_cycle,
    COUNT(*)                                                         AS customers,
    SUM(is_churned)                                                  AS churned,
    ROUND(SUM(is_churned) / COUNT(*) * 100, 1)                       AS churned_pct,
    ROUND(SUM(is_churned) / NULLIF(SUM(tenure_months), 0) * 100, 2)  AS monthly_churn_pct,
    ROUND(AVG(mrr), 2)                                               AS avg_mrr,
    ROUND(SUM(CASE WHEN is_churned THEN mrr ELSE 0 END), 2)          AS mrr_lost
FROM stg_customers
GROUP BY plan, billing_cycle
ORDER BY FIELD(plan, 'Basic', 'Pro', 'Business'), billing_cycle;


-- Q7: When do customers churn? (tenure at churn)
SELECT
    bucket,
    COUNT(*)                                          AS churned_customers,
    ROUND(COUNT(*) / SUM(COUNT(*)) OVER () * 100, 1)  AS share_of_churn_pct
FROM (
    SELECT
        CASE
            WHEN tenure_months <= 3  THEN '0-3 months'
            WHEN tenure_months <= 6  THEN '4-6 months'
            WHEN tenure_months <= 12 THEN '7-12 months'
            ELSE '13+ months'
        END AS bucket
    FROM stg_customers
    WHERE is_churned
) t
GROUP BY bucket
ORDER BY FIELD(bucket, '0-3 months', '4-6 months', '7-12 months', '13+ months');


-- Q8: Why do customers churn? (reasons by plan)
SELECT
    plan,
    churn_reason,
    COUNT(*)                                                                  AS churned_customers,
    ROUND(COUNT(*) / SUM(COUNT(*)) OVER (PARTITION BY plan) * 100, 1)         AS pct_of_plan_churn,
    ROUND(SUM(mrr), 2)                                                        AS mrr_lost,
    RANK() OVER (PARTITION BY plan ORDER BY COUNT(*) DESC)                    AS reason_rank
FROM stg_customers
WHERE is_churned
GROUP BY plan, churn_reason
ORDER BY FIELD(plan, 'Basic', 'Pro', 'Business'), reason_rank;


-- Q9: Cohort retention by signup quarter
SELECT
    CONCAT(YEAR(signup_date), '-Q', QUARTER(signup_date)) AS cohort,
    COUNT(*)                                              AS customers,
    ROUND(SUM(observed_months >= 1  AND tenure_months >= 1)  / NULLIF(SUM(observed_months >= 1), 0)  * 100, 1) AS m1_retention_pct,
    ROUND(SUM(observed_months >= 3  AND tenure_months >= 3)  / NULLIF(SUM(observed_months >= 3), 0)  * 100, 1) AS m3_retention_pct,
    ROUND(SUM(observed_months >= 6  AND tenure_months >= 6)  / NULLIF(SUM(observed_months >= 6), 0)  * 100, 1) AS m6_retention_pct,
    ROUND(SUM(observed_months >= 12 AND tenure_months >= 12) / NULLIF(SUM(observed_months >= 12), 0) * 100, 1) AS m12_retention_pct
FROM stg_customers
GROUP BY cohort
ORDER BY cohort;


-- Q10: Engagement before churn (churned vs active)
SELECT
    CASE WHEN c.is_churned THEN 'Churned' ELSE 'Active' END                       AS status,
    COUNT(*)                                                                      AS customers,
    ROUND(AVG(e.logins_first3), 1)                                                AS avg_logins_first3,
    ROUND(AVG(e.logins_last3), 1)                                                 AS avg_logins_last3,
    ROUND((AVG(e.logins_last3) / AVG(e.logins_first3) - 1) * 100, 1)              AS login_change_pct,
    ROUND(AVG(e.tickets_last3), 2)                                                AS avg_tickets_last3
FROM stg_engagement e
JOIN stg_customers c ON c.customer_id = e.customer_id
GROUP BY c.is_churned
ORDER BY status DESC;


-- Q11: Active customers showing churn warning signs (MRR at risk)
SELECT
    COUNT(*)                                                        AS at_risk_customers,
    ROUND(SUM(c.mrr), 2)                                            AS mrr_at_risk,
    ROUND(SUM(c.mrr) / (SELECT SUM(mrr) FROM stg_customers WHERE NOT is_churned) * 100, 1) AS pct_of_active_mrr
FROM stg_customers c
JOIN stg_engagement e ON e.customer_id = c.customer_id
WHERE NOT c.is_churned
  AND e.logins_last3 <= 0.6 * e.logins_first3;


-- Q11: At-risk watchlist (highest MRR first)
SELECT
    c.customer_id,
    c.plan,
    c.billing_cycle,
    c.channel,
    c.mrr,
    ROUND(e.logins_first3, 1)                                       AS logins_first3,
    ROUND(e.logins_last3, 1)                                        AS logins_last3,
    ROUND((e.logins_last3 / NULLIF(e.logins_first3, 0) - 1) * 100, 1) AS login_change_pct,
    ROUND(e.tickets_last3, 2)                                       AS tickets_last3
FROM stg_customers c
JOIN stg_engagement e ON e.customer_id = c.customer_id
WHERE NOT c.is_churned
  AND e.logins_last3 <= 0.6 * e.logins_first3
ORDER BY c.mrr DESC, login_change_pct
LIMIT 50;