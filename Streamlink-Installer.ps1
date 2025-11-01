# ===================================================================
# === My-Streamlink-Installer.ps1
# ===
# === A personal GUI wizard to walk through the manual installation
# === and configuration of the full Streamlink + MPV suite.
# === (v4.1 - Fixed ParserError typo)
# ===================================================================

# --- Load GUI Assemblies ---
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

# --- Define GitHub Download URLs ---
$global:urls = @{
  # MPV is now handled by a custom button, but we'll leave the repo name
  'mpv' = @{
    Type = "github-release-page"
    Repo = "shinchiro/mpv-winbuild-cmake"
  }
  'streamlink' = @{
    Type    = "github-latest"
    Repo    = "streamlink/windows-builds"
    Pattern = "streamlink*-x86_64.exe" # matches streamlink-7.6.0-1-py313-x86_64.exe
  }
  'gui' = @{
    Type    = "github-latest"
    Repo    = "streamlink/streamlink-twitch-gui"
    Pattern = "streamlink-twitch-gui*-win64*-installer.exe" # matches ...win64-installer.exe
  }
  'chatterino' = @{
    Type    = "github-latest"
    Repo    = "SevenTV/chatterino7"
    Pattern = "*Chatterino*.Installer.exe" # matches Chatterino7.Installer.exe
  }
  'ttvlol' = @{
    Type = "direct"
    Url  = "https://github.com/2bc4/streamlink-ttvlol/releases/latest/download/twitch.py"
  }
}

# --- Function to Get Latest Download URL from GitHub (no token, HTML-only) ---
function Get-LatestDownloadUrl {
  [CmdletBinding()]
  param(
    [Parameter(Mandatory = $true)][string]$repo,
    [Parameter(Mandatory = $true)][string]$pattern # e.g. "streamlink*-x86_64.exe"
  )

  # Ensure TLS 1.2+ (older Windows compatibility)
  try {
    [Net.ServicePointManager]::SecurityProtocol =
      [Net.ServicePointManager]::SecurityProtocol -bor
      [Net.SecurityProtocolType]::Tls12
    try {
      # Tls13 may not exist on older frameworks; ignore if it errors
      [Net.ServicePointManager]::SecurityProtocol =
        [Net.ServicePointManager]::SecurityProtocol -bor
        [Net.SecurityProtocolType]::Tls13
    }
    catch { }
  }
  catch { }

  $ua = @{ "User-Agent" = "My-Streamlink-Installer" }
  $wc = New-Object System.Management.Automation.WildcardPattern($pattern, 'IgnoreCase')

  # 1) Resolve the latest tag without following redirects (to get /tag/<version>)
  $latestUrl = "https://github.com/$repo/releases/latest"
  $tagUrl = $null

  try {
    $resp = Invoke-WebRequest -Uri $latestUrl -Headers $ua -MaximumRedirection 0 -UseBasicParsing -ErrorAction SilentlyContinue
    $loc = $resp.Headers["Location"]
    if (-not $loc) {
      try {
        $null = Invoke-WebRequest -Method GET -Uri $latestUrl -Headers $ua -MaximumRedirection 0 -UseBasicParsing -ErrorAction Stop
      }
      catch {
        $loc = $_.Exception.Response.GetResponseHeader("Location")
      }
    }
    if ($loc) {
      $tagUrl = if ($loc -match '^https?://') { $loc } else { "https://github.com$loc" }
    }
  }
  catch { }

  if (-not $tagUrl) { $tagUrl = $latestUrl }

  # 2) Download the tag page HTML and scrape asset links
  try {
    $html = Invoke-WebRequest -Uri $tagUrl -Headers $ua -UseBasicParsing -ErrorAction Stop
    $content = $html.Content

    # Find all /<owner>/<repo>/releases/download/.../<file>
    $matches = [regex]::Matches($content, '(?i)href="(/[^"]+/releases/download/[^"]+)"')
    $seen = @{}
    foreach ($m in $matches) {
      $href = $m.Groups[1].Value
      if (-not $seen.ContainsKey($href)) {
        $seen[$href] = $true
        $abs = if ($href -match '^https?://') { $href } else { "https://github.com$href" }
        $file = Split-Path -Leaf ($href -split '\?' | Select-Object -First 1)
        if ($wc.IsMatch($file)) {
          return $abs
        }
      }
    }

    # Soft fallback: prefer any "*installer*.exe", then any .exe
    foreach ($m in $matches) {
      $href = $m.Groups[1].Value
      $file = Split-Path -Leaf ($href -split '\?' | Select-Object -First 1)
      if ($file -match '(?i)installer' -and $file -match '(?i)\.exe$') {
        return ("https://github.com" + $href)
      }
    }
    foreach ($m in $matches) {
      $href = $m.Groups[1].Value
      $file = Split-Path -Leaf ($href -split '\?' | Select-Object -First 1)
      if ($file -match '(?i)\.exe$') {
        return ("https://github.com" + $href)
      }
    }
  }
  catch {
    # ignore and fall through
  }

  # 3) If all else fails, return the releases page
  return $latestUrl
}

