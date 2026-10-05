import { getMe } from "@/lib/api";

export default async function Home() {
  const me = await getMe();
  return (
    <div className="mx-auto max-w-4xl">
      <h1 className="text-2xl font-semibold tracking-tight">Welcome, {me.name?.split(" ")[0] ?? me.email}</h1>
      <p className="mt-1 text-sm text-muted">Signed in through Keycloak. Your tokens stay on the server; this page only holds a session cookie.</p>

      <section className="mt-6 grid gap-4 sm:grid-cols-2">
        <div className="rounded-xl border border-line bg-panel p-5">
          <h2 className="text-sm font-medium text-muted">Tenant</h2>
          <p className="mt-1 text-lg font-semibold">{me.tenant?.name ?? "None (platform)"}</p>
          {me.tenant && <p className="mt-0.5 font-mono text-xs text-muted">{me.tenant.slug} · {me.tenant.id}</p>}
        </div>
        <div className="rounded-xl border border-line bg-panel p-5">
          <h2 className="text-sm font-medium text-muted">Roles</h2>
          <div className="mt-2 flex flex-wrap gap-1.5">
            {me.roles.length ? (
              me.roles.map((r) => (
                <span key={r} className="rounded-full bg-accent-soft px-2.5 py-0.5 text-xs font-medium text-accent">
                  {r}
                </span>
              ))
            ) : (
              <span className="text-sm text-muted">No Nova roles</span>
            )}
          </div>
          <p className="mt-3 text-xs text-muted">Roles come from Keycloak; every permission is decided by OpenFGA (M4).</p>
        </div>
      </section>

      <section className="mt-4 rounded-xl border border-line bg-panel p-5">
        <h2 className="text-sm font-medium text-muted">Identity</h2>
        <dl className="mt-2 grid grid-cols-[6rem_1fr] gap-y-1 text-sm">
          <dt className="text-muted">Email</dt>
          <dd>{me.email ?? "—"}</dd>
          <dt className="text-muted">Subject</dt>
          <dd className="truncate font-mono text-xs leading-5">{me.sub}</dd>
        </dl>
      </section>
    </div>
  );
}
