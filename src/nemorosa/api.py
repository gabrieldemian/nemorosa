import html
import json
import time
from urllib.parse import parse_qs, urljoin, urlparse

import humanfriendly
import msgspec
import requests
from bs4 import BeautifulSoup, Tag

from . import logger


class LoginException(Exception):
    pass


class RequestException(Exception):
    pass


class GazelleBase:
    """Base class for Gazelle API, containing common attributes and methods."""

    def __init__(self, server):
        self.session = requests.Session()
        self.session.headers = {
            "Accept-Charset": "utf-8",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko)"
            " Chrome/137.0.0.0 Safari/537.36 Edg/137.0.0.0",
        }
        self.server = server
        self.authkey = None
        self.passkey = None
        self.last_request_time = 0
        self.logger = logger.get_logger()

        spec = TRACKER_SPECS[server]
        self.interval = spec.interval + 0.1
        self.source_flag = spec.source_flag
        self.tracker_url = spec.tracker_url
        self.tracker_query = spec.tracker_query

    @property
    def announce(self):
        return f"{self.tracker_url}/{self.passkey}/announce"

    def wait(self):
        """Ensure request interval time."""
        now = time.monotonic()
        elapsed = now - self.last_request_time
        if elapsed < self.interval:
            sleep_time = self.interval - elapsed
            self.logger.debug(f"Sleep for {sleep_time:.2f} seconds")
            time.sleep(sleep_time)
        self.last_request_time = time.monotonic()

    def torrent(self, torrent_id):
        """Get torrent object - subclasses need to implement specific request logic.

        Args:
            torrent_id: The ID of the torrent to retrieve.

        Returns:
            dict: Torrent object data, empty dict on error.
        """
        torrent_object = {}
        self.logger.debug(f"Getting torrent by id: {torrent_id}")
        try:
            torrent_lookup = self._get_torrent_data(torrent_id)
        except Exception as e:
            self.logger.error(f"Failed to get torrent by id {torrent_id}. Error: {e}")
            return torrent_object  # return empty dict on error

        torrent_lookup_status = torrent_lookup.get("status", None)
        if torrent_lookup_status == "success":
            self.logger.debug(f"Torrent lookup successful for id: {torrent_id}")
            torrent_object = torrent_lookup["response"]["torrent"]
            torrent_object["fileList"] = self.parse_file_list(torrent_object.get("fileList", ""))
        else:
            self.logger.error(f"Torrent lookup failed for id: {torrent_id}. Status: {torrent_lookup_status}")
        return torrent_object

    def _get_torrent_data(self, torrent_id):
        """Get torrent data - subclasses must implement.

        Args:
            torrent_id: The ID of the torrent to retrieve.

        Returns:
            dict: Response data from the API.

        Raises:
            NotImplementedError: Always raised for base class.
        """
        raise NotImplementedError("Subclasses must implement _get_torrent_data")

    def parse_file_list(self, file_list_str):
        """Parse the file list from a torrent object.

        The file list is expected to be a string with entries separated by '|||'.
        Each entry is in the format 'filename{{{filesize}}}'.

        Args:
            file_list_str (str): Raw file list string from torrent object.

        Returns:
            dict: Dictionary mapping filename to file size.
        """
        file_list = {}
        if file_list_str:
            self.logger.debug("Parsing file list")
            # split the string into individual entries
            entries = file_list_str.split("|||")
            for entry in entries:
                # split filename and filesize
                parts = entry.split("{{{")
                if len(parts) == 2:
                    filename = html.unescape(parts[0].strip())
                    filesize = parts[1].removesuffix("}}}").strip()
                    file_list[filename] = int(filesize)
                else:
                    self.logger.warning(f"Malformed entry in file list: {entry}")
        else:
            self.logger.warning("File list is empty or None")

        return file_list

    def download_torrent(self, torrent_id):
        """Download a torrent by its ID.

        Args:
            torrent_id: The ID of the torrent to download.

        Returns:
            bytes: The content of the torrent file with potentially modified source flag,
                  or None if download fails.
        """
        max_retries = 8
        for attempt in range(max_retries):
            try:
                self.wait()
                response = self._download_torrent_response(torrent_id)
                if response.status_code != 200:
                    self.logger.error(f"Status of request is {response.status_code}. Aborting...")
                    self.logger.error(f"Response content: {response._content}")
                    raise RequestException(f"HTTP {response.status_code}: {response._content}")

                self.logger.debug(f"Torrent {torrent_id} downloaded successfully")
                return response.content
            except Exception as e:
                if attempt < max_retries - 1:
                    self.logger.warning(f"Error downloading torrent: {e}, retrying ({attempt + 1}/{max_retries})...")
                    time.sleep(2)
                else:
                    self.logger.error(f"Failed to download torrent after {max_retries} attempts: {e}")
                    return None

    def _download_torrent_response(self, torrent_id):
        """Get download torrent response - subclasses must implement.

        Args:
            torrent_id: The ID of the torrent to download.

        Returns:
            requests.Response: HTTP response object.

        Raises:
            NotImplementedError: Always raised for base class.
        """
        raise NotImplementedError("Subclasses must implement _download_torrent_response")

    def get_torrent_url(self, torrent_id):
        """Get the permalink for a torrent by its ID.

        Args:
            torrent_id: The ID of the torrent.

        Returns:
            str: Torrent permalink.
        """
        return f"{self.server}/torrents.php?torrentid={torrent_id}"

    def get_torrent_link(self, torrent_id):
        """Get the direct download link for a torrent by its ID.

        Args:
            torrent_id: The ID of the torrent.

        Returns:
            str: Direct download URL for the torrent.
        """
        return (
            f"{self.server}/torrents.php?action=download"
            f"&id={torrent_id}"
            f"&authkey={self.authkey}"
            f"&torrent_pass={self.passkey}"
        )

    def search_torrent_by_filename(self, filename):
        """Search torrents by filename - subclasses must implement specific logic.

        Args:
            filename (str): Filename to search for.

        Returns:
            list: List of matching torrent objects.

        Raises:
            NotImplementedError: Always raised for base class.
        """
        raise NotImplementedError("Subclasses must implement search_torrent_by_filename")

    def search_torrent_by_hash(self, torrent_hash):
        """Search torrent by hash - subclasses must implement specific logic.

        Args:
            torrent_hash (str): Torrent hash to search for.

        Returns:
            dict: Search result with torrent information, or None if not found.

        Raises:
            NotImplementedError: Always raised for base class.
        """
        raise NotImplementedError("Subclasses must implement search_torrent_by_hash")

    def ajax(self, action, **kwargs):
        """Make an AJAX request at a given action page.

        Args:
            action (str): The action to perform.
            **kwargs: Additional parameters for the request.

        Returns:
            dict: JSON response from the server.

        Raises:
            RequestException: If the request fails.
        """
        ajaxpage = self.server + "/ajax.php"
        params = {"action": action}
        if self.authkey:
            params["auth"] = self.authkey
        params.update(kwargs)

        self.wait()  # Respect rate limit

        r = self.session.get(ajaxpage, params=params, allow_redirects=False)
        try:
            json_response = r.json()
            return json_response
        except ValueError as e:
            raise RequestException from e

    def request(self, url, params=None, method="GET", data=None):
        """Send HTTP request and return response.

        Args:
            url (str): Request URL.
            params (dict, optional): Query parameters.
            method (str): HTTP method. Defaults to "GET".
            data (dict, optional): Request body data.

        Returns:
            requests.Response: HTTP response object.

        Raises:
            ValueError: If unsupported HTTP method is used.
        """
        self.wait()
        full_url = urljoin(self.server, url)

        if method == "GET":
            response = self.session.get(full_url, params=params)
        elif method == "POST":
            response = self.session.post(full_url, data=data, params=params)
        else:
            raise ValueError("Unsupported HTTP method")
        return response


