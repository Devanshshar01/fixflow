import { cn } from "@/lib/utils";

interface StatCardProps {
  label: string;
  value: string | number;
  sub?: string;
  accent?: boolean;
}

export default function StatCard({ label, value, sub, accent }: StatCardProps) {
  return (
    <div
      className={cn(
        "rounded-lg border p-5",
        accent
          ? "bg-blue-600 border-blue-600 text-white"
          : "bg-white border-gray-200"
      )}
    >
      <p
        className={cn(
          "text-xs font-medium uppercase tracking-wide mb-2",
          accent ? "text-blue-100" : "text-gray-500"
        )}
      >
        {label}
      </p>
      <p
        className={cn(
          "text-3xl font-bold leading-none",
          accent ? "text-white" : "text-gray-900"
        )}
      >
        {value}
      </p>
      {sub && (
        <p
          className={cn(
            "text-xs mt-1.5",
            accent ? "text-blue-200" : "text-gray-500"
          )}
        >
          {sub}
        </p>
      )}
    </div>
  );
}