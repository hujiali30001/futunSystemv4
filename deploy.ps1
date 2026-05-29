param(
    [string]$HostName = "43.165.166.57",
    [string]$User = "ubuntu",
    [string]$SshKey = ".tmp-ssh/futunsystemv3_deploy_ed25519",
    [string]$ServerPath = "/home/ubuntu/furunsystemv4/current",
    [string]$ServiceName = "furun-api",
    [switch]$SkipFrontend,
    [switch]$SkipBackend
)

$ErrorActionPreference = "Continue"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$sshTarget = "${User}@${HostName}"
$sshKeyPath = (Resolve-Path $SshKey).Path

$commonArgs = "-i `"$sshKeyPath`" -o StrictHostKeyChecking=no -o PasswordAuthentication=no -o BatchMode=yes"

function do_ssh([string]$cmd) {
    $full = "ssh -n -T $commonArgs $sshTarget `"$cmd`""
    cmd /c "$full"
    if ($LASTEXITCODE -ne 0) { Write-Host "[WARN] ssh exit=$LASTEXITCODE" -ForegroundColor Yellow }
}

function do_scp([string]$src, [string]$dst, [switch]$Recurse) {
    $r = if ($Recurse) { "-r" } else { "" }
    $full = "scp $r $commonArgs `"$src`" `"$dst`""
    cmd /c "$full"
    if ($LASTEXITCODE -ne 0) { Write-Host "[WARN] scp exit=$LASTEXITCODE" -ForegroundColor Yellow }
}

if (-not $SkipFrontend) {
    Write-Host "[build] Building frontend..." -ForegroundColor Cyan
    Push-Location web
    try { npm run build; if ($LASTEXITCODE -ne 0) { throw "Build failed" } } finally { Pop-Location }

    $jsFile  = Get-ChildItem "web/dist/assets/index-*.js"  | Select-Object -First 1
    $cssFile = Get-ChildItem "web/dist/assets/index-*.css" | Select-Object -First 1

    Write-Host "[deploy] Uploading frontend..." -ForegroundColor Cyan
    do_scp "web/dist/index.html" "${sshTarget}:${ServerPath}/web/dist/"
    do_ssh "rm -f ${ServerPath}/web/dist/assets/*"
    do_scp $jsFile.FullName  "${sshTarget}:${ServerPath}/web/dist/assets/"
    do_scp $cssFile.FullName "${sshTarget}:${ServerPath}/web/dist/assets/"
}

if (-not $SkipBackend) {
    Write-Host "[deploy] Uploading backend..." -ForegroundColor Cyan
    do_scp -Recurse "app" "${sshTarget}:${ServerPath}/"
}

Write-Host "[restart] Restarting $ServiceName..." -ForegroundColor Cyan
do_ssh "find ${ServerPath} -name '*.pyc' -delete && sudo systemctl restart ${ServiceName}"
Write-Host "  Waiting 6s..." -ForegroundColor Gray
Start-Sleep -Seconds 6

Write-Host "[check] Checking health..." -ForegroundColor Cyan
$result = cmd /c "ssh -n -T $commonArgs $sshTarget `"curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/`""
$result = $result -replace '\s',''

if ($result -eq "200") {
    Write-Host "[OK] Deploy successful" -ForegroundColor Green
} else {
    Write-Host "[ERR] Server returned '$result'" -ForegroundColor Red
    exit 1
}
