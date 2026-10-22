import { describe, expect, it } from "vitest";
import {
  cleanProjectDescription,
  getProjectCategory,
} from "@/lib/project-categories";

describe("Project Categories & Dual-Window Partitioning", () => {
  it("detects explicit [College] tag in project description", () => {
    expect(
      getProjectCategory({ name: "Project Alpha", description: "[College] Final capstone" })
    ).toBe("college");
  });

  it("detects explicit [Personal] tag in project description", () => {
    expect(
      getProjectCategory({ name: "Project Beta", description: "[Personal] Fitness tracker" })
    ).toBe("personal");
  });

  it("detects college keywords in project name", () => {
    expect(getProjectCategory({ name: "cordic", description: null })).toBe("college");
    expect(getProjectCategory({ name: "ASIC Design", description: null })).toBe("college");
    expect(getProjectCategory({ name: "ECE 340 Homework", description: null })).toBe("college");
    expect(getProjectCategory({ name: "Physics Lab 2", description: null })).toBe("college");
    expect(getProjectCategory({ name: "Semester Review", description: null })).toBe("college");
  });

  it("defaults to personal for general project names without college keywords", () => {
    expect(
      getProjectCategory({ name: "Local Dev Verification Project", description: null })
    ).toBe("personal");
    expect(getProjectCategory({ name: "Blockday launch", description: null })).toBe("personal");
    expect(getProjectCategory({ name: "Studio", description: null })).toBe("personal");
  });

  it("cleans [College] and [Personal] tags from displayed description", () => {
    expect(cleanProjectDescription("[College] Final capstone")).toBe("Final capstone");
    expect(cleanProjectDescription("[Personal] Fitness tracker")).toBe("Fitness tracker");
    expect(cleanProjectDescription("[College]")).toBeNull();
    expect(cleanProjectDescription(null)).toBeNull();
    expect(cleanProjectDescription(undefined)).toBeNull();
    expect(cleanProjectDescription("Regular notes without tags")).toBe("Regular notes without tags");
  });
});
