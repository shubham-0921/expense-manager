from pydantic import BaseModel, Field
from typing import Optional


class ExpenseRequest(BaseModel):
    date: str = Field(..., description="Date of expense (e.g. '2025-01-15' or '15 Jan 2025')")
    amount: float = Field(..., description="Expense amount")
    payment_method: str = Field("", description="Payment method (e.g. 'UPI', 'Cash', 'Credit Card')")
    category: str = Field(..., description="Expense category (e.g. 'Food', 'Transport', 'Shopping')")
    details: str = Field("", description="What the expense was for")
    split_with: str = Field("", description="Person to split with, if any")
    added_to_split: str = Field("", description="Whether added to splitwise or similar (e.g. 'Yes', 'No')")
    comment: str = Field("", description="Any additional notes")


class ExpenseResponse(BaseModel):
    status: str
    message: str
    row_number: int


class SummaryRequest(BaseModel):
    month: Optional[str] = Field(None, description="Month to filter (e.g. '2025-01' or 'January 2025')")
    category: Optional[str] = Field(None, description="Category to filter by")


class SummaryResponse(BaseModel):
    total: float
    count: int
    by_category: dict[str, float]
    by_payment_mode: dict[str, float]
    filters_applied: dict[str, str]
