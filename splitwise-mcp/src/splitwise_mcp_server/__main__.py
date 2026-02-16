"""Entry point for the Splitwise MCP Server."""

import asyncio
import os
import sys

from splitwise_mcp_server.server import create_server, token_store
from splitwise_mcp_server.user_context import set_access_token, reset_access_token


class TokenAuthMiddleware:
    """ASGI middleware that extracts ?token=<uuid> from MCP requests,
    looks up the real Splitwise access token in the TokenStore, and
    sets the ``current_access_token`` context variable so that every
    tool handler can call ``client_manager.get_client()`` without
    any extra arguments.

    Non-MCP routes (``/``, ``/authorize``, ``/callback``) are passed
    through without authentication.
    """

    PUBLIC_PREFIXES = ("/authorize", "/callback")

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            path = scope.get("path", "")

            # Let public OAuth pages through
            if path == "/" or path.startswith(self.PUBLIC_PREFIXES):
                await self.app(scope, receive, send)
                return

            # Extract token from query string
            from urllib.parse import parse_qs
            qs = parse_qs(scope.get("query_string", b"").decode())
            user_token = (qs.get("token") or [None])[0]

            if user_token:
                # Import here to avoid circular ref at module level
                import logging
                _logger = logging.getLogger("splitwise_mcp_server.middleware")
                from splitwise_mcp_server.server import token_store as ts
                _logger.info(f"Middleware: token_store={'exists' if ts else 'None'}, user_token={user_token[:8]}...")
                access_token = ts.get_access_token(user_token) if ts else None
                _logger.info(f"Middleware: access_token={'found' if access_token else 'NOT FOUND'}")
                if access_token:
                    set_access_token(access_token)
                    try:
                        await self.app(scope, receive, send)
                    finally:
                        reset_access_token()
                    return
                else:
                    _logger.warning(f"Middleware: UUID {user_token} not found in token store!")

            # No valid token — still allow the request through (the tool
            # handler will raise a clear error via _get_client()).
            await self.app(scope, receive, send)
            return

        # Non-HTTP scopes (lifespan, websocket) pass through
        await self.app(scope, receive, send)


def main():
    """Main entry point for the MCP server."""
    try:
        server = create_server()
        transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
        host = os.environ.get("MCP_HOST", "0.0.0.0")
        port = int(os.environ.get("MCP_PORT", "8000"))

        if transport == "streamable-http":
            from starlette.middleware import Middleware
            from starlette.middleware.cors import CORSMiddleware

            cors = Middleware(
                CORSMiddleware,
                allow_origins=["*"],
                allow_methods=["*"],
                allow_headers=["*"],
            )
            token_auth = Middleware(TokenAuthMiddleware)

            asyncio.run(server.run(
                transport="streamable-http",
                host=host,
                port=port,
                middleware=[cors, token_auth],
                stateless_http=True,
            ))
        else:
            asyncio.run(server.run())
    except KeyboardInterrupt:
        print("\nShutting down Splitwise MCP Server...")
        sys.exit(0)
    except Exception as e:
        print(f"Error starting server: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
