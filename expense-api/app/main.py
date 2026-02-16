from fastapi import FastAPI, HTTPException

from app.database import init_db, register_user, get_user, set_splitwise_token, get_splitwise_token
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


@app.post("/splitwise-token")
def save_splitwise_token(req: dict):
    """Store a user's Splitwise MCP token."""
    telegram_user_id = req.get("telegram_user_id")
    token = req.get("splitwise_token")
    if not telegram_user_id or not token:
        raise HTTPException(status_code=400, detail="telegram_user_id and splitwise_token required")
    user = get_user(telegram_user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not registered")
    set_splitwise_token(telegram_user_id, token)
    return {"status": "success", "message": "Splitwise token saved"}


@app.get("/splitwise-token/{telegram_user_id}")
def get_splitwise_token_endpoint(telegram_user_id: str):
    """Get a user's Splitwise MCP token."""
    token = get_splitwise_token(telegram_user_id)
    if not token:
        raise HTTPException(status_code=404, detail="No Splitwise token found")
    return {"splitwise_token": token}


@app.delete("/splitwise-token/{telegram_user_id}")
def delete_splitwise_token(telegram_user_id: str):
    """Remove a user's Splitwise MCP token."""
    user = get_user(telegram_user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not registered")
    set_splitwise_token(telegram_user_id, "")
    return {"status": "success", "message": "Splitwise token removed"}


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