class GazelleJSONAPI(GazelleBase):
    def __init__(self, api_key=None, cookies=None, server=None):
        super().__init__(server)

        # Add API key to headers (if provided)
        if api_key:
            self.session.headers["Authorization"] = api_key

        self.auth_method = "api_key" if api_key else "cookies"

        if api_key is None and cookies:
            self.session.cookies = cookies

        try:
            self._auth()
        except RequestException as e:
            self.logger.error(f"Failed to authenticate: {e}")

    def _auth(self):
        """Get auth key from server.

        Raises:
            RequestException: If authentication fails.
        """
        accountinfo = self.ajax("index")
        self.authkey = accountinfo["response"]["authkey"]
        self.passkey = accountinfo["response"]["passkey"]

    def logout(self):
        """Log out user."""
        logoutpage = self.server + "/logout.php"
        params = {"auth": self.authkey}
        self.session.get(logoutpage, params=params, allow_redirects=False)

    def _get_torrent_data(self, torrent_id):
        """Implement the base class abstract method.

        Args:
            torrent_id: The ID of the torrent to retrieve.

        Returns:
            dict: Response data from the torrent API.
        """
        return self.ajax("torrent", id=torrent_id)

    def search_torrent_by_filename(self, filename):
        params = {"filelist": filename}
        try:
            response = self.ajax("browse", **params)
            # Log API response status
            if response.get("status") != "success":
                self.logger.warning(f"API failure for file '{filename}': {json.dumps(response, ensure_ascii=False)}")
                return []
            else:
                self.logger.debug(f"API search successful for file '{filename}'")

            # Process search results
            torrents = []
            for group in response["response"]["results"]:
                if "torrents" in group:
                    torrents.extend(group["torrents"])

            return torrents
        except Exception as e:
            self.logger.error(f"Error searching for torrent by filename '{filename}': {e}")
            return []

    def search_torrent_by_hash(self, torrent_hash):
        """Search torrent by hash using the torrent API.

        Args:
            torrent_hash (str): Torrent hash to search for.

        Returns:
            dict: Search result with torrent information, or None if not found.
        """
        try:
            response = self.ajax("torrent", hash=torrent_hash)
            if response.get("status") == "success":
                self.logger.debug(f"Hash search successful for hash '{torrent_hash}'")
                return response
            else:
                self.logger.debug(f"No torrent found matching hash '{torrent_hash}'")
                return None
        except Exception as e:
            self.logger.error(f"Error searching for torrent by hash '{torrent_hash}': {e}")
            return None

    def _download_torrent_response(self, torrent_id):
        """Implement the base class abstract method - get download torrent response.

        Args:
            torrent_id: The ID of the torrent to download.

        Returns:
            requests.Response: HTTP response object.
        """
        if self.auth_method == "api_key":
            ajaxpage = self.server + "/ajax.php"
            return self.session.get(ajaxpage, params={"action": "download", "id": torrent_id})
        else:
            torrent_link = self.get_torrent_link(torrent_id)
            return self.session.get(torrent_link)


