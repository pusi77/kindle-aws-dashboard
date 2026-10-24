from datetime import date

from collect import collect, open_anomalies, summarize_costs


def day(d, **services):
    groups = [
        {"Keys": [k], "Metrics": {"UnblendedCost": {"Amount": str(v)}}} for k, v in services.items()
    ]
    return {"TimePeriod": {"Start": d}, "Groups": groups}


def test_summarize_costs():
    results = [
        day("2026-09-01", EC2=10),
        day("2026-09-02", EC2=5),
        day("2026-09-03", EC2=100),  # after "same point" (today is the 2nd): excluded
        day("2026-10-01", EC2=7, S3=1, **{"AWS Cost Explorer": 0.03}),
        day("2026-10-02", EC2=2),  # today, partial
    ]
    c = summarize_costs(results, date(2026, 10, 2))
    assert c["mtd"] == 10.03
    assert c["last_month_same_point"] == 15
    assert c["top_services"][0] == {"name": "EC2", "amount": 9}
    assert c["ce_api_mtd"] == 0.03
    assert len(c["daily"]) == 14
    assert c["daily"][-1] == {"date": "2026-10-02", "amount": 2}
    assert c["daily"][0] == {"date": "2026-09-19", "amount": 0}


def test_open_anomalies():
    base = {"Impact": {"MaxImpact": 5, "TotalImpact": 12.345}, "RootCauses": [{"Service": "S3"}]}
    found = open_anomalies(
        [
            {**base, "AnomalyStartDate": "2026-10-20", "AnomalyEndDate": "2026-10-23"},
            {**base, "AnomalyStartDate": "2026-10-01", "AnomalyEndDate": "2026-10-05"},  # old
            {**base, "AnomalyStartDate": "2026-10-22", "Feedback": "NO"},  # dismissed
        ],
        date(2026, 10, 24),
    )
    assert found == [{"service": "S3", "impact": 12.35, "start": "2026-10-20"}]


def test_one_failing_source_does_not_stop_the_others():
    class Broken:
        ce_requests = 0

        def costs(self, today):
            raise RuntimeError("boom")

        def budget_status(self):
            return []

        def anomalies(self, today):
            return []

        def kindle(self, now):
            return None

    data = collect(Broken())
    assert data["costs"] is None
    assert data["errors"] == {"costs": "RuntimeError"}
    assert data["budgets"] == [] and data["anomalies"] == []
