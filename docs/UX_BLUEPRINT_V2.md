# Interface blueprint, version 2

Status: proposed. Follows `MASTER_PRODUCT_SPEC_V2.md`.

## Principles

1. The agent reports to the user; the user does not operate the agent.
2. One screen answers five questions: is it working, what has it done, what did it find, who replied,
   what needs me.
3. Every screen states the next step, or states that there is none.
4. Settings have working defaults and stay out of the way.
5. Plain words. No "campaign", "draft", "threshold", "source", "pool", "cooldown" or "sandbox" in the
   main interface.

## Screens

Version 1 has ten menu entries. Version 2 has three, plus setup.

| Version 2 | Replaces |
| --- | --- |
| **Home** | Today, Drafts, Inbox notices |
| **Applications** | Applications, Inbox conversations |
| **Settings** | Searches, Where we look, CVs and facts, Setup chat, Account |
| Setup (first visit only) | Setup chat, CVs and facts, Account |
| Admin (operators only) | Admin |

## Front door (signed out)

One page: what it does in one sentence, three short steps (CV, Gmail, Start), "Create your account" and
"Sign in". While sign-up is by invitation it says so and offers a way to ask for one.

## Setup

```
Step 1 of 4  Your CV
+--------------------------------------------------+
|  [ Drop your CV here, or choose a file ]         |
+--------------------------------------------------+
   after reading:
   Here is what I understood
   Name            Nisar Ahmad                [edit]
   Based in        Islamabad, Pakistan        [edit]
   Experience      15 years                   [edit]
   Work you do     Senior backend engineer    [edit]
   Main skills     C#, .NET, SQL Server ...   [edit]
   Languages       English, Urdu              [edit]
                                   [ Looks right ]
```

```
Step 2 of 4  Your Gmail
   Applications go out from this address and
   replies come back to it.
   [ Connect Gmail ]
   Send my reports to   you@example.com      [change]
```

```
Step 3 of 4  What you want
+--------------------------------------------------+
| Senior backend engineer roles using C# and .NET, |
| remote or in countries I can work in.            |
+--------------------------------------------------+
   Where may you work today?   [ Pakistan x ] [ add ]
   Apply where the employer sponsors a visa?  (o) Yes ( ) No
                                         [ Continue ]
```

```
Step 4 of 4  Review
   Your agent will
     - look for new jobs every few hours
     - apply to up to 10 a day that fit your CV
     - answer routine questions from employers
   It will ask you about
     - interview times, salary and offers
     - anything it cannot answer from your CV
   [ ] Try it first: prepare everything, send nothing
                                   [ Start my agent ]
```

Behaviour: each step saves as it goes; leaving and returning resumes at the same step; a failed CV read
says what went wrong and allows another file; a refused Gmail connection explains what was not granted.

## Home

```
Your agent is working.  Last checked jobs 20 minutes ago.     [ Pause ]

Needs you (2)
  Paessler: asks for your salary expectation        [ Answer ]
  adesso: wants you to apply on their portal        [ Open  ]

This week
  12 applied      3 replies      1 interview      240 jobs checked

Latest
  Today 13:05   Applied   Senior .NET Developer, Wavetec        confirmed
  Today 11:40   Reply     Hannover Re: received your application
  Yesterday     Skipped   41 jobs: 22 need German, 9 too junior, 10 other
```

- The top line has four states: working, paused, stopped, cannot work (with the one thing to fix).
- "Needs you" is empty most days and then reads "Nothing needs you."
- "Skipped" is one line with reasons, expandable. It is how the user sees the gates working.
- An item under "Needs you" opens a single question with the answer field in place.

## Applications

One list, newest first, with four states the user sees: **Applied**, **Replied**, **Interview**,
**Closed** (rejected, withdrawn, no response). A search box and a state filter. Opening one shows the job,
what was sent (the form answers or the email, and the CV used), the confirmation, and the conversation.
"Withdraw" and "Never contact this company" are here.

## Settings

Sections, each collapsed until opened: Your CV and facts, What you want, Where and how much (countries,
daily limit, hours), Mail, Reports, Model keys, Never contact, Your data. Every control shows its default
and what it does in one sentence.

## Reports by email

A daily or weekly message with the same content as Home. Urgent items (interview, offer) are sent at once.

## Accessibility and devices

- Works at 360 pixels wide; Home's three blocks stack in the order status, needs you, latest.
- Every control reachable and operable by keyboard, with a visible focus ring.
- Text contrast at least 4.5 to 1; status is never shown by colour alone.
- Motion only on user action, and none when the device asks for reduced motion.

## Words

| Version 1 | Version 2 |
| --- | --- |
| Search, campaign | What you want |
| Draft awaiting approval | (removed; the agent sends) |
| Apply by hand | Needs you |
| Match threshold | (Settings) How picky should I be? |
| Where we look | (removed from the user's view) |
| Dry run | Try it first |
| Needs attention | Needs you |
