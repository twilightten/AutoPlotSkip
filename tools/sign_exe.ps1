# 给 dist/AutoPlotSkip/AutoPlotSkip.exe 做一次自签名，让 Windows 认得出「发行者」，
# 双击时不再被 SmartScreen 拦成「无法识别的应用 / 发布者未知」。
#
# 做法：本机生成一张自签名代码签名证书 -> 用它签 exe -> 把证书装进当前用户的
#       「受信任的根证书颁发机构」和「受信任的发布者」。这样只对**这台机器**生效，
#       不花钱、不需要管理员（CurrentUser 证书库普通权限就能写），也不会影响别人。
#
# 用法（普通 PowerShell 窗口即可，不需要管理员）：
#   powershell -ExecutionPolicy Bypass -File tools\sign_exe.ps1
# 想换掉旧证书重来：
#   powershell -ExecutionPolicy Bypass -File tools\sign_exe.ps1 -Reset

param(
    [switch]$Reset,
    [string]$Exe = (Join-Path $PSScriptRoot '..\dist\AutoPlotSkip\AutoPlotSkip.exe')
)

$ErrorActionPreference = 'Stop'
$Subject = 'CN=AutoPlotSkip, O=AutoPlotSkip, C=CN'

$Exe = [System.IO.Path]::GetFullPath($Exe)
if (-not (Test-Path $Exe)) {
    Write-Host "[X] 找不到 $Exe —— 先跑 python tools\build_exe.py 打包" -ForegroundColor Red
    exit 1
}

# ---- 1. 找或建证书 ----------------------------------------------------------
$cert = Get-ChildItem Cert:\CurrentUser\My |
    Where-Object { $_.Subject -eq $Subject -and $_.HasPrivateKey } |
    Sort-Object NotAfter -Descending | Select-Object -First 1

if ($Reset -and $cert) {
    Write-Host "[i] -Reset：先删除旧的自签名证书"
    Get-ChildItem Cert:\CurrentUser\Root, Cert:\CurrentUser\TrustedPublisher |
        Where-Object { $_.Subject -eq $Subject } | Remove-Item -Force -ErrorAction SilentlyContinue
    Get-ChildItem Cert:\CurrentUser\My |
        Where-Object { $_.Subject -eq $Subject } | Remove-Item -Force -ErrorAction SilentlyContinue
    $cert = $null
}

if (-not $cert) {
    Write-Host "[1/3] 生成自签名代码签名证书 …"
    $cert = New-SelfSignedCertificate `
        -Type CodeSigningCert `
        -Subject $Subject `
        -KeyUsage DigitalSignature `
        -FriendlyName 'AutoPlotSkip Self-Signed Code Signing' `
        -CertStoreLocation 'Cert:\CurrentUser\My' `
        -NotAfter (Get-Date).AddYears(10) `
        -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.3', '2.5.29.19={text}')
} else {
    Write-Host "[1/3] 复用已有证书（到期 $(('{0:yyyy-MM-dd}' -f $cert.NotAfter))）"
}

# ---- 2. 证书装进当前用户的信任库 -------------------------------------------
Write-Host "[2/3] 把证书装入「受信任的根证书颁发机构」+「受信任的发布者」…"
foreach ($store in @('Root', 'TrustedPublisher')) {
    $path = "Cert:\CurrentUser\$store"
    $already = Get-ChildItem $path | Where-Object { $_.Thumbprint -eq $cert.Thumbprint }
    if ($already) {
        Write-Host "      $store 里已有该证书，跳过"
        continue
    }
    try {
        $tmp = Join-Path $env:TEMP "aps_sign_$store.cer"
        Export-Certificate -Cert $cert -FilePath $tmp -Type CERT | Out-Null
        Import-Certificate -FilePath $tmp -CertStoreLocation $path | Out-Null
        Remove-Item $tmp -Force -ErrorAction SilentlyContinue
        Write-Host "      已装入 $store"
    } catch {
        Write-Host "      [!] 装入 $store 失败：$($_.Exception.Message)" -ForegroundColor Yellow
        Write-Host "          若之后仍被拦，用管理员身份重跑本脚本即可。"
    }
}

# ---- 3. 签名 ---------------------------------------------------------------
Write-Host "[3/3] 给 exe 签名 …"
$sig = Set-AuthenticodeSignature -FilePath $Exe -Certificate $cert `
    -TimestampServer 'http://timestamp.digicert.com' -HashAlgorithm SHA256 `
    -ErrorAction SilentlyContinue
if (-not $sig -or $sig.Status -eq 'UnknownError') {
    # 时间戳服务器连不上时退回不带时间戳（本机自用足够）
    $sig = Set-AuthenticodeSignature -FilePath $Exe -Certificate $cert `
        -HashAlgorithm SHA256
}

Write-Host ""
Write-Host "签名状态：$($sig.Status)" -ForegroundColor $(if ("$($sig.Status)" -eq 'Valid') { 'Green' } else { 'Yellow' })
if ($sig.StatusMessage) { Write-Host "说明：$($sig.StatusMessage)" }
Write-Host "证书指纹：$($cert.Thumbprint)"
Write-Host ""
if ("$($sig.Status)" -eq 'Valid') {
    Write-Host "[OK] 已签名。现在双击 $Exe 应该不再跳 SmartScreen。" -ForegroundColor Green
    Write-Host "     如果还跳，关掉再开一次（资源管理器会缓存判定），或注销重登。"
} else {
    Write-Host "[!] 没签成 Valid，仍可直接运行；在 SmartScreen 里点「更多信息 → 仍要运行」一次即可。" -ForegroundColor Yellow
}
