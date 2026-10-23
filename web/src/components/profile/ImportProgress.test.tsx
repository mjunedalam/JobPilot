import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ImportJob } from "../../api/queries";
import { ImportProgress } from "./ImportProgress";

const stages = (active: string): ImportJob["stages"] =>
  ["upload", "read", "model", "save"].map((key, i, all) => ({
    key, label: key, detail: key === active ? "qwen3:8b is writing what it found" : "",
    status: all.indexOf(active) > i ? "done" : key === active ? "active" : "pending",
    started_at: null, finished_at: null,
  }));

const base: ImportJob = {
  id: "j", filename: "cv.pdf", size_bytes: 11_000, status: "running", elapsed_s: 24,
  stages: stages("model"), model: "qwen3:8b", provider: "ollama", streaming: true, words: 1408,
  tokens: 306, tokens_per_s: 23, percent: 24.4, eta_s: 41, attempt: 1,
  found: { fields: { full_name: "Mohammad Juned Alam", current_role: "Senior AI Software Engineer" },
           skills: ["Python", "LangGraph"], education: [] },
  result: null, error: "",
};

describe("ImportProgress", () => {
  it("shows live progress and the details found so far", () => {
    render(<ImportProgress job={base} onAgain={vi.fn()} />);
    expect(screen.getByRole("progressbar")).toHaveAttribute("aria-valuenow", "24");
    expect(screen.getByText("23 tok/s")).toBeInTheDocument();
    expect(screen.getByText("about 41s left")).toBeInTheDocument();
    expect(screen.getByText("Mohammad Juned Alam")).toBeInTheDocument();
    expect(screen.getAllByLabelText("not found yet").length).toBe(4); // company, location, email, phone
    expect(screen.getByRole("list", { name: "Skills found" })).toHaveTextContent("PythonLangGraph");
    expect(screen.getByText("live")).toBeInTheDocument();
  });

  it("waits indeterminately until the model starts writing", () => {
    render(<ImportProgress job={{ ...base, streaming: false, percent: null, tokens: 0 }} onAgain={vi.fn()} />);
    expect(screen.getByRole("progressbar")).not.toHaveAttribute("aria-valuenow");
    expect(screen.getByText(/reading your CV before it starts writing/)).toBeInTheDocument();
  });

  it("summarises the result and explains failures", () => {
    const done = { ...base, status: "done", stages: stages("none"), percent: 100,
      result: { version: 2, resume_label: "cv", missing: [], usage: {}, updated: ["email", "skills"], kept: ["full_name"] } };
    const { unmount } = render(<ImportProgress job={done} onAgain={vi.fn()} />);
    expect(screen.getByRole("status")).toHaveTextContent("Saved as profile version 2");
    expect(screen.getByRole("status")).toHaveTextContent("Kept your existing full name");
    unmount();
    render(<ImportProgress job={{ ...base, status: "failed", error: "the file has no text" }} onAgain={vi.fn()} />);
    expect(screen.getByRole("alert")).toHaveTextContent("the file has no text");
  });
});
