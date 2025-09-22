"""
Torrent Client Module

This module provides a unified interface for different torrent clients including
Transmission, qBittorrent, and Deluge.
"""

import base64
import json
import os
import posixpath
import re
import threading
import time
from abc import ABC, abstractmethod
from enum import Enum
from urllib.parse import parse_qsl, urlparse

import deluge_client
import msgspec
import qbittorrentapi
import torf
import transmission_rpc
from transmission_rpc.constants import RpcMethod

from . import config, filecompare, logger


class TorrentState(Enum):
    """Torrent download state enumeration."""

    UNKNOWN = "unknown"
    DOWNLOADING = "downloading"
    SEEDING = "seeding"
    PAUSED = "paused"
    STOPPED = "stopped"
    COMPLETED = "completed"
    CHECKING = "checking"
    ERROR = "error"
    QUEUED = "queued"
    STALLED = "stalled"
    MOVING = "moving"
    ALLOCATING = "allocating"
    METADATA_DOWNLOADING = "metadata_downloading"


# State mapping tables for different torrent clients
TRANSMISSION_STATE_MAPPING = {
    "stopped": TorrentState.STOPPED,
    "check pending": TorrentState.CHECKING,
    "checking": TorrentState.CHECKING,
    "download pending": TorrentState.QUEUED,
    "downloading": TorrentState.DOWNLOADING,
    "seed pending": TorrentState.QUEUED,
    "seeding": TorrentState.SEEDING,
}

QBITTORRENT_STATE_MAPPING = {
    "error": TorrentState.ERROR,
    "missingFiles": TorrentState.ERROR,
    "uploading": TorrentState.SEEDING,
    "pausedUP": TorrentState.PAUSED,
    "stoppedUP": TorrentState.PAUSED,
    "queuedUP": TorrentState.QUEUED,
    "stalledUP": TorrentState.SEEDING,
    "checkingUP": TorrentState.CHECKING,
    "forcedUP": TorrentState.SEEDING,
    "allocating": TorrentState.ALLOCATING,
    "downloading": TorrentState.DOWNLOADING,
    "metaDL": TorrentState.METADATA_DOWNLOADING,
    "forcedMetaDL": TorrentState.METADATA_DOWNLOADING,
    "pausedDL": TorrentState.PAUSED,
    "stoppedDL": TorrentState.PAUSED,
    "queuedDL": TorrentState.QUEUED,
    "forcedDL": TorrentState.DOWNLOADING,
    "stalledDL": TorrentState.DOWNLOADING,
    "checkingDL": TorrentState.CHECKING,
    "checkingResumeData": TorrentState.CHECKING,
    "moving": TorrentState.MOVING,
    "unknown": TorrentState.UNKNOWN,
}

DELUGE_STATE_MAPPING = {
    "Error": TorrentState.ERROR,
    "Paused": TorrentState.PAUSED,
    "Queued": TorrentState.QUEUED,
    "Checking": TorrentState.CHECKING,
    "Downloading": TorrentState.DOWNLOADING,
    "Downloading Metadata": TorrentState.METADATA_DOWNLOADING,
    "Finished": TorrentState.COMPLETED,
    "Seeding": TorrentState.SEEDING,
    "Allocating": TorrentState.ALLOCATING,
    "Moving": TorrentState.MOVING,
    "Active": TorrentState.SEEDING,
    "Inactive": TorrentState.PAUSED,
}


class TorrentConflictError(Exception):
    """Exception raised when torrent cannot coexist with local torrent due to source flag issues."""

    pass


class ClientTorrentFile(msgspec.Struct):
    """Represents a file within a torrent from torrent client."""

    name: str
    size: int
    progress: float  # File download progress (0.0 to 1.0)


class ClientTorrentInfo(msgspec.Struct):
    """Represents a torrent with all its information from torrent client."""

    id: str
    name: str
    hash: str
    progress: float
    total_size: int
    files: list[ClientTorrentFile]
    trackers: list[str]
    download_dir: str
    state: TorrentState = TorrentState.UNKNOWN  # Torrent state
    existing_target_trackers: list[str] = msgspec.field(default_factory=list)
    piece_progress: list[bool] = msgspec.field(default_factory=list)  # Piece download status

    @property
    def fdict(self) -> dict[str, int]:
        """Generate file dictionary mapping relative file path to file size.

        Returns:
            dict[str, int]: Dictionary mapping relative file path to file size.
        """
        return {posixpath.relpath(f.name, self.name): f.size for f in self.files}


