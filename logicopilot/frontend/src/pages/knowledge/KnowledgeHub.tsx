/** Knowledge Hub (2026-11-28) - Cargora's single source of truth, a module application of its
 *  own (like Finance and Production Support), Super Admin only. Opened from the header's app
 *  icons in a new tab; the login cookie is shared, so it is already signed in (no second login).
 *
 *  Written pages come from docs/knowledge in the repository (they change in the same commit as
 *  the code); Database, APIs, Settings and Change History are read live from the code. */
import { useEffect, useMemo, useState, type ReactNode, type SVGProps } from "react";
import { Link, NavLink, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { marked } from "marked";
import DOMPurify from "dompurify";
import { useAuth } from "../../auth/useAuth";
import * as kb from "../../api/knowledge";
import type { KbApi, KbChange, KbHome, KbPage, KbPageMeta, KbSearchHit, KbTable } from "../../api/knowledge";
import {
  ArrowRightIcon, ClockIcon, CodeIcon, DatabaseIcon, DocumentIcon, GearIcon, LayersIcon, SearchIcon,
  SparklesIcon, TargetIcon, UsersIcon, BriefcaseIcon, ChartBarIcon, CheckBadgeIcon,
} from "../../components/ui/icons";
import { formatDateTime } from "../../utils/dateTime";

function BookIcon(props: SVGProps<SVGSVGElement>) {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" {...props}>
      <path d="M2 5.5A2.5 2.5 0 0 1 4.5 3H10a2 2 0 0 1 2 2v15a2 2 0 0 0-2-2H4.5A2.5 2.5 0 0 1 2 15.5z" />
      <path d="M22 5.5A2.5 2.5 0 0 0 19.5 3H14a2 2 0 0 0-2 2v15a2 2 0 0 1 2-2h5.5a2.5 2.5 0 0 0 2.5-2.5z" />
    </svg>
  );
}

const CATEGORY_ICON: Record<string, (p: SVGProps<SVGSVGElement>) => ReactNode> = {
  architecture: LayersIcon, modules: BriefcaseIcon, workflows: ArrowRightIcon, "business-logic": CheckBadgeIcon,
  integrations: CodeIcon, operations: GearIcon, product: DocumentIcon, "user-guides": UsersIcon,
  business: ChartBarIcon, decisions: TargetIcon, admin: GearIcon,
};
const CATEGORY_TONE: Record<string, string> = {
  architecture: "bg-violet-50 text-violet-700 dark:bg-violet-500/10 dark:text-violet-300",
  modules: "bg-indigo-50 text-indigo-700 dark:bg-indigo-500/10 dark:text-indigo-300",
  workflows: "bg-sky-50 text-sky-700 dark:bg-sky-500/10 dark:text-sky-300",
  "business-logic": "bg-emerald-50 text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300",
  integrations: "bg-teal-50 text-teal-700 dark:bg-teal-500/10 dark:text-teal-300",
  operations: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
  product: "bg-amber-50 text-amber-800 dark:bg-amber-500/10 dark:text-amber-300",
  "user-guides": "bg-pink-50 text-pink-700 dark:bg-pink-500/10 dark:text-pink-300",
  business: "bg-orange-50 text-orange-700 dark:bg-orange-500/10 dark:text-orange-300",
  decisions: "bg-lime-50 text-lime-800 dark:bg-lime-500/10 dark:text-lime-300",
  admin: "bg-slate-100 text-slate-700 dark:bg-slate-800 dark:text-slate-300",
};
const CATEGORY_LABEL: Record<string, string> = {
  architecture: "Architecture", modules: "Modules & Features", workflows: "Workflows",
  "business-logic": "Business Logic & Rules", integrations: "APIs & Integrations", operations: "Deployment & Operations",
  product: "Product Documentation", "user-guides": "User Guides", business: "Business & Marketing",
  decisions: "Design Decisions", admin: "Admin & Settings",
};

function initials(name: string) {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((p) => p[0]?.toUpperCase()).join("") || "?";
}

export function KnowledgeHubPage() {
  const loc = useLocation();
  const section = loc.pathname.replace(/^\/knowledge\/?/, "").split("/")[0] || "home";
  return (
    <KbLayout section={section}>
      {section === "home" && <HomeView />}
      {section === "c" && <CategoryView />}
      {section === "p" && <PageView />}
      {section === "database" && <DatabaseView />}
      {section === "apis" && <ApisView />}
      {section === "settings" && <SettingsView />}
      {section === "changes" && <ChangesView />}
      {section === "search" && <SearchView />}
    </KbLayout>
  );
}

