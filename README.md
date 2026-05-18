# Musiced

A minimal Windows desktop app that downloads music from public URLs (YouTube, SoundCloud, Bandcamp, etc.) as lossless FLAC files with embedded cover art and metadata. Built with PyQt6 and yt-dlp. Companion to the [lucyna.dev/music](https://lucyna.dev/music) web player.

## Features

- Paste any URL yt-dlp supports and get a tagged FLAC
- YouTube playlists auto-expand into individual rows
- 3 downloads run in parallel, failed jobs auto-retry twice, existing files are skipped
- Embedded cover art + metadata via ffmpeg postprocessors
- Persistent queue across restarts
- Bundles `ffmpeg.exe` and `ffprobe.exe` &mdash; end users do not need a separate ffmpeg install

## Download

Grab the latest `Musiced.exe` from the [Releases page](https://github.com/ethanoeyna/musiced/releases). No install required &mdash; just double-click to run.

## Run from source

Requires Python 3.14+ and [uv](https://docs.astral.sh/uv/).

```sh
uv sync
uv run musiced
```

## Build a standalone .exe

From PowerShell:

```powershell
./build.ps1
```

The build produces a single-file `dist/Musiced.exe` (~200 MB &mdash; bundled ffmpeg and ffprobe account for most of it). Drop it on any modern Windows machine and run it. Config and queue state are written to a `data/` folder next to the .exe.

## Out of scope

DRM platforms (Spotify, Apple Music, Tidal) are not and will not be supported. Playback is the [lucyna.dev/music](https://lucyna.dev/music) web player's job &mdash; Musiced just produces the files.

## License

MIT &mdash; see [LICENSE](LICENSE). Bundled ffmpeg is LGPL ([ffmpeg.org](https://ffmpeg.org/)).
