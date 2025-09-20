"""
Torrent Client Module

This module provides a unified interface for different torrent clients including
Transmission, qBittorrent, and Deluge.
"""

import base64
import os
import posixpath
import time
from abc import ABC, abstractmethod
from urllib.parse import parse_qsl, urlparse

import deluge_client
import msgspec
import qbittorrentapi
import torf
import transmission_rpc

from . import config, filecompare, logger


class ClientTorrentFile(msgspec.Struct):
    """Represents a file within a torrent from torrent client."""

    name: str
    size: int


class ClientTorrentInfo(msgspec.Struct):
    """Represents a torrent with all its information from torrent client."""

    id: str | int
    name: str
    hash: str
    percent_done: float
    total_size: int
    files: list[ClientTorrentFile]
    trackers: list[str]
    download_dir: str
    existing_target_trackers: list[str] = msgspec.field(default_factory=list)

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
                return None

            # Check if torrent meets basic conditions (same as get_filtered_torrents)
            check_trackers_list = config.cfg.global_config.check_trackers
            if check_trackers_list and not any(
                any(check_str in url for check_str in check_trackers_list) for url in target_torrent.trackers
            ):
                return None

            # Filter MP3 files (based on configuration)
            if config.cfg.global_config.exclude_mp3:
                has_mp3 = any(posixpath.splitext(file.name)[1].lower() == ".mp3" for file in target_torrent.files)
                if has_mp3:
                    return None

            # Check if torrent contains music files (if check_music_only is enabled)
            if config.cfg.global_config.check_music_only:
                has_music = any(filecompare.is_music_file(file.name) for file in target_torrent.files)
                if not has_music:
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
                percent_done=target_torrent.percent_done,
                total_size=target_torrent.total_size,
                files=target_torrent.files,
                trackers=target_torrent.trackers,
                download_dir=target_torrent.download_dir,
                existing_target_trackers=list(existing_trackers),
            )

        except Exception as e:
            if self.logger:
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
            valid_torrents = {}  # Torrents that meet basic conditions

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
                    if self.logger:
                        self.logger.debug(
                            f"Skipping {content_name}: already exists on all target trackers {existing_trackers}"
                        )
                    continue

                # Otherwise include in results
                filtered_torrents[content_name] = ClientTorrentInfo(
                    id=torrent.id,
                    name=content_name,
                    hash=torrent.hash,
                    percent_done=torrent.percent_done,
                    total_size=torrent.total_size,
                    files=torrent.files,
                    trackers=torrent.trackers,
                    download_dir=torrent.download_dir,
                    existing_target_trackers=list(existing_trackers),  # Record existing target trackers
                )

            return filtered_torrents

        except Exception as e:
            if self.logger:
                self.logger.error("Error retrieving torrents: %s", e)
            return {}

    def inject_torrent(self, torrent_data, download_dir: str, local_torrent_name: str, rename_map: dict) -> bool:
        """Inject torrent into client (includes complete logic).

        Derived classes only need to implement specific client operation methods.

        Args:
            torrent_data: Torrent file data.
            download_dir (str): Download directory.
            local_torrent_name (str): Local torrent name.
            rename_map (dict): File rename mapping.

        Returns:
            bool: True if injection successful, False otherwise.
        """
        # Flag to track if rename map has been processed
        rename_map_processed = False

        max_retries = 8
        for attempt in range(max_retries):
            try:
                # Add torrent to client
                torrent_id = self._add_torrent(torrent_data, download_dir)

                # Check if tracker is correctly added
                torrent_obj = torf.Torrent.read_stream(torrent_data)
                target_tracker = torrent_obj.trackers.flat[0] if torrent_obj.trackers else ""
                if not self._has_target_tracker(torrent_id, target_tracker):
                    if self.logger:
                        self.logger.debug(f"Torrent does not have target tracker: {target_tracker}")
                    self._remove_torrent(torrent_id)
                    torrent_id = self._add_torrent(torrent_data, download_dir)

                # Get current torrent name
                current_name = self._get_torrent_name(torrent_id)

                # Rename entire torrent
                if current_name != local_torrent_name:
                    self._rename_torrent(torrent_id, current_name, local_torrent_name)
                    if self.logger:
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
                        if self.logger:
                            self.logger.debug(f"Renamed torrent file {torrent_file_name} to {local_file_name}")

                # Verify torrent (if renaming was performed)
                if current_name != local_torrent_name or rename_map:
                    if self.logger:
                        self.logger.debug("Verifying torrent after renaming")
                    self._verify_torrent(torrent_id)

                if self.logger:
                    self.logger.success("Torrent injected successfully")
                return True
            except Exception as e:
                if attempt < max_retries - 1:
                    if self.logger:
                        self.logger.debug(f"Error injecting torrent: {e}, retrying ({attempt + 1}/{max_retries})...")
                    time.sleep(2)
                else:
                    if self.logger:
                        self.logger.error(f"Failed to inject torrent after {max_retries} attempts: {e}")
                    return False

    # ===== The following methods need to be implemented by derived classes =====

    @abstractmethod
    def _add_torrent(self, torrent_data, download_dir: str) -> str | int:
        """Add torrent to client, return torrent ID.

        Args:
            torrent_data: Torrent file data.
            download_dir (str): Download directory.

        Returns:
            str | int: Torrent ID.
        """
        pass

    @abstractmethod
    def _remove_torrent(self, torrent_id: str | int):
        """Remove torrent from client.

        Args:
            torrent_id (str | int): Torrent ID.
        """
        pass

    @abstractmethod
    def _get_torrent_name(self, torrent_id: str | int) -> str:
        """Get torrent name.

        Args:
            torrent_id (str | int): Torrent ID.

        Returns:
            str: Torrent name.
        """
        pass

    @abstractmethod
    def _rename_torrent(self, torrent_id: str | int, old_name: str, new_name: str):
        """Rename entire torrent.

        Args:
            torrent_id (str | int): Torrent ID.
            old_name (str): Old torrent name.
            new_name (str): New torrent name.
        """
        pass

    @abstractmethod
    def _rename_file(self, torrent_id: str | int, old_path: str, new_name: str):
        """Rename file within torrent.

        Args:
            torrent_id (str | int): Torrent ID.
            old_path (str): Old file path.
            new_name (str): New file name.
        """
        pass

    @abstractmethod
    def _verify_torrent(self, torrent_id: str | int):
        """Verify torrent integrity.

        Args:
            torrent_id (str | int): Torrent ID.
        """
        pass

    @abstractmethod
    def _has_target_tracker(self, torrent_id: str | int, target_tracker: str) -> bool:
        """Check if torrent contains target tracker.

        Args:
            torrent_id (str | int): Torrent ID.
            target_tracker (str): Target tracker URL.

        Returns:
            bool: True if torrent contains target tracker.
        """
        pass

    @abstractmethod
    def _process_rename_map(self, torrent_id: str | int, base_path: str, rename_map: dict) -> dict:
        """Process rename mapping to adapt to specific torrent client.

        Args:
            torrent_id (str | int): Torrent ID.
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
            if self.logger:
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
                current_name = self._get_torrent_name(torrent_id)

                # Rename entire torrent
                if current_name != new_name:
                    self._rename_torrent(torrent_id, current_name, new_name)
                    if self.logger:
                        self.logger.debug(f"Renamed torrent {torrent_id} from {current_name} to {new_name}")

                # Rename files according to reverse rename map
                if reverse_rename_map:
                    for local_file_name, incoming_file_name in reverse_rename_map.items():
                        self._rename_file(
                            torrent_id,
                            local_file_name,
                            incoming_file_name,
                        )
                        if self.logger:
                            self.logger.debug(
                                f"Renamed file {local_file_name} to {incoming_file_name} in torrent {torrent_id}"
                            )

                # Verify torrent after renaming
                if current_name != new_name or reverse_rename_map:
                    if self.logger:
                        self.logger.debug(f"Verifying torrent {torrent_id} after reverse renaming")
                    self._verify_torrent(torrent_id)

                results[str(torrent_id)] = True
                if self.logger:
                    self.logger.success(f"Reverse injection completed successfully for torrent {torrent_id}")

            except Exception as e:
                results[str(torrent_id)] = False
                if self.logger:
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
        parsed = parse_libtc_url(url)
        self.torrents_dir = parsed.get("torrents_dir", "/config/torrents")
        self.client = transmission_rpc.Client(
            host=parsed.get("host", "localhost"),
            port=parsed.get("port", 9091),
            username=parsed.get("username"),
            password=parsed.get("password"),
        )

    def get_torrents(self) -> list[ClientTorrentInfo]:
        """Get all torrents from Transmission.

        Returns:
            list[ClientTorrentInfo]: List of torrent information.
        """
        try:
            torrents = self.client.get_torrents()
            result = []

            for torrent in torrents:
                result.append(
                    ClientTorrentInfo(
                        id=torrent.id,
                        name=torrent.name,
                        hash=torrent.hash_string,
                        percent_done=torrent.percent_done,
                        total_size=torrent.total_size,
                        files=[ClientTorrentFile(name=f["name"], size=f["length"]) for f in torrent.fields["files"]],
                        trackers=torrent.tracker_list,
                        download_dir=torrent.download_dir,
                    )
                )

            return result

        except Exception as e:
            if self.logger:
                self.logger.error("Error retrieving torrents from Transmission: %s", e)
            return []

    def _add_torrent(self, torrent_data, download_dir: str) -> int:
        """Add torrent to Transmission.

        Args:
            torrent_data: Torrent file data.
            download_dir (str): Download directory.

        Returns:
            int: Torrent ID.
        """
        added_torrent = self.client.add_torrent(
            torrent_data, download_dir=download_dir, paused=True, labels=[config.cfg.downloader.label]
        )
        return added_torrent.id

    def _remove_torrent(self, torrent_id: int):
        """Remove torrent from Transmission.

        Args:
            torrent_id (int): Torrent ID.
        """
        self.client.remove_torrent(torrent_id, delete_data=False)

    def _get_torrent_name(self, torrent_id: int) -> str:
        """Get torrent name."""
        return self.client.get_torrent(torrent_id).name

    def _rename_torrent(self, torrent_id: int, old_name: str, new_name: str):
        """Rename entire torrent."""
        self.client.rename_torrent_path(torrent_id, location=old_name, name=new_name)

    def _rename_file(self, torrent_id: int, old_path: str, new_name: str):
        """Rename file within torrent."""
        self.client.rename_torrent_path(torrent_id, location=old_path, name=new_name)

    def _verify_torrent(self, torrent_id: int):
        """Verify torrent integrity."""
        self.client.verify_torrent(torrent_id)

    def _has_target_tracker(self, torrent_id: int, target_tracker: str) -> bool:
        """Check if torrent contains target tracker."""
        torrent = self.client.get_torrent(torrent_id)
        return any(target_tracker in url for url in torrent.tracker_list)

    def _process_rename_map(self, torrent_id: int, base_path: str, rename_map: dict) -> dict:
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
            if self.logger:
                self.logger.error(f"Error getting torrent data from Transmission: {e}")
            return None


class QBittorrentClient(TorrentClient):
    """qBittorrent torrent client implementation."""

    def __init__(self, url: str):
        super().__init__()
        parsed = parse_libtc_url(url)
        self.client = qbittorrentapi.Client(
            host=parsed.get("url", "http://localhost:8080"),
            username=parsed.get("username"),
            password=parsed.get("password"),
        )
        # Authenticate with qBittorrent
        self.client.auth_log_in()

    def get_torrents(self) -> list[ClientTorrentInfo]:
        """Get all torrents from qBittorrent."""
        try:
            torrents = self.client.torrents_info()
            result = []

            for torrent in torrents:
                # Get torrent files
                files = self.client.torrents_files(torrent_hash=torrent.hash)
                # Get torrent trackers
                trackers = self.client.torrents_trackers(torrent_hash=torrent.hash)
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
                        percent_done=torrent.progress,
                        total_size=torrent.size,
                        files=[ClientTorrentFile(name=f.name, size=f.size) for f in files],
                        trackers=tracker_urls,
                        download_dir=torrent.save_path,
                    )
                )

            return result

        except Exception as e:
            if self.logger:
                self.logger.error("Error retrieving torrents from qBittorrent: %s", e)
            return []

    def _add_torrent(self, torrent_data, download_dir: str) -> str:
        """Add torrent to qBittorrent."""
        self.client.torrents_add(
            torrent_files=torrent_data, save_path=download_dir, is_paused=True, category=config.cfg.downloader.label
        )
        # qBittorrent doesn't return the hash directly, we need to decode it
        torrent_obj = torf.Torrent.read_stream(torrent_data)
        info_hash = torrent_obj.infohash
        return info_hash

    def _remove_torrent(self, torrent_id: str):
        """Remove torrent from qBittorrent."""
        self.client.torrents_delete(torrent_hashes=torrent_id, delete_files=False)

    def _get_torrent_name(self, torrent_id: str) -> str:
        """Get torrent name."""
        torrent_info = self.client.torrents_info(torrent_hashes=torrent_id)
        if torrent_info:
            return torrent_info[0].name
        return ""

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

    def _has_target_tracker(self, torrent_id: str, target_tracker: str) -> bool:
        """Check if torrent contains target tracker."""
        trackers = self.client.torrents_trackers(torrent_hash=torrent_id)
        tracker_urls = [tracker.url for tracker in trackers]
        return any(target_tracker in url for url in tracker_urls)

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
            if self.logger:
                self.logger.error(f"Error getting torrent data from qBittorrent: {e}")
            return None


class DelugeClient(TorrentClient):
    """Deluge torrent client implementation."""

    def __init__(self, url: str):
        super().__init__()
        parsed = parse_libtc_url(url)
        self.torrents_dir = parsed.get("torrents_dir", "")
        self.client = deluge_client.DelugeRPCClient(
            host=parsed.get("host", "localhost"),
            port=parsed.get("port", 58846),
            username=parsed.get("username", ""),
            password=parsed.get("password", ""),
            decode_utf8=True,
        )
        # Connect to Deluge daemon
        self.client.connect()

    def get_torrents(self) -> list[ClientTorrentInfo]:
        """Get all torrents from Deluge."""
        try:
            torrent_ids = self.client.call("core.get_torrents_status", {}, [])
            result = []

            for torrent_id, torrent_info in torrent_ids.items():
                result.append(
                    ClientTorrentInfo(
                        id=torrent_id,
                        name=torrent_info["name"],
                        hash=torrent_info["hash"],
                        percent_done=torrent_info["progress"] / 100.0,
                        total_size=torrent_info["total_size"],
                        files=[ClientTorrentFile(name=f["path"], size=f["size"]) for f in torrent_info["files"]],
                        trackers=[t["url"] for t in torrent_info["trackers"]],
                        download_dir=torrent_info["save_path"],
                    )
                )

            return result

        except Exception as e:
            if self.logger:
                self.logger.error("Error retrieving torrents from Deluge: %s", e)
            return []

    def _add_torrent(self, torrent_data, download_dir: str) -> str:
        """Add torrent to Deluge."""
        torrent_b64 = base64.b64encode(torrent_data).decode()

        try:
            if self.logger:
                self.logger.info("Adding torrent to Deluge...")

            torrent_id = self.client.call(
                "core.add_torrent_file",
                f"{os.urandom(16).hex()}.torrent",  # filename
                torrent_b64,
                {
                    "download_location": download_dir,
                    "add_paused": True,
                },
            )

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

            return torrent_id
        except Exception as e:
            if self.logger:
                self.logger.error(f"Failed to add torrent: {e}")
            return None

    def _remove_torrent(self, torrent_id: str):
        """Remove torrent from Deluge."""
        self.client.call("core.remove_torrent", torrent_id, False)

    def _get_torrent_name(self, torrent_id: str) -> str:
        """Get torrent name."""
        torrent_info = self.client.call("core.get_torrent_status", torrent_id, ["name"])
        return torrent_info["name"]

    def _rename_torrent(self, torrent_id: str, old_name: str, new_name: str):
        """Rename entire torrent."""
        self.client.call("core.rename_folder", torrent_id, old_name + "/", new_name + "/")

    def _rename_file(self, torrent_id: str, old_path: str, new_name: str):
        """Rename file within torrent."""
        try:
            self.client.call("core.rename_files", torrent_id, [(old_path, new_name)])
        except Exception as e:
            if self.logger:
                self.logger.warning(f"Failed to rename file in Deluge: {e}")

    def _verify_torrent(self, torrent_id: str):
        """Verify torrent integrity."""
        self.client.call("core.force_recheck", [torrent_id])

    def _has_target_tracker(self, torrent_id: str, target_tracker: str) -> bool:
        """Check if torrent contains target tracker."""
        torrent_info = self.client.call("core.get_torrent_status", torrent_id, ["trackers"])
        tracker_urls = [tracker["url"] for tracker in torrent_info["trackers"]]
        return any(target_tracker in url for url in tracker_urls)

    def _process_rename_map(self, torrent_id: str, base_path: str, rename_map: dict) -> dict:
        """
        Deluge needs to use index to rename files
        """
        new_rename_map = {}
        torrent_info = self.client.call("core.get_torrent_status", torrent_id, ["files"])
        files = torrent_info.get("files", [])
        for file in files:
            relpath = posixpath.relpath(file["path"], base_path)
            if relpath in rename_map:
                new_rename_map[file["index"]] = posixpath.join(base_path, rename_map[relpath])
        return new_rename_map

    def _get_torrent_data_by_hash(self, torrent_hash: str) -> bytes | None:
        """Get torrent data from Deluge by hash."""
        try:
            torrent_path = posixpath.join(self.torrents_dir, torrent_hash + ".torrent")
            with open(torrent_path, "rb") as f:
                return f.read()
        except Exception as e:
            if self.logger:
                self.logger.error(f"Error getting torrent data from Deluge: {e}")
            return None


def parse_libtc_url(url):
    """Parse torrent client URL and extract connection parameters"""
    # transmission+http://127.0.0.1:9091/?torrents_dir=/path
    # rutorrent+http://RUTORRENT_ADDRESS:9380/plugins/rpc/rpc.php
    # deluge://username:password@127.0.0.1:58664
    # qbittorrent+http://username:password@127.0.0.1:8080

    kwargs = {}
    parsed = urlparse(url)
    scheme = parsed.scheme.split("+")
    netloc = parsed.netloc

    if "@" in netloc:
        auth, netloc = netloc.rsplit("@", 1)
        username, password = auth.split(":", 1)
        kwargs["username"] = username
        kwargs["password"] = password

    client = scheme[0]
    if client in ["qbittorrent", "rutorrent"]:
        kwargs["url"] = f"{scheme[1]}://{netloc}{parsed.path}"
    else:
        kwargs["scheme"] = scheme[-1]
        kwargs["host"], kwargs["port"] = netloc.split(":")
        kwargs["port"] = int(kwargs["port"])

    kwargs.update(dict(parse_qsl(parsed.query)))

    return kwargs


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
