import {
  Briefcase,
  CalendarDays,
  Languages,
  type LucideIcon,
  MonitorSmartphone,
  Tag,
  Users,
} from "lucide-react";

/*
 * Onboarding questions and the helpers that turn the form into API request bodies.
 *
 * To add a question, add an entry to ONBOARDING_QUESTIONS. The form renders it
 * and its answer is saved automatically:
 * - Answers are stored in `users.onboarding_answers` under the question's `id`.
 * - A question with `profileField` is saved to that `users` column instead
 *   (through PATCH /api/v1/users/me), not into the answers JSON.
 */

type Option = { value: string; label: string };

type BaseQuestion = {
  id: string;
  label: string;
  icon: LucideIcon;
  required: boolean;
  /** Saves the answer to this `users` column instead of `onboarding_answers`. */
  profileField?: "preferred_language";
  /** Spans the full width of the form. */
  wide?: boolean;
};

export type OnboardingQuestion =
  | (BaseQuestion & { kind: "select"; placeholder: string; options: Option[] })
  // Comma-separated text, stored as an array of strings.
  | (BaseQuestion & { kind: "list"; placeholder: string; maxLength: number })
  // Checkboxes, stored as an array of the selected values.
  | (BaseQuestion & { kind: "multiselect"; options: Option[] });

export const ONBOARDING_QUESTIONS: OnboardingQuestion[] = [
  {
    id: "age_range",
    kind: "select",
    label: "Age range",
    icon: CalendarDays,
    required: true,
    placeholder: "Select your age range",
    options: [
      { value: "under-18", label: "Under 18" },
      { value: "18-24", label: "18–24" },
      { value: "25-34", label: "25–34" },
      { value: "35-49", label: "35–49" },
      { value: "50-64", label: "50–64" },
      { value: "65-plus", label: "65+" },
    ],
  },
  {
    id: "tech_literacy",
    kind: "select",
    label: "Comfort with technology",
    icon: MonitorSmartphone,
    required: true,
    placeholder: "How comfortable are you online?",
    options: [
      { value: "beginner", label: "Beginner — I use a few apps and need help sometimes" },
      { value: "comfortable", label: "Comfortable — I use email, apps and social media daily" },
      { value: "advanced", label: "Advanced — I'm very confident with technology" },
    ],
  },
  {
    id: "preferred_language",
    kind: "select",
    label: "Preferred language",
    icon: Languages,
    required: true,
    profileField: "preferred_language",
    placeholder: "Select a language",
    // Values are language tags (e.g. `en`, `fr-CA`), as the API requires.
    options: [
      { value: "en", label: "English" },
      { value: "fr", label: "Français" },
      { value: "es", label: "Español" },
      { value: "pt", label: "Português" },
      { value: "zh", label: "中文" },
      { value: "ar", label: "العربية" },
      { value: "hi", label: "हिन्दी" },
      { value: "pa", label: "ਪੰਜਾਬੀ" },
      { value: "ur", label: "اردو" },
      { value: "tl", label: "Tagalog" },
    ],
  },
  {
    id: "industry",
    kind: "select",
    label: "Industry",
    icon: Briefcase,
    required: false,
    placeholder: "Select your industry",
    options: [
      { value: "arts", label: "Arts & media" },
      { value: "technology", label: "Technology" },
      { value: "engineering", label: "Engineering" },
      { value: "science", label: "Science & research" },
      { value: "healthcare", label: "Healthcare" },
      { value: "education", label: "Education" },
      { value: "finance", label: "Finance & business" },
      { value: "trades", label: "Trades & services" },
      { value: "student", label: "Student" },
      { value: "retired", label: "Retired" },
      { value: "other", label: "Other" },
    ],
  },
  {
    id: "interests",
    kind: "list",
    label: "Interests",
    icon: Tag,
    required: false,
    wide: true,
    placeholder: "e.g. technology, travel, shopping",
    maxLength: 300,
  },
  {
    id: "family_members",
    kind: "multiselect",
    label: "Family members",
    icon: Users,
    required: false,
    wide: true,
    options: [
      { value: "parent", label: "Parent" },
      { value: "grandparent", label: "Grandparent" },
      { value: "partner", label: "Partner / spouse" },
      { value: "sibling", label: "Sibling" },
      { value: "child", label: "Child" },
      { value: "grandchild", label: "Grandchild" },
      { value: "niece_nephew", label: "Niece / nephew" },
      { value: "cousin", label: "Cousin" },
      { value: "pet", label: "Pet" },
    ],
  },
];

export type OnboardingAnswers = Record<string, string | string[]>;
export type ProfileFields = Partial<Record<NonNullable<BaseQuestion["profileField"]>, string>>;

function readAnswer(question: OnboardingQuestion, formData: FormData): string | string[] | null {
  switch (question.kind) {
    case "select": {
      const value = String(formData.get(question.id) ?? "").trim();
      return value || null;
    }
    case "list": {
      const items = String(formData.get(question.id) ?? "")
        .split(",")
        .map((item) => item.trim())
        .filter(Boolean);
      return items.length ? items : null;
    }
    case "multiselect": {
      const values = formData.getAll(question.id).map(String);
      return values.length ? values : null;
    }
  }
}

/**
 * The answers to store in `users.onboarding_answers`.
 * Unanswered optional questions are left out rather than stored as "" or [].
 * Returns `{}` (never null) when nothing was answered: the API requires an object.
 */
export function buildOnboardingAnswers(formData: FormData): OnboardingAnswers {
  const answers: OnboardingAnswers = {};
  for (const question of ONBOARDING_QUESTIONS) {
    if (question.profileField) continue;
    const answer = readAnswer(question, formData);
    if (answer !== null) answers[question.id] = answer;
  }
  return answers;
}

/** The answers that are saved to `users` columns rather than the answers JSON. */
export function buildProfileFields(formData: FormData): ProfileFields {
  const fields: ProfileFields = {};
  for (const question of ONBOARDING_QUESTIONS) {
    if (!question.profileField) continue;
    const answer = readAnswer(question, formData);
    if (typeof answer === "string") fields[question.profileField] = answer;
  }
  return fields;
}

// The API requires usernames to match ^[a-z0-9_]{3,30}$.
const USERNAME_MAX_LENGTH = 30;
const USERNAME_FALLBACK = "user";

/**
 * Builds a username from the user's name: lowercase letters only, with `_`
 * where spaces were. e.g. "  Zoë O'Brien " -> "zoe_obrien".
 */
export function usernameFromName(firstName: string, lastName: string): string {
  const username = `${firstName} ${lastName}`
    .normalize("NFD") // split accented letters so "ë" becomes "e" + accent
    .toLowerCase()
    .trim()
    .replace(/\s+/g, "_")
    .replace(/[^a-z_]/g, "")
    .replace(/_+/g, "_")
    .replace(/^_|_$/g, "")
    .slice(0, USERNAME_MAX_LENGTH)
    .replace(/_$/, "");

  return username.length >= 3 ? username : USERNAME_FALLBACK;
}

/** Adds a random suffix to a taken username, e.g. "alex_morgan" -> "alex_morgan_4821". */
export function withRandomSuffix(username: string): string {
  const suffix = String(Math.floor(1000 + Math.random() * 9000));
  return `${username.slice(0, USERNAME_MAX_LENGTH - suffix.length - 1)}_${suffix}`;
}
