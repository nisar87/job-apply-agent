# First run on your own computer

Everything in this repository was tested against stand-ins for Gemini, Gmail and the
job sites, because the build environment could not reach them. This page takes you from
the code to a first real run, in order of risk.

## 1. Get the code next to your key file

You already have `C:\Users\<you>\job-apply-agent\.env`. In a terminal:

```bash
cd "C:\Users\<you>\job-apply-agent"
git init
git remote add origin https://github.com/nisar87/job-apply-agent.git
git pull origin main
```

`.env` is ignored by Git, so your key stays where it is.

## 2. Try it with no real services (demo mode)

In `.env` set `LLM_PROVIDER=fake` and `MAIL_PROVIDER=fake`, then follow "Run it locally"
in the README. Sign up, upload a CV, use the setup chat, press "Look for jobs now" and
"Write drafts now". Sample jobs and a practice mailbox are used; nothing leaves your
computer.

## 3. Check the Gemini connection

Set `LLM_PROVIDER=gemini` in `.env`, then:

```bash
cd backend
python ../scripts/check_gemini.py
```

Four lines should say `[ok]`. If a model name is rejected, change the three
`GEMINI_MODEL_...` lines in `.env` to models your key can use. Model names change often;
they are settings for that reason.

## 4. Real Gemini, practice mailbox

Keep `MAIL_PROVIDER=fake`. Add a free Adzuna key (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`) or
rely on Arbeitnow, which needs no key. Run a search as a dry run and read the drafts.
This is where to judge the writing and tune the match threshold.

## 5. Real Gmail, sandboxed

1. In Google Cloud, create a project, enable the Gmail API, and create an OAuth client
   of type "Web application" with the redirect URI from `.env`.
2. Fill `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`, set `MAIL_PROVIDER=gmail`.
3. Set `SANDBOX_RECIPIENT` to an address of your own. Outside production every
   application is delivered there instead of to the employer. With the real Gmail
   provider and no sandbox address, the application refuses to send.
4. Connect Gmail on the Account page, approve one draft, and check that it arrives in
   your sandbox mailbox with the CV attached.

## 6. First real application

Only after step 5 looks right: set `APP_ENV=production` on the machine that runs it,
keep approval mode on "I approve every draft", and approve one application you would
have sent yourself.