// ---------------------------------------------------------------- layout

function KbLayout({ section, children }: { section: string; children: ReactNode }) {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const nav: { to: string; label: string; Icon: (p: SVGProps<SVGSVGElement>) => ReactNode; key: string }[] = [
    { to: "/knowledge", label: "Home", Icon: BookIcon, key: "home" },
    ...["architecture", "modules", "workflows", "business-logic", "integrations", "operations", "product", "user-guides", "business", "decisions"]
      .map((c) => ({ to: `/knowledge/c/${c}`, label: CATEGORY_LABEL[c], Icon: CATEGORY_ICON[c], key: `c:${c}` })),
  ];
  const live = [
    { to: "/knowledge/database", label: "Database", Icon: DatabaseIcon, key: "database" },
    { to: "/knowledge/apis", label: "API endpoints", Icon: CodeIcon, key: "apis" },
    { to: "/knowledge/settings", label: "Settings (.env)", Icon: GearIcon, key: "settings" },
    { to: "/knowledge/changes", label: "Change History", Icon: ClockIcon, key: "changes" },
  ];
  const linkCls = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-3 rounded-xl px-3 py-2 text-sm font-medium transition ${isActive
      ? "bg-gradient-to-r from-indigo-600 to-violet-600 text-white shadow-lg shadow-indigo-900/40"
      : "text-slate-300 hover:bg-white/5 hover:text-white"}`;
  return (
    <div className="flex h-svh overflow-hidden bg-slate-50 dark:bg-slate-950">
      <aside className="flex w-64 shrink-0 flex-col bg-[#0f1430] text-white">
        <Link to="/knowledge" className="flex items-center gap-3 px-5 py-5">
          <span className="flex h-10 w-10 items-center justify-center rounded-xl bg-gradient-to-br from-indigo-500 to-violet-600 text-lg font-bold">C</span>
          <span className="leading-tight"><span className="block font-semibold">Cargora</span><span className="text-xs text-slate-400">Knowledge Hub</span></span>
        </Link>
        <nav className="flex-1 space-y-0.5 overflow-y-auto px-3 pb-4">
          {nav.map((n) => (
            <NavLink key={n.key} to={n.to} end={n.key === "home"} className={linkCls}>
              <n.Icon className="h-4 w-4 shrink-0" />{n.label}
            </NavLink>
          ))}
          <p className="px-3 pb-1 pt-4 text-[10px] font-semibold uppercase tracking-wider text-slate-500">Read live from the code</p>
          {live.map((n) => (
            <NavLink key={n.key} to={n.to} className={linkCls}>
              <n.Icon className="h-4 w-4 shrink-0" />{n.label}
            </NavLink>
          ))}
          <div className="my-3 border-t border-white/10" />
          <NavLink to="/knowledge/p/admin/writing-docs" className={linkCls}><GearIcon className="h-4 w-4" />How to write docs</NavLink>
        </nav>
        <div className="border-t border-white/10 px-5 py-4 text-xs text-slate-400">
          <p className="truncate font-medium text-slate-200">{user?.full_name || user?.email}</p>
          <p>Super Admin</p>
        </div>
      </aside>
      <div className="flex min-w-0 flex-1 flex-col overflow-y-auto">
        <header className="sticky top-0 z-20 flex items-center gap-4 border-b border-slate-200 bg-white/90 px-8 py-4 backdrop-blur dark:border-slate-800 dark:bg-slate-900/90">
          <BookIcon className="h-8 w-8 text-indigo-600 dark:text-indigo-400" />
          <div className="min-w-0">
            <h1 className="text-lg font-bold text-slate-900 dark:text-slate-50">Knowledge Hub</h1>
            <p className="text-xs text-slate-500">Cargora - centralised, versioned and always up-to-date product knowledge</p>
          </div>
          <form className="relative ml-auto w-full max-w-md" onSubmit={(e) => { e.preventDefault(); if (q.trim()) navigate(`/knowledge/search?q=${encodeURIComponent(q.trim())}`); }}>
            <SearchIcon className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400" />
            <input value={q} onChange={(e) => setQ(e.target.value)} placeholder='Search knowledge (e.g. "ERP flow", "CTH logic")'
              className="w-full rounded-xl border border-slate-200 bg-white py-2 pl-9 pr-3 text-sm focus:border-indigo-400 focus:outline-none focus:ring-2 focus:ring-indigo-100 dark:border-slate-700 dark:bg-slate-900 dark:focus:ring-indigo-500/20" />
          </form>
          <a href="/" className="hidden text-xs font-semibold text-slate-500 hover:text-indigo-600 md:block" title="Back to the Cargora app">Cargora app ↗</a>
        </header>
        <main className="mx-auto w-full max-w-6xl p-8" data-section={section}>{children}</main>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- shared bits

function useLoad<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setData(null);
    setError(null);
    fn().then((d) => alive && setData(d)).catch((e) => alive && setError(e?.response?.data?.detail ?? "Could not load."));
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  return { data, error };
}

function Loading({ error }: { error: string | null }) {
  return <p className={`text-sm ${error ? "text-rose-600" : "text-slate-400"}`}>{error ?? "Loading…"}</p>;
}

function CategoryPill({ category }: { category: string }) {
  return <span className={`rounded-md px-2 py-0.5 text-[11px] font-semibold ${CATEGORY_TONE[category] ?? CATEGORY_TONE.admin}`}>{CATEGORY_LABEL[category] ?? category}</span>;
}

function Avatar({ name }: { name: string }) {
  return (
    <span title={name} className="inline-flex h-7 w-7 items-center justify-center rounded-full bg-indigo-100 text-[10px] font-bold text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-300">
      {initials(name)}
    </span>
  );
}

function Markdown({ text }: { text: string }) {
  const html = useMemo(() => DOMPurify.sanitize(marked.parse(text, { async: false, gfm: true }) as string), [text]);
  return (
    <div
      className="kb-prose max-w-none text-[15px] leading-7 text-slate-700 dark:text-slate-300
        [&_a]:font-medium [&_a]:text-indigo-600 [&_a]:underline dark:[&_a]:text-indigo-400
        [&_blockquote]:my-4 [&_blockquote]:border-l-4 [&_blockquote]:border-indigo-300 [&_blockquote]:bg-indigo-50/50 [&_blockquote]:px-4 [&_blockquote]:py-2 dark:[&_blockquote]:bg-indigo-500/5
        [&_code]:rounded [&_code]:bg-slate-100 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-[13px] [&_code]:text-slate-800 dark:[&_code]:bg-slate-800 dark:[&_code]:text-slate-200
        [&_h1]:mb-4 [&_h1]:text-2xl [&_h1]:font-bold [&_h1]:text-slate-900 dark:[&_h1]:text-slate-50
        [&_h2]:mb-3 [&_h2]:mt-8 [&_h2]:border-b [&_h2]:border-slate-200 [&_h2]:pb-1 [&_h2]:text-xl [&_h2]:font-semibold [&_h2]:text-slate-900 dark:[&_h2]:border-slate-700 dark:[&_h2]:text-slate-50
        [&_h3]:mb-2 [&_h3]:mt-6 [&_h3]:text-lg [&_h3]:font-semibold [&_h3]:text-slate-900 dark:[&_h3]:text-slate-100
        [&_li]:my-1 [&_ol]:my-3 [&_ol]:list-decimal [&_ol]:pl-6 [&_p]:my-3 [&_ul]:my-3 [&_ul]:list-disc [&_ul]:pl-6
        [&_pre]:my-4 [&_pre]:overflow-x-auto [&_pre]:rounded-xl [&_pre]:bg-slate-900 [&_pre]:p-4 [&_pre]:text-[13px] [&_pre]:leading-6 [&_pre]:text-slate-100
        [&_pre_code]:bg-transparent [&_pre_code]:p-0 [&_pre_code]:text-slate-100
        [&_table]:my-4 [&_table]:w-full [&_table]:border-collapse [&_table]:text-sm
        [&_td]:border [&_td]:border-slate-200 [&_td]:px-3 [&_td]:py-2 [&_td]:align-top dark:[&_td]:border-slate-700
        [&_th]:border [&_th]:border-slate-200 [&_th]:bg-slate-50 [&_th]:px-3 [&_th]:py-2 [&_th]:text-left [&_th]:font-semibold dark:[&_th]:border-slate-700 dark:[&_th]:bg-slate-800"
      dangerouslySetInnerHTML={{ __html: html }}
    />
  );
}

function Card({ children, className = "" }: { children: ReactNode; className?: string }) {
  return <div className={`rounded-2xl border border-slate-200 bg-white p-5 shadow-sm dark:border-slate-800 dark:bg-slate-900 ${className}`}>{children}</div>;
}

// ---------------------------------------------------------------- views

function HomeView() {
  const { data: h, error } = useLoad<KbHome>(kb.getHome, []);
  if (!h) return <Loading error={error} />;
  const stats = [
    { n: h.counts.documents, label: "Documents", Icon: BookIcon, tone: "from-indigo-50 text-indigo-600 dark:from-indigo-500/10 dark:text-indigo-300" },
    { n: h.counts.modules, label: "Modules", Icon: LayersIcon, tone: "from-violet-50 text-violet-600 dark:from-violet-500/10 dark:text-violet-300" },
    { n: h.counts.api_endpoints, label: "API endpoints", Icon: CodeIcon, tone: "from-sky-50 text-sky-600 dark:from-sky-500/10 dark:text-sky-300" },
    { n: h.counts.db_tables, label: "Database tables", Icon: DatabaseIcon, tone: "from-teal-50 text-teal-600 dark:from-teal-500/10 dark:text-teal-300" },
  ];
  const pct = h.completeness;
  return (
    <div className="space-y-6">
      <section className="relative overflow-hidden rounded-3xl bg-gradient-to-r from-indigo-100 via-violet-100 to-white p-8 dark:from-indigo-500/15 dark:via-violet-500/10 dark:to-slate-900">
        <h2 className="text-3xl font-extrabold text-slate-900 dark:text-white">Cargora Knowledge Hub</h2>
        <p className="mt-2 max-w-2xl text-slate-700 dark:text-slate-300">Single source of truth for our product, technology, business and go-to-market.</p>
        <div className="mt-4 flex flex-wrap gap-4 text-sm font-medium text-slate-700 dark:text-slate-200">
          {["Always up to date", "Version controlled", "Linked to code changes"].map((t) => (
            <span key={t} className="inline-flex items-center gap-1.5"><span className="flex h-5 w-5 items-center justify-center rounded-full bg-indigo-600 text-[11px] text-white">✓</span>{t}</span>
          ))}
        </div>
        <SparklesIcon className="absolute -right-4 -top-4 h-40 w-40 text-indigo-300/30" />
      </section>

      <div className="grid grid-cols-2 gap-4 lg:grid-cols-5">
        {stats.map((s) => (
          <Card key={s.label} className="flex items-center gap-4 p-4">
            <span className={`flex h-12 w-12 items-center justify-center rounded-xl bg-gradient-to-br to-white dark:to-slate-900 ${s.tone}`}><s.Icon className="h-6 w-6" /></span>
            <span><span className="block text-2xl font-bold tabular-nums text-slate-900 dark:text-slate-50">{s.n}</span><span className="text-sm text-slate-500">{s.label}</span></span>
          </Card>
        ))}
        <Card className="flex items-center gap-4 p-4" >
          <span className="relative flex h-14 w-14 items-center justify-center rounded-full"
            style={{ background: `conic-gradient(rgb(16 185 129) ${pct * 3.6}deg, rgb(226 232 240) 0deg)` }}>
            <span className="flex h-11 w-11 items-center justify-center rounded-full bg-white text-sm font-bold text-slate-900 dark:bg-slate-900 dark:text-slate-50">{pct}%</span>
          </span>
          <span className="text-sm"><span className="block font-semibold text-slate-900 dark:text-slate-50">Documentation completeness</span>
            <span className="text-xs text-slate-500">{h.modules_documented} of {h.counts.modules} modules have a page</span></span>
        </Card>
      </div>

      <div className="grid gap-6 lg:grid-cols-[1fr_22rem]">
        <Card>
          <div className="mb-3 flex items-center justify-between">
            <h3 className="flex items-center gap-2 font-semibold text-slate-900 dark:text-slate-50"><ClockIcon className="h-5 w-5 text-indigo-600" />Recently Updated</h3>
            <Link to="/knowledge/changes" className="text-sm font-medium text-indigo-600 hover:underline dark:text-indigo-400">Change history →</Link>
          </div>
          <table className="w-full text-sm">
            <thead className="text-left text-xs font-semibold uppercase tracking-wide text-slate-500">
              <tr className="border-b border-slate-200 dark:border-slate-700"><th className="py-2">Document</th><th>Category</th><th>Updated</th><th>By</th></tr>
            </thead>
            <tbody>
              {h.recent.map((r) => (
                <tr key={r.slug} className="border-b border-slate-100 dark:border-slate-800">
                  <td className="py-2.5"><Link to={`/knowledge/p/${r.slug}`} className="flex items-center gap-2 font-medium text-slate-800 hover:text-indigo-600 dark:text-slate-100"><DocumentIcon className="h-4 w-4 text-indigo-500" />{r.title}</Link></td>
                  <td><CategoryPill category={r.category} /></td>
                  <td className="whitespace-nowrap text-slate-500">{formatDateTime(r.updated)}</td>
                  <td>{r.updated_by && <Avatar name={r.updated_by} />}</td>
                </tr>
              ))}
              {h.recent.length === 0 && <tr><td colSpan={4} className="py-6 text-center text-slate-400">No pages yet.</td></tr>}
            </tbody>
          </table>
        </Card>
        <div className="space-y-6">
          <Card>
            <h3 className="mb-3 font-semibold text-slate-900 dark:text-slate-50">Quick Links</h3>
            <ul className="space-y-1">
              {h.quick_links.map((l) => (
                <li key={l.slug}>
                  <Link to={`/knowledge/p/${l.slug}`} className="flex items-center justify-between rounded-lg px-2 py-2 text-sm text-slate-700 hover:bg-slate-50 dark:text-slate-200 dark:hover:bg-slate-800">
                    {l.title}<ArrowRightIcon className="h-4 w-4 text-slate-400" />
                  </Link>
                </li>
              ))}
            </ul>
          </Card>
          <Card>
            <h3 className="mb-3 font-semibold text-slate-900 dark:text-slate-50">Modules</h3>
            <div className="flex flex-wrap gap-1.5">
              {h.modules.map((m) => <span key={m.key} className="rounded-md bg-slate-100 px-2 py-0.5 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">{m.name}</span>)}
            </div>
          </Card>
        </div>
      </div>
    </div>
  );
}

function CategoryView() {
  const loc = useLocation();
  const category = loc.pathname.replace(/^\/knowledge\/c\//, "").split("/")[0] || "";
  const { data, error } = useLoad(() => kb.getPages(category), [category]);
  if (!data) return <Loading error={error} />;
  const Icon = CATEGORY_ICON[category] ?? DocumentIcon;
  return (
    <div className="space-y-5">
      <div className="flex items-center gap-3">
        <span className={`flex h-11 w-11 items-center justify-center rounded-xl ${CATEGORY_TONE[category] ?? ""}`}><Icon className="h-5 w-5" /></span>
        <div><h2 className="text-2xl font-bold text-slate-900 dark:text-slate-50">{CATEGORY_LABEL[category] ?? category}</h2>
          <p className="text-sm text-slate-500">{data.items.length} page(s)</p></div>
      </div>
      {data.items.length === 0 ? (
        <Card><p className="text-sm text-slate-500">No pages here yet. They are added in <code>logicopilot/docs/knowledge/{category}/</code> with each change - see <Link to="/knowledge/p/admin/writing-docs" className="font-medium text-indigo-600">How to write docs</Link>.</p></Card>
      ) : (
        <div className="grid gap-4 md:grid-cols-2">
          {data.items.map((p) => <PageCard key={p.slug} p={p} />)}
        </div>
      )}
    </div>
  );
}

function PageCard({ p }: { p: KbPageMeta }) {
  return (
    <Link to={`/knowledge/p/${p.slug}`} className="group block rounded-2xl border border-slate-200 bg-white p-5 shadow-sm transition hover:-translate-y-0.5 hover:shadow-md dark:border-slate-800 dark:bg-slate-900">
      <div className="mb-2 flex items-center gap-2"><CategoryPill category={p.category} />{p.status === "draft" && <span className="rounded bg-amber-100 px-1.5 py-0.5 text-[10px] font-bold text-amber-800">DRAFT</span>}</div>
      <h3 className="font-semibold text-slate-900 group-hover:text-indigo-700 dark:text-slate-50">{p.title}</h3>
      {p.summary && <p className="mt-1 text-sm text-slate-500">{p.summary}</p>}
      <p className="mt-3 flex items-center gap-2 text-xs text-slate-400">{p.updated_by && <Avatar name={p.updated_by} />}Updated {formatDateTime(p.updated)}</p>
    </Link>
  );
}

function PageView() {
  const loc = useLocation();
  const slug = decodeURIComponent(loc.pathname.replace(/^\/knowledge\/p\//, ""));
  const { data: p, error } = useLoad<KbPage>(() => kb.getPage(slug), [slug]);
  if (!p) return <Loading error={error} />;
  return (
    <div className="grid gap-6 lg:grid-cols-[1fr_16rem]">
      <article className="rounded-2xl border border-slate-200 bg-white p-8 shadow-sm dark:border-slate-800 dark:bg-slate-900">
        <div className="mb-4 flex flex-wrap items-center gap-2 text-xs text-slate-500">
          <Link to={`/knowledge/c/${p.category}`}><CategoryPill category={p.category} /></Link>
          {p.status === "draft" && <span className="rounded bg-amber-100 px-1.5 py-0.5 font-bold text-amber-800">DRAFT</span>}
        </div>
        <Markdown text={p.body} />
      </article>
      <aside className="space-y-4 lg:sticky lg:top-24 lg:self-start">
        <Card className="space-y-3 text-sm">
          <Meta label="Last updated" value={formatDateTime(p.updated)} />
          <Meta label="By" value={p.updated_by || "—"} />
          <Meta label="Owner" value={p.owner || "—"} />
          <Meta label="Module" value={p.module || "—"} />
          <Meta label="File" value={<code className="break-all text-[11px]">{p.path}</code>} />
        </Card>
        <p className="px-1 text-xs text-slate-500">Edit this page in the repository with the code change it describes.</p>
      </aside>
    </div>
  );
}

function Meta({ label, value }: { label: string; value: ReactNode }) {
  return <div><p className="text-[11px] font-semibold uppercase tracking-wide text-slate-400">{label}</p><p className="text-slate-800 dark:text-slate-200">{value}</p></div>;
}

function DatabaseView() {
  const { data, error } = useLoad<KbTable[]>(kb.getDatabase, []);
  const [q, setQ] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  if (!data) return <Loading error={error} />;
  const shown = data.filter((t) => !q || `${t.table} ${t.model} ${t.doc} ${t.columns.map((c) => c.name).join(" ")}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <div className="space-y-4">
      <LiveHeader title="Database" text={`${data.length} tables, read live from the code's data models - always current.`} q={q} setQ={setQ} placeholder="Find a table or column…" />
      <div className="space-y-2">
        {shown.map((t) => (
          <Card key={t.table} className="p-0">
            <button type="button" onClick={() => setOpen(open === t.table ? null : t.table)} className="flex w-full items-start justify-between gap-4 p-4 text-left">
              <span><span className="font-mono text-sm font-semibold text-slate-900 dark:text-slate-50">{t.table}</span>
                {t.model && <span className="ml-2 text-xs text-slate-400">{t.model}</span>}
                {t.doc && <span className="mt-1 block text-xs text-slate-500">{t.doc.slice(0, 220)}{t.doc.length > 220 ? "…" : ""}</span>}</span>
              <span className="shrink-0 text-xs text-slate-400">{t.columns.length} columns {open === t.table ? "▲" : "▼"}</span>
            </button>
            {open === t.table && (
              <div className="overflow-x-auto border-t border-slate-100 px-4 pb-4 dark:border-slate-800">
                <table className="mt-2 w-full text-xs">
                  <thead className="text-left text-slate-500"><tr><th className="py-1.5">Column</th><th>Type</th><th>Null</th><th>Links to</th></tr></thead>
                  <tbody>{t.columns.map((c) => (
                    <tr key={c.name} className="border-t border-slate-100 dark:border-slate-800">
                      <td className="py-1.5 font-mono">{c.pk ? "🔑 " : ""}{c.name}</td><td className="font-mono text-slate-500">{c.type}</td>
                      <td>{c.nullable ? "yes" : "no"}</td><td className="font-mono text-indigo-600 dark:text-indigo-400">{c.fk ?? ""}</td>
                    </tr>))}</tbody>
                </table>
              </div>
            )}
          </Card>
        ))}
      </div>
    </div>
  );
}

