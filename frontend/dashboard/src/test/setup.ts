import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

// Vitest runs without `globals`, so Testing Library never installs its own auto-cleanup
// hook: without this, each render leaks into the next test's DOM and queries match
// elements from a previous case.
afterEach(cleanup);