# --- Define Text for Steps ---
$global:stepInstructions = @{
  1 = @{
    Title = "Welcome to Your Personal Installer"
    Text  = "This wizard will guide you through manually installing and configuring your complete Streamlink + MPV setup.`n`nClick 'Next' to begin."
  }
  2 = @{
    Title = "Step 1: Download & Install MPV"
    Text  = "1. Click 'Open Download Page' to go to the latest MPV releases.`n`n2. On the page, find the **Assets** section.`n`n3. Look for the file ending in **`mpv-x86_64-gcc-....7z`**.`n   (This is the recommended 64-bit version. Avoid `dev`, `aarch64`, or `i686` files).`n`n4. Download that `.7z` file.`n`n5. You need **7-Zip** or **WinRAR** to extract it.`n`n6. Create a permanent folder, for example: `C:\Program Files\mpv` (You may need admin rights).`n`n7. Extract all files from the downloaded `.7z` archive into that new folder.`n   (The final path to the player should be `C:\Program Files\mpv\mpv.exe`)`n`n8. Click 'Next' when done."
  }
  3 = @{
    Title = "Step 2: Create MPV Configuration"
    Text  = "This step adds the high-quality settings to MPV.`n`n1. Click 'Open Config Folder'. This will create and open the folder: `%APPDATA%\mpv``n`n2. Inside this folder, create a new text file named **`mpv.conf`** (Make sure it's not `mpv.conf.txt`).`n`n3. Click 'Copy Config' and paste the text into your new `mpv.conf` file.`n`n4. Save and close the file. Click 'Next' when done."
  }
  4 = @{
    Title = "Step 3: Install Streamlink"
    Text  = "1. Click the 'Download' button below to automatically download the latest version.`n`n2. Run the installer and use all default options.`n`n3. Click 'Next' when done."
  }
  5 = @{
    Title = "Step 4: Install Streamlink Twitch GUI"
    Text  = "1. Click the 'Download' button below to automatically download the latest version.`n`n2. Run the installer.`n`n3. **IMPORTANT:** After installing, run the Streamlink Twitch GUI **ONCE** and then close it. This creates the settings file we need.`n`n4. Click 'Next' when done."
  }
  6 = @{
    Title = "Step 5: Install Chatterino (7TV)"
    Text  = "1. Click the 'Download' button below to automatically download the latest version.`n`n2. Run the installer.`n`n3. Click 'Next' when done."
  }
  7 = @{
    Title = "Step 6: Install TTVLOL Plugin"
    Text  = "1. Click 'Download Plugin' to automatically download twitch.py.`n`n2. Click 'Open Folder'. This opens %APPDATA%\streamlink\ with the plugins folder already created.`n`n3. Move the downloaded twitch.py file into the plugins folder.`n`nFinal path: ...streamlink\plugins\twitch.py"
  }
  8 = @{
    Title = "Step 7: Configure Player Path"
    Text  = "Open Streamlink Twitch GUI and go to Settings (gear icon).`n`n1. Go to the **Player** tab.`n`n2. For **'Player path'**, set it to where you extracted MPV:`n`n`C:\Program Files\mpv\mpv.exe` (or your custom path)`n`n3. For **'Player arguments'**, click the 'Copy Args' button below and paste the text in the box."
  }
  9 = @{
    Title = "Step 8: Configure Streaming (Ads)"
    Text  = "In the same GUI Settings window:`n`n1. Go to the **Main** tab.`n`n2. Check the box for **'Enable advanced settings and features'**.`n`n3. Go to the **Streaming** tab.`n`n4. In the **'Custom parameters'** box, click the 'Copy Args' button below and paste the text in the box."
  }
  10 = @{
    Title = "All Done!"
    Text  = "Your setup is complete. All applications are installed and configured.`n`nYou can now use the Streamlink Twitch GUI.`n`nClick 'Finish' to exit."
  }
}

