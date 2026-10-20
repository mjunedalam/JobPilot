/** Profile fields editable as free text. The server parses them (e.g. "30 LPA", "60 days",
 *  "Pune, Delhi") with the same rules as the CLI Q&A, so the UI only formats for display. */
export interface EditableField {
  name: string;
  label: string;
  hint?: string;
}

export const EDITABLE_FIELDS: EditableField[] = [
  { name: "full_name", label: "Full name" },
  { name: "email", label: "Email" },
  { name: "phone", label: "Phone" },
  { name: "current_location", label: "Current location" },
  { name: "current_role", label: "Current role" },
  { name: "current_company", label: "Current company" },
  { name: "total_experience_years", label: "Total experience (years)" },
  { name: "notice_period_days", label: "Notice period", hint: "e.g. 60 days" },
  { name: "current_ctc", label: "Current CTC", hint: "e.g. 24 LPA, $120,000" },
  { name: "expected_ctc", label: "Expected CTC", hint: "e.g. 30 LPA" },
  { name: "preferred_locations", label: "Preferred locations", hint: "Comma separated" },
  { name: "work_modes", label: "Work modes", hint: "remote, hybrid, onsite" },
  { name: "target_roles", label: "Target roles", hint: "Comma separated" },
  { name: "excluded_companies", label: "Never apply to", hint: "Comma separated" },
  { name: "work_authorization", label: "Work authorization" },
];

interface Money {
  amount: number;
  currency: string;
}

function isMoney(value: unknown): value is Money {
  return typeof value === "object" && value !== null && "amount" in value && "currency" in value;
}

export function formatValue(value: unknown): string {
  if (value == null) return "";
  if (Array.isArray(value)) return value.join(", ");
  if (isMoney(value)) return `${value.amount} ${value.currency}`;
  return String(value);
}
