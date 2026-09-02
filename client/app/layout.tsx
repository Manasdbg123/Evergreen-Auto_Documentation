import type { Metadata } from "next";
import "./globals.css";
import Sidebar from "@/components/Sidebar";
import { Toaster } from "react-hot-toast";
import { UnauthorizedDialogProvider } from "@/components/UnauthorizedDialogContext";
// import LogOutBox from "@/components/LogOut";

export const metadata: Metadata = {
  title: "Clueso Docs",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="flex bg-neutral-50">
        <UnauthorizedDialogProvider>
          <Sidebar />
          {/* <LogOutBox /> */}
          <Toaster position="bottom-center" reverseOrder={false} />

          <div className="flex-1 h-screen overflow-y-auto">{children}</div>
        </UnauthorizedDialogProvider>
      </body>
    </html>
  );
}
