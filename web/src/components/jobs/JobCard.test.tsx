import { screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { Job } from "../../api/queries";
import { mockApi, renderWithQuery } from "../../test/utils";
import { JobCard } from "./JobCard";

afterEach(() => vi.unstubAllGlobals());

const job: Job = {
  id: 7, title: "Senior Backend Engineer", company: "Acme", location: "Pune", work_mode: "hybrid",
  experience_min: 5, experience_max: 8, salary_min: 2_500_000, salary_max: 3_500_000,
  salary_currency: "INR", posted_at: new Date(Date.now() - 3 * 86_400_000).toISOString(),
  portals: ["naukri", "linkedin"],
  source_urls: ["https://naukri.example/1", "https://linkedin.example/1"], skills: [],
  match_score: 88, match_reason: "7 yrs fits 5-8; Pune is a preferred city",
  red_flags: ["high:upfront_fee: asks for a fee or deposit", "soft:personal_email: recruiter uses a personal email address"],
  status: "found", description: "",
};

describe("JobCard", () => {
  it("shows score, facts, sources and flags by severity", () => {
    mockApi({});
    const { container } = renderWithQuery(<JobCard job={job} rank={1} />);
    expect(screen.getByLabelText("Match 88 out of 100")).toBeInTheDocument();
    expect(screen.getByText(/Pune · hybrid · 5–8 yrs · ₹25 L–35 L · 3d ago/)).toBeInTheDocument();
    expect(screen.getByText("asks for a fee or deposit").closest(".badge")).toHaveClass("badge-stop");
    expect(screen.getByText("recruiter uses a personal email address").closest(".badge")).toHaveClass("badge-amber");
    expect(container.querySelector(".job")).toHaveClass("job-flagged");
    expect(screen.getAllByRole("link")).toHaveLength(2);
    for (const link of screen.getAllByRole("link")) expect(link).toHaveAttribute("rel", "noopener noreferrer");
  });
});
