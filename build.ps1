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
          <TextBlock Text="Entry Script:" VerticalAlignment="Center" Width="100"/>
          <TextBox x:Name="tbEntry" Width="520"/>
          <Button x:Name="btnBrowseEntry" Content="Browse..." Margin="6,0,0,0" Width="80"/>
        </StackPanel>

        <StackPanel Orientation="Horizontal" Grid.Row="1" Margin="0,6,0,0">
          <TextBlock Text="Icon (.ico):" VerticalAlignment="Center" Width="100"/>
          <TextBox x:Name="tbIcon" Width="520"/>
          <Button x:Name="btnBrowseIcon" Content="Browse..." Margin="6,0,0,0" Width="80"/>
        </StackPanel>

        <GroupBox Header="Add-Data (SOURCE -> DEST)" Grid.Row="2" Margin="0,6,0,0">
          <Grid>
            <Grid.ColumnDefinitions>
              <ColumnDefinition Width="*"/>
              <ColumnDefinition Width="Auto"/>
            </Grid.ColumnDefinitions>
            <Grid.RowDefinitions>
              <RowDefinition Height="*"/>
              <RowDefinition Height="Auto"/>
            </Grid.RowDefinitions>
            <ListBox x:Name="lbAddData" Grid.Row="0" Grid.ColumnSpan="2" />
            <StackPanel Orientation="Horizontal" Grid.Row="1" Grid.Column="0" Margin="0,6,0,0">
              <TextBox x:Name="tbSrc" Width="300" Margin="0,0,6,0"/>
              <TextBox x:Name="tbDest" Width="180" Margin="0,0,6,0"/>
              <Button x:Name="btnAddPair" Content="Add" Width="80"/>
              <Button x:Name="btnRemovePair" Content="Remove Selected" Margin="6,0,0,0" Width="130"/>
              <Button x:Name="btnBrowseSrc" Content="Browse Source" Margin="6,0,0,0" Width="120"/>
            </StackPanel>
          </Grid>
        </GroupBox>
      </Grid>
    </StackPanel>

    <GroupBox Grid.Row="1" Header="Build Log">
      <ScrollViewer VerticalScrollBarVisibility="Auto">
        <TextBox x:Name="tbLog" IsReadOnly="True" AcceptsReturn="True" TextWrapping="Wrap" FontFamily="Consolas" Height="260"/>
      </ScrollViewer>
    </GroupBox>

    <DockPanel Grid.Row="2" LastChildFill="False" Margin="0,8,0,0">
      <CheckBox x:Name="cbDry" Content="Dry Run (don't build)" Margin="0,0,12,0"/>
      <TextBlock x:Name="lblPyI" VerticalAlignment="Center" Foreground="Gray" Margin="0,0,12,0"/>
      <Button x:Name="btnBuild" Content="Build" Width="120" Margin="0,0,6,0"/>
      <Button x:Name="btnClose" Content="Close" Width="80"/>
    </DockPanel>
  </Grid>
</Window>
"@

# ----------------------------------------
# ---------- UI Init and Logic ----------
# ----------------------------------------

# Load XAML
$window = [XamlReader]::Parse($xaml)

# Get control references dynamically
$controls = @(
    'tbProject', 'tbOrg', 'tbDesc', 'tbRepo', 'tbExe', 'tbVersion',
    'btnSuggest', 'tbEntry', 'btnBrowseEntry', 'tbIcon', 'btnBrowseIcon',
    'lbAddData', 'tbSrc', 'tbDest', 'btnAddPair', 'btnRemovePair', 'btnBrowseSrc',
    'tbLog', 'cbDry', 'btnBuild', 'btnClose', 'lblPyI'
)
$controls | ForEach-Object {
    Set-Variable -Name $_ -Value $window.FindName($_)
}

# Defaults
$tbProject.Text = 'StreamNook'
$tbOrg.Text = 'StreamNook'
$tbDesc.Text = 'StreamNook - Open-source Twitch Client'
$tbRepo.Text = 'https://github.com/winters27/StreamNook'
$tbExe.Text = 'StreamNook'
$tbEntry.Text = '.\main.py'
$tbIcon.Text = 'assets\icons\icon_256x256.ico'
@('assets', 'data') | ForEach-Object { [void]$lbAddData.Items.Add("$_ -> $_") }
$tbVersion.Text = Suggest-NextVersion

# Logging Helper
function UI-Log($msg) {
    $tbLog.AppendText((Get-Date).ToString('HH:mm:ss ') + $msg + "`r`n")
    $tbLog.ScrollToEnd()
}
UI-Log "Working directory: $scriptRoot"

# File/Folder Pickers (cleaner, use splatting, set InitialDirectory)
function Pick-File([string]$filter = 'All files (*.*)|*.*') {
    $dlg = [OpenFileDialog]@{
        Filter = $filter
        Multiselect = $false
        InitialDirectory = $scriptRoot
    }
    if ($dlg.ShowDialog() -eq [DialogResult]::OK) { return $dlg.FileName } else { return $null }
}
function Pick-Folder {
    $dlg = [FolderBrowserDialog]@{
        SelectedPath = $scriptRoot
    }
    if ($dlg.ShowDialog() -eq [DialogResult]::OK) { return $dlg.SelectedPath } else { return $null }
}

# Events
$btnSuggest.Add_Click({ $tbVersion.Text = Suggest-NextVersion })

$btnBrowseEntry.Add_Click({
    $f = Pick-File 'Python (*.py)|*.py|All files (*.*)|*.*'
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

        # --- 3. Build Argument List ---
        $args = @('--onefile', '--windowed', '--strip', '--collect-all=discordrpc', '--collect-all=PySide6')
        if ($UpxDir -and (Test-Path $UpxDir)) {
            & $Logger "Enabling UPX compression from: $UpxDir"
            $args += "--upx-dir=$UpxDir"
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
        & $Logger "Build complete: $dist"
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
