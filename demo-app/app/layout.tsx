import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Lewis & Clark GraphRAG Demo",
  description: "Combining vector search with Neo4j knowledge graph traversal",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