$global:maxSteps = $global:stepInstructions.Count
$global:currentStep = 1

# --- Create Form and Controls ---
$form = New-Object System.Windows.Forms.Form
$form.Text = "Streamlink Suite Installer"
$form.Size = New-Object System.Drawing.Size(620, 520)
$form.MinimumSize = $form.Size
$form.StartPosition = "CenterScreen"
$form.FormBorderStyle = "FixedSingle"
$form.MaximizeBox = $false
$form.BackColor = [System.Drawing.Color]::FromArgb(12, 12, 13) # Dark background #0c0c0d

# Bottom panel for navigation buttons
$panelBottom = New-Object System.Windows.Forms.Panel
$panelBottom.Height = 70
$panelBottom.Dock = "Bottom"
$panelBottom.BackColor = [System.Drawing.Color]::FromArgb(18, 18, 20) # Slightly lighter dark

# Progress indicator
$labelStep = New-Object System.Windows.Forms.Label
$labelStep.Text = "Step 1 of $global:maxSteps"
$labelStep.Location = New-Object System.Drawing.Point(25, 15)
$labelStep.AutoSize = $true
$labelStep.Font = New-Object System.Drawing.Font("Segoe UI", 9)
$labelStep.ForeColor = [System.Drawing.Color]::FromArgb(151, 177, 185) # Accent color

# Navigation buttons with modern styling
$buttonBack = New-Object System.Windows.Forms.Button
$buttonBack.Text = "← Back"
$buttonBack.Size = New-Object System.Drawing.Size(100, 35)
$buttonBack.Location = New-Object System.Drawing.Point(290, 20)
$buttonBack.Enabled = $false
$buttonBack.FlatStyle = "Flat"
$buttonBack.FlatAppearance.BorderSize = 1
$buttonBack.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(151, 177, 185)
$buttonBack.BackColor = [System.Drawing.Color]::FromArgb(25, 25, 28)
$buttonBack.ForeColor = [System.Drawing.Color]::FromArgb(151, 177, 185)
$buttonBack.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Regular)
$buttonBack.Cursor = "Hand"

$buttonNext = New-Object System.Windows.Forms.Button
$buttonNext.Text = "Next →"
$buttonNext.Size = New-Object System.Drawing.Size(100, 35)
$buttonNext.Location = New-Object System.Drawing.Point(400, 20)
$buttonNext.FlatStyle = "Flat"
$buttonNext.FlatAppearance.BorderSize = 1
$buttonNext.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(151, 177, 185)
$buttonNext.BackColor = [System.Drawing.Color]::FromArgb(38, 50, 52)
$buttonNext.ForeColor = [System.Drawing.Color]::White
$buttonNext.Font = New-Object System.Drawing.Font("Segoe UI", 10, [System.Drawing.FontStyle]::Regular)
$buttonNext.Cursor = "Hand"

