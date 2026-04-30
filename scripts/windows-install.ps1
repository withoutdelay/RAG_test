param(
  [ValidateSet("install", "restart", "stop", "status", "logs")]
  [string]$Action = "install",

  [switch]$SkipPrerequisites,
  [switch]$NoBrowser,
  [switch]$NoElevate,

  [string]$ProjectName = "rag_test_customer",
  [int]$BackendPort = 8000,
  [int]$FrontendPort = 3000
)

$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path (Join-Path $PSScriptRoot "..")
$EnvFile = Join-Path $RootDir ".env"
$EnvExampleFile = Join-Path $RootDir ".env.example"
$ComposeFile = Join-Path $RootDir "docker-compose.prod.yml"
$InstallAssetsDir = Join-Path $RootDir "install-assets"
$script:RebootMayBeRequired = $false

function Write-Step {
  param([string]$Message)
  Write-Host "[install] $Message" -ForegroundColor Cyan
}

function Write-Warn {
  param([string]$Message)
  Write-Host "[install] WARNING: $Message" -ForegroundColor Yellow
}

function Write-Fail {
  param([string]$Message)
  Write-Host "[install] ERROR: $Message" -ForegroundColor Red
}

function Test-Administrator {
  $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
  $principal = New-Object Security.Principal.WindowsPrincipal($identity)
  return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

function Test-CommandExists {
  param([string]$Name)
  return [bool](Get-Command $Name -ErrorAction SilentlyContinue)
}

function Refresh-Path {
  $machinePath = [Environment]::GetEnvironmentVariable("Path", "Machine")
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  $env:Path = "$machinePath;$userPath"
}

function Request-ElevationIfNeeded {
  if ($NoElevate -or $SkipPrerequisites -or (Test-Administrator)) {
    return
  }

  $needsInstall = -not (Test-CommandExists "docker") -or -not (Test-CommandExists "git")
  if (-not $needsInstall) {
    return
  }

  Write-Step "Administrator permission is required to install missing prerequisites."
  $args = @(
    "-NoProfile",
    "-ExecutionPolicy", "Bypass",
    "-File", "`"$PSCommandPath`"",
    "-Action", $Action,
    "-ProjectName", "`"$ProjectName`"",
    "-BackendPort", $BackendPort,
    "-FrontendPort", $FrontendPort
  )
  if ($NoBrowser) {
    $args += "-NoBrowser"
  }

  Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList ($args -join " ")
  exit 0
}

function Enable-WslFeatures {
  if ($SkipPrerequisites) {
    return
  }

  if (-not (Test-Administrator)) {
    Write-Warn "Skipping WSL feature enablement because this PowerShell session is not elevated."
    return
  }

  Write-Step "Ensuring Windows WSL2 features are enabled"
  $features = @(
    "Microsoft-Windows-Subsystem-Linux",
    "VirtualMachinePlatform"
  )

  foreach ($feature in $features) {
    & dism.exe /online /enable-feature /featurename:$feature /all /norestart | Out-Host
    if ($LASTEXITCODE -eq 3010) {
      $script:RebootMayBeRequired = $true
    } elseif ($LASTEXITCODE -ne 0) {
      throw "Failed to enable Windows feature $feature (exit code $LASTEXITCODE)"
    }
  }

  if (Test-CommandExists "wsl") {
    & wsl.exe --set-default-version 2 *> $null
  }
}

function Install-WingetPackage {
  param(
    [string]$PackageId,
    [string]$CommandName,
    [string]$Label
  )

  if (Test-CommandExists $CommandName) {
    Write-Step "$Label is already available"
    return
  }

  if ($SkipPrerequisites) {
    throw "$Label is missing. Re-run without -SkipPrerequisites or install it manually."
  }

  if (-not (Test-CommandExists "winget")) {
    throw "winget is not available. Install Microsoft App Installer first, then rerun this script."
  }

  Write-Step "Installing $Label with winget"
  & winget install --id $PackageId -e --silent --accept-source-agreements --accept-package-agreements | Out-Host
  if ($LASTEXITCODE -eq 3010) {
    $script:RebootMayBeRequired = $true
    Write-Warn "$Label installer requested a Windows reboot."
  } elseif ($LASTEXITCODE -ne 0) {
    throw "Failed to install $Label with winget (exit code $LASTEXITCODE)"
  }

  Refresh-Path
  if (-not (Test-CommandExists $CommandName)) {
    Write-Warn "$Label was installed, but the command is not visible in this shell yet. A reboot or new terminal may be required."
  }
}

function Assert-HostCapacity {
  Write-Step "Checking host capacity"

  try {
    $computer = Get-CimInstance Win32_ComputerSystem
    $memoryGb = [math]::Round($computer.TotalPhysicalMemory / 1GB, 1)
    if ($memoryGb -lt 16) {
      Write-Warn "Detected $memoryGb GB RAM. 16 GB or more is recommended for document parsing and local embeddings."
    }
  } catch {
    Write-Warn "Unable to check RAM: $($_.Exception.Message)"
  }

  try {
    $driveName = ([System.IO.Path]::GetPathRoot($RootDir.Path)).TrimEnd("\").TrimEnd(":")
    $drive = Get-PSDrive -Name $driveName
    $freeGb = [math]::Round($drive.Free / 1GB, 1)
    if ($freeGb -lt 30) {
      Write-Warn "Detected $freeGb GB free disk on drive $driveName. 30 GB or more is recommended for Docker images and parsed assets."
    }
  } catch {
    Write-Warn "Unable to check free disk space: $($_.Exception.Message)"
  }

  try {
    $processor = Get-CimInstance Win32_Processor | Select-Object -First 1
    if ($processor.PSObject.Properties.Name -contains "VirtualizationFirmwareEnabled") {
      if (-not $processor.VirtualizationFirmwareEnabled) {
        Write-Warn "CPU virtualization appears disabled. Docker Desktop may not start until virtualization is enabled in BIOS/UEFI."
      }
    }
  } catch {
    Write-Warn "Unable to check CPU virtualization: $($_.Exception.Message)"
  }
}

function Ensure-Prerequisites {
  Assert-HostCapacity
  Enable-WslFeatures
  Install-WingetPackage -PackageId "Git.Git" -CommandName "git" -Label "Git"
  Install-WingetPackage -PackageId "Docker.DockerDesktop" -CommandName "docker" -Label "Docker Desktop"
}

function Start-DockerDesktop {
  $candidates = @()
  if ($env:ProgramFiles) {
    $candidates += (Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe")
  }
  if ($env:LOCALAPPDATA) {
    $candidates += (Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe")
  }

  foreach ($dockerDesktop in $candidates) {
    if (Test-Path $dockerDesktop) {
      Write-Step "Starting Docker Desktop"
      Start-Process -FilePath $dockerDesktop | Out-Null
      return
    }
  }

  Write-Warn "Docker Desktop executable was not found in the common install locations."
}

function Start-DockerService {
  $service = Get-Service -Name "com.docker.service" -ErrorAction SilentlyContinue
  if (-not $service) {
    return
  }

  if ($service.Status -ne "Running") {
    try {
      Write-Step "Starting Docker Desktop service"
      Start-Service -Name "com.docker.service" -ErrorAction Stop
    } catch {
      Write-Warn "Unable to start com.docker.service automatically: $($_.Exception.Message)"
    }
  }
}

function Invoke-DockerInfoProbe {
  $stdout = Join-Path $env:TEMP "rag-test-docker-info.out"
  $stderr = Join-Path $env:TEMP "rag-test-docker-info.err"

  try {
    Remove-Item $stdout, $stderr -Force -ErrorAction SilentlyContinue
    $process = Start-Process `
      -FilePath "docker" `
      -ArgumentList @("info") `
      -NoNewWindow `
      -Wait `
      -PassThru `
      -RedirectStandardOutput $stdout `
      -RedirectStandardError $stderr

    $output = ""
    if (Test-Path $stdout) {
      $output += (Get-Content $stdout -Raw -ErrorAction SilentlyContinue)
    }
    if (Test-Path $stderr) {
      $output += (Get-Content $stderr -Raw -ErrorAction SilentlyContinue)
    }

    return @{
      Ready = ($process.ExitCode -eq 0)
      Output = $output.Trim()
    }
  } catch {
    return @{
      Ready = $false
      Output = $_.Exception.Message
    }
  } finally {
    Remove-Item $stdout, $stderr -Force -ErrorAction SilentlyContinue
  }
}

function Wait-DockerReady {
  Refresh-Path
  if (-not (Test-CommandExists "docker")) {
    if ($script:RebootMayBeRequired) {
      throw "Docker CLI is not available yet. Reboot Windows and rerun install-windows.cmd."
    }
    throw "Docker CLI is not available. Install Docker Desktop and rerun this script."
  }

  Start-DockerService
  Start-DockerDesktop
  Write-Step "Waiting for Docker engine"

  for ($i = 1; $i -le 120; $i++) {
    $probe = Invoke-DockerInfoProbe
    if ($probe.Ready) {
      Write-Step "Docker engine is ready"
      return
    }

    if (($i % 12) -eq 0) {
      Write-Step "Docker engine is still starting. Make sure Docker Desktop is open and has finished its first-run setup."
      Start-DockerService
      Start-DockerDesktop
    }

    Start-Sleep -Seconds 5
  }

  $lastProbe = Invoke-DockerInfoProbe
  $detail = $lastProbe.Output
  if ($detail -match "dockerDesktopLinuxEngine|pipe|named pipe") {
    throw "Docker Desktop is installed, but the Linux engine is not ready. Open Docker Desktop manually, finish first-run setup, make sure it is using Linux containers, then rerun install-windows.cmd. Last docker error: $detail"
  }

  if ($script:RebootMayBeRequired) {
    throw "Docker did not become ready. Windows features were changed, so reboot Windows and rerun install-windows.cmd."
  }
  throw "Docker did not become ready. Check Docker Desktop, WSL2, virtualization, company security policy, and Docker Desktop first-run prompts. Last docker error: $detail"
}

function Read-DotEnv {
  $values = @{}
  if (-not (Test-Path $EnvFile)) {
    return $values
  }

  foreach ($line in Get-Content $EnvFile) {
    if ($line -match "^\s*#" -or $line -match "^\s*$") {
      continue
    }
    if ($line -match "^\s*([^=\s]+)\s*=\s*(.*)\s*$") {
      $key = $Matches[1]
      $value = $Matches[2].Trim()
      if (($value.StartsWith('"') -and $value.EndsWith('"')) -or ($value.StartsWith("'") -and $value.EndsWith("'"))) {
        $value = $value.Substring(1, $value.Length - 2)
      }
      $values[$key] = $value
    }
  }

  return $values
}

function Set-DotEnvValue {
  param(
    [string]$Key,
    [string]$Value
  )

  $lines = @()
  if (Test-Path $EnvFile) {
    $lines = @(Get-Content $EnvFile)
  }

  $found = $false
  $next = foreach ($line in $lines) {
    if ($line -match "^\s*$([regex]::Escape($Key))\s*=") {
      $found = $true
      "$Key=$Value"
    } else {
      $line
    }
  }
  if (-not $found) {
    $next += "$Key=$Value"
  }

  $utf8NoBom = New-Object System.Text.UTF8Encoding($false)
  [System.IO.File]::WriteAllLines($EnvFile, $next, $utf8NoBom)
}

function Ensure-EnvFile {
  if (-not (Test-Path $ComposeFile)) {
    throw "Missing docker-compose.prod.yml at $ComposeFile"
  }

  if (Test-Path $EnvFile) {
    Write-Step "Using existing .env file"
    return
  }

  if (-not (Test-Path $EnvExampleFile)) {
    throw "Missing .env.example at $EnvExampleFile"
  }

  Write-Step "Creating .env from .env.example"
  Copy-Item $EnvExampleFile $EnvFile
  Set-DotEnvValue -Key "APP_ENV" -Value "production"
  Set-DotEnvValue -Key "COMPOSE_PROJECT_NAME" -Value $ProjectName
  Set-DotEnvValue -Key "AUTH_ENABLED" -Value "true"
  Set-DotEnvValue -Key "AUTH_USERNAME" -Value "admin"
  Set-DotEnvValue -Key "AUTH_SESSION_COOKIE_NAME" -Value "presale_session"
  Set-DotEnvValue -Key "AUTH_COOKIE_SECURE" -Value "false"
  Set-DotEnvValue -Key "AUTH_COOKIE_SAMESITE" -Value "lax"
  Set-DotEnvValue -Key "NEXT_PUBLIC_AUTH_ENABLED" -Value "true"
  Set-DotEnvValue -Key "NEXT_PUBLIC_AUTH_COOKIE_NAME" -Value "presale_session"
  Set-DotEnvValue -Key "BACKEND_EXTRAS" -Value "parsing"
  Set-DotEnvValue -Key "QDRANT_COLLECTION" -Value "presale_knowledge_qwen3_vl_embedding"
  Set-DotEnvValue -Key "BACKEND_PORT" -Value ([string]$BackendPort)
  Set-DotEnvValue -Key "FRONTEND_PORT" -Value ([string]$FrontendPort)
  Set-DotEnvValue -Key "NEXT_PUBLIC_API_BASE_URL" -Value "http://localhost:$BackendPort/api/v1"
  Set-DotEnvValue -Key "CORS_ALLOW_ORIGINS" -Value "http://localhost:$FrontendPort,http://127.0.0.1:$FrontendPort"
  Set-DotEnvValue -Key "EMBEDDING_BACKEND" -Value "dashscope-multimodal"
  Set-DotEnvValue -Key "EMBEDDING_BASE_URL" -Value "https://dashscope.aliyuncs.com/compatible-mode/v1"
  Set-DotEnvValue -Key "EMBEDDING_ENDPOINT_PATH" -Value "/services/embeddings/multimodal-embedding/multimodal-embedding"
  Set-DotEnvValue -Key "EMBEDDING_MODEL" -Value "qwen3-vl-embedding"
  Set-DotEnvValue -Key "EMBEDDING_DIMENSION" -Value "1024"
  Set-DotEnvValue -Key "EMBEDDING_BATCH_SIZE" -Value "16"
  Set-DotEnvValue -Key "EMBEDDING_LOCAL_FILES_ONLY" -Value "false"
  Set-DotEnvValue -Key "FORMULA_OCR_BACKEND" -Value "none"

  Write-Warn ".env was created with mock LLM settings. Edit .env with the real Qwen/OpenAI-compatible API settings before customer testing."
  Write-Warn "AUTH_ENABLED=true was set. Configure AUTH_PASSWORD before exposing the app."
  Write-Warn "Third-party embeddings are enabled by default. Set EMBEDDING_API_KEY or QWEN_API_KEY before importing documents."
}

function Apply-CustomerEnvMigrations {
  $values = Read-DotEnv
  $changed = $false

  $backendExtras = ""
  if ($values.ContainsKey("BACKEND_EXTRAS")) {
    $backendExtras = "," + $values["BACKEND_EXTRAS"].ToLowerInvariant() + ","
  }
  if (-not $values.ContainsKey("BACKEND_EXTRAS") -or $backendExtras.Contains(",full,") -or $backendExtras.Contains(",embeddings,")) {
    Set-DotEnvValue -Key "BACKEND_EXTRAS" -Value "parsing"
    $changed = $true
  }

  $embeddingBackend = ""
  if ($values.ContainsKey("EMBEDDING_BACKEND")) {
    $embeddingBackend = $values["EMBEDDING_BACKEND"].ToLowerInvariant().Replace("_", "-")
  }
  $embeddingModel = ""
  if ($values.ContainsKey("EMBEDDING_MODEL")) {
    $embeddingModel = $values["EMBEDDING_MODEL"].ToLowerInvariant()
  }
  $embeddingEndpoint = ""
  if ($values.ContainsKey("EMBEDDING_ENDPOINT_PATH")) {
    $embeddingEndpoint = $values["EMBEDDING_ENDPOINT_PATH"].ToLowerInvariant()
  }
  $shouldMigrateEmbedding = (
    $embeddingBackend -eq "" -or
    $embeddingBackend -eq "sentence-transformers" -or
    (
      $embeddingBackend -eq "openai-compatible" -and
      ($embeddingModel -eq "" -or $embeddingModel -eq "text-embedding-v4") -and
      ($embeddingEndpoint -eq "" -or $embeddingEndpoint -eq "/embeddings")
    )
  )
  if ($shouldMigrateEmbedding) {
    Set-DotEnvValue -Key "EMBEDDING_BACKEND" -Value "dashscope-multimodal"
    Set-DotEnvValue -Key "EMBEDDING_BASE_URL" -Value "https://dashscope.aliyuncs.com/compatible-mode/v1"
    Set-DotEnvValue -Key "EMBEDDING_ENDPOINT_PATH" -Value "/services/embeddings/multimodal-embedding/multimodal-embedding"
    Set-DotEnvValue -Key "EMBEDDING_MODEL" -Value "qwen3-vl-embedding"
    Set-DotEnvValue -Key "EMBEDDING_DIMENSION" -Value "1024"
    Set-DotEnvValue -Key "EMBEDDING_BATCH_SIZE" -Value "16"
    Set-DotEnvValue -Key "EMBEDDING_LOCAL_FILES_ONLY" -Value "false"
    Set-DotEnvValue -Key "FORMULA_OCR_BACKEND" -Value "none"
    $changed = $true
  }

  if (-not $values.ContainsKey("QDRANT_COLLECTION") -or $values["QDRANT_COLLECTION"] -eq "presale_knowledge") {
    Set-DotEnvValue -Key "QDRANT_COLLECTION" -Value "presale_knowledge_qwen3_vl_embedding"
    $changed = $true
  }

  if (-not $values.ContainsKey("AUTH_ENABLED")) {
    Set-DotEnvValue -Key "AUTH_ENABLED" -Value "true"
    $changed = $true
  }
  if (-not $values.ContainsKey("NEXT_PUBLIC_AUTH_ENABLED")) {
    Set-DotEnvValue -Key "NEXT_PUBLIC_AUTH_ENABLED" -Value "true"
    $changed = $true
  }

  if ($changed) {
    Write-Warn ".env was migrated to customer deployment defaults: BACKEND_EXTRAS=parsing and third-party embeddings."
  }
}

function Import-InstallAssets {
  if (-not (Test-Path $InstallAssetsDir)) {
    return
  }

  $imageTar = Join-Path $InstallAssetsDir "docker-images.tar"
  if (Test-Path $imageTar) {
    Write-Step "Loading Docker image bundle from install-assets\\docker-images.tar"
    & docker load -i $imageTar
    if ($LASTEXITCODE -ne 0) {
      throw "Failed to load Docker image bundle: $imageTar"
    }
  }

  $caseLibraryZip = Join-Path $InstallAssetsDir "case_library.zip"
  $sourceCaseLibrary = Join-Path $InstallAssetsDir "case_library"
  if ((Test-Path $caseLibraryZip) -or (Test-Path $sourceCaseLibrary)) {
    $targetCaseLibrary = Join-Path $RootDir "backend\data\case_library"
    New-Item -ItemType Directory -Force -Path $targetCaseLibrary | Out-Null
  }

  if (Test-Path $caseLibraryZip) {
    $targetCaseLibrary = Join-Path $RootDir "backend\data\case_library"
    Write-Step "Extracting install-assets\\case_library.zip into backend\\data\\case_library"
    Expand-Archive -Path $caseLibraryZip -DestinationPath $targetCaseLibrary -Force

    $nestedCaseLibrary = Join-Path $targetCaseLibrary "case_library"
    if (Test-Path $nestedCaseLibrary) {
      foreach ($item in @(Get-ChildItem -Path $nestedCaseLibrary -Force)) {
        Copy-Item -Path $item.FullName -Destination $targetCaseLibrary -Recurse -Force
      }
      Remove-Item $nestedCaseLibrary -Recurse -Force
    }
  }

  if (Test-Path $sourceCaseLibrary) {
    $targetCaseLibrary = Join-Path $RootDir "backend\data\case_library"
    Write-Step "Importing packaged case_library into backend\\data\\case_library"
    $items = @(Get-ChildItem -Path $sourceCaseLibrary -Force)
    if ($items.Count -eq 0) {
      Write-Warn "install-assets\\case_library exists but is empty."
    } else {
      foreach ($item in $items) {
        Copy-Item -Path $item.FullName -Destination $targetCaseLibrary -Recurse -Force
      }
    }
  }
}

function Get-EffectivePorts {
  $values = Read-DotEnv
  $backend = $BackendPort
  $frontend = $FrontendPort

  if ($values.ContainsKey("BACKEND_PORT") -and $values["BACKEND_PORT"] -match "^\d+$") {
    $backend = [int]$values["BACKEND_PORT"]
  }
  if ($values.ContainsKey("FRONTEND_PORT") -and $values["FRONTEND_PORT"] -match "^\d+$") {
    $frontend = [int]$values["FRONTEND_PORT"]
  }

  return @{
    Backend = $backend
    Frontend = $frontend
    Values = $values
  }
}

function Warn-EnvIssues {
  param([hashtable]$EnvValues, [int]$EffectiveBackendPort)

  if ($EnvValues.ContainsKey("NEXT_PUBLIC_API_BASE_URL")) {
    $expected = "http://localhost:$EffectiveBackendPort/api/v1"
    $actual = $EnvValues["NEXT_PUBLIC_API_BASE_URL"]
    if ($actual -match "localhost|127\.0\.0\.1" -and $actual -ne $expected) {
      Write-Warn "NEXT_PUBLIC_API_BASE_URL is '$actual', but BACKEND_PORT is $EffectiveBackendPort. Frontend export/generation calls may fail unless these match."
    }
  }

  if ($EnvValues.ContainsKey("LLM_PROVIDER_BACKEND") -and $EnvValues["LLM_PROVIDER_BACKEND"] -eq "mock") {
    Write-Warn "LLM_PROVIDER_BACKEND=mock. The app will start, but real generation needs Qwen/OpenAI-compatible credentials in .env."
  }

  if ($EnvValues.ContainsKey("AUTH_ENABLED") -and $EnvValues["AUTH_ENABLED"].ToLowerInvariant() -eq "true") {
    $authPassword = ""
    if ($EnvValues.ContainsKey("AUTH_PASSWORD")) {
      $authPassword = $EnvValues["AUTH_PASSWORD"]
    }
    $authPasswordHash = ""
    if ($EnvValues.ContainsKey("AUTH_PASSWORD_HASH")) {
      $authPasswordHash = $EnvValues["AUTH_PASSWORD_HASH"]
    }
    if ([string]::IsNullOrWhiteSpace($authPassword) -and [string]::IsNullOrWhiteSpace($authPasswordHash)) {
      Write-Warn "AUTH_ENABLED=true but AUTH_PASSWORD/AUTH_PASSWORD_HASH is empty. Login will fail until a password is configured."
    }
  }

  if ($EnvValues.ContainsKey("BACKEND_EXTRAS")) {
    $backendExtras = "," + $EnvValues["BACKEND_EXTRAS"].ToLowerInvariant() + ","
    if ($backendExtras.Contains(",full,") -or $backendExtras.Contains(",embeddings,")) {
      Write-Warn "BACKEND_EXTRAS includes local ML dependencies. Customer installs should normally use BACKEND_EXTRAS=parsing with third-party embeddings."
    }
  }

  $embeddingBackend = ""
  if ($EnvValues.ContainsKey("EMBEDDING_BACKEND")) {
    $embeddingBackend = $EnvValues["EMBEDDING_BACKEND"].ToLowerInvariant().Replace("_", "-")
  }
  if ($embeddingBackend -eq "openai-compatible" -or $embeddingBackend -eq "dashscope-multimodal") {
    $embeddingKey = ""
    foreach ($keyName in @("EMBEDDING_API_KEY", "QWEN_API_KEY", "OPENAI_API_KEY")) {
      if ($EnvValues.ContainsKey($keyName) -and -not [string]::IsNullOrWhiteSpace($EnvValues[$keyName])) {
        $embeddingKey = $EnvValues[$keyName]
        break
      }
    }
    if ($embeddingKey -in @("", "sk-xxxxx", "replace-with-real-key", "your-api-key", "xxx")) {
      Write-Warn "EMBEDDING_BACKEND=$embeddingBackend but no real embedding API key is configured. Document import/retrieval will fail until EMBEDDING_API_KEY or QWEN_API_KEY is set."
    }
  }

  if ($embeddingBackend -eq "fallback") {
    Write-Warn "EMBEDDING_BACKEND=fallback can start the app but retrieval quality will be weak."
  }

  if (($embeddingBackend -eq "openai-compatible" -or $embeddingBackend -eq "dashscope-multimodal") -and $EnvValues.ContainsKey("QDRANT_COLLECTION")) {
    if ($EnvValues["QDRANT_COLLECTION"] -eq "presale_knowledge") {
      Write-Warn "QDRANT_COLLECTION is still presale_knowledge. Use a new collection name or rebuild the library when switching embedding models."
    }
  }

  if ($EnvValues.ContainsKey("EMBEDDING_LOCAL_FILES_ONLY") -and $EnvValues["EMBEDDING_LOCAL_FILES_ONLY"] -eq "true") {
    Write-Warn "EMBEDDING_LOCAL_FILES_ONLY=true. On a fresh customer laptop this requires a preloaded embedding model cache inside the backend container."
  }
}

function Invoke-Compose {
  param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$ComposeArgs
  )

  & docker compose --project-name $ProjectName --env-file $EnvFile -f $ComposeFile @ComposeArgs
  if ($LASTEXITCODE -ne 0) {
    throw "docker compose failed: $($ComposeArgs -join ' ')"
  }
}

function Wait-Postgres {
  param([string]$User, [string]$Database)

  Write-Step "Waiting for Postgres"
  for ($i = 1; $i -le 90; $i++) {
    & docker compose --project-name $ProjectName --env-file $EnvFile -f $ComposeFile exec -T postgres pg_isready -U $User -d $Database *> $null
    if ($LASTEXITCODE -eq 0) {
      return
    }
    Start-Sleep -Seconds 2
  }

  Invoke-Compose logs --tail=120 postgres
  throw "Timed out waiting for Postgres"
}

function Wait-Url {
  param(
    [string]$Url,
    [string]$ServiceName,
    [int]$Attempts = 90
  )

  Write-Step "Waiting for $ServiceName at $Url"
  for ($i = 1; $i -le $Attempts; $i++) {
    try {
      $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 3
      if ($response.StatusCode -ge 200 -and $response.StatusCode -lt 500) {
        return
      }
    } catch {
      Start-Sleep -Seconds 2
    }
  }

  Invoke-Compose logs --tail=120 $ServiceName
  throw "Timed out waiting for $ServiceName at $Url"
}

function Run-Install {
  Ensure-Prerequisites
  Wait-DockerReady
  Ensure-EnvFile
  Apply-CustomerEnvMigrations
  Import-InstallAssets

  $ports = Get-EffectivePorts
  Warn-EnvIssues -EnvValues $ports.Values -EffectiveBackendPort $ports.Backend

  $postgresUser = "copilot"
  $postgresDb = "copilot_db"
  if ($ports.Values.ContainsKey("POSTGRES_USER")) {
    $postgresUser = $ports.Values["POSTGRES_USER"]
  }
  if ($ports.Values.ContainsKey("POSTGRES_DB")) {
    $postgresDb = $ports.Values["POSTGRES_DB"]
  }

  Write-Step "Starting infrastructure containers"
  Invoke-Compose up -d --build postgres redis qdrant minio gateway

  Wait-Postgres -User $postgresUser -Database $postgresDb

  Write-Step "Running database migrations"
  Invoke-Compose run --rm backend alembic upgrade head

  Write-Step "Starting backend and frontend containers"
  Invoke-Compose up -d --build backend frontend

  Wait-Url -Url "http://127.0.0.1:$($ports.Backend)/health" -ServiceName "backend" -Attempts 90
  Wait-Url -Url "http://127.0.0.1:$($ports.Frontend)/projects" -ServiceName "frontend" -Attempts 90

  $frontendUrl = "http://127.0.0.1:$($ports.Frontend)/projects"
  Write-Step "Installation completed"
  Write-Host "Frontend: $frontendUrl"
  Write-Host "Backend:  http://127.0.0.1:$($ports.Backend)/health"
  Write-Host "Logs:     powershell -ExecutionPolicy Bypass -File scripts\windows-install.ps1 -Action logs"
  Write-Host "Stop:     powershell -ExecutionPolicy Bypass -File scripts\windows-install.ps1 -Action stop"

  if (-not $NoBrowser) {
    Start-Process $frontendUrl | Out-Null
  }
}

function Run-Restart {
  Wait-DockerReady
  Ensure-EnvFile
  Apply-CustomerEnvMigrations
  Invoke-Compose down
  Run-Install
}

function Main {
  if ([System.Environment]::OSVersion.Platform -ne [System.PlatformID]::Win32NT) {
    throw "This installer is intended for Windows. Use scripts/dev-up.sh or scripts/deploy.sh on macOS/Linux."
  }

  Set-Location $RootDir
  Request-ElevationIfNeeded

  switch ($Action) {
    "install" {
      Run-Install
    }
    "restart" {
      Run-Restart
    }
    "stop" {
      Ensure-EnvFile
      Wait-DockerReady
      Invoke-Compose down
    }
    "status" {
      Ensure-EnvFile
      Wait-DockerReady
      Invoke-Compose ps
    }
    "logs" {
      Ensure-EnvFile
      Wait-DockerReady
      Invoke-Compose logs -f --tail=150
    }
  }
}

try {
  Main
} catch {
  Write-Fail $_.Exception.Message
  Write-Host ""
  Write-Host "Troubleshooting:"
  Write-Host "1. If Docker or WSL2 was just installed, reboot Windows and rerun install-windows.cmd."
  Write-Host "2. Make sure BIOS/UEFI virtualization is enabled."
  Write-Host "3. If ports 3000 or 8000 are occupied, edit FRONTEND_PORT/BACKEND_PORT in .env and rerun."
  Write-Host "4. Use '-Action logs' after startup to inspect container logs."
  exit 1
}
