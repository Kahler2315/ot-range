; OT Range — Windows installer
;
; Produces a single Setup.exe (built by Inno Setup — https://jrsoftware.org)
; that adds a Start Menu / Desktop shortcut launching the control panel.
; Requires Docker Desktop with WSL2 integration; see README.md's
; "Windows" section for why (this range's router container needs raw
; packet capture, a Linux-container thing regardless of host OS, and
; Docker Desktop itself needs WSL2 or Hyper-V on Windows anyway).
;
; This installer does NOT bundle the repository itself — only the tiny
; bootstrap.sh alongside this file. The shortcut runs that script
; inside WSL, which clones the repo on first launch (and `git pull`s
; on every later launch) before handing off to start-panel.sh, so the
; shortcut always runs whatever's on `master` instead of going stale
; the day after this installer ships.
;
; The shortcut's Parameters are built at install time by a [Code]
; function (GetBootstrapParams below), not a hardcoded string — a
; static "wsl.exe bash -lc '...multi-clause command with its own
; quoting...'" runs straight into a real mess: Inno's preprocessor and
; its ini-file parser both use double-quotes with their own doubling
; escape rules, and getting a shell one-liner through both layers
; intact needs quadrupled quotes that are nearly impossible to hand
; -verify without a compiler. Pointing at a real script file sidesteps
; all of it — the only string built at install time is a single quoted
; path, and {code:...} substitution happens after ini-parsing, so
; nothing here needs escaping at all.
;
; Build: install Inno Setup, then `iscc ot-range.iss` from this
; directory (or see ../.github/workflows/windows-installer.yml, which
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
DisableReadyPage=yes
OutputDir=dist
OutputBaseFilename=OT-Range-Setup
Compression=lzma
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
; No admin rights needed — this only writes files under the per-user
; install dir plus shortcuts; WSL/Docker do the actual work.
PrivilegesRequired=lowest
WizardStyle=modern
UninstallDisplayName={#MyAppName}

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"; GroupDescription: "Additional icons:"

[Files]
Source: "bootstrap.sh"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{sys}\wsl.exe"; Parameters: "{code:GetBootstrapParams}"; IconFilename: "{sys}\wsl.exe"; Comment: "Simulated OT/ICS cyber range — see SECURITY.md"
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{sys}\wsl.exe"; Parameters: "{code:GetBootstrapParams}"; IconFilename: "{sys}\wsl.exe"; Tasks: desktopicon

[Code]
// WSL2 auto-mounts Windows drives under /mnt/<lowercase-drive-letter>,
// e.g. C:\Program Files\OT Range -> /mnt/c/Program Files/OT Range.
// Reading bootstrap.sh from there (rather than copying it into the
// WSL filesystem) is fine — it's a few lines, read once per launch,
// before the real work happens natively inside ~/ot-range.
function WslPath(WinPath: String): String;
var
  DriveLetter: String;
  Rest: String;
begin
  DriveLetter := Lowercase(Copy(WinPath, 1, 1));
  Rest := Copy(WinPath, 3, Length(WinPath) - 2); // strip "C:"
  StringChangeEx(Rest, '\', '/', True);
  Result := '/mnt/' + DriveLetter + Rest;
end;

// Returns the full Parameters string for the wsl.exe shortcut. Built
// here, not as a static [Icons] value, so the only quoting involved
// is one pair around a path — see this file's header comment.
function GetBootstrapParams(Param: String): String;
var
  ScriptPath: String;
begin
  ScriptPath := WslPath(ExpandConstant('{app}')) + '/bootstrap.sh';
  Result := 'bash "' + ScriptPath + '"';
end;

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
