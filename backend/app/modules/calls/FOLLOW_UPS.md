# Call integration issue drafts

These are local drafts for the two issues requested in PR #7. They have not
been published to GitHub.

## Decide and implement the source of `scenario.ask` (done)

Resolved in KAN-18: `scam_scenarios.ask` (migration 0005), required when a
scenario is created. Built-in scenarios added before it need an ask set before
they can start a call.

The call context requires an explicit `scenario.ask`, but `scam_scenarios` has
no column for it. The call endpoint cannot assemble a valid context until the
team chooses where that request comes from. The parser must not invent a
default money request.

Acceptance criteria:

- Agree on the source of the ask with the scenario and call integration owners.
- Document the mapping and add any necessary schema, API and seed-data changes.
- Test that call assembly supplies the chosen ask and refuses to start the
  agent when it is missing or invalid.

## Decide whose onboarding answers populate call-context `details` (done)

Resolved: only the target's own answers (the user starting the call), and only
`interests`, under the key `Their interests`. The persona's answers are never
read. See "Trust boundary and integration decisions" in README.md, and
`onboarding_details` in `service.py`.

Why only `interests`:

- **The onboarding questions are not decided yet.** The form asks only for an
  age range and optional interests, with a note to add more questions "once
  the final set has been decided", and it does not save its answers to the
  backend yet. The backend stores the answers as free-form JSON, with no fixed
  keys (the schema says "The question set is TBD"). So there is no agreed list
  of answers to choose from, and no way to know what a future question will
  ask. Passing every answer on would send any question added later to the
  caller without anyone deciding it should, so only answers that were
  deliberately chosen are used, and unknown keys are ignored.
- **`interests` is the one current answer that helps the call.** A caller
  posing as a friend can bring up the target's interests to sound familiar,
  which is a real scam tactic the training should show.
- **`age_range` is left out.** It adds nothing to the conversation, and it
  could tell the caller that the target is under 18.
- **Only the target's answers.** The form tells users their answers are used
  "only to personalize your training". A call is the target's training, so
  their own answers fit that promise; the persona's would be their data used
  in someone else's training.

Revisit this when the question set is decided: for each new question, decide
whether the caller should know it, and if so add it to `onboarding_details`
with a key written from the caller's side (like `Their interests`) and a test.

The call context accepts optional personal facts in `details`. The mapping from
`users.onboarding_answers` is unresolved: the call has both a target (`user_id`)
and a persona (`persona_user_id`). Selecting the wrong person's answers could
give the agent inappropriate personal facts.

Acceptance criteria:

- Agree with the profile and call integration owners whose answers may be used
  and which facts may be passed to the agent.
- Document and implement the mapping after the call authorization checks.
- Test with distinct target/persona answers to verify that only the intended
  facts are included, and cover absent or removed onboarding answers.

AI-assisted documentation (Codex and Claude Code), cited per the CSC398 AI policy.
