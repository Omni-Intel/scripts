#Requires -Version 5.1
#Requires -RunAsAdministrator
<#
Run locally on a Windows seat in administrator Windows PowerShell 5.1.
Changes WinRM, its built-in firewall rules and creates a server certificate.
Does not change account passwords or group membership.
Enable-PSRemoting can also change the machine-wide UAC remote policy.
Without -AllowLocalAdminRemoteToken, restore its value captured at script start.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [System.Net.IPAddress]$ControllerAddress,
    [Parameter(Mandatory = $true)]
    [System.Net.IPAddress]$SeatAddress,
    [string]$LoginUser = $env:USERNAME,
    [switch]$AllowLocalAdminRemoteToken
)
$ErrorActionPreference = 'Stop'
# Check prerequisites before changing WinRM or firewall configuration.
if (-not (Get-NetIPAddress -IPAddress $SeatAddress.IPAddressToString -ErrorAction SilentlyContinue)) {
    throw 'SeatAddress must be an IP address assigned to this computer.'
}
$publicProfiles = @(Get-NetConnectionProfile | Where-Object { $_.NetworkCategory -eq 'Public' })
if ($publicProfiles.Count -gt 0) {
    throw 'Public network profiles detected. Verify that the connected network is trusted and change it to Private before running this bootstrap. The script does not change network profiles automatically.'
}
$user = Get-LocalUser -Name $LoginUser
$adminGroup = Get-LocalGroup -SID 'S-1-5-32-544'
$members = @(Get-LocalGroupMember -Group $adminGroup)
if (-not ($members | Where-Object { $_.SID -eq $user.SID })) {
    throw "This bootstrap expects an existing local administrator. Confirm permissions for $LoginUser first."
}
$root = Join-Path $env:ProgramData 'ExperimentPlatform\bootstrap'
New-Item -ItemType Directory -Path $root -Force | Out-Null
$policyPath = 'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Policies\System'
$oldPolicy = Get-ItemProperty -LiteralPath $policyPath
@{
    Timestamp = (Get-Date).ToString('o')
    LocalAccountTokenFilterPolicy = $oldPolicy.LocalAccountTokenFilterPolicy
    WinRMService = (Get-Service WinRM | Select-Object Status, StartType)
    BuiltInFirewallRules = @(Get-NetFirewallRule -Name 'WINRM-*' -ErrorAction SilentlyContinue |
        Select-Object Name, Enabled, Profile)
} | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath (
    Join-Path $root ('before-' + (Get-Date -Format 'yyyyMMdd-HHmmss') + '.json')) -Encoding UTF8

Write-Host 'Windows WinRM HTTPS bootstrap 1.0.0'
Enable-PSRemoting -SkipNetworkProfileCheck -Force
Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' -ErrorAction SilentlyContinue |
    Set-NetFirewallRule -RemoteAddress $ControllerAddress.IPAddressToString | Out-Null
Get-NetFirewallRule -Name 'WINRM-HTTPS-In-TCP*' -ErrorAction SilentlyContinue |
    Set-NetFirewallRule -RemoteAddress $ControllerAddress.IPAddressToString | Out-Null
# Do not use the PowerShell WSMan: drive. Its initialization can run
# quick configuration again without honoring SkipNetworkProfileCheck.
$wsman = New-Object -ComObject 'WSMan.Automation'
$session = $wsman.CreateSession()
$configBase = 'http://schemas.microsoft.com/wbem/wsman/1/config'
function Set-NativeWinRMValue {
    param([string]$ResourceUri, [string]$PropertyName, [string]$PropertyValue)
    [xml]$document = $session.Get($ResourceUri, 0)
    $node = $document.DocumentElement.SelectSingleNode("*[local-name()='$PropertyName']")
    if ($null -eq $node) { throw "Missing WinRM property: $PropertyName" }
    $node.InnerText = $PropertyValue
    [void]$session.Put($ResourceUri, $document.OuterXml, 0)
    [xml]$verified = $session.Get($ResourceUri, 0)
    if ($verified.DocumentElement.SelectSingleNode("*[local-name()='$PropertyName']").InnerText -ne $PropertyValue) {
        throw "WinRM property not applied: $PropertyName"
    }
}
function Get-NativeWinRMListeners {
    $enumerator = $session.Enumerate("$configBase/Listener", '', '', 0)
    while (-not $enumerator.AtEndOfStream) {
        [xml]$item = $enumerator.ReadItem()
        Write-Output -NoEnumerate $item
    }
}
Set-NativeWinRMValue "$configBase/service" 'AllowUnencrypted' 'false'
Set-NativeWinRMValue "$configBase/service/auth" 'Basic' 'false'
Set-NativeWinRMValue "$configBase/service/auth" 'Negotiate' 'true'

