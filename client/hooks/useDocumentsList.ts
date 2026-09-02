"use client";

import { useEffect, useState, useCallback } from "react";
import { listDocuments } from "@/lib/api";
import type { DocumentSummary } from "@/lib/types";

const POLL_INTERVAL_MS = 12000;

export function useDocumentsList() {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const refresh = useCallback(async () => {
    try {
      const docs = await listDocuments();
      setDocuments(docs);
    } catch {
      // sidebar failing silently is fine — main content area will surface real errors
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
    // always poll, regardless of route — this is what makes a document
    // generating in the background show up in the sidebar automatically
    const interval = setInterval(refresh, POLL_INTERVAL_MS);
    return () => clearInterval(interval);
  }, [refresh]);

  return { documents, isLoading, refresh };
}
