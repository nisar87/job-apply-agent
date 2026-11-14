# One-time setup of the private staging environment in its own Firebase / Google Cloud project.
# Safe to run again: every step skips what already exists. Needs `gcloud auth login` first.
# Reads GEMINI_API_KEY and GOOGLE_CLIENT_SECRET from the repository's .env; never prints a secret.
$ErrorActionPreference = "Continue"   # gcloud writes progress to stderr; success is judged by exit codes
. "$PSScriptRoot\config.ps1"
$root = Resolve-Path "$PSScriptRoot\..\.."
$gcloudExe = (Get-Command gcloud.cmd -ErrorAction Stop).Source
function gcloud {
    & $gcloudExe @args 2>&1 | ForEach-Object { if ($_ -is [System.Management.Automation.ErrorRecord]) { Write-Host $_.ToString() } else { $_ } }
    if ($LASTEXITCODE -ne 0) { throw "gcloud $($args -join ' ') failed (exit $LASTEXITCODE)" }
}

function Has($cmd) { & cmd /c "$cmd >nul 2>nul"; return $LASTEXITCODE -eq 0 }
function RandomSecret([int]$n = 40) {
    $chars = [char[]]"abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    $bytes = New-Object byte[] $n
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    -join ($bytes | ForEach-Object { $chars[$_ % $chars.Length] })
}
function EnvValue($name) {
    $line = Get-Content "$root\.env" | Where-Object { $_ -match "^$name=" } | Select-Object -First 1
    if (-not $line) { throw "$name is missing from .env" }
    $line.Substring($name.Length + 1)
}
function PutSecret($name, $value) {
    $tmp = New-TemporaryFile
    [IO.File]::WriteAllText($tmp, $value)   # no trailing newline
    if (Has "gcloud secrets describe $name --project $Project") {
        gcloud secrets versions add $name --data-file=$tmp --project $Project | Out-Null
    } else {
        gcloud secrets create $name --data-file=$tmp --replication-policy=automatic --project $Project | Out-Null
    }
    Remove-Item $tmp -Force
    Write-Host "secret $name stored"
}

gcloud config set project $Project | Out-Null
Write-Host "== APIs"
gcloud services enable run.googleapis.com sqladmin.googleapis.com artifactregistry.googleapis.com `
    cloudbuild.googleapis.com secretmanager.googleapis.com cloudscheduler.googleapis.com iam.googleapis.com `
    iamcredentials.googleapis.com sts.googleapis.com firebasehosting.googleapis.com --project $Project

Write-Host "== Artifact Registry"
if (-not (Has "gcloud artifacts repositories describe $Repo --location $Region --project $Project")) {
    gcloud artifacts repositories create $Repo --repository-format=docker --location $Region --project $Project
}

Write-Host "== Runtime service account"
if (-not (Has "gcloud iam service-accounts describe $RunSA --project $Project")) {
    gcloud iam service-accounts create jaa-run --display-name "Job Apply Agent runtime" --project $Project
}
foreach ($role in "roles/cloudsql.client", "roles/secretmanager.secretAccessor") {
    gcloud projects add-iam-policy-binding $Project --member "serviceAccount:$RunSA" --role $role --condition=None | Out-Null
}

Write-Host "== File bucket (CVs)"
if (-not (Has "gcloud storage buckets describe gs://$Bucket --project $Project")) {
    gcloud storage buckets create "gs://$Bucket" --location $Region --uniform-bucket-level-access --public-access-prevention --project $Project
}
gcloud storage buckets add-iam-policy-binding "gs://$Bucket" --member "serviceAccount:$RunSA" --role roles/storage.objectAdmin | Out-Null

