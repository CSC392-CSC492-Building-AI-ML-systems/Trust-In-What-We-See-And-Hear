// Must match the backend's PRIVACY_POLICY_VERSION (backend/.env.example). The backend records
// its own value on the user's profile; this one is only shown to the user.
export const PRIVACY_POLICY_VERSION = "2026-11";

const STORAGE_KEY = "privacy_policy_accepted_version";

/**
 * Remembers, for this browser tab, that the user accepted the current policy on
 * `/onboarding/privacy`. The onboarding form checks this before it sends
 * `accept_privacy_policy: true`; the backend records the acceptance when the profile is created.
 */
export function markPrivacyPolicyAccepted() {
  try {
    sessionStorage.setItem(STORAGE_KEY, PRIVACY_POLICY_VERSION);
  } catch {
    // Storage blocked (e.g. private mode): the user will be asked to accept again.
  }
}

export function hasAcceptedPrivacyPolicy(): boolean {
  try {
    return sessionStorage.getItem(STORAGE_KEY) === PRIVACY_POLICY_VERSION;
  } catch {
    return false;
  }
}

export function clearPrivacyPolicyAcceptance() {
  try {
    sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // Nothing to clear.
  }
}
