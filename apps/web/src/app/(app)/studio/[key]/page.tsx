import { notFound } from "next/navigation";

import { StudioIsland } from "@/features/studio/island";
import { ApiError, apiGet } from "@/lib/api";
import type { CatalogEntry, Validation, VersionBrief } from "@/lib/types";

export default async function StudioPage(props: PageProps<"/studio/[key]">) {
  const { key } = await props.params;
  const [versions, agents, actions, apps] = await Promise.all([
    apiGet<VersionBrief[]>(`/workflows/${key}/versions`),
    apiGet<CatalogEntry[]>("/catalog/agents"),
    apiGet<CatalogEntry[]>("/catalog/actions"),
    apiGet<CatalogEntry[]>("/catalog/apps"),
  ]);
  // the draft when there is one, else the live version (the first edit creates the draft)
  const draft = await apiGet<Validation & { yaml: string }>(`/workflows/${key}/draft`).catch((e) => {
    if (e instanceof ApiError && e.status === 404) return null;
    throw e;
  });
  let yaml = draft?.yaml;
  let validation: Validation = draft ? { valid: draft.valid, issues: draft.issues } : { valid: true, issues: [] };
  if (!yaml) {
    if (!versions.length) notFound();
    yaml = (await apiGet<{ yaml: string }>(`/workflows/${key}/versions/${versions[0].version}`)).yaml;
    validation = { valid: true, issues: [] };
  }

  return (
    <div className="-m-4 h-[calc(100vh-3.5rem)] md:-m-6">
      <StudioIsland wfKey={key} initialYaml={yaml} initialValidation={validation} catalog={{ agents, actions, apps }} initialVersions={versions} />
    </div>
  );
}
