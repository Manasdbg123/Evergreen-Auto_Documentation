// src/routes/ai.ts
import { Router, type Request, type Response } from "express";
import Anthropic from "@anthropic-ai/sdk";

const anthropic = new Anthropic({ apiKey: process.env.ANTHROPIC_API_KEY });
const router = Router();

const ACTION_PROMPTS: Record<string, string> = {
  rewrite:
    "Rewrite the following text to be clearer and more concise, preserving its meaning:",
  expand: "Expand the following text with more detail and helpful context:",
  simplify:
    "Simplify the following text so it is easy to follow for a non-technical reader:",
};

router.post("/rewrite", async (req: Request, res: Response) => {
  const { text, action } = req.body as { text?: string; action?: string };

  if (!text || !action || !ACTION_PROMPTS[action]) {
    res.status(400).json({
      error: "text and a valid action (rewrite|expand|simplify) are required",
    });
    return;
  }

  try {
    const response = await anthropic.messages.create({
      model: "claude-sonnet-4-6",
      max_tokens: 500,
      messages: [
        { role: "user", content: `${ACTION_PROMPTS[action]}\n\n${text}` },
      ],
    });

    const textBlock = response.content.find((b) => b.type === "text");
    if (!textBlock || textBlock.type !== "text") {
      throw new Error("No text block in Claude response");
    }

    res.json({ newText: textBlock.text.trim() });
  } catch (err) {
    console.error("AI rewrite failed:", err);
    res.status(500).json({ error: "failed to generate rewrite" });
  }
});

export default router;
