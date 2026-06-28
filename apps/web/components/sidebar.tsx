"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import {
  LayoutDashboard,
  GitBranch,
  Zap,
  LogOut,
  Terminal,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { authApi } from "@/lib/api";
import type { User } from "@/lib/api";

const NAV_ITEMS = [
  { href: "/dashboard",          label: "Overview",     icon: LayoutDashboard, exact: true  },
  { href: "/dashboard/repos",    label: "Repositories", icon: GitBranch,       exact: false },
  { href: "/dashboard/patterns", label: "Rule engine",  icon: Zap,             exact: false },
];

interface SidebarProps {
  user: User;
}

export default function Sidebar({ user }: SidebarProps) {
  const pathname = usePathname();

  return (
    <aside className="w-56 flex-shrink-0 border-r border-gray-200 bg-white min-h-screen flex flex-col">

      {/* Logo */}
      <div className="px-5 py-4 border-b border-gray-100">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 bg-blue-600 rounded-md flex items-center justify-center">
            <Terminal className="w-4 h-4 text-white" strokeWidth={2.5} />
          </div>
          <span className="font-semibold text-gray-900 tracking-tight">FixFlow</span>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5">
        {NAV_ITEMS.map(({ href, label, icon: Icon, exact }) => {
          const active = exact ? pathname === href : pathname.startsWith(href);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "flex items-center gap-3 px-3 py-2 rounded-md text-sm font-medium transition-colors",
                active
                  ? "bg-blue-50 text-blue-700"
                  : "text-gray-600 hover:bg-gray-100 hover:text-gray-900"
              )}
            >
              <Icon className="w-4 h-4 flex-shrink-0" />
              {label}
            </Link>
          );
        })}
      </nav>

      {/* User footer */}
      <div className="px-3 py-4 border-t border-gray-100">
        <div className="flex items-center gap-3 px-3 py-2 mb-1">
          {user.avatar_url ? (
            <img
              src={user.avatar_url}
              alt={user.login}
              className="w-7 h-7 rounded-full flex-shrink-0 ring-1 ring-gray-200"
            />
          ) : (
            <div className="w-7 h-7 bg-gray-200 rounded-full flex-shrink-0" />
          )}
          <div className="min-w-0">
            <p className="text-sm font-medium text-gray-900 truncate">
              {user.name ?? user.login}
            </p>
            <p className="text-xs text-gray-400 truncate">@{user.login}</p>
          </div>
        </div>

        <button
          onClick={() => authApi.logout()}
          className="flex items-center gap-3 px-3 py-2 rounded-md text-sm text-gray-500 hover:bg-gray-100 hover:text-gray-700 transition-colors w-full text-left"
        >
          <LogOut className="w-4 h-4 flex-shrink-0" />
          Sign out
        </button>
      </div>
    </aside>
  );
}