Write-Host "== Cloud SQL (smallest instance)"
if (-not (Has "gcloud sql instances describe $SqlInst --project $Project")) {
    gcloud sql instances create $SqlInst --database-version POSTGRES_16 --edition ENTERPRISE --tier db-f1-micro `
        --region $Region --storage-size 10 --storage-auto-increase --backup-start-time 02:00 `
        --database-flags cloudsql.iam_authentication=off --project $Project
}
if (-not (Has "gcloud sql databases describe jobagent --instance $SqlInst --project $Project")) {
    gcloud sql databases create jobagent --instance $SqlInst --project $Project
}
$secretsExist = Has "gcloud secrets describe DATABASE_URL --project $Project"
if (-not $secretsExist) {
    # The owner role runs migrations; the app role is unprivileged so row-level security applies to it.
    $ownerPw = RandomSecret; $appPw = RandomSecret
    gcloud sql users set-password postgres --instance $SqlInst --password $ownerPw --project $Project | Out-Null
    if (Has "gcloud sql users describe jobagent_app --instance $SqlInst --project $Project") {
        gcloud sql users set-password jobagent_app --instance $SqlInst --password $appPw --project $Project | Out-Null
    } else {
        gcloud sql users create jobagent_app --instance $SqlInst --password $appPw --project $Project | Out-Null
    }
    $sock = "host=/cloudsql/$SqlConn"
    PutSecret DATABASE_URL "postgresql+psycopg://jobagent_app:$appPw@/jobagent?$sock"
    PutSecret DATABASE_OWNER_URL "postgresql+psycopg://postgres:$ownerPw@/jobagent?$sock"
    PutSecret SECRET_KEY (RandomSecret 64)
    $fernet = & "$root\backend\.venv\Scripts\python.exe" -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"
    PutSecret TOKEN_ENCRYPTION_KEY $fernet.Trim()
    PutSecret SCHEDULER_TOKEN (RandomSecret 48)
}
if (-not (Has "gcloud secrets describe GEMINI_API_KEY --project $Project")) { PutSecret GEMINI_API_KEY (EnvValue GEMINI_API_KEY) }
# GOOGLE_CLIENT_SECRET belongs to this project's own OAuth client (created in the console); it is stored
# once with: gcloud secrets create GOOGLE_CLIENT_SECRET --data-file=<file holding only the secret>
if (-not (Has "gcloud secrets describe GOOGLE_CLIENT_SECRET --project $Project")) {
    Write-Host "NOTE: store GOOGLE_CLIENT_SECRET for this project's OAuth client before deploying"
}

Write-Host "== GitHub Actions: keyless sign-in (Workload Identity Federation)"
$DeploySA = "jaa-deploy@$Project.iam.gserviceaccount.com"
if (-not (Has "gcloud iam service-accounts describe $DeploySA --project $Project")) {
    gcloud iam service-accounts create jaa-deploy --display-name "GitHub Actions deployer" --project $Project
}
foreach ($role in "roles/run.admin", "roles/cloudbuild.builds.editor", "roles/artifactregistry.writer",
                  "roles/firebasehosting.admin", "roles/storage.admin", "roles/serviceusage.serviceUsageConsumer",
                  "roles/cloudscheduler.admin", "roles/logging.viewer", "roles/secretmanager.secretAccessor") {
    gcloud projects add-iam-policy-binding $Project --member "serviceAccount:$DeploySA" --role $role --condition=None | Out-Null
}
gcloud iam service-accounts add-iam-policy-binding $RunSA --member "serviceAccount:$DeploySA" --role roles/iam.serviceAccountUser --project $Project | Out-Null
$number = gcloud projects describe $Project --format "value(projectNumber)"
gcloud iam service-accounts add-iam-policy-binding "$number-compute@developer.gserviceaccount.com" --member "serviceAccount:$DeploySA" --role roles/iam.serviceAccountUser --project $Project 2>$null | Out-Null
if (-not (Has "gcloud iam workload-identity-pools describe github --location global --project $Project")) {
    gcloud iam workload-identity-pools create github --location global --display-name "GitHub" --project $Project
    gcloud iam workload-identity-pools providers create-oidc job-apply-agent --location global --workload-identity-pool github `
        --issuer-uri "https://token.actions.githubusercontent.com" `
        --attribute-mapping "google.subject=assertion.sub,attribute.repository=assertion.repository,attribute.ref=assertion.ref" `
        --attribute-condition "assertion.repository == 'nisar87/job-apply-agent' && assertion.ref == 'refs/heads/main'" `
        --project $Project
}
gcloud iam service-accounts add-iam-policy-binding $DeploySA --role roles/iam.workloadIdentityUser `
    --member "principalSet://iam.googleapis.com/projects/$number/locations/global/workloadIdentityPools/github/attribute.repository/nisar87/job-apply-agent" `
    --project $Project | Out-Null
Write-Host "workload identity provider: projects/$number/locations/global/workloadIdentityPools/github/providers/job-apply-agent"
Write-Host "setup done"
