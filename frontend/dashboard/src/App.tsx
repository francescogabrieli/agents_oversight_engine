import { Route, Routes } from "react-router-dom";
import { ReviewInboxPage } from "./routes/ReviewInboxPage";
import { ReviewThreadPage } from "./routes/ReviewThreadPage";

export function App() {
  return (
    <main className="mx-auto flex min-h-screen w-full max-w-7xl flex-col gap-8 px-4 py-6 md:px-8 md:py-10">
      <Routes>
        <Route path="/" element={<ReviewInboxPage />} />
        <Route path="/threads/:threadId" element={<ReviewThreadPage />} />
      </Routes>
    </main>
  );
}
