from fastapi import FastAPI, HTTPException

from app.database import init_db, register_user, get_user
from app.models import (
    ExpenseRequest, ExpenseResponse,
    SummaryResponse,
    UserRegistrationRequest, UserRegistrationResponse,
)
from app.service import ExpenseService
from app.sheets_client import SheetsClient

app = FastAPI(title="Expense Tracker API", version="1.0.0")

sheets_client = SheetsClient()
expense_service = ExpenseService(sheets_client)

# Initialize the user database on startup
init_db()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/register", response_model=UserRegistrationResponse)
def register(req: UserRegistrationRequest):
    try:
        # Write header row to the sheet if missing
        sheets_client.ensure_headers(req.spreadsheet_id, req.sheet_name)
        register_user(req.telegram_user_id, req.spreadsheet_id, req.sheet_name, req.name)
        return UserRegistrationResponse(
            status="success",
            message=f"User {req.telegram_user_id} registered with spreadsheet {req.spreadsheet_id}",
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/user/{telegram_user_id}")
def get_user_info(telegram_user_id: str):
    user = get_user(telegram_user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not registered")
    return user


@app.post("/expense", response_model=ExpenseResponse)
def add_expense(expense: ExpenseRequest):
    try:
        return expense_service.add_expense(expense)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.get("/summary", response_model=SummaryResponse)
def get_summary(user_id: str, month: str | None = None, category: str | None = None):
    try:
        return expense_service.get_summary(user_id=user_id, month=month, category=category)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
