# Nemorosa

![Python Version](https://img.shields.io/badge/python-3.11%2B-blue)
[![License](https://img.shields.io/badge/license-gplv3-green)](LICENSE)

Nemorosa is a specialized cross-seeding tool designed specifically for music torrents. Unlike traditional cross-seeding tools that require identical torrents, Nemorosa excels at partial matching and automatic file mapping, enabling cross-seeding from **any source site** to Gazelle-based trackers, making it the most natural and humanfriendly solution for music torrent cross-seeding.

**Key Features:**
- **Natural User Experience**: Automatically reads torrents from your client, finds cross-seeding opportunities, and seamlessly injects matched torrents
- **Advanced Partial Matching**: Handles cases like different block sizes, missing artwork, or modified covers that would fail with traditional tools
- **Automatic File Mapping**: Automatically renames folders and files to match your existing content, including handling zero-width spaces and different Japanese character encodings
- **Wide Site Support**: Supports cross-seeding from **any source site** (including non-Gazelle trackers, public trackers, or any other torrent source) to Gazelle-based target trackers:
  - **GazelleJSONAPI**: RED/OPS/DIC (modern Gazelle with API support)
  - **Gazelle (Legacy)**: LZTR/Libble (legacy Gazelle with parser support)
- **Web Server Mode**: HTTP API and webhook support for integration with other tools and automation
- **Smart Retry System**: Automatically retry failed downloads and track undownloaded torrents
- **Multi-Client Support**: Works with Transmission, qBittorrent, and Deluge
- **Hash-Based Search**: Advanced hash matching with source flag modification for optimal cross-seeding

## Prerequisites

- Python 3.11+
- One of the supported torrent clients with remote access enabled:
  - **Transmission** with RPC enabled
  - **qBittorrent** with Web UI enabled
  - **Deluge** with daemon mode enabled
- Access to Gazelle-based target trackers for cross-seeding (**source sites can be ANY type**):
  - **GazelleJSONAPI**: RED (redacted.sh), OPS (orpheus.network), DIC (dicmusic.com)
  - **Gazelle (Legacy)**: LZTR (lztr.me), Libble (libble.me)
- Valid API key or cookie for target tracker authentication

## Installation

### Using uv

1. Install uv:

**On Windows:**
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

**On macOS/Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

2. Install nemorosa package from github:
```bash
uv tool install git+https://github.com/KyokoMiki/nemorosa
```

### Using Docker

#### Quick Start with Docker Compose

1. Clone the repository:
```bash
git clone https://github.com/KyokoMiki/nemorosa.git
cd nemorosa
```

2. Create a data directory for configuration:
```bash
mkdir -p data
```

3. Create your configuration file:
```bash
# The first run will create a default config file
docker-compose run --rm nemorosa --help
```

4. Edit the configuration file at `data/config.yml` with your settings

5. Start the service:
```bash
docker-compose up -d
```

#### Manual Docker Build

```bash
# Build the image
docker build -t nemorosa .

# Run in server mode
docker run -d \
  --name nemorosa \
  -p 8256:8256 \
  -v $(pwd)/data:/app/data \
  nemorosa --server

# Run in CLI mode
docker run --rm \
  -v $(pwd)/data:/app/data \
  nemorosa --help
```


## Configuration

Nemorosa uses a YAML configuration file for settings. On first run, it will automatically create a default configuration file.

### Configuration File Location

The configuration file is automatically located at:
- **Windows**: `%APPDATA%\nemorosa\config.yml`
- **macOS**: `~/Library/Application Support/nemorosa/config.yml`
- **Linux**: `~/.config/nemorosa/config.yml`
- **Docker**: `/app/data/config.yml` (mounted from host `./data/config.yml`)

You can also specify a custom configuration file path using the `--config` option.

### Configuration Structure

```yaml
# Global settings
global:
  loglevel: info  # Log level: debug, info, warning, error, critical
  no_download: false  # Whether to only check without downloading
  exclude_mp3: true  # Whether to exclude MP3 format files
  check_trackers:  # List of trackers to check, set to null to check all
    - "flacsfor.me"
    - "home.opsfet.ch" 
    - "52dic.vip"
  check_music_only: true  # Whether to check music files only

# Web server settings
server:
  host: null  # Server host address, null means listen on all interfaces
  port: 8256  # Server port
  api_key: "your_api_key_here"  # API key for accessing web interface

# Torrent client configuration
downloader:
  client: "transmission+http://user:pass@localhost:9091/transmission/rpc"
  label: "nemorosa"  # Download label (cannot be empty)

# Target sites for cross-seeding
target_site:
  - server: "https://redacted.sh"
    tracker: "flacsfor.me"
    api_key: "your_api_key_here"
  - server: "https://orpheus.network"
    tracker: "home.opsfet.ch"
    api_key: "your_api_key_here"
  - server: "https://dicmusic.com"
    tracker: "52dic.vip"
    cookie: "your_cookie_here"  # Sites without API support use cookie authentication
```

### Torrent Client URLs

Supported torrent client URL formats:

- **Transmission**: `transmission+http://username:password@host:port/?torrents_dir=/path`
- **qBittorrent**: `qbittorrent+http://username:password@host:port`
- **Deluge**: `deluge://username:password@host:port/?torrents_dir=/path`

### Authentication

You can use either API keys or cookies for tracker authentication:

```yaml
target_site:
  # Using API key
  - server: "https://example.tracker"
    tracker: "example.tracker"
    api_key: "your_api_key_here"
  
  # Using cookies
  - server: "https://example.tracker"
    tracker: "example.tracker"
    cookie: "session=abc123; user=xyz789"
```

## Usage

### Basic Usage

With a properly configured `config.yml` file, simply run:

```bash
nemorosa
```

### Command Line Options

```bash
nemorosa [OPTIONS]
```

### Available Options

| Option | Description | Default |
|--------|-------------|---------|
| `--config` | Path to YAML configuration file | Auto-detected |
| `--client` | Torrent client URL (overrides config) | From config |
| `--no-download` | Only save URLs, don't download torrents | `false` |
| `-r, --retry-undownloaded` | Retry downloading failed torrents | `false` |
| `-s, --server` | Start nemorosa in server mode | `false` |
| `-t, --torrent` | Process a single torrent by infohash | None |
| `--host` | Server host (server mode only) | From config |
| `--port` | Server port (server mode only) | `8256` |
| `-l, --loglevel` | Log verbosity (debug/info/warning/error/critical) | `info` |

### Example Usage

**Basic usage (using configuration file):**
```bash
nemorosa
```

**Using custom configuration file:**
```bash
nemorosa --config /path/to/my-config.yml
```

**Override client URL from command line:**
```bash
nemorosa --client "qbittorrent+http://admin:password@localhost:8080"
```

**Debug mode with verbose output:**
```bash
nemorosa --loglevel debug
```

**Dry run:**
```bash
nemorosa --no-download
```

**Retry downloading previously failed torrents:**
```bash
nemorosa --retry-undownloaded
```

**Process a single torrent by infohash:**
```bash
nemorosa --torrent "abc123def456..."
```

**Start web server mode:**
```bash
nemorosa --server
```

**Start web server on specific host and port:**
```bash
nemorosa --server --host 0.0.0.0 --port 9000
```

**Docker usage examples:**

**Run in server mode with Docker:**
```bash
docker run -d \
  --name nemorosa \
  -p 8256:8256 \
  -v $(pwd)/data:/app/data \
  nemorosa --server
```

**Process torrents with Docker:**
```bash
docker run --rm \
  -v $(pwd)/data:/app/data \
  nemorosa --torrent "abc123def456..."
```

**Using Docker Compose:**
```bash
# Start server
docker-compose up -d
```

## Web Server API

When running in server mode, Nemorosa provides a REST API for integration with other tools:

### Endpoints

- **`GET /`** - Server information and available endpoints
- **`POST /api/webhook`** - Process a single torrent by infohash

### Webhook Usage

Process a torrent by sending a POST request to the webhook endpoint:

```bash
curl -X POST "http://localhost:8256/api/webhook?infoHash=abc123def456..." \
     -H "Authorization: Bearer your_api_key"
```

### API Documentation

When the server is running, visit `http://localhost:8256/docs` for interactive API documentation.

## Features

### Core Capabilities
- **Multi-client support**: Works with Transmission, qBittorrent, and Deluge
- **Multiple tracker support**: Scan multiple private trackers simultaneously
- **Configuration-driven**: YAML-based configuration with automatic setup
- **Cross-platform**: Works on Windows, macOS, and Linux
- **Web server mode**: HTTP API and webhook support for automation
- **Single torrent processing**: Process individual torrents by infohash
- **Smart retry system**: Automatic retry of failed downloads with database tracking

### Music Torrent Specialization
- **Partial Matching**: Handles torrents with different block sizes, missing artwork, or modified covers
- **Automatic File Mapping**: Intelligently renames folders and files to match your existing content
- **Conflict Detection**: Automatically identifies and excludes conflicting files (e.g., different compression formats)
- **Character Encoding Handling**: Resolves issues with zero-width spaces and different Japanese character encodings
- **Universal Source Support**: Works with torrents from **any source site** (Gazelle, non-Gazelle, public trackers, etc.), enabling cross-seeding to Gazelle-based trackers
- **Hash-Based Search**: Advanced torrent hash matching with automatic source flag modification
- **Source Flag Management**: Automatically modifies torrent source flags for optimal cross-seeding

### User Experience
- **Seamless Injection**: One-command operation with automatic torrent injection like IYUU and cross-seed
- **Smart Retry**: Automatically retry failed downloads with database persistence
- **Detailed Logging**: Comprehensive logging with configurable verbosity
- **Natural Workflow**: Avoids the "semi-automatic" cross-seeding experience
- **Web Interface**: REST API and webhook support for integration with other tools
- **Flexible Operation**: Support for both batch processing and single torrent operations


## How It Works

Nemorosa provides a completely automated cross-seeding experience specifically designed for music torrents:

1. **Connect** to your torrent client (Transmission/qBittorrent/Deluge)
2. **Scan** for eligible music torrents from **ANY source** (your existing torrents from any tracker)
3. **Smart Search** across multiple tracker APIs using advanced matching:
   - **Hash-based search**: Direct torrent hash matching
   - **Partial file matching**: Handles different block sizes, missing artwork
   - **Intelligent filename pattern recognition**: Fallback search with cleaned filenames
   - **Content hash verification**: Conflict detection and file size validation
4. **Automatic File Mapping**:
   - Rename folders to match your existing structure
   - Handle filename modifications and character encoding issues
   - Resolve zero-width spaces and Japanese character encoding differences
5. **Download** matching .torrent files
6. **Seamless Injection**: Automatically inject torrents into your client with proper labels
7. **Verify** torrent contents and start seeding automatically
8. **Track** results in database for future reference and retry operations

### Advanced Features

- **Web Server Mode**: HTTP API endpoints for integration with other tools
- **Single Torrent Processing**: Process individual torrents by infohash via CLI or API
- **Smart Retry System**: Automatic retry of failed downloads with full context preservation
- **Multi-site Support**: Simultaneous processing across multiple tracker sites
- **Source Flag Management**: Automatic modification of torrent source flags for optimal cross-seeding

This process eliminates the traditional pain points of music torrent cross-seeding, where manual renaming and file mapping were required, and enables seamless cross-seeding from **any source site** to Gazelle-based trackers.


## First Run Setup

On first run, Nemorosa will create a default configuration file and exit. You need to:

1. Edit the configuration file with your settings
2. Add your torrent client connection details
3. Add your tracker API keys or cookies
4. Run Nemorosa again

### Debug Mode

For detailed troubleshooting information:
```bash
nemorosa --loglevel debug
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the GPL-3.0 License - see the [LICENSE](LICENSE) file for details.
