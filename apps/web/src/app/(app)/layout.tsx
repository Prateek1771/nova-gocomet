import { redirect } from "next/navigation";

import { ApiError, getMe } from "@/lib/api";
import { currentSession } from "@/lib/session";

// nav lands milestone by milestone; items render disabled until their screen exists
const NAV = [
  { label: "Home", href: "/", m: null },
  { label: "Inbox", href: "/inbox", m: "M1" },
  { label: "Runs", href: "/runs", m: "M1" },
  { label: "Studio", href: "/studio", m: "M3" },
  { label: "Documents", href: "/documents", m: "M2" },
  { label: "Exceptions", href: "/exceptions", m: "M6" },
  { label: "Admin", href: "/admin", m: "M4" },
];

export default async function AppLayout({ children }: LayoutProps<"/">) {
  if (!(await currentSession())) redirect("/api/auth/login");

  let me;
  try {
    me = await getMe();
  } catch (e) {
    if (e instanceof ApiError && e.status === 401) redirect("/api/auth/login");
    return <AccessProblem message={e instanceof ApiError ? e.message : "Nova API is unreachable"} />;
  }

  const initials = (me.name ?? me.email ?? "?")
    .split(/\s+/)
    .map((w) => w[0])
    .join("")
    .slice(0, 2)
    .toUpperCase();

  return (
    <div className="flex min-h-screen bg-canvas text-ink">
      <aside className="hidden w-60 shrink-0 flex-col border-r border-line bg-panel px-3 py-5 md:flex">
        <div className="flex items-center gap-2 px-2">
          <span className="grid size-8 place-items-center rounded-lg bg-linear-to-br from-indigo-500 to-cyan-400 text-sm font-bold text-white">
            N
          </span>
          <span className="text-lg font-semibold tracking-tight">Nova</span>
        </div>
        <nav className="mt-8 flex flex-col gap-0.5">
          {NAV.map((n) =>
            n.m ? (
              <span key={n.href} className="flex items-center justify-between rounded-md px-2 py-1.5 text-sm text-muted" title={`Arrives in ${n.m}`}>
                {n.label}
                <span className="rounded border border-line px-1.5 text-[10px] font-medium">{n.m}</span>
              </span>
            ) : (
              <a key={n.href} href={n.href} className="rounded-md bg-accent-soft px-2 py-1.5 text-sm font-medium text-accent">
                {n.label}
              </a>
            ),
          )}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-14 items-center justify-between border-b border-line bg-panel px-4 md:px-6">
          <span className="rounded-full border border-line px-3 py-1 text-xs font-medium">
            {me.tenant ? me.tenant.name : "Platform"}
          </span>
          <div className="flex items-center gap-3">
            <div className="hidden text-right sm:block">
              <div className="text-sm font-medium leading-tight">{me.name ?? me.email}</div>
              <div className="text-xs text-muted">{me.roles.join(", ")}</div>
            </div>
            <span className="grid size-8 place-items-center rounded-full bg-accent-soft text-xs font-semibold text-accent">{initials}</span>
            <form action="/api/auth/logout" method="post">
              <button className="rounded-md border border-line px-3 py-1.5 text-sm hover:bg-canvas focus-visible:outline-2 focus-visible:outline-accent">
                Sign out
              </button>
            </form>
          </div>
        </header>
        <main className="flex-1 p-4 md:p-8">{children}</main>
      </div>
    </div>
  );
}

function AccessProblem({ message }: { message: string }) {
  return (
    <div className="grid min-h-screen place-items-center bg-canvas p-4 text-ink">
      <div className="max-w-md rounded-xl border border-line bg-panel p-6 shadow-sm">
        <h1 className="text-lg font-semibold">Can&apos;t open Nova</h1>
        <p className="mt-2 text-sm text-muted">{message}</p>
        <form action="/api/auth/logout" method="post" className="mt-4">
          <button className="rounded-md bg-accent px-3 py-1.5 text-sm font-medium text-white">Sign in as someone else</button>
        </form>
      </div>
    </div>
  );
}
