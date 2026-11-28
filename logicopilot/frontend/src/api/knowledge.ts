import { apiClient } from "./client";

/** Knowledge Hub (backend: api/v1/knowledge.py, app/core/knowledge.py) - Super Admin only. */
export interface KbPageMeta {
  slug: string; title: string; category: string; module: string; owner: string;
  status: string; summary: string; updated: string; updated_by: string; path?: string;
}
export interface KbPage extends KbPageMeta { body: string }
export interface KbHome {
  counts: { documents: number; modules: number; api_endpoints: number; db_tables: number };
  completeness: number;
  modules_documented: number;
  recent: Pick<KbPageMeta, "slug" | "title" | "category" | "updated" | "updated_by">[];
  quick_links: { title: string; slug: string }[];
  by_category: Record<string, number>;
  categories: Record<string, string>;
  modules: { key: string; name: string }[];
}
export interface KbTable {
  table: string; model: string; doc: string;
  columns: { name: string; type: string; nullable: boolean; pk: boolean; fk: string | null }[];
}
export interface KbApi { method: string; path: string; module: string; name: string; summary: string; tags: string[] }
export interface KbChange {
  hash: string; date: string; author: string; subject: string; files: string[]; modules: string[]; docs_changed: boolean;
}
export interface KbSearchHit { type: "page" | "table" | "api"; slug?: string; title: string; category?: string; snippet: string }

export const getHome = () => apiClient.get<KbHome>("/knowledge/home").then((r) => r.data);
export const getPages = (category?: string) =>
  apiClient.get<{ items: KbPageMeta[]; categories: Record<string, string> }>("/knowledge/pages", { params: { category } }).then((r) => r.data);
export const getPage = (slug: string) =>
  apiClient.get<KbPage>(`/knowledge/pages/${slug.split("/").map(encodeURIComponent).join("/")}`).then((r) => r.data);
export const getDatabase = () => apiClient.get<{ tables: KbTable[] }>("/knowledge/database").then((r) => r.data.tables);
export const getApis = () => apiClient.get<{ endpoints: KbApi[] }>("/knowledge/apis").then((r) => r.data.endpoints);
export const getSettings = () =>
  apiClient.get<{ settings: { name: string; type: string; set: boolean }[] }>("/knowledge/settings").then((r) => r.data.settings);
export const getChanges = (limit = 150) =>
  apiClient.get<{ items: KbChange[] }>("/knowledge/changes", { params: { limit } }).then((r) => r.data.items);
export const search = (q: string) =>
  apiClient.get<{ results: KbSearchHit[] }>("/knowledge/search", { params: { q } }).then((r) => r.data.results);
