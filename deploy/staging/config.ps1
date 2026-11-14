# Names and settings shared by setup.ps1 and deploy.ps1. Nothing secret lives here: secrets are in
# Google Secret Manager and are created once by setup.ps1.
$Project   = "job-apply-agent-staging"
$Region    = "europe-west1"             # Firebase Hosting can route to Cloud Run here
$Domain    = "jaa-x7k2.nisarworks.com"  # deliberately hard to guess; invite-only and not indexed anyway
$Repo      = "jaa"                      # Artifact Registry repository
$SqlInst   = "jaa-db"
$Bucket    = "$Project-files"
$ApiSvc    = "jaa-api"
$WebSvc    = "jaa-web"
$MigrateJob = "jaa-migrate"
$RunSA     = "jaa-run@$Project.iam.gserviceaccount.com"
$Registry  = "$Region-docker.pkg.dev/$Project/$Repo"
$SqlConn   = "${Project}:${Region}:$SqlInst"

# Gmail sign-in uses the OAuth client of the job Google account's project (job-apply-agent-514718).
$GoogleClientId = "947809586306-f44637cuoi2vd5h6dtlhcvncfhgi2ojf.apps.googleusercontent.com"

# Staging is private: only these addresses can sign up, every application goes to the sandbox address.
$SignupAllowlist  = "nisar.3dcellular@gmail.com"
$SandboxRecipient = "nisar_87@hotmail.com"
