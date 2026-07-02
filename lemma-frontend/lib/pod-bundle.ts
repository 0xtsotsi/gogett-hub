/** Bundle resource-kind vocabulary shared by the share sheet and the import
 * wizard — one place for display order, singular forms, and count formatting
 * so the two surfaces can't drift. Icons stay with each consumer: they're
 * presentation, not vocabulary. */

/** User-facing bundle resource kinds in display order — what someone "gets"
 * when they install a bundle. */
export const RESOURCE_KINDS: readonly string[] = [
    'tables',
    'functions',
    'agents',
    'workflows',
    'schedules',
    'surfaces',
    'apps',
];

/** Full plan-step order: user-facing resources first, then the grant passes
 * that run once every resource exists — plan plumbing, not something the
 * user "gets". */
export const RESOURCE_KIND_ORDER: readonly string[] = [
    ...RESOURCE_KINDS,
    'agent_grants',
    'function_grants',
];

/** Singular forms for count = 1 ("1 agent", not "1 agents"). */
export const SINGULAR: Record<string, string> = {
    tables: 'table',
    functions: 'function',
    agents: 'agent',
    workflows: 'workflow',
    schedules: 'schedule',
    surfaces: 'surface',
    apps: 'app',
    agent_grants: 'agent access',
    function_grants: 'function access',
};

/** Group headings for the plan list. */
export const KIND_LABEL: Record<string, string> = {
    tables: 'Tables',
    functions: 'Functions',
    agents: 'Agents',
    workflows: 'Workflows',
    schedules: 'Schedules',
    surfaces: 'Surfaces',
    apps: 'Apps',
    agent_grants: 'Agent access',
    function_grants: 'Function access',
};

/** "5 tables" / "1 agent" — a singularized count for one kind. */
export function formatKindCount(kind: string, count: number): string {
    return `${count} ${count === 1 ? (SINGULAR[kind] ?? kind) : kind}`;
}

/** "5 tables · 4 functions · 1 agent · 1 app" from non-zero resource counts;
 * empty string when there's nothing to say. */
export function formatResourceCounts(counts: Record<string, number>): string {
    return RESOURCE_KINDS.filter((kind) => (counts[kind] ?? 0) > 0)
        .map((kind) => formatKindCount(kind, counts[kind]))
        .join(' · ');
}

/** Non-zero per-kind counts off an import plan's steps, in display order —
 * user-facing kinds only (grants are an implementation detail of the plan). */
export function planResourceCounts(steps: { resource_type: string }[]): {
    kind: string;
    count: number;
}[] {
    const counts = new Map<string, number>();
    for (const s of steps) counts.set(s.resource_type, (counts.get(s.resource_type) ?? 0) + 1);
    return RESOURCE_KINDS.flatMap((kind) => {
        const n = counts.get(kind) ?? 0;
        return n ? [{ kind, count: n }] : [];
    });
}
