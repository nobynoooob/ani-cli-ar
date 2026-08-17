<div align="center">

<h2>💖 Support This Open Source Project</h2>
<p>Your support helps maintain the project and keeps the updates coming!</p>
<a href="https://paypal.me/np4abdou">
  <img src="https://img.shields.io/badge/Donate_with_PayPal-00457C?style=for-the-badge&logo=paypal&logoColor=white" alt="Donate with PayPal">
</a>
<br><br><br>

**Lightweight terminal-based anime streaming with Arabic subtitles**

<p align="center">
  <a href="https://github.com/nobynoooob/ani-cli-ar/stargazers">
    <img src="https://img.shields.io/github/stars/nobynoooob/ani-cli-ar?style=for-the-badge" />
  </a>
  <a href="https://github.com/nobynoooob/ani-cli-ar/network">
    <img src="https://img.shields.io/github/forks/nobynoooob/ani-cli-ar?style=for-the-badge" />
  </a>
  <br>
  <a href="https://github.com/nobynoooob/ani-cli-ar/releases">
    <img src="https://img.shields.io/github/v/release/nobynoooob/ani-cli-ar?style=for-the-badge" />
  </a>
  <a href="https://pypi.org/project/ani-cli-arabic">
    <img src="https://img.shields.io/pypi/v/ani-cli-arabic?style=for-the-badge" />
  </a>
  <a href="https://aur.archlinux.org/packages/ani-cli-arabic">
    <img src="https://img.shields.io/aur/version/ani-cli-arabic?style=for-the-badge" />
  </a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/License-GPL--3.0-green?style=for-the-badge" />
</p>

<br>

</div>

---

## 📑 Navigation

