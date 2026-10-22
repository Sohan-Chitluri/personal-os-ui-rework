export type ProjectCategory = "college" | "personal";

export const COLLEGE_KEYWORDS = [
  "cordic",
  "asic",
  "college",
  "course",
  "homework",
  "assignment",
  "exam",
  "class",
  "lecture",
  "university",
  "semester",
  "lab",
  "ece",
  "cs",
  "math",
  "physics",
  "study",
  "thesis",
];

export function getProjectCategory(project: {
  name: string;
  description?: string | null;
}): ProjectCategory {
  const desc = (project.description || "").toLowerCase();
  if (desc.includes("[college]")) return "college";
  if (desc.includes("[personal]")) return "personal";

  const combined = `${project.name} ${project.description || ""}`.toLowerCase();
  for (const keyword of COLLEGE_KEYWORDS) {
    if (combined.includes(keyword)) {
      return "college";
    }
  }

  return "personal";
}

export function cleanProjectDescription(desc: string | null | undefined): string | null {
  if (!desc) return null;
  const cleaned = desc.replace(/\[(college|personal)\]/gi, "").trim();
  return cleaned || null;
}