function ApisView() {
  const { data, error } = useLoad<KbApi[]>(kb.getApis, []);
  const [q, setQ] = useState("");
  if (!data) return <Loading error={error} />;
  const shown = data.filter((a) => !q || `${a.method} ${a.path} ${a.module} ${a.summary}`.toLowerCase().includes(q.toLowerCase()));
  const byModule: Record<string, KbApi[]> = {};
  for (const a of shown) (byModule[a.module] ??= []).push(a);
  const methodTone: Record<string, string> = { GET: "bg-sky-100 text-sky-800", POST: "bg-emerald-100 text-emerald-800", PUT: "bg-amber-100 text-amber-800", PATCH: "bg-violet-100 text-violet-800", DELETE: "bg-rose-100 text-rose-800" };
  return (
    <div className="space-y-4">
      <LiveHeader title="API endpoints" text={`${data.length} endpoints in ${Object.keys(byModule).length} modules, read live from the running backend.`} q={q} setQ={setQ} placeholder="Find an endpoint…" />
      {Object.entries(byModule).map(([mod, list]) => (
        <Card key={mod}>
          <h3 className="mb-2 font-mono text-sm font-semibold text-slate-900 dark:text-slate-50">{mod} <span className="font-sans text-xs font-normal text-slate-400">· {list.length}</span></h3>
          <ul className="divide-y divide-slate-100 dark:divide-slate-800">
            {list.map((a) => (
              <li key={a.method + a.path} className="flex flex-wrap items-baseline gap-2 py-1.5 text-xs">
                <span className={`w-14 shrink-0 rounded px-1.5 py-0.5 text-center font-bold ${methodTone[a.method] ?? "bg-slate-100"}`}>{a.method}</span>
                <span className="font-mono text-slate-800 dark:text-slate-200">{a.path}</span>
                {a.summary && <span className="w-full pl-16 text-slate-500">{a.summary}</span>}
              </li>
            ))}
          </ul>
        </Card>
      ))}
    </div>
  );
}

