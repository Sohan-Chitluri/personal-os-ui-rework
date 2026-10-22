import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { MarkdownContent } from "@/components/markdown-content";

describe("MarkdownContent", () => {
  it("renders headings, bold, italic, and lists without raw markdown markers", () => {
    const markdown = `
### Compute & Infrastructure (Week 1)
Here is **bold emphasis** and *italic note*.

- Task 1: Initialize database
- Task 2: Configure Redis
1. Ordered step one
2. Ordered step two
`;

    const { container } = render(<MarkdownContent content={markdown} />);

    // Headings
    const heading = screen.getByRole("heading", { level: 3 });
    expect(heading).toHaveTextContent("Compute & Infrastructure (Week 1)");
    expect(heading).toHaveClass("font-display");

    // Bold text
    const boldEl = screen.getByText("bold emphasis");
    expect(boldEl.tagName).toBe("STRONG");
    expect(boldEl).toHaveClass("font-bold");

    // Italic text
    const italicEl = screen.getByText("italic note");
    expect(italicEl.tagName).toBe("EM");

    // Lists
    const listItems = container.querySelectorAll("li");
    expect(listItems.length).toBe(4);
    expect(screen.getByText(/Initialize database/)).toBeInTheDocument();
    expect(screen.getByText(/Ordered step one/)).toBeInTheDocument();
  });

  it("renders inline code and fenced code blocks with horizontal overflow protection", () => {
    const markdown = `
Run \`uvicorn personal_os.api.main:app\` to start the server.

\`\`\`bash
git checkout -b feature/session-memory
npm install
\`\`\`
`;

    const { container } = render(<MarkdownContent content={markdown} />);

    // Inline code
    const inlineCode = screen.getByText("uvicorn personal_os.api.main:app");
    expect(inlineCode.tagName).toBe("CODE");
    expect(inlineCode).toHaveClass("font-mono");
    expect(inlineCode.parentElement?.tagName).not.toBe("PRE");

    // Fenced code block
    const preElement = container.querySelector("pre");
    expect(preElement).toBeInTheDocument();
    expect(preElement).toHaveClass("overflow-x-auto");
    expect(preElement).toHaveClass("max-w-full");
    expect(preElement).toHaveTextContent(/git checkout -b feature\/session-memory/);
  });

  it("renders tables, blockquotes, and horizontal rules", () => {
    const markdown = `
> Architectural Decision: Keep SQLite local and PostgreSQL cloud.

---

| Stage | Latency | Status |
| :--- | :--- | :--- |
| Pipeline 1 | 10ns | Verified |
| Pipeline 2 | 12ns | Pending |
`;

    const { container } = render(<MarkdownContent content={markdown} />);

    // Blockquote
    const quote = container.querySelector("blockquote");
    expect(quote).toBeInTheDocument();
    expect(quote).toHaveTextContent(/Architectural Decision/);

    // Horizontal rule
    const hr = container.querySelector("hr");
    expect(hr).toBeInTheDocument();

    // Table
    const table = container.querySelector("table");
    expect(table).toBeInTheDocument();
    const headers = container.querySelectorAll("th");
    expect(headers.length).toBe(3);
    expect(screen.getByText("Stage")).toBeInTheDocument();
    expect(screen.getByText("Latency")).toBeInTheDocument();
    expect(screen.getByText("Verified")).toBeInTheDocument();
  });

  it("safely opens valid external links and prevents unsafe protocols", () => {
    const markdown = `
[Safe Docs](https://docs.github.com/en)
[Unsafe Javascript](javascript:alert('xss'))
`;

    render(<MarkdownContent content={markdown} />);

    const safeLink = screen.getByRole("link", { name: "Safe Docs" });
    expect(safeLink).toHaveAttribute("href", "https://docs.github.com/en");
    expect(safeLink).toHaveAttribute("target", "_blank");
    expect(safeLink).toHaveAttribute("rel", "noopener noreferrer");

    // Unsafe link should not be rendered as an anchor
    expect(screen.queryByRole("link", { name: "Unsafe Javascript" })).toBeNull();
    expect(screen.getByText("Unsafe Javascript")).toBeInTheDocument();
  });

  it("strips internal Hermes reasoning tags defensively", () => {
    const withReasoning = `
<think>
Internal chain of thought reasoning should never appear
</think>
Here is the visible response to the user.
`;

    render(<MarkdownContent content={withReasoning} />);

    expect(screen.queryByText(/Internal chain of thought/)).toBeNull();
    expect(screen.getByText("Here is the visible response to the user.")).toBeInTheDocument();
  });
});
