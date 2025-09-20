"""
Database operation module - replacing JSON file storage.
Provides SQLite storage functionality for torrent scan history, result mapping, URL records and other data.
"""

import json
import os
import sqlite3
import threading
from contextlib import contextmanager, suppress
from typing import Any

from . import config


class TorrentDatabase:
    """Torrent database management class."""

    def __init__(self, db_path: str | None = None):
        """Initialize database connection.

        Args:
            db_path: Database file path, if None uses config directory.
        """
        if db_path is None:
            config_dir = config.get_config_dir()
            db_path = os.path.join(config_dir, "nemorosa.db")

        self.db_path = db_path
        self._local = threading.local()

        # Ensure database directory exists
        os.makedirs(os.path.dirname(db_path), exist_ok=True)

        # Initialize database tables
        self._init_database()

    @property
    def connection(self):
        """Get thread-local database connection.

        Returns:
            sqlite3.Connection: Thread-local database connection.
        """
        if not hasattr(self._local, "connection"):
            self._local.connection = sqlite3.connect(self.db_path)
            self._local.connection.row_factory = sqlite3.Row
        return self._local.connection

    @contextmanager
    def transaction(self):
        """Database transaction context manager.

        Yields:
            sqlite3.Connection: Database connection within transaction.
        """
        conn = self.connection
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_database(self):
        """Initialize database table structure."""
        with self.transaction() as conn:
            # Scan results table - merge original scan_history, torrent_mapping, torrent_results
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scan_results (
                    file_hash TEXT NOT NULL,
                    torrent_name TEXT,
                    site_host TEXT DEFAULT 'default',
                    torrent_id TEXT,
                    scanned_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (file_hash, site_host)
                )
            """)

            # Undownloaded torrents table - record detailed information of undownloaded torrents
            conn.execute("""
                CREATE TABLE IF NOT EXISTS undownloaded_torrents (
                    torrent_id TEXT NOT NULL,
                    site_host TEXT DEFAULT 'default',
                    download_dir TEXT,
                    local_torrent_name TEXT,
                    rename_map TEXT,  -- JSON format storage for rename mapping
                    added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (torrent_id, site_host)
                )
            """)

            # Job log table - for scheduler job tracking
            conn.execute("""
                CREATE TABLE IF NOT EXISTS job_log (
                    job_name TEXT PRIMARY KEY,
                    last_run TIMESTAMP,
                    next_run TIMESTAMP,
                    run_count INTEGER DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

            # Create indexes to improve query performance
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_results_time ON scan_results(scanned_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_scan_results_torrent_id ON scan_results(torrent_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_undownloaded_time ON undownloaded_torrents(added_at)")

    # ================== Scan results related methods ==================

    def add_scan_result(
        self, file_hash: str, torrent_name: str = None, torrent_id: str = None, site_host: str = "default"
    ):
        """Add scan result record.

        Args:
            file_hash (str): File hash.
            torrent_name (str, optional): Torrent name.
            torrent_id (str, optional): Torrent ID (can be None to indicate not found).
            site_host (str): Site hostname.
        """
        with self.transaction() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO scan_results "
                "(file_hash, torrent_name, torrent_id, site_host) VALUES (?, ?, ?, ?)",
                (file_hash, torrent_name, torrent_id, site_host),
            )

    def is_hash_scanned(self, file_hash: str, site_host: str = None) -> bool:
        """Check if specified file hash has been scanned.

        Args:
            file_hash (str): File hash.
            site_host (str, optional): Site hostname, if None checks all sites.

        Returns:
            bool: True if scanned, False otherwise.
        """
        with self.connection as conn:
            if site_host is None:
                # Check all sites
                cursor = conn.execute("SELECT 1 FROM scan_results WHERE file_hash = ? LIMIT 1", (file_hash,))
            else:
                # Check specific site
                cursor = conn.execute(
                    "SELECT 1 FROM scan_results WHERE file_hash = ? AND site_host = ? LIMIT 1",
                    (file_hash, site_host),
                )
            return cursor.fetchone() is not None

    # ================== Undownloaded torrents related methods ==================

    def load_undownloaded_torrents(self, site_host: str = "default") -> dict[str, dict[str, Any]]:
        """Load undownloaded torrent information for specified site.

        Args:
            site_host (str): Site hostname, defaults to 'default'.

        Returns:
            dict[str, dict[str, Any]]: Mapping dictionary from torrent ID to detailed information.
        """
        with self.connection as conn:
            cursor = conn.execute(
                "SELECT torrent_id, download_dir, local_torrent_name, rename_map "
                "FROM undownloaded_torrents WHERE site_host = ?",
                (site_host,),
            )
            result = {}
            for row in cursor.fetchall():
                result[row["torrent_id"]] = {
                    "download_dir": row["download_dir"],
                    "local_torrent_name": row["local_torrent_name"],
                    "rename_map": json.loads(row["rename_map"]) if row["rename_map"] else {},
                }
            return result

    def add_undownloaded_torrent(self, torrent_id: str, torrent_info: dict, site_host: str = "default"):
        """Add undownloaded torrent information.

        Args:
            torrent_id (str): Torrent ID.
            torrent_info (dict): Dictionary containing download_dir, local_torrent_name, rename_map.
            site_host (str): Site hostname.
        """
        with self.transaction() as conn:
            conn.execute(
                """INSERT OR REPLACE INTO undownloaded_torrents 
                   (torrent_id, site_host, download_dir, local_torrent_name, rename_map) 
                   VALUES (?, ?, ?, ?, ?)""",
                (
                    torrent_id,
                    site_host,
                    torrent_info.get("download_dir"),
                    torrent_info.get("local_torrent_name"),
                    json.dumps(torrent_info.get("rename_map", {}), ensure_ascii=False),
                ),
            )

    def remove_undownloaded_torrent(self, torrent_id: str, site_host: str = "default"):
        """Remove specified torrent from undownloaded torrents table.

        Args:
            torrent_id (str): Torrent ID.
            site_host (str): Site hostname.
        """
        with self.transaction() as conn:
            conn.execute(
                "DELETE FROM undownloaded_torrents WHERE torrent_id = ? AND site_host = ?",
                (torrent_id, site_host),
            )

    # ================== Job log related methods ==================

    def get_job_last_run(self, job_name: str) -> int | None:
        """Get last run timestamp for a job.

        Args:
            job_name (str): Name of the job.

        Returns:
            int | None: Last run timestamp in seconds since epoch, or None if never run.
        """
        with self.connection as conn:
            cursor = conn.execute("SELECT last_run FROM job_log WHERE job_name = ?", (job_name,))
            result = cursor.fetchone()
            return result[0] if result else None

    def update_job_run(self, job_name: str, last_run: int, next_run: int | None = None):
        """Update job run information.

        Args:
            job_name (str): Name of the job.
            last_run (int): Last run timestamp in seconds since epoch.
            next_run (int | None): Next run timestamp in seconds since epoch, or None.
        """
        with self.transaction() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO job_log (job_name, last_run, next_run, run_count)
                VALUES (?, ?, ?, COALESCE((SELECT run_count FROM job_log WHERE job_name = ?), 0) + 1)
                """,
                (job_name, last_run, next_run, job_name),
            )

    def get_job_run_count(self, job_name: str) -> int:
        """Get run count for a job.

        Args:
            job_name (str): Name of the job.

        Returns:
            int: Number of times the job has run.
        """
        with self.connection as conn:
            cursor = conn.execute("SELECT run_count FROM job_log WHERE job_name = ?", (job_name,))
            result = cursor.fetchone()
            return result[0] if result else 0

    def close(self):
        """Close database connection."""
        if hasattr(self._local, "connection"):
            with suppress(Exception):
                self._local.connection.close()
            delattr(self._local, "connection")


# Global database instance
_db_instance = None
_db_lock = threading.Lock()


def get_database(db_path: str | None = None) -> TorrentDatabase:
    """Get global database instance (singleton pattern).

    Args:
        db_path (str, optional): Database file path, if None uses nemorosa.db in config directory.

    Returns:
        TorrentDatabase: Database instance.
    """
    global _db_instance
    with _db_lock:
        if _db_instance is None:
            if db_path is None:
                # Use database file in config directory
                config_dir = config.get_config_dir()
                db_path = os.path.join(config_dir, "nemorosa.db")
            _db_instance = TorrentDatabase(db_path)
        return _db_instance
