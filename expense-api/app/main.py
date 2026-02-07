from fastapi import FastAPI, HTTPException

from app.models import ExpenseRequest, ExpenseResponse, SummaryRequest, SummaryResponse
from app.service import ExpenseService
from app.sheets_client import SheetsClient

app = FastAPI(title="Expense Tracker API", version="1.0.0")

sheets_client = SheetsClient()
expense_service = ExpenseService(sheets_client)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/expense", response_model=ExpenseResponse)
def add_expense(expense: ExpenseRequest):
    try:
        return expense_service.add_expense(expense)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/summary", response_model=SummaryResponse)
def get_summary(month: str | None = None, category: str | None = None):
    try:
        return expense_service.get_summary(month=month, category=category)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
