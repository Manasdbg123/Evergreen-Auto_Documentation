import StarterKit from "@tiptap/starter-kit";
import Image from "@tiptap/extension-image";
import Placeholder from "@tiptap/extension-placeholder";

export const documentExtensions = [
  StarterKit.configure({
    heading: { levels: [1] }, // only one heading level — it's the title
    bulletList: false,
    orderedList: false,
    blockquote: false,
    codeBlock: false,
    code: false,
    strike: false,
    horizontalRule: false,
  }),
  Image.configure({
    inline: false,
    HTMLAttributes: { class: "rounded-md w-full" },
  }),
  Placeholder.configure({
    placeholder: ({ node }) => {
      if (node.type.name === "heading") return "Untitled";
      return "Write something, or drop an image below...";
    },
  }),
];
