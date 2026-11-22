import { useState } from "react";
import { apiClient } from "../../api/client";

/** The server hands back "/api/v1/auth/avatar/<id>?v=..."; resolve it against whatever base
 *  URL the API client is actually configured with, so it also works when VITE_API_BASE_URL
 *  points somewhere other than this origin. */
export function avatarSrc(url: string | null | undefined): string | null {
  if (!url) return null;
  const base = (apiClient.defaults.baseURL ?? "/api/v1").replace(/\/$/, "");
  return url.startsWith("/api/v1") ? `${base}${url.slice("/api/v1".length)}` : url;
}

export function initialsOf(name: string | null | undefined): string {
  return (name ?? "")
    .split(/\s+/)
    .filter(Boolean)
    .map((part) => part[0])
    .slice(0, 2)
    .join("")
    .toUpperCase();
}

const SIZES = {
  sm: "h-9 w-9 text-xs",
  lg: "h-24 w-24 text-2xl",
} as const;

/** A user's photo, falling back to their initials when there is none (or it fails to load). */
export function Avatar({
  name, url, size = "sm", className = "",
}: {
  name: string | null | undefined;
  url: string | null | undefined;
  size?: keyof typeof SIZES;
  className?: string;
}) {
  const src = avatarSrc(url);
  const [failedSrc, setFailedSrc] = useState<string | null>(null);
  const showImage = !!src && failedSrc !== src;
  return (
    <div
      className={`flex shrink-0 items-center justify-center overflow-hidden rounded-full bg-indigo-100 font-semibold text-indigo-700 dark:bg-indigo-500/20 dark:text-indigo-200 ${SIZES[size]} ${className}`}
    >
      {showImage ? (
        <img src={src} alt={name ?? ""} className="h-full w-full object-cover" onError={() => setFailedSrc(src)} />
      ) : (
        initialsOf(name)
      )}
    </div>
  );
}
