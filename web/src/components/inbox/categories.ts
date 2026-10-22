type Tone = "neutral" | "amber" | "go" | "sky" | "stop";

export const CATEGORY_LABEL: Record<string, string> = {
  interview_invite: "Interview",
  recruiter_outreach: "Recruiter",
  assessment: "Assessment",
  rejection: "Rejection",
  offer: "Offer",
  document_request: "Documents",
  application_confirmation: "Confirmation",
  other: "Other",
};

export const CATEGORY_TONE: Record<string, Tone> = {
  interview_invite: "amber",
  recruiter_outreach: "sky",
  assessment: "amber",
  rejection: "stop",
  offer: "go",
  document_request: "amber",
  application_confirmation: "neutral",
  other: "neutral",
};

export const ACTION_LABEL: Record<string, string> = {
  needs_action: "Needs you",
  drafting: "Reply in progress",
  replied: "Replied",
  ignored: "Ignored",
  done: "Done",
  info: "For your information",
};
