"""Core processing functions for nemorosa."""

import posixpath
import re
import traceback
from typing import Any
from urllib.parse import urlparse

import torf

from . import api, config, db, filecompare, logger
from .torrent_client import ClientTorrentInfo, TorrentClient


class NemorosaCore:
    """Main class for processing torrents and cross-seeding operations."""

    def __init__(self, torrent_client: TorrentClient, target_apis: list[api.GazelleJSONAPI | api.GazelleParser]):
        """Initialize the torrent processor.

        Args:
            torrent_client: Torrent client instance for managing torrents
            target_apis: List of target site API configurations
        """
        self.torrent_client = torrent_client
        self.target_apis = target_apis
        self.database = db.get_database()
        self.stats = {
            "found": 0,
            "downloaded": 0,
            "scanned": 0,
            "cnt_dl_fail": 0,
            "attempted": 0,
            "successful": 0,
            "failed": 0,
            "removed": 0,
        }
        self.logger = logger.get_logger()

    @staticmethod
    def make_filename_query(filename):
        """Generate cleaned search query string from filename.

        Features:
        1. Remove path part, keep only filename
        2. Replace garbled characters with equal-length spaces
        3. Merge consecutive spaces into single space

        Args:
            filename (str): Original filename.

        Returns:
            str: Cleaned filename, returns None if unable to process.
        """

        # Remove path part, keep only filename
        base_filename = posixpath.basename(filename)

        # Replace common garbled characters and special symbols with equal-length spaces
        # Including: question marks, Chinese question marks, consecutive underscores, brackets, etc.
        sanitized_name = base_filename

        # Replace common garbled characters and invisible characters with equal-length spaces
        # Including zero-width spaces, control characters, and other invisible Unicode characters
        sanitized_name = re.sub(
            r'[?？�_\-\.·~`!@#$%^&*+=|\\:";\'<>?,/\u200b\u200c\u200d\u2060\ufeff\u00a0\u180e\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\u0000-\u001f\u007f-\u009f]',
            " ",
            sanitized_name,
        )

        # Finally merge consecutive multiple spaces into single space
        sanitized_name = re.sub(r"\s+", " ", sanitized_name).strip()

        return sanitized_name

    def hash_based_search(
        self,
        *,
        torrent_object: torf.Torrent,
        api: api.GazelleJSONAPI | api.GazelleParser,
    ) -> int:
        """Search for torrent using hash-based search.

        Args:
            torrent_object (torf.Torrent): Torrent object for hash calculation.
            api: API instance for the target site.

        Returns:
            int: Torrent ID if found, -1 otherwise.
        """
        self.logger.debug("Trying hash-based search first")
        try:
            # Get target source flag from API
            target_source_flag = api.source_flag

            source_flags = [target_source_flag, ""]

            # Define possible source flags for the target tracker
            # This should match the logic in fertilizer
            if target_source_flag == "RED":
                source_flags.append("PTH")
            elif target_source_flag == "OPS":
                source_flags.append("APL")

            # Create a copy of the torrent and try different source flags
            for flag in source_flags:
                try:
                    torrent_object.source = flag

                    # Calculate hash
                    torrent_hash = torrent_object.infohash

                    # Search torrent by hash
                    search_result = api.search_torrent_by_hash(torrent_hash)
                    if search_result:
                        self.logger.success(f"Found torrent by hash! Hash: {torrent_hash}")

                        # Get torrent ID from search result
                        torrent_id = search_result["response"]["torrent"]["id"]
                        if torrent_id:
                            tid = int(torrent_id)
                            self.logger.success(f"Found match! Torrent ID: {tid}")
                            return tid
                except Exception as e:
                    self.logger.debug(f"Error calculating hash for source '{flag}': {e}")
                    continue

        except Exception as e:
            self.logger.debug(f"Hash search failed: {e}")

        return -1

    def filename_search(
        self,
        *,
        fdict: dict,
        tsize: int,
        api: api.GazelleJSONAPI | api.GazelleParser,
    ) -> int:
        """Search for torrent using filename-based search.

        Args:
            fdict (dict): File dictionary mapping filename to size.
            tsize (int): Total size of the torrent.
            api: API instance for the target site.

        Returns:
            int: Torrent ID if found, -1 otherwise.
        """
        self.logger.debug("Hash search failed or not available, falling back to filename search")
        # search for the files with top 5 longest name
        tid = -1
        scan_querys = []
        max_fnames = sorted(fdict.keys(), key=lambda fname: len(fname), reverse=True)
        for index, fname in enumerate(max_fnames):
            if index == 0 or posixpath.splitext(fname)[1] in [
                ".flac",
                ".mp3",
                ".dsf",
                ".dff",
                ".m4a",
            ]:
                scan_querys.append(fname)
            if len(scan_querys) >= 5:
                break

        for fname in scan_querys:
            self.logger.debug(f"Searching for file: {fname}")
            fname_query = fname
            try:
                torrents = api.search_torrent_by_filename(fname_query)
            except Exception as e:
                self.logger.error(f"Error searching for file '{fname_query}': {e}")
                continue

            # Record the number of results found
            self.logger.debug(f"Found {len(torrents)} potential matches for file '{fname_query}'")

            # If no results found and it's a music file, try make filename query and search again
            if len(torrents) == 0 and posixpath.splitext(fname)[1] in [
                ".flac",
                ".mp3",
                ".dsf",
                ".dff",
                ".m4a",
            ]:
                fname_query = self.make_filename_query(fname)
                if fname_query != fname:
                    self.logger.debug(
                        f"No results found for '{fname}', trying fallback search with basename: '{fname_query}'"
                    )
                    try:
                        fallback_torrents = api.search_torrent_by_filename(fname_query)
                        if fallback_torrents:
                            torrents = fallback_torrents
                            self.logger.debug(
                                f"Fallback search found {len(torrents)} potential matches for '{fname_query}'"
                            )
                        else:
                            self.logger.debug(f"Fallback search also found no results for '{fname_query}'")
                    except Exception as e:
                        self.logger.error(f"Error in fallback search for file basename '{fname_query}': {e}")

            # Match by total size
            size_match_found = False
            for t in torrents:
                if tsize == t["size"]:
                    tid = t["torrentId"]
                    size_match_found = True
                    self.logger.success(f"Size match found! Torrent ID: {tid} (Size: {tsize})")
                    break

            if size_match_found:
                break

            # Handle cases with too many results
            if len(torrents) > 20:
                self.logger.warning(f"Too many results found for file '{fname_query}' ({len(torrents)}). Skipping.")
                continue

            # Match by file content
            if tid == -1:
                tid = self.match_by_file_content(
                    torrents=torrents,
                    fname_query=fname_query,
                    fname=fname,
                    fdict=fdict,
                    scan_querys=scan_querys,
                    api=api,
                )

            # If match found, exit early
            if tid != -1:
                self.logger.debug(f"Match found with file '{fname}'. Stopping search.")
                break

            self.logger.debug(f"No more results for file '{fname}'")
            if posixpath.splitext(fname)[1] in [".flac", ".mp3", ".dsf", ".dff", ".m4a"]:
                self.logger.debug("Stopping search as music file match is not found")
                break

        return tid

    def match_by_file_content(
        self,
        *,
        torrents: list[dict],
        fname_query: str,
        fname: str,
        fdict: dict,
        scan_querys: list[str],
        api: api.GazelleJSONAPI | api.GazelleParser,
    ) -> int:
        """Match torrents by file content.

        Args:
            torrents (list[dict]): List of torrents to check.
            fname_query (str): Query string for filename matching.
            fname (str): Original filename.
            fdict (dict): File dictionary mapping filename to size.
            scan_querys (list[str]): List of scan queries.
            api: API instance for the target site.

        Returns:
            int: Torrent ID if found, -1 otherwise.
        """
        tid = -1
        self.logger.debug(f"No size match found. Checking file contents for '{fname_query}'")
        for t_index, t in enumerate(torrents, 1):
            self.logger.debug(f"Checking torrent #{t_index}/{len(torrents)}: ID {t['torrentId']}")

            resp = api.torrent(t["torrentId"])
            resp_files = resp.get("fileList", {})

            # Get files in fileList corresponding to fname_query
            fname_query_words = fname_query.split()
            matching_keys = []

            if fname_query == fname:
                matching_keys.append(fname_query)
            else:
                # Check all keys in resp_files
                for key in resp_files:
                    # Check if all words in fname_query are in key
                    if all(word in key for word in fname_query_words):
                        matching_keys.append(key)

                self.logger.debug(
                    f"Found {len(matching_keys)} files matching query '{fname_query}': "
                    f"{matching_keys[:3]}{'...' if len(matching_keys) > 3 else ''}"
                )

            # Check if collected matching keys have file matches
            matched = False
            for matching_key in matching_keys:
                # Check if this key is in our file dictionary and size matches
                if resp_files.get(matching_key, 0) == fdict[fname]:
                    self.logger.debug(f"File size match found for key: {matching_key}")

                    # If it's a music file, match directly
                    if posixpath.splitext(matching_key)[1] in [
                        ".flac",
                        ".mp3",
                        ".dsf",
                        ".dff",
                        ".m4a",
                    ]:
                        tid = t["torrentId"]
                        matched = True
                        self.logger.success(f"Music file match found! Torrent ID: {tid} (File: {matching_key})")
                        break  # Break out of matching_keys loop
                    else:
                        # For non-music files, still need to check music files
                        check_music_file = scan_querys[-1]
                        if resp_files.get(check_music_file, 0) == fdict.get(check_music_file, 0):
                            tid = t["torrentId"]
                            matched = True
                            self.logger.success(f"File match found! Torrent ID: {tid} (File: {matching_key})")
                            break  # Break out of matching_keys loop

            if matched:
                # Check file conflicts
                if filecompare.check_conflicts(fdict, resp_files):
                    self.logger.debug("Conflict detected. Skipping this torrent.")
                    tid = -1  # Reset tid
                    matched = False

                if matched:
                    break  # Break out of torrent traversal loop

        return tid

    def process_torrent_search(
        self,
        *,
        fdict: dict,
        tsize: int,
        scan_source: str,
        local_torrent_name: str,
        api: api.GazelleJSONAPI | api.GazelleParser,
        download_dir: str,
        torrent_object: torf.Torrent | None = None,
    ):
        """Process torrent search and injection.

        Args:
            fdict (dict): File dictionary mapping filename to size.
            tsize (int): Total size of the torrent.
            scan_source (str): Source hash for scanning.
            local_torrent_name (str): Local torrent name.
            api: API instance for the target site.
            download_dir (str): Download directory.
            torrent_object (torf.Torrent, optional): Original torrent object for hash search.

        Returns:
            tuple: (torrent_id, downloaded) - torrent ID and download success status.
        """
        self.stats["scanned"] += 1

        tid = -1
        use_existing_torrent = True

        # Try hash-based search first if torrent object is available
        if torrent_object:
            tid = self.hash_based_search(torrent_object=torrent_object, api=api)

        # If hash search didn't find anything, try filename search
        if tid == -1:
            tid = self.filename_search(fdict=fdict, tsize=tsize, api=api)
            use_existing_torrent = False

        # Handle no match found case
        if tid == -1:
            self.logger.header("No matching torrent found")
            # Get site hostname
            site_host = urlparse(api.server).netloc

            # Record scan result: no matching torrent found
            self.database.add_scan_result(scan_source, local_torrent_name, None, site_host)
            return tid, False

        # Found a match
        self.stats["found"] += 1
        self.logger.success(f"Found match! Torrent ID: {tid}")

        # If found via hash search, modify the existing torrent for the new tracker
        # Otherwise, download the torrent data
        if use_existing_torrent:
            torrent_object.comment = api.get_torrent_url(tid)
            torrent_object.trackers = [api.announce]
            torrent_data = torrent_object.dump()
        else:
            torrent_data = api.download_torrent(tid)
            torrent_object = torf.Torrent.read_stream(torrent_data)

        # Generate file dictionary and rename map
        fdict_torrent = {}
        for f in torrent_object.files:
            fdict_torrent["/".join(f.parts[1:])] = f.size

        rename_map = filecompare.generate_rename_map(fdict, fdict_torrent)

        # Inject torrent and handle renaming
        downloaded = False
        if not config.cfg.global_config.no_download:
            if self.torrent_client.inject_torrent(torrent_data, download_dir, local_torrent_name, rename_map):
                downloaded = True
                self.stats["downloaded"] += 1
                self.logger.success("Torrent injected successfully")
            else:
                self.logger.error(f"Failed to inject torrent: {tid}")
                self.stats["cnt_dl_fail"] += 1
                if self.stats["cnt_dl_fail"] <= 10:
                    self.logger.error(traceback.format_exc())
                    self.logger.error(
                        f"It might because the torrent id {tid} has reached the "
                        f"limitation of non-browser downloading of {api.server}. "
                        f"The failed download info will be saved to database. "
                        "You can download it from your own browser."
                    )
                    if self.stats["cnt_dl_fail"] == 10:
                        self.logger.debug("Suppressing further hinting for .torrent file downloading failures")

        # Get site hostname
        site_host = urlparse(api.server).netloc

        # Record scan result: matching torrent found
        self.database.add_scan_result(scan_source, local_torrent_name, str(tid), site_host)
        if not downloaded:
            torrent_info = {
                "download_dir": download_dir,
                "local_torrent_name": local_torrent_name,
                "rename_map": rename_map,
            }
            self.database.add_undownloaded_torrent(str(tid), torrent_info, site_host)

        return tid, downloaded

    def process_single_torrent_from_client(
        self,
        torrent_name: str,
        torrent_details: ClientTorrentInfo,
    ) -> bool:
        """Process a single torrent from client torrent list.

        Args:
            torrent_name (str): Name of the torrent.
            torrent_details (dict): Torrent details from client.

        Returns:
            bool: True if any target site was successful, False otherwise.
        """

        # Check if torrent has been scanned
        if self.database.is_hash_scanned(torrent_details.hash):
            self.logger.debug(
                "Skipping already scanned torrent: %s (%s)",
                torrent_name,
                torrent_details.hash,
            )
            return False

        # Prepare file list and size
        tsize = torrent_details.total_size
        fdict = {posixpath.relpath(f.name, torrent_name): f.size for f in torrent_details.files}

        # Try to get torrent data from torrent client for hash search
        torrent_object = None
        # Get torrent hash from torrent details
        torrent_hash = torrent_details.hash
        torrent_object = self.torrent_client.get_torrent_object(torrent_hash)

        # Scan and match for each target site
        any_success = False
        existing_target_trackers = set(torrent_details.existing_target_trackers)

        for api_instance in self.target_apis:
            self.logger.debug(f"Trying target site: {api_instance.server} (tracker: {api_instance.tracker_query})")

            # Check if this content already exists on current target tracker
            if api_instance.tracker_query in existing_target_trackers:
                self.logger.debug(f"Content already exists on {api_instance.tracker_query}, skipping")
                continue

            try:
                # Scan and match
                tid, _ = self.process_torrent_search(
                    fdict=fdict,
                    tsize=tsize,
                    scan_source=torrent_details.hash,
                    local_torrent_name=torrent_name,
                    api=api_instance,
                    download_dir=torrent_details.download_dir,
                    torrent_object=torrent_object,  # Pass torrent object for hash search
                )

                if tid != -1:
                    any_success = True
                    self.logger.success(f"Successfully processed on {api_instance.server}")

            except Exception as e:
                self.logger.error(f"Error processing torrent on {api_instance.server}: {e}")
                continue

        return any_success

    def process_torrents(self):
        """Process torrents in client, supporting multiple target sites."""
        self.logger.section("===== Processing Torrents =====")

        # Extract target_trackers from target_apis
        target_trackers = [api_instance.tracker_query for api_instance in self.target_apis]

        # Reset stats for this processing session
        self.stats = {"found": 0, "downloaded": 0, "scanned": 0, "cnt_dl_fail": 0}

        try:
            # Get filtered torrent list
            torrents = self.torrent_client.get_filtered_torrents(target_trackers)
            self.logger.debug("Found %d torrents in client matching the criteria", len(torrents))

            for i, (torrent_name, torrent_details) in enumerate(torrents.items()):
                self.logger.header(
                    "Processing %d/%d: %s (%s)",
                    i + 1,
                    len(torrents),
                    torrent_name,
                    torrent_details.hash,
                )

                # Process single torrent
                any_success = self.process_single_torrent_from_client(
                    torrent_name=torrent_name,
                    torrent_details=torrent_details,
                )

                # Record processed torrents (scan history handled inside scan function)
                if any_success:
                    self.logger.success("Torrent processed successfully on at least one target site")
                else:
                    self.logger.warning("Torrent not found on any target sites")

        except Exception as e:
            self.logger.error("Error processing torrents: %s", e)
            self.logger.error(traceback.format_exc())
        finally:
            self.logger.success("Torrent processing summary:")
            self.logger.success("Torrents scanned: %d", self.stats["scanned"])
            self.logger.success("Matches found: %d", self.stats["found"])
            self.logger.success(".torrent files downloaded: %d", self.stats["downloaded"])
            self.logger.section("===== Torrent Processing Complete =====")

    def retry_undownloaded_torrents(self):
        """Re-download undownloaded torrents."""
        self.logger.section("===== Retrying Undownloaded Torrents =====")

        # Reset retry stats
        retry_stats = {"attempted": 0, "successful": 0, "failed": 0, "removed": 0}

        try:
            # Process undownloaded torrents for each target site
            for api_instance in self.target_apis:
                site_host = urlparse(api_instance.server).netloc
                self.logger.debug(f"Processing undownloaded torrents for site: {api_instance.server}")

                # Get undownloaded torrents for this site
                undownloaded_torrents = self.database.load_undownloaded_torrents(site_host)

                if not undownloaded_torrents:
                    self.logger.debug(f"No undownloaded torrents found for site: {api_instance.server}")
                    continue

                self.logger.info(
                    f"Found {len(undownloaded_torrents)} undownloaded torrents for site: {api_instance.server}"
                )

                for torrent_id, torrent_info in undownloaded_torrents.items():
                    retry_stats["attempted"] += 1
                    self.logger.header(
                        f"Retrying torrent ID: {torrent_id} ({retry_stats['attempted']}/{len(undownloaded_torrents)})"
                    )

                    try:
                        # Download torrent data
                        torrent_data = api_instance.download_torrent(torrent_id)

                        # Get torrent information
                        download_dir = torrent_info.get("download_dir", "")
                        local_torrent_name = torrent_info.get("local_torrent_name", "")
                        rename_map = torrent_info.get("rename_map", {})

                        self.logger.debug(f"Attempting to inject torrent: {local_torrent_name}")
                        self.logger.debug(f"Download directory: {download_dir}")
                        self.logger.debug(f"Rename map: {rename_map}")

                        # Try to inject torrent into client
                        if self.torrent_client.inject_torrent(
                            torrent_data, download_dir, local_torrent_name, rename_map
                        ):
                            retry_stats["successful"] += 1
                            retry_stats["removed"] += 1

                            # Injection successful, remove from undownloaded table
                            self.database.remove_undownloaded_torrent(torrent_id, site_host)
                            self.logger.success(f"Successfully downloaded and injected torrent {torrent_id}")
                            self.logger.success(f"Removed torrent {torrent_id} from undownloaded list")
                        else:
                            retry_stats["failed"] += 1
                            self.logger.error(f"Failed to inject torrent {torrent_id}")

                    except Exception as e:
                        retry_stats["failed"] += 1
                        self.logger.error(f"Error processing torrent {torrent_id}: {e}")
                        continue

        except Exception as e:
            self.logger.error("Error retrying undownloaded torrents: %s", e)
            self.logger.error(traceback.format_exc())
        finally:
            self.logger.success("Retry undownloaded torrents summary:")
            self.logger.success("Torrents attempted: %d", retry_stats["attempted"])
            self.logger.success("Successfully downloaded: %d", retry_stats["successful"])
            self.logger.success("Failed downloads: %d", retry_stats["failed"])
            self.logger.success("Removed from undownloaded list: %d", retry_stats["removed"])
            self.logger.section("===== Retry Undownloaded Torrents Complete =====")

    def process_single_torrent(
        self,
        infohash: str,
    ) -> dict[str, Any]:
        """Process a single torrent by infohash from torrent client.

        Args:
            infohash (str): Infohash of the torrent to process.

        Returns:
            dict: Processing result with status and details.
        """

        try:
            # Extract target_trackers from target_apis
            target_trackers = [api_instance.tracker_query for api_instance in self.target_apis]

            # Get torrent details from torrent client with existing trackers info
            torrent_info = self.torrent_client.get_single_torrent(infohash, target_trackers)

            if not torrent_info:
                return {
                    "status": "error",
                    "message": f"Torrent with infohash {infohash} not found in client",
                    "infohash": infohash,
                }

            # Check if torrent has been scanned
            if self.database.is_hash_scanned(infohash):
                return {
                    "status": "skipped",
                    "message": f"Torrent {infohash} already scanned",
                    "infohash": infohash,
                    "torrent_name": torrent_info.name,
                }

            # Check if torrent already exists on all target trackers
            existing_trackers = set(torrent_info.existing_target_trackers)
            target_tracker_set = set(target_trackers)

            if target_tracker_set.issubset(existing_trackers):
                return {
                    "status": "skipped",
                    "message": f"Torrent already exists on all target trackers: {list(existing_trackers)}",
                    "infohash": infohash,
                    "torrent_name": torrent_info.name,
                    "existing_trackers": list(existing_trackers),
                }

            # Reset stats for this processing session
            self.stats = {"found": 0, "downloaded": 0, "scanned": 0, "cnt_dl_fail": 0}

            # Process the torrent using the same logic as process_single_torrent_from_client
            any_success = self.process_single_torrent_from_client(
                torrent_name=torrent_info.name,
                torrent_details=torrent_info,
            )

            return {
                "status": "success" if any_success else "not_found",
                "message": f"Processed torrent: {torrent_info.name} ({infohash})",
                "infohash": infohash,
                "torrent_name": torrent_info.name,
                "any_success": any_success,
                "stats": self.stats,
                "existing_trackers": list(existing_trackers),
            }

        except Exception as e:
            self.logger.error(f"Error processing single torrent {infohash}: {str(e)}")
            return {"status": "error", "message": f"Error processing torrent: {str(e)}", "infohash": infohash}

    def process_reverse_announce_torrent(
        self,
        torrent_name: str,
        torrent_link: str,
        torrent_data: bytes,
    ) -> dict[str, Any]:
        """Process a single announce torrent for cross-seeding."""
        # TODO: Implement this
        pass
