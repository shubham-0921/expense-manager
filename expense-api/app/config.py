from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    google_sheets_spreadsheet_id: str
    google_sheets_sheet_name: str = "Sheet1"
    google_service_account_file: str = "service_account.json"
    host: str = "0.0.0.0"
    port: int = 8000

    model_config = {"env_file": ".env"}


settings = Settings()
