"""Web server module for nemorosa."""

import base64
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel

from . import config, logger, scheduler
from .core import NemorosaCore
from .torrent_client import create_torrent_client


class WebhookResponse(BaseModel):
    """Webhook response model."""

    status: str
    message: str
    data: dict[str, Any] | None = None


class JobResponse(BaseModel):
    """Job response model."""

    status: str
    message: str
    job_name: str | None = None
    next_run: str | None = None
    last_run: str | None = None


class AnnounceRequest(BaseModel):
    """Announce request model."""

    name: str
    link: str
    torrentdata: str  # Base64 encoded torrent data


# Global variables
app_logger: logging.Logger | None = None
target_apis: list | None = None
job_manager: scheduler.JobManager | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan event handler for FastAPI app."""
    global job_manager, app_logger, target_apis

    # Startup
    if job_manager and target_apis:
        # Create torrent client for scheduler
        torrent_client = create_torrent_client(config.cfg.downloader.client)

        # Start scheduler
        await job_manager.start_scheduler(target_apis, torrent_client)
        app_logger.info("Scheduler started with configured jobs")

    yield

    # Shutdown
    if job_manager:
        job_manager.stop_scheduler()
        app_logger.info("Scheduler stopped")


# Create FastAPI app
app = FastAPI(
    title="Nemorosa Web Server",
    description="Music torrent cross-seeding tool with automatic file mapping and seamless injection",
    version="0.0.1",
    lifespan=lifespan,
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
        "endpoints": {
            "webhook": "/api/webhook",
            "announce": "/api/announce",
            "job": "/api/job",
            "docs": "/docs",
        },
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

        # Use the provided target_apis
        current_target_apis = globals()["target_apis"]

        # Process the torrent
        processor = NemorosaCore(torrent_client, current_target_apis)
        result = processor.process_single_torrent(infoHash)

        return WebhookResponse(status="success", message="Torrent processed successfully", data=result)

    except HTTPException:
        raise
    except Exception as e:
        if app_logger:
            app_logger.error(f"Error processing torrent {infoHash}: {str(e)}")

        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}") from e


@app.post("/api/announce", response_model=WebhookResponse)
async def announce(
    request: AnnounceRequest,
    _: bool = Depends(verify_api_key),
):
    """Process torrent announce from tracker.

    This endpoint receives announce notifications with torrent data
    in JSON format from external systems like autobrr.

    Args:
        request: Announce request containing torrent name, link, and data
        _: API key verification

    Returns:
        WebhookResponse: Processing result
    """
    # Validate request data
    if not request.name or not request.name.strip():
        raise HTTPException(status_code=400, detail="Torrent name cannot be empty")

    if not request.link or not request.link.strip():
        raise HTTPException(status_code=400, detail="Torrent link cannot be empty")

    if not request.torrentdata or not request.torrentdata.strip():
        raise HTTPException(status_code=400, detail="Torrent data cannot be empty")

    try:
        try:
            torrent_bytes = base64.b64decode(request.torrentdata)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Invalid base64 torrent data: {str(e)}") from e

        # Create torrent client using global config
        torrent_client = create_torrent_client(config.cfg.downloader.client)

        # Use the provided target_apis
        current_target_apis = globals()["target_apis"]

        # Log the announce
        if app_logger:
            app_logger.info(f"Received announce for torrent: {request.name} from {request.link}")

        # Process the torrent for cross-seeding using the reverse announce function
        processor = NemorosaCore(torrent_client, current_target_apis)
        result = processor.process_reverse_announce_torrent(
            torrent_name=request.name,
            torrent_link=request.link,
            torrent_data=torrent_bytes,
        )

        return WebhookResponse(
            status="success",
            message="Torrent announce processed successfully",
            data={
                "name": request.name,
                "link": request.link,
                "result": result,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        if app_logger:
            app_logger.error(f"Error processing torrent announce {request.name}: {str(e)}")

        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}") from e


@app.post("/api/job", response_model=JobResponse)
async def trigger_job(
    job_type: str = Query(..., description="Job type: search or cleanup"),
    _: bool = Depends(verify_api_key),
):
    """Trigger a job to run early.

    Args:
        job_type: Type of job to trigger (search, cleanup)
        _: API key verification

    Returns:
        JobResponse: Job trigger result
    """
    # Validate job type
    valid_job_types = ["search", "cleanup"]
    if job_type not in valid_job_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid job type '{job_type}'. Must be one of: {valid_job_types}",
        )

    try:
        # Get job manager
        job_mgr = scheduler.get_job_manager()

        # Convert string to JobType enum
        job_type_enum = scheduler.JobType(job_type)

        # Trigger the job
        result = await job_mgr.trigger_job_early(job_type_enum)

        # Map internal status to HTTP status codes
        if result["status"] == "not_found":
            raise HTTPException(status_code=404, detail=result["message"])
        elif result["status"] == "conflict":
            raise HTTPException(status_code=409, detail=result["message"])
        elif result["status"] == "error":
            raise HTTPException(status_code=500, detail=result["message"])

        return JobResponse(
            status=result["status"],
            message=result["message"],
            job_name=result.get("job_name"),
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid job type: {str(e)}") from e
    except Exception as e:
        if app_logger:
            app_logger.error(f"Error triggering job {job_type}: {str(e)}")

        raise HTTPException(status_code=500, detail=f"Internal server error: {str(e)}") from e


@app.get("/api/job/{job_type}", response_model=JobResponse)
async def get_job_status(
    job_type: str,
    _: bool = Depends(verify_api_key),
):
    """Get job status.

    Args:
        job_type: Type of job to get status for (search, rss, cleanup)
        _: API key verification

    Returns:
        JobResponse: Job status information
    """
    # Validate job type
    valid_job_types = ["search", "cleanup"]
    if job_type not in valid_job_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid job type '{job_type}'. Must be one of: {valid_job_types}",
        )

    try:
        # Get job manager
        job_mgr = scheduler.get_job_manager()

        # Convert string to JobType enum
        job_type_enum = scheduler.JobType(job_type)

        # Get job status
        result = job_mgr.get_job_status(job_type_enum)

        return JobResponse(
            status=result["status"],
            message=result["message"],
            job_name=result.get("job_name"),
            next_run=result.get("next_run"),
            last_run=result.get("last_run"),
        )

    except ValueError as e:
        raise HTTPException(status_code=400, detail=f"Invalid job type: {str(e)}") from e
    except Exception as e:
        if app_logger:
            app_logger.error(f"Error getting job status for {job_type}: {str(e)}")

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
    global app_logger, job_manager

    # Use config values if not provided
    if host is None:
        host = config.cfg.server.host
    if port is None:
        port = config.cfg.server.port

    # Set global variables
    globals()["target_apis"] = target_apis
    job_manager = scheduler.get_job_manager()

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

    # Check if scheduler should be initialized
    if any(
        [
            config.cfg.server.search_cadence,
            config.cfg.server.cleanup_cadence,
        ]
    ):
        app_logger.info("Scheduler will be started with configured jobs")
    else:
        app_logger.info("No scheduled jobs configured")

    # Import uvicorn here to avoid import issues
    import uvicorn

    # Run server
    uvicorn.run("nemorosa.webserver:app", host=host, port=port, log_level=log_level, reload=False)
