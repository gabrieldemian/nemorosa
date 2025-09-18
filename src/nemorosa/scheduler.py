"""Scheduler module for nemorosa."""

import re
from datetime import datetime
from enum import Enum
from typing import Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from . import config, db, logger
from .core import NemorosaCore


class JobType(Enum):
    """Job type enumeration."""

    SEARCH = "search"
    CLEANUP = "cleanup"


class JobManager:
    """Job manager for handling scheduled tasks."""

    def __init__(self):
        """Initialize job manager."""
        self.scheduler = AsyncIOScheduler()
        self.logger = logger.get_logger()
        self.job_log_db = db.get_database()
        self._setup_job_log_table()

    def _setup_job_log_table(self):
        """Set up job log table in database."""
        try:
            # Create job_log table if it doesn't exist
            self.job_log_db.execute(
                """
                CREATE TABLE IF NOT EXISTS job_log (
                    job_name TEXT PRIMARY KEY,
                    last_run INTEGER,
                    next_run INTEGER,
                    run_count INTEGER DEFAULT 0
                )
                """
            )
            self.job_log_db.commit()
        except Exception as e:
            self.logger.error(f"Failed to setup job_log table: {e}")

    async def get_job_last_run(self, job_name: str) -> int | None:
        """Get last run timestamp for a job.

        Args:
            job_name: Name of the job.

        Returns:
            Last run timestamp in seconds since epoch, or None if never run.
        """
        try:
            cursor = self.job_log_db.execute("SELECT last_run FROM job_log WHERE job_name = ?", (job_name,))
            result = cursor.fetchone()
            return result[0] if result else None
        except Exception as e:
            self.logger.error(f"Failed to get last run for job {job_name}: {e}")
            return None

    async def update_job_run(self, job_name: str, last_run: int, next_run: int | None = None):
        """Update job run information.

        Args:
            job_name: Name of the job.
            last_run: Last run timestamp in seconds since epoch.
            next_run: Next run timestamp in seconds since epoch, or None.
        """
        try:
            self.job_log_db.execute(
                """
                INSERT OR REPLACE INTO job_log (job_name, last_run, next_run, run_count)
                VALUES (?, ?, ?, COALESCE((SELECT run_count FROM job_log WHERE job_name = ?), 0) + 1)
                """,
                (job_name, last_run, next_run, job_name),
            )
            self.job_log_db.commit()
        except Exception as e:
            self.logger.error(f"Failed to update job run for {job_name}: {e}")

    async def get_job_run_count(self, job_name: str) -> int:
        """Get run count for a job.

        Args:
            job_name: Name of the job.

        Returns:
            Number of times the job has run.
        """
        try:
            cursor = self.job_log_db.execute("SELECT run_count FROM job_log WHERE job_name = ?", (job_name,))
            result = cursor.fetchone()
            return result[0] if result else 0
        except Exception as e:
            self.logger.error(f"Failed to get run count for job {job_name}: {e}")
            return 0

    def start_scheduler(self, target_apis: list, torrent_client: Any):
        """Start the scheduler with configured jobs.

        Args:
            target_apis: List of target API connections.
            torrent_client: Torrent client instance.
        """
        self.target_apis = target_apis
        self.torrent_client = torrent_client

        # Add search job if configured
        if config.cfg.server.search_cadence:
            self._add_search_job()

        # Add cleanup job
        self._add_cleanup_job()

        # Start scheduler
        self.scheduler.start()
        self.logger.info("Scheduler started successfully")

    def _add_search_job(self):
        """Add search job to scheduler."""
        try:
            from apscheduler.triggers.interval import IntervalTrigger

            # Parse cadence string (e.g., "1 day", "6 hours", "30 minutes")
            cadence = config.cfg.server.search_cadence
            interval = self._parse_cadence(cadence)

            self.scheduler.add_job(
                self._run_search_job,
                trigger=IntervalTrigger(**interval),
                id=JobType.SEARCH.value,
                name="Search Job",
                max_instances=1,
                replace_existing=True,
            )
            self.logger.info(f"Added search job with cadence: {cadence}")
        except Exception as e:
            self.logger.error(f"Failed to add search job: {e}")

    def _add_cleanup_job(self):
        """Add cleanup job to scheduler."""
        try:
            from apscheduler.triggers.interval import IntervalTrigger

            # Parse cadence string
            cadence = config.cfg.server.cleanup_cadence
            interval = self._parse_cadence(cadence)

            self.scheduler.add_job(
                self._run_cleanup_job,
                trigger=IntervalTrigger(**interval),
                id=JobType.CLEANUP.value,
                name="Cleanup Job",
                max_instances=1,
                replace_existing=True,
            )
            self.logger.info(f"Added cleanup job with cadence: {cadence}")
        except Exception as e:
            self.logger.error(f"Failed to add cleanup job: {e}")

    def _parse_cadence(self, cadence: str) -> dict[str, int]:
        """Parse cadence string into interval parameters.

        Args:
            cadence: Cadence string (e.g., "1 day", "6 hours", "30 minutes").

        Returns:
            Dictionary with interval parameters.

        Raises:
            ValueError: If cadence string is invalid.
        """
        # Parse patterns like "1 day", "6 hours", "30 minutes", "2 weeks"
        patterns = [
            (r"(\d+)\s*weeks?", {"weeks": 1}),
            (r"(\d+)\s*days?", {"days": 1}),
            (r"(\d+)\s*hours?", {"hours": 1}),
            (r"(\d+)\s*minutes?", {"minutes": 1}),
            (r"(\d+)\s*seconds?", {"seconds": 1}),
        ]

        for pattern, unit in patterns:
            match = re.match(pattern, cadence.lower().strip())
            if match:
                value = int(match.group(1))
                return {k: v * value for k, v in unit.items()}

        raise ValueError(f"Invalid cadence format: {cadence}")

    async def _run_search_job(self):
        """Run search job."""
        job_name = JobType.SEARCH.value
        self.logger.info(f"Starting {job_name} job")

        try:
            # Record job start
            start_time = int(datetime.now().timestamp())
            await self.update_job_run(job_name, start_time)

            # Run the actual search process
            processor = NemorosaCore(self.torrent_client, self.target_apis)
            processor.process_torrents()

            # Record successful completion
            end_time = int(datetime.now().timestamp())
            self.logger.info(f"Completed {job_name} job in {end_time - start_time} seconds")

        except Exception as e:
            self.logger.error(f"Error in {job_name} job: {e}")

    async def _run_cleanup_job(self):
        """Run cleanup job."""
        job_name = JobType.CLEANUP.value
        self.logger.info(f"Starting {job_name} job")

        try:
            # Record job start
            start_time = int(datetime.now().timestamp())
            await self.update_job_run(job_name, start_time)

            # Run cleanup process
            processor = NemorosaCore(self.torrent_client, self.target_apis)
            processor.retry_undownloaded_torrents()

            # Record successful completion
            end_time = int(datetime.now().timestamp())
            self.logger.info(f"Completed {job_name} job in {end_time - start_time} seconds")

        except Exception as e:
            self.logger.error(f"Error in {job_name} job: {e}")

    async def trigger_job_early(self, job_type: JobType) -> dict[str, Any]:
        """Trigger a job to run early.

        Args:
            job_type: Type of job to trigger.

        Returns:
            Dictionary with trigger result.
        """
        job_name = job_type.value
        self.logger.info(f"Triggering {job_name} job early")

        try:
            # Check if job is currently running
            running_jobs = [job.id for job in self.scheduler.get_jobs() if job.next_run_time]
            if job_name in running_jobs:
                return {
                    "status": "error",
                    "message": f"Job {job_name} is already running",
                    "job_name": job_name,
                }

            # Trigger the job
            if job_type == JobType.SEARCH:
                await self._run_search_job()
            elif job_type == JobType.CLEANUP:
                await self._run_cleanup_job()

            # For search jobs, delay the next scheduled run
            if job_type == JobType.SEARCH:
                # Get the job and modify its next run time
                job = self.scheduler.get_job(job_name)
                if job:
                    # Calculate double cadence delay
                    # This is a simplified approach - in practice, you'd need to
                    # modify the trigger or reschedule the job
                    pass

            return {
                "status": "success",
                "message": f"Job {job_name} triggered successfully",
                "job_name": job_name,
            }

        except Exception as e:
            self.logger.error(f"Error triggering {job_name} job: {e}")
            return {
                "status": "error",
                "message": f"Error triggering job: {str(e)}",
                "job_name": job_name,
            }

    def get_job_status(self, job_type: JobType) -> dict[str, Any]:
        """Get status of a job.

        Args:
            job_type: Type of job to get status for.

        Returns:
            Dictionary with job status.
        """
        job_name = job_type.value
        job = self.scheduler.get_job(job_name)

        if not job:
            return {
                "status": "not_found",
                "message": f"Job {job_name} not found",
                "job_name": job_name,
            }

        return {
            "status": "active",
            "job_name": job_name,
            "next_run": job.next_run_time.isoformat() if job.next_run_time else None,
            "last_run": None,  # Would need to get from database
        }

    def stop_scheduler(self):
        """Stop the scheduler."""
        self.scheduler.shutdown()
        self.logger.info("Scheduler stopped")


# Global job manager instance
job_manager: JobManager | None = None


def get_job_manager() -> JobManager:
    """Get global job manager instance.

    Returns:
        JobManager instance.
    """
    global job_manager
    if job_manager is None:
        job_manager = JobManager()
    return job_manager
