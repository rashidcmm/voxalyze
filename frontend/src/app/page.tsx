import Link from "next/link";

export default function Home() {
  return (
    <main className="flex flex-1 flex-col items-center justify-center gap-6 p-6 text-center">
      <h1 className="text-3xl font-semibold tracking-tight">
        Speech Analysis Engine
      </h1>
      <p className="max-w-md text-gray-500">
        Solo GD/debate practice — record, get scored on pronunciation,
        vocabulary, structure and topic relevance, and track improvement
        over sessions.
      </p>
      <div className="flex gap-3">
        <Link
          href="/signup"
          className="rounded-md bg-black px-4 py-2 text-sm font-medium text-white dark:bg-white dark:text-black"
        >
          Get started
        </Link>
        <Link
          href="/login"
          className="rounded-md border border-black/15 px-4 py-2 text-sm font-medium dark:border-white/15"
        >
          Log in
        </Link>
      </div>
    </main>
  );
}