$buttonCancel = New-Object System.Windows.Forms.Button
$buttonCancel.Text = "Cancel"
$buttonCancel.Size = New-Object System.Drawing.Size(80, 35)
$buttonCancel.Location = New-Object System.Drawing.Point(510, 20)
$buttonCancel.FlatStyle = "Flat"
$buttonCancel.FlatAppearance.BorderSize = 0
$buttonCancel.BackColor = [System.Drawing.Color]::FromArgb(18, 18, 20)
$buttonCancel.ForeColor = [System.Drawing.Color]::FromArgb(120, 120, 130)
$buttonCancel.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$buttonCancel.Cursor = "Hand"

$panelBottom.Controls.AddRange(@($buttonBack, $buttonNext, $buttonCancel, $labelStep))

# Title label with modern styling
$labelTitle = New-Object System.Windows.Forms.Label
$labelTitle.Text = "Step Title"
$labelTitle.Font = New-Object System.Drawing.Font("Segoe UI", 18, [System.Drawing.FontStyle]::Bold)
$labelTitle.ForeColor = [System.Drawing.Color]::White
$labelTitle.Dock = "Top"
$labelTitle.TextAlign = "MiddleCenter"
$labelTitle.Padding = New-Object System.Windows.Forms.Padding(20)
$labelTitle.Height = 80

# Subtitle label
$labelSubtitle = New-Object System.Windows.Forms.Label
$labelSubtitle.Text = ""
$labelSubtitle.Font = New-Object System.Drawing.Font("Segoe UI", 10)
$labelSubtitle.ForeColor = [System.Drawing.Color]::FromArgb(151, 177, 185)
$labelSubtitle.Dock = "Top"
$labelSubtitle.TextAlign = "MiddleCenter"
$labelSubtitle.Height = 25

# Action buttons panel with glass effect
$panelActions = New-Object System.Windows.Forms.Panel
$panelActions.Height = 60
$panelActions.Dock = "Bottom"
$panelActions.BackColor = [System.Drawing.Color]::FromArgb(18, 18, 20)

$buttonDownload = New-Object System.Windows.Forms.Button
$buttonDownload.Text = "Download"
$buttonDownload.Size = New-Object System.Drawing.Size(200, 40) # Increased size
$buttonDownload.Location = New-Object System.Drawing.Point(110, 10) # Centered
$buttonDownload.Visible = $false
$buttonDownload.FlatStyle = "Flat"
$buttonDownload.FlatAppearance.BorderSize = 1
$buttonDownload.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(151, 177, 185)
$buttonDownload.BackColor = [System.Drawing.Color]::FromArgb(38, 50, 52)
$buttonDownload.ForeColor = [System.Drawing.Color]::White
$buttonDownload.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Regular)
$buttonDownload.Cursor = "Hand"

$buttonAction = New-Object System.Windows.Forms.Button
$buttonAction.Text = "Helper Action"
$buttonAction.Size = New-Object System.Drawing.Size(200, 40) # Increased size
$buttonAction.Location = New-Object System.Drawing.Point(320, 10) # Adjusted
$buttonAction.Visible = $false
$buttonAction.FlatStyle = "Flat"
$buttonAction.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(151, 177, 185)
$buttonAction.BackColor = [System.Drawing.Color]::FromArgb(25, 25, 28)
$buttonAction.ForeColor = [System.Drawing.Color]::FromArgb(151, 177, 185)
$buttonAction.Font = New-Object System.Drawing.Font("Segoe UI", 11, [System.Drawing.FontStyle]::Regular)
$buttonAction.Cursor = "Hand"

$panelActions.Controls.AddRange(@($buttonDownload, $buttonAction))

# Instructions panel with glass effect background
$panelInstructions = New-Object System.Windows.Forms.Panel
$panelInstructions.Dock = "Fill"
$panelInstructions.BackColor = [System.Drawing.Color]::FromArgb(18, 18, 20)
$panelInstructions.Padding = New-Object System.Windows.Forms.Padding(30, 10, 30, 10)

