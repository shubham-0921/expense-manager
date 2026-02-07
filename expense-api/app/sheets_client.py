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

    def _range(self, sheet_name: str, range_str: str) -> str:
        return f"{sheet_name}!{range_str}"

    def ensure_headers(self, spreadsheet_id: str, sheet_name: str):
        """Write header row if the sheet is empty or missing headers."""
        result = self.sheet.values().get(
            spreadsheetId=spreadsheet_id,
            range=self._range(sheet_name, "A1:H1"),
        ).execute()
        values = result.get("values", [])
        if not values or values[0] != COLUMNS:
            self.sheet.values().update(
                spreadsheetId=spreadsheet_id,
                range=self._range(sheet_name, "A1"),
                valueInputOption="USER_ENTERED",
                body={"values": [COLUMNS]},
            ).execute()

    def get_all_rows(self, spreadsheet_id: str, sheet_name: str) -> list[list[str]]:
        """Fetch all data rows (excluding header)."""
        result = self.sheet.values().get(
            spreadsheetId=spreadsheet_id,
            range=self._range(sheet_name, "A:H"),
        ).execute()
        values = result.get("values", [])
        # Skip header row if present
        if values and values[0] == COLUMNS:
            return values[1:]
        return values

    def get_row_count(self, spreadsheet_id: str, sheet_name: str) -> int:
        """Get total number of rows including header."""
        result = self.sheet.values().get(
            spreadsheetId=spreadsheet_id,
            range=self._range(sheet_name, "A:A"),
        ).execute()
        values = result.get("values", [])
        return len(values)

    def append_row(self, spreadsheet_id: str, sheet_name: str, row: list[str]) -> int:
        """Append a row and return the row number it was inserted at."""
        next_row = self.get_row_count(spreadsheet_id, sheet_name) + 1
        self.sheet.values().update(
            spreadsheetId=spreadsheet_id,
            range=self._range(sheet_name, f"A{next_row}"),
            valueInputOption="USER_ENTERED",
            body={"values": [row]},
        ).execute()
        return next_row
