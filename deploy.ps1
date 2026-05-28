param(
    [string]$HostName = "43.165.166.57",
    [string]$User = "ubuntu",
    [string]$SshKey = ".tmp-ssh/futunsystemv3_deploy_ed25519",
    [string]$ServerPath = "/home/ubuntu/furunsystemv4/current",
    [string]$ServiceName = "furun-api",
    [switch]$SkipFrontend,
    [switch]$SkipBackend
)

$ErrorActionPreference = "Stop"
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$sshTarget = "${User}@${HostName}"

if (-not $SkipFrontend) {
    Write-Host "[build] Building frontend..." -ForegroundColor Cyan
    Push-Location web
    try { npm run build; if ($LASTEXITCODE -ne 0) { throw "Build failed" } } finally { Pop-Location }

    $jsFile  = Get-ChildItem "web/dist/assets/index-*.js"  | Select-Object -First 1
    $cssFile = Get-ChildItem "web/dist/assets/index-*.css" | Select-Object -First 1

    Write-Host "[deploy] Uploading frontend..." -ForegroundColor Cyan
    & scp -i $SshKey -o StrictHostKeyChecking=no "web/dist/index.html" "${sshTarget}:${ServerPath}/web/dist/"
    & ssh -i $SshKey -o StrictHostKeyChecking=no $sshTarget "rm -f ${ServerPath}/web/dist/assets/index-*"
    & scp -i $SshKey -o StrictHostKeyChecking=no $jsFile.FullName  "${sshTarget}:${ServerPath}/web/dist/assets/"
    & scp -i $SshKey -o StrictHostKeyChecking=no $cssFile.FullName "${sshTarget}:${ServerPath}/web/dist/assets/"
}

if (-not $SkipBackend) {
    Write-Host "[deploy] Uploading backend..." -ForegroundColor Cyan
    & scp -i $SshKey -o StrictHostKeyChecking=no -r "app" "${sshTarget}:${ServerPath}/"
}

Write-Host "[restart] Restarting $ServiceName..." -ForegroundColor Cyan
$remoteCmd = @"
find ${ServerPath} -name '*.pyc' -delete
sudo systemctl restart ${ServiceName}
sleep 5
curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/
"@

$result = & ssh -i $SshKey -o StrictHostKeyChecking=no $sshTarget $remoteCmd

if ($result -eq "200") {
    Write-Host "[OK] Deploy successful" -ForegroundColor Green
} else {
    Write-Host "[ERR] Server returned $result" -ForegroundColor Red
    exit 1
}
