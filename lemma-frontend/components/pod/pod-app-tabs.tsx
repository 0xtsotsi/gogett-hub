'use client';

import Link from 'next/link';
import { usePathname, useSearchParams } from 'next/navigation';
import { ExternalLink, Home } from 'lucide-react';
import { useApp } from '@/components/app/app-context';
import { getAppAccent } from '@/lib/app/app-accent';

function formatAppTitle(value: string | null | undefined) {
    const cleaned = (value || '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (!cleaned) return 'Untitled';
    return cleaned
        .split(' ')
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}

// Pod workspace tab strip: "Home" is the leftmost anchor (always a way back to
// the blank-canvas home), a divider sets it off from the app set, and each app
// page is a peer tab. Tabs carry the same accent chip (emoji or initial,
// `.app-icon` colored by `getAppAccent`) the home app cards use, kept small so
// the strip stays light. Inactive tabs are muted and lift on hover; the selected
// tab fills into a pill and reveals an "open in new tab" shortcut to its live URL.
export function PodAppTabs({ podId }: { podId: string }) {
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const { pages } = useApp();

    if (pages.length === 0) return null;

    const isHomeActive = pathname === `/pod/${podId}` || pathname === `/pod/${podId}/`;
    const activeSlug = pathname.startsWith(`/pod/${podId}/app/view`)
        ? searchParams.get('page')
        : null;

    return (
        <nav className="no-scrollbar flex min-w-0 flex-nowrap items-center gap-0.5 overflow-x-auto" aria-label="Pod apps">
            <Link
                href={`/pod/${podId}`}
                data-state={isHomeActive ? 'active' : undefined}
                aria-current={isHomeActive ? 'page' : undefined}
                className="inline-flex h-8 shrink-0 cursor-pointer items-center gap-1.5 rounded-md px-2 text-sm text-[var(--text-secondary)] transition-colors hover:bg-[color:color-mix(in_srgb,var(--surface-2)_60%,transparent)] hover:text-[var(--text-primary)] data-[state=active]:bg-[var(--surface-2)] data-[state=active]:font-medium data-[state=active]:text-[var(--text-primary)]"
            >
                <Home className="h-3.5 w-3.5 shrink-0" strokeWidth={1.8} />
                Home
            </Link>

            <span className="mx-1 h-5 w-px shrink-0 bg-[var(--border-subtle)]" aria-hidden="true" />

            {pages.map((page) => {
                const title = formatAppTitle(page.title || page.slug);
                const active = activeSlug === page.slug;
                const accent = getAppAccent(page.slug);
                const viewHref = `/pod/${podId}/app/view?page=${encodeURIComponent(page.slug)}`;

                return (
                    <div
                        key={page.slug}
                        data-state={active ? 'active' : undefined}
                        className="group inline-flex h-8 shrink-0 items-center rounded-md transition-colors hover:bg-[color:color-mix(in_srgb,var(--surface-2)_60%,transparent)] data-[state=active]:bg-[var(--surface-2)]"
                    >
                        <Link
                            href={viewHref}
                            aria-current={active ? 'page' : undefined}
                            title={title}
                            className="inline-flex h-8 min-w-0 cursor-pointer items-center gap-1.5 rounded-md pl-1.5 pr-2 text-sm text-[var(--text-secondary)] transition-colors group-hover:text-[var(--text-primary)] group-data-[state=active]:font-medium group-data-[state=active]:text-[var(--text-primary)]"
                        >
                            <span
                                data-accent={accent}
                                className="app-tile app-icon flex h-5 w-5 shrink-0 items-center justify-center rounded-md text-xs font-medium leading-none"
                            >
                                {page.icon || title.charAt(0)}
                            </span>
                            <span className="max-w-[10rem] truncate">{title}</span>
                        </Link>
                        {active && page.url ? (
                            <a
                                href={page.url}
                                target="_blank"
                                rel="noreferrer"
                                aria-label={`Open ${title} in new tab`}
                                title="Open in new tab"
                                className="mr-1 inline-flex h-6 w-6 shrink-0 cursor-pointer items-center justify-center rounded text-[var(--text-tertiary)] transition-colors hover:text-[var(--text-primary)]"
                            >
                                <ExternalLink className="h-3.5 w-3.5" />
                            </a>
                        ) : null}
                    </div>
                );
            })}
        </nav>
    );
}
