'use client';

import { useMemo, useState } from 'react';
import {
    AlertTriangle,
    Check,
    CircleAlert,
    FileArchive,
    RotateCcw,
    Upload,
} from 'lucide-react';
import { toast } from 'sonner';

import { Button } from '@/components/ui/button';
import {
    type Capability,
    type ImportStep,
    type PodImport,
    useApplyImport,
    useCreateImport,
} from '@/lib/hooks/use-pod-imports';

type Phase = 'upload' | 'review' | 'result';

const TIER_LABEL: Record<string, string> = {
    code: 'Runs code',
    external: 'External access',
    ai: 'AI agents',
    data: 'Data',
};

function Section({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <div className="mb-5">
            <p className="mb-2 text-[13px] font-medium text-[var(--text-secondary)]">{title}</p>
            {children}
        </div>
    );
}

function CapabilityList({ capabilities }: { capabilities: Capability[] }) {
    if (!capabilities.length) return null;
    return (
        <Section title="This pod will">
            <ul className="space-y-1.5">
                {capabilities.map((cap, i) => (
                    <li key={i} className="flex items-center gap-2 text-sm text-[var(--text-primary)]">
                        <span className="h-1.5 w-1.5 rounded-full bg-[var(--delight)]" aria-hidden />
                        {cap.summary}
                        <span className="text-xs text-[var(--text-tertiary)]">
                            {TIER_LABEL[cap.tier] ?? cap.tier}
                        </span>
                    </li>
                ))}
            </ul>
        </Section>
    );
}

function RequirementsList({ requirements }: { requirements: Record<string, unknown> }) {
    const connectors = (requirements.connectors as { key: string; purpose?: string }[]) ?? [];
    const members = (requirements.members as { key: string }[]) ?? [];
    const variables = (requirements.variables as { key: string; purpose?: string }[]) ?? [];
    const data = requirements.data as { row_count?: number; tables_with_seed?: string[] } | undefined;

    if (!connectors.length && !members.length && !variables.length && !data) {
        return (
            <Section title="Needs from you">
                <p className="text-sm text-[var(--state-success)]">
                    Nothing to wire up — this bundle is self-contained.
                </p>
            </Section>
        );
    }
    return (
        <Section title="Needs from you">
            <ul className="space-y-1.5 text-sm">
                {connectors.map((c) => (
                    <li key={c.key} className="text-[var(--text-primary)]">
                        <span className="font-medium">connector</span> {c.key}
                        {c.purpose ? <span className="text-[var(--text-tertiary)]"> · {c.purpose}</span> : null}
                    </li>
                ))}
                {members.map((m) => (
                    <li key={m.key} className="text-[var(--text-primary)]">
                        <span className="font-medium">person</span> {m.key}
                        <span className="text-[var(--text-tertiary)]"> · defaults to you</span>
                    </li>
                ))}
                {variables.map((v) => (
                    <li key={v.key} className="text-[var(--text-primary)]">
                        <span className="font-medium">variable</span> {v.key}
                    </li>
                ))}
                {data ? (
                    <li className="text-[var(--text-primary)]">
                        <span className="font-medium">data</span> {data.row_count ?? 0} row(s) across{' '}
                        {(data.tables_with_seed ?? []).join(', ')}
                    </li>
                ) : null}
            </ul>
        </Section>
    );
}

function StepRow({ step }: { step: ImportStep }) {
    const icon =
        step.status === 'COMPLETED' ? (
            <Check className="h-4 w-4 text-[var(--state-success)]" />
        ) : step.status === 'FAILED' ? (
            <CircleAlert className="h-4 w-4 text-[var(--state-error)]" />
        ) : step.status === 'SKIPPED' ? (
            <span className="text-xs text-[var(--text-tertiary)]">skipped</span>
        ) : (
            <span className="h-1.5 w-1.5 rounded-full bg-[var(--border-strong)]" aria-hidden />
        );
    return (
        <li className="flex items-center gap-3 border-b border-[var(--border-subtle)] py-2 last:border-0">
            <span className="flex w-4 justify-center">{icon}</span>
            <span className="text-xs uppercase tracking-wide text-[var(--text-tertiary)]">
                {step.action}
            </span>
            <span className="flex-1 text-sm text-[var(--text-primary)]">
                {step.resource_type}/{step.resource_name}
            </span>
            {step.destructive ? (
                <span className="flex items-center gap-1 text-xs text-[var(--state-error)]">
                    <AlertTriangle className="h-3.5 w-3.5" /> data loss
                </span>
            ) : null}
            {step.error ? (
                <span className="max-w-[40%] truncate text-xs text-[var(--state-error)]" title={step.error}>
                    {step.error}
                </span>
            ) : null}
        </li>
    );
}

function PlanList({ imp }: { imp: PodImport }) {
    return (
        <Section title={`Plan · ${imp.progress_done}/${imp.progress_total}`}>
            <ul className="rounded-[10px] border border-[var(--border-subtle)] bg-[var(--surface-panel)] px-3">
                {imp.plan.map((step) => (
                    <StepRow key={`${step.resource_type}/${step.resource_name}`} step={step} />
                ))}
            </ul>
        </Section>
    );
}