class GazelleParser(GazelleBase):
    def __init__(self, cookies=None, server=None, api_key=None):
        super().__init__(server)

        if cookies:
            self.session.cookies.update(cookies)
            self.logger.debug("Using provided cookies")
        else:
            self.logger.warning("No cookies provided")

    def _auth(self):
        """Get authkey and passkey from server by performing a blank search.

        Raises:
            RequestException: If authentication fails.
        """
        try:
            self.search_torrent_by_filename("")
        except RequestException as e:
            self.logger.error(f"Failed to authenticate: {e}")

    def search_torrent_by_filename(self, filename):
        """Execute search and return torrent list.

        Args:
            filename (str): Filename to search for.

        Returns:
            list: List containing torrent information.
        """
        params = {"action": "advanced", "filelist": filename}

        response = self.request("torrents.php", params=params)
        return self.parse_search_results(response.text)

    def search_torrent_by_hash(self, torrent_hash):
        """Search torrent by hash using the torrent API.

        Args:
            torrent_hash (str): Torrent hash to search for.

        Returns:
            dict: Search result with torrent information, or None if not found.
        """
        try:
            response = self.ajax("torrent", hash=torrent_hash)
            if response.get("status") == "success":
                self.logger.debug(f"Hash search successful for hash '{torrent_hash}'")
                return response
            else:
                self.logger.debug(f"No torrent found matching hash '{torrent_hash}'")
                return None
        except Exception as e:
            self.logger.error(f"Error searching for torrent by hash '{torrent_hash}': {e}")
            return None

    def parse_search_results(self, html):
        """Parse search results page.

        Args:
            html (str): HTML content of the search results page.

        Returns:
            list: List of parsed torrent information.
        """
        soup = BeautifulSoup(html, "html.parser")
        torrents = []

        # Find all torrents under albums
        torrent_rows = soup.select("tr.group_torrent")
        for torrent_row in torrent_rows:
            torrent = self.parse_torrent_row(torrent_row)
            if torrent:
                torrents.append(torrent)

        return torrents

    def parse_torrent_row(self, row: Tag):
        """Parse single torrent row.

        Args:
            row (Tag): BeautifulSoup Tag object representing a torrent row.

        Returns:
            dict: Parsed torrent information, or None if parsing fails.
        """
        try:
            # Get download link
            download_link = row.select_one('a[href^="torrents.php?action=download&id="]')
            if not download_link:
                return None

            # Ensure href is a string
            href = download_link.get("href")
            if not href or not isinstance(href, str):
                return None

            parsed_url = urlparse(href)
            query_params = parse_qs(parsed_url.query)
            torrent_id = query_params.get("id", [None])[0]
            if not torrent_id:
                return None

            self.authkey = query_params.get("authkey", [None])[0]
            self.passkey = query_params.get("torrent_pass", [None])[0]

            full_download_url = urljoin(self.server, href)

            # Get torrent size
            size_cell = row.select("td")[3]  # Column 4 is size
            size_text = size_cell.get_text(strip=True).replace(",", "")
            size = humanfriendly.parse_size(size_text, binary=True)

            # Get title
            title_node = row.select_one("td:nth-child(2) > div > a")
            title = title_node.get_text(strip=True) if title_node else f"Torrent {torrent_id}"

            # Get torrent type
            format_nodes = row.select("td:nth-child(2) > div > span")
            formats = [span.get_text(strip=True) for span in format_nodes] if format_nodes else []

            # Get torrent info
            info_nodes = row.select("td:nth-child(2) > div > span.small")
            info_text = info_nodes[0].get_text(strip=True) if info_nodes else ""

            return {
                "torrentId": torrent_id,
                "download_link": full_download_url,
                "size": size,
                "title": title,
                "formats": formats,
                "info": info_text,
            }
        except Exception as e:
            self.logger.error(f"Error parsing torrent row: {e}")
            return None

    def _get_torrent_data(self, torrent_id):
        """Implement the base class abstract method.

        Args:
            torrent_id: The ID of the torrent to retrieve.

        Returns:
            dict: Response data from the torrent API.
        """
        return self.ajax("torrent", id=torrent_id)

    def _download_torrent_response(self, torrent_id):
        """Implement the base class abstract method - get download torrent response.

        Args:
            torrent_id: The ID of the torrent to download.

        Returns:
            requests.Response: HTTP response object.
        """
        download_url = self.get_torrent_link(torrent_id)
        return self.request(download_url)