# Instructions text with modern styling
$textInstructions = New-Object System.Windows.Forms.RichTextBox
$textInstructions.ReadOnly = $true
$textInstructions.Dock = "Fill"
$textInstructions.Font = New-Object System.Drawing.Font("Segoe UI", 11)
$textInstructions.BorderStyle = "None"
$textInstructions.BackColor = [System.Drawing.Color]::FromArgb(22, 22, 25)
$textInstructions.ForeColor = [System.Drawing.Color]::FromArgb(200, 200, 210)
$textInstructions.SelectionIndent = 20
$textInstructions.SelectionRightIndent = 20

$panelInstructions.Controls.Add($textInstructions)

# Add controls in proper order (order matters for docking!)
$form.Controls.Add($panelBottom)     # Bottom navigation
$form.Controls.Add($panelActions)     # Action buttons panel
$form.Controls.Add($panelInstructions) # Instructions panel
$form.Controls.Add($labelSubtitle)     # Subtitle
$form.Controls.Add($labelTitle)        # Title at top

# Add hover effects for buttons
$buttonNext.Add_MouseEnter({
  $this.BackColor = [System.Drawing.Color]::FromArgb(48, 63, 66)
})
$buttonNext.Add_MouseLeave({
  $this.BackColor = [System.Drawing.Color]::FromArgb(38, 50, 52)
})

$buttonBack.Add_MouseEnter({
  if ($this.Enabled) {
    $this.BackColor = [System.Drawing.Color]::FromArgb(35, 35, 38)
    $this.ForeColor = [System.Drawing.Color]::White
  }
})
$buttonBack.Add_MouseLeave({
  if ($this.Enabled) {
    $this.BackColor = [System.Drawing.Color]::FromArgb(25, 25, 28)
    $this.ForeColor = [System.Drawing.Color]::FromArgb(151, 177, 185)
  }
})

$buttonDownload.Add_MouseEnter({
  $this.BackColor = [System.Drawing.Color]::FromArgb(48, 63, 66)
})
$buttonDownload.Add_MouseLeave({
  $this.BackColor = [System.Drawing.Color]::FromArgb(38, 50, 52)
})

$buttonAction.Add_MouseEnter({
  $this.BackColor = [System.Drawing.Color]::FromArgb(35, 35, 38)
  $this.ForeColor = [System.Drawing.Color]::White
})
$buttonAction.Add_MouseLeave({
  $this.BackColor = [System.Drawing.Color]::FromArgb(25, 25, 28)
  $this.ForeColor = [System.Drawing.Color]::FromArgb(151, 177, 185)
})

$buttonCancel.Add_MouseEnter({
  $this.ForeColor = [System.Drawing.Color]::FromArgb(151, 177, 185)
})
$buttonCancel.Add_MouseLeave({
  $this.ForeColor = [System.Drawing.Color]::FromArgb(120, 120, 130)
})

