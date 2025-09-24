# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased](https://github.com/KyokoMiki/nemorosa/compare/0.1.0...HEAD)

## [0.1.0](https://github.com/KyokoMiki/nemorosa/compare/0.0.1...0.1.0) - 2025-09-24

### Added

- **Full Search**: Search all torrents in torrent client
- **Single Torrent Search**: Process individual torrents by infohash for cross-seeding
- **Advanced Partial Matching**: Handle cases like different block sizes, missing artwork, or modified covers
- **Automatic File Mapping**: Automatically rename folders and files to match existing content
- **Torrent Injection**: Seamlessly inject matched torrents into supported clients
- **Web Server**: HTTP API and webhook support for integration with other tools and automation
- **Scheduled Jobs**: Automated search and cleanup tasks with configurable intervals
- **Announce Matching**: Automatically match cross-seeds from IRC announces or RSS feeds
- **Triggering Searches**: Enable immediate cross-seed searches when torrents finish downloading
- **Post Process**: Automated post-processing of previous injected torrents
- **Retry Undownloaded**: Retry failed downloads and undownloaded torrents

**Full Changelog**: https://github.com/KyokoMiki/nemorosa/commits/0.1.0
