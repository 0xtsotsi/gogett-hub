import {
    absolutizeReadmeAssetUrls,
    getReadmeRawCandidates,
    type KitDefinition,
} from '@/lib/kits/catalog';

/** The kit-catalog README helpers only read `github` off the definition — a
 * bare repo URL stands in for a full catalog entry. */
function asKitSource(owner: string, repo: string): KitDefinition {
    return {
        id: `${owner}/${repo}`,
        name: repo,
        description: '',
        github: `https://github.com/${owner}/${repo}`,
    };
}

/** Fetch a public GitHub repo's README.md straight from raw.githubusercontent.com —
 * no auth, no rate-limited API call. Tries the two common default branch names
 * (the raw CDN doesn't support a "HEAD" alias the way codeload's zipball
 * endpoint does) and absolutizes relative image URLs against the branch that
 * answered, so screenshots render off-GitHub. */
export async function fetchGithubReadme(
    owner: string,
    repo: string,
): Promise<{ markdown: string; branch: string } | null> {
    const kit = asKitSource(owner, repo);
    for (const candidate of getReadmeRawCandidates(kit)) {
        try {
            const res = await fetch(candidate.url, { cache: 'no-store' });
            if (res.ok) {
                return {
                    markdown: absolutizeReadmeAssetUrls(await res.text(), kit, candidate.branch),
                    branch: candidate.branch,
                };
            }
        } catch {
            // try the next candidate branch
        }
    }
    return null;
}
