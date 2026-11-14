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

# Gmail sign-in uses this project's own OAuth client (consent screen in Testing; the job mailbox is the
# test user). Its secret is in Secret Manager as GOOGLE_CLIENT_SECRET.
$GoogleClientId = "644630290784-j7a8potb2ja59dctu8qipef2cn3r95lo.apps.googleusercontent.com"

# Staging is private: only these addresses can sign up, every application goes to the sandbox address.
$SignupAllowlist  = "nisar.3dcellular@gmail.com"
$SandboxRecipient = "nisar_87@hotmail.com"
