; OT Range — Windows installer
;
; Produces a single Setup.exe (built by Inno Setup — https://jrsoftware.org)
; that adds a Start Menu / Desktop shortcut launching the control panel.
; Requires Docker Desktop with WSL2 integration; see README.md's
; "Windows" section for why (this range's router container needs raw
; packet capture, a Linux-container thing regardless of host OS, and
; Docker Desktop itself needs WSL2 or Hyper-V on Windows anyway).
;
; This installer does NOT bundle the repository. The shortcut it
; creates clones the repo into WSL on first launch (and `git pull`s on
; every later launch), so it always runs whatever is on the `master`
; branch instead of going stale the day after you build the installer.
; See the [Icons] section's shell one-liner.
;
; Build: install Inno Setup, then `iscc ot-range.iss` from this
; directory (or see .github/workflows/windows-installer.yml, which
; does exactly that on a real Windows CI runner on every change here).
;
; Honest status: compiled and validated on GitHub's windows-latest
; runners in CI. NOT end-to-end tested on a real Windows machine with
; WSL2 + Docker Desktop actually installed — that combination isn't
; available in this project's development environment. If the shortcut
; doesn't behave as documented, please open an issue.

#define MyAppName "OT Range Control Panel"
#define MyAppVersion "1.0"
#define MyAppPublisher "ot-range-maintainers"
#define MyAppURL "https://github.com/Kahler2315/ot-range"
#define RepoURL "https://github.com/Kahler2315/ot-range.git"

; The full command WSL runs: clone the repo if it's not already
; present in the WSL home directory, otherwise pull the latest, then
; hand off to start-panel.sh (which does its own venv setup on first
; run and opens the browser). `|| true` on the pull keeps this working
; offline once already cloned. Inlined here (not a script committed to
; the repo) because it has to work *before* the repo exists locally.
#define BootstrapCmd "bash -lc ""if [ -d ~/ot-range ]; then cd ~/ot-range && git pull -q || true; else git clone -q " + RepoURL + " ~/ot-range && cd ~/ot-range; fi && ./start-panel.sh"""

[Setup]
AppId={{9F3E7B1A-6C2D-4A8E-9B0E-3F5C7D9A1B2E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\OT Range
DefaultGroupName=OT Range
DisableProgramGroupPage=yes
DisableDirPage=yes
DisableReadyPage=yes
OutputDir=dist
OutputBaseFilename=OT-Range-Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
; No admin rights needed — this only writes shortcuts, nothing under
; Program Files gets executed, WSL/Docker do the real work.
PrivilegesRequired=lowest
WizardStyle=modern
UninstallDisplayName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{sys}\wsl.exe"; Parameters: "{#BootstrapCmd}"; IconFilename: "{sys}\wsl.exe"; Comment: "Simulated OT/ICS cyber range — see SECURITY.md"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{sys}\wsl.exe"; Parameters: "{#BootstrapCmd}"; IconFilename: "{sys}\wsl.exe"; Tasks: desktopicon

[Code]
function InitializeSetup(): Boolean;
var
  ResultCode: Integer;
  WslFound: Boolean;
begin
  Result := True;
  WslFound := Exec(ExpandConstant('{sys}\where.exe'), 'wsl.exe', '', SW_HIDE,
    ewWaitUntilTerminated, ResultCode) and (ResultCode = 0);
  // Skip the message box on unattended (/SILENT or /VERYSILENT) installs —
  // IT departments pushing this via group policy shouldn't hang on a
  // blocking dialog. The shortcut still gets created either way.
  if (not WslFound) and (not WizardSilent()) then
  begin
    MsgBox(
      'This range runs inside WSL2 (Windows Subsystem for Linux), which ' +
      'wasn''t detected on this PC.' + #13#10 + #13#10 +
      'You can still finish this install — the shortcut just won''t work ' +
      'until WSL2 and Docker Desktop (with WSL integration enabled) are ' +
      'installed. Open an Administrator PowerShell and run:' + #13#10 +
      '    wsl --install' + #13#10 +
      'then install Docker Desktop from docker.com, restart, and the ' +
      'shortcut this installer creates will start working.',
      mbInformation, MB_OK);
  end;
end;