$allListeners = @(Get-NativeWinRMListeners)
$listeners = @($allListeners | Where-Object {
    $_.DocumentElement.SelectSingleNode("*[local-name()='Transport']").InnerText -eq 'HTTPS'
})
if ($listeners.Count -gt 1) {
    throw 'Multiple HTTPS listeners already exist; inspect them before changing this machine.'
}
if ($listeners.Count -eq 0) {
    $certParams = @{
        Type = 'SSLServerAuthentication'
        Subject = "CN=$env:COMPUTERNAME"
        TextExtension = @("2.5.29.17={text}DNS=$env:COMPUTERNAME&IPAddress=$($SeatAddress.IPAddressToString)")
        CertStoreLocation = 'Cert:\LocalMachine\My'
        KeyAlgorithm = 'RSA'
        KeyLength = 2048
        KeyExportPolicy = 'NonExportable'
        NotAfter = (Get-Date).AddMonths(3)
        FriendlyName = 'Experiment management WinRM server'
    }
    $cert = New-SelfSignedCertificate @certParams
    if ($allListeners.Count -eq 0) { throw 'No listener exists after Enable-PSRemoting.' }
    $listenerNamespace = $allListeners[0].DocumentElement.NamespaceURI
    $hostnameXml = [Security.SecurityElement]::Escape($env:COMPUTERNAME)
    $listenerXml = @"
<cfg:Listener xmlns:cfg="$listenerNamespace">
  <cfg:Hostname>$hostnameXml</cfg:Hostname>
  <cfg:CertificateThumbprint>$($cert.Thumbprint)</cfg:CertificateThumbprint>
</cfg:Listener>
"@
    [void]$session.Create("$configBase/Listener?Address=*+Transport=HTTPS", $listenerXml, 0)
} else {
    $thumbprint = $listeners[0].DocumentElement.SelectSingleNode("*[local-name()='CertificateThumbprint']").InnerText
    $cert = Get-Item -LiteralPath "Cert:\LocalMachine\My\$thumbprint"
    Write-Warning 'Reusing existing HTTPS certificate. Verify that its SAN includes the seat IP or use its DNS name.'
}
$ruleName = 'ExperimentPlatform-Ansible-HTTPS'
if (Get-NetFirewallRule -Name $ruleName -ErrorAction SilentlyContinue) {
    Set-NetFirewallRule -Name $ruleName -Enabled True -RemoteAddress $ControllerAddress.IPAddressToString | Out-Null
} else {
    New-NetFirewallRule -Name $ruleName -DisplayName 'Experiment management WinRM HTTPS' `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort 5986 `
        -RemoteAddress $ControllerAddress.IPAddressToString -Profile Any | Out-Null
}
if ($AllowLocalAdminRemoteToken) {
    Write-Warning 'Enabling full remote tokens for local administrators affects the whole machine.'
    New-ItemProperty -LiteralPath $policyPath -Name LocalAccountTokenFilterPolicy `
        -PropertyType DWord -Value 1 -Force | Out-Null
} elseif ($null -ne $oldPolicy.LocalAccountTokenFilterPolicy) {
    New-ItemProperty -LiteralPath $policyPath -Name LocalAccountTokenFilterPolicy `
        -PropertyType DWord -Value $oldPolicy.LocalAccountTokenFilterPolicy -Force | Out-Null
} else {
    Remove-ItemProperty -LiteralPath $policyPath -Name LocalAccountTokenFilterPolicy `
        -ErrorAction SilentlyContinue
}
# Close remote HTTP ingress after local native configuration is complete.
Get-NetFirewallRule -Name 'WINRM-HTTP-In-TCP*' -ErrorAction SilentlyContinue |
    Disable-NetFirewallRule | Out-Null
$certFile = Join-Path $root 'winrm-server.cer'
Export-Certificate -Cert $cert -FilePath $certFile -Force | Out-Null
[pscustomobject]@{
    ComputerName = $env:COMPUTERNAME
    Login = "$env:COMPUTERNAME\$LoginUser"
    ControllerAllowed = $ControllerAddress.IPAddressToString
    CertificateFile = $certFile
    CertificateSHA256 = (Get-FileHash -LiteralPath $certFile -Algorithm SHA256).Hash
    CertificateThumbprint = $cert.Thumbprint
    LocalAccountTokenFilterPolicy = (Get-ItemProperty -LiteralPath $policyPath).LocalAccountTokenFilterPolicy
}
Write-Warning 'Check for other custom firewall rules allowing 5985/5986. The script does not modify unrelated custom rules.'
