/*
  Warnings:

  - A unique constraint covering the columns `[documentId,versionNumber]` on the table `DocVersion` will be added. If there are existing duplicate values, this will fail.

*/
-- AlterTable
ALTER TABLE "Step" ADD COLUMN     "embedding" JSONB;

-- CreateTable
CREATE TABLE "Diff" (
    "id" TEXT NOT NULL,
    "oldVersionId" TEXT NOT NULL,
    "newVersionId" TEXT NOT NULL,
    "stepDiffs" JSONB NOT NULL,
    "createdAt" TIMESTAMP(3) NOT NULL DEFAULT CURRENT_TIMESTAMP,

    CONSTRAINT "Diff_pkey" PRIMARY KEY ("id")
);

-- CreateIndex
CREATE UNIQUE INDEX "Diff_oldVersionId_newVersionId_key" ON "Diff"("oldVersionId", "newVersionId");

-- CreateIndex
CREATE UNIQUE INDEX "DocVersion_documentId_versionNumber_key" ON "DocVersion"("documentId", "versionNumber");

-- AddForeignKey
ALTER TABLE "Diff" ADD CONSTRAINT "Diff_oldVersionId_fkey" FOREIGN KEY ("oldVersionId") REFERENCES "DocVersion"("id") ON DELETE RESTRICT ON UPDATE CASCADE;

-- AddForeignKey
ALTER TABLE "Diff" ADD CONSTRAINT "Diff_newVersionId_fkey" FOREIGN KEY ("newVersionId") REFERENCES "DocVersion"("id") ON DELETE RESTRICT ON UPDATE CASCADE;
