"use client";

import { useRouter } from "next/navigation";
import { createDocument } from "@/lib/api";
import VideoUploadForm from "@/components/VideoUploadForm";
import { useUnauthorizedDialog } from "@/components/UnauthorizedDialogContext";
// import { getCurrentUser } from "@/lib/auth";

export default function CreateDocumentPage() {
  const router = useRouter();
  const { showUnauthorizedDialog } = useUnauthorizedDialog();

  async function handleSubmit(file: File, title: string) {
    // const user = await getCurrentUser();
    // if (!user) {
    //   showUnauthorizedDialog();
    //   return;
    // }
    const result = await createDocument(file, title, showUnauthorizedDialog);
    router.push(`/document/${result.documentId}`);
  }

  return (
    <main className="h-full flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-xl">
        <h1 className="text-xl font-semibold text-neutral-900 mb-1">
          New document
        </h1>
        <p className="text-sm text-neutral-500 mb-8">
          Upload a rough, unedited screen recording to get started.
        </p>
        <VideoUploadForm
          submitLabel="Generate document"
          onSubmit={handleSubmit}
          titleField
        />
      </div>
    </main>
  );
}
