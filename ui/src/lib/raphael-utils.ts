export interface ParsedProjectResult {
  name: string;
  description?: string | undefined;
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
