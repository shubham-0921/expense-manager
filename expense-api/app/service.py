from collections import defaultdict
from datetime import datetime

from app.models import ExpenseRequest, ExpenseResponse, SummaryResponse
from app.sheets_client import SheetsClient


class ExpenseService:
    def __init__(self, client: SheetsClient):
        self.client = client

    def add_expense(self, expense: ExpenseRequest) -> ExpenseResponse:
        row = [
            expense.date,
            str(expense.amount),
            expense.payment_method,
            expense.category,
            expense.details,
            expense.split_with,
            expense.added_to_split,
            expense.comment,
        ]
        row_number = self.client.append_row(row)
        return ExpenseResponse(
            status="success",
            message=f"Expense of {expense.amount} added to row {row_number}",
            row_number=row_number,
        )

    def get_summary(self, month: str | None = None, category: str | None = None) -> SummaryResponse:
        rows = self.client.get_all_rows()
        filters_applied: dict[str, str] = {}

        if month:
            rows = self._filter_by_month(rows, month)
            filters_applied["month"] = month

        if category:
            rows = [r for r in rows if len(r) > 3 and r[3].strip().lower() == category.strip().lower()]
            filters_applied["category"] = category

        total = 0.0
        count = 0
        by_category: dict[str, float] = defaultdict(float)
        by_payment_mode: dict[str, float] = defaultdict(float)

        for row in rows:
            try:
                amount = float(row[1]) if len(row) > 1 else 0.0
            except (ValueError, IndexError):
                continue

            total += amount
            count += 1

            cat = row[3].strip() if len(row) > 3 and row[3].strip() else "Uncategorized"
            by_category[cat] += amount

            mode = row[2].strip() if len(row) > 2 and row[2].strip() else "Unknown"
            by_payment_mode[mode] += amount

        return SummaryResponse(
            total=round(total, 2),
            count=count,
            by_category=dict(by_category),
            by_payment_mode=dict(by_payment_mode),
            filters_applied=filters_applied,
        )

    def _filter_by_month(self, rows: list[list[str]], month: str) -> list[list[str]]:
        """Filter rows by month. Accepts '2025-01' or 'January 2025' formats."""
        target_year, target_month = self._parse_month(month)
        if target_year is None:
            return rows

        filtered = []
        for row in rows:
            if not row or not row[0].strip():
                continue
            row_date = self._parse_date(row[0].strip())
            if row_date and row_date.year == target_year and row_date.month == target_month:
                filtered.append(row)
        return filtered

    def _parse_month(self, month_str: str) -> tuple[int | None, int | None]:
        """Parse month string into (year, month)."""
        # Try '2025-01' format
        try:
            dt = datetime.strptime(month_str, "%Y-%m")
            return dt.year, dt.month
        except ValueError:
            pass

        # Try 'January 2025' format
        try:
            dt = datetime.strptime(month_str, "%B %Y")
            return dt.year, dt.month
        except ValueError:
            pass

        # Try 'Jan 2025' format
        try:
            dt = datetime.strptime(month_str, "%b %Y")
            return dt.year, dt.month
        except ValueError:
            pass

        return None, None

    def _parse_date(self, date_str: str) -> datetime | None:
        """Try multiple date formats."""
        formats = [
            "%Y-%m-%d",
            "%d-%m-%Y",
            "%d/%m/%Y",
            "%d %b %Y",
            "%d %B %Y",
            "%m/%d/%Y",
        ]
        for fmt in formats:
            try:
                return datetime.strptime(date_str, fmt)
            except ValueError:
                continue
        return None
