<!-- LOVABLE:BEGIN -->

> [!IMPORTANT]
> This project is connected to [Lovable](https://lovable.dev). Avoid rewriting
> published git history — force pushing, or rebasing/amending/squashing commits
> that are already pushed — as it rewrites history on Lovable's side and the
> user will likely lose their project history.
>
> Commits you push to the connected branch sync back to Lovable and show up in
> the editor, so keep the branch in a working state.

<!-- LOVABLE:END -->

- Keep Blockday browser-local and dependency-light; personal task data persists in localStorage because the app is intentionally local-only.
- Preserve the desktop-first Mondrian grid language through semantic global tokens; this is the product's defining interaction and visual structure.
- Blockday is six route pages (/, /flow, /projects, /focus, /stats, /diary) sharing one shell: AppHeader + BlockdayProvider live in the root route, and all pages compose the shared blocks in `src/components/blockday-ui.tsx` from the single sample dataset in `src/lib/blockday-data.ts`; the old single-screen `blockday-app` component is gone.
