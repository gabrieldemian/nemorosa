# Nemorosa

![Python Version](https://img.shields.io/badge/python-3.11%2B-blue)
[![License](https://img.shields.io/badge/license-gplv3-green)](LICENSE)

Nemorosa is a specialized cross-seeding tool designed specifically for music torrents. Unlike traditional cross-seeding tools that require identical torrents, Nemorosa excels at partial matching and automatic file mapping, making it the most natural and comprehensive solution for music torrent cross-seeding.

**Key Features:**
- **Natural User Experience**: Automatically reads torrents from your client, finds cross-seeding opportunities, and seamlessly injects matched torrents
- **Advanced Partial Matching**: Handles cases like different block sizes, missing artwork, or modified covers that would fail with traditional tools
- **Automatic File Mapping**: Automatically renames folders and files to match your existing content, including handling zero-width spaces and different Japanese character encodings
- **Wide Site Support**: Works with Gazelle-based music trackers (RED/OPS/DIC) and even NexusPHP sites (U2) for maximum cross-seeding coverage

## Prerequisites

- Python 3.11+
- One of the supported torrent clients with remote access enabled:
  - **Transmission** with RPC enabled
  - **qBittorrent** with Web UI enabled
  - **Deluge** with daemon mode enabled
- Access to Gazelle-based private trackers (RED/OPS/DIC)
- Valid API key or cookie for authentication

## Installation

### Using uv

```bash
uv tool install git+https://github.com/KyokoMiki/nemorosa
```

### Prerequisites

You need to have [uv](https://docs.astral.sh/uv/) installed. Install it with:

```bash
# On Windows
powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# On macOS/Linux
curl -LsSf https://astral.sh/uv/install.sh | sh
```

## Configuration

Nemorosa uses a YAML configuration file for settings. On first run, it will automatically create a default configuration file.

### Configuration File Location

The configuration file is automatically located at:
- **Windows**: `%APPDATA%\nemorosa\config.yml`
- **macOS**: `~/Library/Application Support/nemorosa/config.yml`
- **Linux**: `~/.config/nemorosa/config.yml`

You can also specify a custom configuration file path using the `--config` option.

### Configuration Structure

```yaml
# Global settings
global:
  loglevel: info
  no_download: false
  exclude_mp3: true
  check_trackers:
    - flacsfor.me
    - home.opsfet.ch
    - 52dic.vip
    - open.cd
    - daydream.dmhy.best

# Torrent client configuration
downloader:
  client: "transmission+http://user:pass@localhost:9091/transmission/rpc"
  label: "nemorosa"

# Target sites for cross-seeding
target_site:
  - server: "https://redacted.sh"
    tracker: "flacsfor.me"
    api_key: "your_api_key_here"
  - server: "https://orpheus.network"
    tracker: "home.opsfet.ch"
    api_key: "your_api_key_here"
```

### Torrent Client URLs

Supported torrent client URL formats:

- **Transmission**: `transmission+http://username:password@host:port/transmission/rpc`
- **qBittorrent**: `qbittorrent+http://username:password@host:port`
- **Deluge**: `deluge://username:password@host:port`

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

**Only scan, don't download torrents:**
```bash
nemorosa --no-download
```

**Retry downloading previously failed torrents:**
```bash
nemorosa --retry-undownloaded
```

## Features

### Core Capabilities
- **Multi-client support**: Works with Transmission, qBittorrent, and Deluge
- **Multiple tracker support**: Scan multiple private trackers simultaneously
- **Configuration-driven**: YAML-based configuration with automatic setup
- **Cross-platform**: Works on Windows, macOS, and Linux

### Music Torrent Specialization
- **Partial Matching**: Handles torrents with different block sizes, missing artwork, or modified covers
- **Automatic File Mapping**: Intelligently renames folders and files to match your existing content
- **Conflict Detection**: Automatically identifies and excludes conflicting files (e.g., different compression formats)
- **Character Encoding Handling**: Resolves issues with zero-width spaces and different Japanese character encodings
- **Wide Site Compatibility**: Supports both Gazelle-based trackers (RED/OPS/DIC) and NexusPHP sites (U2)

### User Experience
- **Seamless Injection**: One-command operation with automatic torrent injection like IYUU and cross-seed
- **Smart Retry**: Automatically retry failed downloads
- **Detailed Logging**: Comprehensive logging with configurable verbosity
- **Natural Workflow**: Avoids the "semi-automatic" cross-seeding experience

## Database Storage

Nemorosa uses SQLite database to track:

- Processed torrent hashes to avoid duplicate scans
- Found torrent matches and their mapping
- Failed downloads for retry attempts
- Cross-seeding history and statistics

## How It Works

Nemorosa provides a completely automated cross-seeding experience specifically designed for music torrents:

1. **Connect** to your torrent client (Transmission/qBittorrent/Deluge)
2. **Scan** for eligible music torrents from configured trackers
3. **Smart Search** across multiple tracker APIs using advanced matching:
   - Partial file matching (handles different block sizes, missing artwork)
   - Intelligent filename pattern recognition
   - Content hash verification with conflict detection
4. **Automatic File Mapping**:
   - Rename folders to match your existing structure
   - Handle filename modifications and character encoding issues
   - Resolve zero-width spaces and Japanese character encoding differences
5. **Download** matching .torrent files
6. **Seamless Injection**: Automatically inject torrents into your client with proper labels
7. **Verify** torrent contents and start seeding automatically
8. **Track** results in database for future reference

This process eliminates the traditional pain points of music torrent cross-seeding, where manual renaming and file mapping were required.

## Troubleshooting

### First Run Setup

On first run, Nemorosa will create a default configuration file and exit. You need to:

1. Edit the configuration file with your settings
2. Add your torrent client connection details
3. Add your tracker API keys or cookies
4. Run Nemorosa again

### Common Issues

**Configuration file not found:**
```bash
nemorosa --config /path/to/your/config.yml
```

**Database permissions:**
The database is stored in your user data directory. Ensure you have write permissions.

**Client connection failed:**
- Verify your torrent client is running
- Check the URL format matches your client type
- Ensure remote access is enabled in your client settings

**API authentication failed:**
- Verify your API keys are correct and active
- Check if your IP is whitelisted on the tracker
- For cookie authentication, ensure the cookies are current

### Debug Mode

For detailed troubleshooting information:
```bash
nemorosa --loglevel debug
```

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.

## License

This project is licensed under the GPL-3.0 License - see the [LICENSE](LICENSE) file for details.
