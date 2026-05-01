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

function Test-DockerPortConflict {
    param(
        [string]$Output,
        [int]$Port
    )

    return (
        $Output -match "port is already allocated" -or
        $Output -match "Bind for 0\.0\.0\.0:$Port failed" -or
        $Output -match "Ports are not available"
    )
}

function Invoke-DockerRunWithAvailablePort {
    for ($port = $StartPort; $port -le $MaxPort; $port++) {
        if (-not (Test-PortAvailable -Port $port)) {
            Write-Host "Port $port appears to be in use. Trying next port..."
            continue
        }

        $logFile = [System.IO.Path]::GetTempFileName()
        try {
            Write-Host "Trying Docker port mapping at http://localhost:$port"

            $dockerCommand = "docker run --rm -p ${port}:8000 -e HOST_PORT=$port $ImageName 2>&1"
            & cmd.exe /d /c $dockerCommand | Tee-Object -FilePath $logFile
            $exitCode = $LASTEXITCODE

            if ($exitCode -eq 0) {
                return
            }

            $output = Get-Content -Raw -Path $logFile
            if (Test-DockerPortConflict -Output $output -Port $port) {
                Write-Host "Port $port is already allocated by Docker. Trying next port..."
                continue
            }

            throw "Docker run failed with exit code $exitCode."
        }
        finally {
            Remove-Item $logFile -ErrorAction SilentlyContinue
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

if (-not $NoBuild) {
    docker build -t $ImageName .
    if ($LASTEXITCODE -ne 0) {
        throw "Docker image build failed."
    }
}

Invoke-DockerRunWithAvailablePort
