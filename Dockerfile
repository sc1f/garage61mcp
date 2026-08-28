# Garage61 MCP server, HTTP transport.
# Same image runs anywhere a container runs (App Runner, Fly, a VPS):
#   docker build -t garage61-mcp .
#   docker run -p 8080:8080 garage61-mcp
# No secrets baked in: callers bring their own Garage61 token per request.
FROM python:3.12-slim

WORKDIR /app
COPY pyproject.toml setup.py README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

ENV HOST=0.0.0.0 \
    PORT=8080

EXPOSE 8080

# Unprivileged user; nothing here needs root.
RUN useradd -r -u 10001 mcp
USER mcp

CMD ["garage61-mcp-http"]