function SettingsView() {
  const { data, error } = useLoad(kb.getSettings, []);
  if (!data) return <Loading error={error} />;
  return (
    <div className="space-y-4">
      <LiveHeader title="Settings (.env)" text="Setting names and types read from the code. Values are never shown - they hold keys and passwords." />
      <Card className="p-0">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-left text-xs font-semibold uppercase tracking-wide text-slate-500 dark:bg-slate-800"><tr><th className="px-4 py-2">Name</th><th>Type</th><th>On this server</th></tr></thead>
          <tbody>{data.map((s) => (
            <tr key={s.name} className="border-t border-slate-100 dark:border-slate-800">
              <td className="px-4 py-1.5 font-mono text-xs">{s.name}</td><td className="font-mono text-xs text-slate-500">{s.type}</td>
              <td className="text-xs">{s.set ? <span className="text-emerald-700 dark:text-emerald-300">set</span> : <span className="text-slate-400">default</span>}</td>
            </tr>))}</tbody>
        </table>
      </Card>
    </div>
  );
}

function ChangesView() {
  const { data, error } = useLoad<KbChange[]>(() => kb.getChanges(200), []);
  const [mod, setMod] = useState("");
  const [who, setWho] = useState("");
  const [gapsOnly, setGapsOnly] = useState(false);
  if (!data) return <Loading error={error} />;
  const modules = Array.from(new Set(data.flatMap((c) => c.modules))).sort();
  const people = Array.from(new Set(data.map((c) => c.author))).sort();
  const gap = (c: KbChange) => c.code_changed && !c.docs_changed;
  const shown = data.filter((c) => (!mod || c.modules.includes(mod)) && (!who || c.author === who) && (!gapsOnly || gap(c)));
  const withDocs = data.filter((c) => c.docs_changed).length;
  const gaps = data.filter(gap).length;
  const sel = "rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900";
  return (
    <div className="space-y-4">
      <LiveHeader title="Change History" text={`The last ${data.length} changes, from the code's history - who, what and why. ${withDocs} of them also updated docs.`} />
      <div className="flex flex-wrap gap-2">
        <select value={mod} onChange={(e) => setMod(e.target.value)} className={sel}><option value="">All modules</option>{modules.map((m) => <option key={m}>{m}</option>)}</select>
        <select value={who} onChange={(e) => setWho(e.target.value)} className={sel}><option value="">Everyone</option>{people.map((p) => <option key={p}>{p}</option>)}</select>
        <label className={`inline-flex cursor-pointer items-center gap-2 rounded-lg border px-3 py-2 text-sm ${gapsOnly ? "border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-500/40 dark:bg-amber-500/10 dark:text-amber-200" : "border-slate-200 bg-white dark:border-slate-700 dark:bg-slate-900"}`}>
          <input type="checkbox" checked={gapsOnly} onChange={(e) => setGapsOnly(e.target.checked)} />
          Missing docs ({gaps})
        </label>
        <span className="self-center text-xs text-slate-500">{shown.length} change(s)</span>
      </div>
      <ol className="relative space-y-3 border-l-2 border-indigo-100 pl-6 dark:border-indigo-500/20">
        {shown.map((c) => (
          <li key={c.hash} className="relative">
            <span className="absolute -left-[31px] top-4 h-3 w-3 rounded-full border-2 border-white bg-indigo-500 dark:border-slate-950" />
            <Card className="p-4">
              <div className="flex flex-wrap items-center gap-2 text-xs text-slate-500">
                <Avatar name={c.author} /><span className="font-medium text-slate-700 dark:text-slate-200">{c.author}</span>
                <span>· {formatDateTime(c.date)}</span><code className="text-[11px]">{c.hash}</code>
                {c.docs_changed && <span className="rounded bg-emerald-50 px-1.5 py-0.5 font-semibold text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">docs updated</span>}
                {gap(c) && <span className="rounded bg-amber-50 px-1.5 py-0.5 font-semibold text-amber-800 dark:bg-amber-500/10 dark:text-amber-300">no docs</span>}
              </div>
              <p className="mt-2 text-sm font-medium text-slate-900 dark:text-slate-50">{c.subject}</p>
              <div className="mt-2 flex flex-wrap gap-1">{c.modules.map((m) => <span key={m} className="rounded bg-slate-100 px-1.5 py-0.5 font-mono text-[10px] text-slate-600 dark:bg-slate-800 dark:text-slate-300">{m}</span>)}</div>
            </Card>
          </li>
        ))}
      </ol>
    </div>
  );
}