export function ImportPodBundleWizard({ podId }: { podId: string }) {
    const [phase, setPhase] = useState<Phase>('upload');
    const [file, setFile] = useState<File | null>(null);
    const [imp, setImp] = useState<PodImport | null>(null);

    const createImport = useCreateImport();
    const applyImport = useApplyImport();

    const destructiveCount = useMemo(
        () => imp?.plan.filter((s) => s.destructive).length ?? 0,
        [imp],
    );

    const onUpload = async () => {
        if (!file) return;
        try {
            const result = await createImport.mutateAsync({ podId, file, sourceName: file.name });
            setImp(result);
            setPhase('review');
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Upload failed');
        }
    };

    const onApply = async () => {
        if (!imp) return;
        try {
            const result = await applyImport.mutateAsync({ podId, importId: imp.id });
            setImp(result);
            setPhase('result');
            if (result.status === 'COMPLETED') toast.success('Import complete');
            else if (result.status === 'FAILED') toast.error('Import failed — you can resume');
        } catch (e) {
            toast.error(e instanceof Error ? e.message : 'Apply failed');
        }
    };

    const reset = () => {
        setFile(null);
        setImp(null);
        setPhase('upload');
    };

    return (
        <div className="mx-auto w-full max-w-2xl">
            <div className="surface-panel rounded-[12px] p-6">
                {phase === 'upload' && (
                    <>
                        <p className="mb-4 text-sm text-[var(--text-secondary)]">
                            Upload a pod bundle archive (.zip or .tar.gz). We&apos;ll show you exactly
                            what it does and what it needs before anything is applied.
                        </p>
                        <label className="flex cursor-pointer flex-col items-center gap-2 rounded-[10px] border border-dashed border-[var(--border-strong)] px-4 py-8 text-center hover:bg-[var(--surface-2)]">
                            <Upload className="h-5 w-5 text-[var(--text-tertiary)]" />
                            <span className="text-sm text-[var(--text-secondary)]">
                                {file ? file.name : 'Choose a bundle archive'}
                            </span>
                            <input
                                type="file"
                                accept=".zip,.tar.gz,.tgz,.tar"
                                className="hidden"
                                onChange={(e) => setFile(e.target.files?.[0] ?? null)}
                            />
                        </label>
                        <div className="mt-5 flex justify-end gap-2">
                            <Button disabled={!file} loading={createImport.isPending} onClick={onUpload}>
                                <FileArchive className="mr-1.5 h-4 w-4" /> Analyze bundle
                            </Button>
                        </div>
                    </>
                )}

                {phase === 'review' && imp && (
                    <>
                        <CapabilityList capabilities={imp.capabilities} />
                        <RequirementsList requirements={imp.requirements} />
                        <PlanList imp={imp} />
                        {destructiveCount > 0 && (
                            <div className="mb-5 flex items-start gap-2 rounded-[10px] border border-[var(--state-error)] bg-[color-mix(in_srgb,var(--state-error)_8%,transparent)] px-3 py-2.5">
                                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--state-error)]" />
                                <p className="text-sm text-[var(--text-primary)]">
                                    {destructiveCount} table change(s) will drop or rebuild columns —
                                    existing data in those columns is lost.
                                </p>
                            </div>
                        )}
                        <div className="flex justify-between">
                            <Button variant="ghost" onClick={reset}>
                                Back
                            </Button>
                            <Button
                                variant={destructiveCount > 0 ? 'destructive' : 'primary'}
                                loading={applyImport.isPending}
                                onClick={onApply}
                            >
                                {destructiveCount > 0 ? 'Apply (data loss)' : 'Apply import'}
                            </Button>
                        </div>
                    </>
                )}

                {phase === 'result' && imp && (
                    <>
                        <div className="mb-4 flex items-center gap-2">
                            {imp.status === 'COMPLETED' ? (
                                <>
                                    <Check className="h-5 w-5 text-[var(--state-success)]" />
                                    <p className="text-base font-medium text-[var(--text-primary)]">
                                        Imported · {imp.progress_done}/{imp.progress_total}
                                    </p>
                                </>
                            ) : (
                                <>
                                    <CircleAlert className="h-5 w-5 text-[var(--state-error)]" />
                                    <p className="text-base font-medium text-[var(--text-primary)]">
                                        Stopped at {imp.progress_done}/{imp.progress_total}
                                    </p>
                                </>
                            )}
                        </div>
                        {imp.error ? (
                            <p className="mb-4 text-sm text-[var(--state-error)]">{imp.error}</p>
                        ) : null}
                        <PlanList imp={imp} />
                        <div className="flex justify-between">
                            <Button variant="ghost" onClick={reset}>
                                Import another
                            </Button>
                            {imp.status === 'FAILED' && (
                                <Button loading={applyImport.isPending} onClick={onApply}>
                                    <RotateCcw className="mr-1.5 h-4 w-4" /> Resume
                                </Button>
                            )}
                        </div>
                    </>
                )}
            </div>
        </div>
    );
}
