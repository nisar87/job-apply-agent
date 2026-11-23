# Product decisions, version 2

Recorded 23 November 2026. This file is the record of what the owner decided, what was assumed, what was
rejected and what is still open. The specification follows it.

## Decided by the owner

| # | Decision | Owner's words, shortened |
| --- | --- | --- |
| D1 | Everything automatic; nothing left for the user to do by hand in the normal case | "I want everything automatic, nothing manual" |
| D2 | The agent submits applications, including web forms, in the user's name without a check | "yes agent should submit my information on my behalf" |
| D3 | A clear country filter decides where the agent applies | "there must be a good filter, which country to apply" |
| D4 | The product is general. Roles, seniority, skills and languages come from the user's CV, not from settings written for one person | "do not customize this product for me, you should define things by yourself after reading the CV" |
| D5 | The number of live, relevant jobs must be much larger | "it should have large number of active jobs" |
| D6 | The interface must be understandable without study | "I never able to understand the interface" |
| D7 | Two model providers: Claude for the demanding steps, Gemini for the ordinary ones; if Claude is unavailable, continue on Gemini | "for stronger thing you can use that ... if claude api expire then keep using gemini" |
| D8 | AI CV Studio will later tailor a CV per job; the agent decides when to ask for one and defines the interface it needs | "it will your decision either to send to aicvstudio ... you will have to tell me what parameter" |
| D9 | The user should never have to work out what to do next; the product says so | "later I don't want to keep figuring out what to do" |

| D10 | A user may exclude their own country and ask only for jobs abroad with visa sponsorship, or remote jobs | "I can only work in Pakistan but I don't need any job from Pakistan, I need visa and sponsor letter or remote job" |

Facts the owner gave about himself, used only as test data, not built into the product: speaks English and
Urdu, no German; right to work in Pakistan only; wants sponsored roles abroad or remote roles, none in Pakistan.

## Assumptions made to proceed (say so if any is wrong)

| # | Assumption | Reason |
| --- | --- | --- |
| A1 | Web forms on supported hiring systems become the main route. Email is used only when the ad itself gives an address | Follows from D1 and D5; evidence in the audit |
| A2 | Emails to addresses the agent looked up on a company's site stop | Wrong inbox (adesso's reply); risk to the user's mailbox and name |
| A3 | "Nothing manual" has three exceptions the agent cannot remove: CAPTCHA or a forced login, a legal declaration with no stored fact, and commitments (interview time, salary, offer). These go to one "Needs you" list | The owner's own brief requires asking for help at these points rather than bypassing them |
| A4 | Languages the user can work in are read from the CV and confirmed once. A job that requires another language is skipped | D4, and the adesso reply |
| A5 | Work eligibility is one question at setup: "Which countries may you work in today?" plus "Apply where the employer must sponsor a visa?" (default yes, only to ads that offer it) | Cannot be read reliably from a CV; decides D3 |
| A6 | Role family and level come from the CV: the agent proposes target titles and the levels it will accept, and excludes internships, junior and unrelated roles. The user confirms with one tap | D4; audit findings on matching |
| A7 | Daily limit starts at 10 submissions and rises automatically to 25 when the confirmed-submission rate stays above 90% for a week | Protects the user's name while the form route is new |
| A8 | First users: the owner and invited testers only, until 30 real submissions are confirmed | Real applications carry real consequences |
| A9 | Google sign-in for Gmail is the product's method. The app-password method remains for the owner until Google approves the app | Vision; Google's review takes weeks |
| A10 | Each user may bring their own model keys; the operator's keys are the fallback where allowed | Already built; keeps cost with the user |

## Rejected alternatives

| Alternative | Why rejected |
| --- | --- |
| Keep email as the main route and find more addresses | Reaches general inboxes; most employers require their portal |
| Apply through LinkedIn, Indeed or similar by automation | Their terms forbid it; accounts get closed |
| Solve CAPTCHAs or create employer-site accounts automatically | Bypasses a control; forbidden by the owner's brief |
| Raise volume by lowering the match threshold | Produces applications like the internship and "Head of Engineering" ones |
| Rewrite the backend | The foundation is sound; the gaps are matching, forms and interface |
| Tailor a CV for every job from day one | Cost and delay with unproven benefit; begin with strong matches only |

## Open questions

| # | Question | Default until answered |
| --- | --- | --- |
| Q2 | Budget for Google's verification and yearly security assessment for Gmail sign-in | Stay on app password for invited users |
| Q3 | AI CV Studio: who builds the interface in section "CV tailoring service" of the specification, and when | Agent works without it; hook stays switched off |
| Q4 | Daily spending limit per user on Claude and Gemini | US$1 a day, as today |
| Q5 | Public launch, pricing, legal entity | Out of scope for version 2 |
