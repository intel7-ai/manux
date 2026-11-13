"""PC command catalogue and validation. Data only; execution belongs to output/windows."""
MAX_SCRIPT_UNITS = 8000


def script_problem(value):
    if not value.strip():
        return "PowerShell 명령이 비어 있어요"
    if "\0" in value:
        return "PowerShell 명령에 잘못된 문자가 있어요"
    if len(value.encode("utf-16-le")) > MAX_SCRIPT_UNITS * 2:
        return "명령이 너무 길어요. .ps1 파일로 저장한 뒤 그 파일을 호출해 주세요"
    return None


SYSTEM_ACTIONS = {}


def _add(key, label, kind, value, *, warning=""):
    SYSTEM_ACTIONS[key] = dict(label=label, kind=kind, value=value, warning=warning)


for key, label, args in (
    ("shutdown", "컴퓨터 종료 · 셧다운", "/s /t 0"),
    ("restart", "컴퓨터 다시 시작 · 재부팅", "/r /t 0"),
    ("sign_out", "Windows 로그아웃", "/l"),
    ("hibernate", "최대 절전", "/h"),
    ("cancel_shutdown", "Windows에 예약된 종료 취소", "/a"),
):
    _add(key, label, "powershell", '& "$env:SystemRoot\\System32\\shutdown.exe" ' + args,
         warning="현재 작업과 Windows 세션에 영향을 줍니다" if key != "cancel_shutdown" else "")
_add("lock", "컴퓨터 잠금", "hotkey", "win+l")
_add("sleep", "컴퓨터 절전", "powershell", '''Add-Type -TypeDefinition 'using System; using System.Runtime.InteropServices; public static class ManuxPower { [DllImport("powrprof.dll", SetLastError=true)] public static extern bool SetSuspendState(bool hibernate, bool force, bool disableWake); }'
if (-not [ManuxPower]::SetSuspendState($false, $false, $false)) { throw 'Windows가 절전 요청을 거부했습니다' }''', warning="PC가 절전 상태로 전환됩니다. 장치의 절전 지원이 필요합니다")

for key, label, uri in (
    ("settings", "Windows 설정", ""),
    ("display", "디스플레이·밝기 설정", "display"),
    ("sound", "소리·출력·마이크 설정", "sound"),
    ("volume_mixer", "앱별 볼륨 믹서", "apps-volume"),
    ("bluetooth", "Bluetooth 설정", "bluetooth"),
    ("wifi", "Wi-Fi 설정", "network-wifi"),
    ("ethernet", "유선 네트워크 설정", "network-ethernet"),
    ("vpn", "VPN 설정", "network-vpn"),
    ("proxy", "프록시 설정", "network-proxy"),
    ("hotspot", "모바일 핫스팟 설정", "network-mobilehotspot"),
    ("network", "네트워크 상태", "network-status"),
    ("power", "전원·배터리 설정", "powersleep"),
    ("storage", "저장소 설정", "storagesense"),
    ("apps", "설치된 앱 관리", "appsfeatures"),
    ("default_apps", "기본 앱 설정", "defaultapps"),
    ("startup_apps", "시작 프로그램 설정", "startupapps"),
    ("notifications", "알림 설정", "notifications"),
    ("mouse", "마우스 설정", "mousetouchpad"),
    ("touchpad", "터치패드 설정", "devices-touchpad"),
    ("printers", "프린터·스캐너 설정", "printers"),
    ("camera", "카메라 권한", "privacy-webcam"),
    ("microphone", "마이크 권한", "privacy-microphone"),
    ("privacy", "개인정보 설정", "privacy"),
    ("accounts", "계정 설정", "yourinfo"),
    ("signin", "로그인 옵션", "signinoptions"),
    ("date_time", "날짜·시간 설정", "dateandtime"),
    ("language", "언어 설정", "regionlanguage"),
    ("accessibility", "접근성 설정", "easeofaccess"),
    ("personalization", "개인 설정", "personalization"),
    ("background", "배경 화면 설정", "personalization-background"),
    ("taskbar", "작업 표시줄 설정", "taskbar"),
    ("windows_update", "Windows 업데이트 설정", "windowsupdate"),
    ("recovery", "복구 설정", "recovery"),
    ("about", "PC 사양·정보", "about"),
):
    _add("settings_" + key, label + " 열기", "launch_app", "ms-settings:" + uri)

