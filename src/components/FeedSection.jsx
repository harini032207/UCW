"use client";
import FeedSection from "@/components/FeedSection";

export default function Home() {
  return (
    <main className="min-h-screen bg-slate-950 text-slate-100 py-8">
      {/* Container */}
      <div className="container mx-auto px-4">
        <h1 className="text-2xl font-bold text-center text-indigo-400 mb-6">
          Nexus Feed & Social Space 🚀
        </h1>

        {/* Post Creation & Feed Display */}
        <FeedSection />
      </div>
    </main>
  );
}