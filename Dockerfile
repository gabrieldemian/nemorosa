# Use official uv image with Python 3.13
FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

# Set working directory
WORKDIR /app

# Set environment variables for uv optimization
ENV UV_CACHE_DIR=/opt/uv-cache
ENV UV_PYTHON_CACHE_DIR=/opt/uv-cache/python
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy dependency files first for better layer caching
COPY pyproject.toml uv.lock ./

# Install dependencies
RUN --mount=type=cache,target=/opt/uv-cache \
    uv sync --locked --no-install-project

# Copy source code
COPY . .

# Install the project in non-editable mode for production
RUN --mount=type=cache,target=/opt/uv-cache \
    uv sync --locked --no-editable --no-dev

# Create data directories for configuration and database
RUN mkdir -p /app/data

# Set environment variables
# Override platformdirs to use /app/data for configuration
ENV XDG_CONFIG_HOME=/app/data
ENV XDG_DATA_HOME=/app/data

# Expose port for web interface
EXPOSE 8256

# Use uv to run the application
ENTRYPOINT ["uv", "run", "--project", "/app", "--no-sync", "nemorosa"]