class TrackerSpec(msgspec.Struct):
    """Predefined Tracker specification."""

    api_type: type[GazelleJSONAPI] | type[GazelleParser]
    interval: int
    source_flag: str
    tracker_url: str
    tracker_query: str


TRACKER_SPECS = {
    "https://redacted.sh": TrackerSpec(
        api_type=GazelleJSONAPI,
        interval=1,
        source_flag="RED",
        tracker_url="https://flacsfor.me",
        tracker_query="flacsfor.me",
    ),
    "https://orpheus.network": TrackerSpec(
        api_type=GazelleJSONAPI,
        interval=2,
        source_flag="OPS",
        tracker_url="https://home.opsfet.ch",
        tracker_query="home.opsfet.ch",
    ),
    "https://dicmusic.com": TrackerSpec(
        api_type=GazelleJSONAPI,
        interval=2,
        source_flag="DICMusic",
        tracker_url="https://tracker.52dic.vip",
        tracker_query="tracker.52dic.vip",
    ),
    "https://libble.me": TrackerSpec(
        api_type=GazelleParser,
        interval=4,
        source_flag="LENNY",
        tracker_url="https://tracker.libble.me:34443",
        tracker_query="tracker.libble.me",
    ),
    "https://lztr.me": TrackerSpec(
        api_type=GazelleParser,
        interval=4,
        source_flag="LZTR",
        tracker_url="https://tracker.lztr.me:34443",
        tracker_query="tracker.lztr.me",
    ),
}


def get_api_instance(server: str, cookies: dict | None = None, api_key: str | None = None):
    """Get appropriate API instance based on server address.

    Args:
        server (str): Server address.
        cookies (dict | None): Optional cookies.
        api_key (str | None): Optional API key.

    Returns:
        GazelleBase: API instance.

    Raises:
        ValueError: If unsupported server is provided.
    """
    if server not in TRACKER_SPECS:
        raise ValueError(f"Unsupported server: {server}")

    api_class = TRACKER_SPECS[server].api_type

    return api_class(cookies=cookies, api_key=api_key, server=server)
