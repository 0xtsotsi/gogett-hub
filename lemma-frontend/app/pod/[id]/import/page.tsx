'use client';

import { use } from 'react';
import { Download } from 'lucide-react';
import { toast } from 'sonner';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { ImportPodBundleWizard } from '@/components/pod/import-pod-bundle-wizard';
import { PodSettingsShell } from '@/components/pod/pod-settings-shell';
import { Button } from '@/components/ui/button';
import { useExportPod } from '@/lib/hooks/use-pod-imports';

export default function PodImportPage({ params }: { params: Promise<{ id: string }> }) {
    return (
        <ProtectedRoute>
            <PodImportPageContent params={params} />
        </ProtectedRoute>
    );
}

function PodImportPageContent({ params }: { params: Promise<{ id: string }> }) {
    const { id: podId } = use(params);
    const exportPod = useExportPod();

    const onExport = () => {
        exportPod.mutate(
            { podId, withData: true },
            {
                onSuccess: (filename) => toast.success(`Exported ${filename}`),
                onError: (e) => toast.error(e instanceof Error ? e.message : 'Export failed'),
            },
        );
    };

    return (
        <PodSettingsShell
            podId={podId}
            title="Import / export"
            description="Download this pod as a bundle, or bring one in — reviewing what it does and needs before applying."
            action={
                <Button variant="secondary" loading={exportPod.isPending} onClick={onExport}>
                    <Download className="mr-1.5 h-4 w-4" /> Export bundle
                </Button>
            }
        >
            <ImportPodBundleWizard podId={podId} />
        </PodSettingsShell>
    );
}
