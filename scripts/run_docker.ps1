param(
    [int]$StartPort = 8000,
    [int]$MaxPort = 8099,
    [string]$ImageName = "autodriving-sensor-qa",
    [switch]$NoBuild
)

$ErrorActionPreference = "Stop"

function Test-PortAvailable {
    param([int]$Port)

    $listener = $null
    try {
        $listener = [System.Net.Sockets.TcpListener]::new([System.Net.IPAddress]::Any, $Port)
        $listener.Start()
        return $true
    }
    catch {
        return $false
    }
    finally {
        if ($null -ne $listener) {
            $listener.Stop()
        }
    }
}

function Find-AvailablePort {
    for ($port = $StartPort; $port -le $MaxPort; $port++) {
        if (Test-PortAvailable -Port $port) {
            return $port
        }
    }

    throw "$StartPort-$MaxPort 범위에서 사용 가능한 포트를 찾지 못했습니다."
}

if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
    throw "Docker CLI를 찾을 수 없습니다. Docker Desktop을 먼저 설치하거나 실행해 주세요."
}

docker info *> $null
if ($LASTEXITCODE -ne 0) {
    throw "Docker daemon에 연결할 수 없습니다. Docker Desktop이 실행 중인지 확인해 주세요."
}

$hostPort = Find-AvailablePort

if (-not $NoBuild) {
    docker build -t $ImageName .
    if ($LASTEXITCODE -ne 0) {
        throw "Docker image build failed."
    }
}

Write-Host "Docker container will be available at http://localhost:$hostPort"
docker run --rm `
    -p "${hostPort}:8000" `
    -e "HOST_PORT=$hostPort" `
    $ImageName
