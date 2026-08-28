#ifndef AppVersion
  #error AppVersion must be provided by the release builder
#endif
#ifndef PayloadDir
  #error PayloadDir must be provided by the release builder
#endif
#ifndef OutputDir
  #error OutputDir must be provided by the release builder
#endif

#define AppName "ELM Memory"
#define AppPublisher "Miawd3"
#define AppURL "https://github.com/Miawd3/elm-memory"

[Setup]
AppId={{9C9B3722-BE57-46DA-8AFE-B8585D6F5C8A}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={localappdata}\Programs\ELM Memory
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
ChangesEnvironment=yes
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
OutputDir={#OutputDir}
OutputBaseFilename=ELM-Memory-{#AppVersion}-windows-x64-setup
LicenseFile={#PayloadDir}\LICENSE
UninstallDisplayIcon={app}\elm.exe
VersionInfoVersion={#AppVersion}.0
VersionInfoProductName={#AppName}
VersionInfoDescription=Local memory for coding agents

[Tasks]
Name: "addtopath"; Description: "Add ELM commands to my user PATH"; Flags: checkedonce

[Files]
Source: "{#PayloadDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\ELM Documentation"; Filename: "{app}\README.md"
Name: "{group}\Uninstall ELM Memory"; Filename: "{uninstallexe}"

[Code]
function CleanPathEntry(Value: String): String;
begin
  Result := RemoveBackslashUnlessRoot(Trim(Value));
end;

function PathsMatch(Left, Right: String): Boolean;
begin
  Result := CompareText(CleanPathEntry(Left), CleanPathEntry(Right)) = 0;
end;

function RewriteUserPath(AppPath: String; AddEntry: Boolean): Boolean;
var
  ExistingPath: String;
  Remaining: String;
  Segment: String;
  NewPath: String;
  SeparatorAt: Integer;
  Found: Boolean;
begin
  if not RegQueryStringValue(HKCU, 'Environment', 'Path', ExistingPath) then
    ExistingPath := '';
  Remaining := ExistingPath;
  NewPath := '';
  Found := False;
  repeat
    SeparatorAt := Pos(';', Remaining);
    if SeparatorAt > 0 then begin
      Segment := Copy(Remaining, 1, SeparatorAt - 1);
      Delete(Remaining, 1, SeparatorAt);
    end else begin
      Segment := Remaining;
      Remaining := '';
    end;
    if PathsMatch(Segment, AppPath) then
      Found := True
    else if Trim(Segment) <> '' then begin
      if NewPath <> '' then
        NewPath := NewPath + ';';
      NewPath := NewPath + Segment;
    end;
  until Remaining = '';

  if AddEntry and not Found then begin
    if NewPath <> '' then
      NewPath := NewPath + ';';
    NewPath := NewPath + AppPath;
  end;
  Result := RegWriteExpandStringValue(HKCU, 'Environment', 'Path', NewPath);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if (CurStep = ssPostInstall) and WizardIsTaskSelected('addtopath') then
    if not RewriteUserPath(ExpandConstant('{app}'), True) then
      MsgBox('ELM was installed, but Setup could not update your user PATH.', mbError, MB_OK);
end;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if CurUninstallStep = usUninstall then
    RewriteUserPath(ExpandConstant('{app}'), False);
end;