# --- Function to Update GUI Content ---
function Load-Step {
  param ([int]$step)

  $data = $global:stepInstructions[$step]
  $form.SuspendLayout()

  $labelTitle.Text = $data.Title
  $textInstructions.Text = $data.Text
  $labelStep.Text = "Step $step of $global:maxSteps"

  # Set subtitle based on step
  switch ($step) {
    1  { $labelSubtitle.Text = "Let's get your Streamlink + MPV setup configured" }
    2  { $labelSubtitle.Text = "The lightweight, high-performance video player" }
    3  { $labelSubtitle.Text = "Set up high-quality playback" }
    4  { $labelSubtitle.Text = "Core streaming engine" }
    5  { $labelSubtitle.Text = "User-friendly interface for Twitch" }
    6  { $labelSubtitle.Text = "Enhanced chat with 7TV emotes" }
    7  { $labelSubtitle.Text = "Ad-blocking plugin for Twitch" }
    8  { $labelSubtitle.Text = "Connect MPV to Streamlink" }
    9  { $labelSubtitle.Text = "Enable ad-free streaming" }
    10 { $labelSubtitle.Text = "Setup complete!" }
  }

  # Reset Button Visibility & State
  $buttonDownload.Visible = $false
  $buttonAction.Visible = $false
  $buttonBack.Enabled = ($step -gt 1)
  $buttonNext.Text = "Next →"

  # Reset button locations for 2-button layout
  $buttonDownload.Location = New-Object System.Drawing.Point(110, 10)
  $buttonAction.Location = New-Object System.Drawing.Point(320, 10)
  $buttonDownload.Size = New-Object System.Drawing.Size(200, 40)
  $buttonAction.Size = New-Object System.Drawing.Size(200, 40)

  # Update back button styling when disabled
  if (-not $buttonBack.Enabled) {
    $buttonBack.ForeColor = [System.Drawing.Color]::FromArgb(80, 80, 85)
    $buttonBack.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(80, 80, 85)
  }
  else {
    $buttonBack.ForeColor = [System.Drawing.Color]::FromArgb(151, 177, 185)
    $buttonBack.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(151, 177, 185)
  }

  # Configure Controls for Specific Step
  switch ($step) {
    2 { # MPV
      $buttonDownload.Visible = $true
      $buttonDownload.Text = "📁 Open Download Page"
      $buttonDownload.Tag = "open_mpv_release_page"
      $buttonDownload.Location = New-Object System.Drawing.Point(210, 10) # Center
    }
    3 { # Configure MPV
      $buttonDownload.Visible = $true
      $buttonDownload.Text = "📁 Open Config Folder"
      $buttonDownload.Tag = "open_mpv_conf_folder"

      $buttonAction.Visible = $true
      $buttonAction.Text = "📋 Copy Config"
      $buttonAction.Tag = "copy_mpv_conf"
    }
    4 { # Streamlink
      $buttonDownload.Visible = $true
      $buttonDownload.Text = "🡇 Download Streamlink"
      $buttonDownload.Tag = "streamlink"
      $buttonDownload.Location = New-Object System.Drawing.Point(210, 10) # Center
    }
    5 { # Streamlink GUI
      $buttonDownload.Visible = $true
      $buttonDownload.Text = "🡇 Download GUI"
      $buttonDownload.Tag = "gui"
      $buttonDownload.Location = New-Object System.Drawing.Point(210, 10) # Center
    }
    6 { # Chatterino
      $buttonDownload.Visible = $true
      $buttonDownload.Text = "🡇 Download Chatterino"
      $buttonDownload.Tag = "chatterino"
      $buttonDownload.Location = New-Object System.Drawing.Point(210, 10) # Center
    }
    7 { # TTVLOL
      $buttonDownload.Visible = $true
      $buttonDownload.Text = "🡇 Download Plugin"
      $buttonDownload.Tag = "ttvlol"

      $buttonAction.Visible = $true
      $buttonAction.Text = "📁 Open Folder"
      $buttonAction.Tag = "open_folder"
    }
    8 { # Configure Player
      $buttonAction.Visible = $true
      $buttonAction.Text = "📋 Copy Player Args"
      $buttonAction.Tag = "copy_player_args"
      $buttonAction.Location = New-Object System.Drawing.Point(210, 10) # Center
    }
    9 { # Configure Streaming
      $buttonAction.Visible = $true
      $buttonAction.Text = "📋 Copy Stream Args"
      $buttonAction.Tag = "copy_stream_args"
      $buttonAction.Location = New-Object System.Drawing.Point(210, 10) # Center
    }
    10 { # Finish
      $buttonNext.Text = "Finish ✓"
      $buttonNext.BackColor = [System.Drawing.Color]::FromArgb(40, 80, 60)
      $buttonBack.Enabled = $false
      $buttonBack.ForeColor = [System.Drawing.Color]::FromArgb(80, 80, 85)
      $buttonBack.FlatAppearance.BorderColor = [System.Drawing.Color]::FromArgb(80, 80, 85)
    }
  }

  $form.ResumeLayout()
}