class TorrentClient(ABC):
    """Abstract base class for torrent clients."""

    def __init__(self):
        self.logger = logger.get_logger()

    @abstractmethod
    def get_torrents(self) -> list[ClientTorrentInfo]:
        """Get all torrents from client.

        Returns:
            list[ClientTorrentInfo]: List of torrent information objects.
        """
        pass

    @abstractmethod
    def resume_torrent(self, torrent_id: str) -> bool:
        """Resume downloading a torrent.

        Args:
            torrent_id (str): Torrent ID or hash.

        Returns:
            bool: True if successful, False otherwise.
        """
        pass

    def get_single_torrent(self, infohash: str, target_trackers: list[str]) -> ClientTorrentInfo | None:
        """Get single torrent by infohash with existing trackers information.

        This method follows the same logic as get_filtered_torrents but for a single torrent.
        It finds the torrent by infohash and determines which target trackers this content
        already exists on by checking all torrents with the same content name.

        Args:
            infohash (str): Torrent infohash.
            target_trackers (list[str]): List of target tracker names.

        Returns:
            ClientTorrentInfo | None: Torrent information with existing_trackers, or None if not found.
        """
        try:
            # Get all torrents
            torrents = list(self.get_torrents())

            # Find torrent by infohash
            target_torrent = None
            for torrent in torrents:
                if torrent.hash == infohash:
                    target_torrent = torrent
                    break

            if not target_torrent:
                self.logger.debug(f"Torrent with infohash {infohash} not found in client torrent list")
                return None

            self.logger.debug(f"Found torrent: {target_torrent.name} ({infohash})")

            # Check if torrent meets basic conditions (same as get_filtered_torrents)
            check_trackers_list = config.cfg.global_config.check_trackers
            if check_trackers_list and not any(
                any(check_str in url for check_str in check_trackers_list) for url in target_torrent.trackers
            ):
                self.logger.debug(f"Torrent {target_torrent.name} filtered out: tracker not in check_trackers list")
                self.logger.debug(f"Torrent trackers: {target_torrent.trackers}")
                self.logger.debug(f"Required trackers: {check_trackers_list}")
                return None

            # Filter MP3 files (based on configuration)
            if config.cfg.global_config.exclude_mp3:
                has_mp3 = any(posixpath.splitext(file.name)[1].lower() == ".mp3" for file in target_torrent.files)
                if has_mp3:
                    self.logger.debug(
                        f"Torrent {target_torrent.name} filtered out: contains MP3 files (exclude_mp3=true)"
                    )
                    return None

            # Check if torrent contains music files (if check_music_only is enabled)
            if config.cfg.global_config.check_music_only:
                has_music = any(filecompare.is_music_file(file.name) for file in target_torrent.files)
                if not has_music:
                    self.logger.debug(
                        f"Torrent {target_torrent.name} filtered out: no music files found (check_music_only=true)"
                    )
                    file_extensions = [posixpath.splitext(f.name)[1].lower() for f in target_torrent.files]
                    self.logger.debug(f"File extensions in torrent: {file_extensions}")
                    return None

            # Get content name and find all torrents with the same content name
            content_name = target_torrent.name

            # Collect which target trackers this content already exists on
            # (by checking all torrents with the same content name)
            existing_trackers = set()
            for torrent in torrents:
                if torrent.name == content_name:
                    for tracker_url in torrent.trackers:
                        for target_tracker in target_trackers:
                            if target_tracker in tracker_url:
                                existing_trackers.add(target_tracker)

            # Return torrent info with existing_trackers
            return ClientTorrentInfo(
                id=target_torrent.id,
                name=target_torrent.name,
                hash=target_torrent.hash,
                progress=target_torrent.progress,
                total_size=target_torrent.total_size,
                files=target_torrent.files,
                trackers=target_torrent.trackers,
                download_dir=target_torrent.download_dir,
                state=target_torrent.state,
                existing_target_trackers=list(existing_trackers),
            )

        except Exception as e:
            self.logger.error("Error retrieving single torrent: %s", e)
            return None

    def get_filtered_torrents(self, target_trackers: list[str]) -> dict[str, ClientTorrentInfo]:
        """Get filtered torrent list.

        This method contains common filtering logic, derived classes only need to implement get_torrents().

        New logic:
        1. Group by torrent content (same name considered same content)
        2. Check which target trackers each content already exists on
        3. Only return content that doesn't exist on all target trackers

        Args:
            target_trackers (list[str]): List of target tracker names.

        Returns:
            dict[str, dict]: Dictionary mapping torrent name to torrent info.
        """
        try:
            # Get all torrents
            torrents = list(self.get_torrents())

            # Step 1: Group by content name, collect which trackers each content exists on
            content_tracker_mapping = {}  # {content_name: set(trackers)}
            valid_torrents: dict[str, ClientTorrentInfo] = {}  # Torrents that meet basic conditions

            for torrent in torrents:
                # Only process torrents that meet CHECK_TRACKERS conditions
                check_trackers_list = config.cfg.global_config.check_trackers
                if check_trackers_list and not any(
                    any(check_str in url for check_str in check_trackers_list) for url in torrent.trackers
                ):
                    continue

                # Filter MP3 files (based on configuration)
                if config.cfg.global_config.exclude_mp3:
                    has_mp3 = any(posixpath.splitext(file.name)[1].lower() == ".mp3" for file in torrent.files)
                    if has_mp3:
                        continue

                # Check if torrent contains music files (if check_music_only is enabled)
                if config.cfg.global_config.check_music_only:
                    has_music = any(filecompare.is_music_file(file.name) for file in torrent.files)
                    if not has_music:
                        continue

                content_name = torrent.name

                # Record which trackers this content exists on
                if content_name not in content_tracker_mapping:
                    content_tracker_mapping[content_name] = set()

                for tracker_url in torrent.trackers:
                    for target_tracker in target_trackers:
                        if target_tracker in tracker_url:
                            content_tracker_mapping[content_name].add(target_tracker)

                # Save torrent info (if duplicated, choose better version)
                if content_name not in valid_torrents:
                    valid_torrents[content_name] = torrent
                else:
                    # Choose version with fewer files or smaller size
                    existing = valid_torrents[content_name]
                    if len(torrent.files) < len(existing.files) or (
                        len(torrent.files) == len(existing.files) and torrent.total_size < existing.total_size
                    ):
                        valid_torrents[content_name] = torrent

            # Step 2: Filter out content that already exists on all target trackers
            filtered_torrents = {}
            target_tracker_set = set(target_trackers)

            for content_name, torrent in valid_torrents.items():
                existing_trackers = content_tracker_mapping.get(content_name, set())

                # If this content already exists on all target trackers, skip
                if target_tracker_set.issubset(existing_trackers):
                    self.logger.debug(
                        f"Skipping {content_name}: already exists on all target trackers {existing_trackers}"
                    )
                    continue

                # Otherwise include in results
                filtered_torrents[content_name] = ClientTorrentInfo(
                    id=torrent.id,
                    name=content_name,
                    hash=torrent.hash,
                    progress=torrent.progress,
                    total_size=torrent.total_size,
                    files=torrent.files,
                    trackers=torrent.trackers,
                    download_dir=torrent.download_dir,
                    state=torrent.state,
                    existing_target_trackers=list(existing_trackers),  # Record existing target trackers
                )

            return filtered_torrents

        except Exception as e:
            self.logger.error("Error retrieving torrents: %s", e)
            return {}

    def inject_torrent(
        self, torrent_data, download_dir: str, local_torrent_name: str, rename_map: dict, hash_match: bool
    ) -> bool:
        """Inject torrent into client (includes complete logic).

        Derived classes only need to implement specific client operation methods.

        Args:
            torrent_data: Torrent file data.
            download_dir (str): Download directory.
            local_torrent_name (str): Local torrent name.
            rename_map (dict): File rename mapping.
            hash_match (bool): Whether this is a hash match, if True, skip verification.

        Returns:
            bool: True if injection successful, False otherwise.
        """
        # Flag to track if rename map has been processed
        rename_map_processed = False

        # Add torrent to client
        try:
            torrent_id = self._add_torrent(torrent_data, download_dir, hash_match)
        except TorrentConflictError as e:
            self.logger.error(f"Torrent injection failed due to conflict: {e}")
            self.logger.error(
                "This usually happens because the source flag of the torrent to be injected is incorrect, "
                "which generally occurs on trackers that do not enforce source flag requirements."
            )
            raise

        max_retries = 8
        for attempt in range(max_retries):
            try:
                # Get current torrent name
                current_name = self._get_torrent_name(torrent_id)
                if not current_name:
                    raise ValueError(f"Failed to get torrent name for {torrent_id}")

                # Rename entire torrent
                if current_name != local_torrent_name:
                    self._rename_torrent(torrent_id, current_name, local_torrent_name)
                    self.logger.debug(f"Renamed torrent from {current_name} to {local_torrent_name}")

                # Process rename map only once
                if not rename_map_processed:
                    rename_map = self._process_rename_map(
                        torrent_id=torrent_id, base_path=local_torrent_name, rename_map=rename_map
                    )
                    rename_map_processed = True

                # Rename files
                if rename_map:
                    for torrent_file_name, local_file_name in rename_map.items():
                        self._rename_file(
                            torrent_id,
                            torrent_file_name,
                            local_file_name,
                        )
                        self.logger.debug(f"Renamed torrent file {torrent_file_name} to {local_file_name}")

                # Verify torrent (if renaming was performed or not hash match for non-Transmission clients)
                should_verify = (
                    current_name != local_torrent_name
                    or rename_map
                    or (not hash_match and not isinstance(self, TransmissionClient))
                )
                if should_verify:
                    self.logger.debug("Verifying torrent after renaming")
                    time.sleep(1)
                    self._verify_torrent(torrent_id)

                self.logger.success("Torrent injected successfully")
                return True
            except Exception as e:
                if attempt < max_retries - 1:
                    self.logger.debug(f"Error injecting torrent: {e}, retrying ({attempt + 1}/{max_retries})...")
                    time.sleep(2)
                else:
                    self.logger.error(f"Failed to inject torrent after {max_retries} attempts: {e}")
                    return False

        # This should never be reached, but just in case
        return False

    # ===== The following methods need to be implemented by derived classes =====

    @abstractmethod
    def _add_torrent(self, torrent_data, download_dir: str, hash_match: bool) -> str:
        """Add torrent to client, return torrent ID.

        Args:
            torrent_data: Torrent file data.
            download_dir (str): Download directory.
            hash_match (bool): Whether this is a hash match, if True, skip verification.

        Returns:
            str: Torrent ID.
        """
        pass

    @abstractmethod
    def _remove_torrent(self, torrent_id: str):
        """Remove torrent from client.

        Args:
            torrent_id (str): Torrent ID.
        """
        pass

    @abstractmethod
    def get_torrent_info(self, torrent_id: str) -> ClientTorrentInfo | None:
        """Get torrent information.

        Args:
            torrent_id (str): Torrent ID.

        Returns:
            ClientTorrentInfo | None: Torrent information object, or None if not found.
        """
        pass

    @abstractmethod
    def _rename_torrent(self, torrent_id: str, old_name: str, new_name: str):
        """Rename entire torrent.

        Args:
            torrent_id (str): Torrent ID.
            old_name (str): Old torrent name.
            new_name (str): New torrent name.
        """
        pass

    @abstractmethod
    def _rename_file(self, torrent_id: str, old_path: str, new_name: str):
        """Rename file within torrent.

        Args:
            torrent_id (str): Torrent ID.
            old_path (str): Old file path.
            new_name (str): New file name.
        """
        pass

    @abstractmethod
    def _verify_torrent(self, torrent_id: str):
        """Verify torrent integrity.

        Args:
            torrent_id (str): Torrent ID.
        """
        pass

    @abstractmethod
    def _get_torrent_name(self, torrent_id: str) -> str:
        """Get torrent name by ID.

        Args:
            torrent_id (str): Torrent ID.

        Returns:
            str: Torrent name.
        """
        pass

    @abstractmethod
    def _process_rename_map(self, torrent_id: str, base_path: str, rename_map: dict) -> dict:
        """Process rename mapping to adapt to specific torrent client.

        Args:
            torrent_id (str): Torrent ID.
            base_path (str): Base path for files.
            rename_map (dict): Original rename mapping.

        Returns:
            dict: Processed rename mapping.
        """
        pass

    def get_torrent_object(self, torrent_hash: str) -> "torf.Torrent | None":
        """Get torrent object from client by hash.

        Args:
            torrent_hash (str): Torrent hash.

        Returns:
            torf.Torrent | None: Torrent object, or None if not available.
        """
        try:
            torrent_data = self._get_torrent_data_by_hash(torrent_hash)
            if torrent_data:
                return torf.Torrent.read_stream(torrent_data)
            return None
        except Exception as e:
            self.logger.error(f"Error getting torrent object for hash {torrent_hash}: {e}")
            return None

    def reverse_inject_torrent(
        self, matched_torrents: list[ClientTorrentInfo], new_name: str, reverse_rename_map: dict
    ) -> dict[str, bool]:
        """Reverse inject logic: rename all local torrents to match incoming torrent format.

        Args:
            matched_torrents (list[ClientTorrentInfo]): List of local torrents to rename.
            new_name (str): New torrent name to match incoming torrent.
            reverse_rename_map (dict): File rename mapping from local to incoming format.

        Returns:
            dict[str, bool]: Dictionary mapping torrent ID to success status.
        """
        results = {}

        for matched_torrent in matched_torrents:
            torrent_id = matched_torrent.id
            try:
                # Get current torrent name
                torrent_info = self.get_torrent_info(torrent_id)
                if torrent_info is None:
                    self.logger.warning(f"Failed to get torrent info for {torrent_id}, skipping")
                    continue
                current_name = torrent_info.name

                # Rename entire torrent
                if current_name != new_name:
                    self._rename_torrent(torrent_id, current_name, new_name)
                    self.logger.debug(f"Renamed torrent {torrent_id} from {current_name} to {new_name}")

                # Rename files according to reverse rename map
                if reverse_rename_map:
                    for local_file_name, incoming_file_name in reverse_rename_map.items():
                        self._rename_file(
                            torrent_id,
                            local_file_name,
                            incoming_file_name,
                        )
                        self.logger.debug(
                            f"Renamed file {local_file_name} to {incoming_file_name} in torrent {torrent_id}"
                        )

                # Verify torrent after renaming
                if current_name != new_name or reverse_rename_map:
                    self.logger.debug(f"Verifying torrent {torrent_id} after reverse renaming")
                    self._verify_torrent(torrent_id)

                results[str(torrent_id)] = True
                self.logger.success(f"Reverse injection completed successfully for torrent {torrent_id}")

            except Exception as e:
                results[str(torrent_id)] = False
                self.logger.error(f"Failed to reverse inject torrent {torrent_id}: {e}")

        return results

    @abstractmethod
    def _get_torrent_data_by_hash(self, torrent_hash: str) -> bytes | None:
        """Get torrent data from client by hash - subclasses must implement.

        Args:
            torrent_hash (str): Torrent hash.

        Returns:
            bytes | None: Torrent file data, or None if not available.
        """
        pass