function SearchView() {
  const [params] = useSearchParams();
  const q = params.get("q") ?? "";
  const { data, error } = useLoad<KbSearchHit[]>(() => kb.search(q), [q]);
  return (
    <div className="space-y-4">
      <h2 className="text-2xl font-bold text-slate-900 dark:text-slate-50">Search: “{q}”</h2>
      {!data ? <Loading error={error} /> : data.length === 0 ? (
        <Card><p className="text-sm text-slate-500">Nothing found. Try fewer words, or a field / table / screen name.</p></Card>
      ) : (
        <ul className="space-y-2">
          {data.map((r, i) => (
            <li key={i}>
              <Card className="p-4">
                <div className="flex items-center gap-2 text-xs">
                  <span className="rounded bg-slate-100 px-1.5 py-0.5 font-semibold uppercase text-slate-600 dark:bg-slate-800 dark:text-slate-300">{r.type === "page" ? "Page" : r.type === "table" ? "Table" : "API"}</span>
                  {r.category && <CategoryPill category={r.category} />}
                </div>
                {r.type === "page" && r.slug
                  ? <Link to={`/knowledge/p/${r.slug}`} className="mt-1 block font-semibold text-indigo-700 hover:underline dark:text-indigo-300">{r.title}</Link>
                  : <Link to={r.type === "table" ? "/knowledge/database" : "/knowledge/apis"} className="mt-1 block font-mono text-sm font-semibold text-indigo-700 hover:underline dark:text-indigo-300">{r.title}</Link>}
                {r.snippet && <p className="mt-1 text-sm text-slate-500">{r.snippet}</p>}
              </Card>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function LiveHeader({ title, text, q, setQ, placeholder }: { title: string; text: string; q?: string; setQ?: (v: string) => void; placeholder?: string }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 className="flex items-center gap-2 text-2xl font-bold text-slate-900 dark:text-slate-50">{title}
          <span className="rounded-full bg-emerald-50 px-2 py-0.5 text-[11px] font-semibold text-emerald-700 dark:bg-emerald-500/10 dark:text-emerald-300">live from code</span></h2>
        <p className="text-sm text-slate-500">{text}</p>
      </div>
      {setQ && (
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder={placeholder}
          className="w-64 rounded-lg border border-slate-200 bg-white px-3 py-2 text-sm dark:border-slate-700 dark:bg-slate-900" />
      )}
    </div>
  );
}
