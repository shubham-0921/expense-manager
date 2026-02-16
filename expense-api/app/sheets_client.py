from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from app.config import settings

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

# Column mapping: A=Date, B=Amount, C=Mode of Payment, D=Category,
# E=Details, F=Split with, G=Added to split, H=Comment
COLUMNS = ["Date", "Amount", "Mode of Payment", "Category", "Details", "Split with", "Added to split", "Comment"]


class SheetsClient:
    def __init__(self):
        self._creds = Credentials.from_service_account_file(
            settings.google_service_account_file, scopes=SCOPES
        )
        self._build_service()

    def _build_service(self):
        service = build("sheets", "v4", credentials=self._creds)
        self.sheet = service.spreadsheets()

    def _execute_with_retry(self, request):
        """Execute a Google Sheets API request, rebuilding the connection on BrokenPipeError."""
        try:
            return request.execute()
        except BrokenPipeError:
            self._build_service()
            return request.execute()

    def _range(self, sheet_name: str, range_str: str) -> str:
        return f"{sheet_name}!{range_str}"

    def ensure_headers(self, spreadsheet_id: str, sheet_name: str):
        """Write header row if the sheet is empty or missing headers."""
        result = self._execute_with_retry(self.sheet.values().get(
            spreadsheetId=spreadsheet_id,
            range=self._range(sheet_name, "A1:H1"),
        ))
        values = result.get("values", [])
        if not values or values[0] != COLUMNS:
            self._execute_with_retry(self.sheet.values().update(
                spreadsheetId=spreadsheet_id,
                range=self._range(sheet_name, "A1"),
                valueInputOption="USER_ENTERED",
                body={"values": [COLUMNS]},
            ))

    def get_all_rows(self, spreadsheet_id: str, sheet_name: str) -> list[list[str]]:
        """Fetch all data rows (excluding header)."""
        result = self._execute_with_retry(self.sheet.values().get(
            spreadsheetId=spreadsheet_id,
            range=self._range(sheet_name, "A:H"),
        ))
        values = result.get("values", [])
        # Skip header row if present
        if values and values[0] == COLUMNS:
            return values[1:]
        return values

    def get_row_count(self, spreadsheet_id: str, sheet_name: str) -> int:
        """Get total number of rows including header."""
        result = self._execute_with_retry(self.sheet.values().get(
            spreadsheetId=spreadsheet_id,
            range=self._range(sheet_name, "A:A"),
        ))
        values = result.get("values", [])
        return len(values)

    def append_row(self, spreadsheet_id: str, sheet_name: str, row: list[str]) -> int:
        """Append a row and return the row number it was inserted at."""
        next_row = self.get_row_count(spreadsheet_id, sheet_name) + 1
        self._execute_with_retry(self.sheet.values().update(
            spreadsheetId=spreadsheet_id,
            range=self._range(sheet_name, f"A{next_row}"),
            valueInputOption="USER_ENTERED",
            body={"values": [row]},
        ))
        return next_row
