import { SearchX } from "lucide-react";
import Link from "next/link";

export default function NotFound() {
  return (
    <div className="mx-auto mt-16 max-w-md text-center">
      <SearchX className="mx-auto size-10 text-muted" aria-hidden />
      <h1 className="mt-3 text-lg font-semibold">Not found</h1>
      <p className="mt-1 text-sm text-muted">It doesn&apos;t exist, or it belongs to another organization.</p>
      <Link href="/inbox" className="mt-4 inline-block rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-on-accent">
        Back to the inbox
      </Link>
    </div>
  );
}
