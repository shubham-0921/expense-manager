FROM python:3.12-slim AS builder

WORKDIR /app

COPY pyproject.toml ./
COPY src/ src/

RUN pip install --no-cache-dir .


FROM python:3.12-slim

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin/splitwise-mcp-server /usr/local/bin/splitwise-mcp-server

RUN useradd --create-home appuser && mkdir -p /data && chown appuser:appuser /data
USER appuser

ENV MCP_TRANSPORT=streamable-http
ENV MCP_HOST=0.0.0.0
ENV MCP_PORT=8000
ENV TOKEN_DB_PATH=/data/tokens.db

VOLUME ["/data"]
EXPOSE 8000

ENTRYPOINT ["python", "-m", "splitwise_mcp_server"]
