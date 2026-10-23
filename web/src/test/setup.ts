import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";
import { undoStore } from "../lib/undo";

// Undo windows are tested on their own; elsewhere answers go out at once.
undoStore.windowMs = 0;

afterEach(() => {
  cleanup();
  localStorage.clear();
});
