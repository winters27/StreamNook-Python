using namespace System.Windows
using namespace System.Windows.Controls
using namespace System.Windows.Markup
using namespace System.Windows.Forms # For file dialogs
using namespace System.Diagnostics
using namespace System.Threading


[CmdletBinding()]
param(
    [Parameter(Mandatory=$false)]
    [switch]$NonInteractive,

    [Parameter(Mandatory=$false)]
    [string]$VersionOverride,

    [Parameter(Mandatory=$false)]
    [string]$UpxDir
)

# --- Setup ---
$scriptRoot = Split-Path -Parent $PSCommandPath
Set-Location -Path $scriptRoot

# --- Type Assemblies (must come after 'using') ---
Add-Type -AssemblyName PresentationFramework, PresentationCore, WindowsBase, System.Xaml, System.Windows.Forms

# --- STA Check (Crucial for WPF/Forms) ---
if ([Thread]::CurrentThread.ApartmentState -ne 'STA') {
    Write-Host "Restarting PowerShell in STA mode..." -ForegroundColor Yellow
    $psi = New-Object ProcessStartInfo
    $psi.FileName = (Get-Process -Id $PID).Path
    $psi.Arguments = "-NoProfile -ExecutionPolicy Bypass -STA -File `"$PSCommandPath`""
    $psi.UseShellExecute = $true
    $psi.WorkingDirectory = $scriptRoot # preserve working dir
    [Process]::Start($psi) | Out-Null
    exit
}

# ----------------------------------------
# ---------- Helper Functions ----------
# ----------------------------------------

function Suggest-NextVersion {
    # 1. Try to get the version from Git tags first
    try {
        git fetch --tags --force 2>$null
        $latestTag = (git tag --list "v*" --sort=-v:refname | Select-Object -First 1)
        if ($latestTag -and ($latestTag -match "^v(\d+)\.(\d+)\.(\d+)$")) {
            $maj = [int]$Matches[1]
            $min = [int]$Matches[2]
            $pat = [int]$Matches[3] + 1 # Suggest next patch version
            return "{0}.{1}.{2}" -f $maj, $min, $pat
        }
    }
    catch {
        # Git failed, proceed to fallback
    }

    # 2. Fallback to local version_info.txt
    if (Test-Path -LiteralPath 'version_info.txt') {
        try {
            $text = Get-Content -LiteralPath 'version_info.txt' -Raw
            if ($text -match "StringStruct\(u'FileVersion',\s*u'(?<ver>\d+\.\d+\.\d+\.\d+)'\)") {
                $v = $Matches['ver']
                $p = $v.Split('.') | ForEach-Object { [int]$_ }
                $p[3] = $p[3] + 1 # Increment build number
                return ($p -join '.')
            }
        }
        catch {
            # Failed to parse, proceed to final fallback
        }
    }

    # 3. Final fallback
    return '1.0.0.0'
}

function Parse-VersionTuple([string]$v) {
    if (-not $v) { throw "Version string cannot be empty." }
    
    # Accept Major.Minor.Patch and append .0 for build
    if ($v -match '^\d+\.\d+\.\d+$') {
        $v += '.0'
    }

    if (-not ($v -match '^\d+(\.\d+){3}$')) {
        throw "Version must be Major.Minor.Patch or Major.Minor.Patch.Build (e.g., 1.2.3 or 1.2.3.4)."
    }
    $parts = $v.Split('.') | ForEach-Object { [int]$_ }
    return '(' + ($parts -join ', ') + ')'
}

function Find-PyInstaller {
    try {
        $ver = & pyinstaller --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver) { return @{ Cmd = @('pyinstaller'); Version = $ver.Trim() } }
    }
    catch { }
    try {
        $ver = & py -m PyInstaller --version 2>$null
        if ($LASTEXITCODE -eq 0 -and $ver) { return @{ Cmd = @('py', '-m', 'PyInstaller'); Version = $ver.Trim() } }
    }
    catch { }
    return $null
}

function Get-AddDataSeparator($pyiCmd) {
    try {
        $help = & @($pyiCmd) --help 2>$null
        if ($help -match 'SOURCE:DEST') { return ':' }
        if ($help -match 'SOURCE;DEST') { return ';' }
    }
    catch { }
    return ':' # PyInstaller 6+ default
}

function New-VersionInfoContent {
    param(
        [string]$Org, [string]$Description, [string]$Version, [string]$Project,
        [string]$ExeName, [string]$RepoUrl, [string]$TupleStr, [int]$Year
    )
    @"
VSVersionInfo(
  ffi=FixedFileInfo(
    filevers=$TupleStr,
    prodvers=$TupleStr,
    mask=0x3f,
    flags=0x0,
    OS=0x40004,
    fileType=0x1,
    subtype=0x0,
    date=(0, 0)
  ),
  kids=[
    StringFileInfo(
      [
        StringTable(
          u'040904B0',
          [
            StringStruct(u'CompanyName',      u'$Org'),
            StringStruct(u'FileDescription',  u'$Description'),
            StringStruct(u'FileVersion',      u'$Version'),
            StringStruct(u'InternalName',     u'$Project'),
            StringStruct(u'OriginalFilename', u'$ExeName.exe'),
            StringStruct(u'ProductName',      u'$Project'),
            StringStruct(u'ProductVersion',   u'$Version'),
            StringStruct(u'Comments',         u'Source code: $RepoUrl'),
            StringStruct(u'LegalCopyright',   u'© $Year StreamNook contributors')
          ]
        )
      ]
    ),
    VarFileInfo([VarStruct(u'Translation', [1033, 1200])])
  ]
)
"@
}

# ----------------------------------------
# ---------- WPF UI Definition ----------
# ----------------------------------------

# NOTE: Use x:Name (WPF standard) instead of Name (WinForms)
$xaml = @"
<Window xmlns="http://schemas.microsoft.com/winfx/2006/xaml/presentation"
        xmlns:x="http://schemas.microsoft.com/winfx/2006/xaml"
        Title="StreamNook Build Assistant" Height="640" Width="820" WindowStartupLocation="CenterScreen">
  <Grid Margin="12">
    <Grid.RowDefinitions>
      <RowDefinition Height="Auto"/>
      <RowDefinition Height="*"/>
      <RowDefinition Height="Auto"/>
    </Grid.RowDefinitions>

    <StackPanel Orientation="Vertical" Grid.Row="0" Margin="0,0,0,8">
      <TextBlock Text="Build Metadata" FontWeight="Bold" FontSize="16" Margin="0,0,0,6"/>
      <UniformGrid Rows="6" Columns="4" Margin="0,0,0,6">
        <TextBlock Text="Project" VerticalAlignment="Center"/>
        <TextBox x:Name="tbProject" Grid.ColumnSpan="3" Margin="6,0,0,0"/>
        <TextBlock Text="Organization" VerticalAlignment="Center"/>
        <TextBox x:Name="tbOrg" Grid.ColumnSpan="3" Margin="6,0,0,0"/>
        <TextBlock Text="Description" VerticalAlignment="Center"/>
        <TextBox x:Name="tbDesc" Grid.ColumnSpan="3" Margin="6,0,0,0"/>
        <TextBlock Text="Repo URL" VerticalAlignment="Center"/>
        <TextBox x:Name="tbRepo" Grid.ColumnSpan="3" Margin="6,0,0,0"/>
        <TextBlock Text="EXE Name" VerticalAlignment="Center"/>
        <TextBox x:Name="tbExe" Grid.ColumnSpan="3" Margin="6,0,0,0"/>
        <TextBlock Text="Version" VerticalAlignment="Center"/>
        <StackPanel Orientation="Horizontal" Grid.ColumnSpan="3" Margin="6,0,0,0">
          <TextBox x:Name="tbVersion" Width="140"/>
          <Button x:Name="btnSuggest" Content="Suggest" Margin="6,0,0,0" Width="80"/>
        </StackPanel>
      </UniformGrid>

      <TextBlock Text="Files &amp; Inputs" FontWeight="Bold" FontSize="16" Margin="0,6,0,6"/>
      <Grid>
        <Grid.ColumnDefinitions>
          <ColumnDefinition Width="*"/>
          <ColumnDefinition Width="Auto"/>
        </Grid.ColumnDefinitions>
        <Grid.RowDefinitions>
          <RowDefinition Height="Auto"/>
          <RowDefinition Height="Auto"/>
          <RowDefinition Height="160"/>
        </Grid.RowDefinitions>

        <StackPanel Orientation="Horizontal" Grid.Row="0">
          <TextBlock Text="Entry Script:" VerticalAlignment="Center" Width="80"/>
          <TextBox x:Name="tbEntry" Width="320"/>
          <Button x:Name="btnBrowseEntry" Content="Browse…" Margin="6,0,0,0" Width="80"/>
        </StackPanel>

        <StackPanel Orientation="Horizontal" Grid.Row="1" Margin="0,4,0,0">
          <TextBlock Text="Icon (.ico):" VerticalAlignment="Center" Width="80"/>
          <TextBox x:Name="tbIcon" Width="320"/>
          <Button x:Name="btnBrowseIcon" Content="Browse…" Margin="6,0,0,0" Width="80"/>
        </StackPanel>

        <GroupBox Header="Add Data (--add-data pairs)" Grid.Row="2" Margin="0,6,0,0">
          <Grid>
            <Grid.RowDefinitions>
              <RowDefinition Height="Auto"/>
              <RowDefinition Height="*"/>
              <RowDefinition Height="Auto"/>
            </Grid.RowDefinitions>
            <StackPanel Orientation="Horizontal" Grid.Row="0" Margin="4">
              <TextBlock Text="SOURCE:" VerticalAlignment="Center" Width="60"/>
              <TextBox x:Name="tbSrc" Width="140"/>
              <Button x:Name="btnBrowseSrc" Content="…" Margin="4,0,0,0" Width="30"/>
              <TextBlock Text="DEST:" VerticalAlignment="Center" Margin="8,0,0,0" Width="40"/>
              <TextBox x:Name="tbDest" Width="100"/>
              <Button x:Name="btnAddPair" Content="Add" Margin="6,0,0,0" Width="60"/>
            </StackPanel>
            <ListBox x:Name="lbAddData" Grid.Row="1" Margin="4"/>
            <Button x:Name="btnRemovePair" Content="Remove Selected" Grid.Row="2" Margin="4" HorizontalAlignment="Right" Width="120"/>
          </Grid>
        </GroupBox>
      </Grid>
    </StackPanel>

    <GroupBox Header="Output Log" Grid.Row="1" Margin="0,6,0,6">
      <TextBox x:Name="tbLog" IsReadOnly="True" TextWrapping="Wrap" AcceptsReturn="True" VerticalScrollBarVisibility="Auto"/>
    </GroupBox>

    <StackPanel Orientation="Horizontal" Grid.Row="2" Margin="0,6,0,0">
      <CheckBox x:Name="cbDry" Content="Dry Run (show command only)" VerticalAlignment="Center"/>
      <Button x:Name="btnBuild" Content="Build EXE" Margin="12,0,0,0" Padding="12,4" FontWeight="Bold"/>
      <Button x:Name="btnClose" Content="Close" Margin="6,0,0,0" Padding="12,4"/>
      <TextBlock x:Name="lblPyI" Margin="12,0,0,0" VerticalAlignment="Center" FontStyle="Italic" Foreground="Gray"/>
    </StackPanel>
  </Grid>
</Window>
"@

# ----------------------------------------
# ---------- Parse XAML & Wire Up UI ----------
# ----------------------------------------

$reader = [System.Xml.XmlReader]::Create([System.IO.StringReader]::new($xaml))
$window = [XamlReader]::Load($reader)

$tbProject   = $window.FindName("tbProject")
$tbOrg       = $window.FindName("tbOrg")
$tbDesc      = $window.FindName("tbDesc")
$tbRepo      = $window.FindName("tbRepo")
$tbExe       = $window.FindName("tbExe")
$tbVersion   = $window.FindName("tbVersion")
$btnSuggest  = $window.FindName("btnSuggest")

$tbEntry        = $window.FindName("tbEntry")
$tbIcon         = $window.FindName("tbIcon")
$btnBrowseEntry = $window.FindName("btnBrowseEntry")
$btnBrowseIcon  = $window.FindName("btnBrowseIcon")

$tbSrc          = $window.FindName("tbSrc")
$tbDest         = $window.FindName("tbDest")
$btnBrowseSrc   = $window.FindName("btnBrowseSrc")
$lbAddData      = $window.FindName("lbAddData")
$btnAddPair     = $window.FindName("btnAddPair")
$btnRemovePair  = $window.FindName("btnRemovePair")

$tbLog      = $window.FindName("tbLog")
$cbDry      = $window.FindName("cbDry")
$btnBuild   = $window.FindName("btnBuild")
$btnClose   = $window.FindName("btnClose")
$lblPyI     = $window.FindName("lblPyI")

# Pre-populate defaults
$tbProject.Text  = 'StreamNook'
$tbOrg.Text      = 'StreamNook Project'
$tbDesc.Text     = 'StreamNook - Open-source Twitch Stream Viewer with Discord Rich Presence'
$tbRepo.Text     = 'https://github.com/winters27/StreamNook'
$tbExe.Text      = 'StreamNook'
$tbVersion.Text  = Suggest-NextVersion
$tbEntry.Text    = '.\main.py'
$tbIcon.Text     = 'assets\icons\icon_256x256.ico'
$lbAddData.Items.Add('assets -> assets')
$lbAddData.Items.Add('data -> data')

function UI-Log([string]$msg) {
    $tbLog.AppendText("$msg`r`n")
    $tbLog.ScrollToEnd()
}

function Pick-File([string]$filter = 'Python Scripts (*.py)|*.py|All files (*.*)|*.*') {
    $dlg = New-Object System.Windows.Forms.OpenFileDialog
    $dlg.Filter = $filter
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $dlg.FileName }
    return $null
}

function Pick-Folder {
    $dlg = New-Object System.Windows.Forms.FolderBrowserDialog
    if ($dlg.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { return $dlg.SelectedPath }
    return $null
}

$btnSuggest.Add_Click({ $tbVersion.Text = Suggest-NextVersion })

$btnBrowseEntry.Add_Click({
    $f = Pick-File
    if ($f) { $tbEntry.Text = (Resolve-Path $f).Path }
})

$btnBrowseIcon.Add_Click({
    $f = Pick-File 'Icons (*.ico)|*.ico|All files (*.*)|*.*'
    if ($f) { $tbIcon.Text = (Resolve-Path $f).Path }
})

$btnBrowseSrc.Add_Click({
    # Allow picking file or folder for source
    $f = Pick-File
    if (-not $f) { $f = Pick-Folder }
    if ($f) { $tbSrc.Text = (Resolve-Path $f).Path }
})

$btnAddPair.Add_Click({
    $src = $tbSrc.Text.Trim()
    $dst = $tbDest.Text.Trim()
    if (-not $src -or -not $dst) { UI-Log "Please fill both SOURCE and DEST."; return }
    [void]$lbAddData.Items.Add("$src -> $dst")
    $tbSrc.Text = ''; $tbDest.Text = ''
})

$btnRemovePair.Add_Click({
    while ($lbAddData.SelectedItems.Count -gt 0) { $lbAddData.Items.Remove($lbAddData.SelectedItem) }
})

$btnClose.Add_Click({ $window.Close() })

# Detect PyInstaller now
$pyi = Find-PyInstaller
if ($pyi) {
    $lblPyI.Text = "PyInstaller detected: $($pyi.Version)"
}
else {
    $lblPyI.Text = "PyInstaller NOT found — install with:  py -m pip install pyinstaller"
}

# ----------------------------------------
# ---------- BUILD ACTION (Refactored) ----------
# ----------------------------------------

function Invoke-Build {
    param(
        [Parameter(Mandatory)] [hashtable]$BuildParams,
        [Parameter(Mandatory)] [hashtable]$PyInstallerInfo,
        [Parameter(Mandatory)] [scriptblock]$Logger,
        [switch]$IsDryRun,
        [switch]$IsInteractive
    )

    $Error.Clear()
    try {
        if (-not $PyInstallerInfo) { throw "PyInstaller not found. Install with:  py -m pip install pyinstaller" }

        # --- 1. Validation & Version Info ---
        $params = New-Object psobject -Property $BuildParams
        if (-not $params.Project) { throw "Project name is required." }
        if (-not $params.Org) { throw "Organization is required." }
        if (-not $params.Desc) { throw "Description is required." }
        if (-not $params.ExeName) { throw "EXE name is required." }
        if (-not (Test-Path -LiteralPath $params.Entry)) { throw "Entry script not found: $($params.Entry)" }
        if (-not (Test-Path -LiteralPath $params.Icon)) { throw "Icon not found: $($params.Icon)" }

        $tuple = Parse-VersionTuple $params.Version
        $year = (Get-Date).Year

        # --- 2. Generate version_info.txt ---
        & $Logger "Generating version_info.txt for version $($params.Version) …"
        $content = New-VersionInfoContent -Org $params.Org -Description $params.Desc -Version $params.Version -Project $params.Project -ExeName $params.ExeName -RepoUrl $params.Repo -TupleStr $tuple -Year $year
        $content | Out-File -FilePath "version_info.txt" -Encoding UTF8
        & $Logger "version_info.txt written."

        $sep = Get-AddDataSeparator $PyInstallerInfo.Cmd
        & $Logger "Using add-data separator '$sep'."

        # --- 3. Build Argument List (OPTIMIZED FOR SIZE) ---
        $args = @(
            '--onefile',
            '--windowed',
            '--strip'
        )
        
        # Collect Discord RPC dependencies
        $args += '--collect-all=discordrpc'
        
        # For PySide6, only collect what's actually needed instead of everything
        # Using hidden-import instead of collect-all to avoid pulling 100+ MB of unused Qt modules
        $args += '--hidden-import=PySide6.QtCore'
        $args += '--hidden-import=PySide6.QtGui'
        $args += '--hidden-import=PySide6.QtWidgets'
        
        # Exclude only the really big modules you definitely don't use
        # Being more conservative here to avoid breaking urllib/ssl
        $args += '--exclude-module=tkinter'
        $args += '--exclude-module=matplotlib'
        $args += '--exclude-module=PIL'
        
        # Enable UPX compression if available
        if ($UpxDir -and (Test-Path $UpxDir)) {
            & $Logger "Enabling UPX compression from: $UpxDir"
            $args += "--upx-dir=$UpxDir"
        } else {
            & $Logger "UPX not found - build will be larger. Install with: choco install upx"
        }
        
        foreach ($item in $params.AddData) {
            if ($item -match '^(?<src>.+?)\s*->\s*(?<dst>.+)$') {
                $src = $Matches.src
                $dst = $Matches.dst
                $args += '--add-data'
                $args += ("{0}{1}{2}" -f $src, $sep, $dst)
            }
        }
        $args += "--icon=$($params.Icon)"
        $args += '--version-file'; $args += 'version_info.txt'
        $args += '--name'; $args += $params.ExeName
        $args += $params.Entry

        & $Logger ("Command: " + (($PyInstallerInfo.Cmd -join ' ') + ' ' + ($args -join ' ')))

        if ($IsDryRun) {
            & $Logger "[DRY RUN] Skipping PyInstaller execution."
            return
        }

        # --- 4. Run Build ---
        if ($IsInteractive) {
            # --- Run in Background Job (for non-freezing UI) ---
            & $Logger "Starting build job… UI will remain responsive."
            $btnBuild.IsEnabled = $false

            $buildScriptBlock = {
                param($pyiCmd, $buildArgs, $workingDir)
                Set-Location -Path $workingDir
                & @($pyiCmd) @($buildArgs) *>&1
            }
            $job = Start-Job -ScriptBlock $buildScriptBlock -ArgumentList @($PyInstallerInfo.Cmd, $args, $scriptRoot)

            $timer = New-Object System.Windows.Threading.DispatcherTimer
            $timer.Interval = [TimeSpan]::FromMilliseconds(200)
            $timer.Add_Tick({
                Receive-Job -Job $job -Keep | ForEach-Object { & $Logger $_.ToString() }
                if ($job.State -ne 'Running') {
                    $timer.Stop()
                    Receive-Job -Job $job | ForEach-Object { & $Logger $_.ToString() }
                    & $Logger "Build job finished with state: $($job.State)."
                    if ($job.State -eq 'Failed') { & $Logger "ERROR: Check job error details." }
                    
                    Post-Build-Verification -ExeName $params.ExeName -Logger $Logger
                    
                    $btnBuild.IsEnabled = $true
                    Remove-Job -Job $job
                }
            })
            $timer.Start()
        }
        else {
            # --- Run Synchronously (for CI/CD) ---
            & $Logger "Starting synchronous build..."
            & @($PyInstallerInfo.Cmd) @($args)
            if ($LASTEXITCODE -ne 0) {
                throw "PyInstaller failed with exit code $LASTEXITCODE."
            }
            & $Logger "PyInstaller finished."
            Post-Build-Verification -ExeName $params.ExeName -Logger $Logger
        }
    }
    catch {
        & $Logger ("ERROR: " + $_.Exception.Message)
        if ($IsInteractive) { $btnBuild.IsEnabled = $true }
        else { exit 1 } # Exit with error code in non-interactive mode
    }
}

function Post-Build-Verification {
    param([string]$ExeName, [scriptblock]$Logger)
    
    $dist = Join-Path -Path (Resolve-Path '.\dist').Path -ChildPath "$ExeName.exe"
    if (Test-Path -LiteralPath $dist) {
        $sizeBytes = (Get-Item $dist).Length
        $sizeMB = [math]::Round($sizeBytes / 1MB, 2)
        & $Logger "Build complete: $dist ($sizeMB MB)"
        try {
            $vi = (Get-Item $dist).VersionInfo
            & $Logger ("  CompanyName: " + $vi.CompanyName)
            & $Logger ("  FileDescription: " + $vi.FileDescription)
            & $Logger ("  FileVersion: " + $vi.FileVersion)
            & $Logger ("  ProductName: " + $vi.ProductName)
            & $Logger ("  ProductVersion: " + $vi.ProductVersion)
        } catch {
            & $Logger "Could not read VersionInfo from built EXE. $_"
        }
    } else {
        & $Logger "Built EXE not found in dist\ — check the log above."
    }
}

# ----------------------------------------
# ---------- Main Execution Logic ----------
# ----------------------------------------

if ($NonInteractive) {
    # --- Non-Interactive / CI/CD Mode ---
    $pyi = Find-PyInstaller
    $buildParams = @{
        Project     = 'StreamNook'
        Org         = 'StreamNook Project'
        Desc        = 'StreamNook - Open-source Twitch Stream Viewer with Discord Rich Presence'
        Repo        = 'https://github.com/winters27/StreamNook'
        ExeName     = 'StreamNook'
        Version     = if ($VersionOverride) { $VersionOverride } else { Suggest-NextVersion }
        Entry       = '.\main.py'
        Icon        = 'assets\icons\icon_256x256.ico'
        AddData     = @('assets -> assets', 'data -> data')
    }
    Invoke-Build -BuildParams $buildParams -PyInstallerInfo $pyi -Logger { param($msg) Write-Host $msg } -IsInteractive:$false
}
else {
    # --- Interactive / GUI Mode ---
    $btnBuild.Add_Click({
        $buildParams = @{
            Project     = $tbProject.Text.Trim()
            Org         = $tbOrg.Text.Trim()
            Desc        = $tbDesc.Text.Trim()
            Repo        = $tbRepo.Text.Trim()
            ExeName     = $tbExe.Text.Trim()
            Version     = $tbVersion.Text.Trim()
            Entry       = $tbEntry.Text.Trim()
            Icon        = $tbIcon.Text.Trim()
            AddData     = @($lbAddData.Items)
        }
        Invoke-Build -BuildParams $buildParams -PyInstallerInfo $pyi -Logger $UI-Log -IsDryRun:$cbDry.IsChecked -IsInteractive:$true
    })

    $window.ShowDialog() | Out-Null
}