# --- Button Click Actions ---
$buttonNext_Click = {
  if ($this.Text -like "Finish*") {
    $form.Close()
  }
  else {
    $global:currentStep++
    Load-Step -step $global:currentStep
  }
}

$buttonBack_Click = {
  if ($global:currentStep -gt 1) {
    $global:currentStep--
    Load-Step -step $global:currentStep
  }
}

$buttonCancel_Click = {
  $form.Close()
}

$buttonDownload_Click = {
  if ($this.Tag) {
    # Handle non-download, special-case actions
    if ($this.Tag -eq "open_mpv_conf_folder") {
      $path = "$env:APPDATA\mpv"
      if (-not (Test-Path $path)) {
        New-Item -Path $path -ItemType Directory -Force | Out-Null
      }
      Invoke-Item $path
      return
    }
    elseif ($this.Tag -eq "open_mpv_release_page") {
      Start-Process "https://github.com/shinchiro/mpv-winbuild-cmake/releases/latest"
      return
    }

    # Standard download logic
    $urlInfo = $global:urls[$this.Tag]
    $downloadUrl = $null
    $fileName = $null

    # Update button to show downloading state
    $originalText = $this.Text
    $this.Text = "⏳ Getting link..."
    $this.Enabled = $false
    [System.Windows.Forms.Application]::DoEvents()

    try {
      if ($urlInfo.Type -eq "direct") {
        # Direct download URL
        $downloadUrl = $urlInfo.Url
        $fileName = Split-Path -Leaf $urlInfo.Url
      }
      elseif ($urlInfo.Type -eq "github-latest") {
        # Get latest release URL (HTML-only resolver)
        $downloadUrl = Get-LatestDownloadUrl -repo $urlInfo.Repo -pattern $urlInfo.Pattern

        # Determine if the resolved URL points directly to a file (.exe/.py/.7z), even with querystrings
        $leaf = Split-Path -Leaf ($downloadUrl -split '\?' | Select-Object -First 1)
        $ext = [IO.Path]::GetExtension($leaf)

        if ($ext -and @(".exe", ".py", ".7z") -contains $ext.ToLower()) {
          $fileName = $leaf

          # Auto-download the file
          $this.Text = "⏳ Downloading..."
          [System.Windows.Forms.Application]::DoEvents()

          # Open save dialog
          $saveDialog = New-Object System.Windows.Forms.SaveFileDialog
          $saveDialog.FileName = $fileName
          
          switch ($ext.ToLower()) {
            ".exe" { $saveDialog.Filter = "Executable files (*.exe)|*.exe|All files (*.*)|*.*" }
            ".py"  { $saveDialog.Filter = "Python files (*.py)|*.py|All files (*.*)|*.*" }
            ".7z"  { $saveDialog.Filter = "7-Zip archives (*.7z)|*.7z|All files (*.*)|*.*" }
            default { $saveDialog.Filter = "All files (*.*)|*.*" }
          }
          
          $saveDialog.InitialDirectory = [Environment]::GetFolderPath("Downloads")

          if ($saveDialog.ShowDialog() -eq "OK") {
            # Download the file
            $this.Text = "⏬ Downloading..."
            [System.Windows.Forms.Application]::DoEvents()

            try {
              $webClient = New-Object System.Net.WebClient
              $webClient.Headers.Add("User-Agent", "PowerShell")
              $webClient.DownloadFile($downloadUrl, $saveDialog.FileName)

              [System.Windows.Forms.MessageBox]::Show(
                "Download completed successfully!`n`nFile saved to:`n$($saveDialog.FileName)",
                "✓ Download Complete",
                "OK",
                "Information"
              )

              # Ask if user wants to run the installer (for .exe files)
              if ($ext.ToLower() -eq ".exe") {
                $result = [System.Windows.Forms.MessageBox]::Show(
                  "Would you like to run the installer now?",
                  "Run Installer?",
                  "YesNo",
                  "Question"
                )

                if ($result -eq "Yes") {
                  Start-Process $saveDialog.FileName
                }
              }
            }
            catch {
              [System.Windows.Forms.MessageBox]::Show(
                "Download failed: $_`n`nOpening download page instead...",
                "Download Error",
                "OK",
                "Warning" # <--- THIS WAS THE FIX
              )
              Start-Process $downloadUrl
            }
          }
        }
        else {
          # Fallback to opening the releases page (or resolved tag page)
          Start-Process $downloadUrl
        }
      }
    }
    catch {
      # On any error, open the releases page
      [System.Windows.Forms.MessageBox]::Show(
        "Could not get direct download link.`nOpening releases page instead...",
        "Note",
        "OK",
        "Information"
      )
      if ($urlInfo.Repo) {
        Start-Process "https://github.com/$($urlInfo.Repo)/releases/latest"
      }
    }
    finally {
      # Restore button state
      $this.Text = $originalText
      $this.Enabled = $true
    }
  }
}

