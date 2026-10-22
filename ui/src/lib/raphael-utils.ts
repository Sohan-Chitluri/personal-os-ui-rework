export interface ParsedProjectResult {
  name: string;
  description?: string | undefined;
}

export interface ParsedTaskResult {
  id?: string | undefined;
  title: string;
  dueDate?: string | undefined;
  hardDeadline?: string | undefined;
  project?: string | undefined;
  description?: string | undefined;
  note?: string | undefined;
}

export function cleanMessageContent(raw: string): string {
  if (!raw) return "";
  let text = raw;
  text = text.replace(
    /<(?:think|thought|reasoning)>[\s\S]*?(?:<\/(?:think|thought|reasoning)>|$)/gi,
    "",
  );
  text = text.replace(
    /[ \t]*┌[─-]+[ \t]*Reasoning[ \t]*[─-]*[┐\n][\s\S]*?(?:└[─-]+[┘\n]?[ \t]*\n?|$)/gi,
    "",
  );
  text = text.replace(
    /[ \t]*\+[-=]+[ \t]*Reasoning[ \t]*[-=]*\+?[\s\S]*?(?:\+[-=]+\+[ \t]*\n?|$)/gi,
    "",
  );
  return text.trim();
}

export function parseProjectResult(content: string): ParsedProjectResult | null {
  if (!content) return null;
  const match = content.match(/I have created the project \*\*([^*]+)\*\*/i);
  if (!match) return null;
  const name = match[1]!.trim();
  const descMatch = content.match(/-\s*Description:\s*([^\n]+)/i);
  return {
    name,
    description: descMatch ? descMatch[1]!.trim() : undefined,
  };
}

export function parseTaskResult(content: string): ParsedTaskResult | null {
  if (!content) return null;
  const match = content.match(/I have created the task \*\*([^*]+)\*\*(?:\s*\(ID:\s*`([^`]+)`\))?/i);
  if (!match) return null;
  const title = match[1]!.trim();
  const id = match[2]?.trim();

  const dueMatch = content.match(/-\s*Due:\s*([^\n]+)/i);
  const hardMatch = content.match(/-\s*Hard deadline:\s*([^\n]+)/i);
  const projMatch = content.match(/-\s*Project:\s*([^\n]+)/i);
  const descMatch = content.match(/-\s*Description:\s*([^\n]+)/i);
  const noteMatch = content.match(/-\s*Note:\s*([^\n]+)/i);

  const cleanVal = (val?: string) => {
    if (!val) return undefined;
    const trimmed = val.trim();
    if (trimmed.toLowerCase() === "none" || trimmed.toLowerCase() === "null") return undefined;
    return trimmed;
  };

  return {
    id: cleanVal(id),
    title,
    dueDate: cleanVal(dueMatch?.[1]),
    hardDeadline: cleanVal(hardMatch?.[1]),
    project: cleanVal(projMatch?.[1]),
    description: cleanVal(descMatch?.[1]),
    note: cleanVal(noteMatch?.[1]),
  };
}
