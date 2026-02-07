from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_sheets_spreadsheet_id: str = ""  # kept for backwards compat, per-user sheets are looked up from DB
    google_sheets_sheet_name: str = "Sheet1"
    google_service_account_file: str = "service_account.json"
    database_path: str = "data/users.db"
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env"}


settings = Settings()
