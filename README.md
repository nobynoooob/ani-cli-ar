<div align="center">

<h1 align="center">ani-cli-ar</h1>

<p align="center">
  <b>Feature-packed Arabic anime streaming tool with a lightning-fast CLI and a standalone Windows GUI.</b>
</p>

<h2>💖 Support This Project</h2>
<p>Your support helps maintain the project and keeps the updates coming!</p>
<a href="YOUR_DONATION_LINK_HERE">
  <img src="https://img.shields.io/badge/Donate-00457C?style=for-the-badge&logo=paypal&logoColor=white" alt="Donate">
</a>
<br><br>

<p align="center">
  <a href="https://github.com/nobynoooob/ani-cli-ar/stargazers">
    <img src="https://img.shields.io/github/stars/nobynoooob/ani-cli-ar?style=for-the-badge&color=blue" />
  </a>
  <a href="https://github.com/nobynoooob/ani-cli-ar/network">
    <img src="https://img.shields.io/github/forks/nobynoooob/ani-cli-ar?style=for-the-badge&color=blue" />
  </a>
  <br>
  <a href="https://github.com/nobynoooob/ani-cli-ar/releases">
    <img src="https://img.shields.io/github/v/release/nobynoooob/ani-cli-ar?style=for-the-badge&color=success" />
  </a>
  <a href="https://pypi.org/project/ani-cli-ar">
    <img src="https://img.shields.io/pypi/v/ani-cli-ar?style=for-the-badge" />
  </a>
  <a href="https://aur.archlinux.org/packages/ani-cli-ar">
    <img src="https://img.shields.io/aur/version/ani-cli-ar?style=for-the-badge" />
  </a>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.8+-3776AB?style=for-the-badge&logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Platform-Windows_%7C_Linux_%7C_macOS-lightgrey?style=for-the-badge" />
  <img src="https://img.shields.io/badge/License-GPL--3.0-green?style=for-the-badge" />
</p>

<p>لإختيار اللغة العربية اضغط على الزر: </p>
<a href="README.ar.md">
  <img src="https://img.shields.io/badge/Language-Arabic-green?style=for-the-badge&logo=google-translate&logoColor=white" alt="Arabic">
</a>

</div>

---

## 📸 Screenshots

*(Drag and drop a screenshot or GIF of your new Windows GUI here)*

---

## 🚀 Quick Download & Installation

### Option 1: Standalone Windows GUI (Recommended for Windows)
If you want an easy desktop app experience without touching the terminal or installing Python:
1. Go to the [Releases Page](https://github.com/nobynoooob/ani-cli-ar/releases/latest).
2. Download **`ani-cli-ar-v1.9.6-windows-x86_64.zip`** (or `ani-cli-ar-gui.exe`).
3. Extract and double-click to run! *(MPV and Playwright runtime assets are fully bundled).*

---

### Option 2: Terminal CLI (Linux, macOS, Termux)

#### Requirements
- **Python 3.8 or newer** (Python 3.12 recommended)
- **MPV** or **VLC** media player

#### One-Line Installer
```bash
curl -fsSL https://raw.githubusercontent.com/nobynoooob/ani-cli-ar/main/install.sh | sh
```

#### Install via pipx / pip
```bash
# Recommended isolated environment
pipx install ani-cli-ar

# Or direct from PyPI
pip install ani-cli-ar
```

Launch the CLI:
```bash
ani-cli-ar
```

#### Arch Linux (AUR)
```bash
yay -S ani-cli-ar
# or
paru -S ani-cli-ar
```

---

## 🎯 What Can You Do?

### Streaming & Playback
- **Multiple Quality Options**: Watch in 1080p, 720p, or 480p
- **Arabic Subtitles Support**: Built-in AR SUB pipeline and customized scrapers
- **Batch Download**: Download multiple episodes at once for offline viewing
- **Resume from History**: Pick up right where you left off

### Discovery & Browsing
- **Smart Search**: Find anime and movies by English, Japanese, or Arabic titles
- **Trending & Top Rated**: Explore popular and highest-rated shows instantly
- **Genre & Studio Filters**: Filter by Action, Romance, Isekai, MAPPA, Ufotable, and more

### Interface & Customization
- **Dual Interface**: Use the gorgeous Rich Terminal TUI or the native Desktop GUI
- **Color Themes**: Choose from 17 custom color schemes
- **Discord Rich Presence**: Show off what you're watching on Discord with anime posters

---

## ⌨️ Keyboard Shortcuts (CLI)

| Key | Action |
|-----|--------|
| **↑ / ↓** | Navigate lists |
| **Enter** | Select / Confirm |
| **G** | Jump to specific episode |
| **B** | Go back |
| **Q / Esc** | Quit application |
| **Space** | Pause / Resume (in player) |

---

## ⚙️ Configuration

Settings are stored locally in `~/.ani-cli-arabic/database/config.json`. You can configure:
- Default video quality & player (MPV / VLC)
- Discord Rich Presence toggle
- Theme color preferences
- Automatic update checks

---

## 👥 Contributors

**Lead Developer:**
- [@nobynoooob](https://github.com/nobynoooob) - Creator of the standalone GUI and Windows builds.

Want to contribute? Feel free to open issues or submit pull requests!

---

## 🔧 Build & Release (For Maintainers)

### Windows GUI Build
The Windows executable is built automatically via GitHub Actions upon pushing a new `v*` tag. 

To build manually on Windows:
```bash
python build_desktop.py --bundle-mpv --bundle-browser --zip
```

---

## 🌟 Star History

<a href="https://www.star-history.com/#nobynoooob/ani-cli-ar&type=date&legend=top-left">
 <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://api.star-history.com/svg?repos=nobynoooob/ani-cli-ar&type=date&theme=dark&legend=top-left" />
    <source media="(prefers-color-scheme: light)" srcset="https://api.star-history.com/svg?repos=nobynoooob/ani-cli-ar&type=date&legend=top-left" />
    <img alt="Star History Chart" src="https://api.star-history.com/svg?repos=nobynoooob/ani-cli-ar&type=date&legend=top-left" />
  </picture>
</a>

---

## 📄 License

This project is licensed under the **GNU General Public License v3.0**. See the [LICENSE](LICENSE) file for details.

---

<div align="center">

### ⚠️ Important Notice

</div>

> [!CAUTION]
> **By using this software you understand:**
> 
> - We do not host any content; all streams are from third-party sources.
> - This tool is for personal use and educational purposes only.
> - The project is licensed under GPL-3.0 - see [LICENSE](LICENSE) for details.

<br>

<div align="center">

Made with ❤️ by the anime community

[⭐ Star this repo](https://github.com/nobynoooob/ani-cli-ar) | [🐛 Report bugs](https://github.com/nobynoooob/ani-cli-ar/issues)

</div>
