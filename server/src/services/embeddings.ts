const VOYAGE_API_URL = "https://api.voyageai.com/v1/embeddings";

interface VoyageEmbeddingResponse {
  data: { embedding: number[]; index: number }[];
}

// Batch-embeds step text (title + body) so we can compare steps across versions
export async function embedTexts(texts: string[]): Promise<number[][]> {
  const res = await fetch(VOYAGE_API_URL, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.VOYAGE_API_KEY}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      input: texts,
      model: "voyage-3",
      input_type: "document",
    }),
  });

  if (!res.ok) {
    throw new Error(
      `Voyage embeddings request failed: ${res.status} ${await res.text()}`,
    );
  }

  const json = (await res.json()) as VoyageEmbeddingResponse;
  // preserve input order regardless of response ordering
  return json.data.sort((a, b) => a.index - b.index).map((d) => d.embedding);
}

export function cosineSimilarity(a: number[], b: number[]): number {
  let dot = 0,
    normA = 0,
    normB = 0;
  for (let i = 0; i < a.length; i++) {
    dot += a[i] * b[i];
    normA += a[i] * a[i];
    normB += b[i] * b[i];
  }
  return dot / (Math.sqrt(normA) * Math.sqrt(normB));
}