for key, label, target in (
    ("task_manager", "작업 관리자", "taskmgr.exe"),
    ("explorer", "파일 탐색기", "explorer.exe"),
    ("control_panel", "제어판", "control.exe"),
    ("device_manager", "장치 관리자", "devmgmt.msc"),
    ("disk_management", "디스크 관리", "diskmgmt.msc"),
    ("services", "서비스 관리", "services.msc"),
    ("event_viewer", "이벤트 뷰어", "eventvwr.msc"),
    ("system_info", "시스템 정보", "msinfo32.exe"),
    ("resource_monitor", "리소스 모니터", "resmon.exe"),
    ("task_scheduler", "작업 스케줄러", "taskschd.msc"),
    ("firewall", "방화벽 관리", "wf.msc"),
    ("calculator", "계산기", "calc.exe"),
    ("notepad", "메모장", "notepad.exe"),
):
    _add("open_" + key, label + " 열기", "launch_app", target)


def capability_prompt():
    lines = [
        "PC automation: presets are examples, NOT the limit of supported commands.",
        "Choose a built-in system_action, hotkey, launch_app or macro when it fits; otherwise write a powershell script.",
        "Never answer 'not in the catalogue / 찾을 수 없다' just because there is no preset. PowerShell can combine",
        "cmdlets, native executables, pipelines, variables, .NET APIs and existing .ps1 scripts.",
        "Do not invent a cmdlet, installed application, account, path, password or successful execution.",
        "A missing required target or credential needs a precise question; OS permissions and hardware still apply.",
        "- system_action: one exact ID below. Settings IDs OPEN the settings page; they do not toggle that setting.",
        "- powershell: raw PowerShell script in action_value (no Markdown fences, no JSON wrapper inside the string).",
        "  Runs in PowerShell 7 when installed, otherwise Windows PowerShell 5.1, -NoProfile -NonInteractive,",
        "  under the current Windows account. Use compatible syntax or state a required module/version in reason.",
        "  Use $env:USERPROFILE, [Environment]::GetFolderPath(...) and -LiteralPath for user paths.",
        "  Do not use Read-Host, credentials in the script, ExecutionPolicy Bypass or automatic privilege elevation.",
        "  For optional dependencies use Get-Command/Get-Module/Test-Path and report what must be installed.",
        "  Never install software/modules or make unrelated changes unless the user asked for them.",
        "  Return requested information with Write-Output; completion and output appear in the command result panel.",
        "  Long work runs in the background (maximum 10 minutes). Split longer jobs or call the user's existing tool.",
        "  Keep within 8000 UTF-16 code units; for a longer script ask for a saved .ps1 path.",
        "  All PowerShell and session-changing proposals require the user's existing review checkbox; saving never runs them.",
        "Power: computer shutdown / 셧다운 = system_action shutdown; restart = restart; log out = sign_out;",
        "  lock = lock; sleep = sleep; hibernate = hibernate. Use these for power requests, not Alt+F4 or shell key sequences.",
        "  Explain that shutdown/restart affects the whole PC. Do not add force flags or change power policy.",
        "File operations: Get-ChildItem, Get-Item, Test-Path, New-Item, Copy-Item, Move-Item, Rename-Item, Remove-Item;",
        "  Get-Content, Set-Content, Add-Content, Select-String, Compress-Archive, Expand-Archive, Get-FileHash.",
        "Processes/apps: Get-Process, Start-Process, Stop-Process (only the named target), Get-Command, Get-AppxPackage;",
        "  use the program's installed CLI/API for program-specific operations; otherwise ask for its shortcut or target.",
        "Services/tasks: Get-Service, Start-Service, Stop-Service, Restart-Service, Get-ScheduledTask, Start-ScheduledTask.",
        "Network: Get-NetAdapter, Get-NetIPConfiguration, Get-NetIPAddress, Get-DnsClientServerAddress, Test-Connection,",
        "  Test-NetConnection, Resolve-DnsName, Get-NetTCPConnection; Enable/Disable-NetAdapter and DNS/IP changes require",
        "  an explicit adapter/target and may need administrator rights. Do not disconnect the user as an extra step.",
        "System/hardware: Get-ComputerInfo, Get-CimInstance, Get-Volume, Get-Disk, Get-PSDrive, Get-WinEvent, Get-Date.",
        "Brightness can use root/WMI WmiMonitorBrightnessMethods only when hardware supports it; do not claim all monitors support it.",
        "Configuration: registry provider Get/Set/New-ItemProperty, environment variables, Windows services, scheduled tasks,",
        "  firewall cmdlets and application CLIs are available through powershell; ask for missing targets and explain permission needs.",
        "Network/API automation: Invoke-RestMethod, Invoke-WebRequest, ConvertTo/From-Json, Export/Import-Csv.",
        "Never send local files, credentials or device identifiers to a remote service unless the user requested that transfer.",
        "A request merely to connect a function is not a request to execute it during registration.",
        "For a macro, step powershell runs a script and waits for completion; step system uses a system_action ID.",
        "Use gesture_key __new__ and new_posture for a named custom hand shape; a posture ID is NOT a gesture key.",
        "System action IDs:",
    ]
    lines += [f"- {key}: {row['label']}" for key, row in SYSTEM_ACTIONS.items()]
    return "\n".join(lines)
