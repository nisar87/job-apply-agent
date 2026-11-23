# Phase 4: the first 30 real submissions

Goal (roadmap): 30 confirmed real submissions from the owner's account, none to a job the owner rejects.
Nothing here happens until the owner agrees to each step.

## Before starting
1. `v2-build` is merged and deployed to the staging site (owner's approval).
2. The owner's search is on **Dry run** ("Try it first"). Run for two days. Open Applications and look at 30 filled
   forms and written emails: do the answers match the CV and the fact sheet? Note any wrong answer.
3. Aim: at least 70% of forms reach the final step with correct answers (phase 3 acceptance).

## Going live
1. Admin → Users → tick **Real sending** for the owner's account. This also sets **Reviewed first** to 30: the
   first 30 applications each wait for the owner's approval, then go out.
2. Daily limit starts at 10 (it rises to 25 on its own after a week with at least 90% of submissions confirmed).
3. Set the search to **Active** in full-auto mode.

## Each day
- Open Applications → "Needs you". Approve or reject each application; "Reviewed first" on the Admin page counts
  the approved ones (for example 12 / 30).
- Anything marked **Sent, not confirmed** needs a look at its screenshot; it is never retried by itself.
- Reject = skip the job and say why in one line; those reasons become new filter rules.

## Done when
- The Admin counter shows 30 / 30, at least 30 applications are "sent" with a confirmation, and the owner has
  rejected none after the fact. Then set **Reviewed first** to 0: the agent runs without waiting.

## Stop at once if
- An application goes to a job the owner does not want, or a wrong fact is submitted: pause the account (Admin →
  Pause), write the case down, and fix the cause before resuming.
