#!/usr/bin/env python3
"""
Revenue Analytics — Deep revenue insights: trends, by-source, growth, forecasting.

Reads the CRM ledger (revenue now lives in the income-pipeline CRM, not SQLite).
When the ledger is unreachable the report says so and exits 1 instead of guessing.

Usage:
    python3 revenue_analytics.py              # Full analytics report
    python3 revenue_analytics.py --json       # JSON output
    python3 revenue_analytics.py --forecast   # Include 6-month forecast
"""

import json
import sys
from datetime import datetime
from pathlib import Path

# Add utils directory to path for imports
sys.path.insert(0, str(Path(__file__).parent))
from finance_totals import (
    PRIMARY_CURRENCY,
    format_money,
    ledger_revenue_rows,
    ledger_unavailable_errors,
    normalize_currency,
    revenue_overview_from_totals,
    totals_from_rows,
)


def get_rows():
    """All revenue rows from the CRM ledger."""
    return ledger_revenue_rows()


def fmt(amount, currency=PRIMARY_CURRENCY):
    return format_money(amount, currency)


def get_revenue_overview(rows):
    """High-level revenue summary. ฿-labeled fields are THB-only."""
    today = datetime.now().strftime('%Y-%m-%d')
    overview = revenue_overview_from_totals(totals_from_rows(rows))

    by_month = {}
    for r in rows:
        if r.get('received') and r['revenue_date'] <= today and normalize_currency(r.get('currency')) == PRIMARY_CURRENCY:
            month = r['revenue_date'][:7]
            by_month[month] = by_month.get(month, 0) + float(r['amount'] or 0)
    monthly_recv = [by_month[m] for m in sorted(by_month, reverse=True)[:3]]
    mrr = sum(monthly_recv) / len(monthly_recv) if monthly_recv else 0

    overview['mrr'] = mrr
    overview['arr'] = mrr * 12
    return overview


def get_by_source(rows):
    """Revenue breakdown by source_name."""
    grouped = {}
    for r in rows:
        currency = normalize_currency(r.get('currency'))
        key = (r.get('source_name'), r.get('source_type'), currency)
        g = grouped.setdefault(key, {
            'source_name': key[0], 'source_type': key[1], 'currency': currency, 'entries': 0, 'total': 0.0,
            'received': 0.0, 'outstanding': 0.0, 'first': r['revenue_date'], 'last': r['revenue_date']})
        amount = float(r['amount'] or 0)
        g['entries'] += 1
        g['total'] += amount
        g['received' if r.get('received') else 'outstanding'] += amount
        g['first'] = min(g['first'], r['revenue_date'])
        g['last'] = max(g['last'], r['revenue_date'])
    return sorted(grouped.values(), key=lambda g: g['total'], reverse=True)


def get_monthly_trends(rows):
    """Monthly revenue received — only actual (received) months."""
    grouped = {}
    for r in rows:
        currency = normalize_currency(r.get('currency'))
        g = grouped.setdefault((currency, r['revenue_date'][:7]), {
            'currency': currency, 'month': r['revenue_date'][:7], 'entries': 0, 'total': 0.0, 'received': 0.0})
        amount = float(r['amount'] or 0)
        g['entries'] += 1
        g['total'] += amount
        if r.get('received'):
            g['received'] += amount
    ordered = sorted(grouped.values(), key=lambda g: (g['month'], g['currency']))
    return [g for g in ordered if g['received'] > 0]


def get_growth_metrics(rows):
    """Calculate growth rates."""
    monthly = [
        row for row in get_monthly_trends(rows)
        if row.get('currency', PRIMARY_CURRENCY) == PRIMARY_CURRENCY
    ]
    if len(monthly) < 2:
        return {'monthly_growth_rate': 0, 'avg_monthly': 0, 'months_count': len(monthly), 'trend': 'insufficient_data', 'trend_pct': 0, 'reporting_currency': PRIMARY_CURRENCY}

    totals = [m['received'] for m in monthly]
    first, last = totals[0], totals[-1]
    n = len(totals)

    # CAGR-style monthly growth
    if first > 0 and n > 1:
        growth_rate = ((last / first) ** (1 / (n - 1)) - 1) * 100
    else:
        growth_rate = 0

    avg_monthly = sum(totals) / n

    # Trend direction: compare last 3 months avg to prior 3 months avg
    if n >= 6:
        recent_avg = sum(totals[-3:]) / 3
        prior_avg = sum(totals[-6:-3]) / 3
        if prior_avg > 0:
            trend_pct = ((recent_avg / prior_avg) - 1) * 100
        else:
            trend_pct = 100 if recent_avg > 0 else 0
    elif n >= 3:
        recent_avg = sum(totals[-3:]) / 3
        trend_pct = ((recent_avg / first) - 1) * 100 if first > 0 else 0
    else:
        trend_pct = 0

    if trend_pct > 10:
        trend = 'growing'
    elif trend_pct < -10:
        trend = 'declining'
    else:
        trend = 'stable'

    return {
        'monthly_growth_rate': round(growth_rate, 2),
        'avg_monthly': round(avg_monthly, 0),
        'months_count': n,
        'trend': trend,
        'trend_pct': round(trend_pct, 1),
        'first_month': monthly[0]['month'],
        'last_month': monthly[-1]['month'],
        'reporting_currency': PRIMARY_CURRENCY,
    }


