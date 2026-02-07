from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from app.config import settings

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Column mapping: A=Date, B=Amount, C=Mode of Payment, D=Category,
# E=Details, F=Split with, G=Added to split, H=Comment
COLUMNS = ["Date", "Amount", "Mode of Payment", "Category", "Details", "Split with", "Added to split", "Comment"]


class SheetsClient:
    def __init__(self):
        creds = Credentials.from_service_account_file(
            settings.google_service_account_file, scopes=SCOPES
        )
        service = build("sheets", "v4", credentials=creds)
        self.sheet = service.spreadsheets()
        self.spreadsheet_id = settings.google_sheets_spreadsheet_id
        self.sheet_name = settings.google_sheets_sheet_name

    def _range(self, range_str: str) -> str:
        return f"{self.sheet_name}!{range_str}"

    def get_all_rows(self) -> list[list[str]]:
        """Fetch all data rows (excluding header)."""
        result = self.sheet.values().get(
            spreadsheetId=self.spreadsheet_id,
            range=self._range("A:H"),
        ).execute()
        values = result.get("values", [])
        # Skip header row if present
        if values and values[0] == COLUMNS:
            return values[1:]
        return values

    def get_row_count(self) -> int:
        """Get total number of rows including header."""
        result = self.sheet.values().get(
            spreadsheetId=self.spreadsheet_id,
            range=self._range("A:A"),
        ).execute()
        values = result.get("values", [])
        return len(values)

    def append_row(self, row: list[str]) -> int:
        """Append a row and return the row number it was inserted at."""
        next_row = self.get_row_count() + 1
        self.sheet.values().update(
            spreadsheetId=self.spreadsheet_id,
            range=self._range(f"A{next_row}"),
            valueInputOption="USER_ENTERED",
            body={"values": [row]},
        ).execute()
        return next_row
