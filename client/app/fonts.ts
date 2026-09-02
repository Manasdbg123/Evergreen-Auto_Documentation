import { Bricolage_Grotesque } from "next/font/google";

export const bricolageGrotesque = Bricolage_Grotesque({
  subsets: ["latin"],
  display: "swap", // Prevents layout shift
  variable: "--font-bricolage", // Optional: for Tailwind CSS
});