[Installation](#-installation) • [Features](#-what-can-you-do) • [How to Use](#-how-to-use) • [Keyboard Shortcuts](#️-keyboard-shortcuts) • [Configuration](#%EF%B8%8F-configuration) • [Contributors](#-contributors) • [License](#-license)

---

## 📦 Installation

### Requirements
Before installing, make sure you have:
- **Python 3.8 or newer** (Python 3.12 recommended)
- **MPV** or **VLC** media player (for streaming)
- **Playwright Chromium** — auto-installed on first stream (`ensure_playwright_chromium`), no manual step needed

### Method 1: One-Line Installer (Recommended)

```bash
curl -fsSL https://raw.githubusercontent.com/nobynoooob/ani-cli-ar/main/install.sh | sh
```

This auto-detects your environment (Linux, macOS, Termux) and installs via `pipx` or `pip`.

### Method 2: Install via pip / pipx

```bash
# Direct from GitHub (always latest)
pip install git+https://github.com/nobynoooob/ani-cli-ar.git

# Or from PyPI (stable releases)
pip install ani-cli-ar

# Or with pipx (isolated environment, recommended)
pipx install ani-cli-ar
```

Launch the app:
```bash
ani-cli-ar
```

To update:
```bash
pip install --upgrade ani-cli-ar
```

### Method 3: Arch Linux (AUR)

```bash
yay -S ani-cli-arabic
# or
paru -S ani-cli-arabic
```

### Method 4: Pre-built Executables (Linux / Windows)

Grab the standalone binaries from the [releases page](https://github.com/nobynoooob/ani-cli-ar/releases):
- `ani-cli-ar-cli-linux.tar.gz` — extract, then `./ani-cli-ar-cli` (or `./install.sh`)
- `ani-cli-ar-cli-windows.zip` — extract and run `ani-cli-ar-cli-windows.exe`

No Python needed for the pre-built executables; only **mpv** (or VLC) is required.

### Method 5: From Source (Development)

```bash
git clone https://github.com/nobynoooob/ani-cli-ar.git
cd ani-cli-ar
pip install -e .
ani-cli-ar
```

---

## 🎯 What Can You Do?

Here's everything this terminal app offers:

### Streaming & Playback
- **Multiple Quality Options**: Watch in 1080p, 720p, or 480p depending on your internet speed
- **Batch Download**: Download multiple episodes at once to watch offline
- **Trailer Support**: Watch YouTube trailers before committing to an anime
- **Resume from History**: Pick up exactly where you left off
- **mpv/VLC Support**: Choose your preferred media player (buffer/caching flags applied for slow connections)

### Discovery & Browsing
- **Search Anime**: Find any anime or anime movie by name (English, Japanese, and Arabic titles)
- **Trending Now**: See what's currently popular
- **Top Rated**: Browse the highest-rated anime of all time
- **Browse by Genre**: Filter by Action, Romance, Isekai, and 12 other genres
- **Browse by Studio**: Find anime from Toei Animation, MAPPA, Ufotable, and 20+ more studios
- **Latest Releases**: Stay updated with the newest anime

### English + Arabic Tracks
- **English**: multi-provider chain — Miruro, HiAnime, AllAnime, API, Mkissa, GogoAnime (provider chain with per-step failure isolation)
- **Arabic**: dedicated Arabic API pipeline (`AnimeAPI`) with quality selection and Arabic subtitle tracks

### Personal Library
- **Watch History**: Keep track of everything you've watched with timestamps
- **Favorites System**: Bookmark your favorite anime for quick access
- **Episode Tracking**: The app remembers which episode you're on

### Interface & Experience
- **Rich TUI**: Beautiful terminal interface built with the Rich library
- **17 Color Themes**: blue, red, green, purple, cyan, yellow, pink, orange, teal, magenta, lime, coral, lavender, gold, mint, rose, sunset
- **Discord Rich Presence**: Show what you're watching on Discord with anime posters
- **Smooth Navigation**: Intuitive keyboard controls
- **Minimal CLI Mode**: `--interactive "Naruto"` for quick searches (also auto-falls back when the terminal is too narrow)

### Technical Features
- **Zero Ads**: Clean streaming experience
- **Automatic Updates**: Built-in version checker notifies you of new releases (can be turned off)
- **Dependency Auto-installer**: Automatically checks and installs missing dependencies
- **Cross-platform**: Works on Linux, Windows, and macOS (Termux supported)

---

## 🎮 How to Use

1. **Launch the app**: run `ani-cli-arabic` or `ani-cli-ar`
2. **Browse or Search**: use the main menu to search, view trending, or browse genres
3. **Select an Anime**: navigate with arrow keys and press Enter
4. **Pick an Episode**: choose which episode to watch
5. **Select Quality**: pick your preferred video quality
6. **Enjoy**: MPV (or VLC) will launch and start streaming

You can also use interactive mode for quick searches:
```bash
ani-cli-ar -i "One Piece"
```

---

## ⌨️ Keyboard Shortcuts

| Key | What it Does |
|-----|--------------|
| **↑ / ↓** | Navigate through lists |
| **Enter** | Select/Confirm choice |
| **G** | Jump directly to an episode number |
| **B** | Go back to previous screen |
| **Q / Esc** | Quit the application |
| **Space** | Pause/Resume video (in player) |
| **← / →** | Rewind/Forward 5 seconds |
| **F** | Toggle fullscreen |

---

## ⚙️ Configuration

Settings are stored locally in `~/.ani-cli-arabic/database/config.json`

Access the settings menu from the main screen to customize:

- **Default Quality**: 1080p, 720p, or 480p
- **Media Player**: MPV or VLC
- **Auto-next Episode**: Toggle automatic episode continuation
- **Discord Rich Presence**: Show or hide Discord activity
- **Theme Color**: Pick from 17 color schemes
- **Analytics**: Opt-in/out of anonymous usage stats (auto-enabled by default)
- **Update Checking**: Toggle automatic update notifications

You can also manually edit the config file if you prefer.

---

## 🔧 Build & Release (for maintainers)

Build the standalone CLI executable with `build_cli.py` (PyInstaller, GUI frameworks excluded):

```bash
python build_cli.py                          # dist/ani-cli-ar-cli
python build_cli.py --zip                    # also produce a portable .zip
python build_cli.py --exclude-module numpy   # extra exclusions
```

Releases are built automatically by `.github/workflows/build.yml` on `v*` tag pushes
(`ani-cli-ar-cli-linux.tar.gz`, `ani-cli-ar-cli-windows.zip`). The Playwright
Chromium browser is **not** bundled — it downloads on first use.

---

## 👥 Contributors

[![Contributors](https://contrib.rocks/image?repo=nobynoooob/ani-cli-ar)](https://github.com/nobynoooob/ani-cli-ar/graphs/contributors)

**Key Contributors:**
- [@np4abdou1](https://github.com/np4abdou1) - Creator and main developer
- [@Anas-Tou](https://github.com/Anas-Tou) - Contributor

Want to contribute? Feel free to open issues or submit pull requests!

---

## 📄 License

This project is licensed under the **GNU General Public License v3.0**.

You're free to use, modify, and distribute this software under the terms of the GPL-3.0 license. See the [LICENSE](LICENSE) file for the full legal text.

**In simple terms:**
- ✅ Use it for personal or commercial purposes
- ✅ Modify the source code
- ✅ Distribute it to others
- ⚠️ Any modifications must also be open source under GPL-3.0
- ⚠️ Include the original copyright notice

---

<div align="center">

### ⚠️ Important Notice

> [! CAUTION]
> **By using this software you understand:**
>
> - Anonymous usage statistics are collected for the GitHub page stats banner (can be disabled in settings)
> - The project is licensed under GPL-3.0 — see [LICENSE](LICENSE) for details
> - We do not host any content; all streams are from third-party sources
> - This tool is for personal use and educational purposes only

</div>

---

<br>

Made with ❤️ by the anime community

[⭐ Star this repo](https://github.com/nobynoooob/ani-cli-ar) | [🐛 Report bugs](https://github.com/nobynoooob/ani-cli-ar/issues) | [💬 Discussions](https://github.com/nobynoooob/ani-cli-ar/discussions)
