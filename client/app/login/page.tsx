import LoginPage from "@/components/LoginPage";
import { Suspense } from "react";

export default function home() {
  return (
    <Suspense fallback={null}>
      <LoginPage />
    </Suspense>
  );
}
