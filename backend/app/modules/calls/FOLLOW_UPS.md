# Call integration issue drafts

These are local drafts for the two issues requested in PR #7. They have not
been published to GitHub.

## Decide and implement the source of `scenario.ask`

The call context requires an explicit `scenario.ask`, but `scam_scenarios` has
no column for it. The call endpoint cannot assemble a valid context until the
team chooses where that request comes from. The parser must not invent a
default money request.

Acceptance criteria:

- Agree on the source of the ask with the scenario and call integration owners.
- Document the mapping and add any necessary schema, API and seed-data changes.
- Test that call assembly supplies the chosen ask and refuses to start the
  agent when it is missing or invalid.

## Decide whose onboarding answers populate call-context `details`

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

AI-assisted documentation (Codex), cited per the CSC398 AI policy.
