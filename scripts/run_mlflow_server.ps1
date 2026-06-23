param(
    [string]$HostName = $(if ($env:MLFLOW_HOST) { $env:MLFLOW_HOST } else { "127.0.0.1" }),
    [int]$Port = $(if ($env:MLFLOW_PORT) { [int]$env:MLFLOW_PORT } else { 5000 }),
    [string]$BackendStoreUri = $(if ($env:MLFLOW_BACKEND_STORE_URI) { $env:MLFLOW_BACKEND_STORE_URI } else { "sqlite:///mlruns/mlflow.db" }),
    [string]$ArtifactRoot = $(if ($env:MLFLOW_ARTIFACT_ROOT) { $env:MLFLOW_ARTIFACT_ROOT } else { "./mlartifacts" }),
    [string]$PythonBin = $(if ($env:PYTHON_BIN) { $env:PYTHON_BIN } elseif (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" })
)

New-Item -ItemType Directory -Force -Path "mlruns" | Out-Null
New-Item -ItemType Directory -Force -Path "mlartifacts" | Out-Null

Write-Host "MLflow Tracking Server URL: http://$HostName`:$Port"
Write-Host "Backend store URI: $BackendStoreUri"
Write-Host "Artifact root: $ArtifactRoot"
Write-Host "Python: $PythonBin"

& $PythonBin -m mlflow server `
    --host $HostName `
    --port $Port `
    --backend-store-uri $BackendStoreUri `
    --default-artifact-root $ArtifactRoot
