"""Web server module for nemorosa."""

import logging
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from . import config, logger
from .core import process_single_torrent
from .torrent_client import create_torrent_client


class WebhookResponse(BaseModel):
    """Webhook response model."""

    status: str
    message: str
    data: dict[str, Any] | None = None


# Global variables
app_logger: logging.Logger | None = None
target_apis: list | None = None

# Create FastAPI app
app = FastAPI(
    title="Nemorosa Web Server",
    description="Music torrent cross-seeding tool with automatic file mapping and seamless injection",
    version="0.0.1",
)

# Security
security = HTTPBearer(auto_error=False)


def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify API key."""
    # Check if API key is configured in server config
    api_key = config.cfg.server.api_key
    if not api_key:
        # No API key configured, allow all requests
        return True

    if credentials.credentials != api_key:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return True


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log all requests."""
    if app_logger:
        app_logger.info(f"Incoming request: {request.method} {request.url}")

    response = await call_next(request)

    if app_logger:
        app_logger.info(f"Response: {response.status_code}")

    return response


@app.get("/")
async def root():
    """Root endpoint."""
    return {
        "message": "Nemorosa Web Server",
        "version": "0.0.1",
        "endpoints": {"webhook": "/api/webhook", "docs": "/docs"},
    }


@app.post("/api/webhook", response_model=WebhookResponse)
async def webhook(infoHash: str = Query(..., description="Torrent infohash"), _: bool = Depends(verify_api_key)):
    """Process a single torrent via webhook.

    Args:
        infoHash: Torrent infohash from URL parameter
        _: API key verification

    Returns:
        WebhookResponse: Processing result
    """
    # Validate infoHash is not empty
    if not infoHash or not infoHash.strip():
        raise HTTPException(status_code=400, detail="infoHash cannot be empty")

    try:
        # Create torrent client using global config
        torrent_client = create_torrent_client(config.cfg.downloader.client)
        if app_logger:
            torrent_client.set_logger(app_logger)

        # Use the provided target_apis
        current_target_apis = globals()["target_apis"]

        # Process the torrent
        result = process_single_torrent(
            torrent_client=torrent_client,
            target_apis=current_target_apis,
            infohash=infoHash,
        )

        return WebhookResponse(status="success", message="Torrent processed successfully", data=result)

    except HTTPException:
        raise
    except Exception as e:
        if app_logger:
            app_logger.error(f"Error processing torrent {infoHash}: {str(e)}")

        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}") from e


def run_webserver(
    host: str | None = None,
    port: int | None = None,
    log_level: str = "info",
    target_apis: list | None = None,
):
    """Run the web server.

    Args:
        host: Server host (if None, use config value)
        port: Server port (if None, use config value)
        log_level: Log level
        target_apis: List of target API connections
    """
    global app_logger

    # Use config values if not provided
    if host is None:
        host = config.cfg.server.host
    if port is None:
        port = config.cfg.server.port

    # Set global target_apis
    globals()["target_apis"] = target_apis

    # Set up logger
    app_logger = logger.generate_logger(log_level)

    # Log server startup
    display_host = host if host is not None else "all interfaces (IPv4/IPv6)"
    app_logger.info(f"Starting Nemorosa web server on {display_host}:{port}")
    app_logger.info(f"Using torrent client: {config.cfg.downloader.client}")
    app_logger.info(f"Target sites: {len(config.cfg.target_sites)}")

    # Check if API key is configured
    api_key = config.cfg.server.api_key
    if api_key:
        app_logger.info("API key authentication enabled")
    else:
        app_logger.info("API key authentication disabled")

    # Import uvicorn here to avoid import issues
    import uvicorn

    # Run server
    uvicorn.run("nemorosa.webserver:app", host=host, port=port, log_level=log_level, reload=False)
