"use client";

import { useEffect } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { tokenStore } from "@/lib/api";
import { Terminal } from "lucide-react";

export default function AuthCallbackInner() {
  const router = useRouter();
  const params = useSearchParams();

  useEffect(() => {
    const token = params.get("token");

    if (!token) {
      router.replace("/");
      return;
    }

    tokenStore.set(token);
    router.replace("/dashboard");
  }, [params, router]);

  return (
    <div className="min-h-screen bg-white flex flex-col items-center justify-center gap-4">
      <div className="w-9 h-9 bg-blue-600 rounded-lg flex items-center justify-center">
        <Terminal className="w-5 h-5 text-white" strokeWidth={2.5} />
      </div>
      <div className="flex items-center gap-2">
        <div className="w-4 h-4 border-2 border-blue-600 border-t-transparent rounded-full animate-spin" />
        <p className="text-sm text-gray-500">Signing you in...</p>
      </div>
    </div>
  );
}