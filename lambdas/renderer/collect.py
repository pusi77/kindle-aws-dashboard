"""Collect the dashboard data from AWS. Read-only: only Get*/Describe* calls.

collect() returns the dict that render() draws and that is stored as latest.json. A source that
fails is None, with a short error code (never the message: it can contain ARNs) in data["errors"].

Cost Explorer costs $0.01 per request, each page included. One collect() makes 3 requests when
nothing paginates: GetCostAndUsage, GetCostForecast, GetAnomalies.
"""

import logging
from datetime import UTC, date, datetime, timedelta

import boto3
from botocore.exceptions import ClientError

log = logging.getLogger(__name__)

# Gross usage: credits and refunds would hide the real spend.
NO_CREDITS = {"Not": {"Dimensions": {"Key": "RECORD_TYPE", "Values": ["Credit", "Refund"]}}}
CE_SERVICE = "AWS Cost Explorer"
KINDLE_NAMESPACE, KINDLE_METRIC = "KindleDashboard", "Battery"


def first_of_month(d: date) -> date:
    return d.replace(day=1)


def summarize_costs(results: list, today: date) -> dict:
    """Turn DAILY GetCostAndUsage results grouped by SERVICE (from the 1st of last month) into
    the numbers on the dashboard. "Same point last month" = days 1..today's day of last month."""
    month = first_of_month(today)
    last_month = first_of_month(month - timedelta(days=1))
    daily, services = {}, {}
    last_same_point = 0.0
    for r in results:
        day = date.fromisoformat(r["TimePeriod"]["Start"])
        total = sum(float(g["Metrics"]["UnblendedCost"]["Amount"]) for g in r["Groups"])
        daily[day] = total
        if day >= month:
            for g in r["Groups"]:
                name = g["Keys"][0]
                services[name] = services.get(name, 0.0) + float(
                    g["Metrics"]["UnblendedCost"]["Amount"]
                )
        elif last_month <= day and day.day <= today.day:
            last_same_point += total
    last14 = [today - timedelta(days=i) for i in range(13, -1, -1)]
    top = sorted(services.items(), key=lambda kv: kv[1], reverse=True)[:5]
    return {
        "mtd": round(sum(services.values()), 2),
        "last_month_same_point": round(last_same_point, 2),
        "daily": [{"date": d.isoformat(), "amount": round(daily.get(d, 0.0), 2)} for d in last14],
        "top_services": [{"name": n, "amount": round(a, 2)} for n, a in top],
        "ce_api_mtd": round(services.get(CE_SERVICE, 0.0), 2),
    }


def open_anomalies(anomalies: list, today: date) -> list:
    """Anomalies still running or ended in the last 3 days, not dismissed via feedback."""
    out = []
    for a in anomalies:
        end = a.get("AnomalyEndDate")
        if end and date.fromisoformat(end[:10]) < today - timedelta(days=3):
            continue
        if a.get("Feedback") in ("NO", "PLANNED_ACTIVITY"):
            continue
        causes = a.get("RootCauses") or [{}]
        out.append(
            {
                "service": causes[0].get("Service", "Unknown service"),
                "impact": round(a["Impact"].get("TotalImpact", a["Impact"]["MaxImpact"]), 2),
                "start": a.get("AnomalyStartDate", "")[:10],
            }
        )
    return out


