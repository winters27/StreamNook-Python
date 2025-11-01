# My Personal Streamlink Setup Guide

This guide provides a one-click PowerShell installer to quickly reinstall and configure my complete Streamlink + MPC-HC + Streamlink Twitch GUI + Chatterino setup from scratch.

---

## 1. Using the PowerShell Installer

### Quick Start

1. **Download the installer:**
   - Save a copy of `My-Streamlink-Installer.ps1` to your PC.

2. **Run it:**
   - Right-click the `.ps1` file and select **“Run with PowerShell.”**
   - Follow the step-by-step wizard that appears.

3. **What the wizard does:**
   - Automatically downloads the **latest** releases of:
     - **MPC-HC** (Media Player Classic)
     - **Streamlink**
     - **Streamlink Twitch GUI**
     - **Chatterino (7TV Edition)**
     - **TTVLOL plugin**
   - Guides you through configuration, including:
     - Setting MPC-HC as the video player.
     - Adding Streamlink arguments.
     - Enabling ad-free Twitch playback.

4. **When finished:**
   - Everything is installed and pre-configured.
   - You can launch the Streamlink Twitch GUI and start watching streams immediately.

---

## 2. Components Installed

| Component | Repository | Purpose |
| :--- | :--- | :--- |
| **MPC-HC** | [clsid2/mpc-hc](https://github.com/clsid2/mpc-hc/releases) | The media player used by Streamlink. |
| **Streamlink** | [streamlink/windows-builds](https://github.com/streamlink/windows-builds/releases) | The backend CLI that pipes Twitch streams to the player. |
| **Streamlink Twitch GUI** | [streamlink/streamlink-twitch-gui](https://github.com/streamlink/streamlink-twitch-gui/releases) | The desktop frontend for browsing and launching streams. |
| **TTVLOL Plugin** | [2bc4/streamlink-ttvlol](https://github.com/2bc4/streamlink-ttvlol/releases) | Removes Twitch ads by routing through custom proxies. |
| **Chatterino (7TV Edition)** | [SevenTV/chatterino7](https://github.com/SevenTV/chatterino7/releases) | The chat client supporting 7TV, BTTV, and FFZ emotes. |

---

## 3. Manual Fallback Setup (If Needed)

If the PowerShell installer fails for any reason, you can set up everything manually using these steps.

### Step A — Install Software

1. **MPC-HC:**  
   Download the latest `MPC-HC...x64.exe` and install to  
   `C:\Program Files\MPC-HC`

2. **Streamlink:**  
   Download the latest `streamlink-...-x86_64.exe` and install with default options.

3. **Streamlink Twitch GUI:**  
   Download the latest `streamlink-twitch-gui-...-win64-installer.exe` and install it.

4. **Chatterino (7TV):**  
   Download the latest `Chatterino7.Installer.exe` and install it.

5. **TTVLOL Plugin:**  
   Download `twitch.py` from the [TTVLOL releases page](https://github.com/2bc4/streamlink-ttvlol/releases).  
   Then:
   - Press `Win + R` → type `%APPDATA%\streamlink`
   - Create a new folder named `plugins`
   - Move `twitch.py` into `%APPDATA%\streamlink\plugins`

---

### Step B — Configure Streamlink Twitch GUI

1. Open the GUI once (to generate its settings), then close it.
2. Open **Settings** (gear icon).
3. In **Main Tab:**
   - Enable **“Advanced settings and features.”**
4. In **Player Tab:**
   - **Player path:** `C:\Program Files\MPC-HC\mpc-hc64.exe`  
   - **Player arguments:**  
     ```
     "{filename}" /play /close /new /fixedsize 1075,605 /viewpreset 1
     ```
5. In **Streaming Tab:**
   - **Custom parameters:**  
     ```
     --twitch-proxy-playlist=https://lb-na.cdn-perfprod.com,https://eu.luminous.dev --twitch-proxy-playlist-fallback
     ```
6. Restart the GUI — you’re done!

---

## Notes

- The PowerShell installer automatically fetches the newest versions from GitHub — no manual updates needed.
- If the GUI fails to detect Streamlink, ensure it’s in your PATH or reinstall via the wizard.
- You can rerun the installer anytime to repair or refresh the setup.
