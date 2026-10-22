import { describe, expect, it } from "vitest";
import { cleanMessageContent, parseProjectResult } from "@/lib/raphael-utils";

describe("raphael-utils", () => {
  describe("cleanMessageContent", () => {
    it("strips Hermes Unicode reasoning boxes", () => {
      const raw =
        "┌─ Reasoning ──────────────────────────────────────────────────────────────────┐\n" +
        "│ The user wants to add a project named 'cordic' to their project list.         │\n" +
        "│ We can output an action block.                                                │\n" +
        "└──────────────────────────────────────────────────────────────────────────────┘\n" +
        "I have created the project **CORDIC**.\n\nAdding the CORDIC project to your list now...";

      const cleaned = cleanMessageContent(raw);
      expect(cleaned).not.toContain("┌─ Reasoning");
      expect(cleaned).not.toContain("The user wants to add a project");
      expect(cleaned).toContain("I have created the project **CORDIC**.");
      expect(cleaned).toContain("Adding the CORDIC project to your list now...");
    });

    it("strips Hermes ASCII reasoning boxes", () => {
      const raw =
        "+-- Reasoning ----------------------------------+\n" +
        "| Internal planning                              |\n" +
        "+-----------------------------------------------+\n" +
        "Project created successfully.";

      const cleaned = cleanMessageContent(raw);
      expect(cleaned).not.toContain("+-- Reasoning");
      expect(cleaned).not.toContain("Internal planning");
      expect(cleaned).toBe("Project created successfully.");
    });

    it("strips <think> and <reasoning> tags", () => {
      const raw =
        "<think>\nEvaluating user intention.\n</think>\n" +
        "<reasoning>\nSecond pass check.\n</reasoning>\n" +
        "Hello from Raphael!";

      const cleaned = cleanMessageContent(raw);
      expect(cleaned).not.toContain("<think>");
      expect(cleaned).not.toContain("<reasoning>");
      expect(cleaned).not.toContain("Evaluating user intention");
      expect(cleaned).toBe("Hello from Raphael!");
    });

    it("strips unclosed/truncated <think> tags safely", () => {
      const raw = "<think>\nThinking that was cut off";
      const cleaned = cleanMessageContent(raw);
      expect(cleaned).toBe("");
    });

    it("strips unclosed/truncated Hermes reasoning box safely", () => {
      const raw = "┌─ Reasoning ───────────────────┐\nPlanning that was cut off";
      const cleaned = cleanMessageContent(raw);
      expect(cleaned).toBe("");
    });

    it("preserves messages with no reasoning", () => {
      const raw = "Here are your 3 pending tasks for today.";
      expect(cleanMessageContent(raw)).toBe(raw);
    });
  });

  describe("parseProjectResult", () => {
    it("parses project creation confirmation with bold name", () => {
      const content = "I have created the project **CORDIC**.";
      const parsed = parseProjectResult(content);
      expect(parsed).toEqual({
        name: "CORDIC",
        description: undefined,
      });
    });

    it("parses project creation with description", () => {
      const content =
        "I have created the project **CORDIC**.\n- Description: Coordinate Rotation Digital Computer pipeline";
      const parsed = parseProjectResult(content);
      expect(parsed).toEqual({
        name: "CORDIC",
        description: "Coordinate Rotation Digital Computer pipeline",
      });
    });

    it("returns null when no project creation confirmed", () => {
      const content = "You have 5 projects in your workspace.";
      expect(parseProjectResult(content)).toBeNull();
    });
  });
});