$buttonAction_Click = {
  switch ($buttonAction.Tag) {
    "open_folder" {
      $path = "$env:APPDATA\streamlink"
      if (-not (Test-Path $path)) {
        New-Item -Path $path -ItemType Directory -Force | Out-Null
      }
      # Create plugins folder if it doesn't exist
      $pluginsPath = Join-Path $path "plugins"
      if (-not (Test-Path $pluginsPath)) {
        New-Item -Path $pluginsPath -ItemType Directory -Force | Out-Null
      }
      Invoke-Item $path
    }
    "copy_mpv_conf" {
      # This is the config we perfected
      $conf = @"
# --- High-Quality Preset ---
profile=high-quality

# --- Upscaling ---
scale=ewa_lanczossharp
cscale=ewa_lanczossharp

# --- Debanding / Artifact Reduction ---
deband=yes

# --- Hardware Decoding ---
hwdec=d3D11va
gpu-api=d3D11
"@
      Set-Clipboard -Value $conf.Replace("`r`n", "`n") # Ensure Unix line endings
      [System.Windows.Forms.MessageBox]::Show(
        "MPV config copied to clipboard!`n`nPaste this into your new mpv.conf file.",
        "✓ Copied Successfully",
        "OK",
        "Information"
      )
    }
    "copy_player_args" {
      $playerArgs = '--keep-open=no --geometry=1075x605 --no-border --cache=no'
      Set-Clipboard -Value $playerArgs
      [System.Windows.Forms.MessageBox]::Show(
        "MPV player arguments copied to clipboard!`n`nPaste them into the Player Arguments field in Streamlink Twitch GUI settings.",
        "✓ Copied Successfully",
        "OK",
        "Information"
      )
    }
    "copy_stream_args" {
      $streamArgs = '--twitch-proxy-playlist=https://lb-na.cdn-perfprod.com,https://eu.luminous.dev --twitch-proxy-playlist-fallback'
      Set-Clipboard -Value $streamArgs
      [System.Windows.Forms.MessageBox]::Show(
        "Streaming parameters copied to clipboard!`n`nPaste them into the Custom Parameters field in Streamlink Twitch GUI settings.",
        "✓ Copied Successfully",
        "OK",
        "Information"
      )
    }
  }
}

# --- Assign Click Events ---
$buttonNext.Add_Click($buttonNext_Click)
$buttonBack.Add_Click($buttonBack_Click)
$buttonCancel.Add_Click($buttonCancel_Click)
$buttonDownload.Add_Click($buttonDownload_Click)
$buttonAction.Add_Click($buttonAction_Click)

# --- Load and Show the Form ---
$form.Add_Shown({ Load-Step -step $global:currentStep })
[void]$form.ShowDialog()