def get_forecast(rows, months=6):
    """Simple forecast based on recent trends."""
    monthly = [
        row for row in get_monthly_trends(rows)
        if row.get('currency', PRIMARY_CURRENCY) == PRIMARY_CURRENCY
    ]
    if not monthly:
        return []

    totals = [m['received'] for m in monthly]
    avg = sum(totals) / len(totals) if totals else 0

    # Use last 3 months for short-term trend
    if len(totals) >= 3:
        recent_avg = sum(totals[-3:]) / 3
    else:
        recent_avg = avg

    # Simple linear extrapolation
    if len(totals) >= 2:
        slope = (totals[-1] - totals[0]) / len(totals)
    else:
        slope = 0

    now = datetime.now()
    base_y, base_m = now.year, now.month
    forecast = []
    cumulative = sum(totals)

    for i in range(months):
        total_m = (base_m + i - 1) % 12 + 1
        total_y = (base_y * 12 + base_m + i - 1) // 12

        # Blend recent_avg with slope projection
        projected = recent_avg + slope * (i + 1)
        projected = max(0, projected)

        cumulative += projected
        forecast.append({
            'month': f'{total_y}-{total_m:02d}',
            'projected': round(projected, 0),
            'cumulative': round(cumulative, 0),
        })

    return forecast


def generate_report(forecast_mode=False):
    """Generate text report."""
    rows = get_rows()

    overview = get_revenue_overview(rows)
    by_source = get_by_source(rows)
    trends = get_monthly_trends(rows)
    growth = get_growth_metrics(rows)

    lines = []
    lines.append('# 💰 Revenue Analytics')
    lines.append(f'\n*Generated: {datetime.now().strftime("%Y-%m-%d %H:%M")}*\n')

    # Overview
    lines.append('## Overview\n')
    lines.append(f'| Metric | Value |')
    lines.append(f'|--------|-------|')
    lines.append(f'| MRR (Monthly Recurring, THB) | {fmt(overview["mrr"])} |')
    lines.append(f'| ARR (Annual Run Rate, THB) | {fmt(overview["arr"])} |')
    lines.append(f'| Total Received | {overview["received_display"]} |')
    lines.append(f'| Total Pending | {overview["pending_display"]} |')
    lines.append(f'| Total Contract Value (THB) | {fmt(overview["total_contract"])} |')
    lines.append(f'| Revenue Entries | {overview["entries"]} |')

    # By source
    lines.append('\n## Revenue by Source\n')
    lines.append('| Source | Type | Currency | Entries | Total | Received | Outstanding |')
    lines.append('|--------|------|----------|---------|-------|----------|-------------|')
    for s in by_source:
        currency = s.get("currency") or PRIMARY_CURRENCY
        lines.append(f'| {s["source_name"]} | {s["source_type"]} | {currency} | {s["entries"]} | '
                      f'{fmt(s["total"], currency)} | {fmt(s["received"], currency)} | {fmt(s["outstanding"], currency)} |')

    # Monthly trends
    lines.append('\n## Monthly Trends (Received)\n')
    lines.append('| Month | Currency | Entries | Received |')
    lines.append('|-------|----------|---------|----------|')
    for t in trends:
        currency = t.get("currency") or PRIMARY_CURRENCY
        lines.append(f'| {t["month"]} | {currency} | {t["entries"]} | {fmt(t["received"], currency)} |')

    # Growth
    lines.append('\n## Growth Metrics\n')
    lines.append(f'- **Trend:** {growth["trend"].upper()} ({growth["trend_pct"]:+.1f}%)')
    lines.append(f'- **Monthly Growth Rate:** {growth["monthly_growth_rate"]:.2f}%')
    lines.append(f'- **Avg Monthly Revenue:** {fmt(growth["avg_monthly"])}')
    lines.append(f'- **Months Tracked:** {growth["months_count"]}')
    if growth.get('first_month'):
        lines.append(f'- **Period:** {growth["first_month"]} → {growth["last_month"]}')

    # Forecast
    if forecast_mode:
        fc = get_forecast(rows, 6)
        if fc:
            lines.append('\n## 6-Month Forecast\n')
            lines.append('| Month | Projected | Cumulative |')
            lines.append('|-------|-----------|------------|')
            for f in fc:
                lines.append(f'| {f["month"]} | {fmt(f["projected"])} | {fmt(f["cumulative"])} |')

    return '\n'.join(lines)


def main():
    args = sys.argv[1:]
    json_mode = '--json' in args
    forecast_mode = '--forecast' in args or json_mode

    try:
        if json_mode:
            rows = get_rows()
            result = {
                'timestamp': datetime.now().isoformat(),
                'overview': get_revenue_overview(rows),
                'by_source': get_by_source(rows),
                'monthly_trends': get_monthly_trends(rows),
                'growth': get_growth_metrics(rows),
            }
            if forecast_mode:
                result['forecast'] = get_forecast(rows, 6)
            print(json.dumps(result, indent=2))
        else:
            print(generate_report(forecast_mode))
    except ledger_unavailable_errors() as exc:
        print(f'❌ revenue ledger unavailable: {exc}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
