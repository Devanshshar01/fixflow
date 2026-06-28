import Link from "next/link";
import { GitBranch, Zap, Shield, BarChart2, ArrowRight, Terminal } from "lucide-react";

export default function LandingPage() {
  const loginUrl = `${process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000"}/auth/login`;

  return (
    <div className="min-h-screen bg-white">
      {/* Nav */}
      <nav className="border-b border-gray-100 px-6 py-4">
        <div className="mx-auto max-w-5xl flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-7 h-7 bg-blue-600 rounded-md flex items-center justify-center">
              <Terminal className="w-4 h-4 text-white" strokeWidth={2.5} />
            </div>
            <span className="font-semibold text-gray-900 tracking-tight">
              FixFlow
            </span>
          </div>
          
          <a
            href={loginUrl}
            className="flex items-center gap-2 px-4 py-2 bg-gray-900 text-white text-sm font-medium rounded-lg hover:bg-gray-700 transition-colors"
          >
            <svg viewBox="0 0 16 16" className="w-4 h-4 fill-current">
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.013 8.013 0 0016 8c0-4.42-3.58-8-8-8z" />
            </svg>
            Sign in with GitHub
          </a>
        </div>
      </nav>

      {/* Hero */}
      <section className="px-6 pt-24 pb-20 text-center">
        <div className="mx-auto max-w-3xl">
          <div className="inline-flex items-center gap-2 px-3 py-1 bg-blue-50 text-blue-700 text-xs font-medium rounded-full mb-8">
            <span className="w-1.5 h-1.5 bg-blue-500 rounded-full" />
            Open source GitHub App
          </div>

          <h1 className="text-5xl font-bold text-gray-900 leading-tight tracking-tight mb-6">
            Your CI failed.
            <br />
            <span className="text-blue-600">Here&apos;s exactly why.</span>
          </h1>

          <p className="text-xl text-gray-500 leading-relaxed mb-10 max-w-xl mx-auto">
            FixFlow intercepts failed GitHub Actions workflows, analyzes the
            logs, and posts a plain-English root-cause fix directly on your PR.
          </p>

          <a
            href={loginUrl}
            className="inline-flex items-center gap-2 px-6 py-3 bg-blue-600 text-white font-semibold rounded-lg hover:bg-blue-500 transition-colors text-base"
          >
            Install on GitHub
            <ArrowRight className="w-4 h-4" />
          </a>
        </div>
      </section>

      {/* Feature grid */}
      <section className="px-6 pb-24">
        <div className="mx-auto max-w-5xl grid grid-cols-1 md:grid-cols-2 gap-6">
          {[
            {
              icon: Zap,
              title: "Rule engine first",
              body: "300+ known CI error patterns resolved in under 2 seconds, zero AI cost. Gemini handles everything else.",
            },
            {
              icon: Shield,
              title: "Secrets never leave your logs",
              body: "Every log snippet is redacted before analysis or storage. GitHub tokens, AWS keys, database URLs — all masked.",
            },
            {
              icon: GitBranch,
              title: "Posts directly on the PR",
              body: "Root cause, fix, and prevention tip appear as a bot comment the moment analysis completes.",
            },
            {
              icon: BarChart2,
              title: "Failure history dashboard",
              body: "See every CI failure across all your repos — category breakdown, resolution source, and analysis latency.",
            },
          ].map(({ icon: Icon, title, body }) => (
            <div key={title} className="card">
              <div className="flex items-start gap-4">
                <div className="w-9 h-9 bg-blue-50 rounded-lg flex items-center justify-center flex-shrink-0">
                  <Icon className="w-5 h-5 text-blue-600" />
                </div>
                <div>
                  <h3 className="font-semibold text-gray-900 mb-1">{title}</h3>
                  <p className="text-sm text-gray-500 leading-relaxed">{body}</p>
                </div>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}