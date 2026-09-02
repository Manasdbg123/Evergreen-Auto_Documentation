import type { JSONContent } from "@tiptap/core";

export function jsonToMarkdown(doc: JSONContent): string {
  if (!doc.content) return "";
  return doc.content.map(nodeToMarkdown).join("\n\n").trim() + "\n";
}

function nodeToMarkdown(node: JSONContent): string {
  switch (node.type) {
    case "heading": {
      const level = node.attrs?.level ?? 1;
      return `${"#".repeat(level)} ${inlineContentToMarkdown(node.content)}`;
    }

    case "paragraph":
      return inlineContentToMarkdown(node.content);

    case "image": {
      const src = node.attrs?.src ?? "";
      const alt = node.attrs?.alt ?? "";
      return `![${alt}](${src})`;
    }

    default:
      return "";
  }
}

function inlineContentToMarkdown(content: JSONContent[] | undefined): string {
  if (!content) return "";

  return content
    .map((node) => {
      if (node.type !== "text") return "";
      let text = node.text ?? "";

      const marks = node.marks ?? [];
      const isBold = marks.some((m) => m.type === "bold");
      const isItalic = marks.some((m) => m.type === "italic");

      if (isBold && isItalic) text = `***${text}***`;
      else if (isBold) text = `**${text}**`;
      else if (isItalic) text = `*${text}*`;

      return text;
    })
    .join("");
}
