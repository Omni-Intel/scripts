# Windows WinRM HTTPS bootstrap

Run this bootstrap in **administrator Windows PowerShell 5.1** on the computer being managed. It enables PowerShell remoting, configures a WinRM HTTPS listener, limits the built-in WinRM firewall rules to the controller address, disables the built-in HTTP inbound rules, and exports the public server certificate for controller verification.

The script does not set account passwords, add administrators, or change network profiles. Set a non-empty Windows account password separately. All connected network profiles must be trusted and configured as Private or DomainAuthenticated; public-profile compatibility has not been established. Initialization stops before configuration changes if a public profile is detected.

## Download and run

Run locally on the seat in **administrator Windows PowerShell**, using the existing local administrator account that should receive remote access. Only the controller address needs to be supplied. The script automatically selects the local IPv4 source address Windows would use to reach that controller, and resolves the current process account by its local user SID. It prints the selected address and account before configuration changes.

### One line

Replace the example controller IP `192.0.2.1`. This example explicitly allows full remote tokens for local administrators (`-LocalAdmin`), a machine-wide UAC remote-access setting.

```powershell
$f="$env:TEMP\bootstrap-winrm.ps1"; iwr -UseBasicParsing 'https://raw.githubusercontent.com/Omni-Intel/scripts/main/utils/windows/bootstrap-winrm.ps1' -OutFile $f -ErrorAction Stop; if((Get-FileHash $f).Hash -ne 'BC8CA23F472756F24C3D595AE0CFDCFFED754402F2BE06207AF1F44A9409328D'){throw 'Bootstrap checksum mismatch'}; powershell -NoProfile -ExecutionPolicy Bypass -File $f -Controller 192.0.2.1 -LocalAdmin
```

Omit `-LocalAdmin` if the existing remote-access policy must be preserved. No password is supplied to the script; passwords are configured separately and entered on the Ansible controller.

If address selection fails or the desired account differs from the elevated process account, supply `-SeatAddress` and `-LoginUser` explicitly. Elevating with a different account selects that account, not necessarily the desktop's signed-in user. Domain or Microsoft identities that cannot be resolved to an existing local administrator require an explicit local `-LoginUser`.

The hash pins the reviewed script bytes. When the script changes, obtain the intended version's hash through a trusted channel. For reproducible deployment, replace `main` in the URL with the desired commit SHA. Process execution policy does not override organization-enforced Group Policy. GitHub Raw must be reachable from the seat.

## Parameters and privileges

| Parameter | Purpose |
| --- | --- |
| `ControllerAddress` / `Controller` | Required controller IP permitted by the configured firewall rules |
| `SeatAddress` / `Seat` | Optional local IP override; otherwise selected from the route to the controller |
| `LoginUser` / `User` | Optional existing local administrator override; otherwise resolved from the elevated process SID |
| `AllowLocalAdminRemoteToken` / `LocalAdmin` | Explicitly set `LocalAccountTokenFilterPolicy=1` for local administrator remote access |

If local administrator authentication is blocked by UAC remote token filtering, append `-AllowLocalAdminRemoteToken` after evaluating the machine-wide policy change. `Enable-PSRemoting` itself may alter this policy. Without the switch, the script restores the value captured at startup on successful completion; errors may leave partial changes.

Initialization saves a configuration snapshot under `C:\ProgramData\ExperimentPlatform\bootstrap`. This snapshot is for inspection, not automatic rollback. Existing HTTPS listeners are reused; verify their port, enabled state and certificate name. New certificates are self-signed, valid for three months, and contain the local computer name and supplied seat IP. Certificate renewal is not automated.

The result includes `CertificateFile`, `CertificateSHA256`, `CertificateThumbprint` and the login identity. The exported file is `C:\ProgramData\ExperimentPlatform\bootstrap\winrm-server.cer` and contains no private key. Compare its SHA256 independently before trusting it on the controller.

Other custom firewall rules may still allow port 5985 or 5986. The script does not modify unrelated custom rules. Review effective rules after initialization. A successful bootstrap verifies configuration, not a complete authenticated Ansible connection; perform that check from the controller separately.