class TransmissionClient(TorrentClient):
    """Transmission torrent client implementation."""

    def __init__(self, url: str):
        super().__init__()
        config = parse_libtc_url(url)
        self.torrents_dir = config.torrents_dir or "/config/torrents"

        self.client = transmission_rpc.Client(
            host=config.host or "localhost",
            port=config.port or 9091,
            username=config.username,
            password=config.password,
        )

    def _decode_piece_progress(self, pieces_b64: str, piece_count: int) -> list[bool]:
        """Decode base64 pieces data to get piece download status.

        Args:
            pieces_b64: Base64 encoded pieces data from Transmission
            piece_count: Total number of pieces in the torrent

        Returns:
            List of boolean values indicating piece download status
        """
        pieces_data = base64.b64decode(pieces_b64)
        piece_progress = [False] * piece_count

        for byte_index in range(min(len(pieces_data), (piece_count + 7) // 8)):
            byte_value = pieces_data[byte_index]
            start_piece = byte_index * 8
            end_piece = min(start_piece + 8, piece_count)

            for bit_offset in range(end_piece - start_piece):
                bit_index = 7 - bit_offset
                piece_progress[start_piece + bit_offset] = bool(byte_value & (1 << bit_index))

        return piece_progress

    def get_torrents(self) -> list[ClientTorrentInfo]:
        """Get all torrents from Transmission.

        Returns:
            list[ClientTorrentInfo]: List of torrent information.
        """
        try:
            torrents = self.client.get_torrents(
                arguments=[
                    "name",
                    "hashString",
                    "percentDone",
                    "totalSize",
                    "files",
                    "trackerList",
                    "downloadDir",
                    "status",
                    "pieces",
                    "pieceCount",
                ]
            )
            result = []

            for torrent in torrents:
                result.append(
                    ClientTorrentInfo(
                        id=torrent.hash_string,
                        name=torrent.name,
                        hash=torrent.hash_string,
                        progress=torrent.percent_done,
                        total_size=torrent.total_size,
                        files=[
                            ClientTorrentFile(
                                name=f["name"],
                                size=f["length"],
                                progress=f.get("bytesCompleted", 0) / f["length"] if f["length"] > 0 else 0.0,
                            )
                            for f in torrent.fields["files"]
                        ],
                        trackers=torrent.tracker_list,
                        download_dir=torrent.download_dir,
                        state=TRANSMISSION_STATE_MAPPING.get(torrent.status.value, TorrentState.UNKNOWN),
                        piece_progress=self._decode_piece_progress(torrent.pieces, torrent.piece_count),
                    )
                )

            return result

        except Exception as e:
            self.logger.error("Error retrieving torrents from Transmission: %s", e)
            return []

    def _add_torrent(self, torrent_data, download_dir: str, hash_match: bool) -> str:
        """Add torrent to Transmission.

        Args:
            torrent_data: Torrent file data.
            download_dir (str): Download directory.
            hash_match (bool): Not used for Transmission (has fast verification by default).

        Returns:
            str: Torrent hash string.
        """
        # Note: We reimplement this method instead of using client.add_torrent()
        # because we need access to the raw response data to detect torrent-duplicate
        # and handle it appropriately in the injection logic.

        # Get torrent data for RPC call
        torrent_data_b64 = base64.b64encode(torrent_data).decode()

        # Prepare arguments
        kwargs = {
            "download-dir": download_dir,
            "paused": True,
            "metainfo": torrent_data_b64,
            "labels": [config.cfg.downloader.label],
        }

        # Make direct RPC call to get raw response
        query = {"method": RpcMethod.TorrentAdd, "arguments": kwargs}
        http_data = self.client._http_query(query)

        # Parse JSON response
        try:
            data = json.loads(http_data)
        except json.JSONDecodeError as error:
            raise ValueError("failed to parse response as json", query, http_data) from error

        if "result" not in data:
            raise ValueError("Query failed, response data missing without result.", query, data, http_data)

        if data["result"] != "success":
            raise ValueError(f'Query failed with result "{data["result"]}".', query, data, http_data)

        # Extract torrent info from arguments
        res = data["arguments"]
        torrent_info = None
        if "torrent-added" in res:
            torrent_info = res["torrent-added"]
        elif "torrent-duplicate" in res:
            torrent_info = res["torrent-duplicate"]
            error_msg = f"The torrent to be injected cannot coexist with local torrent {torrent_info['hashString']}"
            self.logger.error(error_msg)
            raise TorrentConflictError(error_msg)

        if not torrent_info:
            raise ValueError("Invalid torrent-add response")

        return torrent_info["hashString"]

    def _remove_torrent(self, torrent_id: str):
        """Remove torrent from Transmission.

        Args:
            torrent_id (int): Torrent ID.
        """
        self.client.remove_torrent(torrent_id, delete_data=False)

    def get_torrent_info(self, torrent_id: str) -> ClientTorrentInfo | None:
        """Get torrent information."""
        try:
            torrent = self.client.get_torrent(
                torrent_id,
                arguments=[
                    "name",
                    "hashString",
                    "percentDone",
                    "totalSize",
                    "files",
                    "trackerList",
                    "downloadDir",
                    "status",
                    "pieces",
                    "pieceCount",
                ],
            )
            return ClientTorrentInfo(
                id=torrent.hash_string,
                name=torrent.name,
                hash=torrent.hash_string,
                progress=torrent.percent_done,
                total_size=torrent.total_size,
                files=[
                    ClientTorrentFile(
                        name=f["name"],
                        size=f["length"],
                        progress=f.get("bytesCompleted", 0) / f["length"] if f["length"] > 0 else 0.0,
                    )
                    for f in torrent.fields["files"]
                ],
                trackers=torrent.tracker_list,
                download_dir=torrent.download_dir,
                state=TRANSMISSION_STATE_MAPPING.get(torrent.status.value, TorrentState.UNKNOWN),
                piece_progress=self._decode_piece_progress(torrent.pieces, torrent.piece_count),
            )
        except Exception as e:
            self.logger.error("Error retrieving torrent info from Transmission: %s", e)
            return None

    def _rename_torrent(self, torrent_id: str, old_name: str, new_name: str):
        """Rename entire torrent."""
        self.client.rename_torrent_path(torrent_id, location=old_name, name=new_name)

    def _rename_file(self, torrent_id: str, old_path: str, new_name: str):
        """Rename file within torrent."""
        self.client.rename_torrent_path(torrent_id, location=old_path, name=new_name)

    def _verify_torrent(self, torrent_id: str):
        """Verify torrent integrity."""
        self.client.verify_torrent(torrent_id)

    def _get_torrent_name(self, torrent_id: str) -> str:
        """Get torrent name by ID."""
        torrent = self.client.get_torrent(torrent_id, arguments=["name"])
        return torrent.name

    def resume_torrent(self, torrent_id: str) -> bool:
        """Resume downloading a torrent in Transmission."""
        try:
            self.client.start_torrent(torrent_id)
            return True
        except Exception as e:
            self.logger.error(f"Failed to resume torrent {torrent_id}: {e}")
            return False

    def _process_rename_map(self, torrent_id: str, base_path: str, rename_map: dict) -> dict:
        """Process rename mapping to adapt to Transmission."""
        transmission_map = {}
        temp_map = {}
        for torrent_name, local_name in rename_map.items():
            torrent_name_list = torrent_name.split("/")
            local_name_list = local_name.split("/")
            # Transmission cannot complete non-same-level moves
            if len(torrent_name_list) == len(local_name_list):
                for i in range(len(torrent_name_list)):
                    if torrent_name_list[i] != local_name_list[i]:
                        temp_map[("/".join(torrent_name_list[: i + 1]), local_name_list[i])] = i

        for (key, value), _priority in sorted(temp_map.items(), key=lambda item: item[1], reverse=True):
            transmission_map[posixpath.join(base_path, key)] = value

        return transmission_map

    def _get_torrent_data_by_hash(self, torrent_hash: str) -> bytes | None:
        """Get torrent data from Transmission by hash."""
        try:
            torrent_path = posixpath.join(self.torrents_dir, torrent_hash + ".torrent")
            with open(torrent_path, "rb") as f:
                return f.read()
        except Exception as e:
            self.logger.error(f"Error getting torrent data from Transmission: {e}")
            return None


class QBittorrentClient(TorrentClient):
    """qBittorrent torrent client implementation."""

    def __init__(self, url: str):
        super().__init__()
        config = parse_libtc_url(url)

        self.client = qbittorrentapi.Client(
            host=config.url or "http://localhost:8080",
            username=config.username,
            password=config.password,
        )
        # Authenticate with qBittorrent
        self.client.auth_log_in()

    def get_torrents(self) -> list[ClientTorrentInfo]:
        """Get all torrents from qBittorrent."""
        try:
            torrents = self.client.torrents_info()
            result = []

            for torrent in torrents:
                files = torrent.files
                trackers = torrent.trackers
                # Remove special virtual trackers from tracker_urls
                tracker_urls = [
                    tracker.url
                    for tracker in trackers
                    if tracker.url not in ("** [DHT] **", "** [PeX] **", "** [LSD] **")
                ]

                result.append(
                    ClientTorrentInfo(
                        id=torrent.hash,
                        name=torrent.name,
                        hash=torrent.hash,
                        progress=torrent.progress,
                        total_size=torrent.size,
                        files=[ClientTorrentFile(name=f.name, size=f.size, progress=f.progress) for f in files],
                        trackers=tracker_urls,
                        download_dir=torrent.save_path,
                        state=QBITTORRENT_STATE_MAPPING.get(torrent.state, TorrentState.UNKNOWN),
                        piece_progress=[piece == 2 for piece in torrent.pieceStates] if torrent.pieceStates else [],
                    )
                )

            return result

        except Exception as e:
            self.logger.error("Error retrieving torrents from qBittorrent: %s", e)
            return []

    def _add_torrent(self, torrent_data, download_dir: str, hash_match: bool) -> str:
        """Add torrent to qBittorrent."""

        # qBittorrent doesn't return the hash directly, we need to decode it
        torrent_obj = torf.Torrent.read_stream(torrent_data)
        info_hash = torrent_obj.infohash

        current_time = time.time()

        result = self.client.torrents_add(
            torrent_files=torrent_data,
            save_path=download_dir,
            is_paused=True,
            category=config.cfg.downloader.label,
            use_auto_torrent_management=False,
            is_skip_checking=hash_match,  # Skip hash checking if hash match
        )

        # qBittorrent returns "Ok." for success and "Fails." for failure
        if result != "Ok.":
            # Check if torrent already exists by comparing add time
            try:
                torrent_info = self.client.torrents_info(torrent_hashes=info_hash)
                if torrent_info:
                    # Get the first (and should be only) torrent with this hash
                    existing_torrent = torrent_info[0]
                    # Convert add time to unix timestamp
                    add_time = existing_torrent.added_on
                    if add_time < current_time:
                        raise TorrentConflictError(existing_torrent.hash)
                    # Check if tracker is correct
                    target_tracker = torrent_obj.trackers.flat[0] if torrent_obj.trackers else ""
                    if existing_torrent.tracker != target_tracker:
                        raise TorrentConflictError(existing_torrent.hash)

            except TorrentConflictError as e:
                error_msg = f"The torrent to be injected cannot coexist with local torrent {e}"
                self.logger.error(error_msg)
                raise TorrentConflictError(error_msg) from e
            except Exception as e:
                raise ValueError(f"Failed to add torrent to qBittorrent: {e}") from e

        return info_hash

    def _remove_torrent(self, torrent_id: str):
        """Remove torrent from qBittorrent."""
        self.client.torrents_delete(torrent_hashes=torrent_id, delete_files=False)

    def get_torrent_info(self, torrent_id: str) -> ClientTorrentInfo | None:
        """Get torrent information."""
        try:
            torrent_info = self.client.torrents_info(torrent_hashes=torrent_id)
            if not torrent_info:
                return None

            torrent = torrent_info[0]
            files = torrent.files
            trackers = torrent.trackers
            # Remove special virtual trackers from tracker_urls
            tracker_urls = [
                tracker.url for tracker in trackers if tracker.url not in ("** [DHT] **", "** [PeX] **", "** [LSD] **")
            ]

            return ClientTorrentInfo(
                id=torrent.hash,
                name=torrent.name,
                hash=torrent.hash,
                progress=torrent.progress,
                total_size=torrent.size,
                files=[ClientTorrentFile(name=f.name, size=f.size, progress=f.progress) for f in files],
                trackers=tracker_urls,
                download_dir=torrent.save_path,
                state=QBITTORRENT_STATE_MAPPING.get(torrent.state, TorrentState.UNKNOWN),
                piece_progress=[piece == 2 for piece in torrent.pieceStates] if torrent.pieceStates else [],
            )
        except Exception as e:
            self.logger.error("Error retrieving torrent info from qBittorrent: %s", e)
            return None

    def _rename_torrent(self, torrent_id: str, old_name: str, new_name: str):
        """Rename entire torrent."""
        self.client.torrents_rename(torrent_hash=torrent_id, new_torrent_name=new_name)
        self.client.torrents_rename_folder(torrent_hash=torrent_id, old_path=old_name, new_path=new_name)

    def _rename_file(self, torrent_id: str, old_path: str, new_name: str):
        """Rename file within torrent."""
        self.client.torrents_rename_file(torrent_hash=torrent_id, old_path=old_path, new_path=new_name)

    def _verify_torrent(self, torrent_id: str):
        """Verify torrent integrity."""
        self.client.torrents_recheck(torrent_hashes=torrent_id)

    def _get_torrent_name(self, torrent_id: str) -> str:
        """Get torrent name by ID."""
        torrent_info = self.client.torrents_info(torrent_hashes=torrent_id)
        if torrent_info:
            return torrent_info[0].name
        return ""

    def resume_torrent(self, torrent_id: str) -> bool:
        """Resume downloading a torrent in qBittorrent."""
        try:
            self.client.torrents_resume(torrent_hashes=torrent_id)
            return True
        except Exception as e:
            self.logger.error(f"Failed to resume torrent {torrent_id}: {e}")
            return False

    def _process_rename_map(self, torrent_id: str, base_path: str, rename_map: dict) -> dict:
        """
        qBittorrent needs to prepend the root directory
        """
        new_rename_map = {}
        for key, value in rename_map.items():
            new_rename_map[posixpath.join(base_path, key)] = posixpath.join(base_path, value)
        return new_rename_map

    def _get_torrent_data_by_hash(self, torrent_hash: str) -> bytes | None:
        """Get torrent data from qBittorrent by hash."""
        try:
            torrent_data = self.client.torrents_export(torrent_hash=torrent_hash)
            return torrent_data
        except Exception as e:
            self.logger.error(f"Error getting torrent data from qBittorrent: {e}")
            return None


class DelugeClient(TorrentClient):
    """Deluge torrent client implementation."""

    def __init__(self, url: str):
        super().__init__()
        config = parse_libtc_url(url)
        self.torrents_dir = config.torrents_dir or ""
        self.client = deluge_client.DelugeRPCClient(
            host=config.host or "localhost",
            port=config.port or 58846,
            username=config.username or "",
            password=config.password or "",
            decode_utf8=True,
        )
        # Connect to Deluge daemon
        self.client.connect()

    def get_torrents(self) -> list[ClientTorrentInfo]:
        """Get all torrents from Deluge."""
        try:
            torrent_details = self.client.call(
                "core.get_torrents_status",
                {},
                [
                    "name",
                    "hash",
                    "progress",
                    "total_size",
                    "files",
                    "file_progress",
                    "trackers",
                    "save_path",
                    "state",
                    "pieces",
                    "num_pieces",
                ],
            )
            if torrent_details is None:
                return []
            result = []

            for torrent_id, torrent_info in torrent_details.items():
                if torrent_info["progress"] == 100.0:
                    piece_progress = [True] * torrent_info["num_pieces"]
                else:
                    piece_progress = [piece == 3 for piece in torrent_info["pieces"]]

                result.append(
                    ClientTorrentInfo(
                        id=torrent_id,
                        name=torrent_info["name"],
                        hash=torrent_info["hash"],
                        progress=torrent_info["progress"] / 100.0,
                        total_size=torrent_info["total_size"],
                        files=[
                            ClientTorrentFile(
                                name=f["path"],
                                size=f["size"],
                                progress=torrent_info["file_progress"][f["index"]],
                            )
                            for f in torrent_info["files"]
                        ],
                        trackers=[t["url"] for t in torrent_info["trackers"]],
                        download_dir=torrent_info["save_path"],
                        state=DELUGE_STATE_MAPPING.get(torrent_info["state"], TorrentState.UNKNOWN),
                        piece_progress=piece_progress,
                    )
                )

            return result

        except Exception as e:
            self.logger.error("Error retrieving torrents from Deluge: %s", e)
            return []

    def _add_torrent(self, torrent_data, download_dir: str, hash_match: bool) -> str:
        """Add torrent to Deluge."""
        torrent_b64 = base64.b64encode(torrent_data).decode()
        try:
            torrent_id = self.client.call(
                "core.add_torrent_file",
                f"{os.urandom(16).hex()}.torrent",  # filename
                torrent_b64,
                {
                    "download_location": download_dir,
                    "add_paused": True,
                    "seed_mode": hash_match,  # Skip hash checking if hash match
                },
            )
        except Exception as e:
            if "Torrent already in session" in str(e):
                # Extract torrent ID from error message
                match = re.search(r"\(([a-f0-9]{40})\)", str(e))
                if match:
                    torrent_id = match.group(1)
                    error_msg = f"The torrent to be injected cannot coexist with local torrent {torrent_id}"
                    self.logger.error(error_msg)
                    raise TorrentConflictError(error_msg) from e
                else:
                    raise TorrentConflictError(str(e)) from e
            else:
                raise

        # Set label (if provided)
        label = config.cfg.downloader.label
        if label and torrent_id:
            try:
                self.client.call("label.set_torrent", torrent_id, label)
            except Exception as label_error:
                # If setting label fails, try creating label first
                if "Unknown Label" in str(label_error) or "label does not exist" in str(label_error).lower():
                    self.client.call("label.add", label)
                    # Try setting label again
                    self.client.call("label.set_torrent", torrent_id, label)

        return str(torrent_id)

    def _remove_torrent(self, torrent_id: str):
        """Remove torrent from Deluge."""
        self.client.call("core.remove_torrent", torrent_id, False)

    def get_torrent_info(self, torrent_id: str) -> ClientTorrentInfo | None:
        """Get torrent information."""
        try:
            torrent_info = self.client.call(
                "core.get_torrent_status",
                torrent_id,
                [
                    "name",
                    "hash",
                    "progress",
                    "total_size",
                    "files",
                    "file_progress",
                    "trackers",
                    "save_path",
                    "state",
                    "pieces",
                    "num_pieces",
                ],
            )

            if torrent_info is None:
                return None

            if torrent_info["progress"] == 100.0:
                piece_progress = [True] * torrent_info["num_pieces"]
            else:
                piece_progress = [piece == 3 for piece in torrent_info["pieces"]]

            return ClientTorrentInfo(
                id=torrent_id,
                name=torrent_info["name"],
                hash=torrent_info["hash"],
                progress=torrent_info["progress"] / 100.0,
                total_size=torrent_info["total_size"],
                files=[
                    ClientTorrentFile(
                        name=f["path"],
                        size=f["size"],
                        progress=torrent_info["file_progress"][f["index"]],
                    )
                    for f in torrent_info["files"]
                ],
                trackers=[t["url"] for t in torrent_info["trackers"]],
                download_dir=torrent_info["save_path"],
                state=DELUGE_STATE_MAPPING.get(torrent_info["state"], TorrentState.UNKNOWN),
                piece_progress=piece_progress,
            )
        except Exception as e:
            self.logger.error("Error retrieving torrent info from Deluge: %s", e)
            return None

    def _rename_torrent(self, torrent_id: str, old_name: str, new_name: str):
        """Rename entire torrent."""
        self.client.call("core.rename_folder", torrent_id, old_name + "/", new_name + "/")

    def _rename_file(self, torrent_id: str, old_path: str, new_name: str):
        """Rename file within torrent."""
        try:
            self.client.call("core.rename_files", torrent_id, [(old_path, new_name)])
        except Exception as e:
            self.logger.warning(f"Failed to rename file in Deluge: {e}")

    def _verify_torrent(self, torrent_id: str):
        """Verify torrent integrity."""
        self.client.call("core.force_recheck", [torrent_id])

    def _get_torrent_name(self, torrent_id: str) -> str:
        """Get torrent name by ID."""
        torrent_info = self.client.call("core.get_torrent_status", torrent_id, ["name"])
        if torrent_info is None:
            return ""
        return torrent_info.get("name", "")

    def resume_torrent(self, torrent_id: str) -> bool:
        """Resume downloading a torrent in Deluge."""
        try:
            self.client.call("core.resume_torrent", [torrent_id])
            return True
        except Exception as e:
            self.logger.error(f"Failed to resume torrent {torrent_id}: {e}")
            return False

    def _process_rename_map(self, torrent_id: str, base_path: str, rename_map: dict) -> dict:
        """
        Deluge needs to use index to rename files
        """
        new_rename_map = {}
        torrent_info = self.client.call("core.get_torrent_status", torrent_id, ["files"])
        if torrent_info is None:
            return {}
        files = torrent_info.get("files", [])
        for file in files:
            relpath = posixpath.relpath(file["path"], base_path)
            if relpath in rename_map:
                new_rename_map[file["index"]] = posixpath.join(base_path, rename_map[relpath])
        return new_rename_map

    def _get_torrent_data_by_hash(self, torrent_hash: str) -> bytes | None:
        """Get torrent data from Deluge by hash."""
        try:
            torrent_path = posixpath.join(str(self.torrents_dir), torrent_hash + ".torrent")
            with open(torrent_path, "rb") as f:
                return f.read()
        except Exception as e:
            self.logger.error(f"Error getting torrent data from Deluge: {e}")
            return None


class TorrentClientConfig(msgspec.Struct):
    """Configuration for torrent client connection."""

    # Common fields
    username: str | None = None
    password: str | None = None
    torrents_dir: str | None = None

    # For qBittorrent and rutorrent
    url: str | None = None

    # For Transmission and Deluge
    scheme: str | None = None
    host: str | None = None
    port: int | None = None


def parse_libtc_url(url: str) -> TorrentClientConfig:
    """Parse torrent client URL and extract connection parameters.

    Supported URL formats:
    - transmission+http://127.0.0.1:9091/?torrents_dir=/path
    - rutorrent+http://RUTORRENT_ADDRESS:9380/plugins/rpc/rpc.php
    - deluge://username:password@127.0.0.1:58664
    - qbittorrent+http://username:password@127.0.0.1:8080

    Args:
        url: The torrent client URL to parse

    Returns:
        TorrentClientConfig: Structured configuration object
    """
    parsed = urlparse(url)
    scheme = parsed.scheme.split("+")
    netloc = parsed.netloc

    # Extract username and password if present
    username = None
    password = None
    if "@" in netloc:
        auth, netloc = netloc.rsplit("@", 1)
        username, password = auth.split(":", 1)

    client = scheme[0]

    if client in ["qbittorrent", "rutorrent"]:
        # For qBittorrent and rutorrent, use URL format
        client_url = f"{scheme[1]}://{netloc}{parsed.path}"
        return TorrentClientConfig(
            username=username,
            password=password,
            url=client_url,
            torrents_dir=dict(parse_qsl(parsed.query)).get("torrents_dir"),
        )
    else:
        # For Transmission and Deluge, use host:port format
        host, port_str = netloc.split(":")
        port = int(port_str)

        # Extract additional query parameters
        query_params = dict(parse_qsl(parsed.query))

        return TorrentClientConfig(
            username=username,
            password=password,
            scheme=scheme[-1],
            host=host,
            port=port,
            torrents_dir=query_params.get("torrents_dir"),
        )


# Torrent client factory mapping
TORRENT_CLIENT_MAPPING = {
    "transmission": TransmissionClient,
    "qbittorrent": QBittorrentClient,
    "deluge": DelugeClient,
}


def create_torrent_client(url: str) -> TorrentClient:
    """Create a torrent client instance based on the URL scheme"""
    parsed = urlparse(url)
    client_type = parsed.scheme.split("+")[0]

    if client_type not in TORRENT_CLIENT_MAPPING:
        raise ValueError(f"Unsupported torrent client type: {client_type}")

    return TORRENT_CLIENT_MAPPING[client_type](url)


# Global torrent client instance
_torrent_client_instance: TorrentClient | None = None
_torrent_client_lock = threading.Lock()


def get_torrent_client() -> TorrentClient:
    """Get global torrent client instance.

    Returns:
        TorrentClient: Torrent client instance.
    """
    global _torrent_client_instance
    with _torrent_client_lock:
        if _torrent_client_instance is None:
            # Get client URL from config
            client_url = config.cfg.downloader.client
            _torrent_client_instance = create_torrent_client(client_url)
        return _torrent_client_instance


def set_torrent_client(torrent_client: TorrentClient) -> None:
    """Set global torrent client instance.

    Args:
        torrent_client: Torrent client instance to set as current.
    """
    global _torrent_client_instance
    with _torrent_client_lock:
        _torrent_client_instance = torrent_client