class Collector:
    def __init__(self, session=None, region=None):
        session = session or boto3.Session()
        # Cost Explorer and Budgets only have a us-east-1 endpoint.
        self.ce = session.client("ce", region_name="us-east-1")
        self.budgets = session.client("budgets", region_name="us-east-1")
        self.session = session
        self.region = region or session.region_name
        self.ce_requests = 0

    def costs(self, today: date) -> dict:
        start = first_of_month(first_of_month(today) - timedelta(days=1))
        results, token = [], None
        while True:
            kw = {"NextPageToken": token} if token else {}
            page = self.ce.get_cost_and_usage(
                TimePeriod={
                    "Start": start.isoformat(),
                    "End": (today + timedelta(days=1)).isoformat(),
                },
                Granularity="DAILY",
                Metrics=["UnblendedCost"],
                GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
                Filter=NO_CREDITS,
                **kw,
            )
            self.ce_requests += 1
            results += page["ResultsByTime"]
            token = page.get("NextPageToken")
            if not token:
                break
        c = summarize_costs(results, today)
        c["forecast"] = self.forecast(today)
        return c

    def forecast(self, today: date) -> float | None:
        """End-of-month total. With MONTHLY granularity and a period starting mid-month, Cost
        Explorer returns the forecast for the whole month (actuals so far included), not just
        the remaining days: verified against a DAILY forecast and the Budgets forecast."""
        next_month = first_of_month(first_of_month(today) + timedelta(days=32))
        try:
            r = self.ce.get_cost_forecast(
                TimePeriod={"Start": today.isoformat(), "End": next_month.isoformat()},
                Metric="UNBLENDED_COST",
                Granularity="MONTHLY",
                Filter=NO_CREDITS,
            )
        except ClientError as e:  # e.g. DataUnavailableException on a young account
            log.warning("forecast n/a: %s", e)
            return None
        finally:
            self.ce_requests += 1
        return round(float(r["Total"]["Amount"]), 2)

    def anomalies(self, today: date) -> list:
        found, token = [], None
        while True:
            kw = {"NextPageToken": token} if token else {}
            page = self.ce.get_anomalies(
                DateInterval={"StartDate": (today - timedelta(days=30)).isoformat()}, **kw
            )
            self.ce_requests += 1
            found += page["Anomalies"]
            token = page.get("NextPageToken")
            if not token:
                break
        return open_anomalies(found, today)

    def budget_status(self) -> list:
        account = self.session.client("sts").get_caller_identity()["Account"]
        out = []
        for page in self.budgets.get_paginator("describe_budgets").paginate(AccountId=account):
            for b in page.get("Budgets", []):
                if b["BudgetType"] != "COST" or "BudgetLimit" not in b:
                    continue
                spend = b.get("CalculatedSpend", {})
                out.append(
                    {
                        "name": b["BudgetName"],
                        "limit": float(b["BudgetLimit"]["Amount"]),
                        "actual": float(spend.get("ActualSpend", {}).get("Amount", 0)),
                        "forecast": float(spend.get("ForecastedSpend", {}).get("Amount", 0)),
                    }
                )
        return out

    def kindle(self, now: datetime) -> dict | None:
        """Last battery reading the serve Lambda published, i.e. the Kindle's last fetch."""
        r = self.session.client("cloudwatch", region_name=self.region).get_metric_data(
            MetricDataQueries=[
                {
                    "Id": "battery",
                    "MetricStat": {
                        "Metric": {"Namespace": KINDLE_NAMESPACE, "MetricName": KINDLE_METRIC},
                        "Period": 60,
                        "Stat": "Minimum",
                    },
                }
            ],
            StartTime=now - timedelta(days=3),
            EndTime=now,
            ScanBy="TimestampDescending",
        )["MetricDataResults"][0]
        if not r["Values"]:
            return None
        return {"battery": round(r["Values"][0]), "last_seen": r["Timestamps"][0].isoformat()}


def error_code(e: Exception) -> str:
    return e.response["Error"]["Code"] if isinstance(e, ClientError) else type(e).__name__


def collect(collector: Collector, now=None) -> dict:
    now = now or datetime.now(UTC)
    today = now.date()
    data = {"generated_at": now.isoformat(timespec="seconds"), "currency": "USD", "errors": {}}
    sources = {
        "costs": lambda: collector.costs(today),
        "budgets": collector.budget_status,
        "anomalies": lambda: collector.anomalies(today),
        "kindle": lambda: collector.kindle(now),
    }
    for name, fn in sources.items():
        try:
            data[name] = fn()
        except Exception as e:  # noqa: BLE001 - one failing source must not stop the others
            log.exception("source %s failed", name)
            data[name] = None
            data["errors"][name] = error_code(e)
    data["ce_requests"] = collector.ce_requests
    return data
