# Windows WinRM HTTPS bootstrap

Run this bootstrap in **administrator Windows PowerShell 5.1** on the computer being managed. It enables PowerShell remoting, configures a WinRM HTTPS listener, limits the built-in WinRM firewall rules to the controller address, disables the built-in HTTP inbound rules, and exports the public server certificate for controller verification.

The script does not set account passwords, add administrators, or change network profiles. Set a non-empty Windows account password separately. All connected network profiles must be trusted and configured as Private or DomainAuthenticated; public-profile compatibility has not been established. Initialization stops before configuration changes if a public profile is detected.

## Download and run

Replace the example controller and seat addresses and account name with your deployment values. The seat address must belong to the computer executing the script. Run these commands locally on the seat, not on the controller.

```powershell
$ErrorActionPreference = 'Stop'
$url = 'https://raw.githubusercontent.com/Omni-Intel/scripts/main/utils/windows/bootstrap-winrm.ps1'
$expectedHash = '3EEB4CFA8E538EBD7E35641B9A19CB9B139038F5828EE3BA61E340CEC2BC1718'
$scriptFile = Join-Path $env:TEMP 'bootstrap-winrm.ps1'
Invoke-WebRequest -UseBasicParsing -Uri $url -OutFile $scriptFile
if ((Get-FileHash -LiteralPath $scriptFile -Algorithm SHA256).Hash -ne $expectedHash) {
    throw 'Bootstrap checksum mismatch. Obtain the hash for the intended release before running it.'
}
powershell.exe -NoProfile -ExecutionPolicy Bypass -File $scriptFile `
    -ControllerAddress 192.0.2.1 -SeatAddress 192.0.2.10 -LoginUser labuser
```

The hash pins the reviewed script bytes. When the script changes, obtain the intended version's hash through a trusted channel. For reproducible deployment, replace `main` in the URL with the desired commit SHA. Process execution policy does not override organization-enforced Group Policy.

## Parameters and privileges

| Parameter | Purpose |
| --- | --- |
| `ControllerAddress` | Required controller IP permitted by the configured firewall rules |
| `SeatAddress` | Required local seat IP included in the certificate SAN |
| `LoginUser` | Existing local administrator account; defaults to the current username |
| `AllowLocalAdminRemoteToken` | Explicitly set `LocalAccountTokenFilterPolicy=1` for local administrator remote access |

If local administrator authentication is blocked by UAC remote token filtering, append `-AllowLocalAdminRemoteToken` after evaluating the machine-wide policy change. `Enable-PSRemoting` itself may alter this policy. Without the switch, the script restores the value captured at startup on successful completion; errors may leave partial changes.

Initialization saves a configuration snapshot under `C:\ProgramData\ExperimentPlatform\bootstrap`. This snapshot is for inspection, not automatic rollback. Existing HTTPS listeners are reused; verify their port, enabled state and certificate name. New certificates are self-signed, valid for three months, and contain the local computer name and supplied seat IP. Certificate renewal is not automated.

The result includes `CertificateFile`, `CertificateSHA256`, `CertificateThumbprint` and the login identity. The exported file is `C:\ProgramData\ExperimentPlatform\bootstrap\winrm-server.cer` and contains no private key. Compare its SHA256 independently before trusting it on the controller.

Other custom firewall rules may still allow port 5985 or 5986. The script does not modify unrelated custom rules. Review effective rules after initialization. A successful bootstrap verifies configuration, not a complete authenticated Ansible connection; perform that check from the controller separately.
