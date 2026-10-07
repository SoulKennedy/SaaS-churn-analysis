CREATE DATABASE IF NOT EXISTS churn_project;
USE churn_project;

DROP TABLE IF EXISTS monthly_activity;
DROP TABLE IF EXISTS customers;
DROP TABLE IF EXISTS leads;
DROP TABLE IF EXISTS campaigns;

CREATE TABLE campaigns (
    campaign_id   VARCHAR(10) PRIMARY KEY,
    campaign_name VARCHAR(60),
    channel       VARCHAR(30),
    start_date    DATE,
    end_date      DATE,
    spend         DECIMAL(12,2)
);

CREATE TABLE leads (
    lead_id          INT PRIMARY KEY,
    email            VARCHAR(100),
    lead_date        DATE,
    channel          VARCHAR(30),
    campaign_id      VARCHAR(10),
    country          VARCHAR(30) NULL,
    trial_start_date DATE NULL,
    converted_date   DATE NULL,
    INDEX idx_email (email),
    INDEX idx_campaign (campaign_id)
);

CREATE TABLE customers (
    customer_id   INT PRIMARY KEY,
    lead_id       INT,
    signup_date   DATE,
    plan          VARCHAR(20),
    billing_cycle VARCHAR(10),
    mrr           DECIMAL(8,2),
    country       VARCHAR(30) NULL,
    company_size  VARCHAR(20),
    churn_date    DATE NULL,
    churn_reason  VARCHAR(40) NULL,
    INDEX idx_lead (lead_id)
);

CREATE TABLE monthly_activity (
    customer_id     INT,
    activity_month  DATE,
    logins          INT,
    support_tickets INT,
    PRIMARY KEY (customer_id, activity_month)
);
