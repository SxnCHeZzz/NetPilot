# NetPilot

A compact Windows controller for [Zapret](https://github.com/Flowseal/zapret-discord-youtube), [TG WS Proxy](https://github.com/Flowseal/tg-ws-proxy), and [Cloudflare WARP](https://one.one.one.one/).

**Status:** `0.5.4-rc5` — private testing release. Not affiliated with Flowseal or Cloudflare.

NetPilot keeps the three connections under one tray icon. The application downloads the supported upstream components, starts and stops its own processes, and helps compare Zapret configurations. Network drivers and administrator actions still require Windows approval.

## Install

For Windows 10/11 x64:

1. Open this repository's **Actions → Windows prerelease → Run workflow**. It runs tests and builds the installer and portable distribution on Windows.
2. Download the resulting **NetPilot-0.5.4-RC5-Windows** artifact from that workflow run.
3. For installation, use **`NetPilotSetup.exe`**. For portable use, extract **`NetPilot-Portable.zip`** and run `NetPilot.exe` from the extracted `NetPilot` directory; the exe depends on the other bundled files.
4. On first launch, choose Zapret and/or the official Cloudflare WARP installer. Telegram Proxy is already bundled; WARP is installed by winget to its official directory with Windows consent.

The project deliberately does not track compiled binaries, `dist/`, `build/`, or local settings. Place tested `NetPilotSetup.exe` and `NetPilot-Portable.zip` under **GitHub Releases**, mark release candidates as **Pre-release**. The repository includes a Windows Actions build and does not require a batch build script.

For developers who prefer building locally on Windows:

```powershell
py -m pip install -r requirements.txt
py -m unittest discover -s tests -v
py -m PyInstaller --clean --noconfirm --onedir --windowed --name NetPilot --icon assets\netpilot.ico --add-data "assets;assets" --collect-all proxy --collect-all utils --paths src src\app.py
```

[Inno Setup 6](https://jrsoftware.org/isinfo.php) can compile `installer/NetPilot.iss` into `NetPilotSetup.exe` after the PyInstaller build.

For development:

```powershell
py -m pip install -r requirements.txt
py -m unittest discover -s tests -v
py src\app.py
```

The Windows GitHub Actions workflow builds the installer and portable ZIP when triggered manually or by a `v*` tag. Build artifacts are downloadable from the workflow run; publishing them as a GitHub Release is a separate step. Binary releases belong in **Releases**, not in the Git repository.

## Use

- **Zapret:** toggle the selected `general*.bat` configuration. NetPilot never terminates another application's `winws.exe` or removes a previously installed Windows service.
- **Telegram:** toggle the bundled *console* proxy, which has no separate tray icon. Once the local proxy starts, NetPilot opens an official `tg://proxy` link in Telegram Desktop. Telegram may require confirmation. Turning NetPilot's proxy off does **not** silently change Telegram Desktop's own proxy settings.
- **WARP:** connect and disconnect through the installed official `warp-cli`, without opening the WARP GUI.
- **Tray:** left-click opens the flyout above the notification area; right-click displays quick actions. The usual Close action hides the flyout.

### Strategy checks

The strategy tester reads the official `utils/targets.txt` file from your installed Flowseal release and tests its HTTPS URLs using `curl` HEAD over HTTP/1.1, TLS 1.2 and TLS 1.3, where supported. It also attempts each usable HTTPS host from the installed `list-general.txt`, `list-general-user.txt` and `list-google.txt`, honoring exclusions. Cloudflare domains are kept in a separate user list and merged without duplicates.

This approach is **inspired by** Flowseal's standard `utils/test zapret.ps1`; it does not reproduce its ICMP ping, 16–20 KB DPI tests or voice/UDP/video checks. A blocked or failed web request is not proof that a Zapret strategy does not work. Tests run with a progress indicator, and detailed diagnostics are kept locally.

If NetPilot itself already started Zapret, the tester temporarily stops only that process. When testing fails or is cancelled, it attempts to restore the previous managed configuration. It refuses to take control of an unrelated process or a Windows Zapret service. WARP should be disconnected during comparisons.

Large hostlists and many strategies can take several minutes to test. You can cancel from the same button.

### Telegram troubleshooting

NetPilot waits for `127.0.0.1:1443` before opening Telegram Desktop's proxy link. Telegram must be installed and registered as the handler for `tg://`. If the link is ignored, set an MTProto proxy manually in Telegram Desktop: address `127.0.0.1`, port `1443`, and the generated secret in your local settings file. Keep the secret private.

If the port is occupied by an older independent `TgWsProxy_windows.exe`, close it through its own tray once. NetPilot only stops processes it created.

## Project layout

```text
src/         Desktop UI, adapters, strategy tests, Telegram worker
assets/      Icons and optional domain profiles
installer/   Inno Setup script
.github/     Windows CI workflow
tests/       Unit tests and Qt smoke tests
```

Application state and the generated Telegram secret are stored outside this repository in `%LOCALAPPDATA%\NetPilot`. Do not commit local settings, logs, downloaded components, secrets or build artifacts. `.gitignore` covers the common cases.

## Current limitations

- This is a **release candidate**, not a verified Windows production build. Windows/WinDivert, Telegram Desktop integration, the new in-window confirmation sheets and installer behavior must be tested on a real PC.
- TLS HEAD checks do not verify live streams, voice calls or UDP/QUIC. `curl` behavior can also vary with network policies and its Windows TLS backend.
- Full automated management of every option in Flowseal's changing `service.bat` is not yet implemented. The upstream tool remains available under advanced settings.
- A successful local proxy startup does not mean Telegram Desktop accepted the proxy: the user may need to confirm the link.

## Credits and licensing

NetPilot is an independent project. Its own source is MIT-licensed (see `LICENSE`). TG WS Proxy's bundled console engine is © Flowseal and MIT-licensed; the complete attribution is in `THIRD_PARTY_NOTICES.txt`. Zapret and WARP remain third-party programs with their respective terms and update channels.

## 0.5.3-rc4 (previous)

- Move confirmations, warnings, component setup and advanced options **inside** the main flyout; no secondary Qt popups for these actions.
- Keep Windows' native UAC and file chooser prompts intact.
- Replace text-symbol window controls with consistent painted Back / Settings / Minimize icons.
- Remove the duplicate bottom back button in Settings.
- Retain Flowseal curl/TLS checks, safe process ownership, Telegram Desktop proxy-link integration, and the existing tray position logic.

### Prior changes (0.5.2)

- Added Flowseal-style curl/TLS checks using installed hostlists and diagnostic output.
- Restored a managed Zapret configuration after failed/cancelled tests, without touching unrelated processes.
- Added Telegram Desktop proxy link after the local proxy is ready.

## 0.5.4-rc5 — setup and preferences cleanup

- Removed the obsolete **Quiet during games** preference; Windows manages Do Not Disturb.
- Removed the **Automatically apply winner** switch. Following a successful benchmark, NetPilot now attempts to activate the winning configuration automatically. A failed or cancelled benchmark restores the NetPilot-managed prior process when possible; other software and Windows services remain untouched.
- First-run and **Components** dialogs now offer **Zapret + official Cloudflare WARP**, replacing the redundant disabled Telegram Proxy checkbox. Telegram's console engine is bundled and does not require a separate download.
- WARP installation runs through `winget` and installs into Cloudflare's normal Windows location, not the optional Zapret folder. Windows UAC prompts remain visible and require the user's approval.
- `WinError 10013` download failures now display an actionable network/firewall troubleshooting message.
- Existing user preferences are preserved, except the removed obsolete switches.

Note: a good HTTPS benchmark score is not a guarantee that Discord voice, video streaming or WARP connectivity will work. Real driver/installer tests require Windows.
