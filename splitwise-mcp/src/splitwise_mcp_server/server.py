"""FastMCP server implementation with tool definitions and OAuth routes."""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, AsyncIterator
from urllib.parse import urlencode

import httpx
from fastmcp import FastMCP
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse

from .client import ClientManager, SplitwiseClient
from .resolver import EntityResolver
from .token_store import TokenStore
from .user_context import get_access_token
from .errors import (
    ValidationError,
    RateLimitError,
    validate_required,
    validate_positive_number,
    validate_currency_code,
    validate_date_format,
    validate_email,
    validate_range,
    validate_choice,
    validate_user_split,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ── Globals (initialised in lifespan) ────────────────────────────────────
client_manager: Optional[ClientManager] = None
token_store: Optional[TokenStore] = None

TEMPLATES_DIR = Path(__file__).parent / "templates"


# ── Helpers ──────────────────────────────────────────────────────────────

def _get_client() -> SplitwiseClient:
    """Return the SplitwiseClient for the current request's user."""
    return client_manager.get_client()


def _get_resolver() -> EntityResolver:
    """Return an EntityResolver backed by the current user's client."""
    return EntityResolver(_get_client())


def _server_url() -> str:
    """Base URL the server is externally reachable at."""
    return os.environ.get("SERVER_URL", "http://localhost:8000").rstrip("/")


# ── Lifespan ─────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(server: FastMCP) -> AsyncIterator[None]:
    global client_manager, token_store

    logger.info("Starting Splitwise MCP Server …")

    consumer_key = os.environ.get("SPLITWISE_CONSUMER_KEY", "")
    consumer_secret = os.environ.get("SPLITWISE_CONSUMER_SECRET", "")
    if not consumer_key or not consumer_secret:
        raise ValueError(
            "SPLITWISE_CONSUMER_KEY and SPLITWISE_CONSUMER_SECRET must be set"
        )

    cache_ttl = int(os.environ.get("SPLITWISE_CACHE_TTL", "86400"))
    db_path = os.environ.get("TOKEN_DB_PATH")

    client_manager = ClientManager(consumer_key, consumer_secret, cache_ttl=cache_ttl)
    token_store = TokenStore(db_path)

    logger.info("Splitwise MCP Server started (multi-user mode)")
    try:
        yield
    finally:
        logger.info("Shutting down …")
        if client_manager:
            await client_manager.close_all()
        logger.info("Shutdown complete")


# ── Server ───────────────────────────────────────────────────────────────

def create_server() -> FastMCP:
    mcp = FastMCP("Splitwise MCP Server", lifespan=lifespan)

    # ── OAuth web routes ─────────────────────────────────────────────
    _register_oauth_routes(mcp)

    # ── MCP tools ────────────────────────────────────────────────────
    register_user_tools(mcp)
    register_expense_tools(mcp)
    register_group_tools(mcp)
    register_friend_tools(mcp)
    register_resolution_tools(mcp)
    register_comment_tools(mcp)
    register_utility_tools(mcp)
    register_arithmetic_tools(mcp)

    logger.info("All tools registered")
    return mcp


# ── OAuth Routes ─────────────────────────────────────────────────────────

def _register_oauth_routes(mcp: FastMCP) -> None:

    @mcp.custom_route("/", methods=["GET"])
    async def index(request: Request):
        return HTMLResponse(
            "<h1>Splitwise MCP Server</h1>"
            '<p><a href="/authorize">Connect your Splitwise account</a></p>'
        )

    @mcp.custom_route("/authorize", methods=["GET"])
    async def authorize(request: Request):
        consumer_key = os.environ["SPLITWISE_CONSUMER_KEY"]
        redirect_uri = f"{_server_url()}/callback"
        params = urlencode({
            "client_id": consumer_key,
            "response_type": "code",
            "redirect_uri": redirect_uri,
        })
        return RedirectResponse(
            f"https://secure.splitwise.com/oauth/authorize?{params}"
        )

    @mcp.custom_route("/callback", methods=["GET"])
    async def callback(request: Request):
        code = request.query_params.get("code")
        if not code:
            return JSONResponse({"error": "Missing code parameter"}, status_code=400)

        consumer_key = os.environ["SPLITWISE_CONSUMER_KEY"]
        consumer_secret = os.environ["SPLITWISE_CONSUMER_SECRET"]
        redirect_uri = f"{_server_url()}/callback"

        # Exchange authorisation code for access token
        async with httpx.AsyncClient(timeout=30.0) as http:
            resp = await http.post(
                "https://secure.splitwise.com/oauth/token",
                data={
                    "client_id": consumer_key,
                    "client_secret": consumer_secret,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": redirect_uri,
                },
            )
            if resp.status_code != 200:
                logger.error(f"Token exchange failed: {resp.text}")
                return JSONResponse(
                    {"error": "Token exchange failed", "details": resp.text},
                    status_code=502,
                )
            access_token = resp.json().get("access_token")

        # Fetch user info to store alongside token
        async with httpx.AsyncClient(timeout=30.0) as http:
            user_resp = await http.get(
                "https://secure.splitwise.com/api/v3.0/get_current_user",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            user_data = user_resp.json().get("user", {}) if user_resp.status_code == 200 else {}

        user_info = {
            "id": user_data.get("id"),
            "name": f"{user_data.get('first_name', '')} {user_data.get('last_name', '')}".strip(),
            "email": user_data.get("email"),
        }

        user_token = token_store.create_user(access_token, user_info)
        mcp_url = f"{_server_url()}/mcp?token={user_token}"

        # Render success page
        template = (TEMPLATES_DIR / "success.html").read_text()
        html = (
            template
            .replace("{{user_name}}", user_info.get("name") or "Splitwise User")
            .replace("{{mcp_url}}", mcp_url)
        )
        return HTMLResponse(html)


# ── Auth Middleware (applied inside each tool) ───────────────────────────
# FastMCP's streamable-http transport passes query params through the
# request.  We hook into the MCP request lifecycle via a thin wrapper that
# reads ?token=<uuid>, looks up the real Splitwise access token, and sets
# the context variable.  Because @custom_route handlers are plain Starlette
# handlers they do NOT go through this path — which is what we want (OAuth
# endpoints must be public).
#
# FastMCP does not expose per-tool middleware, so the simplest approach is
# to validate inside each tool.  This is centralised in _get_client() which
# calls client_manager.get_client() → reads current_access_token context var.
# The context var is set by the MCP auth_server_provider or, for the
# streamable-http transport, by a Starlette middleware we inject in __main__.


# ============================================================================
# User Tools
# ============================================================================

def register_user_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_current_user() -> Dict[str, Any]:
        """Get information about the currently authenticated user.

        Returns detailed profile information for the authenticated user including
        their ID, name, email, registration status, and profile picture.
        """
        try:
            return await _get_client().get_current_user()
        except Exception as e:
            logger.error(f"Error getting current user: {e}")
            raise

    @mcp.tool()
    async def get_user(user_id: int) -> Dict[str, Any]:
        """Get information about a specific user by ID."""
        try:
            return await _get_client().get_user(user_id)
        except Exception as e:
            logger.error(f"Error getting user {user_id}: {e}")
            raise


# ============================================================================
# Expense Tools
# ============================================================================

def register_expense_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def create_expense(
        cost: str,
        description: str,
        group_id: int = 0,
        currency_code: str = "INR",
        date: Optional[str] = None,
        category_id: Optional[int] = None,
        users: Optional[List[Dict[str, Any]]] = None,
        split_equally: bool = True,
    ) -> Dict[str, Any]:
        """Create a new expense in Splitwise.

        IMPORTANT: For any calculations involving amounts, you MUST use the arithmetic
        tools (add, subtract, multiply, divide, modulo) BEFORE calling this tool.

        Args:
            cost: Total amount as string with 2 decimal places (e.g., "25.50")
            description: Short description of the expense
            group_id: Group ID for group expenses (default: 0 for non-group expense)
            currency_code: Three-letter currency code (default: "USD")
            date: ISO 8601 datetime string (default: current date/time)
            category_id: Category ID from get_categories (optional)
            users: List of user split information with user_id, paid_share, owed_share (optional).
                   The current authenticated user is auto-included if not already in the list.
            split_equally: Whether to split the expense equally among users (default: True)
        """
        try:
            validate_required(cost, "cost")
            validate_required(description, "description")
            validate_positive_number(cost, "cost")
            validate_currency_code(currency_code)
            if date:
                validate_date_format(date, "date")
            if group_id < 0:
                raise ValidationError("group_id must be non-negative", field="group_id")
            if category_id is not None and category_id <= 0:
                raise ValidationError("category_id must be positive", field="category_id")
            if users:
                validate_user_split(users)

            def _build_expense_data() -> Dict[str, Any]:
                """Build the expense payload (without users — added separately)."""
                data: Dict[str, Any] = {
                    "cost": cost,
                    "description": description,
                    "currency_code": currency_code,
                    "group_id": group_id,
                    "date": date or (datetime.utcnow().isoformat() + "Z"),
                }
                if category_id is not None:
                    data["category_id"] = category_id
                return data

            async def _prepare_users(
                users_input: Optional[List[Dict[str, Any]]],
            ) -> Optional[List[Dict[str, Any]]]:
                """Deep-copy users, auto-include current user, apply equal split."""
                if not users_input:
                    return None
                # Deep copy so retries start from a clean state
                prepared = [dict(u) for u in users_input]

                current_user_data = await _get_client().get_current_user()
                current_user_id = current_user_data.get("user", {}).get("id")
                if current_user_id:
                    user_ids = {u.get("user_id") for u in prepared}
                    if current_user_id not in user_ids:
                        prepared.insert(0, {"user_id": current_user_id})

                if split_equally:
                    total = float(cost)
                    n = len(prepared)
                    per_person = round(total / n, 2)
                    for i, user in enumerate(prepared):
                        user.setdefault("paid_share", f"{total:.2f}" if i == 0 else "0.00")
                        user.setdefault("owed_share", f"{per_person:.2f}")
                return prepared

            async def _submit_expense(
                users_input: Optional[List[Dict[str, Any]]],
            ) -> Dict[str, Any]:
                """Build, prepare, and submit the expense to Splitwise."""
                expense_data = _build_expense_data()
                prepared = await _prepare_users(users_input)
                if prepared:
                    expense_data["users"] = prepared

                result = await _get_client().create_expense(expense_data)
                logger.info(f"create_expense response: {result}")

                # Splitwise returns 200 even on errors — check the response body
                if isinstance(result, dict):
                    errors = result.get("errors")
                    if errors:
                        logger.error(f"Splitwise create_expense errors: {errors}")
                        raise Exception(f"Splitwise error: {errors}")
                return result

            # --- First attempt ---
            try:
                return await _submit_expense(users)
            except Exception as first_err:
                err_str = str(first_err).lower()
                if "not in your friends list" not in err_str:
                    raise  # Not the stale-friends error — don't retry

                # --- Retry: refresh Splitwise's server-side friends cache ---
                logger.warning(
                    "Splitwise friends-list error detected. "
                    "Refreshing friends list and retrying…"
                )
                try:
                    await _get_client().get_friends()
                except Exception:
                    logger.warning("Friends list refresh call failed, retrying expense anyway")

                try:
                    return await _submit_expense(users)
                except Exception as retry_err:
                    reauth_url = f"{_server_url()}/authorize"
                    raise Exception(
                        "Splitwise still reports a friends-list sync issue after retry. "
                        "This is a temporary Splitwise server-side problem.\n\n"
                        "Please re-connect your Splitwise account to fix it: "
                        f"{reauth_url}"
                    ) from retry_err

        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error creating expense: {e}")
            raise

    @mcp.tool()
    async def get_expenses(
        group_id: Optional[int] = None,
        friend_id: Optional[int] = None,
        dated_after: Optional[str] = None,
        dated_before: Optional[str] = None,
        updated_after: Optional[str] = None,
        updated_before: Optional[str] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> Dict[str, Any]:
        """Get list of expenses with optional filters.

        Args:
            group_id: Filter by group ID (optional)
            friend_id: Filter by friend user ID (optional)
            dated_after: ISO 8601 date filter (optional)
            dated_before: ISO 8601 date filter (optional)
            updated_after: ISO 8601 date filter (optional)
            updated_before: ISO 8601 date filter (optional)
            limit: Max expenses to return (default: 20, max: 100)
            offset: Pagination offset (default: 0)
        """
        try:
            for name, val in [("dated_after", dated_after), ("dated_before", dated_before),
                              ("updated_after", updated_after), ("updated_before", updated_before)]:
                if val:
                    validate_date_format(val, name)
            validate_range(limit, "limit", min_val=1, max_val=100)
            validate_range(offset, "offset", min_val=0)

            return await _get_client().get_expenses(
                group_id=group_id, friend_id=friend_id,
                dated_after=dated_after, dated_before=dated_before,
                updated_after=updated_after, updated_before=updated_before,
                limit=limit, offset=offset,
            )
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error getting expenses: {e}")
            raise

    @mcp.tool()
    async def get_expense(expense_id: int) -> Dict[str, Any]:
        """Get detailed information about a specific expense."""
        try:
            return await _get_client().get_expense(expense_id)
        except Exception as e:
            logger.error(f"Error getting expense {expense_id}: {e}")
            raise

    @mcp.tool()
    async def update_expense(
        expense_id: int,
        cost: Optional[str] = None,
        description: Optional[str] = None,
        date: Optional[str] = None,
        category_id: Optional[int] = None,
        users: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Update an existing expense. Only provided fields are changed."""
        try:
            validate_required(expense_id, "expense_id")
            if expense_id <= 0:
                raise ValidationError("expense_id must be positive", field="expense_id")
            if cost is not None:
                validate_positive_number(cost, "cost")
            if date is not None:
                validate_date_format(date, "date")
            if category_id is not None and category_id <= 0:
                raise ValidationError("category_id must be positive", field="category_id")
            if users is not None:
                validate_user_split(users)

            expense_data: Dict[str, Any] = {}
            if cost is not None:
                expense_data["cost"] = cost
            if description is not None:
                expense_data["description"] = description
            if date is not None:
                expense_data["date"] = date
            if category_id is not None:
                expense_data["category_id"] = category_id
            if users is not None:
                expense_data["users"] = users
            if not expense_data:
                raise ValidationError("At least one field must be provided to update")

            return await _get_client().update_expense(expense_id, expense_data)
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error updating expense {expense_id}: {e}")
            raise

    @mcp.tool()
    async def delete_expense(expense_id: int) -> Dict[str, Any]:
        """Delete an expense. This action cannot be undone."""
        try:
            return await _get_client().delete_expense(expense_id)
        except Exception as e:
            logger.error(f"Error deleting expense {expense_id}: {e}")
            raise


# ============================================================================
# Group Tools
# ============================================================================

def register_group_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_groups() -> Dict[str, Any]:
        """Get all groups for the current user."""
        try:
            return await _get_client().get_groups()
        except Exception as e:
            logger.error(f"Error getting groups: {e}")
            raise

    @mcp.tool()
    async def get_group(group_id: int) -> Dict[str, Any]:
        """Get detailed information about a specific group."""
        try:
            return await _get_client().get_group(group_id)
        except Exception as e:
            logger.error(f"Error getting group {group_id}: {e}")
            raise

    @mcp.tool()
    async def create_group(
        name: str,
        group_type: str = "other",
        simplify_by_default: bool = True,
        users: Optional[List[Dict[str, Any]]] = None,
    ) -> Dict[str, Any]:
        """Create a new group."""
        try:
            validate_required(name, "name")
            validate_choice(group_type, "group_type", ["home", "trip", "couple", "other"])
            if users:
                if not isinstance(users, list):
                    raise ValidationError("users must be a list", field="users")
                for i, user in enumerate(users):
                    if not isinstance(user, dict):
                        raise ValidationError(f"users[{i}] must be a dictionary", field="users")
                    if "email" in user and user["email"]:
                        validate_email(user["email"])

            group_data: Dict[str, Any] = {
                "name": name,
                "group_type": group_type,
                "simplify_by_default": simplify_by_default,
            }
            if users:
                group_data["users"] = users

            return await _get_client().create_group(group_data)
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error creating group: {e}")
            raise

    @mcp.tool()
    async def delete_group(group_id: int) -> Dict[str, Any]:
        """Delete a group. All expenses must be settled first."""
        try:
            return await _get_client().delete_group(group_id)
        except Exception as e:
            logger.error(f"Error deleting group {group_id}: {e}")
            raise

    @mcp.tool()
    async def add_user_to_group(
        group_id: int,
        user_id: Optional[int] = None,
        email: Optional[str] = None,
        first_name: Optional[str] = None,
        last_name: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Add a user to a group by user_id or email."""
        try:
            validate_required(group_id, "group_id")
            if group_id <= 0:
                raise ValidationError("group_id must be positive", field="group_id")
            if not user_id and not email:
                raise ValidationError("Either user_id or email must be provided")
            if user_id is not None and user_id <= 0:
                raise ValidationError("user_id must be positive", field="user_id")
            if email:
                validate_email(email)

            user_data: Dict[str, Any] = {}
            if user_id is not None:
                user_data["user_id"] = user_id
            if email:
                user_data["email"] = email
            if first_name:
                user_data["first_name"] = first_name
            if last_name:
                user_data["last_name"] = last_name

            return await _get_client().add_user_to_group(group_id, user_data)
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error adding user to group {group_id}: {e}")
            raise

    @mcp.tool()
    async def remove_user_from_group(group_id: int, user_id: int) -> Dict[str, Any]:
        """Remove a user from a group. User must have zero balance."""
        try:
            return await _get_client().remove_user_from_group(group_id, user_id)
        except Exception as e:
            logger.error(f"Error removing user {user_id} from group {group_id}: {e}")
            raise


# ============================================================================
# Friend Tools
# ============================================================================

def register_friend_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_friends() -> Dict[str, Any]:
        """Get all friends for the current user."""
        try:
            return await _get_client().get_friends()
        except Exception as e:
            logger.error(f"Error getting friends: {e}")
            raise

    @mcp.tool()
    async def get_friend(user_id: int) -> Dict[str, Any]:
        """Get detailed information about a specific friend."""
        try:
            return await _get_client().get_friend(user_id)
        except Exception as e:
            logger.error(f"Error getting friend {user_id}: {e}")
            raise


# ============================================================================
# Resolution Tools
# ============================================================================

def register_resolution_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def resolve_friend(query: str, threshold: int = 70) -> List[Dict[str, Any]]:
        """Resolve a natural language friend reference to user ID(s) using fuzzy matching.

        Args:
            query: Friend name or partial name (e.g., "John", "john smith")
            threshold: Minimum match score 0-100 (default: 70)
        """
        try:
            validate_required(query, "query")
            validate_range(threshold, "threshold", min_val=0, max_val=100)
            matches = await _get_resolver().resolve_friend(query, threshold)
            return [
                {"id": m.id, "name": m.name, "match_score": m.match_score, "additional_info": m.additional_info}
                for m in matches
            ]
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error resolving friend '{query}': {e}")
            raise

    @mcp.tool()
    async def resolve_group(query: str, threshold: int = 70) -> List[Dict[str, Any]]:
        """Resolve a natural language group reference to group ID(s) using fuzzy matching.

        Args:
            query: Group name or partial name (e.g., "roommates", "paris trip")
            threshold: Minimum match score 0-100 (default: 70)
        """
        try:
            validate_required(query, "query")
            validate_range(threshold, "threshold", min_val=0, max_val=100)
            matches = await _get_resolver().resolve_group(query, threshold)
            return [
                {"id": m.id, "name": m.name, "match_score": m.match_score, "additional_info": m.additional_info}
                for m in matches
            ]
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error resolving group '{query}': {e}")
            raise

    @mcp.tool()
    async def resolve_category(query: str, threshold: int = 70) -> List[Dict[str, Any]]:
        """Resolve a natural language category reference to category ID(s) using fuzzy matching.

        Args:
            query: Category name (e.g., "food", "groceries", "utilities")
            threshold: Minimum match score 0-100 (default: 70)
        """
        try:
            validate_required(query, "query")
            validate_range(threshold, "threshold", min_val=0, max_val=100)
            matches = await _get_resolver().resolve_category(query, threshold)
            return [
                {"id": m.id, "name": m.name, "match_score": m.match_score, "additional_info": m.additional_info}
                for m in matches
            ]
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error resolving category '{query}': {e}")
            raise


# ============================================================================
# Comment Tools
# ============================================================================

def register_comment_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def create_comment(expense_id: int, content: str) -> Dict[str, Any]:
        """Create a comment on an expense."""
        try:
            validate_required(expense_id, "expense_id")
            if expense_id <= 0:
                raise ValidationError("expense_id must be positive", field="expense_id")
            validate_required(content, "content")
            return await _get_client().create_comment(expense_id, content)
        except (ValidationError, RateLimitError):
            raise
        except Exception as e:
            logger.error(f"Error creating comment on expense {expense_id}: {e}")
            raise

    @mcp.tool()
    async def get_comments(expense_id: int) -> Dict[str, Any]:
        """Get all comments for an expense."""
        try:
            return await _get_client().get_comments(expense_id)
        except Exception as e:
            logger.error(f"Error getting comments for expense {expense_id}: {e}")
            raise

    @mcp.tool()
    async def delete_comment(comment_id: int) -> Dict[str, Any]:
        """Delete a comment. You can only delete your own comments."""
        try:
            return await _get_client().delete_comment(comment_id)
        except Exception as e:
            logger.error(f"Error deleting comment {comment_id}: {e}")
            raise


# ============================================================================
# Utility Tools
# ============================================================================

def register_utility_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    async def get_categories() -> Dict[str, Any]:
        """Get all supported expense categories and subcategories."""
        try:
            return await _get_client().get_categories()
        except Exception as e:
            logger.error(f"Error getting categories: {e}")
            raise

    @mcp.tool()
    async def get_currencies() -> Dict[str, Any]:
        """Get all supported currency codes."""
        try:
            return await _get_client().get_currencies()
        except Exception as e:
            logger.error(f"Error getting currencies: {e}")
            raise


# ============================================================================
# Arithmetic Tools
# ============================================================================

def register_arithmetic_tools(mcp: FastMCP) -> None:

    @mcp.tool()
    def add(numbers: List[float], decimal_places: int = 2) -> Dict[str, Any]:
        """Add multiple numbers together with proper decimal rounding.

        Examples: add([12.50, 8.75, 15.00]) = 36.25
        """
        if not numbers:
            raise ValueError("numbers list cannot be empty")
        result = round(sum(numbers), decimal_places)
        return {"result": result, "result_formatted": f"{result:.{decimal_places}f}", "operands": numbers, "operation": "addition"}

    @mcp.tool()
    def subtract(numbers: List[float], decimal_places: int = 2) -> Dict[str, Any]:
        """Subtract numbers sequentially: first - second - third - …"""
        if len(numbers) < 2:
            raise ValueError("subtract requires at least 2 numbers")
        result = numbers[0]
        for n in numbers[1:]:
            result -= n
        result = round(result, decimal_places)
        return {"result": result, "result_formatted": f"{result:.{decimal_places}f}", "operands": numbers, "operation": "subtraction"}

    @mcp.tool()
    def multiply(numbers: List[float], decimal_places: int = 2) -> Dict[str, Any]:
        """Multiply multiple numbers together."""
        if len(numbers) < 2:
            raise ValueError("multiply requires at least 2 numbers")
        result = numbers[0]
        for n in numbers[1:]:
            result *= n
        result = round(result, decimal_places)
        return {"result": result, "result_formatted": f"{result:.{decimal_places}f}", "operands": numbers, "operation": "multiplication"}

    @mcp.tool()
    def divide(numbers: List[float], decimal_places: int = 2) -> Dict[str, Any]:
        """Divide numbers sequentially: first / second / third / …"""
        if len(numbers) < 2:
            raise ValueError("divide requires at least 2 numbers")
        result = numbers[0]
        for i, n in enumerate(numbers[1:], 1):
            if n == 0:
                raise ValueError(f"Cannot divide by zero (position {i})")
            result /= n
        result = round(result, decimal_places)
        return {"result": result, "result_formatted": f"{result:.{decimal_places}f}", "operands": numbers, "operation": "division"}

    @mcp.tool()
    def modulo(a: float, b: float, decimal_places: int = 2) -> Dict[str, Any]:
        """Calculate the remainder of a / b."""
        if b == 0:
            raise ValueError("Cannot calculate modulo with zero divisor")
        result = round(a % b, decimal_places)
        return {"result": result, "result_formatted": f"{result:.{decimal_places}f}", "operands": [a, b], "operation": "modulo"}
